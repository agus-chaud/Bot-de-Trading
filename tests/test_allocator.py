"""Behavioral tests for compute_allocation (allocator v1)."""

import pytest

from core_sim import (
    AllocationGeo,
    AllocationWeights,
    compute_allocation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _weights(short: float = 0.3) -> AllocationWeights:
    return AllocationWeights(short=short, long=1.0 - short)


def _geo(ar: float = 0.2) -> AllocationGeo:
    return AllocationGeo(AR=ar, US=1.0 - ar)


def _make_position(bucket: str, market: str, market_value: float) -> dict:
    return {"bucket": bucket, "market": market, "market_value": market_value}


# ---------------------------------------------------------------------------
# Test 1: target buckets with no positions
# ---------------------------------------------------------------------------

def test_target_buckets_with_no_positions():
    result = compute_allocation(
        equity_total=100_000.0,
        positions_snapshot={},
        cash=0.0,
        weights=_weights(0.3),
        geo=_geo(0.2),
    )
    assert result.target_by_bucket["short-AR"] == pytest.approx(6_000.0)
    assert result.target_by_bucket["short-US"] == pytest.approx(24_000.0)
    assert result.target_by_bucket["long-AR"] == pytest.approx(14_000.0)
    assert result.target_by_bucket["long-US"] == pytest.approx(56_000.0)


# ---------------------------------------------------------------------------
# Test 2: headroom equals target when no positions
# ---------------------------------------------------------------------------

def test_headroom_equals_target_when_no_positions():
    result = compute_allocation(
        equity_total=100_000.0,
        positions_snapshot={},
        cash=0.0,
        weights=_weights(0.3),
        geo=_geo(0.2),
    )
    for bucket in ("short-AR", "short-US", "long-AR", "long-US"):
        assert result.headroom_by_bucket[bucket] == pytest.approx(
            result.target_by_bucket[bucket]
        ), f"headroom != target for {bucket}"


# ---------------------------------------------------------------------------
# Test 3: headroom is zero when position exceeds target (never negative)
# ---------------------------------------------------------------------------

def test_headroom_zero_when_position_exceeds_target():
    # short-AR target = 100_000 * 0.3 * 0.2 = 6_000
    # position MV = 10_000 → headroom must be 0, not -4_000
    positions = {
        "SPY": _make_position("short", "AR", 10_000.0),
    }
    result = compute_allocation(
        equity_total=100_000.0,
        positions_snapshot=positions,
        cash=0.0,
        weights=_weights(0.3),
        geo=_geo(0.2),
    )
    assert result.headroom_by_bucket["short-AR"] == pytest.approx(0.0)
    assert result.headroom_by_bucket["short-AR"] >= 0.0


# ---------------------------------------------------------------------------
# Test 4: redistribution — AR unused headroom goes to US
# ---------------------------------------------------------------------------

def test_redistribution_ar_unused_to_us():
    # short-US headroom = 24_000 (no positions), pass unused_headroom_short_ar = 5_000
    result = compute_allocation(
        equity_total=100_000.0,
        positions_snapshot={},
        cash=0.0,
        weights=_weights(0.3),
        geo=_geo(0.2),
        unused_headroom_short_ar=5_000.0,
    )
    # redistribution_log must contain exactly one entry for short-AR → short-US
    entries = [e for e in result.redistribution_log if e.from_bucket == "short-AR" and e.to_bucket == "short-US"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry.amount == pytest.approx(5_000.0)
    assert entry.reason == "ar_unused_to_us"
    # short-US headroom increased by 5_000
    assert result.headroom_by_bucket["short-US"] == pytest.approx(24_000.0 + 5_000.0)


# ---------------------------------------------------------------------------
# Test 5: redistribution — US unused headroom goes to AR (symmetric)
# ---------------------------------------------------------------------------

def test_redistribution_bidirectional_us_unused_to_ar():
    # short-AR headroom = 6_000 (no positions), pass unused_headroom_short_us = 3_000
    result = compute_allocation(
        equity_total=100_000.0,
        positions_snapshot={},
        cash=0.0,
        weights=_weights(0.3),
        geo=_geo(0.2),
        unused_headroom_short_us=3_000.0,
    )
    entries = [e for e in result.redistribution_log if e.from_bucket == "short-US" and e.to_bucket == "short-AR"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry.amount == pytest.approx(3_000.0)
    assert entry.reason == "us_unused_to_ar"
    # short-AR headroom increased by 3_000
    assert result.headroom_by_bucket["short-AR"] == pytest.approx(6_000.0 + 3_000.0)


# ---------------------------------------------------------------------------
# Test 6: no cross-horizon redistribution
# ---------------------------------------------------------------------------

def test_no_cross_horizon_redistribution():
    # Fill up short-US fully so headroom["short-US"] == 0
    # short-US target = 100_000 * 0.3 * 0.8 = 24_000
    positions = {
        "QQQ": _make_position("short", "US", 24_000.0),
    }
    result = compute_allocation(
        equity_total=100_000.0,
        positions_snapshot=positions,
        cash=0.0,
        weights=_weights(0.3),
        geo=_geo(0.2),
        unused_headroom_short_ar=5_000.0,  # AR unused, but US headroom is 0
    )
    # short-US headroom is 0 → redistribution must NOT fire
    assert result.headroom_by_bucket["short-US"] == pytest.approx(0.0)
    # redistribution_log must be empty (no valid destination)
    assert len(result.redistribution_log) == 0
    # long buckets unaffected
    assert result.headroom_by_bucket["long-US"] == pytest.approx(56_000.0)


# ---------------------------------------------------------------------------
# Test 7: zero equity returns all zeros
# ---------------------------------------------------------------------------

def test_zero_equity_returns_all_zeros():
    result = compute_allocation(
        equity_total=0.0,
        positions_snapshot={"SPY": _make_position("short", "US", 1_000.0)},
        cash=500.0,
        weights=_weights(0.3),
        geo=_geo(0.2),
    )
    assert result.equity_total == 0.0
    for bucket in ("short-AR", "short-US", "long-AR", "long-US"):
        assert result.target_by_bucket[bucket] == pytest.approx(0.0)
        assert result.current_mv_by_bucket[bucket] == pytest.approx(0.0)
        assert result.headroom_by_bucket[bucket] == pytest.approx(0.0)
    assert result.notional_long_bucket_mtm == pytest.approx(0.0)
    assert result.notional_long_cash == pytest.approx(0.0)
    assert len(result.redistribution_log) == 0


# ---------------------------------------------------------------------------
# Test 8: notional_long_cash equals headroom sum for long buckets
# ---------------------------------------------------------------------------

def test_notional_long_cash_is_headroom_sum():
    result = compute_allocation(
        equity_total=100_000.0,
        positions_snapshot={},
        cash=0.0,
        weights=_weights(0.3),
        geo=_geo(0.2),
    )
    expected = result.headroom_by_bucket["long-AR"] + result.headroom_by_bucket["long-US"]
    assert result.notional_long_cash == pytest.approx(expected)
