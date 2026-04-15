"""Behavior tests for deterministic portfolio ledger."""

from datetime import date

import pytest

from core_sim import PortfolioLedger


def test_should_apply_buy_and_mark_to_market():
    ledger = PortfolioLedger(starting_cash=10_000)
    snapshot = ledger.update_day(
        trading_day=date(2026, 4, 15),
        fills=[
            {
                "symbol": "SPY",
                "side": "BUY",
                "qty": 10,
                "price": 100.0,
                "market": "US",
                "bucket": "long",
                "fee": 2.0,
            }
        ],
        daily_bars={"SPY": {"close": 105.0}},
    )

    assert snapshot["cash"] == pytest.approx(8_998.0)
    assert snapshot["positions"]["SPY"]["qty"] == pytest.approx(10.0)
    assert snapshot["unrealized_pnl_total"] == pytest.approx(50.0)
    assert snapshot["equity_total"] == pytest.approx(10_048.0)


def test_should_compute_realized_pnl_on_partial_sell():
    ledger = PortfolioLedger(starting_cash=20_000)
    ledger.update_day(
        trading_day=date(2026, 4, 15),
        fills=[
            {
                "symbol": "QQQ",
                "side": "BUY",
                "qty": 20,
                "price": 100.0,
                "market": "US",
                "bucket": "short",
            }
        ],
        daily_bars={"QQQ": {"close": 100.0}},
    )
    snapshot = ledger.update_day(
        trading_day=date(2026, 4, 16),
        fills=[
            {
                "symbol": "QQQ",
                "side": "SELL",
                "qty": 8,
                "price": 110.0,
                "market": "US",
                "bucket": "short",
                "fee": 1.0,
            }
        ],
        daily_bars={"QQQ": {"close": 110.0}},
    )

    assert snapshot["realized_pnl_total"] == pytest.approx(79.0)
    assert snapshot["positions"]["QQQ"]["qty"] == pytest.approx(12.0)
    assert snapshot["unrealized_pnl_total"] == pytest.approx(120.0)


def test_should_reject_sell_above_open_position():
    ledger = PortfolioLedger(starting_cash=10_000)
    ledger.update_day(
        trading_day=date(2026, 4, 15),
        fills=[
            {
                "symbol": "IWM",
                "side": "BUY",
                "qty": 5,
                "price": 200.0,
                "market": "US",
                "bucket": "long",
            }
        ],
        daily_bars={"IWM": {"close": 200.0}},
    )

    with pytest.raises(ValueError, match="cannot sell more than available qty"):
        ledger.update_day(
            trading_day=date(2026, 4, 16),
            fills=[
                {
                    "symbol": "IWM",
                    "side": "SELL",
                    "qty": 6,
                    "price": 201.0,
                    "market": "US",
                    "bucket": "long",
                }
            ],
            daily_bars={"IWM": {"close": 201.0}},
        )


def test_should_fail_when_missing_close_for_open_position():
    ledger = PortfolioLedger(starting_cash=10_000)
    ledger.update_day(
        trading_day=date(2026, 4, 15),
        fills=[
            {
                "symbol": "SPY",
                "side": "BUY",
                "qty": 5,
                "price": 100.0,
                "market": "US",
                "bucket": "short",
            }
        ],
        daily_bars={"SPY": {"close": 100.0}},
    )

    with pytest.raises(ValueError, match="missing close price for symbol SPY"):
        ledger.update_day(
            trading_day=date(2026, 4, 16),
            fills=[],
            daily_bars={},
        )


def test_should_update_short_drawdown_and_reset_by_calendar_month():
    ledger = PortfolioLedger(starting_cash=10_000)
    day_one = ledger.update_day(
        trading_day=date(2026, 4, 29),
        fills=[
            {
                "symbol": "SPY",
                "side": "BUY",
                "qty": 10,
                "price": 100.0,
                "market": "US",
                "bucket": "short",
            }
        ],
        daily_bars={"SPY": {"close": 100.0}},
    )
    day_two = ledger.update_day(
        trading_day=date(2026, 4, 30),
        fills=[],
        daily_bars={"SPY": {"close": 90.0}},
    )
    day_three = ledger.update_day(
        trading_day=date(2026, 5, 1),
        fills=[],
        daily_bars={"SPY": {"close": 95.0}},
    )

    assert day_one["short_bucket"]["monthly_peak"] == pytest.approx(1_000.0)
    assert day_two["short_bucket"]["monthly_drawdown"] == pytest.approx(-0.1)
    assert day_three["short_bucket"]["monthly_peak"] == pytest.approx(950.0)
    assert day_three["short_bucket"]["monthly_drawdown"] == pytest.approx(0.0)
