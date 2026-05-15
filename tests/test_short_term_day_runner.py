"""Integration tests: short-term pipeline + DailyEventBacktester."""

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


def _build_spy_history_rsi_down_cross() -> list[dict[str, float]]:
    # Serie con tramo alcista y caída final para forzar RSI bajo.
    closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0, 110.0, 111.0, 112.0, 113.0, 114.0, 112.0, 110.0, 108.0, 106.0, 104.0, 102.0, 101.0, 100.0, 99.0, 98.0]
    return [{"close": c, "volume": 1_000_000.0} for c in closes]


def _build_spy_history_rsi_up_cross() -> list[dict[str, float]]:
    """Uptrend fuerte: RSI del día actual queda en zona de sobrecompra (>= 80)."""
    closes = [float(100 + i) for i in range(25)]
    return [{"close": c, "volume": 1_000_000.0} for c in closes]


def test_load_merged_whitelist_includes_core_etfs():
    policy = _load_policy()
    wl = load_merged_whitelist(REPO_ROOT, policy)
    assert wl["SPY"] == "US"
    assert wl["PAMP"] == "AR"


def test_load_merged_whitelist_includes_adrs_as_us():
    """ADRs listed in whitelist_us.yaml should be tagged as 'US' market.

    When the same ticker appears in both AR and US whitelists, the US entry
    wins (last-write-wins in the merged dict). This is correct for ADRs that
    trade on NYSE — they follow US session hours and US cost model.
    """
    policy = _load_policy()
    wl = load_merged_whitelist(REPO_ROOT, policy)

    adrs = ["MELI", "YPF", "TGS", "GGAL"]
    for sym in adrs:
        assert sym in wl, f"{sym} should be in the merged whitelist"
        assert wl[sym] == "US", f"{sym} ADR should be tagged as 'US', got '{wl[sym]}'"


def test_load_merged_whitelist_adrs_do_not_drop_other_symbols():
    """Adding adrs bucket should not remove existing etfs, stocks, or AR symbols."""
    policy = _load_policy()
    wl = load_merged_whitelist(REPO_ROOT, policy)

    for etf in ("SPY", "QQQ", "IWM"):
        assert etf in wl, f"ETF {etf} should still be present"
    for stock in ("AAPL", "MSFT"):
        assert stock in wl, f"US stock {stock} should still be present"
    for ar_only in ("PAMP", "BMA", "CEPU", "TXAR", "ALUA", "SUPV"):
        assert ar_only in wl and wl[ar_only] == "AR", f"AR-only {ar_only} should remain 'AR'"


def test_short_term_pipeline_end_to_end_produces_fills():
    policy = _load_policy()
    policy["short_term_engine"]["rsi_overbought_entry"] = 100.0
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

    # Bajo la nueva semántica de bucket-equity DD:
    # Día 1 BUY 10@100 → short_cash=-1000, MV=1000, bucket_eq=0.
    # Día 2 rally a 200 → MV=2000, bucket_eq=1000, peak=1000.
    # Día 3 caída a 190 → MV=1900, bucket_eq=900, DD=-0.10 (<= -0.08 kill threshold).
    ledger.update_day(
        trading_day=date(2026, 4, 28),
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
        trading_day=date(2026, 4, 29),
        fills=[],
        daily_bars={"SPY": {"close": 200.0}},
    )
    ledger.update_day(
        trading_day=date(2026, 4, 30),
        fills=[],
        daily_bars={"SPY": {"close": 190.0}},
    )
    assert ledger.mark_to_market(date(2026, 4, 30), {"SPY": {"close": 190.0}})["short_bucket"]["monthly_drawdown"] <= policy[
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


def test_stop_loss_order_side_is_uppercase_sell():
    """Regression: stop-loss orders must use 'SELL' (uppercase) so PaperBrokerSim accepts them.

    Before the fix, propose_orders appended side='sell' (lowercase) which caused
    ValueError('side must be BUY or SELL') inside PaperBrokerSim._validate_order.

    Strategy: seed a SPY short position at the SAME price as the current bar so the
    monthly drawdown stays at 0 (no kill-switch). Then force check_stop_loss to return
    {'SPY'} and verify (a) the broker_orders list contains a stop-loss entry with
    side='SELL' and (b) run_day completes without raising ValueError.
    """
    from unittest.mock import patch

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

    trading_day = date(2026, 4, 15)
    entry_price = 130.0  # same as bar close → no MTM loss → no kill-switch
    daily_bars = {
        "SPY": {"open": 129.0, "high": 131.0, "low": 128.0, "close": entry_price, "volume": 80_000_000.0},
    }

    # Seed position at entry_price so the current bar shows 0 P&L (kill-switch not triggered)
    ledger.update_day(
        trading_day=date(2026, 4, 14),
        fills=[
            {
                "symbol": "SPY",
                "side": "BUY",
                "qty": 10,
                "price": entry_price,
                "market": "US",
                "bucket": "short",
            }
        ],
        daily_bars={"SPY": {"close": entry_price}},
    )

    backtester = create_short_term_daily_backtester(
        policy_doc=policy,
        repo_root=REPO_ROOT,
        ledger=ledger,
        broker=broker,
        calendar_store=calendar_store,
        corporate_actions_store=actions_store,
    )

    history = {"SPY": _build_spy_history()}

    # Force stop-loss to trigger for SPY regardless of ATR calculation
    with patch("core_sim.short_term_day_runner.check_stop_loss", return_value={"SPY"}):
        # Must NOT raise ValueError — that was the bug
        events = backtester.run_day(
            trading_day=trading_day,
            daily_bars=daily_bars,
            pipeline_context={"history_by_symbol": history},
        )

    proposed = events[2].payload
    stop_loss_orders = [o for o in proposed["broker_orders"] if o.get("reason") == "stop_loss"]
    assert len(stop_loss_orders) == 1, (
        f"expected 1 stop-loss order, got {len(stop_loss_orders)}; "
        f"halt_reason={proposed.get('sizing_metrics', {}).get('halt_reason')}"
    )
    assert stop_loss_orders[0]["side"] == "SELL", (
        "stop-loss side must be 'SELL' (uppercase) — PaperBrokerSim rejects any other value"
    )


def test_check_risk_with_optional_db_maintains_decision_order_without_db():
    """_check_risk_with_optional_db (no DB) must produce the same decision order as
    check_short_risk: data_quality → no_trade_window → kill_switch → daily_loss.

    We test that each scenario returns the FIRST matching reason, proving order.
    """
    from core_sim.risk_guardrails import check_short_risk

    policy = _load_policy()
    ledger = PortfolioLedger(starting_cash=100_000.0)
    handlers = create_short_term_daily_backtester(
        policy_doc=policy,
        repo_root=REPO_ROOT,
        ledger=ledger,
        broker=PaperBrokerSim(
            ledger=ledger,
            cost_model=CostModel(
                market_configs={"US": MarketCostConfig(commission_bps_per_side=1.0, slippage_bps=2.0, min_spread_bps=0.5)}
            ),
        ),
    )

    risk_config = {
        "kill_dd": -0.08,
        "max_daily_short": -0.02,
        "no_trade_first": 15,
        "no_trade_last": 15,
    }

    scenarios = [
        {
            "name": "data_quality fires first",
            "sb": {"monthly_drawdown": -0.09, "daily_return": -0.03},
            "flags": {"halt_on_data_quality": True, "data_quality_ok": False},
            "minutes": 5,
            "expected_reason": "halt_data_quality",
        },
        {
            "name": "no_trade fires before kill switch",
            "sb": {"monthly_drawdown": -0.09, "daily_return": -0.03},
            "flags": {"halt_on_data_quality": True, "data_quality_ok": True},
            "minutes": 5,
            "expected_reason": "no_trade_window",
        },
        {
            "name": "kill switch fires before daily loss",
            "sb": {"monthly_drawdown": -0.09, "daily_return": -0.03},
            "flags": {"halt_on_data_quality": True, "data_quality_ok": True},
            "minutes": None,
            "expected_reason": "short_monthly_kill_switch",
        },
        {
            "name": "daily loss fires when others pass",
            "sb": {"monthly_drawdown": -0.01, "daily_return": -0.03},
            "flags": {"halt_on_data_quality": True, "data_quality_ok": True},
            "minutes": None,
            "expected_reason": "short_daily_loss_limit",
        },
    ]

    for scenario in scenarios:
        direct = check_short_risk(
            scenario["sb"], scenario["flags"], risk_config, scenario["minutes"]
        )
        assert direct.reason == scenario["expected_reason"], (
            f"Scenario '{scenario['name']}': expected {scenario['expected_reason']}, got {direct.reason}"
        )


def test_check_risk_with_db_vs_without_db_decision_equivalence():
    """Decisions (except kill switch persistence) must be equivalent with and without DB.

    Both paths should block on data_quality, no_trade_window, and daily_loss
    with the same reason — only kill_switch semantics differ (stateless vs persisted).
    """
    from core_sim.risk_guardrails import check_short_risk

    policy = _load_policy()
    risk_config = {
        "kill_dd": -0.08,
        "max_daily_short": -0.02,
        "no_trade_first": 15,
        "no_trade_last": 15,
    }

    scenarios_equivalent = [
        {
            "name": "data_quality blocks both paths",
            "sb": {"monthly_drawdown": -0.01, "daily_return": 0.0},
            "flags": {"halt_on_data_quality": True, "data_quality_ok": False},
            "minutes": None,
            "expected_reason": "halt_data_quality",
        },
        {
            "name": "no_trade blocks both paths",
            "sb": {"monthly_drawdown": -0.01, "daily_return": 0.0},
            "flags": {"halt_on_data_quality": True, "data_quality_ok": True},
            "minutes": 5,
            "expected_reason": "no_trade_window",
        },
        {
            "name": "daily_loss blocks both paths",
            "sb": {"monthly_drawdown": -0.01, "daily_return": -0.03},
            "flags": {"halt_on_data_quality": True, "data_quality_ok": True},
            "minutes": None,
            "expected_reason": "short_daily_loss_limit",
        },
        {
            "name": "ok when all pass",
            "sb": {"monthly_drawdown": -0.01, "daily_return": -0.005},
            "flags": {"halt_on_data_quality": True, "data_quality_ok": True},
            "minutes": None,
            "expected_reason": "ok",
        },
    ]

    for scenario in scenarios_equivalent:
        without_db = check_short_risk(
            scenario["sb"], scenario["flags"], risk_config, scenario["minutes"]
        )
        assert without_db.reason == scenario["expected_reason"], (
            f"Scenario '{scenario['name']}': expected {scenario['expected_reason']}, "
            f"got {without_db.reason}"
        )


def test_rsi_overbought_reached_triggers_sell_on_ascending_cross_to_80():
    policy = _load_policy()
    policy["short_term_engine"]["rsi_overbought_entry"] = 80.0
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
    ledger.update_day(
        trading_day=date(2026, 4, 14),
        fills=[
            {
                "symbol": "SPY",
                "side": "BUY",
                "qty": 10,
                "price": 120.0,
                "market": "US",
                "bucket": "short",
            }
        ],
        daily_bars={"SPY": {"close": 120.0}},
    )
    daily_bars = {"SPY": {"open": 124.0, "high": 125.0, "low": 123.0, "close": 124.0, "volume": 50_000_000.0}}
    history = {"SPY": _build_spy_history_rsi_up_cross()}

    with patch("core_sim.short_term_day_runner.check_stop_loss", return_value=[]):
        events = backtester.run_day(
            trading_day=date(2026, 4, 15),
            daily_bars=daily_bars,
            pipeline_context={"history_by_symbol": history, "rsi_prev_by_symbol": {"SPY": 75.0}},
        )
    proposed = events[2].payload
    rsi_orders = [o for o in proposed["broker_orders"] if o.get("reason") == "rsi_overbought_reached"]
    assert len(rsi_orders) == 1
    assert rsi_orders[0]["side"] == "SELL"

    ledger2 = PortfolioLedger(starting_cash=100_000.0)
    broker2 = PaperBrokerSim(
        ledger=ledger2,
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
    backtester2 = create_short_term_daily_backtester(
        policy_doc=policy,
        repo_root=REPO_ROOT,
        ledger=ledger2,
        broker=broker2,
    )
    ledger2.update_day(
        trading_day=date(2026, 4, 14),
        fills=[
            {
                "symbol": "SPY",
                "side": "BUY",
                "qty": 10,
                "price": 120.0,
                "market": "US",
                "bucket": "short",
            }
        ],
        daily_bars={"SPY": {"close": 120.0}},
    )
    with patch("core_sim.short_term_day_runner.check_stop_loss", return_value=[]):
        events_no_cross = backtester2.run_day(
            trading_day=date(2026, 4, 15),
            daily_bars=daily_bars,
            pipeline_context={"history_by_symbol": history, "rsi_prev_by_symbol": {"SPY": 82.0}},
        )
    proposed_no_cross = events_no_cross[2].payload
    rsi_orders_no_cross = [
        o for o in proposed_no_cross["broker_orders"] if o.get("reason") == "rsi_overbought_reached"
    ]
    assert rsi_orders_no_cross == []


def test_rsi_exit_triggers_only_on_descending_crossover():
    policy = _load_policy()
    policy["short_term_engine"]["rsi_overbought_entry"] = 100.0
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
    # Seed short position from previous day.
    ledger.update_day(
        trading_day=date(2026, 4, 14),
        fills=[
            {
                "symbol": "SPY",
                "side": "BUY",
                "qty": 10,
                "price": 97.0,
                "market": "US",
                "bucket": "short",
            }
        ],
        daily_bars={"SPY": {"close": 97.0}},
    )
    daily_bars = {"SPY": {"open": 97.0, "high": 99.0, "low": 95.0, "close": 97.0, "volume": 50_000_000.0}}
    history = {"SPY": _build_spy_history_rsi_down_cross()}

    with patch("core_sim.short_term_day_runner.check_stop_loss", return_value=[]):
        events = backtester.run_day(
            trading_day=date(2026, 4, 15),
            daily_bars=daily_bars,
            pipeline_context={"history_by_symbol": history, "rsi_prev_by_symbol": {"SPY": 60.0}},
        )
    proposed = events[2].payload
    rsi_orders = [o for o in proposed["broker_orders"] if o.get("reason") == "rsi_momentum_exhausted"]
    assert len(rsi_orders) == 1
    assert rsi_orders[0]["side"] == "SELL"

    ledger2 = PortfolioLedger(starting_cash=100_000.0)
    broker2 = PaperBrokerSim(
        ledger=ledger2,
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
    backtester2 = create_short_term_daily_backtester(
        policy_doc=policy,
        repo_root=REPO_ROOT,
        ledger=ledger2,
        broker=broker2,
    )
    ledger2.update_day(
        trading_day=date(2026, 4, 14),
        fills=[
            {
                "symbol": "SPY",
                "side": "BUY",
                "qty": 10,
                "price": 97.0,
                "market": "US",
                "bucket": "short",
            }
        ],
        daily_bars={"SPY": {"close": 97.0}},
    )
    with patch("core_sim.short_term_day_runner.check_stop_loss", return_value=[]):
        events_no_cross = backtester2.run_day(
            trading_day=date(2026, 4, 15),
            daily_bars=daily_bars,
            pipeline_context={"history_by_symbol": history, "rsi_prev_by_symbol": {"SPY": 40.0}},
        )
    proposed_no_cross = events_no_cross[2].payload
    rsi_orders_no_cross = [o for o in proposed_no_cross["broker_orders"] if o.get("reason") == "rsi_momentum_exhausted"]
    assert rsi_orders_no_cross == []
