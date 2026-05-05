"""Tests for reporting.kpi_v0 (smoke KPIs)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reporting.kpi_v0 import (
    build_kpi_v0_report,
    compute_daily_simple_returns,
    compute_max_drawdown,
    compute_net_return_annualized,
    compute_sharpe_annualized,
    compute_sortino_annualized,
    fifo_kpis_from_trade_rows,
    fifo_roundtrip_pnls_for_motor,
    filter_rows_for_fifo_kpis,
    load_equity_csv,
    sort_fills_by_ts,
    sum_costs_by_motor_from_trades,
    write_report_json,
    write_report_markdown,
)


def test_compute_net_return_annualized_spec_example() -> None:
    # Flat 2 days: N=1, factor = E1/E0, annualized = factor^252 - 1
    r, na = compute_net_return_annualized([100.0, 101.0], trading_days_per_year=252)
    assert na is None
    assert r is not None
    assert pytest.approx(r, rel=1e-9) == (1.01) ** 252 - 1.0


def test_compute_net_return_annualized_na_on_short_series() -> None:
    r, na = compute_net_return_annualized([100.0], trading_days_per_year=252)
    assert r is None
    assert na == "insufficient_history"


def test_compute_sharpe_matches_closed_form_three_returns() -> None:
    equity = [100.0, 101.0, 99.5, 102.0]
    rets, na = compute_daily_simple_returns(equity)
    assert na is None and rets is not None
    sharpe, sn = compute_sharpe_annualized(rets, trading_days_per_year=252)
    assert sn is None and sharpe is not None
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / (len(rets) - 1)
    expected = (252**0.5) * (m / (var**0.5))
    assert sharpe == pytest.approx(expected, rel=1e-9)


def test_compute_sortino_all_non_negative_na() -> None:
    rets = [0.01, 0.02]
    sortino, na = compute_sortino_annualized(rets, trading_days_per_year=252)
    assert sortino is None and na == "no_downside_returns"


def test_compute_sharpe_na_on_zero_variance_returns() -> None:
    sharpe, na = compute_sharpe_annualized([0.0, 0.0, 0.0], trading_days_per_year=252)
    assert sharpe is None and na == "zero_std"


def test_fifo_one_roundtrip_profit_factor_infinite_hit_rate_unit() -> None:
    fills = [
        {"ts": "2024-01-02", "symbol": "SPY", "side": "BUY", "qty": "10", "price": "100", "motor": "short"},
        {"ts": "2024-01-03", "symbol": "SPY", "side": "SELL", "qty": "10", "price": "110", "motor": "short"},
    ]
    blk = fifo_kpis_from_trade_rows(fills)
    assert blk["short"]["hit_rate"] == pytest.approx(1.0)
    assert blk["short"]["profit_factor"] == float("inf")
    assert blk["short"]["n_round_trips"] == 1


def test_fifo_hit_rate_and_profit_factor_two_round_trips() -> None:
    fills = [
        {"ts": "2024-01-02", "symbol": "X", "side": "BUY", "qty": "1", "price": "100", "motor": "long"},
        {"ts": "2024-01-03", "symbol": "X", "side": "SELL", "qty": "1", "price": "120", "motor": "long"},
        {"ts": "2024-01-04", "symbol": "X", "side": "BUY", "qty": "1", "price": "50", "motor": "long"},
        {"ts": "2024-01-05", "symbol": "X", "side": "SELL", "qty": "1", "price": "45", "motor": "long"},
    ]
    blk = fifo_kpis_from_trade_rows(fills)
    assert blk["long"]["hit_rate"] == pytest.approx(0.5)
    assert blk["long"]["profit_factor"] == pytest.approx(20.0 / 5.0)
    assert blk["long"]["n_round_trips"] == 2


def test_filter_rows_fifo_ignores_cost_only_rows_without_qty() -> None:
    fills = [{"ts": "2024-01-02", "symbol": "SPY", "side": "BUY", "motor": "short", "fee": "1.5"}]
    assert filter_rows_for_fifo_kpis(fills) == []


def test_fifo_partial_sell_then_close_one_roundtrip() -> None:
    rows = [
        {"ts": "2024-01-01", "symbol": "Z", "side": "BUY", "qty": "100", "price": "10", "motor": "long"},
        {"ts": "2024-01-02", "symbol": "Z", "side": "SELL", "qty": "60", "price": "12", "motor": "long"},
        {"ts": "2024-01-03", "symbol": "Z", "side": "SELL", "qty": "40", "price": "9", "motor": "long"},
    ]
    pnls = fifo_roundtrip_pnls_for_motor(sort_fills_by_ts(rows), "long")
    assert len(pnls) == 1
    expected = (60 * 12 + 40 * 9) - 100 * 10
    assert pnls[0] == pytest.approx(expected)


def test_build_report_segments_include_sharpe_and_fill_metrics(tmp_path: Path) -> None:
    eq = tmp_path / "eq.csv"
    eq.write_text(
        "ts,equity_total,equity_short,equity_long,cash,costs_day\n"
        "2024-01-02,10000,3000,7000,1000,0\n"
        "2024-01-03,10100,3000,7100,1050,0\n"
        "2024-01-04,10050,3050,7000,1000,0\n",
        encoding="utf-8",
    )
    tr = tmp_path / "tr.csv"
    tr.write_text(
        "ts,symbol,side,qty,price,motor,fee\n"
        "2024-01-02,X,BUY,1,100,long,0\n"
        "2024-01-03,X,SELL,1,110,long,0\n",
        encoding="utf-8",
    )
    rep = build_kpi_v0_report(eq, tr)
    assert rep.segment_total["sharpe_annualized"] is not None
    assert rep.segment_total["hit_rate"] == pytest.approx(1.0)
    assert rep.segment_total["profit_factor"] == float("inf")
    assert rep.segment_short["n_round_trips"] == 0
    assert rep.segment_long["n_round_trips"] == 1
    dj = tmp_path / "out.json"
    write_report_json(rep, dj)
    payload = json.loads(dj.read_text(encoding="utf-8"))
    assert payload["segment"]["total"]["profit_factor"] == "inf"


def test_compute_max_drawdown_negative_fraction() -> None:
    # Running peak 110, trough 80 → 80/110 - 1
    mdd = compute_max_drawdown([100.0, 110.0, 80.0, 90.0])
    assert pytest.approx(mdd, rel=1e-9) == 80.0 / 110.0 - 1.0


def test_costs_from_trades_by_motor(tmp_path: Path) -> None:
    fills = tmp_path / "fills.csv"
    fills.write_text(
        "ts,symbol,side,motor,fee\n"
        "2024-01-02,SPY,BUY,short,1.5\n"
        "2024-01-02,QQQ,BUY,long,2.25\n",
        encoding="utf-8",
    )
    from reporting.kpi_v0 import load_trades_csv

    rows = load_trades_csv(fills)
    c = sum_costs_by_motor_from_trades(rows)
    assert c["short"] == pytest.approx(1.5)
    assert c["long"] == pytest.approx(2.25)


def test_build_report_with_split_cost_columns_in_equity(tmp_path: Path) -> None:
    eq = tmp_path / "eq.csv"
    eq.write_text(
        "ts,equity_total,equity_short,equity_long,cash,costs_day,costs_day_short,costs_day_long\n"
        "2024-01-02,10000,3000,7000,500,0,1,2\n"
        "2024-01-03,10050,3050,7000,450,0,0.5,0\n",
        encoding="utf-8",
    )
    rep = build_kpi_v0_report(eq, trades_path=None)
    assert rep.costs_by_motor is not None
    assert rep.costs_by_motor["short"] == pytest.approx(1.5)
    assert rep.costs_by_motor["long"] == pytest.approx(2.0)
    assert rep.costs_na_reason is None


def test_build_report_writes_json_and_md(tmp_path: Path) -> None:
    eq = tmp_path / "eq.csv"
    eq.write_text(
        "ts,equity_total,equity_short,equity_long,cash,costs_day\n"
        "2024-01-02,100000,30000,70000,10000,0\n"
        "2024-01-03,101000,30000,71000,10000,0\n",
        encoding="utf-8",
    )
    tr = tmp_path / "tr.csv"
    tr.write_text("ts,symbol,motor,fee\n2024-01-02,X,short,10\n2024-01-03,Y,long,5\n")

    rep = build_kpi_v0_report(eq, tr)
    j = tmp_path / "out.json"
    m = tmp_path / "out.md"
    write_report_json(rep, j)
    write_report_markdown(rep, m)

    txt = j.read_text(encoding="utf-8")
    assert "report_kpis_v2" in txt
    assert '"costs_by_motor"' in txt
    md = m.read_text(encoding="utf-8")
    assert "# KPI report" in md
    assert "Retorno neto anualizado" in md


def test_load_equity_csv_sorts_by_ts(tmp_path: Path) -> None:
    from textwrap import dedent

    p = tmp_path / "eq_order.csv"
    p.write_text(
        dedent(
            """\
        ts,equity_total,equity_short,equity_long,cash,costs_day
        2024-01-04,3,1,2,0,0
        2024-01-02,1,1,0,0,0
        2024-01-03,2,1,1,0,0
        """
        ),
        encoding="utf-8",
    )
    rows, _ = load_equity_csv(p)
    assert [r["equity_total"] for r in rows] == ["1", "2", "3"]


def test_cli_script_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[1]
    eq = tmp_path / "eq.csv"
    eq.write_text(
        "ts,equity_total,equity_short,equity_long,cash,costs_day_short,costs_day_long\n"
        "2024-01-02,100,30,70,10,2,3\n",
        encoding="utf-8",
    )
    j = tmp_path / "k.json"
    md = tmp_path / "k.md"
    monkeypatch.chdir(repo)
    r = subprocess.run(
        [
            sys.executable,
            str(repo / "scripts" / "report_kpis.py"),
            "--equity",
            str(eq),
            "--out-json",
            str(j),
            "--out-md",
            str(md),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert j.is_file()


def test_mandate_drift_zero_on_exact_30_70_and_geo(tmp_path: Path) -> None:
    eq = tmp_path / "eq.csv"
    eq.write_text(
        "ts,equity_total,equity_short,equity_long,cash,costs_day_short,costs_day_long,"
        "equity_ar,equity_us\n"
        "2024-01-02,10000,3000,7000,0,0,0,2000,8000\n"
        "2024-01-03,10000,3000,7000,0,0,0,2000,8000\n",
        encoding="utf-8",
    )
    rep = build_kpi_v0_report(eq, trades_path=None, policy_path=None)
    assert rep.mandate_drift is not None
    snap = rep.mandate_drift["snapshot_last_ts"]
    assert snap["drift_short_pp"] == pytest.approx(0.0)
    assert snap["drift_long_pp"] == pytest.approx(0.0)
    assert snap["drift_ar_pp"] == pytest.approx(0.0)
    assert snap["drift_us_pp"] == pytest.approx(0.0)


def test_mandate_drift_geo_na_when_missing_columns(tmp_path: Path) -> None:
    eq = tmp_path / "eq.csv"
    eq.write_text(
        "ts,equity_total,equity_short,equity_long,cash,costs_day_short,costs_day_long\n"
        "2024-01-02,10000,3500,6500,0,0,0\n",
        encoding="utf-8",
    )
    rep = build_kpi_v0_report(eq, trades_path=None, policy_path=None)
    snap = rep.mandate_drift["snapshot_last_ts"]
    assert snap["drift_short_pp"] == pytest.approx(5.0)
    assert snap["drift_long_pp"] == pytest.approx(-5.0)
    assert snap.get("geo_na_reason") == "missing_equity_ar_equity_us_columns"


def test_mandate_drift_bands_informational_outside(tmp_path: Path) -> None:
    meta = tmp_path / "meta.yaml"
    meta.write_text(
        "mandate_drift_bands_pp:\n  short: 3\n  long: 3\n  AR: 2\n  US: 2\n",
        encoding="utf-8",
    )
    eq = tmp_path / "eq.csv"
    eq.write_text(
        "ts,equity_total,equity_short,equity_long,equity_ar,equity_us,"
        "cash,costs_day_short,costs_day_long\n"
        "2024-01-04,10000,4000,6000,2500,7500,0,0,0\n",
        encoding="utf-8",
    )
    rep = build_kpi_v0_report(eq, trades_path=None, metadata_path=meta, policy_path=None)
    snap = rep.mandate_drift["snapshot_last_ts"]
    assert snap["outside_band_axes"] == ["short", "long", "AR", "US"]
