"""Single source of truth for which venue a symbol's bars must be read from.

Background (the bug this prevents)
----------------------------------
``market.db`` stores the SAME symbol under several venues with DIFFERENT
currencies: US prices live at ``XNYS`` (and legacy ``US`` from ADR-030, both USD)
while the Buenos Aires listing lives at ``XBUE`` (ARS pesos). 13 symbols are
dual-listed. Readers that did ``SELECT ... FROM ohlcv WHERE ts BETWEEN ?`` WITHOUT
filtering venue let SQLite's last-write-wins mix a USD bar one day with an ARS bar
the next, producing impossible day-to-day returns (e.g. KO "+30000%" =
22519 ARS / 74 USD).

Architecture decision (do NOT mix venues within a series)
---------------------------------------------------------
Signal/analysis is computed on the US price (XNYS, USD) for dual-listed names, so
the exchange rate (CCL) cannot contaminate momentum. AR-native symbols (Merval,
no US listing) use ARS (XBUE). Nothing is re-tagged: the whitelist already tags
each symbol with its market (``US`` or ``AR``) via ``load_merged_whitelist`` and
those tags are authoritative.

The rule
--------
- market ``US`` -> bars come from ``XNYS`` or legacy ``US`` (both USD). ``XNYS`` is
  preferred deterministically when both exist on the same day.
- market ``AR`` -> bars come from ``XBUE`` (ARS).

HARD RULE: never blend venues inside one symbol's series. If a US-tagged symbol has
no XNYS/US bar on a given day it is omitted that day (and likewise for AR/XBUE) —
it is NEVER back-filled from the other venue.

TODO(cedear-execution): mapping US->CEDEAR and the peso execution layer is a future
step. This module only governs how the SIGNAL/measurement layer reads prices.
"""

from __future__ import annotations

# Venues that carry USD bars for a US-tagged symbol. Ordered by preference:
# XNYS is the canonical exchange; ``US`` is legacy from the ADR-030 migration and
# is accepted only so historical days are not lost.
_US_VENUES: tuple[str, ...] = ("XNYS", "US")
# Venue that carries ARS bars for an AR-tagged symbol.
_AR_VENUES: tuple[str, ...] = ("XBUE",)


def venues_for_market(market: str) -> tuple[str, ...]:
    """Return the allowed venues for a market tag, in preference order.

    ``US`` -> ``("XNYS", "US")`` (both USD; XNYS preferred over legacy US).
    ``AR`` -> ``("XBUE",)`` (ARS).

    The match is case-insensitive on the market tag. An unknown market raises
    ``ValueError`` rather than silently returning an empty tuple — a silent empty
    result would drop the symbol entirely and hide a tagging bug.
    """
    m = str(market).strip().upper()
    if m == "US":
        return _US_VENUES
    if m == "AR":
        return _AR_VENUES
    raise ValueError(f"unknown market tag: {market!r} (expected 'US' or 'AR')")


def pick_venue_bar(
    market: str,
    bars_by_venue: dict[str, dict[str, float]],
) -> dict[str, float] | None:
    """Pick the single correct bar for a symbol-day given its market tag.

    ``bars_by_venue`` maps ``venue -> bar`` for ONE symbol on ONE day. Returns the
    bar from the highest-preference allowed venue present, or ``None`` if no allowed
    venue has a bar that day (the symbol is then omitted for that day — never
    substituted from a disallowed venue).

    Determinism: when both ``XNYS`` and legacy ``US`` exist on the same day for a
    US-tagged symbol, ``XNYS`` always wins because it comes first in
    :func:`venues_for_market`.
    """
    for venue in venues_for_market(market):
        bar = bars_by_venue.get(venue)
        if bar is not None:
            return bar
    return None
