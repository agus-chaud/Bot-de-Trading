"""Behavior tests for the venue-policy single source of truth.

These tests pin the RULE that prevents the USD/ARS mixing bug, not the internal
shape of the helpers:
- a US-tagged symbol is read from XNYS/US (USD), an AR-tagged one from XBUE (ARS);
- when both XNYS and legacy US exist the same day, XNYS wins deterministically;
- a symbol with no bar at its allowed venue that day is omitted, not substituted;
- an unknown market tag is a hard error (it would silently drop the symbol).
"""

from __future__ import annotations

import pytest

from data.venue_policy import pick_venue_bar, venues_for_market


def _bar(close: float) -> dict[str, float]:
    return {"open": close, "high": close, "low": close, "close": close, "volume": 1.0}


# ---------------------------------------------------------------------------
# venues_for_market — the allowed venues per market tag
# ---------------------------------------------------------------------------


def test_us_market_reads_from_xnys_then_legacy_us():
    """US-tagged symbols read USD bars; XNYS is preferred over legacy US."""
    assert venues_for_market("US") == ("XNYS", "US")


def test_ar_market_reads_from_xbue_only():
    """AR-tagged symbols read ARS bars from XBUE — never a USD venue."""
    assert venues_for_market("AR") == ("XBUE",)


def test_market_tag_is_case_insensitive():
    assert venues_for_market("us") == ("XNYS", "US")
    assert venues_for_market("ar") == ("XBUE",)


def test_unknown_market_tag_raises_instead_of_dropping_symbol():
    """An unknown tag must fail loudly — a silent empty result would hide a bug."""
    with pytest.raises(ValueError):
        venues_for_market("EU")


# ---------------------------------------------------------------------------
# pick_venue_bar — collapse a day's venues to the one correct bar
# ---------------------------------------------------------------------------


def test_us_symbol_takes_usd_bar_and_ignores_ars_bar():
    """A US-tagged dual-listed symbol takes the XNYS (USD) bar, never XBUE (ARS)."""
    bars_by_venue = {"XNYS": _bar(74.0), "XBUE": _bar(22519.0)}

    chosen = pick_venue_bar("US", bars_by_venue)

    assert chosen is not None
    assert chosen["close"] == 74.0


def test_ar_symbol_takes_ars_bar_only():
    """An AR-tagged symbol reads XBUE; a stray USD venue is irrelevant to it."""
    bars_by_venue = {"XBUE": _bar(22519.0)}

    chosen = pick_venue_bar("AR", bars_by_venue)

    assert chosen is not None
    assert chosen["close"] == 22519.0


def test_xnys_wins_over_legacy_us_on_same_day():
    """Determinism: with both XNYS and legacy US present, XNYS is chosen."""
    bars_by_venue = {"US": _bar(75.44), "XNYS": _bar(78.41)}

    chosen = pick_venue_bar("US", bars_by_venue)

    assert chosen is not None
    assert chosen["close"] == 78.41


def test_legacy_us_is_used_when_xnys_absent_that_day():
    """Legacy US is accepted (ADR-030) so days with only the old venue aren't lost."""
    bars_by_venue = {"US": _bar(75.44)}

    chosen = pick_venue_bar("US", bars_by_venue)

    assert chosen is not None
    assert chosen["close"] == 75.44


def test_us_symbol_with_only_xbue_bar_is_omitted_not_substituted():
    """A US-tagged symbol that only has an ARS bar that day yields None (omitted)."""
    bars_by_venue = {"XBUE": _bar(22519.0)}

    assert pick_venue_bar("US", bars_by_venue) is None


def test_ar_symbol_with_only_usd_bar_is_omitted_not_substituted():
    """An AR-tagged symbol that only has a USD bar that day yields None (omitted)."""
    bars_by_venue = {"XNYS": _bar(74.0)}

    assert pick_venue_bar("AR", bars_by_venue) is None
