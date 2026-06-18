"""Normalize a list[OHLCVRow]: drop invalid rows, forward-fill calendar gaps."""

from __future__ import annotations

import logging
import math
import statistics
from datetime import date, timedelta
from typing import NamedTuple

from data.schema import OHLCVRow

logger = logging.getLogger(__name__)

_OUTLIER_HIGH_FACTOR = 10.0
_OUTLIER_LOW_FACTOR = 0.1
# Salto dia-a-dia tipo cambio de ratio CEDEAR / split no registrado: un close
# que mas que duplica (o menos que halvea) el anterior no es un movimiento de
# mercado plausible — se descarta hasta que el evento se registre y la serie
# se ajuste (scripts/adjust_cedear_ratio.py). Caso origen: SPY XBUE 2026-05-29
# cayo 3x nominal por cambio de ratio 1:3 y paso el filtro de outliers x10.
_RATIO_JUMP_FACTOR = 2.0
_ROLLING_WINDOW = 5
_MAX_FILL_DAYS = 3


class _SkipEvent(NamedTuple):
    symbol: str
    ts: date
    skip_reason: str


def normalize(rows: list[OHLCVRow], calendar: set[date]) -> list[OHLCVRow]:
    """Return a clean, calendar-aligned list of OHLCVRow.

    Steps (in order):
    1. Drop rows with invalid prices or volume.
    2. Drop price outliers (rolling-5-day median ×10 / ÷10).
    3. Forward-fill up to _MAX_FILL_DAYS calendar gaps per symbol.
    4. Sort by ts ascending.

    Never raises — returns [] on empty / invalid input.
    """
    if not rows:
        return []

    try:
        return _normalize(rows, calendar)
    except Exception:
        logger.exception('{"event": "normalizer_unexpected_error"}')
        return []


# ---------------------------------------------------------------------------
# Internal pipeline
# ---------------------------------------------------------------------------

def _normalize(rows: list[OHLCVRow], calendar: set[date]) -> list[OHLCVRow]:
    rows_sorted = sorted(rows, key=lambda r: r.ts)

    # Group by symbol so each symbol has its own rolling window and fill logic.
    symbols: dict[str, list[OHLCVRow]] = {}
    for row in rows_sorted:
        symbols.setdefault(row.symbol, []).append(row)

    result: list[OHLCVRow] = []
    for symbol, sym_rows in symbols.items():
        clean, dropped_dates = _drop_invalid(sym_rows)
        clean, outlier_dates = _drop_outliers(clean)
        # Dates that were explicitly removed must not be forward-filled —
        # they are bad data, not genuine calendar gaps.
        excluded = dropped_dates | outlier_dates
        effective_calendar = calendar - excluded
        filled = _forward_fill(clean, effective_calendar)
        result.extend(filled)

    return sorted(result, key=lambda r: r.ts)


def _is_finite_positive(value: float | None) -> bool:
    """True when *value* is a finite number strictly greater than zero."""
    if value is None:
        return False
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v > 0


def _is_finite_non_negative_volume(value: float | None) -> bool:
    """Volume may be zero on AR feeds (yfinance stale quote); reject NaN/negative only."""
    if value is None:
        return False
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v >= 0


def _drop_invalid(rows: list[OHLCVRow]) -> tuple[list[OHLCVRow], set[date]]:
    """Remove rows with invalid OHLCV values. Returns (kept, dropped_dates)."""
    kept: list[OHLCVRow] = []
    dropped: set[date] = set()
    for row in rows:
        if not _is_finite_non_negative_volume(row.volume):
            _log_skip(row.symbol, row.ts, "invalid_volume")
            dropped.add(row.ts)
            continue
        if not all(
            _is_finite_positive(v) for v in (row.open, row.high, row.low, row.close)
        ):
            _log_skip(row.symbol, row.ts, "invalid_price")
            dropped.add(row.ts)
            continue
        _check_ohlc_consistency(row)
        kept.append(row)
    return kept, dropped


def _drop_outliers(rows: list[OHLCVRow]) -> tuple[list[OHLCVRow], set[date]]:
    """Drop rows where close is > 10× or < 0.1× the rolling-5-day median,
    or where close jumps more than ×2 / ÷2 vs the previous kept close
    (cambio de ratio CEDEAR / split no registrado → ``suspect_ratio_jump``).
    Returns (kept, dropped_dates).
    """
    if not rows:
        return [], set()

    kept: list[OHLCVRow] = []
    dropped: set[date] = set()
    closes: list[float] = []

    for row in rows:
        window = closes[-_ROLLING_WINDOW:] if len(closes) >= _ROLLING_WINDOW else closes[:]
        if len(window) >= 2:
            med = statistics.median(window)
            if row.close > med * _OUTLIER_HIGH_FACTOR or row.close < med * _OUTLIER_LOW_FACTOR:
                _log_skip(row.symbol, row.ts, "price_outlier")
                dropped.add(row.ts)
                # Do NOT append to closes — don't let the outlier poison the window.
                continue
        if closes:
            prev = closes[-1]
            if row.close > prev * _RATIO_JUMP_FACTOR or row.close < prev / _RATIO_JUMP_FACTOR:
                _log_skip(row.symbol, row.ts, "suspect_ratio_jump")
                dropped.add(row.ts)
                continue
        closes.append(row.close)
        kept.append(row)

    return kept, dropped


def _forward_fill(rows: list[OHLCVRow], calendar: set[date]) -> list[OHLCVRow]:
    """Insert imputed rows for calendar gaps of up to _MAX_FILL_DAYS."""
    if not rows:
        return []

    # Only care about calendar days that fall within the range of existing rows.
    first_ts = rows[0].ts
    last_ts = rows[-1].ts
    relevant_cal = sorted(d for d in calendar if first_ts <= d <= last_ts)

    row_by_date: dict[date, OHLCVRow] = {r.ts: r for r in rows}
    result: list[OHLCVRow] = list(rows)

    # Walk the calendar to find gaps.
    i = 0
    while i < len(relevant_cal):
        d = relevant_cal[i]
        if d in row_by_date:
            i += 1
            continue

        # Found a gap — find how many consecutive missing calendar days follow.
        gap_start = i
        gap_dates: list[date] = []
        while i < len(relevant_cal) and relevant_cal[i] not in row_by_date:
            gap_dates.append(relevant_cal[i])
            i += 1

        if not gap_dates:
            continue

        # Find the last valid row before the gap.
        last_valid = _last_row_before(rows, gap_dates[0])
        if last_valid is None:
            # No prior row — cannot fill.
            continue

        symbol = last_valid.symbol
        if len(gap_dates) > _MAX_FILL_DAYS:
            logger.warning(
                '{"event": "gap_too_large", "symbol": "%s", "gap_start": "%s", "gap_days": %d, "skip_reason": "gap_too_large"}',
                symbol,
                gap_dates[0].isoformat(),
                len(gap_dates),
            )
            continue

        for fill_date in gap_dates:
            imputed = _make_imputed(last_valid, fill_date)
            result.append(imputed)
            row_by_date[fill_date] = imputed
            logger.info(
                '{"event": "gap_forward_filled", "symbol": "%s", "ts": "%s"}',
                symbol,
                fill_date.isoformat(),
            )

    return sorted(result, key=lambda r: r.ts)


def _last_row_before(rows: list[OHLCVRow], target: date) -> OHLCVRow | None:
    """Return the latest row whose ts < target (rows must be sorted asc)."""
    result = None
    for row in rows:
        if row.ts < target:
            result = row
        else:
            break
    return result


def _make_imputed(source: OHLCVRow, fill_date: date) -> OHLCVRow:
    return OHLCVRow(
        symbol=source.symbol,
        ts=fill_date,
        open=source.open,
        high=source.high,
        low=source.low,
        close=source.close,
        volume=0.0,
        currency=source.currency,
        venue=source.venue,
        imputed=True,
    )


def _check_ohlc_consistency(row: OHLCVRow) -> None:
    """Log a warning when OHLC ordering is violated — does NOT remove the row."""
    if not (row.high >= row.close and row.high >= row.open and
            row.low <= row.close and row.low <= row.open):
        logger.warning(
            '{"event": "ohlc_inconsistency", "symbol": "%s", "ts": "%s", '
            '"open": %s, "high": %s, "low": %s, "close": %s}',
            row.symbol,
            row.ts.isoformat(),
            row.open,
            row.high,
            row.low,
            row.close,
        )


def _log_skip(symbol: str, ts: date, skip_reason: str) -> None:
    logger.warning(
        '{"event": "row_skipped", "symbol": "%s", "ts": "%s", "skip_reason": "%s"}',
        symbol,
        ts.isoformat(),
        skip_reason,
    )
