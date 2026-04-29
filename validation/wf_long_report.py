"""Walk-forward long engine: agregación de métricas (T5) y reporte JSON (T6)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from validation.report import StageResult

_LONG_METRIC_KEYS = (
    "max_drift_observed_pp",
    "total_rebalance_cost",
    "monthly_drawdown_long",
    "rebalances_executed",
)


def _window_skip_reason(result: StageResult) -> str | None:
    """Motivo si la ventana no aporta a agregados; None si es usable."""
    if result.skipped:
        return "stage_skipped"
    m = result.metrics
    for key in _LONG_METRIC_KEYS:
        if key not in m or m[key] is None:
            return "incomplete_metrics"
    return None


def _window_usable_for_aggregates(result: StageResult) -> bool:
    return _window_skip_reason(result) is None


@dataclass(frozen=True)
class LongEngineWfSummary:
    """Agregados solo sobre ventanas usables (no skipped, métricas completas)."""

    windows_total: int
    windows_used_in_aggregates: int
    worst_monthly_drawdown_long: float | None
    avg_rebalance_cost: float | None
    total_rebalances_executed: int
    max_drift_observed_pp: float | None
    max_drift_window_index: int | None
    max_drift_period_start: date | None
    max_drift_period_end: date | None


@dataclass(frozen=True)
class SkippedWindowInfo:
    window_index: int
    period_start: date | None
    period_end: date | None
    reason: str


@dataclass
class LongEngineWfReportModel:
    """Contenido lógico del reporte WF largo (antes de serializar fechas)."""

    report_type: str = "long_engine_walk_forward"
    policy_version: int = 1
    generated_at: str = ""
    windows_total: int = 0
    windows_used_in_aggregates: int = 0
    windows_skipped: list[SkippedWindowInfo] = field(default_factory=list)
    per_window: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def aggregate_long_engine_wf_summary(
    windows: list[list[date]],
    results: list[StageResult],
) -> tuple[LongEngineWfSummary, list[SkippedWindowInfo]]:
    """Calcula summary + lista de ventanas excluidas de agregados.

    Raises:
        ValueError: si ``len(windows) != len(results)``.
    """
    if len(windows) != len(results):
        raise ValueError("windows and results must have the same length")

    n = len(windows)
    if n == 0:
        summary = LongEngineWfSummary(
            windows_total=0,
            windows_used_in_aggregates=0,
            worst_monthly_drawdown_long=None,
            avg_rebalance_cost=None,
            total_rebalances_executed=0,
            max_drift_observed_pp=None,
            max_drift_window_index=None,
            max_drift_period_start=None,
            max_drift_period_end=None,
        )
        return summary, []

    skipped_info: list[SkippedWindowInfo] = []
    usable_indices: list[int] = []

    for i, (win, res) in enumerate(zip(windows, results, strict=True)):
        if not win:
            skipped_info.append(SkippedWindowInfo(i, None, None, "empty_window"))
            continue
        ps, pe = win[0], win[-1]
        reason = _window_skip_reason(res)
        if reason:
            skipped_info.append(SkippedWindowInfo(i, ps, pe, reason))
        else:
            usable_indices.append(i)

    used = len(usable_indices)
    if used == 0:
        summary = LongEngineWfSummary(
            windows_total=n,
            windows_used_in_aggregates=0,
            worst_monthly_drawdown_long=None,
            avg_rebalance_cost=None,
            total_rebalances_executed=0,
            max_drift_observed_pp=None,
            max_drift_window_index=None,
            max_drift_period_start=None,
            max_drift_period_end=None,
        )
        return summary, skipped_info

    mdds: list[float] = []
    costs: list[float] = []
    total_reb = 0
    drifts: list[tuple[int, float]] = []

    for i in usable_indices:
        m = results[i].metrics
        mdds.append(float(m["monthly_drawdown_long"]))
        costs.append(float(m["total_rebalance_cost"]))
        total_reb += int(m["rebalances_executed"])
        drifts.append((i, float(m["max_drift_observed_pp"])))

    worst_mdd = min(mdds)
    avg_cost = sum(costs) / len(costs)
    max_idx, max_drift = max(drifts, key=lambda t: t[1])
    wmax = windows[max_idx]

    summary = LongEngineWfSummary(
        windows_total=n,
        windows_used_in_aggregates=used,
        worst_monthly_drawdown_long=round(worst_mdd, 6),
        avg_rebalance_cost=round(avg_cost, 4),
        total_rebalances_executed=total_reb,
        max_drift_observed_pp=round(max_drift, 4),
        max_drift_window_index=max_idx,
        max_drift_period_start=wmax[0],
        max_drift_period_end=wmax[-1],
    )
    return summary, skipped_info


def build_long_engine_wf_report_model(
    windows: list[list[date]],
    results: list[StageResult],
    policy_version: int,
    generated_at: str | None = None,
) -> LongEngineWfReportModel:
    """Arma el modelo completo (per_window + summary + skipped)."""
    if len(windows) != len(results):
        raise ValueError("windows and results must have the same length")

    summary_obj, skipped = aggregate_long_engine_wf_summary(windows, results)
    gen = generated_at or datetime.now(tz=timezone.utc).isoformat()

    per_window: list[dict[str, Any]] = []
    for i, (win, res) in enumerate(zip(windows, results, strict=True)):
        ps = win[0].isoformat() if win else None
        pe = win[-1].isoformat() if win else None
        skip_reason = _window_skip_reason(res)
        per_window.append(
            {
                "window_index": i,
                "period_start": ps,
                "period_end": pe,
                "trading_day_count": len(win),
                "skipped": res.skipped,
                "skip_reason": skip_reason,
                "metrics": {k: res.metrics.get(k) for k in _LONG_METRIC_KEYS},
            }
        )

    summary_dict: dict[str, Any] = {
        "worst_monthly_drawdown_long": summary_obj.worst_monthly_drawdown_long,
        "avg_rebalance_cost": summary_obj.avg_rebalance_cost,
        "total_rebalances_executed": summary_obj.total_rebalances_executed,
        "max_drift_observed_pp": summary_obj.max_drift_observed_pp,
        "max_drift_window_index": summary_obj.max_drift_window_index,
        "max_drift_period_start": summary_obj.max_drift_period_start.isoformat()
        if summary_obj.max_drift_period_start
        else None,
        "max_drift_period_end": summary_obj.max_drift_period_end.isoformat()
        if summary_obj.max_drift_period_end
        else None,
    }

    skipped_dicts = [
        {
            "window_index": s.window_index,
            "period_start": s.period_start.isoformat(),
            "period_end": s.period_end.isoformat(),
            "reason": s.reason,
        }
        for s in skipped
    ]

    return LongEngineWfReportModel(
        policy_version=policy_version,
        generated_at=gen,
        windows_total=summary_obj.windows_total,
        windows_used_in_aggregates=summary_obj.windows_used_in_aggregates,
        windows_skipped=skipped,
        per_window=per_window,
        summary=summary_dict,
    )


def long_engine_wf_report_to_json_dict(model: LongEngineWfReportModel) -> dict[str, Any]:
    """Dict JSON-serializable (fechas ya como string en per_window/summary)."""
    skipped_serial = [
        {
            "window_index": s.window_index,
            "period_start": s.period_start.isoformat() if s.period_start else None,
            "period_end": s.period_end.isoformat() if s.period_end else None,
            "reason": s.reason,
        }
        for s in model.windows_skipped
    ]
    return {
        "report_type": model.report_type,
        "policy_version": model.policy_version,
        "generated_at": model.generated_at,
        "windows_total": model.windows_total,
        "windows_used_in_aggregates": model.windows_used_in_aggregates,
        "windows_skipped": skipped_serial,
        "per_window": model.per_window,
        "summary": model.summary,
    }


def save_long_engine_wf_report_json(
    payload: dict[str, Any],
    reports_dir: Path,
    filename_prefix: str = "long_engine_wf",
) -> Path:
    """Escribe JSON en ``reports_dir`` con timestamp (mismo estilo que validation_wf)."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    out_path = reports_dir / f"{filename_prefix}_{ts}.json"
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path
