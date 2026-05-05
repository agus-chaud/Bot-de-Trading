#!/usr/bin/env python3
"""Regenera fixtures KPI golden de 60 d\u00edas (Fase 5, plan Fase 5 \u00edtem 9).

Uso (manual, NO se invoca desde CI):

    python scripts/regenerate_kpi_golden_fixtures.py

Produce, en ``tests/fixtures/kpi_golden/``:

    equity_60d.csv             \u2014 serie diaria con equity total/short/long, geo AR/US y costos por motor
    trades_60d.csv             \u2014 fills con motor expl\u00edcito (round-trips FIFO short y long)
    benchmark_returns_60d.csv  \u2014 retornos benchmark alineables por ts
    metadata.yaml              \u2014 spec_id, run_id, trading_days_per_year, weights/geo expl\u00edcitos
    expected_kpis.json         \u2014 valores golden que el test de regresi\u00f3n usa para comparar

El dataset es **completamente determin\u00edstico** (seed fijo, sin random no controlado).
Cualquier cambio aqu\u00ed obliga a justificar una nueva versi\u00f3n del spec o explicar por qu\u00e9
se acepta el drift en los KPIs (ver ``docs/kpi_report_spec.v1.md``).
"""

from __future__ import annotations

import csv
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from reporting.kpi_v0 import (  # noqa: E402
    _sanitize_json_values,
    build_kpi_v0_report,
)


FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "kpi_golden"
N_TRADING_DAYS = 60
START_DATE = date(2024, 1, 2)


def _trading_days(n: int, start: date) -> list[date]:
    """``n`` d\u00edas h\u00e1biles consecutivos (lunes a viernes) desde ``start``."""
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _det_returns(n: int, *, amplitude: float, period: int, drift: float) -> list[float]:
    """Retornos diarios determin\u00edsticos: drift + onda senoidal de amplitud y per\u00edodo fijos."""
    return [drift + amplitude * math.sin(2.0 * math.pi * i / period) for i in range(n)]


def build_fixtures() -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    """Construye filas para equity, trades y benchmark de forma determin\u00edstica."""
    days = _trading_days(N_TRADING_DAYS, START_DATE)

    rets_total = _det_returns(N_TRADING_DAYS, amplitude=0.012, period=11, drift=0.0006)
    rets_short = _det_returns(N_TRADING_DAYS, amplitude=0.018, period=7, drift=0.0004)
    rets_long = _det_returns(N_TRADING_DAYS, amplitude=0.008, period=13, drift=0.0007)
    rets_bench = _det_returns(N_TRADING_DAYS, amplitude=0.010, period=11, drift=0.0005)

    eq_total = [10000.0]
    eq_short = [3000.0]
    eq_long = [7000.0]
    for i in range(1, N_TRADING_DAYS):
        eq_total.append(eq_total[-1] * (1.0 + rets_total[i]))
        eq_short.append(eq_short[-1] * (1.0 + rets_short[i]))
        eq_long.append(eq_long[-1] * (1.0 + rets_long[i]))

    eq_rows: list[dict[str, str]] = []
    for i, d in enumerate(days):
        et = eq_total[i]
        es = eq_short[i]
        el = eq_long[i]
        ear = round(et * 0.20, 6)
        eus = round(et * 0.80, 6)
        cs = 1.25 if i % 5 == 0 else 0.0
        cl = 0.85 if i % 7 == 0 else 0.0
        eq_rows.append(
            {
                "ts": d.isoformat(),
                "equity_total": f"{et:.6f}",
                "equity_short": f"{es:.6f}",
                "equity_long": f"{el:.6f}",
                "equity_ar": f"{ear:.6f}",
                "equity_us": f"{eus:.6f}",
                "cash": f"{(et - es - el):.6f}",
                "costs_day": f"{(cs + cl):.6f}",
                "costs_day_short": f"{cs:.6f}",
                "costs_day_long": f"{cl:.6f}",
            }
        )

    trades = _build_trades(days)
    bench = [
        {"ts": d.isoformat(), "benchmark_return": f"{rets_bench[i]:.10f}"}
        for i, d in enumerate(days)
    ]
    return eq_rows, trades, bench


def _build_trades(days: list[date]) -> list[dict[str, str]]:
    """Operaciones determin\u00edsticas: 2 round-trips short (1 ganador, 1 perdedor) + 2 long."""
    plan = [
        (5, "AAA", "BUY", 10, 100.0, "short", 1.0),
        (12, "AAA", "SELL", 10, 110.0, "short", 1.1),
        (18, "BBB", "BUY", 5, 200.0, "short", 1.0),
        (25, "BBB", "SELL", 5, 190.0, "short", 0.95),
        (8, "ETF1", "BUY", 4, 250.0, "long", 1.5),
        (35, "ETF1", "SELL", 4, 270.0, "long", 1.62),
        (15, "ETF2", "BUY", 6, 150.0, "long", 1.0),
        (45, "ETF2", "SELL", 6, 145.0, "long", 0.97),
    ]
    out: list[dict[str, str]] = []
    for idx, sym, side, qty, price, motor, fee in plan:
        out.append(
            {
                "ts": days[idx].isoformat(),
                "symbol": sym,
                "side": side,
                "qty": str(qty),
                "price": f"{price:.4f}",
                "motor": motor,
                "fee": f"{fee:.4f}",
            }
        )
    out.sort(key=lambda r: (r["ts"], r["symbol"]))
    return out


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_metadata(path: Path) -> None:
    meta = {
        "spec_id": "rpt_kpi.v1",
        "run_id": "kpi_golden_60d",
        "reporting_ccy": "USD",
        "trading_days_per_year": 252,
        "weights": {"short": 0.30, "long": 0.70},
        "geo": {"AR": 0.20, "US": 0.80},
        "mandate_drift_bands_pp": {"short": 5.0, "long": 5.0, "AR": 3.0, "US": 3.0},
    }
    path.write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")


def main() -> int:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    eq, tr, br = build_fixtures()

    eq_path = FIXTURES_DIR / "equity_60d.csv"
    tr_path = FIXTURES_DIR / "trades_60d.csv"
    br_path = FIXTURES_DIR / "benchmark_returns_60d.csv"
    meta_path = FIXTURES_DIR / "metadata.yaml"
    golden_path = FIXTURES_DIR / "expected_kpis.json"

    _write_csv(eq_path, eq)
    _write_csv(tr_path, tr)
    _write_csv(br_path, br)
    _write_metadata(meta_path)

    rep = build_kpi_v0_report(
        eq_path,
        tr_path,
        metadata_path=meta_path,
        policy_path=None,
        benchmark_returns_path=br_path,
    )
    payload = _sanitize_json_values(rep.to_json_dict())
    payload.pop("run_id", None)
    long_seg = payload.get("segment", {}).get("long")
    if isinstance(long_seg, dict):
        long_seg.pop("mdd_12m_rolling_series", None)
    drift = payload.get("mandate_drift")
    if isinstance(drift, dict):
        drift.pop("series", None)

    golden_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("regenerated:")
    for p in (eq_path, tr_path, br_path, meta_path, golden_path):
        print(" -", p.relative_to(REPO_ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
