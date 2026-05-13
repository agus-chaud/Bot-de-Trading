"""Behavior tests for long_term_engine v1 (monthly sleeve, bands, intents)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from core_sim.long_term_engine import (
    LongTermEngineConfig,
    SatelliteLimits,
    build_long_term_orders_intent,
    current_weights_mtm,
    drift_per_line_pp,
    is_first_us_trading_day_of_week,
    is_first_us_trading_day_of_month,
    is_rebalance_day_by_rule,
    long_term_engine_config_from_policy_dict,
    should_rebalance_long,
    target_weights,
    validate_long_term_engine_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _policy_cfg():
    with (REPO_ROOT / "config" / "policy.v1.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _lt_from_repo() -> LongTermEngineConfig:
    return long_term_engine_config_from_policy_dict(_policy_cfg()["long_term_engine"])


def test_repo_policy_long_term_block_is_consistent():
    cfg = _lt_from_repo()
    validate_long_term_engine_config(cfg)
    tw = target_weights(cfg)
    assert abs(sum(tw.values()) - 1.0) < 1e-9
    assert set(tw) == {"SPY", "IWM", "QQQ"}


def test_is_first_us_trading_day_of_week_picks_earliest_session_in_week():
    sessions = frozenset(
        {
            date(2026, 4, 6),  # Monday
            date(2026, 4, 7),
            date(2026, 4, 13),  # next Monday
            date(2026, 4, 14),
        }
    )
    assert is_first_us_trading_day_of_week(date(2026, 4, 6), sessions) is True
    assert is_first_us_trading_day_of_week(date(2026, 4, 7), sessions) is False
    assert is_first_us_trading_day_of_week(date(2026, 4, 13), sessions) is True


def test_is_rebalance_day_by_rule_handles_weekly_and_monthly():
    sessions = frozenset(
        {
            date(2026, 4, 1),
            date(2026, 4, 2),
            date(2026, 5, 3),
            date(2026, 5, 4),
        }
    )
    assert is_rebalance_day_by_rule(
        trading_day=date(2026, 4, 1),
        us_sessions=sessions,
        rebalance_rule="first_us_trading_day_of_calendar_month",
    ) is True
    assert is_rebalance_day_by_rule(
        trading_day=date(2026, 4, 2),
        us_sessions=sessions,
        rebalance_rule="first_us_trading_day_of_calendar_month",
    ) is False
    assert is_first_us_trading_day_of_month(date(2026, 5, 3), sessions) is True


def test_should_rebalance_requires_day_and_band_breach():
    drift_ok = {"SPY": 1.0, "IWM": 1.0}
    drift_bad = {"SPY": 5.0, "IWM": 0.5}
    assert should_rebalance_long(is_rebalance_day=False, drift_pp_by_symbol=drift_bad, drift_threshold_pp=2.0) is False
    assert should_rebalance_long(is_rebalance_day=True, drift_pp_by_symbol=drift_ok, drift_threshold_pp=2.0) is False
    assert should_rebalance_long(is_rebalance_day=True, drift_pp_by_symbol=drift_bad, drift_threshold_pp=2.0) is True


def test_on_weekly_rebalance_day_within_drift_band_emits_no_orders():
    cfg = _lt_from_repo()
    us = frozenset({date(2026, 4, 1), date(2026, 4, 2)})
    day = date(2026, 4, 1)
    prices = {"SPY": 100.0, "IWM": 50.0, "QQQ": 300.0}
    # MTM exactly on targets for a 100k long sleeve
    mtm = 100_000.0
    qty = {"SPY": 0.55 * mtm / 100.0, "IWM": 0.30 * mtm / 50.0, "QQQ": 0.15 * mtm / 300.0}
    wl = frozenset({"SPY", "IWM", "QQQ"})
    intents, skips, _metrics = build_long_term_orders_intent(
        cfg,
        trading_day=day,
        us_sessions=us,
        long_bucket_mtm=mtm,
        long_cash=10_000.0,
        positions_qty=qty,
        prices=prices,
        whitelist_us=wl,
    )
    assert intents == []
    assert any(s.get("reason") == "within_drift_band" for s in skips)


def test_on_weekly_rebalance_day_out_of_band_generates_sell_and_buy_intents():
    cfg = _lt_from_repo()
    us = frozenset({date(2026, 4, 1)})
    day = date(2026, 4, 1)
    prices = {"SPY": 100.0, "IWM": 50.0, "QQQ": 300.0}
    mtm = 100_000.0
    # Overweight SPY vs target 0.55
    qty = {"SPY": 900.0, "IWM": 100.0, "QQQ": 50.0}
    wl = frozenset({"SPY", "IWM", "QQQ"})
    intents, skips, metrics = build_long_term_orders_intent(
        cfg,
        trading_day=day,
        us_sessions=us,
        long_bucket_mtm=mtm,
        long_cash=50_000.0,
        positions_qty=qty,
        prices=prices,
        whitelist_us=wl,
    )
    assert not any(s.get("reason") == "within_drift_band" for s in skips)
    assert metrics["intents_generated"] == len(intents) and len(intents) >= 1
    sides = {i["symbol"]: i["side"] for i in intents}
    assert sides.get("SPY") == "SELL"
    assert any(i["side"] == "BUY" for i in intents)


def test_missing_price_aborts_whole_cycle_without_partial_rebalance():
    cfg = _lt_from_repo()
    us = frozenset({date(2026, 4, 1)})
    day = date(2026, 4, 1)
    prices = {"SPY": 100.0, "IWM": 50.0}  # QQQ missing
    mtm = 100_000.0
    qty = {"SPY": 900.0, "IWM": 100.0, "QQQ": 50.0}
    wl = frozenset({"SPY", "IWM", "QQQ"})
    intents, skips, _metrics = build_long_term_orders_intent(
        cfg,
        trading_day=day,
        us_sessions=us,
        long_bucket_mtm=mtm,
        long_cash=50_000.0,
        positions_qty=qty,
        prices=prices,
        whitelist_us=wl,
    )
    assert intents == []
    assert any(s.get("reason") == "missing_or_invalid_price_abort_cycle" for s in skips)


def test_split_adjusted_qty_and_price_leave_weights_stable_so_band_can_hold():
    """Corporate actions are applied before this engine; split-adjusted state should not invent drift."""
    cfg = _lt_from_repo()
    us = frozenset({date(2026, 4, 1)})
    day = date(2026, 4, 1)
    mtm = 100_000.0
    pre_split_px = 100.0
    # 2:1 split: double qty, halve price — MTM per line unchanged vs pre-split on-target book
    qty = {"SPY": 0.55 * mtm / pre_split_px * 2.0, "IWM": 0.30 * mtm / 50.0, "QQQ": 0.15 * mtm / 300.0}
    prices = {"SPY": pre_split_px / 2.0, "IWM": 50.0, "QQQ": 300.0}
    wl = frozenset({"SPY", "IWM", "QQQ"})
    cur = current_weights_mtm(long_bucket_mtm=mtm, positions_qty=qty, prices=prices, universe=target_weights(cfg))
    drift = drift_per_line_pp(target_weights(cfg), cur)
    assert should_rebalance_long(is_rebalance_day=True, drift_pp_by_symbol=drift, drift_threshold_pp=2.0) is False
    intents, _skips, _m = build_long_term_orders_intent(
        cfg,
        trading_day=day,
        us_sessions=us,
        long_bucket_mtm=mtm,
        long_cash=10_000.0,
        positions_qty=qty,
        prices=prices,
        whitelist_us=wl,
    )
    assert intents == []


def test_turnover_cap_scales_trade_sizes_down():
    cfg = LongTermEngineConfig(
        core_lines=(("SPY", 0.55), ("IWM", 0.30)),
        satellite_lines=(("QQQ", 0.15),),
        satellite_limits=SatelliteLimits(0.20, 0.15, 3),
        drift_rebalance_threshold_pp=1.0,
        drift_convention="per_line",
        rebalance_rule="first_us_trading_day_of_calendar_week",
        max_long_rebalance_turnover_pct=0.05,
        satellite_markets=frozenset(["US"]),
    )
    validate_long_term_engine_config(cfg)
    us = frozenset({date(2026, 6, 1)})
    day = date(2026, 6, 1)
    prices = {"SPY": 100.0, "IWM": 50.0, "QQQ": 300.0}
    mtm = 100_000.0
    qty = {"SPY": 950.0, "IWM": 50.0, "QQQ": 50.0}
    wl = frozenset({"SPY", "IWM", "QQQ"})
    intents, _skips, metrics = build_long_term_orders_intent(
        cfg,
        trading_day=day,
        us_sessions=us,
        long_bucket_mtm=mtm,
        long_cash=80_000.0,
        positions_qty=qty,
        prices=prices,
        whitelist_us=wl,
    )
    assert metrics.get("targets_scaled_for_turnover_cap") is True
    traded = sum(float(i["intent_notional"]) for i in intents)
    assert traded <= mtm * 0.05 + 1.0  # sum of notionals bounded by rough turnover proxy


def test_not_whitelisted_symbol_blocks_cycle():
    cfg = _lt_from_repo()
    us = frozenset({date(2026, 4, 1)})
    day = date(2026, 4, 1)
    prices = {"SPY": 100.0, "IWM": 50.0, "QQQ": 300.0}
    mtm = 100_000.0
    qty = {"SPY": 900.0, "IWM": 100.0, "QQQ": 50.0}
    wl = frozenset({"SPY", "IWM"})  # QQQ missing from whitelist
    intents, skips, _m = build_long_term_orders_intent(
        cfg,
        trading_day=day,
        us_sessions=us,
        long_bucket_mtm=mtm,
        long_cash=50_000.0,
        positions_qty=qty,
        prices=prices,
        whitelist_us=wl,
    )
    assert intents == []
    assert any("symbol_not_whitelisted" in str(s.get("reason", "")) for s in skips)
