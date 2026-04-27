"""Behavior tests for data.normalizer.normalize()."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pytest

from data.normalizer import normalize
from data.schema import OHLCVRow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row(
    ts: date,
    close: float = 100.0,
    open_: float = 99.0,
    high: float = 101.0,
    low: float = 98.0,
    volume: float = 1000.0,
    symbol: str = "SPY",
    imputed: bool = False,
) -> OHLCVRow:
    return OHLCVRow(
        symbol=symbol,
        ts=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        currency="USD",
        venue="XNYS",
        imputed=imputed,
    )


def _workweek(start: date, n: int = 5) -> list[date]:
    """Return n consecutive weekdays starting from start."""
    days: list[date] = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


MON = date(2024, 1, 8)  # Monday


# ---------------------------------------------------------------------------
# Edge: empty input
# ---------------------------------------------------------------------------

class TestEmptyInput:
    def test_empty_list_returns_empty(self):
        # Arrange
        rows: list[OHLCVRow] = []
        calendar: set[date] = {MON}

        # Act
        result = normalize(rows, calendar)

        # Assert
        assert result == []

    def test_empty_calendar_with_valid_rows_returns_rows_unchanged(self):
        rows = [_row(MON)]
        result = normalize(rows, set())
        assert len(result) == 1
        assert result[0].ts == MON


# ---------------------------------------------------------------------------
# Passthrough: valid rows, no gaps
# ---------------------------------------------------------------------------

class TestValidRowsNoGaps:
    def test_valid_rows_within_calendar_returned_unchanged(self):
        # Arrange
        days = _workweek(MON, 3)
        rows = [_row(d) for d in days]
        calendar = set(days)

        # Act
        result = normalize(rows, calendar)

        # Assert
        assert len(result) == 3
        assert all(not r.imputed for r in result)

    def test_output_is_sorted_by_ts_ascending(self):
        days = _workweek(MON, 3)
        rows = [_row(d) for d in reversed(days)]  # feed in reverse order
        calendar = set(days)

        result = normalize(rows, calendar)

        dates = [r.ts for r in result]
        assert dates == sorted(dates)


# ---------------------------------------------------------------------------
# Invalid prices → dropped
# ---------------------------------------------------------------------------

class TestInvalidPriceDropped:
    def test_zero_close_is_dropped(self, caplog):
        days = _workweek(MON, 3)
        rows = [_row(days[0]), _row(days[1], close=0.0), _row(days[2])]

        with caplog.at_level(logging.WARNING, logger="data.normalizer"):
            result = normalize(rows, set(days))

        assert len(result) == 2
        assert days[1] not in {r.ts for r in result}
        assert "invalid_price" in caplog.text

    def test_negative_open_is_dropped(self, caplog):
        days = _workweek(MON, 2)
        rows = [_row(days[0], open_=-1.0), _row(days[1])]

        with caplog.at_level(logging.WARNING, logger="data.normalizer"):
            result = normalize(rows, set(days))

        assert len(result) == 1
        assert result[0].ts == days[1]


# ---------------------------------------------------------------------------
# Invalid volume → dropped
# ---------------------------------------------------------------------------

class TestInvalidVolumeDropped:
    def test_zero_volume_is_dropped(self, caplog):
        days = _workweek(MON, 2)
        rows = [_row(days[0], volume=0.0), _row(days[1])]

        with caplog.at_level(logging.WARNING, logger="data.normalizer"):
            result = normalize(rows, set(days))

        assert len(result) == 1
        assert result[0].ts == days[1]
        assert "invalid_volume" in caplog.text

    def test_negative_volume_is_dropped(self, caplog):
        days = _workweek(MON, 2)
        rows = [_row(days[0], volume=-500.0), _row(days[1])]

        with caplog.at_level(logging.WARNING, logger="data.normalizer"):
            result = normalize(rows, set(days))

        assert len(result) == 1


# ---------------------------------------------------------------------------
# Price outlier (rolling median × 10) → dropped
# ---------------------------------------------------------------------------

class TestPriceOutlierDropped:
    def test_close_above_10x_rolling_median_is_dropped(self, caplog):
        # Arrange: 5 normal rows then one spike that is 11× the median.
        days = _workweek(MON, 7)
        normal_rows = [_row(d, close=100.0) for d in days[:5]]
        spike_row = _row(days[5], close=1100.0)  # 11× median of 100
        last_row = _row(days[6], close=100.0)
        rows = normal_rows + [spike_row, last_row]

        with caplog.at_level(logging.WARNING, logger="data.normalizer"):
            result = normalize(rows, set(days))

        assert days[5] not in {r.ts for r in result}
        assert "price_outlier" in caplog.text

    def test_close_below_0_1x_rolling_median_is_dropped(self, caplog):
        days = _workweek(MON, 7)
        normal_rows = [_row(d, close=100.0) for d in days[:5]]
        dip_row = _row(days[5], close=0.5)  # 0.005× median of 100 → below 0.1×
        last_row = _row(days[6], close=100.0)
        rows = normal_rows + [dip_row, last_row]

        with caplog.at_level(logging.WARNING, logger="data.normalizer"):
            result = normalize(rows, set(days))

        assert days[5] not in {r.ts for r in result}
        assert "price_outlier" in caplog.text

    def test_normal_close_within_threshold_is_kept(self):
        days = _workweek(MON, 7)
        rows = [_row(d, close=100.0) for d in days[:5]]
        rows.append(_row(days[5], close=900.0))  # 9× median — just under limit

        result = normalize(rows, set(days[:6]))

        assert days[5] in {r.ts for r in result}


# ---------------------------------------------------------------------------
# Forward-fill: 1-day gap
# ---------------------------------------------------------------------------

class TestForwardFillOneDay:
    def test_single_missing_calendar_day_is_imputed(self, caplog):
        # Arrange: rows for Mon & Wed, Tuesday is in calendar but missing.
        mon, tue, wed = _workweek(MON, 3)
        rows = [_row(mon), _row(wed)]
        calendar = {mon, tue, wed}

        with caplog.at_level(logging.INFO, logger="data.normalizer"):
            result = normalize(rows, calendar)

        # Assert
        assert len(result) == 3
        imputed = next(r for r in result if r.ts == tue)
        assert imputed.imputed is True
        assert imputed.volume == 0.0
        assert imputed.close == _row(mon).close  # forward-filled from Monday

    def test_imputed_row_carries_source_ohlc(self):
        mon, tue, wed = _workweek(MON, 3)
        src = _row(mon, close=123.45, open_=120.0, high=125.0, low=119.0)
        rows = [src, _row(wed)]
        calendar = {mon, tue, wed}

        result = normalize(rows, calendar)

        imputed = next(r for r in result if r.ts == tue)
        assert imputed.open == 120.0
        assert imputed.high == 125.0
        assert imputed.low == 119.0
        assert imputed.close == 123.45


# ---------------------------------------------------------------------------
# Forward-fill: 3-day gap (max allowed)
# ---------------------------------------------------------------------------

class TestForwardFillThreeDays:
    def test_three_day_gap_all_imputed(self, caplog):
        days = _workweek(MON, 5)  # Mon–Fri
        mon, tue, wed, thu, fri = days
        rows = [_row(mon), _row(fri)]
        calendar = set(days)

        with caplog.at_level(logging.INFO, logger="data.normalizer"):
            result = normalize(rows, calendar)

        assert len(result) == 5
        for d in (tue, wed, thu):
            r = next(x for x in result if x.ts == d)
            assert r.imputed is True
            assert r.volume == 0.0


# ---------------------------------------------------------------------------
# Forward-fill: gap > 3 days → NOT imputed
# ---------------------------------------------------------------------------

class TestGapTooLargeNotImputed:
    def test_four_day_gap_logs_gap_too_large_and_leaves_empty(self, caplog):
        # Mon + 4 missing days + Sat (outside calendar) → need 5 consecutive weekdays gap.
        # Build a week with Mon present, then Tue–Fri missing, then following Mon present.
        start = MON
        days = _workweek(start, 7)  # Mon week1 through Tue week2
        mon1 = days[0]
        missing_days = days[1:5]  # Tue–Fri (4 days)
        mon2 = days[5]

        rows = [_row(mon1), _row(mon2)]
        calendar = set(days[:6])  # Mon1 + Tue–Fri + Mon2

        with caplog.at_level(logging.WARNING, logger="data.normalizer"):
            result = normalize(rows, calendar)

        result_dates = {r.ts for r in result}
        for d in missing_days:
            assert d not in result_dates, f"{d} should NOT be imputed"
        assert "gap_too_large" in caplog.text

    def test_exactly_three_day_gap_does_not_log_gap_too_large(self, caplog):
        days = _workweek(MON, 5)
        mon, tue, wed, thu, fri = days
        rows = [_row(mon), _row(fri)]
        calendar = set(days)

        with caplog.at_level(logging.WARNING, logger="data.normalizer"):
            result = normalize(rows, calendar)

        assert "gap_too_large" not in caplog.text
        assert len(result) == 5


# ---------------------------------------------------------------------------
# Sorting guarantee
# ---------------------------------------------------------------------------

class TestOutputOrdering:
    def test_output_always_sorted_ascending_by_ts(self):
        days = _workweek(MON, 5)
        rows = [_row(d) for d in reversed(days)]
        calendar = set(days)

        result = normalize(rows, calendar)

        dates = [r.ts for r in result]
        assert dates == sorted(dates)

    def test_sorting_holds_when_imputed_rows_are_mixed_in(self):
        days = _workweek(MON, 5)
        mon, tue, wed, thu, fri = days
        rows = [_row(mon), _row(thu), _row(fri)]
        calendar = set(days)

        result = normalize(rows, calendar)

        dates = [r.ts for r in result]
        assert dates == sorted(dates)
