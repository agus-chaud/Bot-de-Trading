"""Behavior tests for IOL API budget evaluation and cadence guards."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from data.iol_api_meter import (
    ApiBudgetEval,
    evaluate_api_budget,
    month_key_for_date,
    should_refresh_dynamic_universe,
    same_iso_week,
)


def test_evaluate_api_budget_hard_disables_dynamic() -> None:
    ev = evaluate_api_budget(
        usage_row={
            "token_count": 25000,
            "refresh_count": 0,
            "history_count": 0,
            "universe_volume_count": 0,
        },
        api_budget_cfg={"monthly_limit": 25000, "soft_limit_pct": 0.8, "max_calls_per_job": 100},
        month_key="2026-05",
    )
    assert ev.monthly_hard_exceeded is True
    assert ev.monthly_soft_exceeded is True
    assert ev.force_monthly_cadence is False


def test_evaluate_api_budget_soft_sets_monthly_cadence_flag() -> None:
    ev = evaluate_api_budget(
        usage_row={
            "token_count": 20000,
            "refresh_count": 0,
            "history_count": 0,
            "universe_volume_count": 0,
        },
        api_budget_cfg={"monthly_limit": 25000, "soft_limit_pct": 0.8, "max_calls_per_job": 100},
        month_key="2026-05",
    )
    assert ev.monthly_hard_exceeded is False
    assert ev.monthly_soft_exceeded is True
    assert ev.force_monthly_cadence is True


def test_evaluate_api_budget_counts_universe_volume_toward_monthly_total() -> None:
    ev = evaluate_api_budget(
        usage_row={
            "token_count": 50,
            "refresh_count": 0,
            "history_count": 100,
            "universe_volume_count": 24850,
        },
        api_budget_cfg={"monthly_limit": 25000, "soft_limit_pct": 0.8, "max_calls_per_job": 100},
        month_key="2026-05",
    )
    assert ev.monthly_total == 25000
    assert ev.monthly_hard_exceeded is True


def test_should_refresh_respects_monthly_hard() -> None:
    db = MagicMock()
    ev = ApiBudgetEval(
        month_key="2026-05",
        monthly_limit=100,
        soft_threshold=80,
        counts={},
        monthly_total=100,
        monthly_hard_exceeded=True,
        monthly_soft_exceeded=True,
        force_monthly_cadence=False,
    )
    ok, reason = should_refresh_dynamic_universe(date(2026, 5, 15), db, frequency="weekly", budget_eval=ev)
    assert ok is False
    assert reason == "monthly_hard_cap"


def test_should_refresh_soft_skips_second_run_same_month() -> None:
    db = MagicMock()
    db.get_latest_universe_selection_date.return_value = date(2026, 5, 10)
    ev = ApiBudgetEval(
        month_key="2026-05",
        monthly_limit=25000,
        soft_threshold=20000,
        counts={},
        monthly_total=20000,
        monthly_hard_exceeded=False,
        monthly_soft_exceeded=True,
        force_monthly_cadence=True,
    )
    ok, reason = should_refresh_dynamic_universe(date(2026, 5, 20), db, frequency="weekly", budget_eval=ev)
    assert ok is False
    assert reason == "soft_monthly_degraded_cadence"


def test_same_iso_week_matches_calendar_week() -> None:
    assert same_iso_week(date(2026, 5, 11), date(2026, 5, 15)) is True
    assert same_iso_week(date(2026, 5, 11), date(2026, 5, 18)) is False


def test_month_key_for_date() -> None:
    assert month_key_for_date(date(2026, 3, 7)) == "2026-03"
