"""Data quality stage for the validation workflow.

This stage is purely informational — it NEVER blocks GO.
It measures imputed bars and calendar gaps across all whitelisted symbols.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path

import yaml

from data.storage import MarketDB
from validation.report import StageResult

logger = logging.getLogger(__name__)

_IMPUTED_PCT_THRESHOLD = 5.0  # informational only

# Venue mapping per market
_VENUE_US = "XNYS"
_VENUE_AR = "BYMA"


# ---------------------------------------------------------------------------
# Whitelist loading
# ---------------------------------------------------------------------------

def _load_whitelist(path: str) -> list[str]:
    """Load a YAML whitelist file and return a flat list of symbol strings."""
    resolved = Path(path)
    if not resolved.is_absolute():
        # Resolve relative to the repo root (two levels up from this file)
        repo_root = Path(__file__).parent.parent.parent
        resolved = repo_root / path
    if not resolved.exists():
        logger.warning("Whitelist file not found: %s", resolved)
        return []
    with resolved.open("r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    symbols: list[str] = []
    if isinstance(doc, dict):
        for section_symbols in doc.values():
            if isinstance(section_symbols, list):
                symbols.extend(str(s) for s in section_symbols)
    return symbols


def _collect_symbols(policy_doc: dict) -> list[tuple[str, str]]:
    """Return a list of (symbol, venue) pairs from the policy whitelist files + inline lists."""
    sym_cfg = policy_doc.get("symbols", {})

    us_file = sym_cfg.get("whitelist_us_file", "")
    ar_file = sym_cfg.get("whitelist_ar_file", "")
    inline_us: list[str] = sym_cfg.get("inline_us") or []
    inline_ar: list[str] = sym_cfg.get("inline_ar") or []

    us_symbols = (_load_whitelist(us_file) if us_file else []) + list(inline_us)
    ar_symbols = (_load_whitelist(ar_file) if ar_file else []) + list(inline_ar)

    pairs: list[tuple[str, str]] = []
    for sym in us_symbols:
        pairs.append((sym, _VENUE_US))
    for sym in ar_symbols:
        pairs.append((sym, _VENUE_AR))
    return pairs


# ---------------------------------------------------------------------------
# Main stage function
# ---------------------------------------------------------------------------

def run_data_quality_stage(
    db: MarketDB,
    trading_days: list[date],
    policy_doc: dict,
) -> StageResult:
    """Run the data quality stage.

    Never blocks GO — passed is always True.
    Violations are informational warnings only.

    Args:
        db: MarketDB instance to query OHLCV data.
        trading_days: Ordered list of trading days for the lookback period.
        policy_doc: Parsed policy.v1.yaml as a dict.

    Returns:
        StageResult with stage="data_quality", passed=True, and metrics/violations populated.
    """
    if not trading_days:
        return StageResult(
            stage="data_quality",
            passed=True,
            metrics={
                "total_bars_checked": 0,
                "imputed_bars": 0,
                "imputed_pct": 0.0,
                "symbols_with_gaps": [],
                "symbols_checked": 0,
                "trading_days_checked": 0,
            },
            violations=[],
        )

    period_start: date = trading_days[0]
    period_end: date = trading_days[-1]
    trading_days_set: set[date] = set(trading_days)

    symbol_venue_pairs = _collect_symbols(policy_doc)

    total_bars_checked = 0
    imputed_bars = 0
    symbols_with_gaps: list[str] = []

    for symbol, venue in symbol_venue_pairs:
        rows = db.get_ohlcv(symbol, period_start, period_end, venue)

        bar_dates: set[date] = {r.ts for r in rows}
        imputed_in_sym = sum(1 for r in rows if r.imputed)

        total_bars_checked += len(rows)
        imputed_bars += imputed_in_sym

        # A gap is a trading day that has no bar at all (real or imputed)
        missing_days = trading_days_set - bar_dates
        if missing_days:
            symbols_with_gaps.append(symbol)
            logger.info(
                '{"event": "data_quality_gap", "symbol": "%s", "venue": "%s", "missing_days": %d}',
                symbol,
                venue,
                len(missing_days),
            )

    imputed_pct = (
        round(imputed_bars / total_bars_checked * 100, 2)
        if total_bars_checked > 0
        else 0.0
    )

    violations: list[str] = []

    if imputed_pct > _IMPUTED_PCT_THRESHOLD:
        violations.append(
            f"imputed_pct {imputed_pct}% exceeds 5% informational threshold"
        )

    if symbols_with_gaps:
        violations.append(
            f"symbols_with_gaps: {symbols_with_gaps}"
        )

    return StageResult(
        stage="data_quality",
        passed=True,
        metrics={
            "total_bars_checked": total_bars_checked,
            "imputed_bars": imputed_bars,
            "imputed_pct": imputed_pct,
            "symbols_with_gaps": symbols_with_gaps,
            "symbols_checked": len(symbol_venue_pairs),
            "trading_days_checked": len(trading_days),
        },
        violations=violations,
    )
