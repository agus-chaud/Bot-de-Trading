"""Fetch AR OHLCV bars from InvertirOnline (primary) with Byma/yfinance fallback."""

from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta
from typing import Optional

import requests
import yfinance as yf

from data.schema import OHLCVRow

logger = logging.getLogger(__name__)

_VENUE = "AR"
_CURRENCY = "ARS"
_REQUIRED_COLUMNS = {"Open", "High", "Low", "Close", "Volume"}

_BACKOFF_SECONDS = [1, 2, 4]
_MAX_ATTEMPTS = 3

_IOL_TOKEN_URL = "https://api.invertironline.com/token"
_IOL_HISTORY_URL = "https://api.invertironline.com/api/v2/{mercado}/Titulos/{symbol}/Cotizacion/seriehistorica/{start}/{end}/ajustada"
_IOL_MERCADO = "bCBA"  # Bolsas y Mercados Argentinos via IOL


class NetworkError(Exception):
    """Raised internally when a network-level failure occurs during fetch."""


class DataError(Exception):
    """Raised internally when the fetched payload is malformed or empty."""


def fetch_ar_ohlcv(
    symbol: str,
    start_date: date,
    end_date: date,
    timeout: int = 30,
) -> Optional[list[OHLCVRow]]:
    """Fetch daily OHLCV bars for *symbol* from IOL (primary) or Byma/yfinance (fallback).

    Credential check is performed upfront — if IOL_USER/IOL_PASS are absent the
    function skips IOL entirely without any network attempt and proceeds straight
    to the Byma fallback.

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
    else:
        result = _fetch_with_retry_iol(symbol, start_date, end_date, timeout, iol_user, iol_pass)
        if result is not None:
            return result
        # IOL exhausted — fall through to Byma
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
) -> Optional[list[OHLCVRow]]:
    """Retry loop for IOL. Returns list[OHLCVRow] on success, None on exhaustion."""
    last_exc: Exception | None = None

    for attempt in range(_MAX_ATTEMPTS):
        try:
            token = _iol_get_token(iol_user, iol_pass, timeout)
            rows = _iol_fetch_once(symbol, start_date, end_date, timeout, token)
            return rows
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


def _iol_get_token(iol_user: str, iol_pass: str, timeout: int) -> str:
    """Obtain a bearer token from IOL. Raises NetworkError on failure."""
    try:
        resp = requests.post(
            _IOL_TOKEN_URL,
            data={
                "username": iol_user,
                "password": iol_pass,
                "grant_type": "password",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]
    except Exception as exc:
        raise NetworkError(f"IOL token request failed: {exc}") from exc


def _iol_fetch_once(
    symbol: str,
    start_date: date,
    end_date: date,
    timeout: int,
    token: str,
) -> list[OHLCVRow]:
    """Single IOL fetch attempt. Raises NetworkError or DataError."""
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
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        raise NetworkError(f"IOL history request failed: {exc}") from exc

    if not payload:
        return []

    try:
        rows = _normalize_iol(symbol, payload)
    except Exception as exc:
        raise DataError(f"IOL normalization failed: {exc}") from exc

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
