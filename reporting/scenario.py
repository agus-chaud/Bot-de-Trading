"""Parametric what-if scenarios over the deterministic v1 measurement layer."""

from __future__ import annotations

import statistics
from dataclasses import fields, replace
from datetime import date
from typing import Any, Mapping, Sequence

from core_sim.short_term_engine import ShortEngineConfig
from reporting.data_quality_envelope import (
    build_data_quality_envelope,
    flatten_bars_by_date,
    thresholds_from_policy,
)
from reporting.signal_ic import (
    DEFAULT_N_MIN,
    compute_rank_ic,
    forward_return,
    hit_rate_at_k,
    ic_decay_curve,
    quantile_spread,
    reconstruct_daily_scores,
)

ALLOWED_OVERRIDE_KEYS: frozenset[str] = frozenset(
    {
        "momentum_lookback_days",
        "rsi_overbought_entry",
        "rsi_exit_threshold",
        "liquidity_percentile_min",
        "volatility_20d_max",
        "top_k_per_market",
    }
)

ALLOWED_REPORT_OVERRIDE_KEYS: frozenset[str] = frozenset({"horizons", "n_min"})

PER_SYMBOL_CAVEAT = (
    "Rank-IC is cross-sectional and requires >= n_min names per day; "
    "a single-symbol filter returns per-symbol selection frequency and "
    "mean forward return when selected instead of cross-sectional IC."
)

DEFAULT_CONFIDENCE_THRESHOLDS: dict[str, dict[str, float | int]] = {
    "high": {"min_n_observations": 60, "max_imputed_pct": 2.0},
    "medium": {"min_n_observations": 20, "max_imputed_pct": 5.0},
}

_INT_KEYS = frozenset({"momentum_lookback_days", "top_k_per_market"})
_FLOAT_KEYS = ALLOWED_OVERRIDE_KEYS - _INT_KEYS


def _coerce_override_value(key: str, value: Any) -> int | float:
    if key in _INT_KEYS:
        coerced = int(value)
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise TypeError(f"override[{key!r}] must coerce to int, got {type(value).__name__}")
        return coerced
    coerced = float(value)
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise TypeError(f"override[{key!r}] must coerce to float, got {type(value).__name__}")
    return coerced


def _validate_override_values(values: Mapping[str, int | float]) -> None:
    if "momentum_lookback_days" in values and int(values["momentum_lookback_days"]) < 1:
        raise ValueError("momentum_lookback_days must be >= 1")
    if "top_k_per_market" in values and int(values["top_k_per_market"]) < 1:
        raise ValueError("top_k_per_market must be >= 1")
    if "liquidity_percentile_min" in values:
        pct = float(values["liquidity_percentile_min"])
        if not 0.0 <= pct <= 1.0:
            raise ValueError("liquidity_percentile_min must be in [0, 1]")
    if "volatility_20d_max" in values and float(values["volatility_20d_max"]) <= 0:
        raise ValueError("volatility_20d_max must be > 0")


def apply_config_override(
    base_config: ShortEngineConfig,
    override: Mapping[str, Any],
) -> ShortEngineConfig:
    """Return a new ``ShortEngineConfig`` with whitelisted knobs overridden.

    Only keys in :data:`ALLOWED_OVERRIDE_KEYS` are accepted; unknown keys raise
    ``ValueError``. An empty override returns ``base_config`` unchanged.
    """
    if not override:
        return base_config

    unknown = sorted(set(override) - ALLOWED_OVERRIDE_KEYS)
    if unknown:
        raise ValueError(f"unknown override keys: {unknown}")

    coerced: dict[str, int | float] = {
        key: _coerce_override_value(key, value) for key, value in override.items()
    }
    _validate_override_values(coerced)
    return replace(base_config, **coerced)


def _validate_report_override(report_override: Mapping[str, Any]) -> None:
    unknown = sorted(set(report_override) - ALLOWED_REPORT_OVERRIDE_KEYS)
    if unknown:
        raise ValueError(f"unknown report_override keys: {unknown}")
    if "horizons" in report_override:
        horizons = report_override["horizons"]
        if not isinstance(horizons, (list, tuple)) or not horizons:
            raise ValueError("report_override horizons must be a non-empty sequence")
        if any(int(h) < 1 for h in horizons):
            raise ValueError("report_override horizons must be >= 1")
    if "n_min" in report_override and int(report_override["n_min"]) < 1:
        raise ValueError("report_override n_min must be >= 1")


def _resolve_report_params(
    horizons: tuple[int, ...],
    n_min: int,
    report_override: Mapping[str, Any] | None,
) -> tuple[tuple[int, ...], int]:
    if not report_override:
        return horizons, n_min
    _validate_report_override(report_override)
    out_horizons = (
        tuple(int(h) for h in report_override["horizons"])
        if "horizons" in report_override
        else horizons
    )
    out_n_min = int(report_override["n_min"]) if "n_min" in report_override else n_min
    return out_horizons, out_n_min


def _resolve_symbol_filter(
    symbol_filter: str | Sequence[str] | None,
) -> frozenset[str] | None:
    if symbol_filter is None:
        return None
    if isinstance(symbol_filter, str):
        return frozenset({symbol_filter})
    symbols = frozenset(str(s) for s in symbol_filter)
    if not symbols:
        raise ValueError("symbol_filter must not be empty")
    return symbols


def _filter_universe(
    bars_by_date: dict[date, dict[str, dict[str, Any]]],
    merged_whitelist: dict[str, str],
    symbols: frozenset[str],
) -> tuple[dict[date, dict[str, dict[str, Any]]], dict[str, str]]:
    filtered_bars: dict[date, dict[str, dict[str, Any]]] = {}
    for day, by_symbol in bars_by_date.items():
        day_filtered = {sym: bar for sym, bar in by_symbol.items() if sym in symbols}
        if day_filtered:
            filtered_bars[day] = day_filtered
    filtered_whitelist = {sym: market for sym, market in merged_whitelist.items() if sym in symbols}
    return filtered_bars, filtered_whitelist


def _build_config_diff(
    base_config: ShortEngineConfig,
    scenario_config: ShortEngineConfig,
    *,
    base_horizons: tuple[int, ...],
    scenario_horizons: tuple[int, ...],
    base_n_min: int,
    scenario_n_min: int,
) -> dict[str, tuple[Any, Any]]:
    diff: dict[str, tuple[Any, Any]] = {}
    for field in fields(ShortEngineConfig):
        base_val = getattr(base_config, field.name)
        scenario_val = getattr(scenario_config, field.name)
        if base_val != scenario_val:
            diff[field.name] = (base_val, scenario_val)
    if base_horizons != scenario_horizons:
        diff["horizons"] = (list(base_horizons), list(scenario_horizons))
    if base_n_min != scenario_n_min:
        diff["n_min"] = (base_n_min, scenario_n_min)
    return diff


def _null_ic_block(horizon: int) -> dict[str, Any]:
    return {
        "horizon": horizon,
        "ic_mean": None,
        "ic_std": None,
        "ic_ir": None,
        "n_days_used": 0,
    }


def _null_hit_rate(horizon: int, top_k: int) -> dict[str, Any]:
    return {
        "horizon": horizon,
        "top_k": top_k,
        "hit_rate": None,
        "baseline": 0.5,
        "n_obs": 0,
        "n_days_used": 0,
    }


def _null_spread(horizon: int) -> dict[str, Any]:
    return {
        "horizon": horizon,
        "quantiles": 5,
        "spread_mean": None,
        "n_days_used": 0,
    }


def _per_symbol_metrics(
    *,
    daily_scores: list[Any],
    bars_by_date: dict[date, dict[str, dict[str, Any]]],
    symbol: str,
    horizon: int,
    top_k: int,
    trading_days: list[date],
) -> dict[str, Any]:
    sorted_dates = sorted(trading_days)
    date_index = {d: i for i, d in enumerate(sorted_dates)}
    selection_days = 0
    scored_days = 0
    fwd_when_selected: list[float] = []

    for day in daily_scores:
        if symbol not in day.scores:
            continue
        scored_days += 1
        fwd = forward_return(symbol, day.trading_day, horizon, sorted_dates, date_index, bars_by_date)
        if fwd is None:
            continue
        ranked = sorted(day.scores.items(), key=lambda item: (item[1], item[0]), reverse=True)
        top_symbols = {sym for sym, _ in ranked[:top_k]}
        if symbol in top_symbols:
            selection_days += 1
            fwd_when_selected.append(fwd)

    return {
        "symbol": symbol,
        "horizon": horizon,
        "top_k": top_k,
        "n_days_with_score": scored_days,
        "selection_frequency": (
            selection_days / scored_days if scored_days > 0 else None
        ),
        "mean_forward_return_when_selected": (
            statistics.fmean(fwd_when_selected) if fwd_when_selected else None
        ),
    }


def _compute_arm(
    *,
    config: ShortEngineConfig,
    bars_by_date: dict[date, dict[str, dict[str, Any]]],
    merged_whitelist: dict[str, str],
    trading_days: list[date],
    horizons: tuple[int, ...],
    n_min: int,
    baseline_horizon: int,
    confidence_thresholds: Mapping[str, Mapping[str, float | int]],
    per_symbol: str | None,
) -> dict[str, Any]:
    daily_scores = reconstruct_daily_scores(
        bars_by_date=bars_by_date,
        merged_whitelist=merged_whitelist,
        config=config,
        trading_days=trading_days,
    )
    data_quality = build_data_quality_envelope(
        flatten_bars_by_date(bars_by_date),
        stale_marks=[],
        expected_dates=trading_days,
        thresholds=confidence_thresholds,
    )
    decay = ic_decay_curve(
        daily_scores,
        bars_by_date,
        horizons=horizons,
        n_min=n_min,
        trading_days=trading_days,
    )

    if per_symbol is not None:
        per_symbol_metrics = _per_symbol_metrics(
            daily_scores=daily_scores,
            bars_by_date=bars_by_date,
            symbol=per_symbol,
            horizon=baseline_horizon,
            top_k=config.top_k_per_market,
            trading_days=trading_days,
        )
        return {
            "view": "per_symbol",
            "per_symbol": per_symbol_metrics,
            "engine_ic": _null_ic_block(baseline_horizon),
            "hit_rate_at_k": _null_hit_rate(baseline_horizon, config.top_k_per_market),
            "quantile_spread": _null_spread(baseline_horizon),
            "decay_curve": decay,
            "data_quality": data_quality,
        }

    engine_ic = compute_rank_ic(
        daily_scores,
        bars_by_date,
        baseline_horizon,
        n_min=n_min,
        trading_days=trading_days,
    )
    hit = hit_rate_at_k(
        daily_scores,
        bars_by_date,
        baseline_horizon,
        top_k=config.top_k_per_market,
        n_min=n_min,
        trading_days=trading_days,
    )
    spread = quantile_spread(
        daily_scores,
        bars_by_date,
        baseline_horizon,
        n_min=n_min,
        trading_days=trading_days,
    )
    return {
        "view": "cross_sectional",
        "engine_ic": engine_ic.as_dict(),
        "hit_rate_at_k": hit,
        "quantile_spread": spread,
        "decay_curve": decay,
        "data_quality": data_quality,
    }


def _delta_optional(base_val: float | None, scenario_val: float | None) -> float | None:
    if base_val is None or scenario_val is None:
        return None
    return scenario_val - base_val


def _build_delta(base: Mapping[str, Any], scenario: Mapping[str, Any]) -> dict[str, Any]:
    delta: dict[str, Any] = {
        "engine_ic": {
            "ic_mean": _delta_optional(
                base["engine_ic"]["ic_mean"], scenario["engine_ic"]["ic_mean"]
            ),
            "ic_ir": _delta_optional(
                base["engine_ic"]["ic_ir"], scenario["engine_ic"]["ic_ir"]
            ),
        },
        "hit_rate_at_k": {
            "hit_rate": _delta_optional(
                base["hit_rate_at_k"]["hit_rate"], scenario["hit_rate_at_k"]["hit_rate"]
            ),
        },
        "quantile_spread": {
            "spread_mean": _delta_optional(
                base["quantile_spread"]["spread_mean"],
                scenario["quantile_spread"]["spread_mean"],
            ),
        },
    }
    if base.get("view") == "per_symbol" and scenario.get("view") == "per_symbol":
        base_ps = base["per_symbol"]
        scenario_ps = scenario["per_symbol"]
        delta["per_symbol"] = {
            "selection_frequency": _delta_optional(
                base_ps["selection_frequency"], scenario_ps["selection_frequency"]
            ),
            "mean_forward_return_when_selected": _delta_optional(
                base_ps["mean_forward_return_when_selected"],
                scenario_ps["mean_forward_return_when_selected"],
            ),
        }
    return delta


def run_scenario(
    *,
    base_config: ShortEngineConfig,
    override: Mapping[str, Any] | None = None,
    report_override: Mapping[str, Any] | None = None,
    bars_by_date: dict[date, dict[str, dict[str, Any]]],
    merged_whitelist: dict[str, str],
    trading_days: list[date],
    horizons: tuple[int, ...] = (1, 2, 3, 5, 8, 10),
    n_min: int = DEFAULT_N_MIN,
    baseline_horizon: int | None = None,
    baseline_seed: int = 12345,
    symbol_filter: str | Sequence[str] | None = None,
    confidence_thresholds: Mapping[str, Mapping[str, float | int]] | None = None,
) -> dict[str, Any]:
    """Compare base vs overridden engine config on the same bars.

    Returns ``{base, scenario, delta, config_diff}`` with Rank-IC, hit-rate, and
    quantile-spread at ``baseline_horizon``. When ``symbol_filter`` selects exactly
    one symbol, cross-sectional IC is omitted and a per-symbol view is returned
    instead (see :data:`PER_SYMBOL_CAVEAT`).

    ``baseline_seed`` is accepted for API stability with future null baselines;
    the scenario comparison itself is fully deterministic.
    """
    _ = baseline_seed
    engine_override = dict(override or {})
    scenario_config = apply_config_override(base_config, engine_override)

    base_horizons = tuple(int(h) for h in horizons)
    scenario_horizons, scenario_n_min = _resolve_report_params(base_horizons, n_min, report_override)
    h0 = baseline_horizon if baseline_horizon is not None else (base_horizons[0] if base_horizons else 1)

    thresholds = dict(confidence_thresholds or DEFAULT_CONFIDENCE_THRESHOLDS)

    symbols = _resolve_symbol_filter(symbol_filter)
    work_bars = bars_by_date
    work_whitelist = merged_whitelist
    if symbols is not None:
        work_bars, work_whitelist = _filter_universe(bars_by_date, merged_whitelist, symbols)

    per_symbol = next(iter(symbols)) if symbols is not None and len(symbols) == 1 else None

    base = _compute_arm(
        config=base_config,
        bars_by_date=work_bars,
        merged_whitelist=work_whitelist,
        trading_days=trading_days,
        horizons=base_horizons,
        n_min=n_min,
        baseline_horizon=h0,
        confidence_thresholds=thresholds,
        per_symbol=per_symbol,
    )
    scenario = _compute_arm(
        config=scenario_config,
        bars_by_date=work_bars,
        merged_whitelist=work_whitelist,
        trading_days=trading_days,
        horizons=scenario_horizons,
        n_min=scenario_n_min,
        baseline_horizon=h0,
        confidence_thresholds=thresholds,
        per_symbol=per_symbol,
    )

    result: dict[str, Any] = {
        "base": base,
        "scenario": scenario,
        "delta": _build_delta(base, scenario),
        "config_diff": _build_config_diff(
            base_config,
            scenario_config,
            base_horizons=base_horizons,
            scenario_horizons=scenario_horizons,
            base_n_min=n_min,
            scenario_n_min=scenario_n_min,
        ),
    }
    if per_symbol is not None:
        result["caveat"] = PER_SYMBOL_CAVEAT
    return result


def confidence_thresholds_from_policy(policy_doc: Mapping[str, Any]) -> dict[str, dict[str, float | int]]:
    """Load confidence tiers from policy (wrapper for CLI callers)."""
    return thresholds_from_policy(policy_doc)
