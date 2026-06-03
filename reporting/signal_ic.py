"""Signal-quality layer for the short-term engine (Rank-IC) — Phases 0–3.

This module is a READ-ONLY measurement layer. It does NOT touch the engine nor
the trading pipeline: it reuses ``build_market_snapshot_rows`` +
``compute_signal_candidates`` to reconstruct, for every business day, the
cross-section of predicted ``signal_score`` per symbol, then evaluates whether
that ranking predicts realized forward returns.

Core metric: Rank-IC = Spearman correlation between the predicted rank and the
realized forward return, computed per day and aggregated over time.

Conventions
-----------
- Prediction on day T: ``score_i,T`` = the ``signal_score`` the engine emits
  (momentum = close/close_n_days_ago - 1.0, post hard filters). Universe U1 =
  candidates with a score (i.e. survived the filters). U2 (full snapshot) is left
  as a door but U1 is the default.
- Label horizon h: ``fwd_ret_i,T,h = close_i,(T+h)/close_i,T - 1`` using the same
  ``close`` the engine consumes (the project's adjusted close stored in
  ``ohlcv.close``). If the bar at T+h is MISSING, that observation is DISCARDED —
  no carry-forward for the label.
- Rank-IC per day: ``IC_T,h = Spearman(rank(score), fwd_ret)`` over the day's
  symbols. If a day has fewer than ``n_min`` symbols (default 5) it is skipped.
- Aggregate: ``IC_mean(h) = mean_T IC_T,h``; ``IC_std(h) = std_T``;
  ``IC_IR(h) = IC_mean / IC_std``. The number of usable days is reported.

Determinism: any randomness (random-ranking baseline) uses a fixed seed.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core_sim.short_term_day_runner import (
    build_market_snapshot_rows,
    load_merged_whitelist,
)
from core_sim.short_term_engine import (
    ShortEngineConfig,
    compute_signal_candidates,
)
from data.venue_policy import pick_venue_bar, venues_for_market

if TYPE_CHECKING:
    from data.storage import MarketDB

DEFAULT_HORIZONS: tuple[int, ...] = (1, 2, 3, 5, 8, 10)
DEFAULT_N_MIN: int = 5

# ---------------------------------------------------------------------------
# Config plumbing
# ---------------------------------------------------------------------------


def short_engine_config_from_policy(policy_doc: dict[str, Any]) -> ShortEngineConfig:
    """Build a ``ShortEngineConfig`` from a loaded policy document.

    Mirrors exactly how ``create_short_term_pipeline_handlers`` builds the engine
    config, so reconstructed scores match the production engine.
    """
    ste = policy_doc["short_term_engine"]
    return ShortEngineConfig(
        momentum_lookback_days=int(ste["momentum_lookback_days"]),
        liquidity_percentile_min=float(ste["liquidity_percentile_min"]),
        volatility_20d_max=float(ste["volatility_20d_max"]),
        top_k_per_market=int(ste["top_k_per_market"]),
        risk_budget_trade_pct=float(ste["risk_budget_trade_pct"]),
        rsi_lookback=int(ste.get("rsi_lookback", 14)),
        rsi_overbought_entry=float(ste.get("rsi_overbought_entry", 80.0)),
        rsi_exit_threshold=float(ste.get("rsi_exit_threshold", 45.0)),
        allow_leverage=bool(ste.get("allow_leverage", False)),
    )


# ---------------------------------------------------------------------------
# Phase 0 — Loader / reconstruction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DayScores:
    """Per-day cross-section of predicted scores plus the bar closes (for labels)."""

    trading_day: date
    scores: dict[str, float]           # symbol -> signal_score (U1 candidates)
    closes: dict[str, float]           # symbol -> close on this day (for fwd labels)
    market_by_symbol: dict[str, str]   # symbol -> market


def _market_open_all_sessions() -> dict[str, Any]:
    """Treat every day in the bar history as a valid session for both venues.

    This matches the engine's default behavior when no calendar store is wired
    (``event_engine`` defaults ``is_us_session`` / ``is_ar_business_day`` to True)
    and the pre-gate convention of iterating only over days that actually have
    bars. Days without a bar for a symbol are naturally absent from ``daily_bars``.
    """
    return {"is_us_session": True, "is_ar_business_day": True}


def build_history_before_day(
    symbol: str,
    trading_day: date,
    sorted_dates: list[date],
    bars_by_date: dict[date, dict[str, dict[str, float]]],
    max_points: int,
) -> list[dict[str, float]]:
    """History strictly before ``trading_day`` (last ``max_points`` days with a bar).

    Identical contract to ``short_term_pre_gate.build_history_before_day`` so the
    reconstructed snapshot rows match what the engine consumes day to day.
    """
    out: list[dict[str, float]] = []
    for d in sorted_dates:
        if d >= trading_day:
            break
        day_bar = bars_by_date.get(d, {}).get(symbol)
        if day_bar is None or "close" not in day_bar:
            continue
        vol = float(day_bar.get("volume", 0.0))
        out.append({"close": float(day_bar["close"]), "volume": vol})
    return out[-max_points:]


def bars_by_date_from_db(
    db: "MarketDB",
    start: date,
    end: date,
    merged_whitelist: dict[str, str],
) -> dict[date, dict[str, dict[str, float]]]:
    """Load OHLCV in [start, end] indexed as ``date -> symbol -> bar``.

    Uses the stored ``close`` (the project's adjusted close) for every symbol.

    Venue policy (see :mod:`data.venue_policy`): each symbol is read ONLY from the
    venue that matches its ``merged_whitelist`` market tag — US-tagged symbols from
    XNYS/US (USD), AR-tagged symbols from XBUE (ARS). This prevents the last-write-
    wins bug that blended USD and ARS bars for dual-listed names. When both XNYS and
    legacy US exist on the same day, XNYS wins deterministically. A symbol with no
    bar at its allowed venue on a given day is omitted that day — never substituted.

    Symbols absent from ``merged_whitelist`` are skipped entirely (no tag = no way to
    know the correct venue, and they are not part of the traded universe anyway).
    """
    cursor = db._conn.execute(
        """
        SELECT symbol, ts, open, high, low, close, volume, venue
        FROM ohlcv
        WHERE ts BETWEEN ? AND ?
        ORDER BY ts ASC
        """,
        (start.isoformat(), end.isoformat()),
    )
    # Stage all venues per (day, symbol), then collapse to the policy-correct bar.
    staged: dict[date, dict[str, dict[str, dict[str, float]]]] = {}
    for row in cursor.fetchall():
        symbol = row["symbol"]
        market = merged_whitelist.get(symbol)
        if market is None:
            continue
        if row["venue"] not in venues_for_market(market):
            continue
        day = date.fromisoformat(row["ts"])
        staged.setdefault(day, {}).setdefault(symbol, {})[row["venue"]] = {
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
        }

    bars_by_date: dict[date, dict[str, dict[str, float]]] = {}
    for day, by_symbol in staged.items():
        for symbol, bars_by_venue in by_symbol.items():
            bar = pick_venue_bar(merged_whitelist[symbol], bars_by_venue)
            if bar is not None:
                bars_by_date.setdefault(day, {})[symbol] = bar
    return bars_by_date


def reconstruct_daily_scores(
    *,
    bars_by_date: dict[date, dict[str, dict[str, float]]],
    merged_whitelist: dict[str, str],
    config: ShortEngineConfig,
    history_cap: int | None = None,
    trading_days: list[date] | None = None,
) -> list[DayScores]:
    """Reconstruct, per business day, the cross-section of engine scores (U1).

    For each day this reuses ``build_market_snapshot_rows`` +
    ``compute_signal_candidates`` exactly as the engine does, so the resulting
    ``signal_score`` per symbol reproduces what the engine emits for identical
    inputs (Phase 0 acceptance criterion).
    """
    sorted_dates = sorted(trading_days or list(bars_by_date.keys()))
    cap = history_cap if history_cap is not None else max(config.momentum_lookback_days + 30, 60)
    market_open = _market_open_all_sessions()

    out: list[DayScores] = []
    for d in sorted_dates:
        daily = bars_by_date.get(d)
        if not daily:
            continue
        history_by_symbol: dict[str, list[dict[str, float]]] = {}
        for sym in daily:
            history_by_symbol[sym] = build_history_before_day(
                sym, d, sorted_dates, bars_by_date, cap
            )

        rows, _skipped = build_market_snapshot_rows(
            trading_day=d,
            daily_bars=daily,
            history_by_symbol=history_by_symbol,
            merged_whitelist=merged_whitelist,
            market_open=market_open,
            ste_cfg=config,
        )
        candidates, _skipped_signal = compute_signal_candidates(rows, config)

        scores = {str(c["symbol"]): float(c["signal_score"]) for c in candidates}
        market_by_symbol = {str(c["symbol"]): str(c["market"]) for c in candidates}
        closes = {sym: float(daily[sym]["close"]) for sym in scores if sym in daily}
        out.append(
            DayScores(
                trading_day=d,
                scores=scores,
                closes=closes,
                market_by_symbol=market_by_symbol,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Forward returns + Spearman
# ---------------------------------------------------------------------------


def forward_return(
    symbol: str,
    trading_day: date,
    horizon: int,
    sorted_dates: list[date],
    date_index: dict[date, int],
    bars_by_date: dict[date, dict[str, dict[str, float]]],
) -> float | None:
    """``close(T+h)/close(T) - 1`` or None if the T or T+h bar is missing.

    T+h is the h-th trading day after T in ``sorted_dates`` (positional, business
    days). No carry-forward: a missing bar at either end discards the observation.
    """
    i = date_index.get(trading_day)
    if i is None or i + horizon >= len(sorted_dates):
        return None
    bar_t = bars_by_date.get(trading_day, {}).get(symbol)
    target_day = sorted_dates[i + horizon]
    bar_th = bars_by_date.get(target_day, {}).get(symbol)
    if bar_t is None or bar_th is None:
        return None
    c0 = float(bar_t.get("close", 0.0))
    c1 = float(bar_th.get("close", 0.0))
    if c0 <= 0 or c1 <= 0:
        return None
    return (c1 / c0) - 1.0


def _rankdata(values: list[float]) -> list[float]:
    """Average-rank of ``values`` (ties share the mean of their positions)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    n = len(values)
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-based average rank
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman_corr(x: list[float], y: list[float]) -> float | None:
    """Spearman rank correlation in [-1, 1], or None when undefined.

    Undefined when fewer than 2 points or when either side has zero rank variance
    (e.g. all scores equal) — Spearman is not meaningful there.
    """
    if len(x) != len(y) or len(x) < 2:
        return None
    rx = _rankdata(x)
    ry = _rankdata(y)
    mx = statistics.fmean(rx)
    my = statistics.fmean(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx ** 0.5 * vy ** 0.5)


# ---------------------------------------------------------------------------
# Phase 1 — Rank-IC at a single horizon
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HorizonIC:
    """Aggregated Rank-IC at a single horizon."""

    horizon: int
    ic_mean: float | None
    ic_std: float | None
    ic_ir: float | None
    n_days_used: int
    daily_ic: list[tuple[date, float]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "horizon": self.horizon,
            "ic_mean": self.ic_mean,
            "ic_std": self.ic_std,
            "ic_ir": self.ic_ir,
            "n_days_used": self.n_days_used,
        }


def _ranker_scores(day: DayScores) -> dict[str, float]:
    return day.scores


def compute_rank_ic(
    daily_scores: list[DayScores],
    bars_by_date: dict[date, dict[str, dict[str, float]]],
    horizon: int,
    *,
    n_min: int = DEFAULT_N_MIN,
    score_override: dict[date, dict[str, float]] | None = None,
    trading_days: list[date] | None = None,
) -> HorizonIC:
    """Rank-IC at one ``horizon`` aggregated over days.

    ``score_override`` lets a baseline (e.g. random ranking) substitute the per-day
    scores while keeping the exact same universe and forward returns.
    """
    sorted_dates = sorted(trading_days or list(bars_by_date.keys()))
    date_index = {d: i for i, d in enumerate(sorted_dates)}

    daily_ic: list[tuple[date, float]] = []
    for day in daily_scores:
        scores = (
            score_override.get(day.trading_day, {})
            if score_override is not None
            else day.scores
        )
        symbols: list[str] = []
        score_vec: list[float] = []
        fwd_vec: list[float] = []
        for sym, sc in scores.items():
            fwd = forward_return(
                sym, day.trading_day, horizon, sorted_dates, date_index, bars_by_date
            )
            if fwd is None:
                continue
            symbols.append(sym)
            score_vec.append(float(sc))
            fwd_vec.append(fwd)
        if len(symbols) < n_min:
            continue
        ic = spearman_corr(score_vec, fwd_vec)
        if ic is None:
            continue
        daily_ic.append((day.trading_day, ic))

    ics = [v for _, v in daily_ic]
    n = len(ics)
    if n == 0:
        return HorizonIC(horizon, None, None, None, 0, [])
    ic_mean = statistics.fmean(ics)
    ic_std = statistics.pstdev(ics) if n > 1 else 0.0
    ic_ir = (ic_mean / ic_std) if ic_std > 0 else None
    return HorizonIC(horizon, ic_mean, ic_std, ic_ir, n, daily_ic)


# ---------------------------------------------------------------------------
# Phase 2 — Horizon grid / decay curve
# ---------------------------------------------------------------------------


def ic_decay_curve(
    daily_scores: list[DayScores],
    bars_by_date: dict[date, dict[str, dict[str, float]]],
    *,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    n_min: int = DEFAULT_N_MIN,
    trading_days: list[date] | None = None,
) -> dict[str, Any]:
    """Compute IC_mean(h) for each horizon, returning a JSON-friendly dict."""
    curve: list[dict[str, Any]] = []
    for h in horizons:
        res = compute_rank_ic(
            daily_scores, bars_by_date, h, n_min=n_min, trading_days=trading_days
        )
        curve.append(res.as_dict())
    return {
        "horizons": list(horizons),
        "n_min": n_min,
        "curve": curve,
    }


# ---------------------------------------------------------------------------
# Phase 3 — Baselines, hit-rate@K, quantile spread
# ---------------------------------------------------------------------------


def random_ranking_baseline(
    daily_scores: list[DayScores],
    bars_by_date: dict[date, dict[str, dict[str, float]]],
    horizon: int,
    *,
    n_min: int = DEFAULT_N_MIN,
    repetitions: int = 20,
    seed: int = 12345,
    trading_days: list[date] | None = None,
) -> dict[str, Any]:
    """Null distribution of IC_mean(h) under random per-day rankings.

    For each repetition, every day's symbols get a freshly shuffled score (same
    universe, same forward returns). Returns the mean and std of the per-rep
    IC_mean values — expected ~0 within a band. Deterministic via fixed seed.
    """
    rng = random.Random(seed)
    rep_means: list[float] = []
    for _ in range(repetitions):
        override: dict[date, dict[str, float]] = {}
        for day in daily_scores:
            syms = list(day.scores.keys())
            shuffled = list(range(len(syms)))
            rng.shuffle(shuffled)
            override[day.trading_day] = {s: float(r) for s, r in zip(syms, shuffled)}
        res = compute_rank_ic(
            daily_scores,
            bars_by_date,
            horizon,
            n_min=n_min,
            score_override=override,
            trading_days=trading_days,
        )
        if res.ic_mean is not None:
            rep_means.append(res.ic_mean)
    if not rep_means:
        return {"horizon": horizon, "null_ic_mean": None, "null_ic_std": None,
                "repetitions": repetitions, "seed": seed, "reps_used": 0}
    null_mean = statistics.fmean(rep_means)
    null_std = statistics.pstdev(rep_means) if len(rep_means) > 1 else 0.0
    return {
        "horizon": horizon,
        "null_ic_mean": null_mean,
        "null_ic_std": null_std,
        "repetitions": repetitions,
        "reps_used": len(rep_means),
        "seed": seed,
    }


def hit_rate_at_k(
    daily_scores: list[DayScores],
    bars_by_date: dict[date, dict[str, dict[str, float]]],
    horizon: int,
    *,
    top_k: int,
    n_min: int = DEFAULT_N_MIN,
    trading_days: list[date] | None = None,
) -> dict[str, Any]:
    """Fraction of top-K names whose fwd_ret beats the day's cross-sectional median.

    Baseline is 0.5 (a random name beats the median half the time). The median is
    computed over the day's evaluable symbols (those with a valid forward return).
    """
    sorted_dates = sorted(trading_days or list(bars_by_date.keys()))
    date_index = {d: i for i, d in enumerate(sorted_dates)}

    hits = 0
    total = 0
    days_used = 0
    for day in daily_scores:
        evaluable: list[tuple[str, float, float]] = []  # (sym, score, fwd)
        for sym, sc in day.scores.items():
            fwd = forward_return(
                sym, day.trading_day, horizon, sorted_dates, date_index, bars_by_date
            )
            if fwd is None:
                continue
            evaluable.append((sym, float(sc), fwd))
        if len(evaluable) < n_min:
            continue
        median = statistics.median(f for _, _, f in evaluable)
        top = sorted(evaluable, key=lambda t: (t[1], t[0]), reverse=True)[:top_k]
        for _, _, fwd in top:
            total += 1
            if fwd > median:
                hits += 1
        days_used += 1

    rate = (hits / total) if total > 0 else None
    return {
        "horizon": horizon,
        "top_k": top_k,
        "hit_rate": rate,
        "baseline": 0.5,
        "n_obs": total,
        "n_days_used": days_used,
    }


def quantile_spread(
    daily_scores: list[DayScores],
    bars_by_date: dict[date, dict[str, dict[str, float]]],
    horizon: int,
    *,
    quantiles: int = 5,
    n_min: int = DEFAULT_N_MIN,
    trading_days: list[date] | None = None,
) -> dict[str, Any]:
    """Mean fwd_ret of the top quantile minus the bottom quantile, averaged over days.

    Symbols are bucketed by score into ``quantiles`` groups; the spread is
    top-group mean fwd_ret minus bottom-group mean fwd_ret. A positive spread
    means high-scored names outperform low-scored names.
    """
    sorted_dates = sorted(trading_days or list(bars_by_date.keys()))
    date_index = {d: i for i, d in enumerate(sorted_dates)}

    spreads: list[float] = []
    for day in daily_scores:
        evaluable: list[tuple[float, float]] = []  # (score, fwd)
        for sym, sc in day.scores.items():
            fwd = forward_return(
                sym, day.trading_day, horizon, sorted_dates, date_index, bars_by_date
            )
            if fwd is None:
                continue
            evaluable.append((float(sc), fwd))
        if len(evaluable) < max(n_min, quantiles):
            continue
        ordered = sorted(evaluable, key=lambda t: t[0])
        n = len(ordered)
        bucket = max(1, n // quantiles)
        bottom = ordered[:bucket]
        top = ordered[-bucket:]
        spread = statistics.fmean(f for _, f in top) - statistics.fmean(f for _, f in bottom)
        spreads.append(spread)

    if not spreads:
        return {"horizon": horizon, "quantiles": quantiles, "spread_mean": None,
                "n_days_used": 0}
    return {
        "horizon": horizon,
        "quantiles": quantiles,
        "spread_mean": statistics.fmean(spreads),
        "spread_std": statistics.pstdev(spreads) if len(spreads) > 1 else 0.0,
        "n_days_used": len(spreads),
    }


# ---------------------------------------------------------------------------
# Convenience: end-to-end report from a DB
# ---------------------------------------------------------------------------


def run_signal_ic_report(
    *,
    db: "MarketDB",
    policy_doc: dict[str, Any],
    repo_root: Path,
    start: date,
    end: date,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    n_min: int = DEFAULT_N_MIN,
    baseline_horizon: int | None = None,
    baseline_repetitions: int = 20,
    baseline_seed: int = 12345,
) -> dict[str, Any]:
    """End-to-end signal-quality report over a DB date range (Phases 0–3).

    Degrades gracefully: if there are not enough days for a horizon, that
    horizon's IC simply reports ``n_days_used`` and ``ic_mean=None`` instead of
    crashing.
    """
    config = short_engine_config_from_policy(policy_doc)
    merged_whitelist = load_merged_whitelist(repo_root, policy_doc)
    bars_by_date = bars_by_date_from_db(db, start, end, merged_whitelist)
    trading_days = sorted(bars_by_date.keys())

    daily_scores = reconstruct_daily_scores(
        bars_by_date=bars_by_date,
        merged_whitelist=merged_whitelist,
        config=config,
        trading_days=trading_days,
    )

    decay = ic_decay_curve(
        daily_scores, bars_by_date, horizons=horizons, n_min=n_min,
        trading_days=trading_days,
    )

    h0 = baseline_horizon if baseline_horizon is not None else (horizons[0] if horizons else 1)
    engine_ic = compute_rank_ic(
        daily_scores, bars_by_date, h0, n_min=n_min, trading_days=trading_days
    )
    random_null = random_ranking_baseline(
        daily_scores, bars_by_date, h0, n_min=n_min,
        repetitions=baseline_repetitions, seed=baseline_seed,
        trading_days=trading_days,
    )
    hit = hit_rate_at_k(
        daily_scores, bars_by_date, h0, top_k=config.top_k_per_market,
        n_min=n_min, trading_days=trading_days,
    )
    spread = quantile_spread(
        daily_scores, bars_by_date, h0, n_min=n_min, trading_days=trading_days
    )

    return {
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "trading_days": len(trading_days),
        "days_with_scores": len(daily_scores),
        "whitelist_size": len(merged_whitelist),
        "decay_curve": decay,
        "baseline_horizon": h0,
        "engine_ic": engine_ic.as_dict(),
        "random_null": random_null,
        "hit_rate_at_k": hit,
        "quantile_spread": spread,
    }
