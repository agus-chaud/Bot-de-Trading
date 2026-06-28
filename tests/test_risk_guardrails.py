"""Unit tests for risk_guardrails pure functions."""

from __future__ import annotations

import json
import logging

import pytest

from core_sim.risk_guardrails import GuardrailResult, check_long_risk, check_short_risk, check_stop_loss, compute_atr, log_risk_cycle


# ---------------------------------------------------------------------------
# Shared config fixtures
# ---------------------------------------------------------------------------

_SHORT_CONFIG = {
    "kill_dd": -0.08,
    "max_daily_short": -0.02,
    "no_trade_first": 15,
    "no_trade_last": 15,
}

_LONG_CONFIG = {"max_daily_long": -0.015}


# ---------------------------------------------------------------------------
# check_short_risk
# ---------------------------------------------------------------------------


def test_check_short_risk_kill_switch():
    sb = {"monthly_drawdown": -0.09, "daily_return": 0.0}
    flags = {"halt_on_data_quality": True, "data_quality_ok": True}
    result = check_short_risk(sb, flags, _SHORT_CONFIG, None)
    assert not result.allowed
    assert result.reason == "short_monthly_kill_switch"


def test_check_short_risk_daily_loss():
    sb = {"monthly_drawdown": -0.01, "daily_return": -0.03}
    flags = {"halt_on_data_quality": True, "data_quality_ok": True}
    result = check_short_risk(sb, flags, _SHORT_CONFIG, None)
    assert not result.allowed
    assert result.reason == "short_daily_loss_limit"


def test_check_short_risk_no_trade_window():
    # 5 minutes from open is within the 15-minute no-trade window
    sb = {"monthly_drawdown": -0.01, "daily_return": 0.0}
    flags = {"halt_on_data_quality": True, "data_quality_ok": True}
    result = check_short_risk(sb, flags, _SHORT_CONFIG, 5)
    assert not result.allowed
    assert result.reason == "no_trade_window"


def test_check_short_risk_data_quality():
    sb = {"monthly_drawdown": -0.01, "daily_return": 0.0}
    flags = {"halt_on_data_quality": True, "data_quality_ok": False}
    result = check_short_risk(sb, flags, _SHORT_CONFIG, None)
    assert not result.allowed
    assert result.reason == "halt_data_quality"


def test_check_short_risk_ok():
    sb = {"monthly_drawdown": -0.01, "daily_return": -0.005}
    flags = {"halt_on_data_quality": True, "data_quality_ok": True}
    # 100 minutes from open — well past the 15-min window and before last 15
    result = check_short_risk(sb, flags, _SHORT_CONFIG, 100)
    assert result.allowed
    assert result.reason == "ok"


def test_check_short_risk_fail_fast_data_quality_before_no_trade():
    """data_quality check must fire before no_trade_window."""
    sb = {"monthly_drawdown": -0.01, "daily_return": 0.0}
    flags = {"halt_on_data_quality": True, "data_quality_ok": False}
    # session_minutes_from_open=5 would also trigger no_trade_window
    result = check_short_risk(sb, flags, _SHORT_CONFIG, 5)
    assert result.reason == "halt_data_quality"


# ---------------------------------------------------------------------------
# check_long_risk
# ---------------------------------------------------------------------------


def test_check_long_risk_loss_limit():
    sb = {"long_daily_return": -0.02}
    result = check_long_risk(sb, _LONG_CONFIG)
    assert not result.allowed
    assert result.reason == "long_daily_loss_limit"


def test_check_long_risk_ok():
    sb = {"long_daily_return": -0.01}
    result = check_long_risk(sb, _LONG_CONFIG)
    assert result.allowed
    assert result.reason == "ok"


def test_check_long_risk_missing_key_defaults_to_zero():
    """If long_daily_return is absent from sb, default 0.0 must not trip the limit."""
    sb: dict = {}
    result = check_long_risk(sb, _LONG_CONFIG)
    assert result.allowed
    assert result.reason == "ok"


def test_check_long_risk_blocks_on_real_breach():
    """check_long_risk must block when long_daily_return breaches the real threshold,
    not just pass because the key is absent and defaults to 0.0.

    This test validates the contract: callers must provide long_daily_return explicitly.
    """
    sb = {"long_daily_return": -0.016}
    result = check_long_risk(sb, _LONG_CONFIG)
    assert not result.allowed
    assert result.reason == "long_daily_loss_limit"
    assert result.meta["long_daily_return"] == pytest.approx(-0.016)
    assert result.meta["limit"] == pytest.approx(-0.015)


def test_check_long_risk_at_exact_threshold_allows():
    """Boundary: exactly at -0.015 must allow (uses strict <, consistent with short)."""
    sb = {"long_daily_return": -0.015}
    result = check_long_risk(sb, _LONG_CONFIG)
    assert result.allowed
    assert result.reason == "ok"


def test_check_long_risk_just_below_threshold_blocks():
    """One tick below threshold must block."""
    sb = {"long_daily_return": -0.0151}
    result = check_long_risk(sb, _LONG_CONFIG)
    assert not result.allowed
    assert result.reason == "long_daily_loss_limit"


# ---------------------------------------------------------------------------
# log_risk_cycle
# ---------------------------------------------------------------------------


def test_log_risk_cycle_emits_json(caplog: pytest.LogCaptureFixture):
    guardrail = GuardrailResult(allowed=True, reason="ok", meta={"monthly_drawdown": -0.01})
    with caplog.at_level(logging.INFO, logger="risk_guardrails"):
        log_risk_cycle(
            engine="short",
            date="2026-04-15",
            guardrail=guardrail,
            orders_proposed=3,
            orders_filled=0,
            kill_switch_active=False,
        )

    assert len(caplog.records) == 1
    record = caplog.records[0]
    payload = json.loads(record.message)

    assert payload["engine"] == "short"
    assert payload["date"] == "2026-04-15"
    assert payload["guardrail_reason"] == "ok"
    assert payload["allowed"] is True
    assert payload["orders_proposed"] == 3
    assert payload["orders_filled"] == 0
    assert payload["kill_switch_active"] is False
    assert "ts" in payload


# ---------------------------------------------------------------------------
# compute_atr
# ---------------------------------------------------------------------------


def _synthetic_bars(n: int, base_close: float = 100.0) -> tuple[list[float], list[float], list[float]]:
    """Generate n bars with predictable OHLC values for deterministic ATR tests."""
    highs = [base_close + 2.0] * n
    lows = [base_close - 2.0] * n
    closes = [base_close] * n
    return highs, lows, closes


def test_compute_atr_basic():
    highs, lows, closes = _synthetic_bars(15)
    result = compute_atr(highs, lows, closes)
    # TR per bar = max(4, |102-100|, |98-100|) = 4.0 for all 14 windows
    assert result is not None
    assert isinstance(result, float)
    assert result == pytest.approx(4.0)


def test_compute_atr_insufficient_history():
    highs, lows, closes = _synthetic_bars(10)
    assert compute_atr(highs, lows, closes) is None


def test_compute_atr_exactly_14_bars_returns_none():
    # 14 bars → only 13 TRs with prev_close — not enough
    highs, lows, closes = _synthetic_bars(14)
    assert compute_atr(highs, lows, closes) is None


# ---------------------------------------------------------------------------
# check_stop_loss
# ---------------------------------------------------------------------------

_SL_CONFIG = {
    "atr_multiplier": 2.0,
    "atr_lookback": 14,
    "fallback_pct_us": -0.05,
    "fallback_pct_ar": -0.08,
}


def test_check_stop_loss_atr_trigger():
    # entry=100, ATR=4.0, stop=100-2*4=92.  close=90 → trigger
    highs, lows, closes = _synthetic_bars(15, base_close=100.0)
    history = [{"high": h, "low": lo, "close": c} for h, lo, c in zip(highs, lows, closes)]
    positions = {"AAPL": {"entry_price": 100.0, "qty": 10.0, "market": "US"}}
    daily_bars = {"AAPL": {"close": 90.0}}
    result = check_stop_loss(positions, daily_bars, {"AAPL": history}, _SL_CONFIG)
    assert "AAPL" in result


def test_check_stop_loss_atr_no_trigger():
    # entry=100, ATR=4.0, stop=92.  close=95 → no trigger
    highs, lows, closes = _synthetic_bars(15, base_close=100.0)
    history = [{"high": h, "low": lo, "close": c} for h, lo, c in zip(highs, lows, closes)]
    positions = {"AAPL": {"entry_price": 100.0, "qty": 10.0, "market": "US"}}
    daily_bars = {"AAPL": {"close": 95.0}}
    result = check_stop_loss(positions, daily_bars, {"AAPL": history}, _SL_CONFIG)
    assert result == []


def test_check_stop_loss_fallback_us():
    # insufficient history → fallback US -5%.  entry=100, stop=95.  close=93 → trigger
    positions = {"MSFT": {"entry_price": 100.0, "qty": 5.0, "market": "US"}}
    daily_bars = {"MSFT": {"close": 93.0}}
    result = check_stop_loss(positions, daily_bars, {}, _SL_CONFIG)
    assert "MSFT" in result


def test_check_stop_loss_fallback_ar():
    # insufficient history → fallback AR -8%.  entry=100, stop=92.  close=94 → no trigger (-6% drop)
    positions = {"GGAL": {"entry_price": 100.0, "qty": 5.0, "market": "AR"}}
    daily_bars = {"GGAL": {"close": 94.0}}
    result = check_stop_loss(positions, daily_bars, {}, _SL_CONFIG)
    assert result == []


def test_check_stop_loss_empty_positions():
    result = check_stop_loss({}, {"AAPL": {"close": 90.0}}, {}, _SL_CONFIG)
    assert result == []


def test_log_risk_cycle_includes_meta_at_debug(caplog: pytest.LogCaptureFixture):
    guardrail = GuardrailResult(allowed=False, reason="short_monthly_kill_switch", meta={"monthly_drawdown": -0.09, "kill_dd": -0.08})
    with caplog.at_level(logging.DEBUG, logger="risk_guardrails"):
        log_risk_cycle(
            engine="short",
            date="2026-04-15",
            guardrail=guardrail,
            orders_proposed=0,
            orders_filled=0,
            kill_switch_active=True,
        )

    payload = json.loads(caplog.records[0].message)
    assert "meta" in payload
    assert payload["meta"]["monthly_drawdown"] == pytest.approx(-0.09)
