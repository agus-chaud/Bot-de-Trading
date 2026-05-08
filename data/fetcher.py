"""Orchestrate fetch → normalize → store for US and AR symbols."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from data.calendar_builder import build_calendar
from data.connectors.ar_connector import fetch_ar_ohlcv
from data.connectors.us_connector import fetch_us_ohlcv
from data.normalizer import normalize
from data.storage import MarketDB

logger = logging.getLogger(__name__)

# calendar_builder stores under these venue keys (see calendar_builder.py)
_VENUE_US = "XNYS"
_VENUE_AR = "XBUE"


@dataclass(frozen=True)
class FetchReport:
    fetched_us: list[str]
    fetched_ar: list[str]
    skipped_us: list[str]
    skipped_ar: list[str]
    rows_stored: int
    errors: list[str]


def fetch_and_store(
    symbols_us: list[str],
    symbols_ar: list[str],
    start_date: date,
    end_date: date,
    db: MarketDB,
) -> FetchReport:
    """Fetch, normalize, and store OHLCV bars for all given symbols.

    Each symbol is processed independently — a failure in one never stops others.
    """
    fetched_us: list[str] = []
    fetched_ar: list[str] = []
    skipped_us: list[str] = []
    skipped_ar: list[str] = []
    errors: list[str] = []
    rows_stored = 0

    # Persist calendars once for the full date range before processing symbols.
    # build_calendar writes XNYS and XBUE rows into db.calendars.
    try:
        build_calendar(start=start_date, end=end_date, db=db)
    except Exception as exc:
        # Non-fatal: normalization will still work with an empty calendar (no fill).
        logger.warning('{"event": "calendar_build_failed", "error": "%s"}', exc)

    cal_us = _get_calendar(db, _VENUE_US)
    cal_ar = _get_calendar(db, _VENUE_AR)

    for symbol in symbols_us:
        try:
            rows = fetch_us_ohlcv(symbol, start_date, end_date)
            if rows is None:
                logger.warning('{"event": "symbol_skipped", "symbol": "%s", "venue": "XNYS", "reason": "connector_returned_none"}', symbol)
                skipped_us.append(symbol)
                continue
            if not rows:
                logger.warning('{"event": "symbol_skipped", "symbol": "%s", "venue": "XNYS", "reason": "empty_data"}', symbol)
                skipped_us.append(symbol)
                continue
            normalized = normalize(rows, cal_us)
            db.upsert_ohlcv(normalized)
            rows_stored += len(normalized)
            fetched_us.append(symbol)
            logger.info('{"event": "symbol_fetched", "symbol": "%s", "venue": "XNYS", "rows": %d}', symbol, len(normalized))
        except Exception as exc:
            msg = f"US:{symbol}: unexpected error: {exc}"
            logger.exception('{"event": "symbol_error", "symbol": "%s", "venue": "XNYS"}', symbol)
            errors.append(msg)

    for symbol in symbols_ar:
        try:
            rows = fetch_ar_ohlcv(symbol, start_date, end_date)
            if rows is None:
                logger.warning('{"event": "symbol_skipped", "symbol": "%s", "venue": "AR", "reason": "connector_returned_none"}', symbol)
                skipped_ar.append(symbol)
                continue
            if not rows:
                logger.warning('{"event": "symbol_skipped", "symbol": "%s", "venue": "AR", "reason": "empty_data"}', symbol)
                skipped_ar.append(symbol)
                continue
            normalized = normalize(rows, cal_ar)
            db.upsert_ohlcv(normalized)
            rows_stored += len(normalized)
            fetched_ar.append(symbol)
            logger.info('{"event": "symbol_fetched", "symbol": "%s", "venue": "AR", "rows": %d}', symbol, len(normalized))
        except Exception as exc:
            msg = f"AR:{symbol}: unexpected error: {exc}"
            logger.exception('{"event": "symbol_error", "symbol": "%s", "venue": "AR"}', symbol)
            errors.append(msg)

    return FetchReport(
        fetched_us=fetched_us,
        fetched_ar=fetched_ar,
        skipped_us=skipped_us,
        skipped_ar=skipped_ar,
        rows_stored=rows_stored,
        errors=errors,
    )


def _get_calendar(db: MarketDB, venue: str) -> set[date]:
    """Read calendar days for *venue* from the local DB."""
    cursor = db._conn.execute(
        "SELECT ts FROM calendars WHERE venue = ?", (venue,)
    )
    return {date.fromisoformat(row["ts"]) for row in cursor.fetchall()}
