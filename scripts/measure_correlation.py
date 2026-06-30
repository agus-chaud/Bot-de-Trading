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

# Ventanas de selloff de la bolsa AR (ADR-059). La correlacion que importa para una
# cobertura NO es el promedio del periodo (se ve baja y engania): es la correlacion
# DURANTE las crisis, cuando todo tiende a correlacionar a 1. Un hedge sirve solo si
# se mantiene <= 0 con el factor (GGAL/PAMP) en estas ventanas.
_CRISIS_WINDOWS: list[tuple[str, date, date]] = [
    ("selloff ago-sep 2025", date(2025, 8, 1), date(2025, 9, 30)),
    ("selloff feb 2026", date(2026, 2, 1), date(2026, 2, 28)),
]


def _in_crisis(d: date) -> bool:
    return any(lo <= d <= hi for _, lo, hi in _CRISIS_WINDOWS)


def _corr_on_dates(
    rets_a: dict[date, float], rets_b: dict[date, float], days: list[date]
) -> tuple[float, int]:
    xs = [rets_a[d] for d in days if d in rets_a and d in rets_b]
    ys = [rets_b[d] for d in days if d in rets_a and d in rets_b]
    return _pearson(xs, ys), len(xs)


def _run_hedge_analysis(
    conn: sqlite3.Connection,
    hedge_symbols: list[str],
    factor_symbols: list[str],
    start: date,
    end: date,
) -> int:
    """Fase 1 del plan_hedge_short: correlacion condicional a crisis.

    Para cada candidato de cobertura, compara su correlacion con el factor (GGAL/PAMP)
    en TODO el periodo vs SOLO durante los selloffs. El criterio de aceptacion es la
    correlacion en crisis, no el promedio.
    """
    universe = list(dict.fromkeys(hedge_symbols + factor_symbols))
    rets = {s: _daily_returns(conn, s, start, end) for s in universe}

    all_days = sorted(set().union(*(set(r) for r in rets.values())) if rets else set())
    crisis_days = [d for d in all_days if _in_crisis(d)]

    print("\nFase 1 - Correlacion condicional a crisis (XBUE/ARS)")
    print(f"  Periodo total : {start} -> {end}  ({len(all_days)} dias)")
    print(f"  Dias en crisis: {len(crisis_days)}  ventanas={[w[0] for w in _CRISIS_WINDOWS]}\n")

    header = f"{'hedge':>7} {'vs factor':>10} {'corr TOTAL':>11} {'corr CRISIS':>12} {'n_crisis':>9}  veredicto"
    print(header)
    print("-" * len(header))

    verdicts: dict[str, list[float]] = {}
    for h in hedge_symbols:
        for f in factor_symbols:
            c_all, _ = _corr_on_dates(rets[h], rets[f], all_days)
            c_cri, n_cri = _corr_on_dates(rets[h], rets[f], crisis_days)
            verdicts.setdefault(h, []).append(c_cri)
            ok = c_cri == c_cri and c_cri <= 0.0
            mark = "OK (<=0)" if ok else ("alto (>0)" if c_cri == c_cri else "sin datos")
            print(f"{h:>7} {f:>10} {c_all:>11.2f} {c_cri:>12.2f} {n_cri:>9}  {mark}")

    print("\nVeredicto por candidato (criterio: corr media en crisis vs factor <= 0):")
    for h in hedge_symbols:
        vals = [v for v in verdicts.get(h, []) if v == v]
        avg = sum(vals) / len(vals) if vals else float("nan")
        ok = avg == avg and avg <= 0.0
        print(f"  {h:>7}: corr media en crisis = {avg:>6.2f}  -> {'SIRVE como hedge' if ok else 'NO cubre en crisis'}")
    print("\n  Nota: una correlacion baja en el promedio puede ser alta en el crash.")
    print("  El unico numero que decide es la columna corr CRISIS.\n")
    return 0


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
    p.add_argument("--hedge", action="store_true",
                   help="Fase 1: correlacion condicional a crisis de candidatos de cobertura")
    p.add_argument("--hedge-symbols", nargs="*", default=["GLD", "KO"],
                   help="Candidatos de cobertura a evaluar (default: GLD KO)")
    p.add_argument("--factor-symbols", nargs="*", default=["GGAL", "PAMP"],
                   help="Simbolos del factor a cubrir (default: GGAL PAMP)")
    args = p.parse_args()

    if args.hedge:
        conn = sqlite3.connect(str(args.db))
        try:
            return _run_hedge_analysis(
                conn,
                [s.upper() for s in args.hedge_symbols],
                [s.upper() for s in args.factor_symbols],
                args.start,
                args.end,
            )
        finally:
            conn.close()

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

    print("\nResumen (más bajo = más diversificado):")
    print(f"  Correlación promedio intra-AR     : {_avg(intra_ar):.2f}   {ar}")
    print(f"  Correlación promedio intra-global : {_avg(intra_gl):.2f}   {glob}")
    print(f"  Correlación promedio AR <-> global: {_avg(cross):.2f}   <- el numero clave")
    print("\n  Interpretacion: si AR<->global es notablemente menor que intra-AR,")
    print("  agregar el bloque global SI diversifica el factor (no es la misma apuesta).\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
