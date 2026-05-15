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


@dataclass(frozen=True)
class UniverseSnapshotRow:
    """One line of the universe snapshot (liquidity selection or overlay)."""

    selection_date: date
    bucket: str  # "merval" | "cedear"
    symbol: str
    rank: int
    metric_value: float | None  # e.g. total volume in window; None for overlay-only rows
    source: str  # dynamic | fallback_last_valid | fallback_static | holding_overlay
    schema_version: int
