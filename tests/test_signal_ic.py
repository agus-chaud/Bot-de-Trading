"""Behavior tests for the signal-quality (Rank-IC) measurement layer.

These tests check WHAT the layer measures, not how:
- Phase 0: reconstructed scores reproduce exactly what the engine emits.
- Phase 1: with a known injected correlation, IC recovers sign and magnitude;
  with no relation it lands near zero.
- Phase 2: the decay curve produces a horizon -> IC dict over real-shaped data.
- Phase 3: a random ranking yields IC ~ 0 within a band; hit-rate and quantile
  spread behave as designed on a synthetic predictive fixture.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from core_sim.short_term_day_runner import build_market_snapshot_rows
from core_sim.short_term_engine import ShortEngineConfig, compute_signal_candidates
from reporting.signal_ic import (
    DayScores,
    bars_by_date_from_db,
    build_history_before_day,
    compute_rank_ic,
    forward_return,
    hit_rate_at_k,
    ic_decay_curve,
    quantile_spread,
    random_ranking_baseline,
    reconstruct_daily_scores,
    spearman_corr,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _cfg(**overrides) -> ShortEngineConfig:
    base = dict(
        momentum_lookback_days=5,
        liquidity_percentile_min=0.0,   # disable liquidity filter for synthetic data
        volatility_20d_max=10.0,        # effectively disable vol filter
        top_k_per_market=5,
        risk_budget_trade_pct=0.005,
        rsi_lookback=14,
        rsi_overbought_entry=200.0,     # never overbought
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
    market_by_symbol: dict[str, str],
) -> dict[date, dict[str, dict[str, float]]]:
    """Build bars_by_date from per-symbol close series (aligned to `days`)."""
    bars: dict[date, dict[str, dict[str, float]]] = {}
    for i, d in enumerate(days):
        day_bars: dict[str, dict[str, float]] = {}
        for sym, series in series_by_symbol.items():
            day_bars[sym] = _bar(series[i])
        bars[d] = day_bars
    return bars


# ---------------------------------------------------------------------------
# Phase 0 — reconstruction matches the engine exactly
# ---------------------------------------------------------------------------


def test_reconstructed_scores_reproduce_engine_output_exactly():
    """For a known day, reconstructed scores equal a direct engine call with the
    same inputs (Phase 0 acceptance criterion)."""
    # Arrange: 30 weekdays, two symbols with distinct ascending momentum.
    days = _weekdays(date(2025, 1, 1), 30)
    cfg = _cfg(momentum_lookback_days=5)
    series = {
        "AAA": [100.0 + i for i in range(30)],          # steady climber
        "BBB": [50.0 + 0.5 * i for i in range(30)],     # slower climber
    }
    markets = {"AAA": "US", "BBB": "US"}
    bars = _build_bars(days, series, markets)
    merged_whitelist = {"AAA": "US", "BBB": "US"}
    target_day = days[20]

    # Act: reconstruct via the layer.
    daily_scores = reconstruct_daily_scores(
        bars_by_date=bars,
        merged_whitelist=merged_whitelist,
        config=cfg,
        trading_days=days,
    )
    recon = next(ds for ds in daily_scores if ds.trading_day == target_day)

    # Build the engine's expected scores directly with the same inputs.
    sorted_dates = sorted(days)
    history = {
        sym: build_history_before_day(sym, target_day, sorted_dates, bars, 60)
        for sym in bars[target_day]
    }
    rows, _ = build_market_snapshot_rows(
        trading_day=target_day,
        daily_bars=bars[target_day],
        history_by_symbol=history,
        merged_whitelist=merged_whitelist,
        market_open={"is_us_session": True, "is_ar_business_day": True},
        ste_cfg=cfg,
    )
    candidates, _ = compute_signal_candidates(rows, cfg)
    expected = {str(c["symbol"]): float(c["signal_score"]) for c in candidates}

    # Assert: identical symbols and identical score values.
    assert recon.scores == expected
    assert set(recon.scores) == {"AAA", "BBB"}


def test_reconstruction_only_keeps_positive_momentum_survivors():
    """A symbol with negative momentum is dropped (engine keeps momentum>0 only)."""
    days = _weekdays(date(2025, 1, 1), 30)
    cfg = _cfg(momentum_lookback_days=5)
    series = {
        "UP": [100.0 + i for i in range(30)],
        "DOWN": [100.0 - i for i in range(30)],   # falling -> negative momentum
    }
    bars = _build_bars(days, series, {"UP": "US", "DOWN": "US"})
    daily_scores = reconstruct_daily_scores(
        bars_by_date=bars,
        merged_whitelist={"UP": "US", "DOWN": "US"},
        config=cfg,
        trading_days=days,
    )
    day = daily_scores[20]
    assert "UP" in day.scores
    assert "DOWN" not in day.scores


# ---------------------------------------------------------------------------
# Spearman primitive
# ---------------------------------------------------------------------------


def test_spearman_is_one_for_perfectly_monotonic_relation():
    assert spearman_corr([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)


def test_spearman_is_minus_one_for_perfectly_inverse_relation():
    assert spearman_corr([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_spearman_is_none_when_one_side_has_no_variance():
    assert spearman_corr([1, 2, 3], [5, 5, 5]) is None


# ---------------------------------------------------------------------------
# Forward returns
# ---------------------------------------------------------------------------


def test_forward_return_uses_close_at_horizon():
    days = _weekdays(date(2025, 1, 1), 10)
    bars = _build_bars(days, {"X": [100.0 * (1.0 + 0.1 * i) for i in range(10)]}, {"X": "US"})
    sorted_dates = sorted(days)
    idx = {d: i for i, d in enumerate(sorted_dates)}
    # close(T)=100, close(T+1)=110 -> +0.10
    fwd = forward_return("X", days[0], 1, sorted_dates, idx, bars)
    assert fwd == pytest.approx(0.10)


def test_forward_return_is_none_when_target_bar_missing():
    days = _weekdays(date(2025, 1, 1), 5)
    bars = _build_bars(days, {"X": [100.0, 101.0, 102.0, 103.0, 104.0]}, {"X": "US"})
    # Drop the bar 2 days ahead so the label cannot be formed.
    del bars[days[3]]["X"]
    sorted_dates = sorted(days)
    idx = {d: i for i, d in enumerate(sorted_dates)}
    assert forward_return("X", days[1], 2, sorted_dates, idx, bars) is None


def test_forward_return_is_none_past_end_of_history():
    days = _weekdays(date(2025, 1, 1), 3)
    bars = _build_bars(days, {"X": [100.0, 101.0, 102.0]}, {"X": "US"})
    sorted_dates = sorted(days)
    idx = {d: i for i, d in enumerate(sorted_dates)}
    assert forward_return("X", days[2], 1, sorted_dates, idx, bars) is None


# ---------------------------------------------------------------------------
# Phase 1 — IC recovers injected correlation
# ---------------------------------------------------------------------------


def _synthetic_scores_with_known_relation(
    days: list[date],
    bars: dict[date, dict[str, dict[str, float]]],
    *,
    score_to_fwd: str,
) -> list[DayScores]:
    """Build DayScores where each day's scores are aligned (or anti-aligned, or
    unrelated) to next-day forward returns, by reading the actual bars.

    score_to_fwd: 'positive' -> score == next-day return; 'negative' -> inverse;
    'noise' -> a fixed unrelated permutation.
    """
    sorted_dates = sorted(days)
    idx = {d: i for i, d in enumerate(sorted_dates)}
    out: list[DayScores] = []
    for d in days:
        symbols = list(bars[d].keys())
        scores: dict[str, float] = {}
        closes: dict[str, float] = {}
        for rank_pos, sym in enumerate(symbols):
            fwd = forward_return(sym, d, 1, sorted_dates, idx, bars)
            closes[sym] = bars[d][sym]["close"]
            if fwd is None:
                scores[sym] = 0.0
                continue
            if score_to_fwd == "positive":
                scores[sym] = fwd
            elif score_to_fwd == "negative":
                scores[sym] = -fwd
            else:  # noise: stable, unrelated to fwd
                scores[sym] = float((rank_pos * 7) % 11)
        out.append(
            DayScores(
                trading_day=d,
                scores=scores,
                closes=closes,
                market_by_symbol={s: "US" for s in symbols},
            )
        )
    return out


def _wide_market(days: list[date], n_symbols: int = 12) -> dict[date, dict[str, dict[str, float]]]:
    """A cross-section with enough symbols per day and varied returns."""
    series: dict[str, list[float]] = {}
    for s in range(n_symbols):
        base = 100.0 + 5.0 * s
        # each symbol has its own day-to-day wiggle so returns vary cross-sectionally
        series[f"S{s:02d}"] = [
            base * (1.0 + 0.02 * math.sin(0.5 * i + s)) for i in range(len(days))
        ]
    markets = {sym: "US" for sym in series}
    return _build_bars(days, series, markets)


def test_ic_recovers_positive_relation_when_scores_predict_returns():
    """When scores equal next-day returns, IC_mean is strongly positive."""
    days = _weekdays(date(2025, 1, 1), 40)
    bars = _wide_market(days, n_symbols=12)
    daily_scores = _synthetic_scores_with_known_relation(days, bars, score_to_fwd="positive")

    res = compute_rank_ic(daily_scores, bars, horizon=1, n_min=5, trading_days=days)

    assert res.n_days_used > 10
    assert res.ic_mean is not None and res.ic_mean > 0.9


def test_ic_recovers_negative_relation_when_scores_invert_returns():
    days = _weekdays(date(2025, 1, 1), 40)
    bars = _wide_market(days, n_symbols=12)
    daily_scores = _synthetic_scores_with_known_relation(days, bars, score_to_fwd="negative")

    res = compute_rank_ic(daily_scores, bars, horizon=1, n_min=5, trading_days=days)

    assert res.ic_mean is not None and res.ic_mean < -0.9


def test_ic_is_near_zero_when_scores_are_unrelated_to_returns():
    days = _weekdays(date(2025, 1, 1), 60)
    bars = _wide_market(days, n_symbols=12)
    daily_scores = _synthetic_scores_with_known_relation(days, bars, score_to_fwd="noise")

    res = compute_rank_ic(daily_scores, bars, horizon=1, n_min=5, trading_days=days)

    assert res.ic_mean is not None
    assert abs(res.ic_mean) < 0.25


def test_days_below_n_min_are_skipped():
    """A day whose cross-section is smaller than n_min contributes no IC."""
    days = _weekdays(date(2025, 1, 1), 20)
    bars = _wide_market(days, n_symbols=12)
    daily_scores = _synthetic_scores_with_known_relation(days, bars, score_to_fwd="positive")

    strict = compute_rank_ic(daily_scores, bars, horizon=1, n_min=50, trading_days=days)
    assert strict.n_days_used == 0
    assert strict.ic_mean is None


# ---------------------------------------------------------------------------
# Phase 2 — decay curve shape
# ---------------------------------------------------------------------------


def test_decay_curve_returns_one_entry_per_horizon():
    days = _weekdays(date(2025, 1, 1), 60)
    bars = _wide_market(days, n_symbols=12)
    daily_scores = _synthetic_scores_with_known_relation(days, bars, score_to_fwd="positive")

    out = ic_decay_curve(daily_scores, bars, horizons=(1, 2, 3, 5), n_min=5, trading_days=days)

    assert out["horizons"] == [1, 2, 3, 5]
    assert [c["horizon"] for c in out["curve"]] == [1, 2, 3, 5]
    # h=1 was constructed to be perfectly predictive -> strongest IC at h=1.
    h1 = next(c for c in out["curve"] if c["horizon"] == 1)
    assert h1["ic_mean"] is not None and h1["ic_mean"] > 0.9


def test_decay_curve_degrades_gracefully_when_horizon_exceeds_history():
    """An over-long horizon yields no usable days instead of crashing."""
    days = _weekdays(date(2025, 1, 1), 8)
    bars = _wide_market(days, n_symbols=12)
    daily_scores = _synthetic_scores_with_known_relation(days, bars, score_to_fwd="positive")

    out = ic_decay_curve(daily_scores, bars, horizons=(1, 50), n_min=5, trading_days=days)
    far = next(c for c in out["curve"] if c["horizon"] == 50)
    assert far["n_days_used"] == 0
    assert far["ic_mean"] is None


# ---------------------------------------------------------------------------
# Phase 3 — baselines, hit-rate, quantile spread
# ---------------------------------------------------------------------------


def test_random_ranking_baseline_is_near_zero_within_band():
    """Shuffled rankings have no edge: null IC mean ~ 0 within a tight band."""
    days = _weekdays(date(2025, 1, 1), 80)
    bars = _wide_market(days, n_symbols=12)
    # Use a genuinely predictive engine fixture; the baseline must still be ~0.
    daily_scores = _synthetic_scores_with_known_relation(days, bars, score_to_fwd="positive")

    null = random_ranking_baseline(
        daily_scores, bars, horizon=1, n_min=5, repetitions=30, seed=42, trading_days=days
    )

    assert null["reps_used"] > 0
    assert null["null_ic_mean"] is not None
    assert abs(null["null_ic_mean"]) < 0.10


def test_random_ranking_baseline_is_deterministic_for_fixed_seed():
    days = _weekdays(date(2025, 1, 1), 40)
    bars = _wide_market(days, n_symbols=10)
    daily_scores = _synthetic_scores_with_known_relation(days, bars, score_to_fwd="positive")

    a = random_ranking_baseline(daily_scores, bars, 1, n_min=5, repetitions=10, seed=7, trading_days=days)
    b = random_ranking_baseline(daily_scores, bars, 1, n_min=5, repetitions=10, seed=7, trading_days=days)
    assert a["null_ic_mean"] == b["null_ic_mean"]


def test_hit_rate_beats_baseline_when_top_k_truly_predicts():
    """With perfectly predictive scores, top-K names beat the daily median far
    more than the 0.5 baseline."""
    days = _weekdays(date(2025, 1, 1), 60)
    bars = _wide_market(days, n_symbols=12)
    daily_scores = _synthetic_scores_with_known_relation(days, bars, score_to_fwd="positive")

    res = hit_rate_at_k(daily_scores, bars, horizon=1, top_k=3, n_min=5, trading_days=days)

    assert res["n_obs"] > 0
    assert res["hit_rate"] is not None and res["hit_rate"] > 0.5


def test_quantile_spread_is_positive_when_high_scores_outperform():
    days = _weekdays(date(2025, 1, 1), 60)
    bars = _wide_market(days, n_symbols=15)
    daily_scores = _synthetic_scores_with_known_relation(days, bars, score_to_fwd="positive")

    res = quantile_spread(daily_scores, bars, horizon=1, quantiles=5, n_min=5, trading_days=days)

    assert res["n_days_used"] > 0
    assert res["spread_mean"] is not None and res["spread_mean"] > 0.0


def test_quantile_spread_is_negative_when_high_scores_underperform():
    days = _weekdays(date(2025, 1, 1), 60)
    bars = _wide_market(days, n_symbols=15)
    daily_scores = _synthetic_scores_with_known_relation(days, bars, score_to_fwd="negative")

    res = quantile_spread(daily_scores, bars, horizon=1, quantiles=5, n_min=5, trading_days=days)

    assert res["spread_mean"] is not None and res["spread_mean"] < 0.0
