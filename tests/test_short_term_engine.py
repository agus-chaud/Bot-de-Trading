"""Unit tests for short-term engine deterministic helpers."""

import pytest

from core_sim import (
    RiskCaps,
    ShortEngineConfig,
    build_orders_intent,
    compute_rsi,
    compute_signal_candidates,
    rank_top_k_by_market,
)


def test_compute_signal_candidates_filters_and_scores():
    config = ShortEngineConfig(
        momentum_lookback_days=20,
        liquidity_percentile_min=0.6,
        volatility_20d_max=0.04,
        top_k_per_market=2,
        risk_budget_trade_pct=0.005,
    )
    market_snapshot = [
        {
            "symbol": "SPY",
            "market": "US",
            "close": 110.0,
            "close_n_days_ago": 100.0,
            "volume_percentile": 0.8,
            "vol_20d": 0.02,
            "rsi": 55.0,
            "session_valid": True,
        },
        {
            "symbol": "QQQ",
            "market": "US",
            "close": 100.0,
            "close_n_days_ago": 110.0,
            "volume_percentile": 0.9,
            "vol_20d": 0.02,
            "rsi": 50.0,
            "session_valid": True,
        },
        {
            "symbol": "IWM",
            "market": "US",
            "close": 200.0,
            "close_n_days_ago": 190.0,
            "volume_percentile": 0.3,
            "vol_20d": 0.02,
            "rsi": 45.0,
            "session_valid": True,
        },
    ]

    candidates, skipped = compute_signal_candidates(market_snapshot=market_snapshot, config=config)

    assert len(candidates) == 1
    assert candidates[0]["symbol"] == "SPY"
    assert candidates[0]["signal_score"] == pytest.approx(0.1)
    assert {item["symbol"] for item in skipped} == {"QQQ", "IWM"}


def test_compute_rsi_on_uptrend_returns_high_value():
    closes = [100 + i for i in range(20)]
    rsi = compute_rsi(closes, lookback=14)
    assert rsi is not None
    assert rsi > 70.0


def test_compute_signal_candidates_skips_when_rsi_overbought():
    config = ShortEngineConfig(
        momentum_lookback_days=20,
        liquidity_percentile_min=0.6,
        volatility_20d_max=0.04,
        top_k_per_market=2,
        risk_budget_trade_pct=0.005,
        rsi_lookback=14,
        rsi_overbought_entry=80.0,
        rsi_exit_threshold=45.0,
    )
    market_snapshot = [
        {
            "symbol": "SPY",
            "market": "US",
            "close": 110.0,
            "close_n_days_ago": 100.0,
            "volume_percentile": 0.8,
            "vol_20d": 0.02,
            "rsi": 85.0,
            "session_valid": True,
        }
    ]
    candidates, skipped = compute_signal_candidates(market_snapshot=market_snapshot, config=config)
    assert candidates == []
    assert len(skipped) == 1
    assert skipped[0]["symbol"] == "SPY"
    assert skipped[0]["reason"] == "rsi_overbought"
    assert skipped[0]["drivers"]["skip_reason"] == "rsi_overbought"
    assert "rsi_overbought" in skipped[0]["drivers"]["failed_filters"]
    assert "liquidity" in skipped[0]["drivers"]["passed_filters"]
    assert "volatility" in skipped[0]["drivers"]["passed_filters"]


def test_rank_top_k_by_market_keeps_best_per_market():
    candidates = [
        {"symbol": "SPY", "market": "US", "signal_score": 0.09},
        {"symbol": "QQQ", "market": "US", "signal_score": 0.12},
        {"symbol": "IWM", "market": "US", "signal_score": 0.03},
        {"symbol": "GGAL", "market": "AR", "signal_score": 0.11},
        {"symbol": "YPF", "market": "AR", "signal_score": 0.10},
    ]

    selected = rank_top_k_by_market(candidates=candidates, top_k_per_market=1)

    assert len(selected) == 2
    assert {item["symbol"] for item in selected} == {"QQQ", "GGAL"}


def test_build_orders_intent_rounds_by_lot_and_returns_snapshot():
    selected = [
        {
            "symbol": "SPY",
            "market": "US",
            "close": 500.0,
            "vol_20d": 0.02,
            "signal_score": 0.15,
            "sector": "ETF",
        }
    ]
    risk_caps = RiskCaps(max_position_pct=0.08, max_sector_pct=0.25)

    intents, skipped, metrics = build_orders_intent(
        selected_candidates=selected,
        short_equity=100_000,
        short_cash=30_000,
        risk_budget_trade_pct=0.005,
        risk_caps=risk_caps,
        current_symbol_notional={"SPY": 0.0},
        current_sector_exposure_pct={"ETF": 0.05},
        lot_size_by_market={"US": 1},
    )

    assert skipped == []
    assert metrics["intents_generated"] == 1
    assert intents[0]["symbol"] == "SPY"
    assert intents[0]["qty"] == 16.0
    assert intents[0]["intent_notional"] == 8_000.0
    assert intents[0]["risk_snapshot"]["max_position_pct"] == 0.08
    assert intents[0]["drivers"]["final_notional"] == pytest.approx(8_000.0)
    assert intents[0]["drivers"]["rounded_qty"] == pytest.approx(16.0)


def test_build_orders_intent_reports_skip_reason_on_missing_headroom():
    selected = [
        {
            "symbol": "SPY",
            "market": "US",
            "close": 500.0,
            "vol_20d": 0.02,
            "signal_score": 0.15,
            "sector": "ETF",
        }
    ]

    intents, skipped, metrics = build_orders_intent(
        selected_candidates=selected,
        short_equity=100_000,
        short_cash=30_000,
        risk_budget_trade_pct=0.005,
        risk_caps=RiskCaps(max_position_pct=0.08, max_sector_pct=0.25),
        current_symbol_notional={"SPY": 8_000.0},
        current_sector_exposure_pct={"ETF": 0.25},
    )

    assert intents == []
    assert skipped[0]["symbol"] == "SPY"
    assert skipped[0]["reason"] == "no_risk_headroom"
    assert skipped[0]["drivers"]["skip_reason"] == "no_risk_headroom"
    assert skipped[0]["drivers"]["symbol_headroom"] == pytest.approx(0.0)
    assert metrics["symbols_skipped_after_sizing"] == 1


def test_build_orders_intent_clips_to_geo_and_tranche_headroom():
    """30/70 + 20/80: notional adicional queda acotado por tranche y por mercado."""
    selected = [
        {
            "symbol": "SPY",
            "market": "US",
            "close": 100.0,
            "vol_20d": 0.02,
            "signal_score": 0.1,
            "sector": "ETF",
        }
    ]
    intents, _, _ = build_orders_intent(
        selected_candidates=selected,
        short_equity=100_000.0,
        short_cash=50_000.0,
        risk_budget_trade_pct=0.02,
        risk_caps=RiskCaps(max_position_pct=0.5, max_sector_pct=0.5),
        short_tranche_headroom=1_000.0,
        geo_headroom={"US": 500.0, "AR": 0.0},
    )
    assert len(intents) == 1
    assert intents[0]["intent_notional"] == pytest.approx(500.0)  # cap geo US, antes del tranche 1000
    assert intents[0]["qty"] == 5.0


def test_build_orders_intent_blocks_when_kill_switch_active():
    intents, skipped, metrics = build_orders_intent(
        selected_candidates=[
            {
                "symbol": "QQQ",
                "market": "US",
                "close": 300.0,
                "vol_20d": 0.02,
                "signal_score": 0.11,
            }
        ],
        short_equity=100_000,
        short_cash=20_000,
        risk_budget_trade_pct=0.005,
        risk_caps=RiskCaps(max_position_pct=0.08, max_sector_pct=0.25),
        kill_switch_active=True,
    )

    assert intents == []
    assert skipped[0]["symbol"] == "*"
    assert skipped[0]["reason"] == "short_kill_switch_active"
    assert skipped[0]["drivers"]["skip_reason"] == "short_kill_switch_active"
    assert metrics["intents_generated"] == 0


def test_compute_signal_candidates_passing_candidate_includes_feature_drivers():
    config = ShortEngineConfig(
        momentum_lookback_days=20,
        liquidity_percentile_min=0.6,
        volatility_20d_max=0.04,
        top_k_per_market=2,
        risk_budget_trade_pct=0.005,
    )
    market_snapshot = [
        {
            "symbol": "SPY",
            "market": "US",
            "close": 110.0,
            "close_n_days_ago": 100.0,
            "volume_percentile": 0.8,
            "vol_20d": 0.02,
            "rsi": 55.0,
            "session_valid": True,
        }
    ]

    candidates, skipped = compute_signal_candidates(market_snapshot=market_snapshot, config=config)

    assert skipped == []
    assert len(candidates) == 1
    drivers = candidates[0]["drivers"]
    assert drivers["momentum_20d"] == pytest.approx(0.1)
    assert drivers["rsi"] == pytest.approx(55.0)
    assert drivers["volume_percentile"] == pytest.approx(0.8)
    assert drivers["vol_20d"] == pytest.approx(0.02)
    assert "liquidity" in drivers["passed_filters"]
    assert "volatility" in drivers["passed_filters"]
    assert "rsi" in drivers["passed_filters"]
    assert "momentum" in drivers["passed_filters"]
    assert drivers["failed_filters"] == []
    assert "skip_reason" not in drivers


def test_compute_signal_candidates_skipped_symbol_includes_stable_reason_codes():
    config = ShortEngineConfig(
        momentum_lookback_days=20,
        liquidity_percentile_min=0.6,
        volatility_20d_max=0.04,
        top_k_per_market=2,
        risk_budget_trade_pct=0.005,
    )
    market_snapshot = [
        {
            "symbol": "IWM",
            "market": "US",
            "close": 200.0,
            "close_n_days_ago": 190.0,
            "volume_percentile": 0.3,
            "vol_20d": 0.02,
            "rsi": 45.0,
            "session_valid": True,
        }
    ]

    candidates, skipped = compute_signal_candidates(market_snapshot=market_snapshot, config=config)

    assert candidates == []
    assert skipped[0]["reason"] == "liquidity_below_threshold"
    assert skipped[0]["drivers"]["skip_reason"] == "liquidity_below_threshold"
    assert skipped[0]["drivers"]["momentum_20d"] == pytest.approx((200.0 / 190.0) - 1.0)
    assert "liquidity_below_threshold" in skipped[0]["drivers"]["failed_filters"]
    assert isinstance(skipped[0]["reason"], str)


def test_build_orders_intent_success_includes_sizing_drivers():
    selected = [
        {
            "symbol": "SPY",
            "market": "US",
            "close": 100.0,
            "vol_20d": 0.02,
            "signal_score": 0.1,
            "sector": "ETF",
        }
    ]

    intents, skipped, _ = build_orders_intent(
        selected_candidates=selected,
        short_equity=100_000.0,
        short_cash=50_000.0,
        risk_budget_trade_pct=0.02,
        risk_caps=RiskCaps(max_position_pct=0.5, max_sector_pct=0.5),
        short_tranche_headroom=1_000.0,
        geo_headroom={"US": 500.0, "AR": 0.0},
    )

    assert skipped == []
    drivers = intents[0]["drivers"]
    assert drivers["geo_headroom"] == pytest.approx(500.0)
    assert drivers["tranche_headroom"] == pytest.approx(1_000.0)
    assert drivers["final_notional"] == pytest.approx(500.0)
    assert "skip_reason" not in drivers
