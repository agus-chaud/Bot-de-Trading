"""Tests walk-forward KPI OOS + gate (reporting.kpi_walk_forward)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from reporting.kpi_walk_forward import (
    evaluate_kpi_oos_thresholds,
    run_kpi_oos_walk_forward_from_paths,
)
from reporting.kpi_v0 import (
    KpiV0Report,
    build_kpi_v0_report_from_tables,
    load_equity_csv,
    load_trades_csv,
)


def _policy_with_kpi_gate(tmp_path: Path) -> Path:
    base = {
        "schema_version": 1,
        "profile": "moderate",
        "weights": {"short": 0.3, "long": 0.7},
        "geo": {"AR": 0.2, "US": 0.8},
        "short_kill_switch_monthly_dd": -0.08,
        "cadence": {"short": "daily", "long": "monthly"},
        "execution_mode": "semi_auto",
        "symbols": {
            "whitelist_us_file": "config/symbols/whitelist_us.yaml",
            "whitelist_ar_file": "config/symbols/whitelist_ar.yaml",
            "inline_us": [],
            "inline_ar": [],
        },
        "risk": {
            "max_notional_per_ticker_pct": 0.08,
            "max_sector_pct": 0.25,
            "max_daily_loss_short_pct": -0.02,
            "max_daily_loss_long_pct": -0.015,
            "max_daily_loss_total_pct": -0.03,
            "max_monthly_loss_short_pct": -0.08,
            "max_monthly_loss_long_pct": -0.06,
            "max_monthly_loss_total_pct": -0.10,
            "no_trade_first_minutes": 15,
            "no_trade_last_minutes": 15,
            "news_window_enabled": False,
            "halt_on_data_quality": True,
            "adv_min_shares_day": 500000,
            "short_kill_switch_until": "manual_reset",
            "stop_loss": {
                "atr_multiplier": 2.0,
                "atr_lookback": 14,
                "fallback_pct_us": -0.05,
                "fallback_pct_ar": -0.08,
            },
        },
        "long_term_engine": {
            "drift_rebalance_threshold_pp": 2.0,
            "drift_convention": "per_line",
            "rebalance_rule": "first_us_trading_day_of_calendar_month",
            "max_long_rebalance_turnover_pct": None,
            "satellite_markets": ["US"],
            "core_lines": [
                {"symbol": "SPY", "target_weight": 0.55},
                {"symbol": "IWM", "target_weight": 0.30},
            ],
            "satellite_lines": [{"symbol": "QQQ", "target_weight": 0.15}],
            "satellite_limits": {
                "max_satellite_weight_total": 0.20,
                "max_weight_per_satellite_line": 0.15,
                "max_satellite_names": 3,
            },
        },
        "short_term_engine": {
            "momentum_lookback_days": 20,
            "liquidity_percentile_min": 0.6,
            "volatility_20d_max": 0.04,
            "top_k_per_market": 5,
            "risk_budget_trade_pct": 0.005,
            "allow_leverage": False,
        },
        "short_term_pre_gate": {
            "enabled": False,
            "walk_forward": {"oos_trading_days": 12, "step_trading_days": 10, "min_oos_windows": 1},
            "thresholds": {
                "monthly_short_drawdown_floor": -0.25,
                "max_fee_pct_of_initial_per_window": 0.05,
                "max_turnover_annualized": 500.0,
            },
        },
        "kpi_oos_gate": {
            "enabled": True,
            "walk_forward": {
                "burn_in_trading_days": 10,
                "oos_trading_days": 30,
                "step_trading_days": 30,
                "min_oos_windows": 1,
            },
            "aggregate": {"rule": "all"},
            "thresholds": {},
        },
        "markets": {
            "US": {"commission_bps_per_side": 1.0, "slippage_bps": 2.0},
            "AR": {"commission_bps_per_side": 15.0, "slippage_bps": 5.0},
        },
        "logging": {"level": "INFO", "structured": True},
        "data_providers": {"us_ohlcv": "x", "ar_ohlcv": "y"},
        "calendar": {
            "source_of_truth": "config/calendars/trading_days.v1.yaml",
            "us_market_code": "XNYS",
            "ar_market_code": "BYMA",
        },
        "corporate_actions": {
            "us_file": "config/corporate_actions/us_actions.v1.yaml",
            "supported_types": ["split", "dividend"],
        },
        "validation_wf": {"lookback_trading_days": 90},
    }
    p = tmp_path / "policy_gate.yaml"
    p.write_text(yaml.safe_dump(base, sort_keys=False), encoding="utf-8")
    return p


def _write_long_equity(tmp_path: Path, n_days: int) -> Path:
    lines = ["ts,equity_total,equity_short,equity_long,cash,costs_day_short,costs_day_long"]
    d0 = date(2024, 1, 2)
    for i in range(n_days):
        d = d0 + timedelta(days=i)
        lines.append(f"{d.isoformat()},10000,3000,{7000 + i},0,0,0")
    p = tmp_path / "eq.csv"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_walk_forward_gate_disabled_returns_aggregate_ok(tmp_path: Path) -> None:
    pol = _policy_with_kpi_gate(tmp_path)
    doc = yaml.safe_load(pol.read_text(encoding="utf-8"))
    doc["kpi_oos_gate"]["enabled"] = False
    pol2 = tmp_path / "pol2.yaml"
    pol2.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    eq = _write_long_equity(tmp_path, 80)
    tr = tmp_path / "tr.csv"
    tr.write_text(
        "ts,symbol,side,qty,price,motor,fee\n2024-01-15,X,BUY,1,100,long,0\n",
        encoding="utf-8",
    )

    result = run_kpi_oos_walk_forward_from_paths(
        equity_path=eq,
        trades_path=tr,
        policy_path=pol2,
        policy_doc_override=yaml.safe_load(pol2.read_text(encoding="utf-8")),
    )
    assert result.gate_enabled is False
    assert result.aggregate_passed is True
    assert len(result.windows) >= 1
    assert all(w.passed for w in result.windows)


def test_walk_forward_fails_impossible_sharpe_floor(tmp_path: Path) -> None:
    doc = yaml.safe_load(_policy_with_kpi_gate(tmp_path).read_text(encoding="utf-8"))
    doc["kpi_oos_gate"]["thresholds"] = {"min_sharpe_annualized_total": 1e9}
    pol = tmp_path / "pol.yaml"
    pol.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    eq = _write_long_equity(tmp_path, 80)
    tr = tmp_path / "tr.csv"
    tr.write_text("ts,symbol,motor,fee\n2024-01-02,X,short,0\n", encoding="utf-8")

    result = run_kpi_oos_walk_forward_from_paths(
        equity_path=eq,
        trades_path=tr,
        policy_path=pol,
        policy_doc_override=doc,
    )
    assert result.gate_enabled is True
    assert result.aggregate_passed is False
    assert any(not w.passed for w in result.windows)


def test_evaluate_max_drawdown_floor_negative() -> None:
    rep = KpiV0Report(segment_total={"max_drawdown": -0.10})
    ok, viol = evaluate_kpi_oos_thresholds(rep, {"max_drawdown_total_floor": -0.20})
    assert ok and viol == []
    ok2, viol2 = evaluate_kpi_oos_thresholds(rep, {"max_drawdown_total_floor": -0.05})
    assert not ok2


def test_k_of_last_q_aggregate(tmp_path: Path) -> None:
    doc = yaml.safe_load(_policy_with_kpi_gate(tmp_path).read_text(encoding="utf-8"))
    doc["kpi_oos_gate"]["walk_forward"] = {
        "burn_in_trading_days": 5,
        "oos_trading_days": 15,
        "step_trading_days": 20,
        "min_oos_windows": 3,
    }
    doc["kpi_oos_gate"]["aggregate"] = {"rule": "k_of_last_q", "k_pass": 2, "last_q_windows": 3}
    doc["kpi_oos_gate"]["thresholds"] = {}
    pol = tmp_path / "pol.yaml"
    pol.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    eq = _write_long_equity(tmp_path, 70)
    tr = tmp_path / "tr.csv"
    tr.write_text("ts,symbol,motor,fee\n2024-01-02,X,short,0\n", encoding="utf-8")

    result = run_kpi_oos_walk_forward_from_paths(
        equity_path=eq,
        trades_path=tr,
        policy_path=pol,
        policy_doc_override=doc,
    )
    assert len(result.windows) == 3
    assert result.aggregate_passed is True


def test_build_from_tables_matches_segment_keys(tmp_path: Path) -> None:
    eq = _write_long_equity(tmp_path, 260)
    tr = tmp_path / "tr.csv"
    tr.write_text(
        "ts,symbol,side,qty,price,motor,fee\n2024-06-15,X,BUY,1,100,long,0\n",
        encoding="utf-8",
    )
    rows, fn = load_equity_csv(eq)
    tre = load_trades_csv(tr)
    rep = build_kpi_v0_report_from_tables(rows, fn, tre, metadata={}, policy_path=None)
    assert rep.segment_long.get("mdd_12m_rolling_last") is not None
