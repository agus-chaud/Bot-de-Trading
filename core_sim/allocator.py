"""Allocator v1 — pure allocation function. No side effects.

Computes target capital by bucket (short-AR, short-US, long-AR, long-US),
headroom over current positions, optional intra-horizon redistribution,
and derived notional metrics for the long sleeve.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

BUCKETS = ("short-AR", "short-US", "long-AR", "long-US")


@dataclass(frozen=True)
class AllocationWeights:
    short: float
    long: float


@dataclass(frozen=True)
class AllocationGeo:
    AR: float
    US: float


@dataclass(frozen=True)
class RedistributionEntry:
    from_bucket: str
    to_bucket: str
    amount: float
    reason: str


@dataclass(frozen=True)
class AllocatorResult:
    equity_total: float
    target_by_bucket: dict[str, float]
    current_mv_by_bucket: dict[str, float]
    headroom_by_bucket: dict[str, float]
    notional_long_bucket_mtm: float
    notional_long_cash: float
    redistribution_log: tuple[RedistributionEntry, ...]


def compute_allocation(
    *,
    equity_total: float,
    positions_snapshot: Mapping[str, Mapping[str, object]],
    cash: float,
    weights: AllocationWeights,
    geo: AllocationGeo,
    unused_headroom_short_ar: float = 0.0,
    unused_headroom_short_us: float = 0.0,
    unused_headroom_long_ar: float = 0.0,
    unused_headroom_long_us: float = 0.0,
) -> AllocatorResult:
    """Compute capital allocation by bucket.

    Parameters
    ----------
    equity_total:
        Total portfolio equity (NAV).
    positions_snapshot:
        Mapping of symbol -> position dict. Each position must have
        ``"bucket"`` (``"short"`` or ``"long"``), ``"market"``
        (``"AR"`` or ``"US"``), and ``"market_value"``.
    cash:
        Undeployed cash in the portfolio.
    weights:
        Fraction of equity assigned to short and long horizons.
    geo:
        Fraction of equity assigned to AR and US markets.
    unused_headroom_short_ar:
        Headroom that went unused in the short-AR bucket (for redistribution).
    unused_headroom_short_us:
        Headroom that went unused in the short-US bucket (for redistribution).
    unused_headroom_long_ar:
        Headroom that went unused in the long-AR bucket (for redistribution).
    unused_headroom_long_us:
        Headroom that went unused in the long-US bucket (for redistribution).
    """
    # Step 1: guard against non-positive equity
    _zero_buckets: dict[str, float] = {b: 0.0 for b in BUCKETS}
    if equity_total <= 0:
        return AllocatorResult(
            equity_total=equity_total,
            target_by_bucket=dict(_zero_buckets),
            current_mv_by_bucket=dict(_zero_buckets),
            headroom_by_bucket=dict(_zero_buckets),
            notional_long_bucket_mtm=0.0,
            notional_long_cash=0.0,
            redistribution_log=(),
        )

    # Step 2: current market value by bucket
    current_mv_by_bucket: dict[str, float] = {b: 0.0 for b in BUCKETS}
    for pos in positions_snapshot.values():
        bucket = str(pos.get("bucket", "")).strip().lower()
        market = str(pos.get("market", "")).strip().upper()
        key = f"{bucket}-{market}"
        if key in current_mv_by_bucket:
            mv = float(pos.get("market_value", 0.0))  # type: ignore[arg-type]
            current_mv_by_bucket[key] += mv

    # Step 3: target by bucket
    target_by_bucket: dict[str, float] = {
        "short-AR": equity_total * weights.short * geo.AR,
        "short-US": equity_total * weights.short * geo.US,
        "long-AR": equity_total * weights.long * geo.AR,
        "long-US": equity_total * weights.long * geo.US,
    }

    # Step 4: headroom (mutable for redistribution, clipped to 0)
    headroom: dict[str, float] = {
        k: max(0.0, target_by_bucket[k] - current_mv_by_bucket[k]) for k in BUCKETS
    }

    # Step 5: bidirectional intra-horizon redistribution
    redistrib: list[RedistributionEntry] = []

    # short horizon: AR unused → US headroom
    if unused_headroom_short_ar > 0 and headroom["short-US"] > 0:
        amount = min(unused_headroom_short_ar, headroom["short-US"])
        headroom["short-US"] += amount
        headroom["short-AR"] = max(0.0, headroom["short-AR"] - amount)
        redistrib.append(
            RedistributionEntry("short-AR", "short-US", amount, "ar_unused_to_us")
        )

    # short horizon: US unused → AR headroom
    if unused_headroom_short_us > 0 and headroom["short-AR"] > 0:
        amount = min(unused_headroom_short_us, headroom["short-AR"])
        headroom["short-AR"] += amount
        headroom["short-US"] = max(0.0, headroom["short-US"] - amount)
        redistrib.append(
            RedistributionEntry("short-US", "short-AR", amount, "us_unused_to_ar")
        )

    # long horizon: AR unused → US headroom
    if unused_headroom_long_ar > 0 and headroom["long-US"] > 0:
        amount = min(unused_headroom_long_ar, headroom["long-US"])
        headroom["long-US"] += amount
        headroom["long-AR"] = max(0.0, headroom["long-AR"] - amount)
        redistrib.append(
            RedistributionEntry("long-AR", "long-US", amount, "ar_unused_to_us")
        )

    # long horizon: US unused → AR headroom
    if unused_headroom_long_us > 0 and headroom["long-AR"] > 0:
        amount = min(unused_headroom_long_us, headroom["long-AR"])
        headroom["long-AR"] += amount
        headroom["long-US"] = max(0.0, headroom["long-US"] - amount)
        redistrib.append(
            RedistributionEntry("long-US", "long-AR", amount, "us_unused_to_ar")
        )

    # Step 6: notional_long_bucket_mtm
    short_headroom_consumed = headroom["short-AR"] + headroom["short-US"]
    notional_long_bucket_mtm = (
        current_mv_by_bucket["long-AR"]
        + current_mv_by_bucket["long-US"]
        + max(0.0, cash - short_headroom_consumed)
    )

    # Step 7: notional_long_cash
    notional_long_cash = headroom["long-AR"] + headroom["long-US"]

    # Step 8: return frozen result
    return AllocatorResult(
        equity_total=equity_total,
        target_by_bucket=target_by_bucket,
        current_mv_by_bucket=dict(current_mv_by_bucket),
        headroom_by_bucket=dict(headroom),
        notional_long_bucket_mtm=notional_long_bucket_mtm,
        notional_long_cash=notional_long_cash,
        redistribution_log=tuple(redistrib),
    )
