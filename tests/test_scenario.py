"""T2.1 — Behavior tests for parametric scenario runner (Mejora 2).

``apply_config_override`` is implemented in T2.2; ``run_scenario`` in T2.3.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from core_sim.short_term_engine import ShortEngineConfig
from reporting.scenario import (
    ALLOWED_OVERRIDE_KEYS,
    PER_SYMBOL_CAVEAT,
    apply_config_override,
    run_scenario,
)


def _cfg(**overrides) -> ShortEngineConfig:
    base = dict(
        momentum_lookback_days=5,
        liquidity_percentile_min=0.0,
        volatility_20d_max=10.0,
        top_k_per_market=5,
        risk_budget_trade_pct=0.005,
        rsi_lookback=14,
        rsi_overbought_entry=200.0,
        rsi_exit_threshold=45.0,
        allow_leverage=False,
    )
    base.update(overrides)
    return ShortEngineConfig(**base)


def _weekdays(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _bar(close: float, volume: float = 1_000_000.0) -> dict[str, float]:
    return {
        "open": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": volume,
    }


def _build_bars(
    days: list[date],
    series_by_symbol: dict[str, list[float]],
) -> dict[date, dict[str, dict[str, float]]]:
    bars: dict[date, dict[str, dict[str, float]]] = {}
    for i, d in enumerate(days):
        bars[d] = {sym: _bar(series[i]) for sym, series in series_by_symbol.items()}
    return bars


def _wide_market(days: list[date], n_symbols: int = 12) -> dict[date, dict[str, dict[str, float]]]:
    series: dict[str, list[float]] = {}
    for s in range(n_symbols):
        base = 100.0 + 5.0 * s
        series[f"S{s:02d}"] = [
            base * (1.0 + 0.02 * math.sin(0.5 * i + s)) for i in range(len(days))
        ]
    return _build_bars(days, series)


def _scenario_fixture():
    days = _weekdays(date(2025, 1, 1), 60)
    bars = _wide_market(days, n_symbols=12)
    merged_whitelist = {f"S{s:02d}": "US" for s in range(12)}
    base_config = _cfg(momentum_lookback_days=5, liquidity_percentile_min=0.0)
    return days, bars, merged_whitelist, base_config


class TestApplyConfigOverride:
    """Pure override helper: whitelist only, validated coercion."""

    def test_empty_override_returns_unchanged_config(self):
        base = _cfg()
        result = apply_config_override(base, {})
        assert result is base

    def test_applies_known_override_keys(self):
        base = _cfg(momentum_lookback_days=5, top_k_per_market=3)
        result = apply_config_override(
            base,
            {
                "momentum_lookback_days": 20,
                "rsi_overbought_entry": 75.0,
                "rsi_exit_threshold": 40.0,
                "liquidity_percentile_min": 0.5,
                "volatility_20d_max": 0.05,
                "top_k_per_market": 7,
            },
        )
        assert result.momentum_lookback_days == 20
        assert result.rsi_overbought_entry == 75.0
        assert result.rsi_exit_threshold == 40.0
        assert result.liquidity_percentile_min == 0.5
        assert result.volatility_20d_max == 0.05
        assert result.top_k_per_market == 7
        assert base.momentum_lookback_days == 5
        assert base.top_k_per_market == 3

    def test_rejects_unknown_override_keys(self):
        base = _cfg()
        with pytest.raises(ValueError, match="unknown override keys"):
            apply_config_override(base, {"risk_budget_trade_pct": 0.01})

    def test_rejects_mixed_known_and_unknown_keys(self):
        base = _cfg()
        with pytest.raises(ValueError, match="unknown override keys"):
            apply_config_override(
                base,
                {"momentum_lookback_days": 10, "allow_leverage": True},
            )

    def test_allowed_keys_match_plan_knobs(self):
        assert ALLOWED_OVERRIDE_KEYS == {
            "momentum_lookback_days",
            "rsi_overbought_entry",
            "rsi_exit_threshold",
            "liquidity_percentile_min",
            "volatility_20d_max",
            "top_k_per_market",
        }

    def test_rejects_invalid_momentum_lookback(self):
        with pytest.raises(ValueError, match="momentum_lookback_days"):
            apply_config_override(_cfg(), {"momentum_lookback_days": 0})

    def test_rejects_invalid_liquidity_percentile(self):
        with pytest.raises(ValueError, match="liquidity_percentile_min"):
            apply_config_override(_cfg(), {"liquidity_percentile_min": 1.5})


class TestRunScenario:
    """End-to-end scenario comparison (implemented in T2.3)."""

    def test_empty_override_yields_near_zero_delta(self):
        days, bars, merged_whitelist, base_config = _scenario_fixture()
        result = run_scenario(
            base_config=base_config,
            override={},
            bars_by_date=bars,
            merged_whitelist=merged_whitelist,
            trading_days=days,
            horizons=(1,),
            n_min=5,
            baseline_horizon=1,
            baseline_seed=42,
        )
        delta = result["delta"]
        assert abs(delta["engine_ic"]["ic_mean"] or 0.0) < 1e-9
        assert abs(delta["engine_ic"]["ic_ir"] or 0.0) < 1e-9
        assert abs(delta["hit_rate_at_k"]["hit_rate"] or 0.0) < 1e-9
        assert abs(delta["quantile_spread"]["spread_mean"] or 0.0) < 1e-9
        assert result["config_diff"] == {}

    def test_momentum_lookback_override_changes_metrics(self):
        days, bars, merged_whitelist, base_config = _scenario_fixture()
        result = run_scenario(
            base_config=base_config,
            override={"momentum_lookback_days": 20},
            bars_by_date=bars,
            merged_whitelist=merged_whitelist,
            trading_days=days,
            horizons=(1,),
            n_min=5,
            baseline_horizon=1,
            baseline_seed=42,
        )
        assert result["config_diff"] == {"momentum_lookback_days": (5, 20)}
        base_ic = result["base"]["engine_ic"]["ic_mean"]
        scenario_ic = result["scenario"]["engine_ic"]["ic_mean"]
        assert base_ic is not None
        assert scenario_ic is not None
        assert base_ic != scenario_ic

    def test_run_scenario_is_deterministic_for_fixed_seed(self):
        days, bars, merged_whitelist, base_config = _scenario_fixture()
        kwargs = dict(
            base_config=base_config,
            override={"momentum_lookback_days": 10},
            bars_by_date=bars,
            merged_whitelist=merged_whitelist,
            trading_days=days,
            horizons=(1,),
            n_min=5,
            baseline_horizon=1,
            baseline_seed=99,
        )
        first = run_scenario(**kwargs)
        second = run_scenario(**kwargs)
        assert first == second

    def test_includes_data_quality_envelope_on_both_arms(self):
        days, bars, merged_whitelist, base_config = _scenario_fixture()
        result = run_scenario(
            base_config=base_config,
            override={},
            bars_by_date=bars,
            merged_whitelist=merged_whitelist,
            trading_days=days,
            horizons=(1,),
            n_min=5,
            baseline_horizon=1,
        )
        for arm in ("base", "scenario"):
            dq = result[arm]["data_quality"]
            assert "confidence" in dq
            assert dq["n_observations"] > 0

    def test_report_override_n_min_reflected_in_config_diff(self):
        days, bars, merged_whitelist, base_config = _scenario_fixture()
        result = run_scenario(
            base_config=base_config,
            override={},
            report_override={"n_min": 8},
            bars_by_date=bars,
            merged_whitelist=merged_whitelist,
            trading_days=days,
            horizons=(1,),
            n_min=5,
            baseline_horizon=1,
        )
        assert result["config_diff"]["n_min"] == (5, 8)

    def test_single_symbol_filter_uses_per_symbol_view(self):
        days, bars, merged_whitelist, base_config = _scenario_fixture()
        result = run_scenario(
            base_config=base_config,
            override={"momentum_lookback_days": 10},
            bars_by_date=bars,
            merged_whitelist=merged_whitelist,
            trading_days=days,
            horizons=(1,),
            n_min=5,
            baseline_horizon=1,
            symbol_filter="S00",
        )
        assert result["caveat"] == PER_SYMBOL_CAVEAT
        assert result["base"]["view"] == "per_symbol"
        assert result["base"]["engine_ic"]["ic_mean"] is None
        assert result["base"]["per_symbol"]["symbol"] == "S00"
        assert "selection_frequency" in result["delta"]["per_symbol"]

    def test_rejects_unknown_report_override_keys(self):
        days, bars, merged_whitelist, base_config = _scenario_fixture()
        with pytest.raises(ValueError, match="unknown report_override keys"):
            run_scenario(
                base_config=base_config,
                report_override={"foo": 1},
                bars_by_date=bars,
                merged_whitelist=merged_whitelist,
                trading_days=days,
                horizons=(1,),
                n_min=5,
                baseline_horizon=1,
            )
