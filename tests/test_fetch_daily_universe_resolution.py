"""Behavior tests for scripts/fetch_daily._resolve_symbols_ar_for_run (universe + holdings + budget)."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from data.iol_api_meter import ApiBudgetEval
from data.schema import UniverseSnapshotRow
from data.universe_selector import DynamicUniverseResult
from scripts.fetch_daily import _resolve_symbols_ar_for_run


def _policy_dynamic() -> dict:
    return {
        "symbols": {
            "whitelist_ar_file": "config/symbols/whitelist_ar.yaml",
            "whitelist_cedear_file": "config/symbols/whitelist_cedear.yaml",
            "whitelist_us_file": "config/symbols/whitelist_us.yaml",
            "universe_selection": {
                "enabled": True,
                "rebalance_frequency": "weekly",
                "targets": {"merval_top_n": 10, "cedears_top_n": 20},
                "volume_window_trading_days": 20,
                "tiebreakers": ["avg_notional_desc", "symbol_asc"],
                "api_budget": {
                    "monthly_limit": 25000,
                    "soft_limit_pct": 0.8,
                    "max_calls_per_job": 2000,
                },
            },
        },
    }


def _budget_ok() -> ApiBudgetEval:
    z = {"token_count": 0, "refresh_count": 0, "history_count": 0, "universe_volume_count": 0}
    return ApiBudgetEval(
        month_key="2026-05",
        monthly_limit=25000,
        soft_threshold=20000,
        counts=z,
        monthly_total=0,
        monthly_hard_exceeded=False,
        monthly_soft_exceeded=False,
        force_monthly_cadence=False,
    )


def test_symbols_override_skips_universe_logic():
    policy = _policy_dynamic()
    db = MagicMock()
    report: dict = {}
    _resolve_symbols_ar_for_run(
        policy=policy,
        db=db,
        today=date(2026, 5, 15),
        symbols_ar_override=["MANUAL"],
        budget_eval=_budget_ok(),
        universe_report=report,
    )
    assert report["symbols_ar_effective"] == ["MANUAL"]
    db.get_latest_universe_selection_date.assert_not_called()


@patch("scripts.fetch_daily.open_ar_position_symbols_from_db", return_value=["HOLD"])
@patch(
    "scripts.fetch_daily.should_refresh_dynamic_universe",
    return_value=(False, "policy_weekly_cadence"),
)
def test_reuses_last_snapshot_when_refresh_skipped(_, __):
    policy = _policy_dynamic()
    db = MagicMock()
    sel = date(2026, 5, 11)
    db.get_latest_universe_selection_date.return_value = sel
    db.get_universe_snapshots_for_date.return_value = [
        UniverseSnapshotRow(
            selection_date=sel,
            bucket="merval",
            symbol="AAA",
            rank=1,
            metric_value=1.0,
            source="dynamic",
            schema_version=1,
        ),
        UniverseSnapshotRow(
            selection_date=sel,
            bucket="cedear",
            symbol="BBB",
            rank=1,
            metric_value=2.0,
            source="dynamic",
            schema_version=1,
        ),
    ]
    report: dict = {}
    _resolve_symbols_ar_for_run(
        policy=policy,
        db=db,
        today=date(2026, 5, 13),
        symbols_ar_override=None,
        budget_eval=_budget_ok(),
        universe_report=report,
    )
    assert report["symbols_ar_effective"] == ["AAA", "BBB", "HOLD"]
    assert report["dynamic_refresh_decision"] == "policy_weekly_cadence"


@patch("scripts.fetch_daily.open_ar_position_symbols_from_db", return_value=[])
@patch(
    "scripts.fetch_daily.should_refresh_dynamic_universe",
    return_value=(False, "monthly_hard_cap"),
)
def test_monthly_hard_skips_refresh_but_keeps_db_snapshot(_, __):
    policy = _policy_dynamic()
    db = MagicMock()
    sel = date(2026, 5, 1)
    db.get_latest_universe_selection_date.return_value = sel
    db.get_universe_snapshots_for_date.return_value = [
        UniverseSnapshotRow(
            selection_date=sel,
            bucket="merval",
            symbol="KEEP",
            rank=1,
            metric_value=3.0,
            source="dynamic",
            schema_version=1,
        ),
    ]
    report: dict = {}
    _resolve_symbols_ar_for_run(
        policy=policy,
        db=db,
        today=date(2026, 5, 20),
        symbols_ar_override=None,
        budget_eval=_budget_ok(),
        universe_report=report,
    )
    assert report["symbols_ar_effective"] == ["KEEP"]


@patch("scripts.fetch_daily.open_ar_position_symbols_from_db", return_value=["H"])
@patch(
    "scripts.fetch_daily.should_refresh_dynamic_universe",
    return_value=(True, "ok"),
)
@patch("scripts.fetch_daily.select_dynamic_universe")
def test_job_budget_abort_reuses_previous_snapshot(mock_select, *_):
    mock_select.return_value = DynamicUniverseResult(
        merval_symbols=[],
        cedear_symbols=[],
        snapshot_rows=[],
        skipped=[("_", "job_budget_exceeded")],
        budget_job_aborted=True,
    )
    policy = _policy_dynamic()
    db = MagicMock()
    sel = date(2026, 4, 28)
    db.get_latest_universe_selection_date.return_value = sel
    db.get_universe_snapshots_for_date.return_value = [
        UniverseSnapshotRow(
            selection_date=sel,
            bucket="merval",
            symbol="OLD",
            rank=1,
            metric_value=1.0,
            source="dynamic",
            schema_version=1,
        ),
    ]
    report: dict = {}
    _resolve_symbols_ar_for_run(
        policy=policy,
        db=db,
        today=date(2026, 5, 15),
        symbols_ar_override=None,
        budget_eval=_budget_ok(),
        universe_report=report,
    )
    assert report["dynamic_selection"] == "aborted_job_budget"
    assert report["symbols_ar_effective"] == ["H", "OLD"]
