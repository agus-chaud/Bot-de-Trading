"""Tests for the daily event engine queue."""

from datetime import date
from pathlib import Path

import pytest

from core_sim import CorporateActionsStore, DailyEventBacktester, PortfolioLedger, TradingCalendarStore


def test_daily_event_pipeline_runs_in_expected_order():
    trading_day = date(2026, 4, 15)
    daily_bars = {
        "SPY": {"open": 510.0, "high": 515.0, "low": 508.0, "close": 514.0, "volume": 100_000_000}
    }

    def generate_signals(**_):
        return [{"symbol": "SPY", "side": "BUY", "score": 0.81}]

    def propose_orders(**kwargs):
        signals = kwargs["signals"]
        return [{"symbol": s["symbol"], "side": s["side"], "qty": 10} for s in signals]

    def risk_check(**kwargs):
        return kwargs["proposed_orders"]

    def fill_orders(**kwargs):
        close_price = kwargs["daily_bars"]["SPY"]["close"]
        return [{"symbol": "SPY", "side": "BUY", "qty": 10, "price": close_price}]

    def update_ledger(**kwargs):
        filled_notional = sum(fill["qty"] * fill["price"] for fill in kwargs["fills"])
        return {"cash": 100_000 - filled_notional, "positions": {"SPY": 10}}

    backtester = DailyEventBacktester(
        generate_signals=generate_signals,
        propose_orders=propose_orders,
        risk_check=risk_check,
        fill_orders=fill_orders,
        update_ledger=update_ledger,
    )

    events = backtester.run_day(trading_day=trading_day, daily_bars=daily_bars)

    assert [event.name for event in events] == list(DailyEventBacktester.EVENT_ORDER)
    assert events[0].payload["trading_day"] == "2026-04-15"
    assert events[1].payload[0]["symbol"] == "SPY"
    assert events[5].payload["positions"]["SPY"] == 10


def test_market_open_event_uses_sorted_symbols():
    backtester = DailyEventBacktester(
        generate_signals=lambda **_: [],
        propose_orders=lambda **_: [],
        risk_check=lambda **_: [],
        fill_orders=lambda **_: [],
        update_ledger=lambda **_: {"cash": 1_000, "positions": {}},
    )

    events = backtester.run_day(
        trading_day=date(2026, 4, 15),
        daily_bars={"QQQ": {}, "IWM": {}, "SPY": {}},
    )

    assert events[0].name == "MarketOpen"
    assert events[0].payload["symbols"] == ("IWM", "QQQ", "SPY")


def test_market_open_enriches_calendar_and_corporate_actions():
    calendar_store = TradingCalendarStore.from_yaml("config/calendars/trading_days.v1.yaml")
    actions_store = CorporateActionsStore.from_yaml("config/corporate_actions/us_actions.v1.yaml")
    backtester = DailyEventBacktester(
        generate_signals=lambda **_: [],
        propose_orders=lambda **_: [],
        risk_check=lambda **_: [],
        fill_orders=lambda **_: [],
        update_ledger=lambda **_: {"cash": 1_000, "positions": {}},
        calendar_store=calendar_store,
        corporate_actions_store=actions_store,
    )

    events = backtester.run_day(
        trading_day=date(2026, 4, 15),
        daily_bars={"SPY": {}, "QQQ": {}},
    )

    market_open = events[0].payload
    assert market_open["is_us_session"] is True
    assert market_open["is_ar_business_day"] is True
    assert market_open["corporate_actions"] == [
        {
            "date": "2026-04-15",
            "symbol": "SPY",
            "action_type": "dividend",
            "value": 1.75,
        }
    ]


def test_corporate_actions_rejects_negative_dividend(tmp_path: Path):
    actions_file = tmp_path / "bad_dividend.yaml"
    actions_file.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "market: US",
                "supported_types:",
                "  - split",
                "  - dividend",
                "actions:",
                "  - date: 2026-04-15",
                "    symbol: SPY",
                "    action_type: dividend",
                "    cash_amount: -0.5",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="cash_amount must be >= 0"):
        CorporateActionsStore.from_yaml(actions_file)


def test_corporate_actions_rejects_non_positive_split_ratio(tmp_path: Path):
    actions_file = tmp_path / "bad_split.yaml"
    actions_file.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "market: US",
                "supported_types:",
                "  - split",
                "  - dividend",
                "actions:",
                "  - date: 2026-04-16",
                "    symbol: IWM",
                "    action_type: split",
                "    split_ratio: 0",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="split_ratio must be > 0"):
        CorporateActionsStore.from_yaml(actions_file)


def test_corporate_actions_rejects_duplicate_symbol_date_type(tmp_path: Path):
    actions_file = tmp_path / "duplicate.yaml"
    actions_file.write_text(
        "\n".join(
            [
                "schema_version: 1",
                "market: US",
                "supported_types:",
                "  - split",
                "  - dividend",
                "actions:",
                "  - date: 2026-04-15",
                "    symbol: SPY",
                "    action_type: dividend",
                "    cash_amount: 1.0",
                "  - date: 2026-04-15",
                "    symbol: SPY",
                "    action_type: dividend",
                "    cash_amount: 1.1",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate corporate action"):
        CorporateActionsStore.from_yaml(actions_file)


def test_daily_event_pipeline_supports_ledger_snapshot_payload():
    trading_day = date(2026, 4, 15)
    daily_bars = {"SPY": {"open": 510.0, "high": 515.0, "low": 508.0, "close": 514.0, "volume": 100_000_000}}
    ledger = PortfolioLedger(starting_cash=100_000)

    def generate_signals(**_):
        return [{"symbol": "SPY", "side": "BUY", "score": 0.81}]

    def propose_orders(**kwargs):
        signal = kwargs["signals"][0]
        return [{"symbol": signal["symbol"], "side": signal["side"], "qty": 10, "market": "US", "bucket": "short"}]

    def risk_check(**kwargs):
        return kwargs["proposed_orders"]

    def fill_orders(**kwargs):
        close_price = kwargs["daily_bars"]["SPY"]["close"]
        order = kwargs["approved_orders"][0]
        return [
            {
                "symbol": order["symbol"],
                "side": order["side"],
                "qty": order["qty"],
                "price": close_price,
                "market": order["market"],
                "bucket": order["bucket"],
                "fee": 0.0,
            }
        ]

    def update_ledger(**kwargs):
        return ledger.update_day(
            trading_day=kwargs["trading_day"],
            fills=kwargs["fills"],
            daily_bars=kwargs["daily_bars"],
        )

    backtester = DailyEventBacktester(
        generate_signals=generate_signals,
        propose_orders=propose_orders,
        risk_check=risk_check,
        fill_orders=fill_orders,
        update_ledger=update_ledger,
    )

    events = backtester.run_day(trading_day=trading_day, daily_bars=daily_bars)
    ledger_snapshot = events[-1].payload

    assert ledger_snapshot["equity_total"] == pytest.approx(100_000.0)
    assert ledger_snapshot["realized_pnl_total"] == pytest.approx(0.0)
    assert ledger_snapshot["short_bucket"]["monthly_drawdown"] == pytest.approx(0.0)
