"""Behavior tests for venue-aware loading in the signal-IC reader.

The bug these tests lock down: ``ohlcv`` stores the same dual-listed symbol under
XNYS (USD) and XBUE (ARS). The old reader did ``SELECT ... WHERE ts BETWEEN ?``
without a venue filter, so SQLite's last-write-wins blended a USD bar one day with
an ARS bar the next, producing impossible day-to-day returns (KO 74 USD vs
22519 ARS). ``bars_by_date_from_db`` must now read each symbol from ONLY the venue
matching its whitelist market tag.

These are integration tests against a real in-memory ``MarketDB`` (no mocks): they
assert WHAT the reader returns, not how it queries.
"""

from __future__ import annotations

from datetime import date

from data.schema import OHLCVRow
from data.storage import MarketDB
from reporting.signal_ic import bars_by_date_from_db, forward_return


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


# ---------------------------------------------------------------------------
# Core rule: read the venue that matches the symbol's market tag
# ---------------------------------------------------------------------------


def test_us_tagged_symbol_returns_only_usd_bars():
    """A US-tagged dual-listed symbol yields the XNYS (USD) close, never XBUE (ARS)."""
    db = _db()
    d0, d1 = date(2025, 6, 2), date(2025, 6, 3)
    db.upsert_ohlcv([
        _row("KO", d0, 74.0, "USD", "XNYS"),
        _row("KO", d0, 22519.0, "ARS", "XBUE"),
        _row("KO", d1, 76.0, "USD", "XNYS"),
        _row("KO", d1, 22959.0, "ARS", "XBUE"),
    ])

    bars = bars_by_date_from_db(db, d0, d1, {"KO": "US"})

    assert bars[d0]["KO"]["close"] == 74.0
    assert bars[d1]["KO"]["close"] == 76.0


def test_ar_tagged_symbol_returns_only_ars_bars():
    """An AR-tagged symbol yields the XBUE (ARS) close, never a USD venue."""
    db = _db()
    d0, d1 = date(2025, 6, 2), date(2025, 6, 3)
    db.upsert_ohlcv([
        _row("GGAL", d0, 74.0, "USD", "XNYS"),
        _row("GGAL", d0, 22519.0, "ARS", "XBUE"),
        _row("GGAL", d1, 76.0, "USD", "XNYS"),
        _row("GGAL", d1, 22959.0, "ARS", "XBUE"),
    ])

    bars = bars_by_date_from_db(db, d0, d1, {"GGAL": "AR"})

    assert bars[d0]["GGAL"]["close"] == 22519.0
    assert bars[d1]["GGAL"]["close"] == 22959.0


def test_impossible_cross_currency_jump_disappears_after_fix():
    """The headline symptom: a clean US series gives a sane ~+2.7% day-to-day return,
    not the catastrophic USD/ARS blend the old reader produced."""
    db = _db()
    d0, d1 = date(2025, 6, 2), date(2025, 6, 3)
    # USD series: 74 -> 76 (+2.7%). ARS bars present but must be ignored for a US tag.
    db.upsert_ohlcv([
        _row("KO", d0, 74.0, "USD", "XNYS"),
        _row("KO", d0, 22519.0, "ARS", "XBUE"),
        _row("KO", d1, 76.0, "USD", "XNYS"),
        _row("KO", d1, 22959.0, "ARS", "XBUE"),
    ])

    bars = bars_by_date_from_db(db, d0, d1, {"KO": "US"})
    sorted_dates = [d0, d1]
    idx = {d: i for i, d in enumerate(sorted_dates)}
    fwd = forward_return("KO", d0, 1, sorted_dates, idx, bars)

    assert fwd is not None
    # Sane single-day return, nowhere near the 300x blend (76/22519 - 1 ≈ -0.9966).
    assert abs(fwd - (76.0 / 74.0 - 1.0)) < 1e-9
    assert -0.5 < fwd < 0.5


# ---------------------------------------------------------------------------
# Determinism: XNYS preferred over legacy US
# ---------------------------------------------------------------------------


def test_xnys_is_preferred_over_legacy_us_when_both_present():
    """When a US-tagged symbol has both XNYS and legacy US on a day, XNYS is used."""
    db = _db()
    d0 = date(2025, 6, 2)
    db.upsert_ohlcv([
        _row("AAPL", d0, 200.0, "USD", "XNYS"),
        _row("AAPL", d0, 199.0, "USD", "US"),
    ])

    bars = bars_by_date_from_db(db, d0, d0, {"AAPL": "US"})

    assert bars[d0]["AAPL"]["close"] == 200.0


def test_legacy_us_bar_is_used_when_xnys_missing_that_day():
    """A day with only the legacy US venue is kept (not lost), per ADR-030."""
    db = _db()
    d0 = date(2025, 6, 2)
    db.upsert_ohlcv([_row("AAPL", d0, 199.0, "USD", "US")])

    bars = bars_by_date_from_db(db, d0, d0, {"AAPL": "US"})

    assert bars[d0]["AAPL"]["close"] == 199.0


# ---------------------------------------------------------------------------
# Omission, not substitution
# ---------------------------------------------------------------------------


def test_us_symbol_day_with_only_xbue_is_omitted():
    """A US-tagged symbol that only has an XBUE bar that day is absent — not filled
    from the wrong venue."""
    db = _db()
    d0, d1 = date(2025, 6, 2), date(2025, 6, 3)
    db.upsert_ohlcv([
        _row("KO", d0, 74.0, "USD", "XNYS"),
        _row("KO", d1, 22959.0, "ARS", "XBUE"),  # only ARS on d1 for a US tag
    ])

    bars = bars_by_date_from_db(db, d0, d1, {"KO": "US"})

    assert "KO" in bars[d0]
    assert "KO" not in bars.get(d1, {})


def test_symbol_absent_from_whitelist_is_skipped():
    """Without a market tag there is no defined venue, so the symbol is dropped."""
    db = _db()
    d0 = date(2025, 6, 2)
    db.upsert_ohlcv([_row("ZZZ", d0, 10.0, "USD", "XNYS")])

    bars = bars_by_date_from_db(db, d0, d0, {})

    assert bars == {}
