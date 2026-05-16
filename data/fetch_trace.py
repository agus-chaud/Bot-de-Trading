"""Taxonomía y helpers para trazabilidad de fetch en fetch_log."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from data.storage import MarketDB

# --- status (resultado del job por símbolo) ---
FETCH_STATUS_OK = "ok"
FETCH_STATUS_SKIP = "skip"
FETCH_STATUS_ERROR = "error"

# --- skip_reason (detalle cuando status != ok) ---
SKIP_EMPTY_DATA = "empty_data"
SKIP_CONNECTOR_RETURNED_NONE = "connector_returned_none"
SKIP_FALLBACK_USED = "fallback_used"
SKIP_MAX_RETRIES_EXCEEDED = "max_retries_exceeded"
SKIP_CREDENTIALS_MISSING = "credentials_missing"
SKIP_BUDGET_EXHAUSTED = "budget_exhausted"
SKIP_DATA_ERROR = "data_error"
SKIP_UNEXPECTED_ERROR = "unexpected_error"

# --- source / provider (fuente efectiva de datos) ---
SOURCE_IOL = "iol"
SOURCE_BYMA = "byma"
SOURCE_YFINANCE = "yfinance"
SOURCE_MIXED = "mixed"

VENUE_US = "XNYS"
VENUE_AR = "XBUE"


def apply_source_attribution(
    trace: SymbolFetchTrace,
    rows_by_source: dict[str, int],
    *,
    partial_fallback: bool = False,
) -> None:
    """Registra fuente efectiva y conteos por proveedor en trace.extra (fetch_log)."""
    iol_n = int(rows_by_source.get(SOURCE_IOL, 0))
    byma_n = int(rows_by_source.get(SOURCE_BYMA, 0))
    yfinance_n = int(rows_by_source.get(SOURCE_YFINANCE, 0))

    trace.extra["rows_by_source"] = dict(rows_by_source)
    trace.extra["partial_fallback"] = partial_fallback

    if partial_fallback and iol_n > 0 and byma_n > 0:
        effective = SOURCE_MIXED
    elif byma_n > 0 and iol_n == 0:
        effective = SOURCE_BYMA
    elif iol_n > 0 and byma_n == 0:
        effective = SOURCE_IOL
    elif yfinance_n > 0:
        effective = SOURCE_YFINANCE
    else:
        effective = trace.provider

    trace.extra["effective_source"] = effective
    if effective:
        trace.source = effective
        if trace.provider is None:
            trace.provider = effective


@dataclass
class SymbolFetchTrace:
    """Trazabilidad de un fetch por símbolo y rango de fechas."""

    symbol: str
    venue: str
    start_date: date
    end_date: date
    status: str = FETCH_STATUS_SKIP
    source: str | None = None
    skip_reason: str | None = None
    provider: str | None = None
    iol_only: bool | None = None
    attempts: int = 0
    rows: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_log_entry(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "provider": self.provider,
            "attempts": self.attempts,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "rows": self.rows,
        }
        if self.iol_only is not None:
            payload["iol_only"] = self.iol_only
        payload.update(self.extra)
        return {
            "symbol": self.symbol,
            "venue": self.venue,
            "status": self.status,
            "source": self.source,
            "skip_reason": self.skip_reason,
            "extra": json.dumps(payload, sort_keys=True),
        }


def persist_fetch_trace(db: MarketDB, trace: SymbolFetchTrace) -> None:
    """Única puerta de persistencia desde el pipeline de ingesta."""
    db.log_fetch(trace.to_log_entry())


def trace_us_result(
    symbol: str,
    start_date: date,
    end_date: date,
    rows: list[Any] | None,
    *,
    attempts: int = 3,
) -> SymbolFetchTrace:
    """Construye traza US a partir del resultado del conector yfinance."""
    trace = SymbolFetchTrace(
        symbol=symbol,
        venue=VENUE_US,
        start_date=start_date,
        end_date=end_date,
        provider=SOURCE_YFINANCE,
        iol_only=None,
        attempts=attempts,
    )
    if rows is None:
        trace.status = FETCH_STATUS_SKIP
        trace.skip_reason = SKIP_MAX_RETRIES_EXCEEDED
        trace.source = SOURCE_YFINANCE
        return trace
    if not rows:
        trace.status = FETCH_STATUS_SKIP
        trace.skip_reason = SKIP_EMPTY_DATA
        trace.source = SOURCE_YFINANCE
        return trace
    trace.status = FETCH_STATUS_OK
    trace.source = SOURCE_YFINANCE
    trace.rows = len(rows)
    trace.attempts = attempts if attempts > 0 else 1
    apply_source_attribution(
        trace,
        {SOURCE_YFINANCE: len(rows)},
        partial_fallback=False,
    )
    return trace
