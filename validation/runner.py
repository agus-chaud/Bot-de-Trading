# Runner principal del validation-wf
# run_validation_wf(policy_doc, db, starting_cash) -> ValidationReport

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path

from .report import StageResult, ValidationReport
from .stages.data_quality import run_data_quality_stage
from .stages.kill_switch_history import run_kill_switch_history_stage
from .stages.long_engine import run_long_engine_stage
from .stages.risk_audit import run_risk_audit_stage
from .stages.short_pre_gate import run_short_pre_gate_stage

logger = logging.getLogger(__name__)

# Repositorio raíz: dos niveles por encima de este archivo
# validation/runner.py → validation/ → repo_root
_REPO_ROOT: Path = Path(__file__).resolve().parents[1]


def _get_trading_days(db, ref: date, lookback: int) -> list[date]:
    """Devuelve los últimos *lookback* días hábiles US hasta *ref* (inclusive).

    Obtiene las sesiones US desde la tabla de calendarios de la DB (venue=XNYS).
    Si no hay datos en la DB, cae back a obtenerlos desde el YAML de calendario
    definido en la policy. Si tampoco hay datos ahí, retorna lista vacía.
    """
    try:
        cursor = db._conn.execute(
            "SELECT ts FROM calendars WHERE venue = 'XNYS' ORDER BY ts ASC"
        )
        all_us_sessions = sorted(
            date.fromisoformat(row[0]) for row in cursor.fetchall()
        )
    except Exception as exc:
        logger.warning('{"event": "runner_calendar_db_error", "error": "%s"}', exc)
        all_us_sessions = []

    if not all_us_sessions:
        logger.warning('{"event": "runner_calendar_empty", "source": "db"}')
        return []

    # Filtrar hasta ref inclusive y tomar los últimos *lookback* días
    eligible = [d for d in all_us_sessions if d <= ref]
    trading_days = eligible[-lookback:] if len(eligible) >= lookback else eligible
    return trading_days


def run_validation_wf(
    policy_doc: dict,
    db,                              # MarketDB instance
    starting_cash: float,
    reference_date: date | None = None,  # default: today
) -> ValidationReport:
    """
    Corre las 5 etapas del validation-wf y devuelve un ValidationReport con GO/NO-GO.

    GO  = todas las etapas pasaron.
          data_quality nunca bloquea GO — reporta y sigue.
    NO-GO = alguna de las etapas 2-5 (short_pre_gate, long_engine,
            risk_audit, kill_switch_history) falló.
    """
    ref = reference_date or date.today()
    lookback: int = policy_doc["validation_wf"]["lookback_trading_days"]
    policy_version: int = policy_doc["schema_version"]

    # Calcular días hábiles reales del período
    trading_days = _get_trading_days(db, ref, lookback)

    # Período cubierto por los días obtenidos
    period_start: date = trading_days[0] if trading_days else ref
    period_end: date = trading_days[-1] if trading_days else ref

    # Ejecutar etapas en orden
    stage_dq = run_data_quality_stage(db, trading_days, policy_doc)
    stage_spg = run_short_pre_gate_stage(db, trading_days, policy_doc, _REPO_ROOT, starting_cash)
    stage_le = run_long_engine_stage(db, trading_days, policy_doc, _REPO_ROOT, starting_cash)
    stage_ra = run_risk_audit_stage(db, trading_days, policy_doc, _REPO_ROOT)
    stage_ksh = run_kill_switch_history_stage(db, trading_days, policy_doc, _REPO_ROOT, starting_cash)

    stages: list[StageResult] = [stage_dq, stage_spg, stage_le, stage_ra, stage_ksh]

    # GO = todas las etapas bloqueantes (2-5) pasaron.
    # data_quality (índice 0) es informativa: no bloquea GO.
    blocking_stages = stages[1:]
    go = all(s.passed for s in blocking_stages)

    return ValidationReport(
        go=go,
        period_start=period_start,
        period_end=period_end,
        lookback_trading_days=lookback,
        stages=stages,
        generated_at=datetime.now(tz=timezone.utc).isoformat(),
        policy_version=policy_version,
    )
