"""Benchmark mixto 20/80: tabla estática + retornos alineados al backtest sin lookahead.

Usa cierres *point-in-time*: en cada fecha de valoración `d` el cierre de un símbolo
es el último disponible con `bar_date <= d`. El retorno entre dos fechas consecutivas
del backtest es el cociente de esos cierres PIT menos 1, ponderado por los pesos de la tabla.
"""

from __future__ import annotations

import csv
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping, Sequence

import yaml

from data.fetcher import fetch_and_store
from data.storage import MarketDB


@dataclass(frozen=True)
class BenchmarkLine:
    symbol: str
    weight: float
    geo: str  # "AR" | "US"
    venue: str  # XNYS | XBUE
    description: str = ""


def load_benchmark_yaml(path: str | Path) -> list[BenchmarkLine]:
    """Load `BenchmarkLine`s from YAML (see ``config/benchmark_mix_20_80.v1.yaml``)."""
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("benchmark yaml root must be a mapping")
    lines = raw.get("lines")
    if not isinstance(lines, list) or not lines:
        raise ValueError("benchmark yaml must contain non-empty 'lines'")

    out: list[BenchmarkLine] = []
    for i, row in enumerate(lines):
        if not isinstance(row, dict):
            raise ValueError(f"lines[{i}] must be a mapping")
        symbol = row.get("symbol")
        weight = row.get("weight")
        geo = str(row.get("geo", "")).strip().upper()
        venue = str(row.get("venue", "")).strip().upper()
        if not isinstance(symbol, str) or not symbol:
            raise ValueError(f"lines[{i}].symbol invalid")
        if not isinstance(weight, int | float):
            raise ValueError(f"lines[{i}].weight must be numeric")
        if geo not in ("AR", "US"):
            raise ValueError(f"lines[{i}].geo must be 'AR' or 'US'")
        if venue not in ("XNYS", "XBUE"):
            raise ValueError(f"lines[{i}].venue must be 'XNYS' or 'XBUE'")
        desc = row.get("description", "")
        if desc is not None and not isinstance(desc, str):
            raise ValueError(f"lines[{i}].description must be a string")
        out.append(
            BenchmarkLine(
                symbol=symbol,
                weight=float(weight),
                geo=geo,
                venue=venue,
                description=str(desc or ""),
            )
        )

    total = sum(ln.weight for ln in out)
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"benchmark weights must sum to 1.0, got {total}")
    return out


def load_benchmark_csv(path: str | Path) -> list[BenchmarkLine]:
    """Load from CSV with columns: symbol, weight, geo, venue[, description]."""
    p = Path(path)
    with p.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("csv has no header")
        required = {"symbol", "weight", "geo", "venue"}
        if not required.issubset(set(reader.fieldnames)):
            raise ValueError(f"csv must include columns {sorted(required)}")

        rows: list[dict[str, str]] = []
        for row in reader:
            if not any((v or "").strip() for v in row.values()):
                continue
            rows.append({k: (v or "").strip() for k, v in row.items()})

    lines: list[BenchmarkLine] = []
    for i, row in enumerate(rows):
        weight_raw = row["weight"]
        try:
            w = float(weight_raw)
        except ValueError as exc:
            raise ValueError(f"row {i}: invalid weight") from exc
        geo = row["geo"].upper()
        venue = row["venue"].upper()
        lines.append(
            BenchmarkLine(
                symbol=row["symbol"],
                weight=w,
                geo=geo,
                venue=venue,
                description=row.get("description", ""),
            )
        )

    total = sum(ln.weight for ln in lines)
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"benchmark weights must sum to 1.0, got {total}")
    return lines


def load_benchmark_table(path: str | Path) -> list[BenchmarkLine]:
    """Dispatch by file suffix: ``.yaml`` / ``.yml`` vs ``.csv``."""
    p = Path(path)
    suf = p.suffix.lower()
    if suf in (".yaml", ".yml"):
        return load_benchmark_yaml(p)
    if suf == ".csv":
        return load_benchmark_csv(p)
    raise ValueError(f"unsupported benchmark table suffix: {suf}")


def asof_close(sorted_bars: Sequence[tuple[date, float]], valuation_date: date) -> float | None:
    """Last close from bars with ``bar_date <= valuation_date``.

    *sorted_bars* must be sorted ascending by date.
    """
    if not sorted_bars:
        return None
    dates = [b[0] for b in sorted_bars]
    idx = bisect_right(dates, valuation_date) - 1
    if idx < 0:
        return None
    return sorted_bars[idx][1]


def align_benchmark_simple_returns(
    lines: Sequence[BenchmarkLine],
    closes_by_symbol: Mapping[str, Sequence[tuple[date, float]]],
    backtest_dates: Sequence[date],
) -> tuple[list[date], list[float | None]]:
    """Return (end_dates, returns) for each consecutive pair in *backtest_dates*.

    ``returns[k]`` is the simple benchmark return from ``backtest_dates[k]`` to
    ``backtest_dates[k + 1]`` (end date = ``backtest_dates[k + 1]``), using PIT closes.

    Missing PIT closes, zero previous close, or undefined ratios yield ``None`` for that
    interval. For strict inner-join (``rpt_kpi.v1`` §12), use ``filter_inner_join_returns``.

    No lookahead: only bars on or before each valuation date are used.
    """
    if len(lines) < 1:
        raise ValueError("lines must be non-empty")
    dates_sorted = sorted(backtest_dates)
    if len(dates_sorted) < 2:
        return [], []

    # Ensure each series is sorted
    series: dict[str, list[tuple[date, float]]] = {}
    for ln in lines:
        raw = closes_by_symbol.get(ln.symbol)
        if raw is None:
            raise KeyError(f"no close series for symbol {ln.symbol}")
        sb = sorted(raw, key=lambda x: x[0])
        series[ln.symbol] = sb

    end_dates: list[date] = []
    returns: list[float | None] = []

    for i in range(len(dates_sorted) - 1):
        d_prev, d_end = dates_sorted[i], dates_sorted[i + 1]
        r_mix: float = 0.0
        ok = True
        for ln in lines:
            c_prev = asof_close(series[ln.symbol], d_prev)
            c_end = asof_close(series[ln.symbol], d_end)
            if c_prev is None or c_end is None or c_prev <= 0.0:
                ok = False
                break
            r_line = c_end / c_prev - 1.0
            r_mix += ln.weight * r_line

        end_dates.append(d_end)
        returns.append(r_mix if ok else None)

    return end_dates, returns


def filter_inner_join_returns(
    end_dates: Sequence[date],
    returns: Sequence[float | None],
) -> tuple[list[date], list[float]]:
    """Drop intervals where benchmark return could not be computed (``None``)."""
    d_out: list[date] = []
    r_out: list[float] = []
    for d, r in zip(end_dates, returns, strict=True):
        if r is not None:
            d_out.append(d)
            r_out.append(r)
    return d_out, r_out


def load_close_series_from_db(
    db: MarketDB,
    symbol: str,
    venue: str,
    start_date: date,
    end_date: date,
) -> list[tuple[date, float]]:
    """Read ascending (ts, close) from SQLite for benchmarking."""
    rows = db.get_ohlcv(symbol, start_date, end_date, venue)
    return [(r.ts, r.close) for r in rows]


def fetch_benchmark_into_db(
    db: MarketDB,
    table_path: str | Path,
    start_date: date,
    end_date: date,
):
    """Download OHLCV for all benchmark lines via ``fetch_and_store``.

    Symbols are split US vs AR by venue (``XNYS`` → US connector, ``XBUE`` → AR).
    """
    lines = load_benchmark_table(table_path)
    symbols_us = sorted({ln.symbol for ln in lines if ln.venue == "XNYS"})
    symbols_ar = sorted({ln.symbol for ln in lines if ln.venue == "XBUE"})
    return fetch_and_store(symbols_us, symbols_ar, start_date, end_date, db)
