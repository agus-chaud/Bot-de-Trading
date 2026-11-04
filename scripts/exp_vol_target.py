#!/usr/bin/env python3
"""Experimento: ¿vol targeting reduce el drawdown? (aislado, NO toca producción).

Corre la sim de investigación (run_research_sim, aportes mensuales + TWR) una vez,
y compara DOS lecturas de la MISMA corrida:

  - **baseline**: los retornos TWR tal cual.
  - **vol target**: cada retorno diario escalado por (vol_objetivo / vol_reciente),
    es decir, MÁS exposición cuando el mercado está calmo y MENOS cuando está
    nervioso. Mantiene el apalancamiento promedio ~1 (no es "apalancar y listo":
    aísla el EFECTO DE FORMA, no un boost de retorno).

Honestidad metodológica:
  - Es vol targeting a nivel CARTERA (sobre el retorno del book), no por posición.
    Aproximación estándar para testear la hipótesis rápido.
  - La vol se calcula con la ventana PREVIA (lag), sin mirar el futuro.
  - El vol_objetivo se calibra con el desvío de toda la serie (peek mínimo, solo
    para fijar la constante); en producción se fija ex-ante.

Uso::

    python scripts/exp_vol_target.py
    python scripts/exp_vol_target.py --window 20 --cap 2.0 --contrib 500000
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import date
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.storage import MarketDB  # noqa: E402
from reporting.twr_walk_forward import (  # noqa: E402
    annualized_sharpe,
    annualized_twr,
    cumulative_twr,
    max_drawdown,
    time_weighted_daily_returns,
)
from scripts.run_paper_live import load_required_calendar_store  # noqa: E402
from reporting.twr_walk_forward import walk_forward_windows  # noqa: E402
from scripts.run_wf_research_sim import (  # noqa: E402
    DEFAULT_CONTRIB,
    DEFAULT_DB,
    _prepare_sim_db,
    _trading_days,
    run_research_sim,
)

Returns = list[tuple[date, float]]


def _metrics(returns: Returns) -> dict[str, float | None]:
    ann = annualized_twr(returns)
    mdd = max_drawdown(returns)
    calmar = (ann / abs(mdd)) if mdd < 0 else None
    return {
        "twr_cum_pct": cumulative_twr(returns) * 100.0,
        "twr_ann_pct": ann * 100.0,
        "max_dd_pct": mdd * 100.0,
        "calmar": calmar,
        "sharpe": annualized_sharpe(returns),
    }


def vol_target_returns(
    returns: Returns,
    *,
    window: int,
    cap: float,
) -> tuple[Returns, float]:
    """Escala cada retorno por vol_objetivo/vol_reciente (lag). Devuelve (serie, leverage_prom)."""
    rs = [r for _, r in returns]
    target = statistics.pstdev(rs) if len(rs) > 1 else 0.0
    scaled: Returns = []
    factors: list[float] = []
    for i, (day, r) in enumerate(returns):
        if i < window or target <= 0:
            factor = 1.0
        else:
            prev = rs[i - window : i]  # ventana PREVIA, sin el día i (no lookahead)
            vol = statistics.pstdev(prev) if len(prev) > 1 else 0.0
            factor = min(target / vol, cap) if vol > 1e-12 else 1.0
        scaled.append((day, factor * r))
        factors.append(factor)
    avg_lev = sum(factors) / len(factors) if factors else 1.0
    return scaled, avg_lev


def _calmar(returns: Returns) -> float | None:
    ann = annualized_twr(returns)
    mdd = max_drawdown(returns)
    return (ann / abs(mdd)) if mdd < 0 else None


# ----------------------------- Indicadores -----------------------------------
# HMA (Hull, bajo lag) y ADX (fuerza de tendencia) sobre un proxy de mercado.
# Devuelven listas alineadas por índice; None donde aún no hay historia suficiente.


def _wma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    denom = period * (period + 1) / 2
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        out[i] = sum(v * (k + 1) for k, v in enumerate(window)) / denom
    return out


def _hma(closes: list[float], period: int) -> list[float | None]:
    import math as _m
    half = _wma(closes, max(1, period // 2))
    full = _wma(closes, period)
    diff = [
        (2 * h - f) if (h is not None and f is not None) else None
        for h, f in zip(half, full)
    ]
    sqn = max(1, int(round(_m.sqrt(period))))
    # WMA sobre diff, saltando los None iniciales.
    out: list[float | None] = [None] * len(diff)
    valid = [(i, d) for i, d in enumerate(diff) if d is not None]
    vals = [d for _, d in valid]
    w = _wma(vals, sqn)
    for j, (i, _) in enumerate(valid):
        out[i] = w[j]
    return out


def _adx(highs, lows, closes, period: int = 14) -> list[float | None]:
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < 2 * period + 1:
        return out
    tr, plus_dm, minus_dm = [0.0], [0.0], [0.0]
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        plus_dm.append(up if (up > dn and up > 0) else 0.0)
        minus_dm.append(dn if (dn > up and dn > 0) else 0.0)
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))

    def _smooth(xs: list[float]) -> list[float]:  # media móvil simple del período
        s = [0.0] * n
        for i in range(period, n):
            s[i] = sum(xs[i - period + 1 : i + 1]) / period
        return s

    str_, sp, sm = _smooth(tr), _smooth(plus_dm), _smooth(minus_dm)
    dx = [0.0] * n
    for i in range(period, n):
        if str_[i] <= 0:
            continue
        pdi = 100 * sp[i] / str_[i]
        mdi = 100 * sm[i] / str_[i]
        dx[i] = 100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) > 0 else 0.0
    for i in range(2 * period, n):
        out[i] = sum(dx[i - period + 1 : i + 1]) / period
    return out


def _proxy_signals(db: MarketDB, symbol: str, venue: str, hma_n: int, adx_n: int):
    """Devuelve {date: (close, hma|None, adx|None)} + lista ordenada de fechas del proxy."""
    rows = db.get_ohlcv(symbol, date(2000, 1, 1), date(2100, 1, 1), venue)
    if not rows:
        return {}, []
    dates = [r.ts for r in rows]
    closes = [float(r.close) for r in rows]
    highs = [float(r.high) for r in rows]
    lows = [float(r.low) for r in rows]
    hma = _hma(closes, hma_n)
    adx = _adx(highs, lows, closes, adx_n)
    table = {d: (closes[i], hma[i], adx[i]) for i, d in enumerate(dates)}
    return table, dates


def regime_gate_oos(
    returns: Returns,
    proxy_table: dict,
    proxy_dates: list,
    *,
    burn_in: int,
    oos: int,
    step: int,
) -> list[dict]:
    """Gate de régimen OOS: umbral de ADX calibrado en train (mediana), aplicado en OOS.

    gate=1 si ADX_prev >= umbral Y (HMA no disponible o close_prev > HMA_prev). Día
    apagado -> retorno 0 ("no operar"). Usa el día previo del proxy (lag, sin lookahead).
    """
    days = [d for d, _ in returns]

    def prev_proxy(d):
        cand = [pd for pd in proxy_dates if pd < d]
        return cand[-1] if cand else None

    out: list[dict] = []
    for w in walk_forward_windows(days, burn_in, oos, step):
        train_adx = [
            proxy_table[d][2]
            for d in proxy_dates
            if w.train_start <= d <= w.train_end and proxy_table[d][2] is not None
        ]
        thr = statistics.median(train_adx) if train_adx else 0.0
        base_oos: Returns = []
        gate_oos: Returns = []
        days_on = days_total = 0
        for d, r in returns:
            if not (w.oos_start <= d <= w.oos_end):
                continue
            pd = prev_proxy(d)
            gate = 1.0
            if pd is not None:
                close_p, hma_p, adx_p = proxy_table[pd]
                trend_ok = (hma_p is None) or (close_p > hma_p)
                strong = (adx_p is None) or (adx_p >= thr)
                gate = 1.0 if (trend_ok and strong) else 0.0
            base_oos.append((d, r))
            gate_oos.append((d, gate * r))
            days_total += 1
            days_on += int(gate)
        out.append(
            {
                "index": w.index,
                "oos_start": w.oos_start,
                "oos_end": w.oos_end,
                "thr": thr,
                "pct_on": 100.0 * days_on / days_total if days_total else 0.0,
                "base_mdd": max_drawdown(base_oos) * 100.0,
                "g_mdd": max_drawdown(gate_oos) * 100.0,
                "base_calmar": _calmar(base_oos),
                "g_calmar": _calmar(gate_oos),
            }
        )
    return out


def walk_forward_oos(
    returns: Returns,
    *,
    burn_in: int,
    oos: int,
    step: int,
    window: int,
    cap: float,
) -> list[dict]:
    """Valida vol targeting OOS: objetivo calibrado SOLO con el train de cada ventana.

    Disciplina out-of-sample: en cada ventana, ``target`` = desvío de los retornos del
    tramo de ENTRENAMIENTO (nunca del OOS). La vol reciente usa la ventana previa (lag).
    Se mide baseline vs vol-target sobre el tramo OOS.
    """
    rs = [r for _, r in returns]
    days = [d for d, _ in returns]
    idx_of = {d: i for i, d in enumerate(days)}
    out: list[dict] = []
    for w in walk_forward_windows(days, burn_in, oos, step):
        train_rs = [r for d, r in returns if w.train_start <= d <= w.train_end]
        target = statistics.pstdev(train_rs) if len(train_rs) > 1 else 0.0
        base_oos: Returns = []
        vt_oos: Returns = []
        for d, r in returns:
            if not (w.oos_start <= d <= w.oos_end):
                continue
            i = idx_of[d]
            prev = rs[i - window : i]  # solo pasado (puede incluir train); sin lookahead
            vol = statistics.pstdev(prev) if len(prev) > 1 else 0.0
            factor = min(target / vol, cap) if (vol > 1e-12 and target > 0) else 1.0
            base_oos.append((d, r))
            vt_oos.append((d, factor * r))
        out.append(
            {
                "index": w.index,
                "oos_start": w.oos_start,
                "oos_end": w.oos_end,
                "base_mdd": max_drawdown(base_oos) * 100.0,
                "vt_mdd": max_drawdown(vt_oos) * 100.0,
                "base_calmar": _calmar(base_oos),
                "vt_calmar": _calmar(vt_oos),
                "base_sharpe": annualized_sharpe(base_oos),
                "vt_sharpe": annualized_sharpe(vt_oos),
            }
        )
    return out


def _fmt(v: float | None, suffix: str = "") -> str:
    return f"{v:+.2f}{suffix}" if v is not None else "n/d"


def _report_wf(series, base_returns: Returns, args) -> int:
    rows = walk_forward_oos(
        base_returns,
        burn_in=args.burn_in, oos=args.oos, step=args.step,
        window=args.window, cap=args.cap,
    )
    line = "=" * 72
    print(f"\n{line}")
    print("  VALIDACION WALK-FORWARD OOS: vol targeting (objetivo calibrado en train)")
    print(f"  DB: {args.db.name} | {series[0].day} -> {series[-1].day} | {len(series)} dias")
    print(f"  burn_in={args.burn_in} oos={args.oos} step={args.step} | window={args.window} cap={args.cap}")
    print(line)
    if not rows:
        print("  Sin ventanas (serie corta para burn_in+oos). Bajá --burn-in/--oos.")
        print(line)
        return 0
    print(f"  {'#':>2} {'OOS desde':12} {'OOS hasta':12} "
          f"{'MaxDD base':>11}{'MaxDD vt':>11}{'Calmar base':>12}{'Calmar vt':>11}")
    print("  " + "-" * 68)
    dd_better = cal_better = 0
    dd_b_sum = dd_v_sum = 0.0
    cal_pairs = 0
    cal_b_sum = cal_v_sum = 0.0
    for r in rows:
        if r["vt_mdd"] > r["base_mdd"]:  # menos negativo = mejor
            dd_better += 1
        dd_b_sum += r["base_mdd"]
        dd_v_sum += r["vt_mdd"]
        cb, cv = r["base_calmar"], r["vt_calmar"]
        if cb is not None and cv is not None:
            cal_pairs += 1
            cal_b_sum += cb
            cal_v_sum += cv
            if cv > cb:
                cal_better += 1
        print(f"  {r['index']:>2} {str(r['oos_start']):12} {str(r['oos_end']):12} "
              f"{r['base_mdd']:>10.2f}%{r['vt_mdd']:>10.2f}%"
              f"{_fmt(cb):>12}{_fmt(cv):>11}")
    n = len(rows)
    print("  " + "-" * 68)
    print(f"  Ventanas: {n} | MaxDD mejora en {dd_better}/{n} | Calmar mejora en {cal_better}/{cal_pairs}")
    print(f"  MaxDD promedio: baseline {dd_b_sum / n:+.2f}%  ->  vol target {dd_v_sum / n:+.2f}%")
    if cal_pairs:
        print(f"  Calmar promedio: baseline {cal_b_sum / cal_pairs:+.2f}  ->  vol target {cal_v_sum / cal_pairs:+.2f}")
    print(line)
    dd_ok = dd_better > n / 2
    cal_ok = cal_pairs > 0 and cal_better > cal_pairs / 2
    if dd_ok and cal_ok:
        print("  VEREDICTO OOS: vol targeting reduce drawdown y mejora Calmar en la MAYORIA")
        print("  de las ventanas. Pasa la validacion OOS -> candidato real a policy.")
    elif dd_ok:
        print("  VEREDICTO OOS: baja drawdown en la mayoria pero Calmar mixto. Promesa parcial.")
    else:
        print("  VEREDICTO OOS: NO consistente OOS. No promover (el resultado in-sample no aguanta).")
    print(line + "\n")
    return 0


def _report_regime(series, base_returns: Returns, db: MarketDB, args) -> int:
    table, pdates = _proxy_signals(db, args.proxy, args.proxy_venue, args.hma, args.adx_period)
    line = "=" * 76
    print(f"\n{line}")
    print("  VALIDACION WALK-FORWARD OOS: gate de regimen (ADX + HMA, 'no operar en lateral')")
    print(f"  proxy={args.proxy}@{args.proxy_venue} | HMA={args.hma} ADX={args.adx_period} | umbral ADX = mediana(train)")
    print(f"  DB: {args.db.name} | {series[0].day} -> {series[-1].day} | {len(series)} dias")
    print(line)
    if not table:
        print(f"  Proxy {args.proxy}@{args.proxy_venue} sin datos. Probá otro --proxy/--proxy-venue.")
        print(line)
        return 1
    rows = regime_gate_oos(
        base_returns, table, pdates,
        burn_in=args.burn_in, oos=args.oos, step=args.step,
    )
    if not rows:
        print("  Sin ventanas (serie corta). Bajá --burn-in/--oos.")
        print(line)
        return 0
    print(f"  {'#':>2} {'OOS desde':12} {'OOS hasta':12} {'%dias ON':>9}"
          f"{'MaxDD base':>11}{'MaxDD gate':>11}{'Calmar base':>12}{'Calmar gate':>12}")
    print("  " + "-" * 72)
    dd_better = cal_better = cal_pairs = 0
    dd_b_sum = dd_g_sum = cal_b_sum = cal_g_sum = 0.0
    for r in rows:
        if r["g_mdd"] > r["base_mdd"]:
            dd_better += 1
        dd_b_sum += r["base_mdd"]
        dd_g_sum += r["g_mdd"]
        cb, cg = r["base_calmar"], r["g_calmar"]
        if cb is not None and cg is not None:
            cal_pairs += 1
            cal_b_sum += cb
            cal_g_sum += cg
            if cg > cb:
                cal_better += 1
        print(f"  {r['index']:>2} {str(r['oos_start']):12} {str(r['oos_end']):12} "
              f"{r['pct_on']:>7.0f}% {r['base_mdd']:>10.2f}%{r['g_mdd']:>10.2f}%"
              f"{_fmt(cb):>12}{_fmt(cg):>12}")
    n = len(rows)
    print("  " + "-" * 72)
    print(f"  Ventanas: {n} | MaxDD mejora en {dd_better}/{n} | Calmar mejora en {cal_better}/{cal_pairs}")
    print(f"  MaxDD promedio: baseline {dd_b_sum / n:+.2f}%  ->  gate {dd_g_sum / n:+.2f}%")
    if cal_pairs:
        print(f"  Calmar promedio: baseline {cal_b_sum / cal_pairs:+.2f}  ->  gate {cal_g_sum / cal_pairs:+.2f}")
    print(line)
    dd_ok = dd_better > n / 2
    cal_ok = cal_pairs > 0 and cal_better > cal_pairs / 2
    if dd_ok and cal_ok:
        print("  VEREDICTO OOS: el gate reduce drawdown y mejora Calmar en la MAYORIA. Pasa -> candidato.")
    elif dd_ok:
        print("  VEREDICTO OOS: baja drawdown en la mayoria, Calmar mixto. Promesa parcial.")
    else:
        print("  VEREDICTO OOS: NO consistente OOS. No promover.")
    print(line + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Experimento vol targeting sobre la sim de investigación")
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--policy", type=Path, default=REPO_ROOT / "config" / "policy.v1.yaml")
    p.add_argument("--start", type=date.fromisoformat, default=date(2025, 1, 1))
    p.add_argument("--end", type=date.fromisoformat, default=date.today())
    p.add_argument("--contrib", type=float, default=DEFAULT_CONTRIB)
    p.add_argument("--window", type=int, default=20, help="Ventana de vol reciente (días)")
    p.add_argument("--cap", type=float, default=2.0, help="Apalancamiento máximo")
    p.add_argument("--no-long", action="store_true")
    p.add_argument("--wf", action="store_true", help="Modo walk-forward OOS (objetivo calibrado en train)")
    p.add_argument("--regime-gate", action="store_true", help="Modo gate de régimen ADX+HMA (WF OOS)")
    p.add_argument("--proxy", type=str, default="GGAL", help="Símbolo proxy de mercado")
    p.add_argument("--proxy-venue", type=str, default="XNYS", help="Venue del proxy")
    p.add_argument("--hma", type=int, default=200, help="Período de la HMA (dirección)")
    p.add_argument("--adx-period", type=int, default=14, help="Período del ADX")
    p.add_argument("--burn-in", type=int, default=120)
    p.add_argument("--oos", type=int, default=60)
    p.add_argument("--step", type=int, default=30)
    args = p.parse_args(argv)

    if not args.db.exists():
        print(f"exp_vol_target: DB no encontrada: {args.db}", file=sys.stderr)
        return 1

    policy_doc = yaml.safe_load(args.policy.open(encoding="utf-8"))
    sim_path = _prepare_sim_db(args.db)
    db = MarketDB(str(sim_path))
    calendar_store = load_required_calendar_store(policy_doc)
    days = _trading_days(db, args.start, args.end)
    if not days:
        print("exp_vol_target: sin días de mercado en el rango", file=sys.stderr)
        return 1

    series = run_research_sim(
        db, days, policy_doc,
        contribution=args.contrib, calendar_store=calendar_store,
        enable_long=not args.no_long,
    )
    base_returns = time_weighted_daily_returns(series)

    if args.regime_gate:
        return _report_regime(series, base_returns, db, args)
    if args.wf:
        return _report_wf(series, base_returns, args)

    vt_returns, avg_lev = vol_target_returns(base_returns, window=args.window, cap=args.cap)

    base = _metrics(base_returns)
    vt = _metrics(vt_returns)

    line = "=" * 64
    print(f"\n{line}")
    print("  EXPERIMENTO: vol targeting (nivel cartera, lag, leverage ~1)")
    print(f"  DB: {args.db.name} | {series[0].day} -> {series[-1].day} | {len(series)} dias")
    print(f"  ventana vol={args.window} | cap={args.cap} | leverage promedio={avg_lev:.2f}")
    print(line)
    print(f"  {'metrica':<16}{'baseline':>14}{'vol target':>14}{'delta':>12}")
    print("  " + "-" * 56)
    for key, label, suf in [
        ("max_dd_pct", "Max drawdown", "%"),
        ("calmar", "Calmar", ""),
        ("sharpe", "Sharpe", ""),
        ("twr_ann_pct", "TWR anual", "%"),
        ("twr_cum_pct", "TWR acumulado", "%"),
    ]:
        b, v = base[key], vt[key]
        delta = (v - b) if (b is not None and v is not None) else None
        print(f"  {label:<16}{_fmt(b, suf):>14}{_fmt(v, suf):>14}{_fmt(delta, suf):>12}")
    print(line)

    dd_b, dd_v = base["max_dd_pct"], vt["max_dd_pct"]
    cal_b, cal_v = base["calmar"], vt["calmar"]
    dd_better = dd_v is not None and dd_b is not None and dd_v > dd_b  # menos negativo
    cal_better = cal_v is not None and cal_b is not None and cal_v > cal_b
    if dd_better and cal_better:
        verdict = "VEREDICTO: vol targeting MEJORA drawdown y Calmar. Vale validar en serio."
    elif dd_better:
        verdict = "VEREDICTO: baja el drawdown pero el Calmar no mejora claro. Mirar con cuidado."
    else:
        verdict = "VEREDICTO: NO mejora el drawdown en esta corrida. No promover."
    print(f"  {verdict}")
    print("  (Recordá: nivel cartera, no por posición; calibración in-sample del objetivo.)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
