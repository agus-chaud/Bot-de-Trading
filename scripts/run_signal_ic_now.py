"""Ad-hoc runner for the signal-IC report over market.db (expanded universe).

READ-ONLY. Does not modify the engine nor the measurement methodology. It calls
the public functions of reporting.signal_ic exactly as run_signal_ic_report does,
plus a few extra breadth/currency-split diagnostics computed from the SAME
DayScores objects (no metric redefinition):

  * per-day cross-section size distribution (median, #days >=5, #usable days)
  * AR vs US name counts per day (currency-mix evidence)
  * IC decay on the mixed universe (default) AND on AR-only / US-only sub-universes,
    by reusing compute_rank_ic with a score_override that simply restricts the
    symbol set per day (same Spearman, same forward returns, same n_min).

Usage:
  python scripts/run_signal_ic_now.py
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import date
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.storage import MarketDB
from core_sim.short_term_day_runner import load_merged_whitelist
from reporting.signal_ic import (
    short_engine_config_from_policy,
    bars_by_date_from_db,
    reconstruct_daily_scores,
    ic_decay_curve,
    compute_rank_ic,
    random_ranking_baseline,
    hit_rate_at_k,
    quantile_spread,
)

DB_PATH = REPO_ROOT / "data" / "market.db"
POLICY_PATH = REPO_ROOT / "config" / "policy.v1.yaml"

START = date(2025, 4, 28)
END = date(2026, 6, 2)
HORIZONS = (1, 2, 3, 5, 8, 10)
N_MIN = 5


def _restrict_scores(daily_scores, markets_allowed):
    """Return a {day: {sym: score}} override keeping only symbols in allowed markets.

    Reuses the exact same scores/forward-returns; just narrows the per-day universe.
    """
    override = {}
    for day in daily_scores:
        keep = {
            sym: sc
            for sym, sc in day.scores.items()
            if day.market_by_symbol.get(sym) in markets_allowed
        }
        override[day.trading_day] = keep
    return override


def _decay_with_override(daily_scores, bars_by_date, trading_days, override):
    out = []
    for h in HORIZONS:
        res = compute_rank_ic(
            daily_scores, bars_by_date, h, n_min=N_MIN,
            score_override=override, trading_days=trading_days,
        )
        out.append(res.as_dict())
    return out


def main() -> None:
    policy_doc = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    config = short_engine_config_from_policy(policy_doc)
    merged_whitelist = load_merged_whitelist(REPO_ROOT, policy_doc)
    db = MarketDB(str(DB_PATH))

    bars_by_date = bars_by_date_from_db(db, START, END, merged_whitelist)
    trading_days = sorted(bars_by_date.keys())
    daily_scores = reconstruct_daily_scores(
        bars_by_date=bars_by_date,
        merged_whitelist=merged_whitelist,
        config=config,
        trading_days=trading_days,
    )

    # ---- Breadth diagnostics (cross-section of SCORED names per day) ----
    sizes = [len(d.scores) for d in daily_scores]
    days_ge5 = sum(1 for s in sizes if s >= 5)
    # currency mix per day among scored names
    ar_counts, us_counts = [], []
    for d in daily_scores:
        mk = d.market_by_symbol
        ar = sum(1 for s in d.scores if mk.get(s) == "AR")
        us = sum(1 for s in d.scores if mk.get(s) == "US")
        ar_counts.append(ar)
        us_counts.append(us)

    breadth = {
        "whitelist_size": len(merged_whitelist),
        "trading_days_loaded": len(trading_days),
        "days_with_any_score": len(daily_scores),
        "cross_section_median": statistics.median(sizes) if sizes else 0,
        "cross_section_mean": round(statistics.fmean(sizes), 3) if sizes else 0,
        "cross_section_max": max(sizes) if sizes else 0,
        "days_with_ge5_names": days_ge5,
        "ar_per_day_median": statistics.median(ar_counts) if ar_counts else 0,
        "us_per_day_median": statistics.median(us_counts) if us_counts else 0,
        "ar_per_day_mean": round(statistics.fmean(ar_counts), 3) if ar_counts else 0,
        "us_per_day_mean": round(statistics.fmean(us_counts), 3) if us_counts else 0,
    }

    # ---- Decay curves: mixed (default), AR-only, US-only ----
    decay_mixed = ic_decay_curve(
        daily_scores, bars_by_date, horizons=HORIZONS, n_min=N_MIN,
        trading_days=trading_days,
    )["curve"]
    decay_ar = _decay_with_override(
        daily_scores, bars_by_date, trading_days, _restrict_scores(daily_scores, {"AR"})
    )
    decay_us = _decay_with_override(
        daily_scores, bars_by_date, trading_days, _restrict_scores(daily_scores, {"US"})
    )

    # ---- Baseline / hit-rate / spread at h=1 (mixed) ----
    h0 = 1
    engine_ic = compute_rank_ic(daily_scores, bars_by_date, h0, n_min=N_MIN, trading_days=trading_days)
    null = random_ranking_baseline(
        daily_scores, bars_by_date, h0, n_min=N_MIN, repetitions=20, seed=12345,
        trading_days=trading_days,
    )
    hit = hit_rate_at_k(
        daily_scores, bars_by_date, h0, top_k=config.top_k_per_market, n_min=N_MIN,
        trading_days=trading_days,
    )
    spread = quantile_spread(daily_scores, bars_by_date, h0, n_min=N_MIN, trading_days=trading_days)

    report = {
        "range": {"start": START.isoformat(), "end": END.isoformat()},
        "breadth": breadth,
        "decay_mixed": decay_mixed,
        "decay_ar_only": decay_ar,
        "decay_us_only": decay_us,
        "engine_ic_h1": engine_ic.as_dict(),
        "random_null_h1": null,
        "hit_rate_at_k_h1": hit,
        "quantile_spread_h1": spread,
    }
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
