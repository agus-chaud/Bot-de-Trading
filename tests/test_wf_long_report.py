"""Tests for validation/wf_long_report.py — T5 aggregation + T6 JSON shape."""

from __future__ import annotations

import json
from datetime import date

import pytest

from validation.report import StageResult
from validation.wf_long_report import (
    aggregate_long_engine_wf_summary,
    build_long_engine_wf_report_model,
    long_engine_wf_report_to_json_dict,
    save_long_engine_wf_report_json,
)


def _long_metrics(
    drift: float,
    cost: float,
    mdd: float,
    rebalances: int,
) -> dict:
    return {
        "max_drift_observed_pp": drift,
        "total_rebalance_cost": cost,
        "monthly_drawdown_long": mdd,
        "rebalances_executed": rebalances,
    }


def _skipped_long() -> StageResult:
    return StageResult(
        stage="long_engine",
        passed=True,
        metrics={
            "max_drift_observed_pp": None,
            "total_rebalance_cost": None,
            "monthly_drawdown_long": None,
            "rebalances_executed": None,
        },
        violations=[],
        skipped=True,
    )


def test_aggregate_excludes_skipped_and_computes_globals() -> None:
    w0 = [date(2024, 1, 2), date(2024, 1, 3)]
    w1 = [date(2024, 2, 1), date(2024, 2, 2)]
    w2 = [date(2024, 3, 4), date(2024, 3, 5)]
    windows = [w0, w1, w2]
    r0 = StageResult("long_engine", True, _long_metrics(1.0, 10.0, -0.05, 2), [])
    r1 = _skipped_long()
    r2 = StageResult("long_engine", True, _long_metrics(5.0, 30.0, -0.02, 1), [])
    results = [r0, r1, r2]

    summary, skipped = aggregate_long_engine_wf_summary(windows, results)

    assert summary.windows_total == 3
    assert summary.windows_used_in_aggregates == 2
    assert len(skipped) == 1
    assert skipped[0].window_index == 1
    assert skipped[0].reason == "stage_skipped"

    assert summary.worst_monthly_drawdown_long == round(-0.05, 6)
    assert summary.avg_rebalance_cost == round(20.0, 4)  # (10+30)/2
    assert summary.total_rebalances_executed == 3
    assert summary.max_drift_observed_pp == 5.0
    assert summary.max_drift_window_index == 2
    assert summary.max_drift_period_start == w2[0]
    assert summary.max_drift_period_end == w2[-1]


def test_aggregate_all_skipped_returns_nulls_and_zero_rebalances() -> None:
    windows = [[date(2024, 1, 2)], [date(2024, 2, 1)]]
    results = [_skipped_long(), _skipped_long()]
    summary, skipped = aggregate_long_engine_wf_summary(windows, results)
    assert summary.windows_used_in_aggregates == 0
    assert summary.worst_monthly_drawdown_long is None
    assert summary.avg_rebalance_cost is None
    assert summary.total_rebalances_executed == 0
    assert summary.max_drift_observed_pp is None
    assert summary.max_drift_window_index is None
    assert len(skipped) == 2


def test_aggregate_empty_inputs() -> None:
    summary, skipped = aggregate_long_engine_wf_summary([], [])
    assert summary.windows_total == 0
    assert skipped == []


def test_aggregate_mismatched_lengths_raises() -> None:
    with pytest.raises(ValueError, match="same length"):
        aggregate_long_engine_wf_summary([[date(2024, 1, 1)]], [])


def test_json_roundtrip_and_required_keys(tmp_path) -> None:
    windows = [
        [date(2024, 1, 2), date(2024, 1, 3)],
        [date(2024, 2, 1), date(2024, 2, 5)],
    ]
    results = [
        StageResult("long_engine", True, _long_metrics(2.0, 5.0, -0.01, 1), []),
        _skipped_long(),
    ]
    model = build_long_engine_wf_report_model(windows, results, policy_version=1)
    payload = long_engine_wf_report_to_json_dict(model)
    json.dumps(payload)  # serializable

    assert payload["report_type"] == "long_engine_walk_forward"
    assert payload["policy_version"] == 1
    assert payload["windows_total"] == 2
    assert payload["windows_used_in_aggregates"] == 1
    assert len(payload["windows_skipped"]) == 1
    assert payload["windows_skipped"][0]["reason"] == "stage_skipped"

    assert len(payload["per_window"]) == 2
    for pw in payload["per_window"]:
        assert "window_index" in pw
        assert "period_start" in pw
        assert "metrics" in pw
        for k in (
            "max_drift_observed_pp",
            "total_rebalance_cost",
            "monthly_drawdown_long",
            "rebalances_executed",
        ):
            assert k in pw["metrics"]

    s = payload["summary"]
    assert "worst_monthly_drawdown_long" in s
    assert "avg_rebalance_cost" in s
    assert "total_rebalances_executed" in s
    assert "max_drift_observed_pp" in s
    assert "max_drift_window_index" in s
    assert "max_drift_period_start" in s
    assert "max_drift_period_end" in s

    out = save_long_engine_wf_report_json(payload, tmp_path)
    assert out.exists()
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["report_type"] == "long_engine_walk_forward"


def test_empty_window_marked_skipped() -> None:
    windows = [[]]
    results = [
        StageResult("long_engine", True, _long_metrics(0.0, 0.0, 0.0, 0), []),
    ]
    summary, skipped = aggregate_long_engine_wf_summary(windows, results)
    assert summary.windows_used_in_aggregates == 0
    assert skipped[0].reason == "empty_window"
    assert skipped[0].period_start is None
