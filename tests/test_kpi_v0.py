"""Tests for reporting.kpi_v0 (smoke KPIs)."""

from __future__ import annotations

from pathlib import Path

import pytest

from reporting.kpi_v0 import (
    build_kpi_v0_report,
    compute_max_drawdown,
    compute_net_return_annualized,
    load_equity_csv,
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
    assert "report_kpis_v0" in txt
    assert '"costs_by_motor"' in txt
    md = m.read_text(encoding="utf-8")
    assert "# KPI report v0" in md
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
