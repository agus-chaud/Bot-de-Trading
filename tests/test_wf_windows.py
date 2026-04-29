"""Tests for validation/wf_windows.py — generate_wf_windows."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from validation.wf_windows import generate_wf_windows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _weekdays(start: date, end: date) -> list[date]:
    """All weekdays (Mon–Fri) in [start, end]."""
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _months(start_year: int, start_month: int, n: int) -> list[date]:
    """Weekday trading days for n months starting at start_year/start_month."""
    from calendar import monthrange

    days: list[date] = []
    y, m = start_year, start_month
    for _ in range(n):
        last = monthrange(y, m)[1]
        days.extend(_weekdays(date(y, m, 1), date(y, m, last)))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return days


# ---------------------------------------------------------------------------
# Normal cases
# ---------------------------------------------------------------------------


def test_basic_3m_window_1m_step_returns_correct_count() -> None:
    """6 months of data, window=3, step=1 → 4 windows."""
    trading_days = _months(2024, 1, 6)
    windows = generate_wf_windows(trading_days, window_months=3, step_months=1)
    assert len(windows) == 4


def test_window_size_equals_step_no_overlap() -> None:
    """window=step=2 → windows must not share any day."""
    trading_days = _months(2024, 1, 6)
    windows = generate_wf_windows(trading_days, window_months=2, step_months=2)
    assert len(windows) == 3
    all_days = [d for w in windows for d in w]
    assert len(all_days) == len(set(all_days)), "windows overlap when step==window"


def test_step_less_than_window_produces_overlap() -> None:
    """window=3, step=1 → consecutive windows share window-step months worth of days."""
    trading_days = _months(2024, 1, 4)
    windows = generate_wf_windows(trading_days, window_months=3, step_months=1)
    assert len(windows) == 2
    # windows[0] and windows[1] must share some days
    set0, set1 = set(windows[0]), set(windows[1])
    assert len(set0 & set1) > 0, "expected overlap for step < window"


def test_each_window_contains_only_days_in_its_calendar_range() -> None:
    """All days in each window fall within the expected calendar months."""
    from calendar import monthrange

    trading_days = _months(2024, 3, 6)
    windows = generate_wf_windows(trading_days, window_months=2, step_months=1)

    for idx, window in enumerate(windows):
        win_start_month = 3 + idx
        win_end_month = win_start_month + 1
        win_start_year = 2024 + (win_start_month - 1) // 12
        actual_start_month = (win_start_month - 1) % 12 + 1
        win_end_year = 2024 + (win_end_month - 1) // 12
        actual_end_month = (win_end_month - 1) % 12 + 1
        last_day = monthrange(win_end_year, actual_end_month)[1]
        win_start = date(win_start_year, actual_start_month, 1)
        win_end = date(win_end_year, actual_end_month, last_day)
        for d in window:
            assert win_start <= d <= win_end


def test_window_1m_step_equals_data_length_returns_one_window() -> None:
    """step_months > available range → only first window (if data fits)."""
    trading_days = _months(2024, 1, 3)
    windows = generate_wf_windows(trading_days, window_months=3, step_months=10)
    assert len(windows) == 1


def test_single_window_contains_all_trading_days_in_range() -> None:
    """When window covers entire dataset, the one window contains all days."""
    trading_days = _months(2024, 1, 3)
    windows = generate_wf_windows(trading_days, window_months=3, step_months=1)
    assert len(windows) == 1
    assert set(windows[0]) == set(trading_days)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_trading_days_returns_empty_list() -> None:
    assert generate_wf_windows([], window_months=3, step_months=1) == []


def test_window_months_larger_than_available_returns_empty() -> None:
    trading_days = _months(2024, 1, 2)
    assert generate_wf_windows(trading_days, window_months=4, step_months=1) == []


def test_zero_window_months_raises_value_error() -> None:
    with pytest.raises(ValueError):
        generate_wf_windows(_months(2024, 1, 3), window_months=0, step_months=1)


def test_negative_window_months_raises_value_error() -> None:
    with pytest.raises(ValueError):
        generate_wf_windows(_months(2024, 1, 3), window_months=-1, step_months=1)


def test_zero_step_months_raises_value_error() -> None:
    with pytest.raises(ValueError):
        generate_wf_windows(_months(2024, 1, 3), window_months=2, step_months=0)


def test_negative_step_months_raises_value_error() -> None:
    with pytest.raises(ValueError):
        generate_wf_windows(_months(2024, 1, 3), window_months=2, step_months=-1)


def test_single_month_data_window_1_returns_one_window() -> None:
    trading_days = _months(2024, 5, 1)
    windows = generate_wf_windows(trading_days, window_months=1, step_months=1)
    assert len(windows) == 1
    assert windows[0] == trading_days


def test_no_window_if_last_window_incomplete() -> None:
    """Data covers only 2 full months; window=3 → no complete 3-month window possible."""
    trading_days = _months(2024, 1, 2)
    windows = generate_wf_windows(trading_days, window_months=3, step_months=1)
    assert len(windows) == 0
