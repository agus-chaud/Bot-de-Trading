"""Behavior tests for data.fetcher.fetch_and_store.

All external I/O (connectors, calendar_builder, normalize, MarketDB) is mocked.
Tests verify the FetchReport contract and per-symbol isolation.
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from data.connectors.ar_connector import ArFetchResult
from data.fetch_trace import (
    FETCH_STATUS_OK,
    FETCH_STATUS_SKIP,
    SKIP_CREDENTIALS_MISSING,
    SKIP_FALLBACK_USED,
    SOURCE_BYMA,
    SOURCE_IOL,
    SOURCE_MIXED,
    SymbolFetchTrace,
    VENUE_AR,
    apply_source_attribution,
)
from data.fetcher import FetchReport, fetch_and_store
from data.schema import OHLCVRow
from data.universe_selector import merge_fetch_universe

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

START = date(2024, 1, 2)
END = date(2024, 1, 5)


def _make_row(symbol: str, ts: date, venue: str = "US") -> OHLCVRow:
    return OHLCVRow(
        symbol=symbol,
        ts=ts,
        open=10.0,
        high=11.0,
        low=9.0,
        close=10.5,
        volume=1000.0,
        currency="USD",
        venue=venue,
        imputed=False,
    )


def _make_db() -> MagicMock:
    """Mock MarketDB with a _conn that returns empty calendar rows."""
    db = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    db._conn.execute.return_value = cursor
    return db


def _ar_fetch_result(
    rows: list[OHLCVRow] | None,
    *,
    symbol: str = "GGAL",
    status: str | None = None,
    skip_reason: str | None = None,
    source: str | None = None,
) -> ArFetchResult:
    if status is None:
        status = FETCH_STATUS_OK if rows else FETCH_STATUS_SKIP
    trace = SymbolFetchTrace(
        symbol=symbol,
        venue=VENUE_AR,
        start_date=START,
        end_date=END,
        status=status,
        skip_reason=skip_reason,
        source=source,
        iol_only=False,
        rows=len(rows) if rows else 0,
    )
    return ArFetchResult(rows=rows, trace=trace)


# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

_PATCH_BUILD_CAL = "data.fetcher.build_calendar"
_PATCH_FETCH_US = "data.fetcher.fetch_us_ohlcv"
_PATCH_FETCH_AR = "data.fetcher.fetch_ar_ohlcv_with_trace"
_PATCH_NORMALIZE = "data.fetcher.normalize"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFetchAndStoreUS:
    def test_successful_us_symbol_appears_in_fetched_us(self):
        """When a US connector returns rows, the symbol lands in fetched_us."""
        raw = [_make_row("SPY", START)]
        normalized = [_make_row("SPY", START)]
        db = _make_db()

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_US, return_value=raw),
            patch(_PATCH_NORMALIZE, return_value=normalized),
        ):
            report = fetch_and_store(["SPY"], [], START, END, db)

        assert "SPY" in report.fetched_us
        assert report.skipped_us == []
        assert report.errors == []

    def test_rows_stored_increments_by_normalized_count(self):
        """rows_stored equals the total rows returned by normalize for successful symbols."""
        raw = [_make_row("SPY", START), _make_row("SPY", date(2024, 1, 3))]
        normalized = raw  # normalize returns same here
        db = _make_db()

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_US, return_value=raw),
            patch(_PATCH_NORMALIZE, return_value=normalized),
        ):
            report = fetch_and_store(["SPY"], [], START, END, db)

        assert report.rows_stored == 2

    def test_connector_returns_none_goes_to_skipped_us(self):
        """None from connector means permanent failure → symbol in skipped_us."""
        db = _make_db()

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_US, return_value=None),
        ):
            report = fetch_and_store(["SPY"], [], START, END, db)

        assert "SPY" in report.skipped_us
        assert report.fetched_us == []
        assert report.rows_stored == 0

    def test_connector_returns_empty_list_goes_to_skipped_us(self):
        """Empty list from connector (valid symbol, no data) → symbol in skipped_us."""
        db = _make_db()

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_US, return_value=[]),
        ):
            report = fetch_and_store(["SPY"], [], START, END, db)

        assert "SPY" in report.skipped_us
        assert report.fetched_us == []

    def test_unexpected_exception_captured_in_errors(self):
        """An unexpected exception for one symbol is captured; report returns normally."""
        db = _make_db()

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_US, side_effect=RuntimeError("boom")),
        ):
            report = fetch_and_store(["SPY"], [], START, END, db)

        assert report.fetched_us == []
        assert report.skipped_us == []
        assert len(report.errors) == 1
        assert "SPY" in report.errors[0]


class TestFetchAndStoreAR:
    def test_successful_ar_symbol_appears_in_fetched_ar(self):
        """When AR connector succeeds, symbol goes to fetched_ar, not fetched_us."""
        raw = [_make_row("GGAL", START, venue="XBUE")]
        normalized = raw
        db = _make_db()

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_AR, return_value=_ar_fetch_result(raw)),
            patch(_PATCH_NORMALIZE, return_value=normalized),
        ):
            report = fetch_and_store([], ["GGAL"], START, END, db)

        assert "GGAL" in report.fetched_ar
        assert report.fetched_us == []

    def test_connector_returns_none_goes_to_skipped_ar(self):
        db = _make_db()

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_AR, return_value=_ar_fetch_result(None)),
        ):
            report = fetch_and_store([], ["GGAL"], START, END, db)

        assert "GGAL" in report.skipped_ar
        assert report.fetched_ar == []

    def test_connector_returns_empty_list_goes_to_skipped_ar(self):
        db = _make_db()

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_AR, return_value=_ar_fetch_result([])),
        ):
            report = fetch_and_store([], ["GGAL"], START, END, db)

        assert "GGAL" in report.skipped_ar


class TestErrorIsolation:
    def test_error_in_one_symbol_does_not_stop_others(self):
        """If the first US symbol raises, the second is still processed."""
        raw = [_make_row("QQQ", START)]
        db = _make_db()

        def _us_connector(symbol, start, end, **_):
            if symbol == "SPY":
                raise RuntimeError("network failure")
            return raw

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_US, side_effect=_us_connector),
            patch(_PATCH_NORMALIZE, return_value=raw),
        ):
            report = fetch_and_store(["SPY", "QQQ"], [], START, END, db)

        assert "QQQ" in report.fetched_us
        assert len(report.errors) == 1
        assert "SPY" in report.errors[0]

    def test_ar_error_does_not_stop_us_symbols(self):
        """AR failure is isolated and doesn't affect US processing."""
        us_rows = [_make_row("SPY", START)]
        db = _make_db()

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_US, return_value=us_rows),
            patch(_PATCH_FETCH_AR, side_effect=RuntimeError("ar failure")),
            patch(_PATCH_NORMALIZE, return_value=us_rows),
        ):
            report = fetch_and_store(["SPY"], ["GGAL"], START, END, db)

        assert "SPY" in report.fetched_us
        assert len(report.errors) == 1


class TestMixedReport:
    def test_mix_us_and_ar_symbols_correctly_separated(self):
        """US and AR results are reported in their respective lists."""
        us_rows = [_make_row("SPY", START)]
        ar_rows = [_make_row("GGAL", START, venue="XBUE")]
        db = _make_db()

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_US, return_value=us_rows),
            patch(_PATCH_FETCH_AR, return_value=_ar_fetch_result(ar_rows)),
            patch(_PATCH_NORMALIZE, side_effect=[us_rows, ar_rows]),
        ):
            report = fetch_and_store(["SPY"], ["GGAL"], START, END, db)

        assert "SPY" in report.fetched_us
        assert "GGAL" in report.fetched_ar
        assert report.skipped_us == []
        assert report.skipped_ar == []

    def test_rows_stored_is_sum_across_all_successful_symbols(self):
        """rows_stored accumulates across US and AR symbols."""
        us_rows = [_make_row("SPY", START), _make_row("SPY", date(2024, 1, 3))]
        ar_rows = [_make_row("GGAL", START, venue="XBUE")]
        db = _make_db()

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_US, return_value=us_rows),
            patch(_PATCH_FETCH_AR, return_value=_ar_fetch_result(ar_rows)),
            patch(_PATCH_NORMALIZE, side_effect=[us_rows, ar_rows]),
        ):
            report = fetch_and_store(["SPY"], ["GGAL"], START, END, db)

        assert report.rows_stored == 3  # 2 US + 1 AR

    def test_skipped_and_fetched_are_mutually_exclusive(self):
        """A symbol cannot appear in both fetched and skipped."""
        us_rows = [_make_row("SPY", START)]
        db = _make_db()

        def _us_connector(symbol, start, end, **_):
            if symbol == "SPY":
                return us_rows
            return None

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_US, side_effect=_us_connector),
            patch(_PATCH_NORMALIZE, return_value=us_rows),
        ):
            report = fetch_and_store(["SPY", "IWM"], [], START, END, db)

        assert set(report.fetched_us).isdisjoint(set(report.skipped_us))


class TestArUniverseContractForFetcher:
    def test_fetch_and_store_should_receive_ar_list_built_like_merge_fetch_universe(self):
        """Ingesta AR usa la misma unión ordenada top_Merval ∪ top_CEDEAR ∪ holdings que expone el selector."""
        top_m = ["BMA", "GGAL"]
        top_c = ["AAPL"]
        holdings = ["OLDPOS"]
        symbols_ar = merge_fetch_universe(top_m, top_c, holdings)
        db = _make_db()

        def _fetch_side_effect(sym: str, start: date, end: date, **_):
            return _ar_fetch_result([_make_row(sym, START, venue="XBUE")], symbol=sym)

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_AR, side_effect=_fetch_side_effect) as mock_ar,
            patch(_PATCH_NORMALIZE, side_effect=lambda rows, _cal: rows),
        ):
            fetch_and_store([], symbols_ar, START, END, db)

        fetched_symbols = {c.args[0] for c in mock_ar.call_args_list}
        assert fetched_symbols == set(symbols_ar)


class TestFetchLogPersistence:
    def test_should_persist_fetch_log_on_us_success(self):
        raw = [_make_row("SPY", START)]
        db = _make_db()

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_US, return_value=raw),
            patch(_PATCH_NORMALIZE, return_value=raw),
        ):
            fetch_and_store(["SPY"], [], START, END, db)

        db.log_fetch.assert_called_once()
        entry = db.log_fetch.call_args.args[0]
        extra = json.loads(entry["extra"])
        assert entry["symbol"] == "SPY"
        assert entry["venue"] == "XNYS"
        assert entry["status"] == "ok"
        assert entry["source"] == "yfinance"
        assert extra["rows_by_source"] == {"yfinance": 1}
        assert extra["effective_source"] == "yfinance"

    def test_should_persist_fetch_log_on_ar_skip(self):
        db = _make_db()

        with patch(_PATCH_BUILD_CAL), patch(
            _PATCH_FETCH_AR,
            return_value=_ar_fetch_result(
                None, skip_reason="connector_returned_none", source="iol"
            ),
        ):
            fetch_and_store([], ["GGAL"], START, END, db)

        db.log_fetch.assert_called_once()
        entry = db.log_fetch.call_args.args[0]
        assert entry["symbol"] == "GGAL"
        assert entry["venue"] == "XBUE"
        assert entry["status"] == "skip"

    def test_should_persist_ar_iol_success_with_provider_source_and_rows(self):
        raw = [_make_row("GGAL", START, venue="XBUE")]
        trace = SymbolFetchTrace(
            symbol="GGAL",
            venue=VENUE_AR,
            start_date=START,
            end_date=END,
            status=FETCH_STATUS_OK,
            provider=SOURCE_IOL,
            source=SOURCE_IOL,
            iol_only=False,
            rows=1,
        )
        apply_source_attribution(trace, {SOURCE_IOL: 1}, partial_fallback=False)
        db = _make_db()

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_AR, return_value=ArFetchResult(rows=raw, trace=trace)),
            patch(_PATCH_NORMALIZE, return_value=raw),
        ):
            fetch_and_store([], ["GGAL"], START, END, db)

        entry = db.log_fetch.call_args.args[0]
        extra = json.loads(entry["extra"])
        assert entry["status"] == "ok"
        assert entry["source"] == SOURCE_IOL
        assert extra["provider"] == SOURCE_IOL
        assert extra["rows"] == 1
        assert extra["effective_source"] == SOURCE_IOL

    def test_should_persist_ar_fallback_with_skip_reason_and_mixed_source(self):
        raw = [
            _make_row("GGAL", START, venue="XBUE"),
            _make_row("GGAL", date(2024, 1, 3), venue="XBUE"),
        ]
        trace = SymbolFetchTrace(
            symbol="GGAL",
            venue=VENUE_AR,
            start_date=START,
            end_date=END,
            status=FETCH_STATUS_OK,
            provider=SOURCE_IOL,
            skip_reason=SKIP_FALLBACK_USED,
            iol_only=False,
            rows=2,
        )
        apply_source_attribution(
            trace, {SOURCE_IOL: 1, SOURCE_BYMA: 1}, partial_fallback=True
        )
        db = _make_db()

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_AR, return_value=ArFetchResult(rows=raw, trace=trace)),
            patch(_PATCH_NORMALIZE, return_value=raw),
        ):
            fetch_and_store([], ["GGAL"], START, END, db)

        entry = db.log_fetch.call_args.args[0]
        extra = json.loads(entry["extra"])
        assert entry["status"] == "ok"
        assert entry["skip_reason"] == SKIP_FALLBACK_USED
        assert entry["source"] == SOURCE_MIXED
        assert extra["partial_fallback"] is True
        assert extra["rows_by_source"] == {SOURCE_IOL: 1, SOURCE_BYMA: 1}

    def test_should_persist_iol_only_and_credentials_missing_on_ar_skip(self):
        trace = SymbolFetchTrace(
            symbol="GGAL",
            venue=VENUE_AR,
            start_date=START,
            end_date=END,
            status=FETCH_STATUS_SKIP,
            provider=SOURCE_IOL,
            skip_reason=SKIP_CREDENTIALS_MISSING,
            iol_only=True,
            rows=0,
        )
        db = _make_db()

        with patch(_PATCH_BUILD_CAL), patch(
            _PATCH_FETCH_AR, return_value=ArFetchResult(rows=None, trace=trace)
        ):
            fetch_and_store([], ["GGAL"], START, END, db, iol_only=True)

        entry = db.log_fetch.call_args.args[0]
        extra = json.loads(entry["extra"])
        assert entry["status"] == "skip"
        assert entry["skip_reason"] == SKIP_CREDENTIALS_MISSING
        assert extra["iol_only"] is True
        assert extra["provider"] == SOURCE_IOL

    def test_should_persist_us_skip_with_max_retries_skip_reason(self):
        db = _make_db()

        with patch(_PATCH_BUILD_CAL), patch(_PATCH_FETCH_US, return_value=None):
            fetch_and_store(["SPY"], [], START, END, db)

        entry = db.log_fetch.call_args.args[0]
        extra = json.loads(entry["extra"])
        assert entry["status"] == "skip"
        assert entry["skip_reason"] == "max_retries_exceeded"
        assert entry["source"] == "yfinance"
        assert extra["provider"] == "yfinance"


class TestFetchReport:
    def test_fetch_report_is_frozen(self):
        """FetchReport is immutable — modification raises AttributeError."""
        report = FetchReport(
            fetched_us=[], fetched_ar=[], skipped_us=[], skipped_ar=[],
            rows_stored=0, errors=[],
        )
        with pytest.raises((AttributeError, TypeError)):
            report.rows_stored = 99  # type: ignore[misc]
