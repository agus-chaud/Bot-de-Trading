"""Walk-forward runner: long_engine validation stage over rolling windows (T4).

Windows are produced by :func:`validation.wf_windows.generate_wf_windows`.
Each window is evaluated independently with a fresh simulation inside
:func:`validation.stages.long_engine.run_long_engine_stage`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from data.storage import MarketDB
from validation.report import StageResult
from validation.stages.long_engine import run_long_engine_stage


def run_long_engine_wf_windows(
    db: MarketDB,
    windows: list[list[date]],
    policy_doc: dict[str, Any],
    repo_root: Path,
    starting_cash: float,
) -> list[StageResult]:
    """Run ``run_long_engine_stage`` once per walk-forward window.

    Args:
        db: Market DB (same instance for all windows; stage only reads OHLCV).
        windows: Ordered trading-day lists, typically from ``generate_wf_windows``.
        policy_doc: Parsed policy YAML.
        repo_root: Repository root (whitelists, corporate actions paths).
        starting_cash: Initial cash for each window (independent runs).

    Returns:
        One ``StageResult`` (``stage="long_engine"``) per window, in order.
        Empty ``windows`` → empty list.
    """
    return [
        run_long_engine_stage(db, window, policy_doc, repo_root, starting_cash)
        for window in windows
    ]


def long_engine_wf_metrics_list(results: list[StageResult]) -> list[dict[str, Any]]:
    """Return ``metrics`` dicts for T5 aggregation (same order as ``results``)."""
    return [dict(r.metrics) for r in results]
