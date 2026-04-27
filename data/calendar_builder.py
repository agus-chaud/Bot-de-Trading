"""Build and persist trading calendar data from pandas_market_calendars."""

from __future__ import annotations

from datetime import date

import pandas_market_calendars as mcal

from data.storage import MarketDB


def build_calendar(start: date, end: date, db: MarketDB) -> None:
    """Fetch NYSE and XBUE trading days and persist them in the calendars table.

    Args:
        start: Inclusive start date.
        end: Inclusive end date.
        db: Open MarketDB instance to persist into.
    """
    _fetch_and_store(exchange="NYSE", venue="XNYS", start=start, end=end, db=db)
    _fetch_and_store(exchange="XBUE", venue="XBUE", start=start, end=end, db=db)


def _fetch_and_store(
    exchange: str, venue: str, start: date, end: date, db: MarketDB
) -> None:
    """Fetch valid days for *exchange* and persist under *venue* key."""
    calendar = mcal.get_calendar(exchange)
    datetime_index = calendar.valid_days(
        start_date=start.isoformat(), end_date=end.isoformat()
    )
    days: list[date] = [dt.date() for dt in datetime_index]
    db.upsert_calendars(venue=venue, days=days)
