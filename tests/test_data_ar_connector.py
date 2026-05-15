"""Behavior tests for data/connectors/ar_connector.py.

All tests are fully mocked — zero real network calls.
"""

from __future__ import annotations

import logging
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from data.connectors.ar_connector import fetch_ar_ohlcv
from data.iol_api_meter import (
    IOL_KIND_HISTORY,
    IOL_KIND_UNIVERSE_VOLUME,
    iol_meter_session,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_START = date(2024, 3, 4)
_END = date(2024, 3, 6)

_IOL_PAYLOAD = [
    {
        "fecha": "2024-03-04T00:00:00",
        "apertura": 1000.0,
        "maximo": 1050.0,
        "minimo": 990.0,
        "ultimoPrecio": 1030.0,
        "volumen": 500_000.0,
    }
]

_IOL_ENV = {"IOL_USER": "testuser", "IOL_PASS": "testpass"}


def _make_byma_df(rows: list[dict] | None = None) -> pd.DataFrame:
    if rows is None:
        rows = [
            {
                "Date": pd.Timestamp("2024-03-04"),
                "Open": 1000.0,
                "High": 1050.0,
                "Low": 990.0,
                "Close": 1030.0,
                "Volume": 500_000.0,
            }
        ]
    df = pd.DataFrame(rows).set_index("Date")
    return df


def _patch_iol_access_token(token: str = "fake-token"):
    return patch("data.connectors.ar_connector._iol_get_access_token", return_value=token)


def _patch_requests_get(json_payload=None, side_effect=None):
    """Patch requests.get used by _iol_fetch_once."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = json_payload if json_payload is not None else _IOL_PAYLOAD
    mock_resp.raise_for_status = MagicMock()
    mock_get = MagicMock()
    if side_effect is not None:
        mock_get.side_effect = side_effect
    else:
        mock_get.return_value = mock_resp
    return patch("data.connectors.ar_connector.requests.get", mock_get)


def _patch_byma_history(return_value=None, side_effect=None):
    mock_ticker = MagicMock()
    if side_effect is not None:
        mock_ticker.history.side_effect = side_effect
    else:
        mock_ticker.history.return_value = return_value if return_value is not None else _make_byma_df()
    return patch("data.connectors.ar_connector.yf.Ticker", return_value=mock_ticker)


# ---------------------------------------------------------------------------
# IOL happy path
# ---------------------------------------------------------------------------


class TestIolSuccess:
    def test_should_return_normalized_ar_ohlcv_rows_when_iol_succeeds_on_first_attempt(self):
        """IOL primary succeeds → returns OHLCVRow list with venue=AR and currency=ARS."""
        with patch.dict("os.environ", _IOL_ENV):
            with _patch_iol_access_token():
                with _patch_requests_get():
                    result = fetch_ar_ohlcv("GGAL", _START, _END)

        assert result is not None
        assert len(result) == 1
        row = result[0]
        assert row.symbol == "GGAL"
        assert row.ts == date(2024, 3, 4)
        assert row.open == pytest.approx(1000.0)
        assert row.high == pytest.approx(1050.0)
        assert row.low == pytest.approx(990.0)
        assert row.close == pytest.approx(1030.0)
        assert row.volume == pytest.approx(500_000.0)
        assert row.currency == "ARS"
        assert row.venue == "AR"
        assert row.imputed is False

    def test_should_preserve_clean_symbol_without_ba_suffix_in_output_rows(self):
        """Symbol in output rows must not have a .BA suffix — IOL never uses it."""
        with patch.dict("os.environ", _IOL_ENV):
            with _patch_iol_access_token():
                with _patch_requests_get():
                    result = fetch_ar_ohlcv("YPFD", _START, _END)

        assert result is not None
        assert all(row.symbol == "YPFD" for row in result)
        assert all(".BA" not in row.symbol for row in result)


# ---------------------------------------------------------------------------
# IOL retry
# ---------------------------------------------------------------------------


class TestIolRetry:
    def test_should_succeed_when_iol_fails_on_first_attempt_and_recovers_on_second(self):
        """Transient IOL network failure on attempt 1 is retried and succeeds on attempt 2."""
        call_count = 0

        def get_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("transient failure")
            mock_resp = MagicMock()
            mock_resp.json.return_value = _IOL_PAYLOAD
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        with patch.dict("os.environ", _IOL_ENV):
            with _patch_iol_access_token():
                with patch("data.connectors.ar_connector.requests.get", side_effect=get_side_effect):
                    with patch("data.connectors.ar_connector.time.sleep"):
                        result = fetch_ar_ohlcv("GGAL", _START, _END)

        assert result is not None
        assert len(result) == 1
        assert call_count == 2

    def test_should_apply_backoff_sleep_between_iol_retries(self):
        """Exponential backoff sleeps are called between IOL retry attempts."""
        sleep_calls = []

        with patch.dict("os.environ", _IOL_ENV):
            with _patch_iol_access_token():
                with patch("data.connectors.ar_connector.requests.get", side_effect=ConnectionError("down")):
                    with patch("data.connectors.ar_connector.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                        with _patch_byma_history(side_effect=ConnectionError("byma also down")):
                            with patch("data.connectors.ar_connector.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                                fetch_ar_ohlcv("GGAL", _START, _END)

        # At minimum 2 sleeps from IOL retries (attempts 1 and 2, no sleep after attempt 3)
        assert len(sleep_calls) >= 2


# ---------------------------------------------------------------------------
# IOL 401 / auth refresh
# ---------------------------------------------------------------------------


class TestIolUnauthorizedRetry:
    def test_should_refresh_access_and_retry_when_history_returns_401(self):
        """401 on history invalidates bearer; next attempt uses refresh_token grant then succeeds."""
        from data.connectors.ar_connector import clear_iol_session_cache

        clear_iol_session_cache()

        def post_side_effect(url, data=None, **kwargs):
            assert "invertironline.com/token" in url
            gtype = (data or {}).get("grant_type")
            mock = MagicMock()
            mock.status_code = 200
            mock.text = ""
            if gtype == "password":
                mock.json.return_value = {
                    "access_token": "access-from-password",
                    "refresh_token": "refresh-1",
                    "expires_in": 900,
                }
                return mock
            if gtype == "refresh_token":
                mock.json.return_value = {
                    "access_token": "access-from-refresh",
                    "refresh_token": "refresh-2",
                    "expires_in": 900,
                }
                return mock
            raise AssertionError(f"unexpected grant_type: {gtype!r}")

        get_calls = {"n": 0}

        def get_side_effect(url, **kwargs):
            get_calls["n"] += 1
            if get_calls["n"] == 1:
                first = MagicMock()
                first.status_code = 401
                return first
            ok = MagicMock()
            ok.status_code = 200
            ok.json.return_value = _IOL_PAYLOAD
            ok.raise_for_status = MagicMock()
            return ok

        with patch.dict("os.environ", _IOL_ENV):
            with patch("data.connectors.ar_connector.requests.post", side_effect=post_side_effect):
                with patch("data.connectors.ar_connector.requests.get", side_effect=get_side_effect):
                    with patch("data.connectors.ar_connector.time.sleep"):
                        result = fetch_ar_ohlcv("GGAL", _START, _END)

        assert result is not None
        assert len(result) == 1
        assert get_calls["n"] == 2


# ---------------------------------------------------------------------------
# Fallback to Byma
# ---------------------------------------------------------------------------


class TestBymaFallback:
    def test_should_use_byma_fallback_when_iol_exhausts_all_retries(self):
        """IOL fails all 3 attempts → Byma fallback returns valid rows."""
        with patch.dict("os.environ", _IOL_ENV):
            with _patch_iol_access_token():
                with patch("data.connectors.ar_connector.requests.get", side_effect=ConnectionError("iol down")):
                    with patch("data.connectors.ar_connector.time.sleep"):
                        with _patch_byma_history():
                            result = fetch_ar_ohlcv("GGAL", _START, _END)

        assert result is not None
        assert len(result) == 1
        assert result[0].venue == "AR"
        assert result[0].currency == "ARS"

    def test_should_log_fallback_trigger_when_switching_from_iol_to_byma(self, caplog):
        """When IOL fails and Byma is used, a structured INFO log with source=byma_fallback is emitted."""
        with patch.dict("os.environ", _IOL_ENV):
            with _patch_iol_access_token():
                with patch("data.connectors.ar_connector.requests.get", side_effect=ConnectionError("iol down")):
                    with patch("data.connectors.ar_connector.time.sleep"):
                        with _patch_byma_history():
                            with caplog.at_level(logging.INFO, logger="data.connectors.ar_connector"):
                                fetch_ar_ohlcv("GGAL", _START, _END)

        assert any("byma_fallback" in r.message for r in caplog.records)
        assert any("iol_failed_using_byma_fallback" in r.message for r in caplog.records)

    def test_should_strip_ba_suffix_from_symbol_in_byma_output_rows(self):
        """Byma uses GGAL.BA for fetching but output rows must have clean 'GGAL' symbol."""
        with patch.dict("os.environ", _IOL_ENV):
            with _patch_iol_access_token():
                with patch("data.connectors.ar_connector.requests.get", side_effect=ConnectionError("iol down")):
                    with patch("data.connectors.ar_connector.time.sleep"):
                        with _patch_byma_history():
                            result = fetch_ar_ohlcv("GGAL", _START, _END)

        assert result is not None
        assert all(row.symbol == "GGAL" for row in result)
        assert all(".BA" not in row.symbol for row in result)


# ---------------------------------------------------------------------------
# Total failure (IOL + Byma both exhaust retries)
# ---------------------------------------------------------------------------


class TestTotalFailure:
    def test_should_return_none_when_iol_and_byma_both_exhaust_all_retries(self):
        """When all providers fail all attempts, returns None without raising."""
        with patch.dict("os.environ", _IOL_ENV):
            with _patch_iol_access_token():
                with patch("data.connectors.ar_connector.requests.get", side_effect=ConnectionError("iol down")):
                    with patch("data.connectors.ar_connector.time.sleep"):
                        with _patch_byma_history(side_effect=ConnectionError("byma also down")):
                            result = fetch_ar_ohlcv("GGAL", _START, _END)

        assert result is None

    def test_should_not_raise_exception_to_caller_when_all_providers_fail(self):
        """Caller safety contract: fetch_ar_ohlcv never propagates exceptions."""
        with patch.dict("os.environ", _IOL_ENV):
            with _patch_iol_access_token():
                with patch("data.connectors.ar_connector.requests.get", side_effect=RuntimeError("crash")):
                    with patch("data.connectors.ar_connector.time.sleep"):
                        with _patch_byma_history(side_effect=OSError("network")):
                            try:
                                result = fetch_ar_ohlcv("GGAL", _START, _END)
                            except Exception as exc:
                                pytest.fail(f"fetch_ar_ohlcv raised an exception: {exc}")

        assert result is None

    def test_should_log_error_when_all_retries_exhausted(self, caplog):
        """A structured ERROR log is emitted after all providers exhaust retries."""
        with patch.dict("os.environ", _IOL_ENV):
            with _patch_iol_access_token():
                with patch("data.connectors.ar_connector.requests.get", side_effect=ConnectionError("iol down")):
                    with patch("data.connectors.ar_connector.time.sleep"):
                        with _patch_byma_history(side_effect=ConnectionError("byma down")):
                            with caplog.at_level(logging.ERROR, logger="data.connectors.ar_connector"):
                                fetch_ar_ohlcv("GGAL", _START, _END)

        error_msgs = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_msgs) > 0
        assert any("fetch_skipped" in m for m in error_msgs)


# ---------------------------------------------------------------------------
# IOL-only mode (universe ranking — no Byma fallback)
# ---------------------------------------------------------------------------


class TestIolOnly:
    def test_should_return_none_without_byma_when_iol_only_and_no_credentials(self):
        """iol_only must not call yfinance if IOL credentials are absent."""
        import os

        with patch.dict("os.environ", {}, clear=True):
            os.environ.pop("IOL_USER", None)
            os.environ.pop("IOL_PASS", None)
            with patch("data.connectors.ar_connector.yf.Ticker") as mock_ticker:
                result = fetch_ar_ohlcv("GGAL", _START, _END, iol_only=True)
        assert result is None
        mock_ticker.assert_not_called()


class TestIolPerKindMetering:
    def test_should_increment_history_counter_for_default_ar_fetch(self):
        db = MagicMock()
        with patch.dict("os.environ", _IOL_ENV):
            with _patch_iol_access_token():
                with _patch_requests_get():
                    with iol_meter_session(db, "2026-05", max_calls_per_job=500):
                        fetch_ar_ohlcv("GGAL", _START, _END)
        db.increment_iol_api_usage.assert_called()
        kw_calls = [c.kwargs for c in db.increment_iol_api_usage.call_args_list]
        assert any(k.get("history") == 1 and k.get("universe_volume") == 0 for k in kw_calls)

    def test_should_increment_universe_volume_counter_when_meter_kind_set(self):
        db = MagicMock()
        with patch.dict("os.environ", _IOL_ENV):
            with _patch_iol_access_token():
                with _patch_requests_get():
                    with iol_meter_session(db, "2026-05", max_calls_per_job=500):
                        fetch_ar_ohlcv(
                            "GGAL",
                            _START,
                            _END,
                            iol_only=True,
                            iol_meter_kind=IOL_KIND_UNIVERSE_VOLUME,
                        )
        kw_calls = [c.kwargs for c in db.increment_iol_api_usage.call_args_list]
        assert any(k.get("universe_volume") == 1 for k in kw_calls)

    def test_should_use_distinct_meter_kind_constants(self):
        assert IOL_KIND_HISTORY != IOL_KIND_UNIVERSE_VOLUME


# ---------------------------------------------------------------------------
# Missing IOL credentials
# ---------------------------------------------------------------------------


class TestMissingCredentials:
    def test_should_skip_iol_immediately_when_credentials_are_absent(self):
        """Without IOL_USER/IOL_PASS env vars, no network call is made to IOL."""
        with patch.dict("os.environ", {}, clear=True):
            # Ensure env vars are absent
            import os
            os.environ.pop("IOL_USER", None)
            os.environ.pop("IOL_PASS", None)

            with patch("data.connectors.ar_connector._iol_get_access_token") as mock_token:
                with patch("data.connectors.ar_connector.requests.get") as mock_get:
                    with _patch_byma_history():
                        result = fetch_ar_ohlcv("GGAL", _START, _END)

            mock_token.assert_not_called()
            mock_get.assert_not_called()

        assert result is not None  # Byma fallback kicks in

    def test_should_log_warning_when_iol_credentials_missing(self, caplog):
        """skip_reason=iol_credentials_missing is logged at WARNING when env vars absent."""
        import os
        with patch.dict("os.environ", {}, clear=True):
            os.environ.pop("IOL_USER", None)
            os.environ.pop("IOL_PASS", None)

            with _patch_byma_history():
                with caplog.at_level(logging.WARNING, logger="data.connectors.ar_connector"):
                    fetch_ar_ohlcv("GGAL", _START, _END)

        assert any("iol_credentials_missing" in r.message for r in caplog.records)

    def test_should_return_none_when_credentials_absent_and_byma_also_fails(self):
        """No credentials + Byma failure → None, not exception."""
        import os
        with patch.dict("os.environ", {}, clear=True):
            os.environ.pop("IOL_USER", None)
            os.environ.pop("IOL_PASS", None)

            with _patch_byma_history(side_effect=ConnectionError("byma down")):
                with patch("data.connectors.ar_connector.time.sleep"):
                    result = fetch_ar_ohlcv("GGAL", _START, _END)

        assert result is None


# ---------------------------------------------------------------------------
# Partial / malformed data
# ---------------------------------------------------------------------------


class TestPartialData:
    def test_should_return_empty_list_when_iol_payload_missing_required_keys(self):
        """IOL response missing required keys (e.g. 'apertura') triggers DataError → []."""
        bad_payload = [{"fecha": "2024-03-04T00:00:00", "ultimoPrecio": 1030.0}]

        with patch.dict("os.environ", _IOL_ENV):
            with _patch_iol_access_token():
                with _patch_requests_get(json_payload=bad_payload):
                    result = fetch_ar_ohlcv("GGAL", _START, _END)

        assert result == []

    def test_should_return_empty_list_when_byma_response_missing_required_columns(self):
        """Byma DataFrame missing Volume column triggers DataError → []."""
        bad_df = pd.DataFrame(
            [{"Open": 1000.0, "High": 1050.0, "Low": 990.0, "Close": 1030.0}],
            index=[pd.Timestamp("2024-03-04")],
        )
        bad_df.index.name = "Date"

        import os
        with patch.dict("os.environ", {}, clear=True):
            os.environ.pop("IOL_USER", None)
            os.environ.pop("IOL_PASS", None)

            with _patch_byma_history(return_value=bad_df):
                result = fetch_ar_ohlcv("GGAL", _START, _END)

        assert result == []

    def test_should_not_retry_on_data_error_from_iol(self):
        """DataError from IOL payload is not retried — only one attempt made before returning []."""
        bad_payload = [{"only_junk": True}]
        call_count = 0

        def get_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            mock_resp.json.return_value = bad_payload
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        with patch.dict("os.environ", _IOL_ENV):
            with _patch_iol_access_token():
                with patch("data.connectors.ar_connector.requests.get", side_effect=get_side_effect):
                    with patch("data.connectors.ar_connector.time.sleep") as mock_sleep:
                        result = fetch_ar_ohlcv("GGAL", _START, _END)

        assert result == []
        assert call_count == 1
        mock_sleep.assert_not_called()
