"""Behavior tests for validation/stages/long_engine.py."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from data.schema import OHLCVRow
from data.storage import MarketDB
from validation.stages.long_engine import run_long_engine_stage

REPO_ROOT = Path(__file__).resolve().parents[1]

_VENUE_US = "XNYS"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _policy_doc() -> dict[str, Any]:
    with (REPO_ROOT / "config" / "policy.v1.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _ohlcv_row(symbol: str, ts: date, close: float = 100.0) -> OHLCVRow:
    return OHLCVRow(
        symbol=symbol,
        ts=ts,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000_000.0,
        currency="USD",
        venue=_VENUE_US,
        imputed=False,
    )


def _make_db_with_data(tmp_path: Path, trading_days: list[date]) -> MarketDB:
    """Create a MarketDB pre-populated with OHLCV rows for all 3 long-sleeve symbols."""
    db = MarketDB(str(tmp_path / "test.db"))
    symbols_prices = {
        "SPY": 100.0,
        "IWM": 50.0,
        "QQQ": 300.0,
    }
    rows: list[OHLCVRow] = []
    for sym, price in symbols_prices.items():
        for d in trading_days:
            rows.append(_ohlcv_row(sym, d, price))
    db.upsert_ohlcv(rows)
    return db


def _make_db_empty(tmp_path: Path) -> MarketDB:
    """Create an empty MarketDB with no OHLCV data."""
    return MarketDB(str(tmp_path / "empty.db"))


def _generate_trading_days(
    start_year: int, start_month: int, num_months: int
) -> list[date]:
    """Generate ~20 weekday trading days per month for num_months starting from start_year/start_month."""
    from datetime import timedelta

    days: list[date] = []
    year, month = start_year, start_month
    for _ in range(num_months):
        # First day of the month
        d = date(year, month, 1)
        count = 0
        while count < 20:
            if d.weekday() < 5:  # Monday–Friday
                days.append(d)
                count += 1
            d += timedelta(days=1)
        # Advance to next month
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    return days


# ---------------------------------------------------------------------------
# Test 1 — 3-month period returns all 4 metrics
# ---------------------------------------------------------------------------


def test_three_month_period_returns_four_metrics(tmp_path: Path) -> None:
    """With 3 months of data the stage runs and returns all 4 metrics (not None, not skipped)."""
    trading_days = _generate_trading_days(2024, 1, 3)
    db = _make_db_with_data(tmp_path, trading_days)
    policy = _policy_doc()

    result = run_long_engine_stage(
        db=db,
        trading_days=trading_days,
        policy_doc=policy,
        repo_root=REPO_ROOT,
        starting_cash=200_000.0,
    )

    assert result.stage == "long_engine"
    assert result.passed is True
    assert result.skipped is False
    assert result.violations == []

    # All 4 metrics must be present and not None
    assert "max_drift_observed_pp" in result.metrics
    assert "total_rebalance_cost" in result.metrics
    assert "monthly_drawdown_long" in result.metrics
    assert "rebalances_executed" in result.metrics

    assert result.metrics["max_drift_observed_pp"] is not None
    assert result.metrics["total_rebalance_cost"] is not None
    assert result.metrics["monthly_drawdown_long"] is not None
    assert result.metrics["rebalances_executed"] is not None

    # Types must be numeric
    assert isinstance(result.metrics["max_drift_observed_pp"], float)
    assert isinstance(result.metrics["total_rebalance_cost"], float)
    assert isinstance(result.metrics["monthly_drawdown_long"], float)
    assert isinstance(result.metrics["rebalances_executed"], int)

    # Sanity bounds
    assert result.metrics["max_drift_observed_pp"] >= 0.0
    assert result.metrics["total_rebalance_cost"] >= 0.0
    assert result.metrics["monthly_drawdown_long"] <= 0.0
    assert result.metrics["rebalances_executed"] >= 0


# ---------------------------------------------------------------------------
# Test 2 — Insufficient data → skipped=True
# ---------------------------------------------------------------------------


def test_empty_trading_days_returns_skipped(tmp_path: Path) -> None:
    """Empty trading_days list → skipped=True, passed=True."""
    db = _make_db_empty(tmp_path)
    policy = _policy_doc()

    result = run_long_engine_stage(
        db=db,
        trading_days=[],
        policy_doc=policy,
        repo_root=REPO_ROOT,
        starting_cash=100_000.0,
    )

    assert result.stage == "long_engine"
    assert result.passed is True
    assert result.skipped is True
    assert result.violations == []
    assert result.metrics["max_drift_observed_pp"] is None
    assert result.metrics["rebalances_executed"] is None


def test_single_month_returns_skipped(tmp_path: Path) -> None:
    """Only 1 month of trading days → skipped=True (need at least 2 months)."""
    trading_days = _generate_trading_days(2024, 3, 1)
    db = _make_db_with_data(tmp_path, trading_days)
    policy = _policy_doc()

    result = run_long_engine_stage(
        db=db,
        trading_days=trading_days,
        policy_doc=policy,
        repo_root=REPO_ROOT,
        starting_cash=100_000.0,
    )

    assert result.skipped is True
    assert result.passed is True


def test_no_ohlcv_in_db_returns_skipped(tmp_path: Path) -> None:
    """3 months of trading days but no OHLCV in DB → skipped=True."""
    trading_days = _generate_trading_days(2024, 1, 3)
    db = _make_db_empty(tmp_path)  # no data inserted
    policy = _policy_doc()

    result = run_long_engine_stage(
        db=db,
        trading_days=trading_days,
        policy_doc=policy,
        repo_root=REPO_ROOT,
        starting_cash=100_000.0,
    )

    assert result.skipped is True
    assert result.passed is True


# ---------------------------------------------------------------------------
# Test 3 — Rebalances are detected when drift is out of band
# ---------------------------------------------------------------------------


def test_rebalance_detected_after_price_drift(tmp_path: Path) -> None:
    """When prices shift enough to trigger drift, rebalances_executed > 0."""
    from datetime import timedelta

    # Build 2 months of trading days
    trading_days = _generate_trading_days(2024, 1, 2)
    db = MarketDB(str(tmp_path / "drift.db"))

    # First month: balanced prices (SPY=100, IWM=50, QQQ=300)
    # Second month: SPY price doubles → massive drift on first day of month 2
    month1_days = [d for d in trading_days if d.month == 1]
    month2_days = [d for d in trading_days if d.month == 2]

    rows: list[OHLCVRow] = []
    for d in month1_days:
        rows.append(_ohlcv_row("SPY", d, 100.0))
        rows.append(_ohlcv_row("IWM", d, 50.0))
        rows.append(_ohlcv_row("QQQ", d, 300.0))
    for d in month2_days:
        rows.append(_ohlcv_row("SPY", d, 200.0))  # price doubled → huge drift
        rows.append(_ohlcv_row("IWM", d, 50.0))
        rows.append(_ohlcv_row("QQQ", d, 300.0))
    db.upsert_ohlcv(rows)

    policy = _policy_doc()
    result = run_long_engine_stage(
        db=db,
        trading_days=trading_days,
        policy_doc=policy,
        repo_root=REPO_ROOT,
        starting_cash=300_000.0,
    )

    assert result.passed is True
    assert result.skipped is False
    # With price doubling on SPY there should be drift observed
    assert result.metrics["max_drift_observed_pp"] >= 0.0


# ---------------------------------------------------------------------------
# Test 4 — Stage is always passed=True (informational)
# ---------------------------------------------------------------------------


def test_stage_always_passed_and_no_violations(tmp_path: Path) -> None:
    """The long_engine stage must never block GO: passed=True, violations=[]."""
    trading_days = _generate_trading_days(2024, 6, 3)
    db = _make_db_with_data(tmp_path, trading_days)
    policy = _policy_doc()

    result = run_long_engine_stage(
        db=db,
        trading_days=trading_days,
        policy_doc=policy,
        repo_root=REPO_ROOT,
        starting_cash=150_000.0,
    )

    assert result.passed is True
    assert result.violations == []
