"""Fetch US OHLCV bars from yfinance with retry and structured logging."""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Optional

import yfinance as yf

from data.schema import OHLCVRow

logger = logging.getLogger(__name__)

_VENUE = "XNYS"
_CURRENCY = "USD"
_REQUIRED_COLUMNS = {"Open", "High", "Low", "Close", "Volume"}

# Backoff delays in seconds for each retry attempt (index = attempt number, 0-based)
_BACKOFF_SECONDS = [1, 2, 4]
_MAX_ATTEMPTS = 3


class NetworkError(Exception):
    """Raised internally when a network-level failure occurs during fetch."""


class DataError(Exception):
    """Raised internally when the fetched payload is malformed or empty."""


def fetch_us_ohlcv(
    symbol: str,
    start_date: date,
    end_date: date,
    timeout: int = 30,
) -> Optional[list[OHLCVRow]]:
    """Fetch daily OHLCV bars for *symbol* from yfinance and normalize to OHLCVRow.

    Retries up to 3 times with exponential backoff on network failures.
    Returns an empty list when yfinance returns no data for a valid symbol.
    Returns None when all retries are exhausted — caller must treat this as a skip.

    Args:
        symbol: Ticker symbol, e.g. "SPY".
        start_date: Inclusive start of range.
        end_date: Inclusive end of range (yfinance end is exclusive, adjusted internally).
        timeout: Per-request timeout in seconds passed to yfinance.

    Returns:
        List of OHLCVRow (possibly empty) on success, or None on permanent failure.
    """
    last_exc: Exception | None = None

    for attempt in range(_MAX_ATTEMPTS):
        try:
            rows = _fetch_once(symbol, start_date, end_date, timeout)
            return rows
        except NetworkError as exc:
            last_exc = exc
            _log_attempt_failure(symbol, attempt, "network_error", str(exc))
            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(_BACKOFF_SECONDS[attempt])
        except DataError as exc:
            # Data errors are not retryable — log and return empty immediately.
            _log_skip(symbol, "data_error", str(exc))
            return []

    _log_skip(symbol, "max_retries_exceeded", str(last_exc))
    return None


def _fetch_once(
    symbol: str,
    start_date: date,
    end_date: date,
    timeout: int,
) -> list[OHLCVRow]:
    """Single fetch attempt — raises NetworkError or DataError, never returns None."""
    try:
        ticker = yf.Ticker(symbol)
        # yfinance end is exclusive, so we use end_date as-is to include it
        df = ticker.history(
            start=start_date.isoformat(),
            end=end_date.isoformat(),
            auto_adjust=True,
            timeout=timeout,
        )
    except Exception as exc:
        # yfinance surfaces connection problems as generic exceptions
        raise NetworkError(f"yfinance request failed: {exc}") from exc

    if df is None or df.empty:
        return []

    missing = _REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise DataError(f"Missing columns in yfinance response: {missing}")

    try:
        rows = _normalize(symbol, df)
    except Exception as exc:
        raise DataError(f"Normalization failed: {exc}") from exc

    return rows


def _normalize(symbol: str, df) -> list[OHLCVRow]:
    """Convert a yfinance DataFrame to a list of OHLCVRow."""
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


def _log_attempt_failure(symbol: str, attempt: int, reason: str, detail: str) -> None:
    logger.warning(
        '{"event": "fetch_attempt_failed", "symbol": "%s", "attempt": %d, "reason": "%s", "detail": "%s"}',
        symbol,
        attempt + 1,
        reason,
        detail,
    )


def _log_skip(symbol: str, skip_reason: str, detail: str) -> None:
    logger.error(
        '{"event": "fetch_skipped", "symbol": "%s", "skip_reason": "%s", "detail": "%s"}',
        symbol,
        skip_reason,
        detail,
    )
