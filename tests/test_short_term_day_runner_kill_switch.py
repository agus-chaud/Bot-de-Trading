"""Tests: kill switch persistente en short_term_day_runner con MarketDB real (SQLite)."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from core_sim import (
    CostModel,
    CorporateActionsStore,
    MarketCostConfig,
    PaperBrokerSim,
    PortfolioLedger,
    TradingCalendarStore,
    create_short_term_pipeline_handlers,
)
from data.storage import MarketDB

REPO_ROOT = Path(__file__).resolve().parents[1]
TRADING_DAY = date(2026, 4, 15)


def _load_policy() -> dict:
    with (REPO_ROOT / "config" / "policy.v1.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_spy_history(*, n: int = 25) -> list[dict[str, float]]:
    return [{"close": float(100 + i), "volume": 1_000_000.0} for i in range(n)]


def _make_ledger_with_monthly_dd(monthly_dd_fraction: float) -> PortfolioLedger:
    """Creates a ledger where the short bucket has a given monthly drawdown but near-zero daily return.

    Buys SPY on Apr 1 at 100, marks it down to target on Apr 14 so that on TRADING_DAY (Apr 15)
    the price is flat — monthly_drawdown = monthly_dd_fraction, daily_return ≈ 0.
    Monthly DD is computed within the same calendar month as the buy, so the position must be
    opened in April for the ledger to track it.
    """
    ledger = PortfolioLedger(starting_cash=200_000.0)
    buy_price = 100.0
    intermediate_price = buy_price * (1.0 + monthly_dd_fraction)

    ledger.update_day(
        trading_day=date(2026, 4, 1),
        fills=[
            {
                "symbol": "SPY",
                "side": "BUY",
                "qty": 10,
                "price": buy_price,
                "market": "US",
                "bucket": "short",
            }
        ],
        daily_bars={"SPY": {"close": buy_price}},
    )
    # Decline accumulated on Apr 14; Apr 15 will be flat at intermediate_price
    ledger.update_day(
        trading_day=date(2026, 4, 14),
        fills=[],
        daily_bars={"SPY": {"close": intermediate_price}},
    )
    return ledger


def _daily_bars_for(price: float) -> dict:
    return {"SPY": {"open": price, "high": price + 1, "low": price - 1, "close": price, "volume": 80_000_000.0}}


def _run_propose(handlers: dict, ledger: PortfolioLedger, close_price: float) -> dict:
    """Invoke propose_orders directly with minimal context."""
    daily_bars = _daily_bars_for(close_price)
    signals = handlers["generate_signals"](
        trading_day=TRADING_DAY,
        daily_bars=daily_bars,
        history_by_symbol={"SPY": _build_spy_history()},
        market_open={"is_us_session": True, "is_ar_business_day": False},
    )
    return handlers["propose_orders"](
        trading_day=TRADING_DAY,
        daily_bars=daily_bars,
        signals=signals,
    )


# ---------------------------------------------------------------------------
# Test 1: sin db → comportamiento stateless, no llama check_and_persist
# ---------------------------------------------------------------------------

def test_runner_without_db_uses_stateless_check():
    """Without db, check_and_persist_kill_switch is never called."""
    policy = _load_policy()
    ledger = PortfolioLedger(starting_cash=100_000.0)
    handlers = create_short_term_pipeline_handlers(policy, REPO_ROOT, ledger, db=None)

    with patch("core_sim.short_term_day_runner.check_and_persist_kill_switch") as mock_persist:
        result = _run_propose(handlers, ledger, close_price=130.0)
        mock_persist.assert_not_called()

    # Pipeline should still work normally (no kill switch in effect)
    assert isinstance(result, dict)
    assert "broker_orders" in result


# ---------------------------------------------------------------------------
# Test 2: runner con db + DD bajo umbral → opera normalmente
# ---------------------------------------------------------------------------

def test_runner_with_db_and_healthy_dd_generates_orders(tmp_path):
    """With db and monthly DD well above threshold, runner proposes orders normally."""
    policy = _load_policy()
    # -1% drawdown — well above the -8% kill threshold
    ledger = _make_ledger_with_monthly_dd(-0.01)
    db = MarketDB(str(tmp_path / "market.db"))

    handlers = create_short_term_pipeline_handlers(policy, REPO_ROOT, ledger, db=db)

    # flat on TRADING_DAY — daily return ~0, monthly_dd ~-1%
    result = _run_propose(handlers, ledger, close_price=100.0 * (1.0 - 0.01))

    assert result.get("sizing_metrics", {}).get("halt_reason") is None or result["sizing_metrics"]["halt_reason"] == ""
    # Kill switch should NOT be active in DB
    state = db.get_kill_switch_state("short")
    assert not state.active


# ---------------------------------------------------------------------------
# Test 3: runner con db + DD cruza -8% → kill switch activa en DB, sin órdenes
# ---------------------------------------------------------------------------

def test_runner_with_db_and_deep_dd_activates_kill_switch(tmp_path):
    """When monthly DD crosses the kill threshold, kill switch is persisted and orders are blocked."""
    policy = _load_policy()
    kill_dd = float(policy["short_kill_switch_monthly_dd"])  # typically -0.08

    # Force monthly DD just below the threshold; daily return on TRADING_DAY stays near 0
    dd_fraction = kill_dd - 0.01  # e.g. -0.09
    ledger = _make_ledger_with_monthly_dd(dd_fraction)
    db = MarketDB(str(tmp_path / "market.db"))

    handlers = create_short_term_pipeline_handlers(policy, REPO_ROOT, ledger, db=db)

    # Price on TRADING_DAY is same as Apr 14 close → daily return ≈ 0
    end_price = 100.0 * (1.0 + dd_fraction)
    result = _run_propose(handlers, ledger, close_price=end_price)

    # Kill switch blocks before stop loss evaluation, so broker_orders is fully empty
    assert result["broker_orders"] == []
    assert result["sizing_metrics"]["halt_reason"] == "short_monthly_kill_switch"

    # Kill switch must be persisted in DB
    state = db.get_kill_switch_state("short")
    assert state.active
    assert state.engine == "short"


# ---------------------------------------------------------------------------
# Test 4: kill switch ya activo en DB → runner bloquea sin re-activar
# ---------------------------------------------------------------------------

def test_runner_with_db_and_preexisting_kill_switch_blocks_without_reactivating(tmp_path):
    """If kill switch is already active in DB, runner blocks orders without creating a duplicate activation."""
    policy = _load_policy()
    ledger = PortfolioLedger(starting_cash=100_000.0)
    db = MarketDB(str(tmp_path / "market.db"))

    # Pre-activate kill switch for the current month
    db.activate_kill_switch(TRADING_DAY, monthly_dd=-0.09, engine="short")

    handlers = create_short_term_pipeline_handlers(policy, REPO_ROOT, ledger, db=db)

    result = _run_propose(handlers, ledger, close_price=130.0)

    # Orders must be blocked
    assert result["broker_orders"] == []
    assert result["sizing_metrics"]["halt_reason"] == "short_monthly_kill_switch"

    # Only one activation row should exist in DB — no duplicate
    db._conn.execute("SELECT COUNT(*) AS cnt FROM kill_switch_log WHERE event='activated' AND engine='short'")
    row = db._conn.execute(
        "SELECT COUNT(*) AS cnt FROM kill_switch_log WHERE event='activated' AND engine='short'"
    ).fetchone()
    assert row["cnt"] == 1
