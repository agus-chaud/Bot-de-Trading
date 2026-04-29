"""Tests for validation/wf_runner.py — walk-forward long_engine runner (T4)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from data.schema import OHLCVRow
from data.storage import MarketDB
from validation.report import StageResult
from validation.wf_runner import long_engine_wf_metrics_list, run_long_engine_wf_windows
from validation.wf_windows import generate_wf_windows

REPO_ROOT = Path(__file__).resolve().parents[1]
_VENUE_US = "XNYS"


def _policy_doc() -> dict[str, Any]:
    with (REPO_ROOT / "config" / "policy.v1.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _ohlcv_row(symbol: str, ts: date, close: float = 100.0) -> OHLCVRow:
    return OHLCVRow(
        symbol=symbol,
        ts=ts,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000_000.0,
        currency="USD",
        venue=_VENUE_US,
        imputed=False,
    )


def _generate_trading_days(
    start_year: int, start_month: int, num_months: int
) -> list[date]:
    from datetime import timedelta

    days: list[date] = []
    year, month = start_year, start_month
    for _ in range(num_months):
        d = date(year, month, 1)
        count = 0
        while count < 20:
            if d.weekday() < 5:
                days.append(d)
                count += 1
            d += timedelta(days=1)
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    return days


def _make_db(tmp_path: Path, trading_days: list[date]) -> MarketDB:
    db = MarketDB(str(tmp_path / "wf_runner.db"))
    rows: list[OHLCVRow] = []
    for sym, price in (("SPY", 100.0), ("IWM", 50.0), ("QQQ", 300.0)):
        for d in trading_days:
            rows.append(_ohlcv_row(sym, d, price))
    db.upsert_ohlcv(rows)
    return db


def _fake_stage_result(idx: int) -> StageResult:
    return StageResult(
        stage="long_engine",
        passed=True,
        metrics={"window_idx": idx},
        violations=[],
    )


@patch("validation.wf_runner.run_long_engine_stage", autospec=True)
def test_runner_invokes_stage_once_per_window_with_correct_days(
    mock_stage: MagicMock,
) -> None:
    mock_stage.side_effect = [
        _fake_stage_result(0),
        _fake_stage_result(1),
        _fake_stage_result(2),
    ]
    w0 = [date(2024, 1, 2), date(2024, 1, 3)]
    w1 = [date(2024, 2, 1)]
    w2 = [date(2024, 3, 4), date(2024, 3, 5), date(2024, 3, 6)]
    windows = [w0, w1, w2]
    db = MagicMock(spec=MarketDB)
    policy: dict[str, Any] = {"schema_version": 1}
    results = run_long_engine_wf_windows(
        db, windows, policy, REPO_ROOT, 100_000.0
    )
    assert len(results) == 3
    assert mock_stage.call_count == 3
    c0, c1, c2 = mock_stage.call_args_list
    assert c0[0][1] == w0
    assert c1[0][1] == w1
    assert c2[0][1] == w2
    assert all(r.stage == "long_engine" for r in results)


def test_runner_empty_windows_returns_empty() -> None:
    db = MagicMock(spec=MarketDB)
    assert (
        run_long_engine_wf_windows(db, [], {}, REPO_ROOT, 1.0) == []
    )


def test_metrics_list_matches_results_order() -> None:
    r0 = StageResult("long_engine", True, {"a": 1}, [])
    r1 = StageResult("long_engine", True, {"a": 2}, [])
    assert long_engine_wf_metrics_list([r0, r1]) == [{"a": 1}, {"a": 2}]


def test_integration_generate_windows_then_runner(tmp_path: Path) -> None:
    """End-to-end: WF windows from T3 + real long_engine stage per window."""
    from datetime import timedelta

    def weekdays(start: date, end: date) -> list[date]:
        out: list[date] = []
        d = start
        while d <= end:
            if d.weekday() < 5:
                out.append(d)
            d += timedelta(days=1)
        return out

    from calendar import monthrange

    days: list[date] = []
    y, m = 2024, 1
    for _ in range(6):
        last = monthrange(y, m)[1]
        days.extend(weekdays(date(y, m, 1), date(y, m, last)))
        m += 1
        if m > 12:
            m = 1
            y += 1

    windows = generate_wf_windows(days, window_months=3, step_months=1)
    assert len(windows) >= 2
    db = _make_db(tmp_path, days)
    policy = _policy_doc()
    results = run_long_engine_wf_windows(
        db, windows, policy, REPO_ROOT, 200_000.0
    )
    assert len(results) == len(windows)
    for r in results:
        assert r.stage == "long_engine"
        assert r.passed is True
        assert "max_drift_observed_pp" in r.metrics
