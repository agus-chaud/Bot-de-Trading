"""TWR + walk-forward para simulaciones de investigación con aportes periódicos.

Módulo PURO (sin I/O, sin DB): toma una serie diaria de (equity, aporte) y produce
retornos time-weighted, índice TWR, métricas y ventanas walk-forward. La separación
es deliberada — la corrección del TWR ante aportes se puede testear sin correr el bot.

¿Por qué TWR y no el retorno crudo del equity? Si el 1 de cada mes inyectás capital,
el equity salta sin que la estrategia haya ganado nada. El retorno crudo
(`V_t/V_{t-1}-1`) leería ese aporte como una ganancia gigante (artefacto). El TWR
**excluye el aporte de la base** antes de medir, así mide la habilidad de la
estrategia, no el timing de tus depósitos. Para juzgar la estrategia (y el gate) → TWR.
Para tu experiencia real en pesos → MWR/TIR (`money_weighted_return`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

_TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class DailyPoint:
    """Un día de la simulación.

    - ``equity``: valor total del portfolio al CIERRE del día (V_t).
    - ``contribution``: aporte externo agregado al INICIO del día (C_t ≥ 0).
      Es plata que entra, NO ganancia. Cero los días sin aporte.
    """

    day: date
    equity: float
    contribution: float = 0.0


def time_weighted_daily_returns(series: list[DailyPoint]) -> list[tuple[date, float]]:
    """Retornos diarios time-weighted, excluyendo los aportes.

    ``r_t = V_t / (V_{t-1} + C_t) - 1`` — el aporte del día t entra al inicio, así que
    forma parte de la base sobre la que se mide el movimiento de mercado, pero **no**
    cuenta como retorno. El primer día no tiene previo → se omite.
    """
    out: list[tuple[date, float]] = []
    for i in range(1, len(series)):
        base = series[i - 1].equity + series[i].contribution
        if base <= 0:
            continue
        out.append((series[i].day, series[i].equity / base - 1.0))
    return out


def twr_index(returns: list[tuple[date, float]], base: float = 100.0) -> list[float]:
    """Índice acumulado (base 100) a partir de los retornos TWR — libre de aportes."""
    idx = [base]
    for _, r in returns:
        idx.append(idx[-1] * (1.0 + r))
    return idx


def cumulative_twr(returns: list[tuple[date, float]]) -> float:
    """Retorno TWR acumulado del período (fracción, p. ej. 0.17 = +17%)."""
    acc = 1.0
    for _, r in returns:
        acc *= 1.0 + r
    return acc - 1.0


def annualized_twr(returns: list[tuple[date, float]]) -> float:
    """TWR anualizado (CAGR) sobre el número de días de la serie."""
    n = len(returns)
    if n == 0:
        return 0.0
    growth = 1.0 + cumulative_twr(returns)
    if growth <= 0:
        return -1.0
    return growth ** (_TRADING_DAYS_PER_YEAR / n) - 1.0


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def annualized_sharpe(returns: list[tuple[date, float]], rf_daily: float = 0.0) -> float | None:
    """Sharpe anualizado sobre los retornos TWR. None si no hay volatilidad medible."""
    rs = [r - rf_daily for _, r in returns]
    sd = _std(rs)
    if sd < 1e-12:  # sin volatilidad medible (incl. ruido de punto flotante)
        return None
    return (_mean(rs) / sd) * math.sqrt(_TRADING_DAYS_PER_YEAR)


def annualized_sortino(returns: list[tuple[date, float]], rf_daily: float = 0.0) -> float | None:
    """Sortino anualizado: penaliza solo el desvío a la baja (downside)."""
    rs = [r - rf_daily for _, r in returns]
    downside = [min(0.0, r) for r in rs]
    dd = math.sqrt(sum(d ** 2 for d in downside) / len(rs)) if rs else 0.0
    if dd < 1e-12:  # sin downside medible
        return None
    return (_mean(rs) / dd) * math.sqrt(_TRADING_DAYS_PER_YEAR)


def max_drawdown(returns: list[tuple[date, float]]) -> float:
    """Max drawdown (fracción ≤ 0) sobre el ÍNDICE TWR — no sobre el equity crudo.

    Crítico: medir el drawdown sobre el equity con aportes lo ESCONDE (la curva sube
    porque metés plata, tapando las pérdidas). Sobre el índice TWR, libre de aportes,
    el drawdown es real.
    """
    idx = twr_index(returns)
    peak = idx[0]
    worst = 0.0
    for v in idx:
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, v / peak - 1.0)
    return worst


def money_weighted_return(
    cashflows: list[tuple[date, float]],
    ending_value: float,
    ending_day: date,
) -> float | None:
    """TIR anualizada (MWR) — tu experiencia real, sensible al timing de los aportes.

    ``cashflows``: aportes como NEGATIVOS (plata que ponés), con su fecha.
    ``ending_value``: valor final del portfolio (positivo). Devuelve la tasa anual que
    hace NPV = 0, o None si no converge. Se resuelve por bisección (robusta, sin
    derivadas).
    """
    flows = list(cashflows) + [(ending_day, ending_value)]
    t0 = flows[0][0]

    def npv(rate: float) -> float:
        total = 0.0
        for d, amt in flows:
            years = (d - t0).days / 365.0
            total += amt / ((1.0 + rate) ** years)
        return total

    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None  # sin cambio de signo en el rango → no hay raíz acá
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-6:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


# ---------------------------------------------------------------------------
# Walk-forward
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WalkForwardWindow:
    """Una ventana: ``burn_in`` días de calentamiento (no se puntúan) + ``oos`` días
    out-of-sample (los que se evalúan)."""

    index: int
    train_start: date
    train_end: date
    oos_start: date
    oos_end: date


def walk_forward_windows(
    days: list[date], burn_in: int, oos: int, step: int
) -> list[WalkForwardWindow]:
    """Genera ventanas rolling: [burn_in | oos], avanzando ``step`` días por ventana.

    Necesita al menos ``burn_in + oos`` días para formar una sola ventana. Si no
    alcanza, devuelve lista vacía (no inventa ventanas — esa honestidad es el punto).
    """
    if burn_in <= 0 or oos <= 0 or step <= 0:
        raise ValueError("burn_in, oos y step deben ser > 0")
    windows: list[WalkForwardWindow] = []
    start = 0
    idx = 0
    while start + burn_in + oos <= len(days):
        train_lo = start
        train_hi = start + burn_in - 1
        oos_lo = start + burn_in
        oos_hi = start + burn_in + oos - 1
        windows.append(
            WalkForwardWindow(
                index=idx,
                train_start=days[train_lo],
                train_end=days[train_hi],
                oos_start=days[oos_lo],
                oos_end=days[oos_hi],
            )
        )
        start += step
        idx += 1
    return windows


def _oos_returns(
    returns: list[tuple[date, float]], window: WalkForwardWindow
) -> list[tuple[date, float]]:
    return [(d, r) for d, r in returns if window.oos_start <= d <= window.oos_end]


# Umbrales por defecto: réplica de kpi_oos_gate.thresholds (POLICY.md §13). Acá se usan
# en modo INVESTIGACIÓN, no son el gate congelado de producción.
DEFAULT_THRESHOLDS = {
    "min_sharpe_annualized": 0.30,
    "min_sortino_annualized": 0.40,
    "max_drawdown_floor": -0.18,
}


def evaluate_walk_forward(
    series: list[DailyPoint],
    *,
    burn_in: int = 120,
    oos: int = 60,
    step: int = 30,
    thresholds: dict[str, float] | None = None,
) -> dict:
    """Corre el walk-forward sobre la serie y devuelve métricas OOS por ventana + agregado.

    MODO INVESTIGACIÓN: los parámetros son configurables a propósito. Esto NO reemplaza
    el gate KPI OOS congelado de producción (POLICY.md §13) — sirve para explorar cómo
    se comporta la estrategia, no para autorizar capital real.
    """
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    returns = time_weighted_daily_returns(series)
    days = [p.day for p in series]
    windows = walk_forward_windows(days, burn_in, oos, step)

    window_reports: list[dict] = []
    for w in windows:
        oos_r = _oos_returns(returns, w)
        sharpe = annualized_sharpe(oos_r)
        sortino = annualized_sortino(oos_r)
        mdd = max_drawdown(oos_r)
        checks = {
            "sharpe": sharpe is not None and sharpe >= th["min_sharpe_annualized"],
            "sortino": sortino is not None and sortino >= th["min_sortino_annualized"],
            "max_drawdown": mdd >= th["max_drawdown_floor"],
        }
        window_reports.append(
            {
                "index": w.index,
                "oos_start": w.oos_start.isoformat(),
                "oos_end": w.oos_end.isoformat(),
                "oos_days": len(oos_r),
                "twr_cumulative": cumulative_twr(oos_r),
                "twr_annualized": annualized_twr(oos_r),
                "sharpe_annualized": sharpe,
                "sortino_annualized": sortino,
                "max_drawdown": mdd,
                "passed": all(checks.values()),
                "checks": checks,
            }
        )

    return {
        "config": {"burn_in": burn_in, "oos": oos, "step": step, "thresholds": th},
        "mode": "research",  # NO es el gate de producción
        "total_days": len(series),
        "num_windows": len(window_reports),
        "windows": window_reports,
        "aggregate_passed": (
            len(window_reports) > 0 and all(w["passed"] for w in window_reports)
        ),
        "insufficient_data": len(window_reports) == 0,
    }
