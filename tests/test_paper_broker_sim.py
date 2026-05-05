"""Behavior tests for paper broker simulator interface."""

from datetime import date

import pytest

from core_sim import CostModel, MarketCostConfig, PaperBrokerSim, PortfolioLedger


def _build_broker() -> PaperBrokerSim:
    ledger = PortfolioLedger(starting_cash=100_000)
    cost_model = CostModel(
        market_configs={
            "US": MarketCostConfig(
                commission_bps_per_side=1.0,
                slippage_bps=2.0,
                min_spread_bps=0.5,
            )
        }
    )
    return PaperBrokerSim(ledger=ledger, cost_model=cost_model)


def test_should_place_order_and_update_positions_and_cash():
    broker = _build_broker()

    fill = broker.place_order(
        {
            "symbol": "SPY",
            "side": "BUY",
            "qty": 100,
            "price": 50.0,
            "market": "US",
            "bucket": "long",
        },
        trading_day=date(2026, 4, 15),
    )

    assert fill["cost_breakdown"]["total"] == pytest.approx(1.75)
    assert fill["fee"] == pytest.approx(1.75)
    assert broker.get_cash() == pytest.approx(94_998.25)
    assert broker.get_positions()["SPY"]["qty"] == pytest.approx(100.0)


def test_should_support_sell_and_track_fill_history():
    broker = _build_broker()
    d = date(2026, 4, 10)
    broker.place_order(
        {
            "symbol": "QQQ",
            "side": "BUY",
            "qty": 10,
            "price": 100.0,
            "market": "US",
            "bucket": "short",
        },
        trading_day=d,
    )
    sell_fill = broker.place_order(
        {
            "symbol": "QQQ",
            "side": "SELL",
            "qty": 4,
            "price": 110.0,
            "market": "US",
            "bucket": "short",
        },
        trading_day=d,
    )

    fills = broker.get_fills()
    assert len(fills) == 2
    assert sell_fill["side"] == "SELL"
    assert broker.get_positions()["QQQ"]["qty"] == pytest.approx(6.0)


def test_should_reject_invalid_order_payloads():
    broker = _build_broker()

    with pytest.raises(ValueError, match="order missing required keys"):
        broker.place_order({"symbol": "SPY"}, trading_day=date(2026, 1, 1))

    with pytest.raises(ValueError, match="side must be BUY or SELL"):
        broker.place_order(
            {
                "symbol": "SPY",
                "side": "HOLD",
                "qty": 1,
                "price": 100.0,
                "market": "US",
                "bucket": "long",
            },
            trading_day=date(2026, 1, 1),
        )


def test_should_fill_orders_using_daily_close_when_price_not_provided():
    broker = _build_broker()

    fills = broker.fill_orders(
        trading_day="2026-04-15",
        approved_orders=[
            {
                "symbol": "SPY",
                "side": "BUY",
                "qty": 10,
                "market": "US",
                "bucket": "long",
            }
        ],
        daily_bars={"SPY": {"close": 500.0}},
    )

    assert len(fills) == 1
    assert fills[0]["price"] == pytest.approx(500.0)
    assert broker.get_positions()["SPY"]["qty"] == pytest.approx(10.0)
