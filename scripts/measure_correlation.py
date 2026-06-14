#!/usr/bin/env python3
"""Mide la correlación entre los holdings del sleeve largo — no asumir, medir.

El walk-forward (ADR-059) mostró que GGAL+PAMP comparten factor (riesgo-país AR) y caen
juntas. Antes de declarar "diversificado" un sleeve nuevo, hay que **medir** la correlación
de los retornos: dos nombres muy correlacionados (≈1) son casi la misma apuesta aunque sean
de sectores distintos. Diversificar de verdad = bajar la correlación promedio, sobre todo
entre el bloque AR y el bloque global.

Todos los retornos se miden en el venue/moneda en que el sleeve largo valúa (XBUE/ARS),
que es la correlación **relevante para el riesgo del portfolio**.

Uso::

  python scripts/measure_correlation.py --policy config/policy.research_diversified.v1.yaml
  python scripts/measure_correlation.py --db data/market_backfill.db --start 2025-01-01
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import sqlite3  # noqa: E402

# Bloques para el resumen (AR-nativo vs global/CEDEAR).
_AR_NATIVE = {"GGAL", "PAMP", "TXAR", "YPFD", "BMA", "CEPU", "ALUA", "SUPV",
              "TGSU2", "CRES", "TECO2", "LOMA", "MIRG", "IRSA"}


def _daily_returns(conn: sqlite3.Connection, symbol: str, start: date, end: date) -> dict[date, float]:
    cur = conn.execute(
        "SELECT ts, close FROM ohlcv WHERE symbol=? AND venue='XBUE' AND imputed=0 "
        "AND ts BETWEEN ? AND ? ORDER BY ts",
        (symbol, start.isoformat(), end.isoformat()),
    )
    rows = cur.fetchall()
    out: dict[date, float] = {}
    for i in range(1, len(rows)):
        prev = rows[i - 1][1]
        if prev:
            out[date.fromisoformat(rows[i][0])] = rows[i][1] / prev - 1.0
    return out


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return float("nan")
    return cov / (vx ** 0.5 * vy ** 0.5)


def main() -> int:
    p = argparse.ArgumentParser(description="Matriz de correlación del sleeve largo")
    p.add_argument("--db", type=Path, default=REPO_ROOT / "data" / "market_backfill.db")
    p.add_argument("--policy", type=Path, default=REPO_ROOT / "config" / "policy.research_diversified.v1.yaml")
    p.add_argument("--start", type=lambda s: date.fromisoformat(s), default=date(2025, 1, 1))
    p.add_argument("--end", type=lambda s: date.fromisoformat(s), default=date.today())
    args = p.parse_args()

    lt = yaml.safe_load(args.policy.open(encoding="utf-8"))["long_term_engine"]
    symbols = [str(it["symbol"]).upper() for it in (lt.get("core_lines", []) + lt.get("satellite_lines", []))]

    conn = sqlite3.connect(str(args.db))
    rets = {s: _daily_returns(conn, s, args.start, args.end) for s in symbols}
    conn.close()

    # Fechas comunes a todos.
    common = set.intersection(*(set(r) for r in rets.values())) if rets else set()
    common = sorted(common)
    series = {s: [rets[s][d] for d in common] for s in symbols}

    print(f"\nCorrelación de retornos diarios (XBUE/ARS) — {len(common)} días comunes\n")
    hdr = "        " + "".join(f"{s:>7}" for s in symbols)
    print(hdr)
    corr: dict[tuple[str, str], float] = {}
    for a in symbols:
        row = f"{a:>7} "
        for b in symbols:
            c = _pearson(series[a], series[b])
            corr[(a, b)] = c
            row += f"{c:>7.2f}"
        print(row)

    def _avg(pairs: list[tuple[str, str]]) -> float:
        vals = [corr[(a, b)] for a, b in pairs if a != b and corr[(a, b)] == corr[(a, b)]]
        return sum(vals) / len(vals) if vals else float("nan")

    ar = [s for s in symbols if s in _AR_NATIVE]
    glob = [s for s in symbols if s not in _AR_NATIVE]
    intra_ar = [(a, b) for a in ar for b in ar if a < b]
    intra_gl = [(a, b) for a in glob for b in glob if a < b]
    cross = [(a, b) for a in ar for b in glob]

    print(f"\nResumen (más bajo = más diversificado):")
    print(f"  Correlación promedio intra-AR     : {_avg(intra_ar):.2f}   {ar}")
    print(f"  Correlación promedio intra-global : {_avg(intra_gl):.2f}   {glob}")
    print(f"  Correlación promedio AR <-> global: {_avg(cross):.2f}   <- el numero clave")
    print("\n  Interpretacion: si AR<->global es notablemente menor que intra-AR,")
    print("  agregar el bloque global SI diversifica el factor (no es la misma apuesta).\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
