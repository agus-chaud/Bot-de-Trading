"""Tests for validation/stages/risk_audit.py."""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

from validation.stages.risk_audit import run_risk_audit_stage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_policy() -> dict:
    import yaml

    with (REPO_ROOT / "config" / "policy.v1.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _make_db() -> MagicMock:
    """Return a minimal MarketDB mock — Tipo B doesn't need real DB data."""
    return MagicMock()


def _trading_days(n: int = 5) -> list[date]:
    from datetime import timedelta

    base = date(2026, 1, 2)
    return [base + timedelta(days=i) for i in range(n)]


# ---------------------------------------------------------------------------
# Tipo A — valid policy passes
# ---------------------------------------------------------------------------

class TestTipoAValid:
    def test_valid_yaml_schema_valid_true(self):
        doc = _load_policy()
        result = run_risk_audit_stage(_make_db(), _trading_days(), doc, REPO_ROOT)
        assert result.metrics["schema_valid"] is True

    def test_valid_yaml_passed_true(self):
        doc = _load_policy()
        result = run_risk_audit_stage(_make_db(), _trading_days(), doc, REPO_ROOT)
        assert result.passed is True

    def test_valid_yaml_no_violations(self):
        doc = _load_policy()
        result = run_risk_audit_stage(_make_db(), _trading_days(), doc, REPO_ROOT)
        assert result.violations == []

    def test_valid_yaml_all_static_checks_pass(self):
        doc = _load_policy()
        result = run_risk_audit_stage(_make_db(), _trading_days(), doc, REPO_ROOT)
        assert result.metrics["static_checks_failed"] == []
        assert len(result.metrics["static_checks_passed"]) > 0

    def test_stage_name_is_risk_audit(self):
        doc = _load_policy()
        result = run_risk_audit_stage(_make_db(), _trading_days(), doc, REPO_ROOT)
        assert result.stage == "risk_audit"

    def test_not_skipped(self):
        doc = _load_policy()
        result = run_risk_audit_stage(_make_db(), _trading_days(), doc, REPO_ROOT)
        assert result.skipped is False


# ---------------------------------------------------------------------------
# Tipo A — weights don't sum to 1
# ---------------------------------------------------------------------------

class TestTipoAWeightsFail:
    def test_weights_not_summing_fails(self):
        doc = copy.deepcopy(_load_policy())
        doc["weights"]["short"] = 0.40  # 0.40 + 0.70 = 1.10
        result = run_risk_audit_stage(_make_db(), _trading_days(), doc, REPO_ROOT)
        assert result.passed is False

    def test_weights_failure_in_static_checks_failed(self):
        doc = copy.deepcopy(_load_policy())
        doc["weights"]["short"] = 0.40
        result = run_risk_audit_stage(_make_db(), _trading_days(), doc, REPO_ROOT)
        assert "weights_sum_to_one" in result.metrics["static_checks_failed"]

    def test_weights_failure_in_violations(self):
        doc = copy.deepcopy(_load_policy())
        doc["weights"]["short"] = 0.40
        result = run_risk_audit_stage(_make_db(), _trading_days(), doc, REPO_ROOT)
        assert any("weights_sum_to_one" in v for v in result.violations)

    def test_geo_not_summing_fails(self):
        doc = copy.deepcopy(_load_policy())
        doc["geo"]["AR"] = 0.50  # 0.50 + 0.80 = 1.30
        result = run_risk_audit_stage(_make_db(), _trading_days(), doc, REPO_ROOT)
        assert result.passed is False
        assert "geo_sum_to_one" in result.metrics["static_checks_failed"]


# ---------------------------------------------------------------------------
# Tipo A — kill_switch_dd positive
# ---------------------------------------------------------------------------

class TestTipoAKillSwitchPositive:
    def test_positive_kill_switch_dd_fails(self):
        doc = copy.deepcopy(_load_policy())
        doc["short_kill_switch_monthly_dd"] = 0.05  # positive — invalid
        result = run_risk_audit_stage(_make_db(), _trading_days(), doc, REPO_ROOT)
        assert result.passed is False

    def test_positive_kill_switch_dd_in_violations(self):
        doc = copy.deepcopy(_load_policy())
        doc["short_kill_switch_monthly_dd"] = 0.05
        result = run_risk_audit_stage(_make_db(), _trading_days(), doc, REPO_ROOT)
        assert "kill_switch_monthly_dd_is_negative" in result.metrics["static_checks_failed"]

    def test_zero_kill_switch_dd_fails(self):
        """Zero is not strictly negative — must also fail."""
        doc = copy.deepcopy(_load_policy())
        doc["short_kill_switch_monthly_dd"] = 0.0
        result = run_risk_audit_stage(_make_db(), _trading_days(), doc, REPO_ROOT)
        assert result.passed is False


# ---------------------------------------------------------------------------
# Tipo A — max_daily_loss fields must be negative
# ---------------------------------------------------------------------------

class TestTipoAMaxDailyLoss:
    @pytest.mark.parametrize("field", [
        "max_daily_loss_short_pct",
        "max_daily_loss_long_pct",
        "max_daily_loss_total_pct",
    ])
    def test_positive_daily_loss_fails(self, field: str):
        doc = copy.deepcopy(_load_policy())
        doc["risk"][field] = 0.02  # positive — invalid
        result = run_risk_audit_stage(_make_db(), _trading_days(), doc, REPO_ROOT)
        assert result.passed is False
        label = f"{field}_is_negative"
        assert label in result.metrics["static_checks_failed"]


# ---------------------------------------------------------------------------
# Tipo B — dynamic guardrail counts
# ---------------------------------------------------------------------------

class TestTipoBDynamicCounts:
    def test_trading_days_audited_matches_input(self):
        doc = _load_policy()
        days = _trading_days(10)
        result = run_risk_audit_stage(_make_db(), days, doc, REPO_ROOT)
        assert result.metrics["trading_days_audited"] == 10

    def test_guardrail_halt_data_quality_counted(self):
        """With halt_on_data_quality=True in policy, every day fires the DQ guardrail."""
        doc = copy.deepcopy(_load_policy())
        assert doc["risk"]["halt_on_data_quality"] is True
        days = _trading_days(5)
        result = run_risk_audit_stage(_make_db(), days, doc, REPO_ROOT)
        # Each of the 5 days should register a potential halt_data_quality activation
        assert result.metrics["guardrail_halt_data_quality"] == 5

    def test_guardrail_halt_data_quality_zero_when_disabled(self):
        """With halt_on_data_quality=False, no DQ halts should fire."""
        doc = copy.deepcopy(_load_policy())
        doc["risk"]["halt_on_data_quality"] = False
        days = _trading_days(5)
        result = run_risk_audit_stage(_make_db(), days, doc, REPO_ROOT)
        assert result.metrics["guardrail_halt_data_quality"] == 0

    def test_guardrail_daily_loss_short_counted(self):
        """Scoreboard at max_daily_loss_short_pct - 0.001 must trigger short limit."""
        doc = _load_policy()
        days = _trading_days(3)
        result = run_risk_audit_stage(_make_db(), days, doc, REPO_ROOT)
        assert result.metrics["guardrail_daily_loss_short"] == 3

    def test_guardrail_daily_loss_long_counted(self):
        """Scoreboard at max_daily_loss_long_pct - 0.001 must trigger long limit."""
        doc = _load_policy()
        days = _trading_days(3)
        result = run_risk_audit_stage(_make_db(), days, doc, REPO_ROOT)
        assert result.metrics["guardrail_daily_loss_long"] == 3

    def test_notional_violations_counted(self):
        """max_notional_per_ticker_pct < 1.0 → one notional violation per day."""
        doc = _load_policy()
        days = _trading_days(4)
        result = run_risk_audit_stage(_make_db(), days, doc, REPO_ROOT)
        assert result.metrics["notional_violations"] == 4

    def test_tipo_b_does_not_affect_passed(self):
        """Tipo B metrics never block GO — passed stays True even with many guardrail hits."""
        doc = _load_policy()
        days = _trading_days(20)
        result = run_risk_audit_stage(_make_db(), days, doc, REPO_ROOT)
        assert result.passed is True

    def test_empty_trading_days_zeroes(self):
        doc = _load_policy()
        result = run_risk_audit_stage(_make_db(), [], doc, REPO_ROOT)
        assert result.metrics["trading_days_audited"] == 0
        assert result.metrics["guardrail_halt_data_quality"] == 0
        assert result.metrics["notional_violations"] == 0

    def test_no_trade_window_always_zero_for_daily_cadence(self):
        """no_trade_window is intraday — must always be 0 in a daily audit."""
        doc = _load_policy()
        days = _trading_days(10)
        result = run_risk_audit_stage(_make_db(), days, doc, REPO_ROOT)
        assert result.metrics["guardrail_no_trade_window"] == 0


# ---------------------------------------------------------------------------
# Metrics keys completeness
# ---------------------------------------------------------------------------

class TestMetricsShape:
    def test_all_expected_keys_present(self):
        doc = _load_policy()
        result = run_risk_audit_stage(_make_db(), _trading_days(), doc, REPO_ROOT)
        expected_keys = {
            "schema_valid",
            "static_checks_passed",
            "static_checks_failed",
            "guardrail_halt_data_quality",
            "guardrail_daily_loss_short",
            "guardrail_daily_loss_long",
            "guardrail_no_trade_window",
            "notional_violations",
            "trading_days_audited",
        }
        assert expected_keys.issubset(result.metrics.keys())
