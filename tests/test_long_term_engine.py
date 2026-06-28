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

# Precios de prueba para el bloque largo AR diversificado (ADR-063): GGAL/PAMP/TXAR + SPY/QQQ/KO.
_PX_GGAL = 1000.0
_PX_PAMP = 500.0
_PX_SPY = 200.0
_PX = {"GGAL": _PX_GGAL, "PAMP": _PX_PAMP, "TXAR": 300.0,
       "SPY": _PX_SPY, "QQQ": 400.0, "KO": 600.0}


def _prices_for(cfg) -> dict:
    return {s: _PX[s] for s in target_weights(cfg)}


def _on_target_qty(cfg, mtm: float) -> dict:
    """Cantidades que dejan cada línea EXACTAMENTE en su peso objetivo (drift 0)."""
    return {s: w * mtm / _PX[s] for s, w in target_weights(cfg).items()}


def _policy_cfg():
    with (REPO_ROOT / "config" / "policy.v1.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _lt_from_repo() -> LongTermEngineConfig:
    return long_term_engine_config_from_policy_dict(_policy_cfg()["long_term_engine"])


def _lt_policy_block() -> dict:
    return dict(_policy_cfg()["long_term_engine"])


# Fase 1 (SDD long-cash-exposure) — la perilla de exposición y el flag de cash.

def test_default_long_config_is_fully_invested():
    cfg = _lt_from_repo()
    assert cfg.allow_cash is False
    assert cfg.equity_exposure == 1.0


def test_allow_cash_false_forces_full_exposure():
    block = _lt_policy_block()
    block["equity_exposure"] = 0.5  # presente pero sin flag → debe ignorarse
    cfg = long_term_engine_config_from_policy_dict(block)
    assert cfg.allow_cash is False
    assert cfg.equity_exposure == 1.0  # idéntico a producción


def test_allow_cash_true_honors_exposure():
    block = _lt_policy_block()
    block["allow_cash"] = True
    block["equity_exposure"] = 0.6
    cfg = long_term_engine_config_from_policy_dict(block)
    assert cfg.allow_cash is True
    assert abs(cfg.equity_exposure - 0.6) < 1e-9
    validate_long_term_engine_config(cfg)  # 0.6 es válido; los pesos siguen sumando 1.0


def test_validate_rejects_out_of_range_exposure():
    block = _lt_policy_block()
    block["allow_cash"] = True
    block["equity_exposure"] = 1.5
    cfg = long_term_engine_config_from_policy_dict(block)
    raised = False
    try:
        validate_long_term_engine_config(cfg)
    except ValueError:
        raised = True
    assert raised, "equity_exposure fuera de [0,1] debe ser rechazado"


# Fase 2 (SDD long-cash-exposure) — la mecánica de la perilla sobre los intents.

def test_exposure_one_is_noop_when_on_target():
    block = _lt_policy_block()
    block["allow_cash"] = True
    block["equity_exposure"] = 1.0
    cfg = long_term_engine_config_from_policy_dict(block)
    ar = frozenset({date(2026, 4, 1)})
    day = date(2026, 4, 1)
    mtm = 100_000.0
    prices = _prices_for(cfg)
    wl = frozenset(target_weights(cfg))
    qty = _on_target_qty(cfg, mtm)  # 100% invertido en objetivo
    intents, skips, _m = build_long_term_orders_intent(
        cfg, trading_day=day, calendar_sessions=ar, long_bucket_mtm=mtm,
        long_cash=0.0, positions_qty=qty, prices=prices, whitelist_long=wl,
    )
    assert intents == []  # exposure 1.0 = sin efecto, idéntico a producción
    assert any(s.get("reason") == "within_drift_band" for s in skips)


def test_exposure_half_de_risks_to_cash():
    block = _lt_policy_block()
    block["allow_cash"] = True
    block["equity_exposure"] = 0.5
    cfg = long_term_engine_config_from_policy_dict(block)
    ar = frozenset({date(2026, 4, 1)})
    day = date(2026, 4, 1)
    mtm = 100_000.0
    prices = _prices_for(cfg)
    wl = frozenset(target_weights(cfg))
    qty = _on_target_qty(cfg, mtm)  # arranca 100% invertido
    intents, _skips, _m = build_long_term_orders_intent(
        cfg, trading_day=day, calendar_sessions=ar, long_bucket_mtm=mtm,
        long_cash=0.0, positions_qty=qty, prices=prices, whitelist_long=wl,
    )
    assert intents, "exposure 0.5 debe des-riesgar (vender) desde 100% invertido"
    assert all(i["side"] == "SELL" for i in intents)  # solo vende, no compra cash
    total_sell = sum(float(i["intent_notional"]) for i in intents)
    # vende ~50% del bucket (deja 50% en cash); el floor de qty entera resta un poco
    assert 0.45 * mtm <= total_sell <= 0.50 * mtm


def test_exposure_rise_rebuys_from_underinvested():
    block = _lt_policy_block()
    block["allow_cash"] = True
    block["equity_exposure"] = 1.0
    cfg = long_term_engine_config_from_policy_dict(block)
    ar = frozenset({date(2026, 4, 1)})
    day = date(2026, 4, 1)
    mtm = 100_000.0
    prices = _prices_for(cfg)
    wl = frozenset(target_weights(cfg))
    qty = {s: 0.5 * q for s, q in _on_target_qty(cfg, mtm).items()}  # 50% invertido
    intents, _skips, _m = build_long_term_orders_intent(
        cfg, trading_day=day, calendar_sessions=ar, long_bucket_mtm=mtm,
        long_cash=50_000.0, positions_qty=qty, prices=prices, whitelist_long=wl,
    )
    assert intents, "subir exposición debe recomprar (re-risk)"
    assert all(i["side"] == "BUY" for i in intents)


def test_exposure_half_holds_cash_without_churn():
    # Comportamiento clave: ya des-riesgado al 50%, NO debe recomprar → sin churn.
    block = _lt_policy_block()
    block["allow_cash"] = True
    block["equity_exposure"] = 0.5
    cfg = long_term_engine_config_from_policy_dict(block)
    ar = frozenset({date(2026, 4, 1)})
    day = date(2026, 4, 1)
    mtm = 100_000.0
    prices = _prices_for(cfg)
    wl = frozenset(target_weights(cfg))
    qty = {s: 0.5 * q for s, q in _on_target_qty(cfg, mtm).items()}  # 50% invertido = objetivo escalado
    intents, skips, _m = build_long_term_orders_intent(
        cfg, trading_day=day, calendar_sessions=ar, long_bucket_mtm=mtm,
        long_cash=50_000.0, positions_qty=qty, prices=prices, whitelist_long=wl,
    )
    assert intents == []  # mantiene el cash quieto, no recompra → sin churn
    assert any(s.get("reason") == "within_drift_band" for s in skips)


def test_exposure_zero_goes_fully_to_cash():
    block = _lt_policy_block()
    block["allow_cash"] = True
    block["equity_exposure"] = 0.0
    cfg = long_term_engine_config_from_policy_dict(block)
    ar = frozenset({date(2026, 4, 1)})
    day = date(2026, 4, 1)
    mtm = 100_000.0
    prices = _prices_for(cfg)
    wl = frozenset(target_weights(cfg))
    qty = _on_target_qty(cfg, mtm)  # 100% invertido
    intents, _skips, _m = build_long_term_orders_intent(
        cfg, trading_day=day, calendar_sessions=ar, long_bucket_mtm=mtm,
        long_cash=0.0, positions_qty=qty, prices=prices, whitelist_long=wl,
    )
    assert intents and all(i["side"] == "SELL" for i in intents)
    total_sell = sum(float(i["intent_notional"]) for i in intents)
    assert total_sell >= 0.95 * mtm  # vende casi todo → 100% cash


def test_repo_policy_long_term_block_is_consistent():
    cfg = _lt_from_repo()
    validate_long_term_engine_config(cfg)
    tw = target_weights(cfg)
    assert abs(sum(tw.values()) - 1.0) < 1e-9
    assert set(tw) == {"GGAL", "PAMP", "TXAR", "SPY", "QQQ", "KO"}


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
    mtm = 100_000.0
    prices = _prices_for(cfg)
    qty = _on_target_qty(cfg, mtm)  # todas las líneas en su objetivo → drift 0
    wl = frozenset(target_weights(cfg))
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
    mtm = 100_000.0
    prices = _prices_for(cfg)
    wl = frozenset(target_weights(cfg))
    qty = _on_target_qty(cfg, mtm)
    qty["GGAL"] *= 3.0   # GGAL sobreponderado → SELL
    qty["KO"] = 0.0      # KO subponderado → BUY
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
    mtm = 100_000.0
    qty = _on_target_qty(cfg, mtm)
    wl = frozenset(target_weights(cfg))
    prices = _prices_for(cfg)
    del prices["SPY"]  # falta un precio del universo → aborta el ciclo completo
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
    qty = _on_target_qty(cfg, mtm)
    prices = _prices_for(cfg)
    # GGAL con split 2:1 ya aplicado: qty ×2 y precio /2 → mismo market value, peso estable.
    qty["GGAL"] *= 2.0
    prices["GGAL"] = _PX_GGAL / 2.0
    wl = frozenset(target_weights(cfg))
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
