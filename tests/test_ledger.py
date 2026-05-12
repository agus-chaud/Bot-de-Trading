"""Behavior tests for deterministic portfolio ledger."""

from datetime import date

import pytest

from core_sim import PortfolioLedger
from core_sim.ledger import DAILY_EQUITY_KPI_COLUMNS


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
    assert ledger.equity_curve_points[0]["ts"] == day.isoformat()


def test_daily_equity_curve_includes_kpi_columns_and_bucket_splits():
    """Contrato rpt_kpi.v1 §2.1: ts, equity_*, cash, costs_day; equity_short + equity_long ≈ total."""
    ledger = PortfolioLedger(starting_cash=10_000)
    ledger.update_day(
        trading_day=date(2026, 6, 11),
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
    pt = ledger.equity_curve_points[-1]
    assert pt["costs_day"] == pytest.approx(2.0)
    assert pt["equity_total"] == pytest.approx(10_048.0)
    assert pt["equity_short"] == pytest.approx(0.0)
    assert pt["equity_long"] == pytest.approx(10_048.0)
    assert pt["cash"] == pytest.approx(8_998.0)
    assert pt["mv_us"] == pytest.approx(1_050.0)
    assert pt["mv_ar"] == pytest.approx(0.0)
    exported = ledger.daily_equity_series_for_kpi_export()
    assert tuple(exported[-1].keys()) == DAILY_EQUITY_KPI_COLUMNS
    assert exported[-1]["costs_day"] == pytest.approx(2.0)


def test_mixed_short_long_buckets_sum_to_total_equity():
    ledger = PortfolioLedger(starting_cash=20_000)
    ledger.update_day(
        trading_day=date(2026, 6, 12),
        fills=[
            {
                "symbol": "QQQ",
                "side": "BUY",
                "qty": 5,
                "price": 200.0,
                "market": "US",
                "bucket": "short",
                "fee": 0.0,
            },
            {
                "symbol": "SPY",
                "side": "BUY",
                "qty": 10,
                "price": 100.0,
                "market": "US",
                "bucket": "long",
                "fee": 0.0,
            },
        ],
        daily_bars={"QQQ": {"close": 200.0}, "SPY": {"close": 100.0}},
    )
    pt = ledger.equity_curve_points[-1]
    assert pt["equity_total"] == pytest.approx(pt["equity_short"] + pt["equity_long"])


def test_should_update_short_drawdown_and_reset_by_calendar_month():
    """Bucket-equity DD: peak es running max de bucket_equity dentro del mes;
    reset al primer día del mes calendario; DD = max(-1.0, bucket_equity / peak - 1)
    clampado a 0 cuando peak <= 0.

    Sin short_allocation (default=0), el peak se seedea con bucket_equity crudo.

    Trayectoria (April):
      - Apr 27 BUY 10@100, close=100  → short_cash=-1000, MV=1000, eq=0,    peak=0,    DD=0
      - Apr 28 close=110              → MV=1100,                  eq=100,  peak=100,  DD=0
      - Apr 29 close=90               → MV=900,                   eq=-100, peak=100,  DD=-1.0 (clamped)
      - Apr 30 close=95               → MV=950,                   eq=-50,  peak=100,  DD=-1.0 (clamped)
    Reset (May):
      - May 1 close=95                → eq=-50, RESET peak=-50,            DD=0
    """
    ledger = PortfolioLedger(starting_cash=10_000)
    day_one = ledger.update_day(
        trading_day=date(2026, 4, 27),
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
        trading_day=date(2026, 4, 28),
        fills=[],
        daily_bars={"SPY": {"close": 110.0}},
    )
    day_three = ledger.update_day(
        trading_day=date(2026, 4, 29),
        fills=[],
        daily_bars={"SPY": {"close": 90.0}},
    )
    day_four = ledger.update_day(
        trading_day=date(2026, 4, 30),
        fills=[],
        daily_bars={"SPY": {"close": 95.0}},
    )
    day_five = ledger.update_day(
        trading_day=date(2026, 5, 1),
        fills=[],
        daily_bars={"SPY": {"close": 95.0}},
    )

    # Day 1: BUY abre la posición; bucket_equity=0 (cash y MV se compensan), peak=0.
    assert day_one["short_bucket"]["monthly_peak"] == pytest.approx(0.0)
    assert day_one["short_bucket"]["monthly_drawdown"] == pytest.approx(0.0)
    assert day_one["short_bucket"].get("daily_return", 0.0) == pytest.approx(0.0)
    # Day 2: precio sube → bucket_equity=100, peak=100, DD=0.
    assert day_two["short_bucket"]["monthly_peak"] == pytest.approx(100.0)
    assert day_two["short_bucket"]["monthly_drawdown"] == pytest.approx(0.0)
    # Day 3: precio cae → bucket_equity=-100, peak conserva 100, DD clamped a -1.0.
    assert day_three["short_bucket"]["monthly_peak"] == pytest.approx(100.0)
    assert day_three["short_bucket"]["monthly_drawdown"] == pytest.approx(-1.0)
    # Day 4: precio sube parcial → bucket_equity=-50, peak conserva 100, DD clamped a -1.0.
    assert day_four["short_bucket"]["monthly_peak"] == pytest.approx(100.0)
    assert day_four["short_bucket"]["monthly_drawdown"] == pytest.approx(-1.0)
    # Day 5 (May): reset → adjusted_equity = -50 + 0 = -50, peak = -50, DD=0 (peak<=0).
    assert day_five["short_bucket"]["monthly_peak"] == pytest.approx(-50.0)
    assert day_five["short_bucket"]["monthly_drawdown"] == pytest.approx(0.0)
    # daily_return sigue calculado sobre MV (fuera de scope del fix de DD).
    assert day_two["short_bucket"]["daily_return"] == pytest.approx((1_100.0 - 1_000.0) / 1_000.0)
    assert day_five["short_bucket"]["daily_return"] == pytest.approx((950.0 - 950.0) / 950.0)


def test_stop_loss_closing_last_short_position_dd_is_clamped():
    """Stop-loss cierra la última posición: el DD mensual se clampea a -1.0
    porque el raw DD (-1.5) excede el piso físico de -100%.

    Trayectoria (sin short_allocation, default=0):
      Day 1: BUY 10@100 close=100  → short_cash=-1000, MV=1000, eq=0,    peak=0
      Day 2: close=110             → MV=1100,                  eq=100,  peak=100
      Day 3: close=95              → MV=950,                   eq=-50,  peak=100
      Day 4: SELL 10@95 (stop)     → short_cash=-50, MV=0,     eq=-50,  peak=100
                                     raw DD = -50/100 - 1 = -1.5 → clamped a -1.0
    """
    ledger = PortfolioLedger(starting_cash=10_000)
    ledger.update_day(
        trading_day=date(2026, 4, 14),
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
    ledger.update_day(
        trading_day=date(2026, 4, 15),
        fills=[],
        daily_bars={"AAPL": {"close": 110.0}},
    )
    ledger.update_day(
        trading_day=date(2026, 4, 16),
        fills=[],
        daily_bars={"AAPL": {"close": 95.0}},
    )
    day_close = ledger.update_day(
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

    assert day_close["short_bucket"]["monthly_peak"] == pytest.approx(100.0)
    assert day_close["short_bucket"]["monthly_drawdown"] == pytest.approx(-1.0)
    assert "AAPL" not in day_close["positions"]
    assert day_close["cash"] == pytest.approx(9_950.0)


def test_short_drawdown_allocation_seeds_peak():
    """Con short_allocation, el DD se mide sobre adjusted_equity (bucket_equity + allocation).
    Esto produce drawdowns semánticamente correctos y siempre en [-1, 0]."""
    ledger = PortfolioLedger(starting_cash=100_000, short_allocation=30_000)
    day_one = ledger.update_day(
        trading_day=date(2026, 4, 14),
        fills=[
            {
                "symbol": "SPY",
                "side": "BUY",
                "qty": 10,
                "price": 100.0,
                "market": "US",
                "bucket": "short",
                "fee": 0.0,
            }
        ],
        daily_bars={"SPY": {"close": 100.0}},
    )
    # bucket_equity = 0; adjusted = 0 + 30_000 = 30_000; peak = 30_000; DD = 0.
    assert day_one["short_bucket"]["monthly_peak"] == pytest.approx(30_000.0)
    assert day_one["short_bucket"]["monthly_drawdown"] == pytest.approx(0.0)

    day_two = ledger.update_day(
        trading_day=date(2026, 4, 15),
        fills=[],
        daily_bars={"SPY": {"close": 90.0}},
    )
    # bucket_equity = -100; adjusted = -100 + 30_000 = 29_900; peak = 30_000.
    # DD = (29_900 / 30_000) - 1 ≈ -0.00333.
    assert day_two["short_bucket"]["monthly_peak"] == pytest.approx(30_000.0)
    assert day_two["short_bucket"]["monthly_drawdown"] == pytest.approx(
        (29_900.0 / 30_000.0) - 1.0
    )
    assert day_two["short_bucket"]["monthly_drawdown"] > -1.0


def test_short_drawdown_clamp_at_minus_one_without_allocation():
    """Sin allocation (default=0), el clamp impide DD < -1.0."""
    ledger = PortfolioLedger(starting_cash=10_000)
    ledger.update_day(
        trading_day=date(2026, 7, 1),
        fills=[
            {
                "symbol": "TSLA",
                "side": "BUY",
                "qty": 10,
                "price": 100.0,
                "market": "US",
                "bucket": "short",
                "fee": 0.0,
            }
        ],
        daily_bars={"TSLA": {"close": 105.0}},
    )
    snap = ledger.update_day(
        trading_day=date(2026, 7, 2),
        fills=[],
        daily_bars={"TSLA": {"close": 80.0}},
    )
    # bucket_equity = -1000 + 800 = -200; peak was 50 (from day 1: -1000+1050=50).
    # raw DD = (-200 / 50) - 1 = -5.0 → clamped to -1.0.
    assert snap["short_bucket"]["monthly_drawdown"] == pytest.approx(-1.0)
    assert snap["short_bucket"]["monthly_drawdown"] >= -1.0


def test_short_allocation_zero_backward_compat():
    """Default short_allocation=0: sin fills, peak=0 y DD=0 cada día."""
    ledger = PortfolioLedger(starting_cash=10_000)
    for i in range(5):
        snap = ledger.mark_to_market(
            trading_day=date(2026, 6, 2 + i), daily_bars={}
        )
        assert snap["short_bucket"]["monthly_peak"] == pytest.approx(0.0)
        assert snap["short_bucket"]["monthly_drawdown"] == pytest.approx(0.0)


def test_close_at_profit_does_not_inflate_short_drawdown():
    """SCN-3: cerrar una short en ganancia no debe inflar el DD por el "phantom MV→0".

    Trayectoria:
      Day 1 BUY 10@100 close=100 → short_cash=-1000, MV=1000, eq=0,   peak=0,   DD=0
      Day 2 close=110           → MV=1100,                  eq=100, peak=100, DD=0
      Day 3 SELL 10@110         → short_cash=-1000+1100=100, MV=0, eq=100, peak=100, DD=0
    """
    ledger = PortfolioLedger(starting_cash=10_000)
    ledger.update_day(
        trading_day=date(2026, 4, 14),
        fills=[
            {
                "symbol": "MSFT",
                "side": "BUY",
                "qty": 10,
                "price": 100.0,
                "market": "US",
                "bucket": "short",
                "fee": 0.0,
            }
        ],
        daily_bars={"MSFT": {"close": 100.0}},
    )
    day_two = ledger.update_day(
        trading_day=date(2026, 4, 15),
        fills=[],
        daily_bars={"MSFT": {"close": 110.0}},
    )
    day_close = ledger.update_day(
        trading_day=date(2026, 4, 16),
        fills=[
            {
                "symbol": "MSFT",
                "side": "SELL",
                "qty": 10,
                "price": 110.0,
                "market": "US",
                "bucket": "short",
                "fee": 0.0,
            }
        ],
        daily_bars={"MSFT": {"close": 110.0}},
    )

    # Pre-cierre: peak=100, DD=0.
    assert day_two["short_bucket"]["monthly_peak"] == pytest.approx(100.0)
    assert day_two["short_bucket"]["monthly_drawdown"] == pytest.approx(0.0)
    # Post-cierre en profit: bucket_equity sigue ≈ 100 (cash compensa MV=0). DD ≈ 0.
    assert "MSFT" not in day_close["positions"]
    assert day_close["short_bucket"]["monthly_peak"] == pytest.approx(100.0)
    assert day_close["short_bucket"]["monthly_drawdown"] == pytest.approx(0.0)
    # NO debe haber phantom DD por MV→0.
    assert day_close["short_bucket"]["monthly_drawdown"] > -0.01


def test_zero_activity_month_yields_zero_drawdown():
    """SCN-5: mes sin fills → peak=0 y DD=0 cada día, sin excepciones."""
    ledger = PortfolioLedger(starting_cash=10_000)
    business_days = [
        date(2026, 6, 1),
        date(2026, 6, 2),
        date(2026, 6, 3),
        date(2026, 6, 4),
        date(2026, 6, 5),
    ]
    for day in business_days:
        snap = ledger.mark_to_market(trading_day=day, daily_bars={})
        assert snap["short_bucket"]["monthly_peak"] == pytest.approx(0.0)
        assert snap["short_bucket"]["monthly_drawdown"] == pytest.approx(0.0)
        assert snap["equity_short"] == pytest.approx(0.0)


def test_multi_fill_month_tracks_running_max_peak():
    """SCN-7: peak es el running-max de bucket_equity y nunca decrece intra-mes.

    Trayectoria de bucket_equity vía MTM sobre 1 posición de 10 shares (BUY day-1 a $100):
        eq_t = -1000 + 10 * close_t
        close → eq    : 100→0, 120→200, 135→350, 128→280, 141→410, 138→380, 141→410, 125→250
        peak progr.   : 0,    200,     350,     350,     410,     410,     410,     410
        DD final      : 250/410 - 1
    """
    ledger = PortfolioLedger(starting_cash=10_000)
    days_and_closes = [
        (date(2026, 7, 1), 100.0, 0.0),
        (date(2026, 7, 2), 120.0, 200.0),
        (date(2026, 7, 3), 135.0, 350.0),
        (date(2026, 7, 6), 128.0, 350.0),
        (date(2026, 7, 7), 141.0, 410.0),
        (date(2026, 7, 8), 138.0, 410.0),
        (date(2026, 7, 9), 141.0, 410.0),
        (date(2026, 7, 10), 125.0, 410.0),
    ]
    # Day 1: BUY 10 @ 100.
    snap = ledger.update_day(
        trading_day=days_and_closes[0][0],
        fills=[
            {
                "symbol": "NVDA",
                "side": "BUY",
                "qty": 10,
                "price": 100.0,
                "market": "US",
                "bucket": "short",
                "fee": 0.0,
            }
        ],
        daily_bars={"NVDA": {"close": days_and_closes[0][1]}},
    )
    assert snap["short_bucket"]["monthly_peak"] == pytest.approx(days_and_closes[0][2])

    last_snap = snap
    prev_peak = days_and_closes[0][2]
    for day, close, expected_peak in days_and_closes[1:]:
        last_snap = ledger.update_day(
            trading_day=day,
            fills=[],
            daily_bars={"NVDA": {"close": close}},
        )
        assert last_snap["short_bucket"]["monthly_peak"] == pytest.approx(expected_peak)
        # Running-max: peak nunca decrece intra-mes.
        assert last_snap["short_bucket"]["monthly_peak"] >= prev_peak
        prev_peak = last_snap["short_bucket"]["monthly_peak"]

    # Final DD = 250 / 410 - 1.
    final_eq = -1_000.0 + 10 * 125.0  # -1000 + 1250 = 250
    assert last_snap["short_bucket"]["monthly_drawdown"] == pytest.approx(final_eq / 410.0 - 1.0)


def test_mid_month_first_activity_seeds_peak_from_that_point():
    """SCN-10: cero actividad al inicio del mes (peak=0) y la primera actividad
    favorable mid-month define el peak en ese instante. Sin DD retroactivo."""
    ledger = PortfolioLedger(starting_cash=10_000)
    quiet_days = [
        date(2026, 8, 3),
        date(2026, 8, 4),
        date(2026, 8, 5),
        date(2026, 8, 6),
        date(2026, 8, 7),
    ]
    for day in quiet_days:
        snap = ledger.mark_to_market(trading_day=day, daily_bars={})
        assert snap["short_bucket"]["monthly_peak"] == pytest.approx(0.0)
        assert snap["short_bucket"]["monthly_drawdown"] == pytest.approx(0.0)

    # Day 6: BUY 10@100, close=130 → short_cash=-1000, MV=1300, eq=300.
    activation = ledger.update_day(
        trading_day=date(2026, 8, 10),
        fills=[
            {
                "symbol": "TSLA",
                "side": "BUY",
                "qty": 10,
                "price": 100.0,
                "market": "US",
                "bucket": "short",
                "fee": 0.0,
            }
        ],
        daily_bars={"TSLA": {"close": 130.0}},
    )
    assert activation["short_bucket"]["monthly_peak"] == pytest.approx(300.0)
    assert activation["short_bucket"]["monthly_drawdown"] == pytest.approx(0.0)
