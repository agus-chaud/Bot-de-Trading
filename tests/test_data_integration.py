"""Integration tests for the data layer pipeline.

Each test exercises multiple layers working together with a real SQLite in-memory DB.
External connectors (yfinance, IOL) are mocked — everything else runs for real.

Key contract discoveries captured here:
- us_connector produces venue="XNYS" (canonical ISO MIC for NYSE) — normalizer passes it through unchanged
- ar_connector produces venue="AR" (NOT "XBUE") — same
- MarketDB.get_ohlcv filters by venue, so queries must use the venue the connector sets
- calendar_builder uses "XNYS"/"XBUE" as venue keys in the calendars table
- fetcher._get_calendar queries by venue from calendars table — must match what build_calendar inserts
- normalize() forward-fills gaps only within the calendar provided; imputed rows have volume=0
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from data.connectors.ar_connector import ArFetchResult
from data.fetch_trace import FETCH_STATUS_OK, FETCH_STATUS_SKIP, SymbolFetchTrace, VENUE_AR
from data.fetcher import FetchReport, fetch_and_store
from data.normalizer import normalize
from data.schema import OHLCVRow
from data.storage import MarketDB


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    """Real MarketDB backed by SQLite in-memory — no temp files, no teardown needed."""
    return MarketDB(":memory:")


def _us_row(symbol: str, ts: date, close: float = 100.0) -> OHLCVRow:
    return OHLCVRow(
        symbol=symbol,
        ts=ts,
        open=close * 0.99,
        high=close * 1.01,
        low=close * 0.98,
        close=close,
        volume=1_000_000.0,
        currency="USD",
        venue="XNYS",   # canonical ISO MIC for NYSE — what us_connector produces
        imputed=False,
    )


def _ar_row(symbol: str, ts: date, close: float = 500.0) -> OHLCVRow:
    return OHLCVRow(
        symbol=symbol,
        ts=ts,
        open=close * 0.99,
        high=close * 1.01,
        low=close * 0.98,
        close=close,
        volume=50_000.0,
        currency="ARS",
        venue="AR",   # what ar_connector actually produces
        imputed=False,
    )


def _consecutive_weekdays(start: date, n: int) -> list[date]:
    days: list[date] = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


# Week of 2024-01-08 (Mon) → 2024-01-12 (Fri) — no holidays
START = date(2024, 1, 8)
END = date(2024, 1, 12)
WEEKDAYS = _consecutive_weekdays(START, 5)  # Mon-Fri


# ---------------------------------------------------------------------------
# Group 1: connector → normalize → storage (pipeline per symbol)
# ---------------------------------------------------------------------------

class TestUSPipelineHappyPath:
    def test_five_rows_survive_pipeline_and_are_retrievable(self, db):
        """US connector rows flow through normalize → upsert → are queryable with correct fields."""
        rows = [_us_row("SPY", d) for d in WEEKDAYS]
        calendar = set(WEEKDAYS)

        normalized = normalize(rows, calendar)
        db.upsert_ohlcv(normalized)

        stored = db.get_ohlcv("SPY", START, END, venue="XNYS")
        assert len(stored) == 5
        assert all(r.venue == "XNYS" for r in stored)
        assert all(r.currency == "USD" for r in stored)
        assert all(r.imputed is False for r in stored)
        dates_stored = {r.ts for r in stored}
        assert dates_stored == set(WEEKDAYS)


class TestARPipelineHappyPath:
    def test_ar_rows_stored_with_ar_venue_and_ars_currency(self, db):
        """AR connector rows keep venue=AR and currency=ARS through the full pipeline."""
        rows = [_ar_row("GGAL", d) for d in WEEKDAYS]
        calendar = set(WEEKDAYS)

        normalized = normalize(rows, calendar)
        db.upsert_ohlcv(normalized)

        stored = db.get_ohlcv("GGAL", START, END, venue="AR")
        assert len(stored) == 5
        assert all(r.venue == "AR" for r in stored)
        assert all(r.currency == "ARS" for r in stored)
        assert all(r.imputed is False for r in stored)


class TestPipelineWithOutlier:
    def test_outlier_row_is_absent_from_storage_after_pipeline(self, db):
        """A price 15× the rolling median is dropped by normalize — not stored."""
        base_close = 100.0
        days = WEEKDAYS
        rows = [_us_row("SPY", d, close=base_close) for d in days[:4]]
        # Day 5: price is 15× the rolling median → should be dropped as outlier
        outlier_day = days[4]
        rows.append(_us_row("SPY", outlier_day, close=base_close * 15))
        calendar = set(days)

        normalized = normalize(rows, calendar)
        db.upsert_ohlcv(normalized)

        stored = db.get_ohlcv("SPY", START, END, venue="XNYS")
        stored_dates = {r.ts for r in stored}
        assert outlier_day not in stored_dates
        # Confirm outlier day was not gap-filled either (it's in excluded set)
        assert len(stored) == 4


class TestPipelineWithGap:
    def test_gap_days_are_imputed_with_zero_volume_in_storage(self, db):
        """Two missing business days within a week are forward-filled with imputed=True, volume=0."""
        # Provide Mon + Fri only; Tue, Wed, Thu are gaps (3 consecutive = within _MAX_FILL_DAYS=3)
        mon, tue, wed, thu, fri = WEEKDAYS
        rows = [_us_row("SPY", mon), _us_row("SPY", fri)]
        # Calendar must include ALL 5 days for the gap to be detectable
        calendar = set(WEEKDAYS)

        normalized = normalize(rows, calendar)
        db.upsert_ohlcv(normalized)

        stored = db.get_ohlcv("SPY", START, END, venue="XNYS")
        imputed_rows = [r for r in stored if r.imputed]
        real_rows = [r for r in stored if not r.imputed]

        assert len(real_rows) == 2
        assert len(imputed_rows) == 3  # Tue, Wed, Thu
        assert all(r.volume == 0.0 for r in imputed_rows)
        assert {r.ts for r in imputed_rows} == {tue, wed, thu}


# ---------------------------------------------------------------------------
# Group 2: fetcher → storage round-trip
# ---------------------------------------------------------------------------

_PATCH_BUILD_CAL = "data.fetcher.build_calendar"
_PATCH_FETCH_US = "data.fetcher.fetch_us_ohlcv"
_PATCH_FETCH_AR = "data.fetcher.fetch_ar_ohlcv_with_trace"


def _seed_calendar(db: MarketDB, venue: str, days: list[date]) -> None:
    """Directly seed the calendars table so fetcher._get_calendar returns real days."""
    db.upsert_calendars(venue=venue, days=days)


def _ar_fetch_result(rows: list[OHLCVRow] | None, *, symbol: str = "GGAL") -> ArFetchResult:
    status = FETCH_STATUS_OK if rows else FETCH_STATUS_SKIP
    trace = SymbolFetchTrace(
        symbol=symbol,
        venue=VENUE_AR,
        start_date=START,
        end_date=END,
        status=status,
        iol_only=False,
        rows=len(rows) if rows else 0,
    )
    return ArFetchResult(rows=rows, trace=trace)


class TestFetchAndStoreRoundTrip:
    def test_report_is_correct_and_rows_queryable_after_fetch(self, db):
        """fetch_and_store with real DB and mocked connectors — report + storage agree."""
        us_rows = [_us_row("SPY", d) for d in WEEKDAYS]
        ar_rows = [_ar_row("GGAL", d) for d in WEEKDAYS]

        # Seed calendar so normalizer has real calendar days (fetcher reads from DB)
        _seed_calendar(db, "XNYS", WEEKDAYS)
        _seed_calendar(db, "XBUE", WEEKDAYS)

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_US, return_value=us_rows),
            patch(_PATCH_FETCH_AR, return_value=_ar_fetch_result(ar_rows)),
        ):
            report = fetch_and_store(["SPY"], ["GGAL"], START, END, db)

        assert report.fetched_us == ["SPY"]
        assert report.fetched_ar == ["GGAL"]
        assert report.skipped_us == []
        assert report.skipped_ar == []
        assert report.errors == []
        assert report.rows_stored == 10  # 5 US + 5 AR

        spy_rows = db.get_ohlcv("SPY", START, END, venue="XNYS")
        ggal_rows = db.get_ohlcv("GGAL", START, END, venue="AR")
        assert len(spy_rows) == 5
        assert len(ggal_rows) == 5


class TestFetchPartialFailure:
    def test_skipped_symbol_absent_from_storage_successful_symbol_present(self, db):
        """When one US connector returns None, only the successful symbol lands in storage."""
        us_rows_qqq = [_us_row("QQQ", d) for d in WEEKDAYS]
        _seed_calendar(db, "XNYS", WEEKDAYS)

        def _us_connector(symbol, start, end, **_):
            if symbol == "SPY":
                return None   # permanent failure
            return us_rows_qqq

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_US, side_effect=_us_connector),
            patch(_PATCH_FETCH_AR, return_value=_ar_fetch_result(None)),
        ):
            report = fetch_and_store(["SPY", "QQQ"], [], START, END, db)

        assert "SPY" in report.skipped_us
        assert "QQQ" in report.fetched_us
        assert report.errors == []

        spy_rows = db.get_ohlcv("SPY", START, END, venue="XNYS")
        qqq_rows = db.get_ohlcv("QQQ", START, END, venue="XNYS")
        assert len(spy_rows) == 0
        assert len(qqq_rows) == 5


class TestFetchIdempotent:
    def test_second_fetch_does_not_duplicate_rows_in_storage(self, db):
        """Calling fetch_and_store twice with the same data yields identical row count — upsert is idempotent."""
        us_rows = [_us_row("SPY", d) for d in WEEKDAYS]
        _seed_calendar(db, "XNYS", WEEKDAYS)

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_US, return_value=us_rows),
            patch(_PATCH_FETCH_AR, return_value=_ar_fetch_result(None)),
        ):
            report1 = fetch_and_store(["SPY"], [], START, END, db)

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_US, return_value=us_rows),
            patch(_PATCH_FETCH_AR, return_value=_ar_fetch_result(None)),
        ):
            report2 = fetch_and_store(["SPY"], [], START, END, db)

        # Both calls should report the same rows_stored count
        assert report1.rows_stored == report2.rows_stored

        # Storage must have exactly 5 rows — not 10
        stored = db.get_ohlcv("SPY", START, END, venue="XNYS")
        assert len(stored) == 5


# ---------------------------------------------------------------------------
# Group 3: calendar + storage consistency
# ---------------------------------------------------------------------------

class TestCalendarStoredAndRetrieved:
    def test_build_calendar_persists_and_is_queryable(self, db):
        """build_calendar writes NYSE days to DB under venue=XNYS and they come back correctly."""
        from data.calendar_builder import build_calendar

        cal_start = date(2024, 1, 2)
        cal_end = date(2024, 1, 12)

        build_calendar(start=cal_start, end=cal_end, db=db)

        cursor = db._conn.execute(
            "SELECT ts FROM calendars WHERE venue = ? ORDER BY ts", ("XNYS",)
        )
        rows = cursor.fetchall()
        assert len(rows) > 0

        dates_in_db = {date.fromisoformat(r["ts"]) for r in rows}
        # All returned dates must fall within the requested range
        assert all(cal_start <= d <= cal_end for d in dates_in_db)
        # All returned dates must be weekdays (NYSE has no weekends)
        assert all(d.weekday() < 5 for d in dates_in_db)

    def test_xbue_calendar_also_written_by_build_calendar(self, db):
        """build_calendar writes XBUE days in addition to XNYS."""
        from data.calendar_builder import build_calendar

        build_calendar(start=date(2024, 1, 2), end=date(2024, 1, 12), db=db)

        cursor = db._conn.execute(
            "SELECT COUNT(*) as cnt FROM calendars WHERE venue = ?", ("XBUE",)
        )
        count = cursor.fetchone()["cnt"]
        assert count > 0


class TestImputedRowsNotCountedAsReal:
    def test_get_ohlcv_returns_imputed_and_real_rows_indistinguishably(self, db):
        """get_ohlcv has no imputed filter — caller must filter on the imputed field.

        This documents the ACTUAL behavior: get_ohlcv returns all rows regardless of
        imputed status. Callers wanting only real bars must filter themselves.
        """
        mon, tue, wed, thu, fri = WEEKDAYS
        # Store 2 real + 3 imputed rows directly
        real_rows = [_us_row("SPY", mon), _us_row("SPY", fri)]
        imputed_rows = [
            OHLCVRow(
                symbol="SPY", ts=d,
                open=100.0, high=101.0, low=99.0, close=100.0,
                volume=0.0, currency="USD", venue="XNYS", imputed=True,
            )
            for d in (tue, wed, thu)
        ]
        db.upsert_ohlcv(real_rows + imputed_rows)

        all_rows = db.get_ohlcv("SPY", START, END, venue="XNYS")
        real_only = [r for r in all_rows if not r.imputed]
        imputed_only = [r for r in all_rows if r.imputed]

        assert len(all_rows) == 5
        assert len(real_only) == 2
        assert len(imputed_only) == 3
        # Documented behavior: no built-in filter on get_ohlcv — caller filters via .imputed
