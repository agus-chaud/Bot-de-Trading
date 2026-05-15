"""Fetch AR OHLCV bars from InvertirOnline (primary) with Byma/yfinance fallback."""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date, timedelta
from typing import Any, Optional

import requests
import yfinance as yf

from data.schema import OHLCVRow
from data.iol_api_meter import (
    IOL_KIND_HISTORY,
    IOL_KIND_REFRESH,
    IOL_KIND_TOKEN,
    IolJobBudgetExhausted,
    record_iol_call,
    try_consume_iol_job_slot,
)

logger = logging.getLogger(__name__)

_VENUE = "AR"
_CURRENCY = "ARS"
_REQUIRED_COLUMNS = {"Open", "High", "Low", "Close", "Volume"}

_BACKOFF_SECONDS = [1, 2, 4]
_MAX_ATTEMPTS = 3

# Auth contract (POST only — GET returns UnsupportedApiVersion in browser):
# https://api.invertironline.com/Help/Autenticacion
_IOL_TOKEN_URL = "https://api.invertironline.com/token"
_IOL_HISTORY_URL = "https://api.invertironline.com/api/v2/{mercado}/Titulos/{symbol}/Cotizacion/seriehistorica/{start}/{end}/ajustada"
_IOL_MERCADO = "bCBA"  # Bolsas y Mercados Argentinos via IOL
_IOL_BEARER_TTL_SECONDS = 15 * 60
_IOL_EXPIRES_SKEW_SECONDS = 120

_iol_lock = threading.Lock()
_iol_session_user: str | None = None
_iol_access_token: str | None = None
_iol_refresh_token: str | None = None
_iol_access_until_monotonic: float = 0.0


class NetworkError(Exception):
    """Raised internally when a network-level failure occurs during fetch."""


class DataError(Exception):
    """Raised internally when the fetched payload is malformed or empty."""


class IolUnauthorized(NetworkError):
    """Bearer rejected (e.g. HTTP 401); session should refresh access token."""


def clear_iol_session_cache() -> None:
    """Drop cached IOL tokens (tests or after credential rotation)."""
    global _iol_session_user, _iol_access_token, _iol_refresh_token, _iol_access_until_monotonic
    with _iol_lock:
        _iol_session_user = None
        _iol_access_token = None
        _iol_refresh_token = None
        _iol_access_until_monotonic = 0.0


def fetch_ar_ohlcv(
    symbol: str,
    start_date: date,
    end_date: date,
    timeout: int = 30,
    *,
    iol_only: bool = False,
    iol_meter_kind: str = IOL_KIND_HISTORY,
) -> Optional[list[OHLCVRow]]:
    """Fetch daily OHLCV bars for *symbol* from IOL (primary) or Byma/yfinance (fallback).

    Credential check is performed upfront — if IOL_USER/IOL_PASS are absent the
    function skips IOL entirely without any network attempt and proceeds straight
    to the Byma fallback.

    Args:
        iol_only: If True, use only IOL (no yfinance fallback). Returns None if
            credentials are missing or IOL fails after retries.

    Returns:
        List of OHLCVRow (possibly empty) on success.
        Empty list when data is received but is structurally invalid (DataError).
        None when all retries on both providers are exhausted.
    """
    iol_user = os.environ.get("IOL_USER")
    iol_pass = os.environ.get("IOL_PASS")

    if not iol_user or not iol_pass:
        logger.warning(
            '{"event": "fetch_skipped", "symbol": "%s", "skip_reason": "iol_credentials_missing", "provider": "iol"}',
            symbol,
        )
        if iol_only:
            return None
    else:
        result: Optional[list[OHLCVRow]] = None
        try:
            result = _fetch_with_retry_iol(
                symbol, start_date, end_date, timeout, iol_user, iol_pass, iol_meter_kind
            )
        except IolJobBudgetExhausted as exc:
            logger.warning(
                '{"event": "iol_fetch_skipped_budget", "symbol": "%s", "iol_only": %s, "detail": "%s"}',
                symbol,
                str(bool(iol_only)).lower(),
                str(exc),
            )
            if iol_only:
                raise
            result = None
        else:
            if result is not None:
                return result
        # IOL exhausted — fall through to Byma
        if iol_only:
            return None
        logger.info(
            '{"event": "fallback_triggered", "symbol": "%s", "skip_reason": "iol_failed_using_byma_fallback", "source": "byma_fallback"}',
            symbol,
        )

    return _fetch_with_retry_byma(symbol, start_date, end_date, timeout)


# ---------------------------------------------------------------------------
# IOL provider
# ---------------------------------------------------------------------------

def _fetch_with_retry_iol(
    symbol: str,
    start_date: date,
    end_date: date,
    timeout: int,
    iol_user: str,
    iol_pass: str,
    iol_meter_kind: str,
) -> Optional[list[OHLCVRow]]:
    """Retry loop for IOL. Returns list[OHLCVRow] on success, None on exhaustion."""
    last_exc: Exception | None = None

    for attempt in range(_MAX_ATTEMPTS):
        try:
            token = _iol_get_access_token(iol_user, iol_pass, timeout)
            rows = _iol_fetch_once(symbol, start_date, end_date, timeout, token, iol_meter_kind)
            return rows
        except IolJobBudgetExhausted as exc:
            logger.warning('{"event": "iol_job_budget_exhausted", "detail": "%s"}', str(exc))
            raise
        except IolUnauthorized as exc:
            last_exc = exc
            _iol_invalidate_bearer_only()
            _log_attempt_failure(symbol, attempt, "iol_unauthorized", str(exc), "iol")
        except NetworkError as exc:
            last_exc = exc
            _log_attempt_failure(symbol, attempt, "network_error", str(exc), "iol")
            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(_BACKOFF_SECONDS[attempt])
        except DataError as exc:
            _log_skip(symbol, "data_error", str(exc), "iol")
            return []

    _log_skip(symbol, "max_retries_exceeded", str(last_exc), "iol")
    return None


def _iol_invalidate_bearer_only() -> None:
    """Force a refresh/login on next token request; keep refresh_token if any."""
    global _iol_access_token, _iol_access_until_monotonic
    with _iol_lock:
        _iol_access_token = None
        _iol_access_until_monotonic = 0.0


def _iol_apply_token_payload(body: dict[str, Any], session_user: str) -> str:
    """Persist tokens from /token JSON; return access_token. Must hold _iol_lock."""
    global _iol_session_user, _iol_access_token, _iol_refresh_token, _iol_access_until_monotonic
    access = body.get("access_token")
    if not access:
        raise NetworkError("IOL token response missing access_token")
    refresh = body.get("refresh_token")
    if refresh:
        _iol_refresh_token = str(refresh)
    expires_in = body.get("expires_in")
    try:
        ttl = int(expires_in) if expires_in is not None else _IOL_BEARER_TTL_SECONDS
    except (TypeError, ValueError):
        ttl = _IOL_BEARER_TTL_SECONDS
    ttl = max(60, ttl - _IOL_EXPIRES_SKEW_SECONDS)
    _iol_session_user = session_user
    _iol_access_token = str(access)
    _iol_access_until_monotonic = time.monotonic() + float(ttl)
    return _iol_access_token


def _iol_token_request(form: dict[str, str], timeout: int) -> dict[str, Any]:
    """POST /token (password or refresh grant). Raises NetworkError on failure."""
    try:
        resp = requests.post(_IOL_TOKEN_URL, data=form, timeout=timeout)
    except Exception as exc:
        raise NetworkError(f"IOL token request failed: {exc}") from exc
    if resp.status_code != 200:
        snippet = (resp.text or "")[:280]
        raise NetworkError(f"IOL token HTTP {resp.status_code}: {snippet}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise NetworkError(f"IOL token response is not JSON: {exc}") from exc
    return body


def _iol_get_access_token(iol_user: str, iol_pass: str, timeout: int) -> str:
    """Return a valid bearer, using cache, refresh_token, or password grant (per IOL docs)."""
    global _iol_session_user, _iol_access_token, _iol_refresh_token, _iol_access_until_monotonic
    with _iol_lock:
        now = time.monotonic()
        if (
            _iol_session_user == iol_user
            and _iol_access_token
            and now < _iol_access_until_monotonic
        ):
            return _iol_access_token
        refresh = _iol_refresh_token if _iol_session_user == iol_user else None

    if refresh:
        if not try_consume_iol_job_slot():
            raise IolJobBudgetExhausted("max_calls_per_job exceeded before IOL token refresh")
        try:
            body = _iol_token_request(
                {"grant_type": "refresh_token", "refresh_token": refresh},
                timeout,
            )
        except NetworkError:
            with _iol_lock:
                if _iol_session_user == iol_user and _iol_refresh_token == refresh:
                    _iol_refresh_token = None
                    _iol_access_token = None
                    _iol_access_until_monotonic = 0.0
        else:
            record_iol_call(IOL_KIND_REFRESH)
            with _iol_lock:
                now = time.monotonic()
                if (
                    _iol_session_user == iol_user
                    and _iol_access_token
                    and now < _iol_access_until_monotonic
                ):
                    return _iol_access_token
                return _iol_apply_token_payload(body, iol_user)

    if not try_consume_iol_job_slot():
        raise IolJobBudgetExhausted("max_calls_per_job exceeded before IOL token (password grant)")
    body = _iol_token_request(
        {
            "username": iol_user,
            "password": iol_pass,
            "grant_type": "password",
        },
        timeout,
    )
    record_iol_call(IOL_KIND_TOKEN)
    with _iol_lock:
        now = time.monotonic()
        if (
            _iol_session_user == iol_user
            and _iol_access_token
            and now < _iol_access_until_monotonic
        ):
            return _iol_access_token
        return _iol_apply_token_payload(body, iol_user)


def _iol_fetch_once(
    symbol: str,
    start_date: date,
    end_date: date,
    timeout: int,
    token: str,
    meter_kind: str,
) -> list[OHLCVRow]:
    """Single IOL fetch attempt. Raises NetworkError or DataError."""
    if not try_consume_iol_job_slot():
        raise IolJobBudgetExhausted("max_calls_per_job exceeded before IOL history GET")
    url = _IOL_HISTORY_URL.format(
        mercado=_IOL_MERCADO,
        symbol=symbol,
        start=start_date.isoformat(),
        end=(end_date + timedelta(days=1)).isoformat(),  # IOL end is exclusive
    )
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        if resp.status_code == 401:
            raise IolUnauthorized("IOL history returned HTTP 401 (bearer expired or invalid)")
        resp.raise_for_status()
        payload = resp.json()
    except IolUnauthorized:
        raise
    except Exception as exc:
        raise NetworkError(f"IOL history request failed: {exc}") from exc

    if not payload:
        record_iol_call(meter_kind)
        return []

    try:
        rows = _normalize_iol(symbol, payload)
    except Exception as exc:
        raise DataError(f"IOL normalization failed: {exc}") from exc

    record_iol_call(meter_kind)
    return rows


def _normalize_iol(symbol: str, payload: list[dict]) -> list[OHLCVRow]:
    """Convert IOL JSON response to list[OHLCVRow].

    IOL returns a list of dicts with keys: apertura, maximo, minimo, ultimoPrecio,
    volumen, fecha — keys verified against the live API contract.
    """
    required_keys = {"apertura", "maximo", "minimo", "ultimoPrecio", "volumen", "fecha"}
    rows: list[OHLCVRow] = []
    for item in payload:
        missing = required_keys - set(item.keys())
        if missing:
            raise DataError(f"Missing keys in IOL response item: {missing}")
        bar_date = date.fromisoformat(item["fecha"][:10])
        rows.append(
            OHLCVRow(
                symbol=symbol,
                ts=bar_date,
                open=float(item["apertura"]),
                high=float(item["maximo"]),
                low=float(item["minimo"]),
                close=float(item["ultimoPrecio"]),
                volume=float(item["volumen"]),
                currency=_CURRENCY,
                venue=_VENUE,
                imputed=False,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Byma / yfinance fallback
# ---------------------------------------------------------------------------

def _fetch_with_retry_byma(
    symbol: str,
    start_date: date,
    end_date: date,
    timeout: int,
) -> Optional[list[OHLCVRow]]:
    """Retry loop for Byma via yfinance. Returns list[OHLCVRow] on success, None on exhaustion."""
    last_exc: Exception | None = None
    yf_symbol = f"{symbol}.BA"

    for attempt in range(_MAX_ATTEMPTS):
        try:
            rows = _byma_fetch_once(symbol, yf_symbol, start_date, end_date, timeout)
            return rows
        except NetworkError as exc:
            last_exc = exc
            _log_attempt_failure(symbol, attempt, "network_error", str(exc), "byma")
            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(_BACKOFF_SECONDS[attempt])
        except DataError as exc:
            _log_skip(symbol, "data_error", str(exc), "byma")
            return []

    _log_skip(symbol, "max_retries_exhausted_byma", str(last_exc), "byma")
    return None


def _byma_fetch_once(
    symbol: str,
    yf_symbol: str,
    start_date: date,
    end_date: date,
    timeout: int,
) -> list[OHLCVRow]:
    """Single Byma/yfinance fetch attempt. Raises NetworkError or DataError."""
    try:
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(
            start=start_date.isoformat(),
            end=end_date.isoformat(),
            auto_adjust=True,
            timeout=timeout,
        )
    except Exception as exc:
        raise NetworkError(f"yfinance request failed for {yf_symbol}: {exc}") from exc

    if df is None or df.empty:
        return []

    missing = _REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise DataError(f"Missing columns in yfinance response: {missing}")

    try:
        rows = _normalize_byma(symbol, df)
    except Exception as exc:
        raise DataError(f"Byma normalization failed: {exc}") from exc

    return rows


def _normalize_byma(symbol: str, df) -> list[OHLCVRow]:
    """Convert yfinance DataFrame to list[OHLCVRow]. symbol is already stripped of .BA."""
    rows: list[OHLCVRow] = []
    for ts, row in df.iterrows():
        bar_date = ts.date() if hasattr(ts, "date") else ts
        rows.append(
            OHLCVRow(
                symbol=symbol,
                ts=bar_date,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=float(row["Volume"]),
                currency=_CURRENCY,
                venue=_VENUE,
                imputed=False,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def _log_attempt_failure(symbol: str, attempt: int, reason: str, detail: str, provider: str) -> None:
    logger.warning(
        '{"event": "fetch_attempt_failed", "symbol": "%s", "attempt": %d, "reason": "%s", "provider": "%s", "detail": "%s"}',
        symbol,
        attempt + 1,
        reason,
        provider,
        detail,
    )


def _log_skip(symbol: str, skip_reason: str, detail: str, provider: str) -> None:
    logger.error(
        '{"event": "fetch_skipped", "symbol": "%s", "skip_reason": "%s", "provider": "%s", "detail": "%s"}',
        symbol,
        skip_reason,
        provider,
        detail,
    )
