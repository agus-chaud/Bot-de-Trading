"""Tests para validation/stages/short_pre_gate.py.

Mockea run_short_term_pre_gate para no correr el walk-forward real.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import yaml

from core_sim.short_term_pre_gate import PreGateReport, PreGateWindowResult
from validation.stages.short_pre_gate import run_short_pre_gate_stage

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_policy() -> dict:
    with (REPO_ROOT / "config" / "policy.v1.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _weekdays_from(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


class _FakeDB:
    """DB stub que devuelve datos vacíos — los tests mockean run_short_term_pre_gate."""

    def __init__(self) -> None:
        self._data: dict = {}

    class _Cursor:
        def fetchall(self):
            return []

    def execute(self, *_args, **_kwargs):  # noqa: ANN202
        return self._Cursor()

    def get_ohlcv(self, *_args, **_kwargs):  # noqa: ANN202
        return []


# ---------------------------------------------------------------------------
# Test 1: disabled → skipped
# ---------------------------------------------------------------------------


def test_disabled_returns_skipped():
    policy = deepcopy(_load_policy())
    policy["short_term_pre_gate"]["enabled"] = False

    days = _weekdays_from(date(2026, 1, 5), 20)
    result = run_short_pre_gate_stage(
        db=_FakeDB(),
        trading_days=days,
        policy_doc=policy,
        repo_root=REPO_ROOT,
        starting_cash=100_000.0,
    )

    assert result.stage == "short_pre_gate"
    assert result.passed is True
    assert result.skipped is True
    assert result.metrics == {}
    assert result.violations == []


# ---------------------------------------------------------------------------
# Test 2: pre-gate pasa → passed=True, violations=[]
# ---------------------------------------------------------------------------


def test_pre_gate_passes():
    policy = deepcopy(_load_policy())
    policy["short_term_pre_gate"]["enabled"] = True

    days = _weekdays_from(date(2026, 1, 5), 60)

    passing_window = PreGateWindowResult(
        metrics={
            "trading_days": tuple(str(d) for d in days[:12]),
            "total_fees": 10.0,
            "fee_ratio_of_initial": 0.0001,
            "sum_buy_notional": 5000.0,
            "min_short_monthly_drawdown": -0.01,
            "avg_equity": 100_000.0,
            "n_days": 12,
            "turnover_annualized_proxy": 1.2,
            "n_fills": 3,
        },
        passed=True,
        violations=[],
    )
    fake_report = PreGateReport(
        passed=True,
        windows=[passing_window, passing_window],
        global_failures=[],
    )

    with patch("validation.stages.short_pre_gate._bars_from_db", return_value={}), \
         patch(
             "validation.stages.short_pre_gate.run_short_term_pre_gate",
             return_value=fake_report,
         ):
        result = run_short_pre_gate_stage(
            db=_FakeDB(),
            trading_days=days,
            policy_doc=policy,
            repo_root=REPO_ROOT,
            starting_cash=100_000.0,
        )

    assert result.stage == "short_pre_gate"
    assert result.passed is True
    assert result.skipped is False
    assert result.violations == []
    assert result.metrics["windows_total"] == 2
    assert result.metrics["windows_passed"] == 2
    assert result.metrics["windows_failed"] == 0
    assert result.metrics["global_failures"] == []
    assert len(result.metrics["per_window"]) == 2


# ---------------------------------------------------------------------------
# Test 3: pre-gate falla → passed=False, violations con mensajes correctos
# ---------------------------------------------------------------------------


def test_pre_gate_fails_with_violations():
    policy = deepcopy(_load_policy())
    policy["short_term_pre_gate"]["enabled"] = True

    days = _weekdays_from(date(2026, 2, 2), 60)

    violation_msg_w1 = "fee_ratio 0.100000 > max 0.050000"
    violation_msg_w2 = "turnover_ann 600.0000 > max 500.0000"

    failed_window_1 = PreGateWindowResult(
        metrics={
            "trading_days": tuple(str(d) for d in days[:12]),
            "total_fees": 10_000.0,
            "fee_ratio_of_initial": 0.10,
            "sum_buy_notional": 5000.0,
            "min_short_monthly_drawdown": -0.01,
            "avg_equity": 100_000.0,
            "n_days": 12,
            "turnover_annualized_proxy": 1.0,
            "n_fills": 5,
        },
        passed=False,
        violations=[violation_msg_w1],
    )
    failed_window_2 = PreGateWindowResult(
        metrics={
            "trading_days": tuple(str(d) for d in days[10:22]),
            "total_fees": 5.0,
            "fee_ratio_of_initial": 0.00005,
            "sum_buy_notional": 25_000_000.0,
            "min_short_monthly_drawdown": -0.01,
            "avg_equity": 100_000.0,
            "n_days": 12,
            "turnover_annualized_proxy": 600.0,
            "n_fills": 50,
        },
        passed=False,
        violations=[violation_msg_w2],
    )
    fake_report = PreGateReport(
        passed=False,
        windows=[failed_window_1, failed_window_2],
        global_failures=[],
    )

    with patch("validation.stages.short_pre_gate._bars_from_db", return_value={}), \
         patch(
             "validation.stages.short_pre_gate.run_short_term_pre_gate",
             return_value=fake_report,
         ):
        result = run_short_pre_gate_stage(
            db=_FakeDB(),
            trading_days=days,
            policy_doc=policy,
            repo_root=REPO_ROOT,
            starting_cash=100_000.0,
        )

    assert result.stage == "short_pre_gate"
    assert result.passed is False
    assert result.skipped is False
    assert violation_msg_w1 in result.violations
    assert violation_msg_w2 in result.violations
    assert len(result.violations) == 2
    assert result.metrics["windows_total"] == 2
    assert result.metrics["windows_failed"] == 2
    assert result.metrics["windows_passed"] == 0


# ---------------------------------------------------------------------------
# Test 4: global_failures → se propagan a violations
# ---------------------------------------------------------------------------


def test_global_failures_propagated_to_violations():
    policy = deepcopy(_load_policy())
    policy["short_term_pre_gate"]["enabled"] = True

    days = _weekdays_from(date(2026, 3, 2), 10)

    global_fail_msg = "insufficient_oos_windows:need_2_got_0_(sorted_days=10,burn_in=45,oos=12,step=10)"
    fake_report = PreGateReport(
        passed=False,
        windows=[],
        global_failures=[global_fail_msg],
    )

    with patch("validation.stages.short_pre_gate._bars_from_db", return_value={}), \
         patch(
             "validation.stages.short_pre_gate.run_short_term_pre_gate",
             return_value=fake_report,
         ):
        result = run_short_pre_gate_stage(
            db=_FakeDB(),
            trading_days=days,
            policy_doc=policy,
            repo_root=REPO_ROOT,
            starting_cash=100_000.0,
        )

    assert result.passed is False
    assert global_fail_msg in result.violations
    assert result.metrics["global_failures"] == [global_fail_msg]
    assert result.metrics["windows_total"] == 0
