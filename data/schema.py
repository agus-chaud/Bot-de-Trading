"""Immutable value objects for market data rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class OHLCVRow:
    """Single OHLCV bar for one symbol, venue, and trading day."""

    symbol: str
    ts: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    currency: str   # "USD" | "ARS"
    venue: str      # "XNYS" | "XBUE"
    imputed: bool   # True if forward-filled to fill a missing session


@dataclass(frozen=True)
class CorporateActionRow:
    """Normalized corporate action event stored in the local DB."""

    symbol: str
    ts: date
    type: str       # "split" | "dividend"
    factor: float   # split ratio or cash dividend amount per share
