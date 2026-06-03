"""Etapa short_pre_gate del validation-wf.

Envuelve run_short_term_pre_gate() y convierte PreGateReport → StageResult.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from core_sim.short_term_day_runner import load_merged_whitelist
from core_sim.short_term_pre_gate import PreGateReport, run_short_term_pre_gate
from data.storage import MarketDB
from data.venue_policy import venues_for_market
from validation.report import StageResult

_STAGE_NAME = "short_pre_gate"


def _bars_from_db(
    db: MarketDB,
    trading_days: list[date],
    merged_whitelist: dict[str, str],
) -> dict[date, dict[str, dict[str, float]]]:
    """Carga los bars OHLCV del período desde la DB y los indexa por fecha → símbolo.

    Política de venue (ver :mod:`data.venue_policy`): cada símbolo se lee SOLO del
    venue que matchea su market tag en ``merged_whitelist`` — US desde XNYS/US (USD),
    AR desde XBUE (ARS). Evita el bug de mezclar USD y ARS en los duales. Con XNYS y
    US legacy ambos presentes gana XNYS (orden de preferencia); si un símbolo no tiene
    barra en su venue ese día, se omite (nunca se sustituye desde otro venue). Símbolos
    fuera de la whitelist se ignoran (sin tag no hay venue definido).
    """
    if not trading_days:
        return {}

    start = min(trading_days)
    end = max(trading_days)

    bars_by_date: dict[date, dict[str, dict[str, float]]] = {}
    for sym, market in merged_whitelist.items():
        seen_days: set[date] = set()
        # venues_for_market está ordenado por preferencia (XNYS antes que US legacy):
        # el primer venue que aporte barra para un día gana, los siguientes no la pisan.
        for venue in venues_for_market(market):
            for row in db.get_ohlcv(sym, start, end, venue):
                if row.ts in seen_days:
                    continue
                seen_days.add(row.ts)
                bars_by_date.setdefault(row.ts, {})[sym] = {
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "volume": row.volume,
                }

    return bars_by_date


def _pre_gate_report_to_stage_result(report: PreGateReport) -> StageResult:
    """Convierte PreGateReport → StageResult con métricas aplanadas."""
    windows_total = len(report.windows)
    windows_passed = sum(1 for w in report.windows if w.passed)
    windows_failed = windows_total - windows_passed

    per_window: list[dict[str, Any]] = [
        {
            "metrics": w.metrics,
            "passed": w.passed,
            "violations": w.violations,
        }
        for w in report.windows
    ]

    metrics: dict[str, Any] = {
        "windows_total": windows_total,
        "windows_passed": windows_passed,
        "windows_failed": windows_failed,
        "global_failures": list(report.global_failures),
        "per_window": per_window,
    }

    # Aplanar todas las violations: global_failures + violations por ventana
    violations: list[str] = list(report.global_failures)
    for w in report.windows:
        violations.extend(w.violations)

    return StageResult(
        stage=_STAGE_NAME,
        passed=report.passed,
        metrics=metrics,
        violations=violations,
        skipped=False,
    )


def run_short_pre_gate_stage(
    db: MarketDB,
    trading_days: list[date],
    policy_doc: dict,
    repo_root: Path,
    starting_cash: float,
) -> StageResult:
    """Etapa short_pre_gate del validation-wf.

    Si el bloque está deshabilitado en policy, devuelve skipped=True sin correr nada.
    Si está habilitado, corre el walk-forward OOS y convierte el reporte.
    """
    cfg = policy_doc.get("short_term_pre_gate", {})
    if not bool(cfg.get("enabled", False)):
        return StageResult(
            stage=_STAGE_NAME,
            passed=True,
            skipped=True,
            metrics={},
            violations=[],
        )

    merged_whitelist = load_merged_whitelist(repo_root, policy_doc)
    bars_by_date = _bars_from_db(db, trading_days, merged_whitelist)

    report = run_short_term_pre_gate(
        policy_doc=policy_doc,
        repo_root=repo_root,
        bars_by_date=bars_by_date,
        starting_cash=starting_cash,
        trading_days=trading_days,
    )

    return _pre_gate_report_to_stage_result(report)
