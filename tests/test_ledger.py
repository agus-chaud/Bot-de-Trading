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


def test_mark_to_market_same_day_updates_single_equity_curve_point():
    ledger = PortfolioLedger(starting_cash=50_000.0)
    day = date(2026, 6, 10)
    ledger.mark_to_market(trading_day=day, daily_bars={})
    ledger.mark_to_market(trading_day=day, daily_bars={})
    assert len(ledger.equity_curve_points) == 1
    assert ledger.equity_curve_points[0]["trading_day"] == day.isoformat()


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
    assert day_one["short_bucket"].get("daily_return", 0.0) == pytest.approx(0.0)
    assert day_two["short_bucket"]["monthly_drawdown"] == pytest.approx(-0.1)
    assert day_two["short_bucket"]["daily_return"] == pytest.approx((900.0 - 1_000.0) / 1_000.0)
    assert day_three["short_bucket"]["monthly_peak"] == pytest.approx(950.0)
    assert day_three["short_bucket"]["monthly_drawdown"] == pytest.approx(0.0)
    assert day_three["short_bucket"]["daily_return"] == pytest.approx((950.0 - 900.0) / 900.0)


def test_stop_loss_closing_last_short_position_does_not_produce_full_drawdown():
    """Cuando un stop-loss cierra la última posición corta, el drawdown mensual
    debe reflejar la pérdida real (~5%) y NO -100% (bug: short_equity=0 / peak)."""
    # Compra 10 acciones a $100 → cost $1_000, short_cash = -1_000
    ledger = PortfolioLedger(starting_cash=10_000)
    day_one = ledger.update_day(
        trading_day=date(2026, 4, 15),
        fills=[
            {
                "symbol": "AAPL",
                "side": "BUY",
                "qty": 10,
                "price": 100.0,
                "market": "US",
                "bucket": "short",
                "fee": 0.0,
            }
        ],
        daily_bars={"AAPL": {"close": 100.0}},
    )
    # short_net_value = 1_000 (positions) + (-1_000) (short_cash) = 0 → peak = 0
    # Eso no es útil como peak, así que verifiquemos el estado inicial...
    # En realidad el peak inicial es 0. El drawdown es 0.0 (peak == 0 → no se computa).

    # Al día siguiente el precio cae a $95 → MV = $950 (sin cerrar posición)
    day_two = ledger.update_day(
        trading_day=date(2026, 4, 16),
        fills=[],
        daily_bars={"AAPL": {"close": 95.0}},
    )
    # short_net_value = 950 + (-1_000) = -50 — el pico sigue siendo 0.

    # Stop-loss cierra la posición a $95: SELL 10 @ $95
    # realized = (95 - 100) * 10 = -50, cash += 950, short_cash += 950
    # short_cash final = -1_000 + 950 = -50
    # short_equity = 0 (sin posiciones abiertas)
    # short_net_value = 0 + (-50) = -50
    day_three = ledger.update_day(
        trading_day=date(2026, 4, 17),
        fills=[
            {
                "symbol": "AAPL",
                "side": "SELL",
                "qty": 10,
                "price": 95.0,
                "market": "US",
                "bucket": "short",
                "fee": 0.0,
            }
        ],
        daily_bars={"AAPL": {"close": 95.0}},
    )

    # La posición cerrada: no debe haber monthly_drawdown = -1.0
    monthly_dd = day_three["short_bucket"]["monthly_drawdown"]
    assert monthly_dd > -1.0, (
        f"Bug: monthly_drawdown={monthly_dd} sugiere short_equity=0/peak (falso -100%)"
    )
    # Sin posiciones abiertas, el snapshot no debe tener AAPL
    assert "AAPL" not in day_three["positions"]
    # El cash total debe reflejar la pérdida real: 10_000 - 1_000 (buy) + 950 (sell) = 9_950
    assert day_three["cash"] == pytest.approx(9_950.0)
