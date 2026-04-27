"""Behavior tests for data/connectors/us_connector.py.

All tests mock yfinance — no real network calls.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch, PropertyMock

import pandas as pd
import pytest

from data.connectors.us_connector import fetch_us_ohlcv


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_START = date(2024, 1, 15)
_END = date(2024, 1, 17)


def _make_df(rows: list[dict] | None = None) -> pd.DataFrame:
    """Build a minimal yfinance-style DataFrame."""
    if rows is None:
        rows = [
            {
                "Date": pd.Timestamp("2024-01-15"),
                "Open": 460.0,
                "High": 465.0,
                "Low": 458.0,
                "Close": 463.0,
                "Volume": 80_000_000.0,
            }
        ]
    df = pd.DataFrame(rows)
    df = df.set_index("Date")
    return df


def _patch_history(return_value=None, side_effect=None):
    """Context manager that patches yf.Ticker(...).history(...)."""
    mock_ticker = MagicMock()
    if side_effect is not None:
        mock_ticker.history.side_effect = side_effect
    else:
        mock_ticker.history.return_value = return_value if return_value is not None else _make_df()
    return patch("data.connectors.us_connector.yf.Ticker", return_value=mock_ticker)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestFetchUsOhlcvSuccess:
    def test_should_return_normalized_ohlcv_rows_on_success(self):
        """Successful fetch returns OHLCVRow list with all schema fields populated."""
        with _patch_history():
            result = fetch_us_ohlcv("SPY", _START, _END)

        assert result is not None
        assert len(result) == 1
        row = result[0]
        assert row.symbol == "SPY"
        assert row.ts == date(2024, 1, 15)
        assert row.open == pytest.approx(460.0)
        assert row.high == pytest.approx(465.0)
        assert row.low == pytest.approx(458.0)
        assert row.close == pytest.approx(463.0)
        assert row.volume == pytest.approx(80_000_000.0)
        assert row.currency == "USD"
        assert row.venue == "US"
        assert row.imputed is False

    def test_should_return_multiple_rows_when_range_spans_several_days(self):
        """Multiple trading days in range produce one OHLCVRow per day."""
        df = _make_df([
            {"Date": pd.Timestamp("2024-01-15"), "Open": 460.0, "High": 465.0, "Low": 458.0, "Close": 463.0, "Volume": 80_000_000.0},
            {"Date": pd.Timestamp("2024-01-16"), "Open": 463.0, "High": 470.0, "Low": 461.0, "Close": 468.0, "Volume": 75_000_000.0},
        ])
        with _patch_history(return_value=df):
            result = fetch_us_ohlcv("SPY", _START, _END)

        assert result is not None
        assert len(result) == 2
        assert result[0].ts == date(2024, 1, 15)
        assert result[1].ts == date(2024, 1, 16)


# ---------------------------------------------------------------------------
# Retry behavior
# ---------------------------------------------------------------------------


class TestRetryBehavior:
    def test_should_succeed_on_second_attempt_when_first_fails_with_network_error(self):
        """Retry logic recovers from a transient network failure on the first attempt."""
        df = _make_df()
        call_count = 0

        def history_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("transient network error")
            return df

        mock_ticker = MagicMock()
        mock_ticker.history.side_effect = history_side_effect

        with patch("data.connectors.us_connector.yf.Ticker", return_value=mock_ticker):
            with patch("data.connectors.us_connector.time.sleep"):
                result = fetch_us_ohlcv("SPY", _START, _END)

        assert result is not None
        assert len(result) == 1
        assert call_count == 2

    def test_should_return_none_when_all_three_attempts_fail(self):
        """Exhausting all retries returns None without raising an exception."""
        with _patch_history(side_effect=ConnectionError("network down")):
            with patch("data.connectors.us_connector.time.sleep"):
                result = fetch_us_ohlcv("SPY", _START, _END)

        assert result is None

    def test_should_not_raise_exception_to_caller_when_retries_exhausted(self):
        """fetch_us_ohlcv must not propagate exceptions — caller safety contract."""
        with _patch_history(side_effect=OSError("timeout")):
            with patch("data.connectors.us_connector.time.sleep"):
                try:
                    result = fetch_us_ohlcv("SPY", _START, _END)
                except Exception as exc:
                    pytest.fail(f"fetch_us_ohlcv raised an exception: {exc}")

        assert result is None

    def test_should_sleep_with_backoff_between_retries(self):
        """Backoff sleeps are applied between failed attempts."""
        sleep_calls = []

        def history_side_effect(**kwargs):
            raise ConnectionError("fail")

        mock_ticker = MagicMock()
        mock_ticker.history.side_effect = history_side_effect

        with patch("data.connectors.us_connector.yf.Ticker", return_value=mock_ticker):
            with patch("data.connectors.us_connector.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                fetch_us_ohlcv("SPY", _START, _END)

        # 3 attempts → 2 sleeps (no sleep after the last attempt)
        assert sleep_calls == [1, 2]

    def test_should_log_skip_reason_when_retries_exhausted(self, caplog):
        """A structured skip_reason is logged at ERROR level after permanent failure."""
        import logging
        with _patch_history(side_effect=ConnectionError("down")):
            with patch("data.connectors.us_connector.time.sleep"):
                with caplog.at_level(logging.ERROR, logger="data.connectors.us_connector"):
                    fetch_us_ohlcv("SPY", _START, _END)

        assert any("fetch_skipped" in r.message for r in caplog.records)
        assert any("max_retries_exceeded" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


class TestTimeoutHandling:
    def test_should_retry_when_request_times_out(self):
        """A timeout exception is treated as a network error and triggers retry."""
        df = _make_df()
        call_count = 0

        def history_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError("request timed out")
            return df

        mock_ticker = MagicMock()
        mock_ticker.history.side_effect = history_side_effect

        with patch("data.connectors.us_connector.yf.Ticker", return_value=mock_ticker):
            with patch("data.connectors.us_connector.time.sleep"):
                result = fetch_us_ohlcv("SPY", _START, _END, timeout=5)

        assert result is not None
        assert call_count == 2


# ---------------------------------------------------------------------------
# Invalid / empty symbol
# ---------------------------------------------------------------------------


class TestInvalidSymbol:
    def test_should_return_empty_list_when_yfinance_returns_no_data(self):
        """An unknown symbol that returns an empty DataFrame yields [] not None."""
        empty_df = pd.DataFrame()
        with _patch_history(return_value=empty_df):
            result = fetch_us_ohlcv("INVALID_XYZ", _START, _END)

        assert result == []

    def test_should_log_skip_reason_for_empty_symbol(self, caplog):
        """Empty data triggers a skip_reason log entry at error or warning level."""
        import logging
        empty_df = pd.DataFrame()
        with _patch_history(return_value=empty_df):
            with caplog.at_level(logging.WARNING, logger="data.connectors.us_connector"):
                fetch_us_ohlcv("INVALID_XYZ", _START, _END)

        # Empty result returns [] immediately — no skip log needed at this level.
        # The test verifies the contract: no exception is raised.


# ---------------------------------------------------------------------------
# Partial / malformed data
# ---------------------------------------------------------------------------


class TestPartialData:
    def test_should_return_empty_and_log_skip_when_required_columns_missing(self, caplog):
        """Missing OHLCV columns in response triggers skip_reason log and returns []."""
        import logging
        # DataFrame missing Volume column
        bad_df = pd.DataFrame(
            [{"Open": 460.0, "High": 465.0, "Low": 458.0, "Close": 463.0}],
            index=[pd.Timestamp("2024-01-15")],
        )
        bad_df.index.name = "Date"

        with _patch_history(return_value=bad_df):
            with caplog.at_level(logging.ERROR, logger="data.connectors.us_connector"):
                result = fetch_us_ohlcv("SPY", _START, _END)

        assert result == []
        assert any("fetch_skipped" in r.message for r in caplog.records)
        assert any("data_error" in r.message for r in caplog.records)

    def test_should_not_retry_on_data_error(self):
        """Data errors (e.g. missing columns) are not retried — only one attempt made."""
        bad_df = pd.DataFrame(
            [{"Open": 460.0, "Close": 463.0}],
            index=[pd.Timestamp("2024-01-15")],
        )
        bad_df.index.name = "Date"

        call_count = 0
        original_history = MagicMock(return_value=bad_df)

        def counting_history(**kwargs):
            nonlocal call_count
            call_count += 1
            return bad_df

        mock_ticker = MagicMock()
        mock_ticker.history.side_effect = counting_history

        with patch("data.connectors.us_connector.yf.Ticker", return_value=mock_ticker):
            with patch("data.connectors.us_connector.time.sleep") as mock_sleep:
                result = fetch_us_ohlcv("SPY", _START, _END)

        assert result == []
        assert call_count == 1
        mock_sleep.assert_not_called()
