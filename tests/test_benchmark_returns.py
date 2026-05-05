"""Tests for static 20/80 benchmark table + PIT-aligned returns."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from data.benchmark_returns import (
    BenchmarkLine,
    align_benchmark_simple_returns,
    asof_close,
    fetch_benchmark_into_db,
    filter_inner_join_returns,
    load_benchmark_table,
    load_close_series_from_db,
)
from data.schema import OHLCVRow
from data.storage import MarketDB


_REPO = Path(__file__).resolve().parent.parent
_BENCH_YAML = _REPO / "config" / "benchmark_mix_20_80.v1.yaml"


def test_load_default_benchmark_yaml_sums_weights() -> None:
    lines = load_benchmark_table(_BENCH_YAML)
    assert len(lines) >= 2
    assert pytest.approx(sum(ln.weight for ln in lines)) == 1.0
    assert any(ln.geo == "AR" for ln in lines)
    assert any(ln.geo == "US" for ln in lines)


def test_asof_close_respects_sorted_bars_and_no_future_bar() -> None:
    jan2 = date(2024, 1, 2)
    jan10 = date(2024, 1, 10)
    jan4 = date(2024, 1, 4)
    bars = [(jan2, 100.0), (jan10, 999.0)]
    # Jan10 bar must not leak into valuations on or before Jan4
    assert asof_close(bars, jan4) == 100.0
    assert asof_close(bars, jan2) == 100.0
    assert asof_close(bars, jan2 - timedelta(days=1)) is None


def test_align_benchmark_weighted_mix() -> None:
    lines = [
        BenchmarkLine("A", 0.2, "AR", "XNYS"),
        BenchmarkLine("B", 0.8, "US", "XNYS"),
    ]
    d0 = date(2024, 1, 2)
    d1 = date(2024, 1, 3)
    d2 = date(2024, 1, 4)
    closes = {
        "A": [(d0, 100.0), (d1, 110.0)],  # +10% step 1
        "B": [(d0, 200.0), (d1, 204.0)],  # +2%
    }
    ends, r = align_benchmark_simple_returns(lines, closes, [d0, d1])
    assert ends == [d1]
    # 0.2*0.10 + 0.8*0.02 = 0.036
    assert r[0] is not None
    assert pytest.approx(r[0], rel=1e-9) == 0.036

    ends2, r2 = align_benchmark_simple_returns(lines, closes, [d0, d1, d2])
    assert ends2 == [d1, d2]
    # second step flat (PIT closes stay at Jan3 closes since no newer bar)
    assert r2[0] is not None
    assert r2[1] is not None
    assert pytest.approx(r2[1]) == 0.0


def test_align_benchmark_none_when_series_starts_mid_window() -> None:
    ln = BenchmarkLine("LATE", 1.0, "US", "XNYS")
    d0, d1, d2 = date(2024, 2, 1), date(2024, 2, 2), date(2024, 2, 5)
    closes = {"LATE": [(d2, 50.0)]}
    ends, r = align_benchmark_simple_returns([ln], closes, [d0, d1, d2])
    assert ends == [d1, d2]
    assert r[0] is None  # no PIT at d0 → d1
    assert r[1] is None  # still no valid PIT at d1 → d2 (need prior close at d1)


def test_filter_inner_join_returns() -> None:
    ends = [date(2024, 1, 3), date(2024, 1, 4)]
    raws: list[float | None] = [None, 0.01]
    d_f, r_f = filter_inner_join_returns(ends, raws)
    assert d_f == [date(2024, 1, 4)]
    assert r_f == [0.01]


def test_load_close_series_from_db_roundtrip(tmp_path) -> None:
    db_path = tmp_path / "m.db"
    db = MarketDB(str(db_path))
    ts = date(2024, 3, 1)
    db.upsert_ohlcv(
        [
            OHLCVRow(
                symbol="SPY",
                ts=ts,
                open=1,
                high=1,
                low=1,
                close=10.5,
                volume=1,
                currency="USD",
                venue="XNYS",
                imputed=False,
            )
        ]
    )
    s = load_close_series_from_db(db, "SPY", "XNYS", ts, ts)
    assert s == [(ts, 10.5)]


def test_fetch_benchmark_into_db_calls_fetch_and_store(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}

    def fake_fetch(symbols_us, symbols_ar, start_date, end_date, db):
        calls["symbols_us"] = symbols_us
        calls["symbols_ar"] = symbols_ar
        calls["start"] = start_date
        calls["end"] = end_date
        from data.fetcher import FetchReport

        return FetchReport([], [], [], [], 0, [])

    monkeypatch.setattr("data.benchmark_returns.fetch_and_store", fake_fetch)
    db = MarketDB(str(tmp_path / "x.db"))
    fetch_benchmark_into_db(db, _BENCH_YAML, date(2024, 1, 2), date(2024, 1, 10))
    assert set(calls["symbols_us"]) == {"ARGT", "SPY"}
    assert calls["symbols_ar"] == []
