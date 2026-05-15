"""Behavior tests for validation/stages/long_engine.py."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from data.schema import OHLCVRow
from data.storage import MarketDB
from validation.stages.long_engine import StageDetails, run_long_engine_stage

REPO_ROOT = Path(__file__).resolve().parents[1]

_VENUE_LONG = "XBUE"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _policy_doc() -> dict[str, Any]:
    with (REPO_ROOT / "config" / "policy.v1.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _ohlcv_row(
    symbol: str,
    ts: date,
    close: float = 100.0,
    *,
    venue: str = _VENUE_LONG,
    currency: str = "ARS",
) -> OHLCVRow:
    return OHLCVRow(
        symbol=symbol,
        ts=ts,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000_000.0,
        currency=currency,
        venue=venue,
        imputed=False,
    )


def _make_db_with_data(tmp_path: Path, trading_days: list[date]) -> MarketDB:
    """MarketDB with XBUE calendars + OHLCV for GGAL/PAMP/SPY (policy largo AR)."""
    db = MarketDB(str(tmp_path / "test.db"))
    db.upsert_calendars("XBUE", trading_days)
    symbols_prices = {
        "GGAL": 1000.0,
        "PAMP": 500.0,
        "SPY": 200.0,
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
        d = date(year, month, 1)
        count = 0
        while count < 20:
            if d.weekday() < 5:
                days.append(d)
                count += 1
            d += timedelta(days=1)
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

    result, details = run_long_engine_stage(
        db=db,
        trading_days=trading_days,
        policy_doc=policy,
        repo_root=REPO_ROOT,
        starting_cash=200_000.0,
    )

    assert details is None
    assert result.stage == "long_engine"
    assert result.passed is True
    assert result.skipped is False
    assert result.violations == []

    assert "max_drift_observed_pp" in result.metrics
    assert "total_rebalance_cost" in result.metrics
    assert "monthly_drawdown_long" in result.metrics
    assert "rebalances_executed" in result.metrics

    assert result.metrics["max_drift_observed_pp"] is not None
    assert result.metrics["total_rebalance_cost"] is not None
    assert result.metrics["monthly_drawdown_long"] is not None
    assert result.metrics["rebalances_executed"] is not None

    assert isinstance(result.metrics["max_drift_observed_pp"], float)
    assert isinstance(result.metrics["total_rebalance_cost"], float)
    assert isinstance(result.metrics["monthly_drawdown_long"], float)
    assert isinstance(result.metrics["rebalances_executed"], int)

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

    result, details = run_long_engine_stage(
        db=db,
        trading_days=[],
        policy_doc=policy,
        repo_root=REPO_ROOT,
        starting_cash=100_000.0,
    )

    assert details is None
    assert result.stage == "long_engine"
    assert result.passed is True
    assert result.skipped is True
    assert result.violations == []
    assert result.metrics["max_drift_observed_pp"] is None
    assert result.metrics["rebalances_executed"] is None


def test_single_week_returns_skipped(tmp_path: Path) -> None:
    """Only 1 ISO week of trading days → skipped=True (policy uses weekly rebalance)."""
    trading_days = [
        date(2024, 3, 4),
        date(2024, 3, 5),
        date(2024, 3, 6),
        date(2024, 3, 7),
        date(2024, 3, 8),
    ]
    db = _make_db_with_data(tmp_path, trading_days)
    policy = _policy_doc()

    result, _ = run_long_engine_stage(
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
    db = _make_db_empty(tmp_path)
    policy = _policy_doc()

    result, _ = run_long_engine_stage(
        db=db,
        trading_days=trading_days,
        policy_doc=policy,
        repo_root=REPO_ROOT,
        starting_cash=100_000.0,
    )

    assert result.skipped is True
    assert result.passed is True


def test_missing_xbue_calendar_returns_skipped(tmp_path: Path) -> None:
    """OHLCV XBUE sí, pero tabla calendars sin XBUE → trading_days_eff vacío → skipped."""
    trading_days = _generate_trading_days(2024, 1, 3)
    db = MarketDB(str(tmp_path / "no_cal.db"))
    symbols_prices = {"GGAL": 1000.0, "PAMP": 500.0, "SPY": 200.0}
    rows = [_ohlcv_row(sym, d, px) for sym, px in symbols_prices.items() for d in trading_days]
    db.upsert_ohlcv(rows)
    policy = _policy_doc()

    result, _ = run_long_engine_stage(
        db=db,
        trading_days=trading_days,
        policy_doc=policy,
        repo_root=REPO_ROOT,
        starting_cash=150_000.0,
    )
    assert result.skipped is True
    assert result.passed is True


def test_stage_runs_without_xnys_calendar_regression_paso5(tmp_path: Path) -> None:
    """Sleeve largo AR: la DB puede no tener calendario XNYS; el stage no debe depender de él."""
    trading_days = _generate_trading_days(2024, 1, 3)
    db = MarketDB(str(tmp_path / "xbue_only_cal.db"))
    db.upsert_calendars("XBUE", trading_days)
    rows: list[OHLCVRow] = []
    for sym, px in {"GGAL": 1000.0, "PAMP": 500.0, "SPY": 200.0}.items():
        for d in trading_days:
            rows.append(_ohlcv_row(sym, d, px))
    db.upsert_ohlcv(rows)

    xnys_count = db._conn.execute("SELECT COUNT(*) AS n FROM calendars WHERE venue = 'XNYS'").fetchone()[
        "n"
    ]
    assert xnys_count == 0

    policy = _policy_doc()
    result, _ = run_long_engine_stage(
        db=db,
        trading_days=trading_days,
        policy_doc=policy,
        repo_root=REPO_ROOT,
        starting_cash=200_000.0,
    )
    assert result.skipped is False
    assert result.passed is True


def test_spy_fill_uses_ar_market_cedear_benchmark_peso_paso5(tmp_path: Path) -> None:
    """SPY en el sleeve se opera como CEDEAR BYMA → fills con ``market`` AR (pesos)."""
    trading_days = _generate_trading_days(2024, 1, 3)
    db = _make_db_with_data(tmp_path, trading_days)
    policy = _policy_doc()
    lte = policy.get("long_term_engine") or {}
    if not str(lte.get("rebalance_rule", "")).startswith("first_ar_business_day_of_"):
        pytest.skip("policy fixture must use AR calendar for this regression")

    result, details = run_long_engine_stage(
        db=db,
        trading_days=trading_days,
        policy_doc=policy,
        repo_root=REPO_ROOT,
        starting_cash=400_000.0,
        return_details=True,
    )
    assert result.skipped is False
    assert details is not None
    spy_fills = [f for f in details.fills if str(f.get("symbol", "")).upper() == "SPY"]
    assert spy_fills, "rebalance inicial hacia objetivos debe tocar línea SPY"
    assert all(str(f.get("market", "")).upper() == "AR" for f in spy_fills)


# ---------------------------------------------------------------------------
# Test 3 — Rebalances are detected when drift is out of band
# ---------------------------------------------------------------------------


def test_rebalance_detected_after_price_drift(tmp_path: Path) -> None:
    """When prices shift enough to trigger drift, rebalances_executed > 0."""
    trading_days = _generate_trading_days(2024, 1, 2)
    db = MarketDB(str(tmp_path / "drift.db"))
    db.upsert_calendars("XBUE", trading_days)

    month1_days = [d for d in trading_days if d.month == 1]
    month2_days = [d for d in trading_days if d.month == 2]

    rows: list[OHLCVRow] = []
    for d in month1_days:
        rows.append(_ohlcv_row("GGAL", d, 1000.0))
        rows.append(_ohlcv_row("PAMP", d, 500.0))
        rows.append(_ohlcv_row("SPY", d, 200.0))
    for d in month2_days:
        rows.append(_ohlcv_row("GGAL", d, 2000.0))
        rows.append(_ohlcv_row("PAMP", d, 500.0))
        rows.append(_ohlcv_row("SPY", d, 200.0))
    db.upsert_ohlcv(rows)

    policy = _policy_doc()
    result, _ = run_long_engine_stage(
        db=db,
        trading_days=trading_days,
        policy_doc=policy,
        repo_root=REPO_ROOT,
        starting_cash=300_000.0,
    )

    assert result.passed is True
    assert result.skipped is False
    assert result.metrics["max_drift_observed_pp"] >= 0.0


# ---------------------------------------------------------------------------
# Test 4 — Stage is always passed=True (informational)
# ---------------------------------------------------------------------------


def test_stage_always_passed_and_no_violations(tmp_path: Path) -> None:
    """The long_engine stage must never block GO: passed=True, violations=[]."""
    trading_days = _generate_trading_days(2024, 6, 3)
    db = _make_db_with_data(tmp_path, trading_days)
    policy = _policy_doc()

    result, _ = run_long_engine_stage(
        db=db,
        trading_days=trading_days,
        policy_doc=policy,
        repo_root=REPO_ROOT,
        starting_cash=150_000.0,
    )

    assert result.passed is True
    assert result.violations == []


# ---------------------------------------------------------------------------
# Test 5 — return_details populates StageDetails
# ---------------------------------------------------------------------------


def test_return_details_populates_daily_equity_and_positions(tmp_path: Path) -> None:
    """return_details=True returns StageDetails with long-sleeve equity series."""
    trading_days = _generate_trading_days(2024, 1, 3)
    db = _make_db_with_data(tmp_path, trading_days)
    policy = _policy_doc()

    result, details = run_long_engine_stage(
        db=db,
        trading_days=trading_days,
        policy_doc=policy,
        repo_root=REPO_ROOT,
        starting_cash=200_000.0,
        return_details=True,
    )

    assert result.skipped is False
    assert isinstance(details, StageDetails)
    assert len(details.daily_equity) > 0
    assert all("date" in row and "equity" in row for row in details.daily_equity)
    assert details.daily_equity[0]["equity"] > 0
    assert isinstance(details.fills, list)
    assert isinstance(details.final_positions, dict)


# ---------------------------------------------------------------------------
# Test 6 — Cost model: sleeve largo usa un solo bloque de mercado (paso 4 audit)
# ---------------------------------------------------------------------------


def test_broker_long_stage_accepts_ar_orders_only_when_ar_sleeve() -> None:
    """El broker del stage debe registrar costos sólo del mercado del sleeve (AR en policy actual)."""
    policy = _policy_doc()
    lte = policy.get("long_term_engine") or {}
    if not str(lte.get("rebalance_rule", "")).startswith("first_ar_business_day_of_"):
        pytest.skip("policy fixture is not AR-calendar long sleeve")

    from core_sim.ledger import PortfolioLedger
    from validation.stages.long_engine import _build_broker_for_long_sleeve

    day = date(2026, 4, 15)
    ledger_ok = PortfolioLedger(100_000.0)
    broker_ar = _build_broker_for_long_sleeve(ledger_ok, policy, long_trade_market="AR")
    broker_ar.fill_orders(
        day,
        [{"symbol": "GGAL", "side": "BUY", "qty": 10.0, "market": "AR", "bucket": "long"}],
        {"GGAL": {"close": 1000.0}},
    )
    assert ledger_ok.positions["GGAL"].market == "AR"

    ledger_bad = PortfolioLedger(100_000.0)
    broker_strict = _build_broker_for_long_sleeve(ledger_bad, policy, long_trade_market="AR")
    with pytest.raises(ValueError, match="Unknown market"):
        broker_strict.fill_orders(
            day,
            [{"symbol": "GGAL", "side": "BUY", "qty": 10.0, "market": "US", "bucket": "long"}],
            {"GGAL": {"close": 1000.0}},
        )
