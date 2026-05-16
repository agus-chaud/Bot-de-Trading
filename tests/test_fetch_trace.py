"""Tests for source attribution in fetch_log extras."""

from __future__ import annotations

import json
from datetime import date

from data.fetch_trace import (
    SOURCE_BYMA,
    SOURCE_IOL,
    SOURCE_MIXED,
    SOURCE_YFINANCE,
    SymbolFetchTrace,
    apply_source_attribution,
    trace_us_result,
)


def test_apply_source_attribution_marks_mixed_when_partial() -> None:
    trace = SymbolFetchTrace(
        symbol="GGAL",
        venue="XBUE",
        start_date=date(2024, 3, 4),
        end_date=date(2024, 3, 8),
    )
    apply_source_attribution(
        trace,
        {SOURCE_IOL: 2, SOURCE_BYMA: 1},
        partial_fallback=True,
    )
    assert trace.source == SOURCE_MIXED
    assert trace.extra["effective_source"] == SOURCE_MIXED
    assert trace.extra["rows_by_source"] == {SOURCE_IOL: 2, SOURCE_BYMA: 1}
    assert trace.extra["partial_fallback"] is True


def test_trace_us_result_includes_rows_by_source() -> None:
    trace = trace_us_result("SPY", date(2024, 1, 2), date(2024, 1, 5), [{"x": 1}])
    extra = json.loads(trace.to_log_entry()["extra"])
    assert extra["rows_by_source"] == {SOURCE_YFINANCE: 1}
    assert extra["effective_source"] == SOURCE_YFINANCE
    assert extra["partial_fallback"] is False
