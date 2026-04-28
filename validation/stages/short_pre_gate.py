"""Etapa short_pre_gate del validation-wf.

Envuelve run_short_term_pre_gate() y convierte PreGateReport → StageResult.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from core_sim.short_term_pre_gate import PreGateReport, run_short_term_pre_gate
from data.storage import MarketDB
from validation.report import StageResult

_STAGE_NAME = "short_pre_gate"


def _bars_from_db(
    db: MarketDB,
    trading_days: list[date],
) -> dict[date, dict[str, dict[str, float]]]:
    """Carga todos los bars OHLCV del período desde la DB y los indexa por fecha → símbolo."""
    if not trading_days:
        return {}

    start = min(trading_days)
    end = max(trading_days)

    # Obtener todos los símbolos con datos en ese período (US + AR)
    cursor = db._conn.execute(
        """
        SELECT DISTINCT symbol, venue
        FROM ohlcv
        WHERE ts BETWEEN ? AND ?
        """,
        (start.isoformat(), end.isoformat()),
    )
    symbol_venues: list[tuple[str, str]] = [(row["symbol"], row["venue"]) for row in cursor.fetchall()]

    bars_by_date: dict[date, dict[str, dict[str, float]]] = {}
    for sym, venue in symbol_venues:
        rows = db.get_ohlcv(sym, start, end, venue)
        for row in rows:
            if row.ts not in bars_by_date:
                bars_by_date[row.ts] = {}
            bars_by_date[row.ts][sym] = {
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

    bars_by_date = _bars_from_db(db, trading_days)

    report = run_short_term_pre_gate(
        policy_doc=policy_doc,
        repo_root=repo_root,
        bars_by_date=bars_by_date,
        starting_cash=starting_cash,
        trading_days=trading_days,
    )

    return _pre_gate_report_to_stage_result(report)
