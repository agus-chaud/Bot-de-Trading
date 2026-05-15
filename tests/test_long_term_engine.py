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
    is_first_ar_business_day_of_month,
    is_first_ar_business_day_of_week,
    is_first_us_trading_day_of_month,
    is_first_us_trading_day_of_week,
    is_rebalance_day_by_rule,
    long_term_engine_config_from_policy_dict,
    should_rebalance_long,
    target_weights,
    validate_long_term_engine_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# Precios de prueba para el bloque largo AR del policy.v1.yaml por defecto (GGAL / PAMP / SPY CEDEAR).
_PX_GGAL = 1000.0
_PX_PAMP = 500.0
_PX_SPY = 200.0


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
    assert set(tw) == {"GGAL", "PAMP", "SPY"}


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


def test_is_rebalance_day_by_rule_handles_weekly_and_monthly_us_and_ar():
    us_days = frozenset(
        {
            date(2026, 4, 1),
            date(2026, 4, 2),
            date(2026, 5, 3),
            date(2026, 5, 4),
        }
    )
    assert (
        is_rebalance_day_by_rule(
            trading_day=date(2026, 4, 1),
            rebalance_rule="first_us_trading_day_of_calendar_month",
            calendar_sessions=us_days,
        )
        is True
    )
    assert (
        is_rebalance_day_by_rule(
            trading_day=date(2026, 4, 2),
            rebalance_rule="first_us_trading_day_of_calendar_month",
            calendar_sessions=us_days,
        )
        is False
    )
    assert is_first_us_trading_day_of_month(date(2026, 5, 3), us_days) is True

    # Solo miércoles en la semana ISO del 2026-04-01 → primer hábil AR semanal es ese día.
    ar_week = frozenset({date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 3)})
    assert (
        is_rebalance_day_by_rule(
            trading_day=date(2026, 4, 1),
            rebalance_rule="first_ar_business_day_of_calendar_week",
            calendar_sessions=ar_week,
        )
        is True
    )
    assert (
        is_rebalance_day_by_rule(
            trading_day=date(2026, 4, 2),
            rebalance_rule="first_ar_business_day_of_calendar_week",
            calendar_sessions=ar_week,
        )
        is False
    )
    assert is_first_ar_business_day_of_week(date(2026, 4, 1), ar_week) is True


def test_first_ar_business_day_of_month_triggers_monthly_rebalance_rule():
    """Paso 5: el primer hábil AR del mes dispara ``first_ar_business_day_of_calendar_month``."""
    ar_march = frozenset(
        {
            date(2026, 3, 2),
            date(2026, 3, 3),
            date(2026, 3, 4),
        }
    )
    assert is_first_ar_business_day_of_month(date(2026, 3, 2), ar_march) is True
    assert is_first_ar_business_day_of_month(date(2026, 3, 3), ar_march) is False
    assert (
        is_rebalance_day_by_rule(
            trading_day=date(2026, 3, 2),
            rebalance_rule="first_ar_business_day_of_calendar_month",
            calendar_sessions=ar_march,
        )
        is True
    )
    assert (
        is_rebalance_day_by_rule(
            trading_day=date(2026, 3, 3),
            rebalance_rule="first_ar_business_day_of_calendar_month",
            calendar_sessions=ar_march,
        )
        is False
    )


def test_should_rebalance_requires_day_and_band_breach():
    drift_ok = {"GGAL": 1.0, "PAMP": 1.0}
    drift_bad = {"GGAL": 5.0, "PAMP": 0.5}
    assert (
        should_rebalance_long(is_rebalance_day=False, drift_pp_by_symbol=drift_bad, drift_threshold_pp=2.0)
        is False
    )
    assert (
        should_rebalance_long(is_rebalance_day=True, drift_pp_by_symbol=drift_ok, drift_threshold_pp=2.0)
        is False
    )
    assert (
        should_rebalance_long(is_rebalance_day=True, drift_pp_by_symbol=drift_bad, drift_threshold_pp=2.0)
        is True
    )


def test_on_weekly_rebalance_day_within_drift_band_emits_no_orders():
    cfg = _lt_from_repo()
    ar = frozenset({date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 3)})
    day = date(2026, 4, 1)
    prices = {"GGAL": _PX_GGAL, "PAMP": _PX_PAMP, "SPY": _PX_SPY}
    mtm = 100_000.0
    qty = {
        "GGAL": 0.42 * mtm / _PX_GGAL,
        "PAMP": 0.43 * mtm / _PX_PAMP,
        "SPY": 0.15 * mtm / _PX_SPY,
    }
    wl = frozenset({"GGAL", "PAMP", "SPY"})
    intents, skips, _metrics = build_long_term_orders_intent(
        cfg,
        trading_day=day,
        calendar_sessions=ar,
        long_bucket_mtm=mtm,
        long_cash=10_000.0,
        positions_qty=qty,
        prices=prices,
        whitelist_long=wl,
    )
    assert intents == []
    assert any(s.get("reason") == "within_drift_band" for s in skips)


def test_on_weekly_rebalance_day_out_of_band_generates_sell_and_buy_intents():
    cfg = _lt_from_repo()
    ar = frozenset({date(2026, 4, 1)})
    day = date(2026, 4, 1)
    prices = {"GGAL": _PX_GGAL, "PAMP": _PX_PAMP, "SPY": _PX_SPY}
    mtm = 100_000.0
    qty = {"GGAL": 900.0, "PAMP": 100.0, "SPY": 50.0}
    wl = frozenset({"GGAL", "PAMP", "SPY"})
    intents, skips, metrics = build_long_term_orders_intent(
        cfg,
        trading_day=day,
        calendar_sessions=ar,
        long_bucket_mtm=mtm,
        long_cash=50_000.0,
        positions_qty=qty,
        prices=prices,
        whitelist_long=wl,
    )
    assert not any(s.get("reason") == "within_drift_band" for s in skips)
    assert metrics["intents_generated"] == len(intents) and len(intents) >= 1
    sides = {i["symbol"]: i["side"] for i in intents}
    assert sides.get("GGAL") == "SELL"
    assert any(i["side"] == "BUY" for i in intents)
    assert all(i.get("market") == "AR" for i in intents)


def test_missing_price_aborts_whole_cycle_without_partial_rebalance():
    cfg = _lt_from_repo()
    ar = frozenset({date(2026, 4, 1)})
    day = date(2026, 4, 1)
    prices = {"GGAL": _PX_GGAL, "PAMP": _PX_PAMP}
    mtm = 100_000.0
    qty = {"GGAL": 900.0, "PAMP": 100.0, "SPY": 50.0}
    wl = frozenset({"GGAL", "PAMP", "SPY"})
    intents, skips, _metrics = build_long_term_orders_intent(
        cfg,
        trading_day=day,
        calendar_sessions=ar,
        long_bucket_mtm=mtm,
        long_cash=50_000.0,
        positions_qty=qty,
        prices=prices,
        whitelist_long=wl,
    )
    assert intents == []
    assert any(s.get("reason") == "missing_or_invalid_price_abort_cycle" for s in skips)


def test_split_adjusted_qty_and_price_leave_weights_stable_so_band_can_hold():
    """Corporate actions are applied before this engine; split-adjusted state should not invent drift."""
    cfg = _lt_from_repo()
    ar = frozenset({date(2026, 4, 1)})
    day = date(2026, 4, 1)
    mtm = 100_000.0
    pre_split_px = 1000.0
    qty = {
        "GGAL": 0.42 * mtm / pre_split_px * 2.0,
        "PAMP": 0.43 * mtm / _PX_PAMP,
        "SPY": 0.15 * mtm / _PX_SPY,
    }
    prices = {"GGAL": pre_split_px / 2.0, "PAMP": _PX_PAMP, "SPY": _PX_SPY}
    wl = frozenset({"GGAL", "PAMP", "SPY"})
    cur = current_weights_mtm(long_bucket_mtm=mtm, positions_qty=qty, prices=prices, universe=target_weights(cfg))
    drift = drift_per_line_pp(target_weights(cfg), cur)
    assert should_rebalance_long(is_rebalance_day=True, drift_pp_by_symbol=drift, drift_threshold_pp=2.0) is False
    intents, _skips, _m = build_long_term_orders_intent(
        cfg,
        trading_day=day,
        calendar_sessions=ar,
        long_bucket_mtm=mtm,
        long_cash=10_000.0,
        positions_qty=qty,
        prices=prices,
        whitelist_long=wl,
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
        calendar_sessions=us,
        long_bucket_mtm=mtm,
        long_cash=80_000.0,
        positions_qty=qty,
        prices=prices,
        whitelist_long=wl,
    )
    assert metrics.get("targets_scaled_for_turnover_cap") is True
    traded = sum(float(i["intent_notional"]) for i in intents)
    assert traded <= mtm * 0.05 + 1.0


def test_not_whitelisted_symbol_blocks_cycle():
    cfg = _lt_from_repo()
    ar = frozenset({date(2026, 4, 1)})
    day = date(2026, 4, 1)
    prices = {"GGAL": _PX_GGAL, "PAMP": _PX_PAMP, "SPY": _PX_SPY}
    mtm = 100_000.0
    qty = {"GGAL": 900.0, "PAMP": 100.0, "SPY": 50.0}
    wl = frozenset({"GGAL", "PAMP"})
    intents, skips, _m = build_long_term_orders_intent(
        cfg,
        trading_day=day,
        calendar_sessions=ar,
        long_bucket_mtm=mtm,
        long_cash=50_000.0,
        positions_qty=qty,
        prices=prices,
        whitelist_long=wl,
    )
    assert intents == []
    assert any("symbol_not_whitelisted" in str(s.get("reason", "")) for s in skips)
