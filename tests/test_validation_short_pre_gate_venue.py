"""Behavior tests for venue-aware loading in the validation short-pre-gate reader.

``validation.stages.short_pre_gate._bars_from_db`` used to iterate over every
``(symbol, venue)`` pair present in ``ohlcv``, so a US-tagged dual-listed symbol
picked up its XBUE (ARS) bars too and blended currencies. It must now read each
symbol from ONLY the venue matching its whitelist market tag.

Integration tests against a real in-memory ``MarketDB`` (no mocks).
"""

from __future__ import annotations

from datetime import date

from data.schema import OHLCVRow
from data.storage import MarketDB
from validation.stages.short_pre_gate import _bars_from_db


def _row(symbol: str, ts: date, close: float, currency: str, venue: str) -> OHLCVRow:
    return OHLCVRow(
        symbol=symbol,
        ts=ts,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000_000.0,
        currency=currency,
        venue=venue,
        imputed=False,
    )


def _db() -> MarketDB:
    return MarketDB(":memory:")


def test_us_tagged_symbol_loads_only_usd_bars():
    db = _db()
    d0, d1 = date(2025, 6, 2), date(2025, 6, 3)
    db.upsert_ohlcv([
        _row("KO", d0, 74.0, "USD", "XNYS"),
        _row("KO", d0, 22519.0, "ARS", "XBUE"),
        _row("KO", d1, 76.0, "USD", "XNYS"),
        _row("KO", d1, 22959.0, "ARS", "XBUE"),
    ])

    bars = _bars_from_db(db, [d0, d1], {"KO": "US"})

    assert bars[d0]["KO"]["close"] == 74.0
    assert bars[d1]["KO"]["close"] == 76.0


def test_ar_tagged_symbol_loads_only_ars_bars():
    db = _db()
    d0 = date(2025, 6, 2)
    db.upsert_ohlcv([
        _row("YPFD", d0, 70.0, "USD", "XNYS"),  # stray USD venue, must be ignored
        _row("YPFD", d0, 31000.0, "ARS", "XBUE"),
    ])

    bars = _bars_from_db(db, [d0], {"YPFD": "AR"})

    assert bars[d0]["YPFD"]["close"] == 31000.0


def test_xnys_preferred_over_legacy_us():
    db = _db()
    d0 = date(2025, 6, 2)
    db.upsert_ohlcv([
        _row("AAPL", d0, 200.0, "USD", "XNYS"),
        _row("AAPL", d0, 199.0, "USD", "US"),
    ])

    bars = _bars_from_db(db, [d0], {"AAPL": "US"})

    assert bars[d0]["AAPL"]["close"] == 200.0


def test_us_symbol_day_with_only_xbue_is_omitted():
    db = _db()
    d0, d1 = date(2025, 6, 2), date(2025, 6, 3)
    db.upsert_ohlcv([
        _row("KO", d0, 74.0, "USD", "XNYS"),
        _row("KO", d1, 22959.0, "ARS", "XBUE"),
    ])

    bars = _bars_from_db(db, [d0, d1], {"KO": "US"})

    assert "KO" in bars[d0]
    assert "KO" not in bars.get(d1, {})
