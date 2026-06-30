"""Walk-forward window generator for validation workflows."""

from __future__ import annotations

from calendar import monthrange
from datetime import date


def generate_wf_windows(
    trading_days: list[date],
    window_months: int,
    step_months: int,
) -> list[list[date]]:
    """Return rolling walk-forward windows over a sorted list of trading days.

    Each window spans exactly `window_months` calendar months. The first window
    starts at the first day of the month containing trading_days[0]. Subsequent
    windows advance by `step_months` calendar months.

    A window is only included if it covers exactly `window_months` full months
    worth of data (i.e. the end month is fully contained in trading_days).
    """
    if window_months <= 0 or step_months <= 0:
        raise ValueError("window_months and step_months must be > 0")

    if not trading_days:
        return []

    min_day = min(trading_days)
    max_day = max(trading_days)

    def _add_months(y: int, m: int, months: int) -> tuple[int, int]:
        total = (m - 1) + months
        return y + total // 12, total % 12 + 1

    def _window_end(start_year: int, start_month: int) -> date:
        end_year, end_month = _add_months(start_year, start_month, window_months - 1)
        last_day = monthrange(end_year, end_month)[1]
        return date(end_year, end_month, last_day)

    max_year, max_month = max_day.year, max_day.month

    windows: list[list[date]] = []
    cur_year, cur_month = min_day.year, min_day.month

    while True:
        end_year, end_month = _add_months(cur_year, cur_month, window_months - 1)
        if (end_year, end_month) > (max_year, max_month):
            break

        win_start = date(cur_year, cur_month, 1)
        win_end = _window_end(cur_year, cur_month)
        window = [d for d in trading_days if win_start <= d <= win_end]
        windows.append(window)

        cur_year, cur_month = _add_months(cur_year, cur_month, step_months)

    return windows
