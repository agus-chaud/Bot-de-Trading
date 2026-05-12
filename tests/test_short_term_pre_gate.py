"""Pre-gate walk-forward: comportamiento y rechazo automático por umbrales."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from core_sim import run_short_term_pre_gate

REPO_ROOT = Path(__file__).resolve().parents[1]


def _weekdays_from(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _synthetic_bars_uptrend(*, days: list[date]) -> dict[date, dict[str, dict[str, float]]]:
    """SPY sube (momentum+), QQQ baja (no entra al ranking); volumen alto."""
    bars: dict[date, dict[str, dict[str, float]]] = {}
    for i, d in enumerate(days):
        spy_close = 100.0 + float(i) * 0.35
        qqq_close = 200.0 - float(i) * 0.2
        bars[d] = {
            "SPY": {
                "open": spy_close,
                "high": spy_close + 0.5,
                "low": spy_close - 0.5,
                "close": spy_close,
                "volume": 80_000_000.0,
            },
            "QQQ": {
                "open": qqq_close,
                "high": qqq_close + 0.5,
                "low": qqq_close - 0.5,
                "close": qqq_close,
                "volume": 30_000_000.0,
            },
        }
    return bars


def test_pre_gate_passes_on_synthetic_walk_forward():
    with (REPO_ROOT / "config" / "policy.v1.yaml").open(encoding="utf-8") as f:
        policy = yaml.safe_load(f)
    days = _weekdays_from(date(2026, 1, 5), 90)
    bars = _synthetic_bars_uptrend(days=days)
    report = run_short_term_pre_gate(
        policy_doc=policy,
        repo_root=REPO_ROOT,
        bars_by_date=bars,
        starting_cash=100_000.0,
        trading_days=days,
    )
    assert report.global_failures == []
    assert len(report.windows) >= 2
    assert report.passed is True
    for w in report.windows:
        assert w.passed is True
        assert w.metrics["n_fills"] >= 0
        assert "entries_blocked_by_rsi" in w.metrics
        assert "exits_by_rsi" in w.metrics
        assert "exits_by_stop_loss" in w.metrics
        assert w.metrics["entries_blocked_by_rsi"] >= 0
        assert w.metrics["exits_by_rsi"] >= 0
        assert w.metrics["exits_by_stop_loss"] >= 0


def test_pre_gate_fails_when_fee_threshold_impossibly_low():
    with (REPO_ROOT / "config" / "policy.v1.yaml").open(encoding="utf-8") as f:
        policy = yaml.safe_load(f)
    policy = deepcopy(policy)
    policy["short_term_engine"]["rsi_overbought_entry"] = 100.0
    policy["short_term_pre_gate"]["thresholds"]["max_fee_pct_of_initial_per_window"] = 1e-9

    days = _weekdays_from(date(2026, 2, 2), 90)
    bars = _synthetic_bars_uptrend(days=days)
    report = run_short_term_pre_gate(
        policy_doc=policy,
        repo_root=REPO_ROOT,
        bars_by_date=bars,
        starting_cash=100_000.0,
        trading_days=days,
    )
    assert report.passed is False
    assert any(not w.passed for w in report.windows)


def test_pre_gate_skipped_when_disabled():
    with (REPO_ROOT / "config" / "policy.v1.yaml").open(encoding="utf-8") as f:
        policy = yaml.safe_load(f)
    policy = deepcopy(policy)
    policy["short_term_pre_gate"]["enabled"] = False
    report = run_short_term_pre_gate(
        policy_doc=policy,
        repo_root=REPO_ROOT,
        bars_by_date={},
    )
    assert report.passed is True
    assert report.windows == []
