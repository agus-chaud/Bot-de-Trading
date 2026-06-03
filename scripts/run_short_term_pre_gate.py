#!/usr/bin/env python3
"""Ejecuta validación pre-gate walk-forward del bloque corto (exit 0 = pass, 1 = fail).

Por defecto usa datos reales desde SQLite (`data/market.db`).
Opcionalmente se puede usar modo demo con `--demo`.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core_sim.short_term_day_runner import load_merged_whitelist  # noqa: E402
from core_sim.short_term_pre_gate import run_short_term_pre_gate  # noqa: E402
from data.storage import MarketDB  # noqa: E402
from data.venue_policy import pick_venue_bar, venues_for_market  # noqa: E402


def _weekdays_from(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _demo_bars(days: list[date]) -> dict[date, dict[str, dict[str, float]]]:
    bars: dict[date, dict[str, dict[str, float]]] = {}
    for i, d in enumerate(days):
        spy_close = 100.0 + float(i) * 0.35
        qqq_close = 200.0 - float(i) * 0.2
        bars[d] = {
            "SPY": {
                "open": spy_close,
                "high": spy_close + 0.5,
                "low": spy_close - 0.5,
                "close": spy_close,
                "volume": 80_000_000.0,
            },
            "QQQ": {
                "open": qqq_close,
                "high": qqq_close + 0.5,
                "low": qqq_close - 0.5,
                "close": qqq_close,
                "volume": 30_000_000.0,
            },
        }
    return bars


def _trading_days_from_db(db: MarketDB, ref: date, lookback: int) -> list[date]:
    """Obtiene hasta `lookback` sesiones XNYS <= `ref` desde la DB."""
    try:
        cursor = db._conn.execute(
            "SELECT ts FROM calendars WHERE venue = 'XNYS' ORDER BY ts ASC"
        )
        sessions = [date.fromisoformat(row["ts"]) for row in cursor.fetchall()]
    except Exception:
        sessions = []

    # Fallback: si no hay calendario cargado, usar fechas existentes en OHLCV.
    if not sessions:
        cursor = db._conn.execute("SELECT DISTINCT ts FROM ohlcv ORDER BY ts ASC")
        sessions = [date.fromisoformat(row["ts"]) for row in cursor.fetchall()]

    eligible = [d for d in sessions if d <= ref]
    if lookback <= 0:
        return eligible
    return eligible[-lookback:] if len(eligible) >= lookback else eligible


def _bars_from_db(
    db: MarketDB,
    trading_days: list[date],
    merged_whitelist: dict[str, str],
) -> dict[date, dict[str, dict[str, float]]]:
    """Carga OHLCV del período y lo indexa como date -> symbol -> bar.

    Política de venue (ver :mod:`data.venue_policy`): cada símbolo se lee SOLO del
    venue que matchea su market tag en ``merged_whitelist`` — US desde XNYS/US (USD),
    AR desde XBUE (ARS). Evita el bug last-write-wins que mezclaba USD y ARS en los
    duales. Con XNYS y US legacy el mismo día gana XNYS; si no hay barra del venue
    correcto ese día, el símbolo se omite (no se sustituye). Símbolos fuera de la
    whitelist se ignoran (sin tag no hay venue definido).
    """
    if not trading_days:
        return {}

    start = min(trading_days).isoformat()
    end = max(trading_days).isoformat()

    cursor = db._conn.execute(
        """
        SELECT symbol, ts, open, high, low, close, volume, venue
        FROM ohlcv
        WHERE ts BETWEEN ? AND ?
        ORDER BY ts ASC
        """,
        (start, end),
    )

    staged: dict[date, dict[str, dict[str, dict[str, float]]]] = {}
    for row in cursor.fetchall():
        symbol = row["symbol"]
        market = merged_whitelist.get(symbol)
        if market is None:
            continue
        if row["venue"] not in venues_for_market(market):
            continue
        day = date.fromisoformat(row["ts"])
        staged.setdefault(day, {}).setdefault(symbol, {})[row["venue"]] = {
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
        }

    bars_by_date: dict[date, dict[str, dict[str, float]]] = {}
    for day, by_symbol in staged.items():
        for symbol, bars_by_venue in by_symbol.items():
            bar = pick_venue_bar(merged_whitelist[symbol], bars_by_venue)
            if bar is not None:
                bars_by_date.setdefault(day, {})[symbol] = bar

    return bars_by_date


def _window_row(index: int, metrics: dict[str, Any], passed: bool, violations: list[str]) -> dict[str, Any]:
    trading_days_raw = metrics.get("trading_days") or ()
    trading_days = [str(x) for x in trading_days_raw]
    start_date = trading_days[0] if trading_days else ""
    end_date = trading_days[-1] if trading_days else ""
    return {
        "window_index": index,
        "start_date": start_date,
        "end_date": end_date,
        "n_days": int(metrics.get("n_days", 0)),
        "n_fills": int(metrics.get("n_fills", 0)),
        "start_equity": float(metrics.get("start_equity", 0.0)),
        "end_equity": float(metrics.get("end_equity", 0.0)),
        "return_pct": float(metrics.get("total_return_pct", 0.0)) * 100.0,
        "max_drawdown_pct": float(metrics.get("max_drawdown_pct", 0.0)) * 100.0,
        "short_monthly_drawdown_pct": float(metrics.get("min_short_monthly_drawdown", 0.0)) * 100.0,
        "turnover_annualized": float(metrics.get("turnover_annualized_proxy", 0.0)),
        "total_fees": float(metrics.get("total_fees", 0.0)),
        "fee_ratio_pct_of_initial": float(metrics.get("fee_ratio_of_initial", 0.0)) * 100.0,
        "entries_blocked_by_rsi": int(metrics.get("entries_blocked_by_rsi", 0)),
        "exits_by_rsi": int(metrics.get("exits_by_rsi", 0)),
        "exits_by_stop_loss": int(metrics.get("exits_by_stop_loss", 0)),
        "passed": bool(passed),
        "violations": "; ".join(violations),
    }


def _aggregate_windows(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        group_id = str(row.get(key) or "")
        if group_id:
            groups[group_id].append(row)

    out: list[dict[str, Any]] = []
    for group_id in sorted(groups):
        items = groups[group_id]
        count = len(items)
        out.append(
            {
                "group": group_id,
                "windows": count,
                "passed_windows": sum(1 for x in items if bool(x["passed"])),
                "avg_return_pct": sum(float(x["return_pct"]) for x in items) / count,
                "avg_max_drawdown_pct": sum(float(x["max_drawdown_pct"]) for x in items) / count,
                "avg_turnover_annualized": sum(float(x["turnover_annualized"]) for x in items) / count,
                "avg_fee_ratio_pct_of_initial": (
                    sum(float(x["fee_ratio_pct_of_initial"]) for x in items) / count
                ),
                "total_entries_blocked_by_rsi": sum(int(x["entries_blocked_by_rsi"]) for x in items),
                "total_exits_by_rsi": sum(int(x["exits_by_rsi"]) for x in items),
                "total_exits_by_stop_loss": sum(int(x["exits_by_stop_loss"]) for x in items),
                "total_fees": sum(float(x["total_fees"]) for x in items),
            }
        )
    return out


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "window_index",
        "start_date",
        "end_date",
        "start_month",
        "start_iso_week",
        "n_days",
        "n_fills",
        "start_equity",
        "end_equity",
        "return_pct",
        "max_drawdown_pct",
        "short_monthly_drawdown_pct",
        "turnover_annualized",
        "total_fees",
        "fee_ratio_pct_of_initial",
        "entries_blocked_by_rsi",
        "exits_by_rsi",
        "exits_by_stop_loss",
        "passed",
        "violations",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    p = argparse.ArgumentParser(description="Short-term walk-forward pre-gate")
    p.add_argument(
        "--policy",
        type=Path,
        default=REPO_ROOT / "config" / "policy.v1.yaml",
        help="Ruta a policy YAML",
    )
    p.add_argument(
        "--demo-days",
        type=int,
        default=90,
        help="Con --demo: cantidad de días hábiles sintéticos SPY/QQQ.",
    )
    p.add_argument(
        "--demo",
        action="store_true",
        help="Usar datos sintéticos demo en vez de DB real.",
    )
    p.add_argument(
        "--db",
        type=Path,
        default=REPO_ROOT / "data" / "market.db",
        help="Ruta SQLite con OHLCV real (default: data/market.db).",
    )
    p.add_argument(
        "--reference-date",
        type=str,
        default=None,
        help="Fecha de referencia ISO YYYY-MM-DD para recortar sesiones (default: hoy).",
    )
    p.add_argument(
        "--lookback-trading-days",
        type=int,
        default=None,
        help="Cantidad de sesiones hacia atrás para evaluar (default: validation_wf.lookback_trading_days).",
    )
    p.add_argument(
        "--out-json",
        type=Path,
        default=None,
        help="Ruta para exportar resumen JSON por ventana.",
    )
    p.add_argument(
        "--out-csv",
        type=Path,
        default=None,
        help="Ruta para exportar resumen CSV por ventana.",
    )
    args = p.parse_args()

    with args.policy.open(encoding="utf-8") as f:
        policy_doc = yaml.safe_load(f)

    if args.demo:
        days = _weekdays_from(date(2026, 1, 5), max(60, args.demo_days))
        bars = _demo_bars(days)
        print(f"mode=demo trading_days={len(days)}")
    else:
        db = MarketDB(str(args.db))
        ref_date = date.fromisoformat(args.reference_date) if args.reference_date else date.today()
        lookback_default = int(policy_doc.get("validation_wf", {}).get("lookback_trading_days", 90))
        lookback_days = args.lookback_trading_days or lookback_default
        days = _trading_days_from_db(db, ref_date, lookback_days)
        if not days:
            print("GLOBAL FAIL: empty_trading_calendar_from_db")
            return 1
        merged_whitelist = load_merged_whitelist(REPO_ROOT, policy_doc)
        bars = _bars_from_db(db, days, merged_whitelist)
        if not bars:
            print("GLOBAL FAIL: empty_bars_from_db")
            return 1
        print(
            "mode=db "
            f"db={args.db} "
            f"reference_date={ref_date.isoformat()} "
            f"trading_days={len(days)} "
            f"bars_days={len(bars)}"
        )

    report = run_short_term_pre_gate(
        policy_doc=policy_doc,
        repo_root=REPO_ROOT,
        bars_by_date=bars,
        trading_days=days,
    )

    if report.global_failures:
        print("GLOBAL FAIL:", report.global_failures)
        return 1

    rows: list[dict[str, Any]] = []
    for i, w in enumerate(report.windows):
        row = _window_row(i, w.metrics, w.passed, w.violations)
        if row["start_date"]:
            start = date.fromisoformat(str(row["start_date"]))
            row["start_month"] = start.strftime("%Y-%m")
            iso = start.isocalendar()
            row["start_iso_week"] = f"{iso.year}-W{iso.week:02d}"
        else:
            row["start_month"] = ""
            row["start_iso_week"] = ""
        rows.append(row)
        print(f"window_{i}", w.metrics, "OK" if w.passed else w.violations)

    if args.out_csv:
        _write_csv(args.out_csv, rows)
        print(f"csv_saved: {args.out_csv}")

    if args.out_json:
        payload = {
            "pre_gate_passed": report.passed,
            "global_failures": report.global_failures,
            "windows_total": len(rows),
            "windows_passed": sum(1 for x in rows if bool(x["passed"])),
            "windows_failed": sum(1 for x in rows if not bool(x["passed"])),
            "summary_by_month": _aggregate_windows(rows, "start_month"),
            "summary_by_iso_week": _aggregate_windows(rows, "start_iso_week"),
            "windows": rows,
        }
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"json_saved: {args.out_json}")

    print("pre_gate_passed:", report.passed)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
