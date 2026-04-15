"""Behavior-focused tests for deterministic fill costs."""

import pytest

from core_sim import CostModel, MarketCostConfig, SlippageMode


def test_should_compute_total_cost_with_fixed_bps_and_spread():
    model = CostModel(
        market_configs={
            "US": MarketCostConfig(
                commission_bps_per_side=1.0,
                slippage_mode=SlippageMode.FIXED_BPS,
                slippage_bps=2.0,
                min_spread_bps=0.5,
            )
        }
    )

    breakdown = model.compute_fill_cost(
        market="US",
        side="BUY",
        qty=100,
        price=50.0,
    )

    assert breakdown.notional == pytest.approx(5_000.0)
    assert breakdown.commission == pytest.approx(0.5)
    assert breakdown.slippage == pytest.approx(1.0)
    assert breakdown.spread == pytest.approx(0.25)
    assert breakdown.total == pytest.approx(1.75)


def test_should_apply_market_specific_configs():
    model = CostModel(
        market_configs={
            "US": MarketCostConfig(commission_bps_per_side=1.0, slippage_bps=2.0),
            "AR": MarketCostConfig(commission_bps_per_side=15.0, slippage_bps=5.0),
        }
    )

    us_cost = model.compute_fill_cost(market="US", side="BUY", qty=100, price=20.0)
    ar_cost = model.compute_fill_cost(market="AR", side="BUY", qty=100, price=20.0)

    assert ar_cost.total > us_cost.total


def test_should_compute_adv_linear_slippage_when_adv_is_available():
    model = CostModel(
        market_configs={
            "US": MarketCostConfig(
                commission_bps_per_side=1.0,
                slippage_mode=SlippageMode.ADV_LINEAR,
                adv_slope_bps=200.0,
            )
        }
    )

    breakdown = model.compute_fill_cost(
        market="US",
        side="BUY",
        qty=2_000,
        price=10.0,
        adv=100_000,
    )

    # participation = 2%; slippage_bps = 200 * 0.02 = 4 bps
    assert breakdown.slippage == pytest.approx(8.0)


def test_should_fallback_to_zero_adv_slippage_when_adv_is_missing():
    model = CostModel(
        market_configs={
            "US": MarketCostConfig(
                commission_bps_per_side=1.0,
                slippage_mode=SlippageMode.ADV_LINEAR,
                adv_slope_bps=120.0,
            )
        }
    )

    breakdown = model.compute_fill_cost(
        market="US",
        side="SELL",
        qty=1_000,
        price=25.0,
        adv=None,
    )

    assert breakdown.slippage == pytest.approx(0.0)


def test_should_reject_invalid_inputs_and_unknown_markets():
    model = CostModel(market_configs={"US": MarketCostConfig(commission_bps_per_side=1.0)})

    with pytest.raises(ValueError, match="qty must be > 0"):
        model.compute_fill_cost(market="US", side="BUY", qty=0, price=10.0)

    with pytest.raises(ValueError, match="price must be > 0"):
        model.compute_fill_cost(market="US", side="BUY", qty=1, price=0.0)

    with pytest.raises(ValueError, match="Unknown market: AR"):
        model.compute_fill_cost(market="AR", side="BUY", qty=1, price=10.0)
