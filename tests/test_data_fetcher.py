"""Behavior tests for data.fetcher.fetch_and_store.

All external I/O (connectors, calendar_builder, normalize, MarketDB) is mocked.
Tests verify the FetchReport contract and per-symbol isolation.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, call, patch

import pytest

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


# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

_PATCH_BUILD_CAL = "data.fetcher.build_calendar"
_PATCH_FETCH_US = "data.fetcher.fetch_us_ohlcv"
_PATCH_FETCH_AR = "data.fetcher.fetch_ar_ohlcv"
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
        raw = [_make_row("GGAL", START, venue="AR")]
        normalized = raw
        db = _make_db()

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_AR, return_value=raw),
            patch(_PATCH_NORMALIZE, return_value=normalized),
        ):
            report = fetch_and_store([], ["GGAL"], START, END, db)

        assert "GGAL" in report.fetched_ar
        assert report.fetched_us == []

    def test_connector_returns_none_goes_to_skipped_ar(self):
        db = _make_db()

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_AR, return_value=None),
        ):
            report = fetch_and_store([], ["GGAL"], START, END, db)

        assert "GGAL" in report.skipped_ar
        assert report.fetched_ar == []

    def test_connector_returns_empty_list_goes_to_skipped_ar(self):
        db = _make_db()

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_AR, return_value=[]),
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
        ar_rows = [_make_row("GGAL", START, venue="AR")]
        db = _make_db()

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_US, return_value=us_rows),
            patch(_PATCH_FETCH_AR, return_value=ar_rows),
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
        ar_rows = [_make_row("GGAL", START, venue="AR")]
        db = _make_db()

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_US, return_value=us_rows),
            patch(_PATCH_FETCH_AR, return_value=ar_rows),
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
            return [_make_row(sym, START, venue="AR")]

        with (
            patch(_PATCH_BUILD_CAL),
            patch(_PATCH_FETCH_AR, side_effect=_fetch_side_effect) as mock_ar,
            patch(_PATCH_NORMALIZE, side_effect=lambda rows, _cal: rows),
        ):
            fetch_and_store([], symbols_ar, START, END, db)

        fetched_symbols = {c.args[0] for c in mock_ar.call_args_list}
        assert fetched_symbols == set(symbols_ar)


class TestFetchReport:
    def test_fetch_report_is_frozen(self):
        """FetchReport is immutable — modification raises AttributeError."""
        report = FetchReport(
            fetched_us=[], fetched_ar=[], skipped_us=[], skipped_ar=[],
            rows_stored=0, errors=[],
        )
        with pytest.raises((AttributeError, TypeError)):
            report.rows_stored = 99  # type: ignore[misc]
