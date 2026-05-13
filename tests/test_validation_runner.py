"""Integration tests for validation/runner.py — run_validation_wf().

All stages and the DB are mocked — no real data or simulation is run.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

from validation.report import StageResult, ValidationReport
from validation.runner import run_validation_wf

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_BASE_DATE = date(2026, 1, 2)
_TRADING_DAYS_90 = [_BASE_DATE + timedelta(days=i) for i in range(90)]


def _make_policy(lookback: int = 90) -> dict:
    """Minimal policy doc sufficient to drive the runner."""
    return {
        "schema_version": 1,
        "validation_wf": {"lookback_trading_days": lookback},
        "weights": {"short": 0.30, "long": 0.70},
        "geo": {"AR": 0.20, "US": 0.80},
        "short_kill_switch_monthly_dd": -0.08,
        "risk": {
            "max_daily_loss_short_pct": -0.02,
            "max_daily_loss_long_pct": -0.015,
            "max_daily_loss_total_pct": -0.03,
            "max_notional_per_ticker_pct": 0.08,
            "halt_on_data_quality": True,
        },
        "short_term_pre_gate": {"enabled": False},
        "long_term_engine": {
            "drift_rebalance_threshold_pp": 2.0,
            "drift_convention": "per_line",
            "rebalance_rule": "first_us_trading_day_of_calendar_week",
            "max_long_rebalance_turnover_pct": None,
            "satellite_markets": ["US"],
            "core_lines": [
                {"symbol": "SPY", "target_weight": 0.55},
                {"symbol": "IWM", "target_weight": 0.30},
            ],
            "satellite_lines": [
                {"symbol": "QQQ", "target_weight": 0.15},
            ],
            "satellite_limits": {
                "max_satellite_weight_total": 0.20,
                "max_weight_per_satellite_line": 0.15,
                "max_satellite_names": 3,
            },
        },
        "markets": {
            "US": {"commission_bps_per_side": 1.0, "slippage_bps": 2.0},
            "AR": {"commission_bps_per_side": 15.0, "slippage_bps": 5.0},
        },
        "symbols": {
            "whitelist_us_file": "",
            "whitelist_ar_file": "",
            "inline_us": [],
            "inline_ar": [],
        },
    }


def _passed_stage(name: str) -> StageResult:
    return StageResult(stage=name, passed=True, metrics={}, violations=[])


def _failed_stage(name: str) -> StageResult:
    return StageResult(stage=name, passed=False, metrics={}, violations=["failed"])


def _make_db_mock(trading_days: list[date] | None = None) -> MagicMock:
    """Return a MarketDB mock that returns the given trading days from the calendars table."""
    days = trading_days if trading_days is not None else _TRADING_DAYS_90
    db = MagicMock()
    # Simulate db._conn.execute(...).fetchall() returning (ts,) rows
    cursor_mock = MagicMock()
    cursor_mock.fetchall.return_value = [(d.isoformat(),) for d in days]
    db._conn.execute.return_value = cursor_mock
    return db


# ---------------------------------------------------------------------------
# Test: all stages pass → go=True
# ---------------------------------------------------------------------------

_STAGE_MODULES = "validation.runner"


class TestAllStagesPass:
    @patch(f"{_STAGE_MODULES}.run_kill_switch_history_stage")
    @patch(f"{_STAGE_MODULES}.run_risk_audit_stage")
    @patch(f"{_STAGE_MODULES}.run_long_engine_stage")
    @patch(f"{_STAGE_MODULES}.run_short_pre_gate_stage")
    @patch(f"{_STAGE_MODULES}.run_data_quality_stage")
    def test_all_passed_returns_go_true(
        self, mock_dq, mock_spg, mock_le, mock_ra, mock_ksh
    ):
        mock_dq.return_value = _passed_stage("data_quality")
        mock_spg.return_value = _passed_stage("short_pre_gate")
        mock_le.return_value = _passed_stage("long_engine")
        mock_ra.return_value = _passed_stage("risk_audit")
        mock_ksh.return_value = _passed_stage("kill_switch_history")

        report = run_validation_wf(_make_policy(), _make_db_mock(), 100_000.0)

        assert report.go is True

    @patch(f"{_STAGE_MODULES}.run_kill_switch_history_stage")
    @patch(f"{_STAGE_MODULES}.run_risk_audit_stage")
    @patch(f"{_STAGE_MODULES}.run_long_engine_stage")
    @patch(f"{_STAGE_MODULES}.run_short_pre_gate_stage")
    @patch(f"{_STAGE_MODULES}.run_data_quality_stage")
    def test_report_has_five_stages(
        self, mock_dq, mock_spg, mock_le, mock_ra, mock_ksh
    ):
        mock_dq.return_value = _passed_stage("data_quality")
        mock_spg.return_value = _passed_stage("short_pre_gate")
        mock_le.return_value = _passed_stage("long_engine")
        mock_ra.return_value = _passed_stage("risk_audit")
        mock_ksh.return_value = _passed_stage("kill_switch_history")

        report = run_validation_wf(_make_policy(), _make_db_mock(), 100_000.0)

        assert len(report.stages) == 5
        stage_names = [s.stage for s in report.stages]
        assert stage_names == [
            "data_quality",
            "short_pre_gate",
            "long_engine",
            "risk_audit",
            "kill_switch_history",
        ]

    @patch(f"{_STAGE_MODULES}.run_kill_switch_history_stage")
    @patch(f"{_STAGE_MODULES}.run_risk_audit_stage")
    @patch(f"{_STAGE_MODULES}.run_long_engine_stage")
    @patch(f"{_STAGE_MODULES}.run_short_pre_gate_stage")
    @patch(f"{_STAGE_MODULES}.run_data_quality_stage")
    def test_report_type(
        self, mock_dq, mock_spg, mock_le, mock_ra, mock_ksh
    ):
        for m, name in [
            (mock_dq, "data_quality"),
            (mock_spg, "short_pre_gate"),
            (mock_le, "long_engine"),
            (mock_ra, "risk_audit"),
            (mock_ksh, "kill_switch_history"),
        ]:
            m.return_value = _passed_stage(name)

        report = run_validation_wf(_make_policy(), _make_db_mock(), 100_000.0)

        assert isinstance(report, ValidationReport)
        assert isinstance(report.go, bool)


# ---------------------------------------------------------------------------
# Test: short_pre_gate fails → go=False
# ---------------------------------------------------------------------------

class TestShortPreGateFails:
    @patch(f"{_STAGE_MODULES}.run_kill_switch_history_stage")
    @patch(f"{_STAGE_MODULES}.run_risk_audit_stage")
    @patch(f"{_STAGE_MODULES}.run_long_engine_stage")
    @patch(f"{_STAGE_MODULES}.run_short_pre_gate_stage")
    @patch(f"{_STAGE_MODULES}.run_data_quality_stage")
    def test_short_pre_gate_fail_returns_go_false(
        self, mock_dq, mock_spg, mock_le, mock_ra, mock_ksh
    ):
        mock_dq.return_value = _passed_stage("data_quality")
        mock_spg.return_value = _failed_stage("short_pre_gate")
        mock_le.return_value = _passed_stage("long_engine")
        mock_ra.return_value = _passed_stage("risk_audit")
        mock_ksh.return_value = _passed_stage("kill_switch_history")

        report = run_validation_wf(_make_policy(), _make_db_mock(), 100_000.0)

        assert report.go is False

    @patch(f"{_STAGE_MODULES}.run_kill_switch_history_stage")
    @patch(f"{_STAGE_MODULES}.run_risk_audit_stage")
    @patch(f"{_STAGE_MODULES}.run_long_engine_stage")
    @patch(f"{_STAGE_MODULES}.run_short_pre_gate_stage")
    @patch(f"{_STAGE_MODULES}.run_data_quality_stage")
    def test_short_pre_gate_fail_stage_recorded(
        self, mock_dq, mock_spg, mock_le, mock_ra, mock_ksh
    ):
        mock_dq.return_value = _passed_stage("data_quality")
        mock_spg.return_value = _failed_stage("short_pre_gate")
        mock_le.return_value = _passed_stage("long_engine")
        mock_ra.return_value = _passed_stage("risk_audit")
        mock_ksh.return_value = _passed_stage("kill_switch_history")

        report = run_validation_wf(_make_policy(), _make_db_mock(), 100_000.0)

        spg_result = next(s for s in report.stages if s.stage == "short_pre_gate")
        assert spg_result.passed is False


# ---------------------------------------------------------------------------
# Test: data_quality fails → go=True (does not block)
# ---------------------------------------------------------------------------

class TestDataQualityDoesNotBlock:
    @patch(f"{_STAGE_MODULES}.run_kill_switch_history_stage")
    @patch(f"{_STAGE_MODULES}.run_risk_audit_stage")
    @patch(f"{_STAGE_MODULES}.run_long_engine_stage")
    @patch(f"{_STAGE_MODULES}.run_short_pre_gate_stage")
    @patch(f"{_STAGE_MODULES}.run_data_quality_stage")
    def test_data_quality_fail_does_not_affect_go(
        self, mock_dq, mock_spg, mock_le, mock_ra, mock_ksh
    ):
        # data_quality fails but all blocking stages pass
        mock_dq.return_value = _failed_stage("data_quality")
        mock_spg.return_value = _passed_stage("short_pre_gate")
        mock_le.return_value = _passed_stage("long_engine")
        mock_ra.return_value = _passed_stage("risk_audit")
        mock_ksh.return_value = _passed_stage("kill_switch_history")

        report = run_validation_wf(_make_policy(), _make_db_mock(), 100_000.0)

        assert report.go is True

    @patch(f"{_STAGE_MODULES}.run_kill_switch_history_stage")
    @patch(f"{_STAGE_MODULES}.run_risk_audit_stage")
    @patch(f"{_STAGE_MODULES}.run_long_engine_stage")
    @patch(f"{_STAGE_MODULES}.run_short_pre_gate_stage")
    @patch(f"{_STAGE_MODULES}.run_data_quality_stage")
    def test_data_quality_fail_recorded_in_report(
        self, mock_dq, mock_spg, mock_le, mock_ra, mock_ksh
    ):
        mock_dq.return_value = _failed_stage("data_quality")
        mock_spg.return_value = _passed_stage("short_pre_gate")
        mock_le.return_value = _passed_stage("long_engine")
        mock_ra.return_value = _passed_stage("risk_audit")
        mock_ksh.return_value = _passed_stage("kill_switch_history")

        report = run_validation_wf(_make_policy(), _make_db_mock(), 100_000.0)

        dq_result = next(s for s in report.stages if s.stage == "data_quality")
        assert dq_result.passed is False
        assert report.go is True


# ---------------------------------------------------------------------------
# Test: period_start and period_end are derived from trading_days
# ---------------------------------------------------------------------------

class TestPeriodDates:
    @patch(f"{_STAGE_MODULES}.run_kill_switch_history_stage")
    @patch(f"{_STAGE_MODULES}.run_risk_audit_stage")
    @patch(f"{_STAGE_MODULES}.run_long_engine_stage")
    @patch(f"{_STAGE_MODULES}.run_short_pre_gate_stage")
    @patch(f"{_STAGE_MODULES}.run_data_quality_stage")
    def test_period_start_equals_first_trading_day(
        self, mock_dq, mock_spg, mock_le, mock_ra, mock_ksh
    ):
        for m, name in [
            (mock_dq, "data_quality"),
            (mock_spg, "short_pre_gate"),
            (mock_le, "long_engine"),
            (mock_ra, "risk_audit"),
            (mock_ksh, "kill_switch_history"),
        ]:
            m.return_value = _passed_stage(name)

        days = [date(2025, 10, 1) + timedelta(days=i) for i in range(90)]
        db = _make_db_mock(trading_days=days)

        report = run_validation_wf(
            _make_policy(lookback=90),
            db,
            100_000.0,
            reference_date=date(2026, 1, 1),
        )

        assert report.period_start == days[0]

    @patch(f"{_STAGE_MODULES}.run_kill_switch_history_stage")
    @patch(f"{_STAGE_MODULES}.run_risk_audit_stage")
    @patch(f"{_STAGE_MODULES}.run_long_engine_stage")
    @patch(f"{_STAGE_MODULES}.run_short_pre_gate_stage")
    @patch(f"{_STAGE_MODULES}.run_data_quality_stage")
    def test_period_end_equals_last_trading_day(
        self, mock_dq, mock_spg, mock_le, mock_ra, mock_ksh
    ):
        for m, name in [
            (mock_dq, "data_quality"),
            (mock_spg, "short_pre_gate"),
            (mock_le, "long_engine"),
            (mock_ra, "risk_audit"),
            (mock_ksh, "kill_switch_history"),
        ]:
            m.return_value = _passed_stage(name)

        days = [date(2025, 10, 1) + timedelta(days=i) for i in range(90)]
        db = _make_db_mock(trading_days=days)

        report = run_validation_wf(
            _make_policy(lookback=90),
            db,
            100_000.0,
            reference_date=date(2026, 1, 1),
        )

        assert report.period_end == days[-1]

    @patch(f"{_STAGE_MODULES}.run_kill_switch_history_stage")
    @patch(f"{_STAGE_MODULES}.run_risk_audit_stage")
    @patch(f"{_STAGE_MODULES}.run_long_engine_stage")
    @patch(f"{_STAGE_MODULES}.run_short_pre_gate_stage")
    @patch(f"{_STAGE_MODULES}.run_data_quality_stage")
    def test_lookback_truncates_to_n_days(
        self, mock_dq, mock_spg, mock_le, mock_ra, mock_ksh
    ):
        """When the DB has 200 days but lookback=90, only the last 90 are used."""
        for m, name in [
            (mock_dq, "data_quality"),
            (mock_spg, "short_pre_gate"),
            (mock_le, "long_engine"),
            (mock_ra, "risk_audit"),
            (mock_ksh, "kill_switch_history"),
        ]:
            m.return_value = _passed_stage(name)

        all_days = [date(2025, 6, 1) + timedelta(days=i) for i in range(200)]
        db = _make_db_mock(trading_days=all_days)
        ref = all_days[-1]

        report = run_validation_wf(
            _make_policy(lookback=90),
            db,
            100_000.0,
            reference_date=ref,
        )

        # The runner passes the last 90 days to each stage.
        # period_start should be all_days[-90], period_end should be all_days[-1].
        assert report.period_start == all_days[-90]
        assert report.period_end == all_days[-1]

    @patch(f"{_STAGE_MODULES}.run_kill_switch_history_stage")
    @patch(f"{_STAGE_MODULES}.run_risk_audit_stage")
    @patch(f"{_STAGE_MODULES}.run_long_engine_stage")
    @patch(f"{_STAGE_MODULES}.run_short_pre_gate_stage")
    @patch(f"{_STAGE_MODULES}.run_data_quality_stage")
    def test_trading_days_passed_to_data_quality(
        self, mock_dq, mock_spg, mock_le, mock_ra, mock_ksh
    ):
        """Verifies that the actual list of trading_days is forwarded to stages."""
        for m, name in [
            (mock_dq, "data_quality"),
            (mock_spg, "short_pre_gate"),
            (mock_le, "long_engine"),
            (mock_ra, "risk_audit"),
            (mock_ksh, "kill_switch_history"),
        ]:
            m.return_value = _passed_stage(name)

        days = [date(2025, 10, 1) + timedelta(days=i) for i in range(5)]
        db = _make_db_mock(trading_days=days)

        run_validation_wf(_make_policy(lookback=5), db, 100_000.0)

        # First positional argument after db is trading_days
        call_args = mock_dq.call_args
        passed_days = call_args[0][1]  # positional: (db, trading_days, policy_doc)
        assert passed_days == days


# ---------------------------------------------------------------------------
# Test: each blocking stage fail individually → go=False
# ---------------------------------------------------------------------------

class TestEachBlockingStageIndividually:
    @pytest.mark.parametrize("failing_stage", [
        "short_pre_gate",
        "long_engine",
        "risk_audit",
        "kill_switch_history",
    ])
    @patch(f"{_STAGE_MODULES}.run_kill_switch_history_stage")
    @patch(f"{_STAGE_MODULES}.run_risk_audit_stage")
    @patch(f"{_STAGE_MODULES}.run_long_engine_stage")
    @patch(f"{_STAGE_MODULES}.run_short_pre_gate_stage")
    @patch(f"{_STAGE_MODULES}.run_data_quality_stage")
    def test_single_blocking_stage_fail_causes_no_go(
        self, mock_dq, mock_spg, mock_le, mock_ra, mock_ksh, failing_stage
    ):
        mock_map = {
            "data_quality": mock_dq,
            "short_pre_gate": mock_spg,
            "long_engine": mock_le,
            "risk_audit": mock_ra,
            "kill_switch_history": mock_ksh,
        }
        for name, m in mock_map.items():
            if name == failing_stage:
                m.return_value = _failed_stage(name)
            else:
                m.return_value = _passed_stage(name)

        report = run_validation_wf(_make_policy(), _make_db_mock(), 100_000.0)

        assert report.go is False


# ---------------------------------------------------------------------------
# Test: policy_version and lookback propagated correctly
# ---------------------------------------------------------------------------

class TestReportMetadata:
    @patch(f"{_STAGE_MODULES}.run_kill_switch_history_stage")
    @patch(f"{_STAGE_MODULES}.run_risk_audit_stage")
    @patch(f"{_STAGE_MODULES}.run_long_engine_stage")
    @patch(f"{_STAGE_MODULES}.run_short_pre_gate_stage")
    @patch(f"{_STAGE_MODULES}.run_data_quality_stage")
    def test_policy_version_matches_schema_version(
        self, mock_dq, mock_spg, mock_le, mock_ra, mock_ksh
    ):
        for m, name in [
            (mock_dq, "data_quality"),
            (mock_spg, "short_pre_gate"),
            (mock_le, "long_engine"),
            (mock_ra, "risk_audit"),
            (mock_ksh, "kill_switch_history"),
        ]:
            m.return_value = _passed_stage(name)

        policy = _make_policy()
        policy["schema_version"] = 42

        report = run_validation_wf(policy, _make_db_mock(), 100_000.0)

        assert report.policy_version == 42

    @patch(f"{_STAGE_MODULES}.run_kill_switch_history_stage")
    @patch(f"{_STAGE_MODULES}.run_risk_audit_stage")
    @patch(f"{_STAGE_MODULES}.run_long_engine_stage")
    @patch(f"{_STAGE_MODULES}.run_short_pre_gate_stage")
    @patch(f"{_STAGE_MODULES}.run_data_quality_stage")
    def test_lookback_trading_days_in_report(
        self, mock_dq, mock_spg, mock_le, mock_ra, mock_ksh
    ):
        for m, name in [
            (mock_dq, "data_quality"),
            (mock_spg, "short_pre_gate"),
            (mock_le, "long_engine"),
            (mock_ra, "risk_audit"),
            (mock_ksh, "kill_switch_history"),
        ]:
            m.return_value = _passed_stage(name)

        report = run_validation_wf(_make_policy(lookback=45), _make_db_mock(), 100_000.0)

        assert report.lookback_trading_days == 45

    @patch(f"{_STAGE_MODULES}.run_kill_switch_history_stage")
    @patch(f"{_STAGE_MODULES}.run_risk_audit_stage")
    @patch(f"{_STAGE_MODULES}.run_long_engine_stage")
    @patch(f"{_STAGE_MODULES}.run_short_pre_gate_stage")
    @patch(f"{_STAGE_MODULES}.run_data_quality_stage")
    def test_generated_at_is_iso_string(
        self, mock_dq, mock_spg, mock_le, mock_ra, mock_ksh
    ):
        for m, name in [
            (mock_dq, "data_quality"),
            (mock_spg, "short_pre_gate"),
            (mock_le, "long_engine"),
            (mock_ra, "risk_audit"),
            (mock_ksh, "kill_switch_history"),
        ]:
            m.return_value = _passed_stage(name)

        report = run_validation_wf(_make_policy(), _make_db_mock(), 100_000.0)

        # Must be parseable as an ISO datetime
        from datetime import datetime
        dt = datetime.fromisoformat(report.generated_at)
        assert dt.tzinfo is not None  # must be timezone-aware
