"""Integration tests: short-term pipeline + DailyEventBacktester."""

from datetime import date
from pathlib import Path

import pytest
import yaml

from core_sim import (
    CostModel,
    CorporateActionsStore,
    MarketCostConfig,
    PaperBrokerSim,
    PortfolioLedger,
    TradingCalendarStore,
    create_short_term_daily_backtester,
    load_merged_whitelist,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_policy() -> dict:
    with (REPO_ROOT / "config" / "policy.v1.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_spy_history(*, n: int = 25) -> list[dict[str, float]]:
    """Closes oldest->newest strictly before `trading_day`; last close = 100 + (n-1)."""
    return [{"close": float(100 + i), "volume": 1_000_000.0} for i in range(n)]


def _build_qqq_history_declining(*, n: int = 25) -> list[dict[str, float]]:
    base = 200.0
    return [{"close": float(base - i), "volume": 2_000_000.0} for i in range(n)]


def test_load_merged_whitelist_includes_core_etfs():
    policy = _load_policy()
    wl = load_merged_whitelist(REPO_ROOT, policy)
    assert wl["SPY"] == "US"
    assert wl["GGAL"] == "AR"


def test_short_term_pipeline_end_to_end_produces_fills():
    policy = _load_policy()
    ledger = PortfolioLedger(starting_cash=100_000.0)
    broker = PaperBrokerSim(
        ledger=ledger,
        cost_model=CostModel(
            market_configs={
                "US": MarketCostConfig(
                    commission_bps_per_side=1.0,
                    slippage_bps=2.0,
                    min_spread_bps=0.5,
                )
            }
        ),
    )
    calendar_store = TradingCalendarStore.from_yaml(str(REPO_ROOT / "config" / "calendars" / "trading_days.v1.yaml"))
    actions_store = CorporateActionsStore.from_yaml(str(REPO_ROOT / "config" / "corporate_actions" / "us_actions.v1.yaml"))

    backtester = create_short_term_daily_backtester(
        policy_doc=policy,
        repo_root=REPO_ROOT,
        ledger=ledger,
        broker=broker,
        calendar_store=calendar_store,
        corporate_actions_store=actions_store,
    )

    # Debe coincidir con `config/calendars/trading_days.v1.yaml` (sesiones explícitas).
    trading_day = date(2026, 4, 15)
    history = {
        "SPY": _build_spy_history(),
        "QQQ": _build_qqq_history_declining(),
    }
    daily_bars = {
        "SPY": {"open": 129.0, "high": 131.0, "low": 128.0, "close": 130.0, "volume": 80_000_000.0},
        "QQQ": {"open": 176.0, "high": 177.0, "low": 175.0, "close": 175.0, "volume": 30_000_000.0},
    }

    events = backtester.run_day(
        trading_day=trading_day,
        daily_bars=daily_bars,
        pipeline_context={"history_by_symbol": history},
    )

    assert [e.name for e in events] == list(backtester.EVENT_ORDER)
    sig = events[1].payload
    assert sig["engine"] == "short_term_v1"
    assert sig["metrics"]["selected"] >= 1

    proposed = events[2].payload
    assert isinstance(proposed, dict)
    assert proposed["sizing_metrics"]["intents_generated"] >= 1
    assert len(proposed["broker_orders"]) >= 1

    risk = events[3].payload
    assert isinstance(risk, list)
    assert len(risk) >= 1

    fills = events[4].payload
    assert len(fills) >= 1
    assert fills[0]["symbol"] == "SPY"


def test_short_term_risk_kill_switch_blocks_orders_same_month():
    policy = _load_policy()
    ledger = PortfolioLedger(starting_cash=100_000.0)
    broker = PaperBrokerSim(
        ledger=ledger,
        cost_model=CostModel(
            market_configs={
                "US": MarketCostConfig(
                    commission_bps_per_side=1.0,
                    slippage_bps=2.0,
                    min_spread_bps=0.5,
                )
            }
        ),
    )
    calendar_store = TradingCalendarStore.from_yaml(str(REPO_ROOT / "config" / "calendars" / "trading_days.v1.yaml"))
    actions_store = CorporateActionsStore.from_yaml(str(REPO_ROOT / "config" / "corporate_actions" / "us_actions.v1.yaml"))

    ledger.update_day(
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
    ledger.update_day(
        trading_day=date(2026, 4, 30),
        fills=[],
        daily_bars={"SPY": {"close": 90.0}},
    )
    assert ledger.mark_to_market(date(2026, 4, 30), {"SPY": {"close": 90.0}})["short_bucket"]["monthly_drawdown"] <= policy[
        "short_kill_switch_monthly_dd"
    ]

    backtester = create_short_term_daily_backtester(
        policy_doc=policy,
        repo_root=REPO_ROOT,
        ledger=ledger,
        broker=broker,
        calendar_store=calendar_store,
        corporate_actions_store=actions_store,
    )

    history = {"SPY": _build_spy_history()}
    daily_bars = {"SPY": {"open": 91.0, "high": 92.0, "low": 89.0, "close": 91.0, "volume": 80_000_000.0}}

    events = backtester.run_day(
        trading_day=date(2026, 4, 30),
        daily_bars=daily_bars,
        pipeline_context={"history_by_symbol": history},
    )

    assert events[3].payload == []
    assert events[4].payload == []


def test_no_trade_window_blocks_in_first_minutes():
    policy = _load_policy()
    ledger = PortfolioLedger(starting_cash=100_000.0)
    broker = PaperBrokerSim(
        ledger=ledger,
        cost_model=CostModel(
            market_configs={
                "US": MarketCostConfig(
                    commission_bps_per_side=1.0,
                    slippage_bps=2.0,
                    min_spread_bps=0.5,
                )
            }
        ),
    )
    calendar_store = TradingCalendarStore.from_yaml(str(REPO_ROOT / "config" / "calendars" / "trading_days.v1.yaml"))
    actions_store = CorporateActionsStore.from_yaml(str(REPO_ROOT / "config" / "corporate_actions" / "us_actions.v1.yaml"))

    backtester = create_short_term_daily_backtester(
        policy_doc=policy,
        repo_root=REPO_ROOT,
        ledger=ledger,
        broker=broker,
        calendar_store=calendar_store,
        corporate_actions_store=actions_store,
    )
    history = {"SPY": _build_spy_history()}
    daily = {"SPY": {"open": 129.0, "high": 131.0, "low": 128.0, "close": 130.0, "volume": 80_000_000.0}}
    events = backtester.run_day(
        trading_day=date(2026, 4, 15),
        daily_bars=daily,
        pipeline_context={"history_by_symbol": history, "session_minutes_from_open": 5},
    )
    assert events[2].payload.get("sizing_metrics", {}).get("halt_reason") == "no_trade_window"
    assert events[2].payload["broker_orders"] == []
    assert events[3].payload == []


def test_halt_data_quality_on_invalid_bar_in_daily_feed():
    policy = _load_policy()
    ledger = PortfolioLedger(starting_cash=100_000.0)
    broker = PaperBrokerSim(
        ledger=ledger,
        cost_model=CostModel(
            market_configs={
                "US": MarketCostConfig(
                    commission_bps_per_side=1.0,
                    slippage_bps=2.0,
                    min_spread_bps=0.5,
                )
            }
        ),
    )
    backtester = create_short_term_daily_backtester(
        policy_doc=policy,
        repo_root=REPO_ROOT,
        ledger=ledger,
        broker=broker,
    )
    history = {
        "SPY": _build_spy_history(),
        "QQQ": _build_spy_history(),
    }
    bad_bars = {
        "SPY": {"open": 129.0, "high": 131.0, "low": 128.0, "close": 130.0},
        "QQQ": {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10_000_000.0},
    }
    events = backtester.run_day(
        trading_day=date(2026, 4, 15),
        daily_bars=bad_bars,
        pipeline_context={"history_by_symbol": history},
    )
    assert not events[1].payload.get("risk_flags", {}).get("data_quality_ok", True)
    assert events[2].payload.get("sizing_metrics", {}).get("halt_reason") == "halt_data_quality"
    assert events[2].payload["broker_orders"] == []


def test_short_daily_loss_limit_stops_proposals():
    policy = _load_policy()
    ledger = PortfolioLedger(starting_cash=100_000.0)
    broker = PaperBrokerSim(
        ledger=ledger,
        cost_model=CostModel(
            market_configs={
                "US": MarketCostConfig(
                    commission_bps_per_side=1.0,
                    slippage_bps=2.0,
                    min_spread_bps=0.5,
                )
            }
        ),
    )
    ledger.update_day(
        date(2026, 4, 14),
        fills=[
            {
                "symbol": "SPY",
                "side": "BUY",
                "qty": 100.0,
                "price": 100.0,
                "market": "US",
                "bucket": "short",
            }
        ],
        daily_bars={"SPY": {"close": 100.0}},
    )
    backtester = create_short_term_daily_backtester(
        policy_doc=policy,
        repo_root=REPO_ROOT,
        ledger=ledger,
        broker=broker,
    )
    history = {"SPY": _build_spy_history()}
    daily = {"SPY": {"open": 100.0, "high": 100.0, "low": 97.0, "close": 97.0, "volume": 80_000_000.0}}
    events = backtester.run_day(
        trading_day=date(2026, 4, 15),
        daily_bars=daily,
        pipeline_context={"history_by_symbol": history},
    )
    assert events[2].payload.get("sizing_metrics", {}).get("halt_reason") == "short_daily_loss_limit"
    assert events[2].payload["broker_orders"] == []
