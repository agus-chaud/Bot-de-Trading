"""Trigger de des-riesgo del sleeve largo por régimen (SDD long-regime-derisk).

Sobre el cimiento de ADR-071 (el largo puede mantener cash vía equity_exposure), este módulo
decide CUÁNDO y CUÁNTO des-riesgar. Funciones PURAS (sin I/O): el caller (sim/runner) provee
los drawdowns y conteos ya calculados.

Un "crash global" se confirma con un GATE de 4 condiciones (no alcanza con que caigan 2 índices):
  1. factor AR caído (GGAL en drawdown profundo)
  2. factor global caído (SPY en drawdown profundo)
  3. AMPLITUD: la mayoría del book está en drawdown (broad-based)
  4. VELOCIDAD: la caída reciente del global es brusca (repentina, no un goteo lento)

Solo si las 4 se cumplen se des-riesga, y la magnitud (severidad) escala la exposición de
forma progresiva (1.0 → exposure_floor). La histéresis (subir lento) la aplica el caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class RegimeDeriskConfig:
    enabled: bool = False
    # Gate de severidad: ambos factores deben caer al menos esto (drawdown, valor negativo).
    confirm_dd: float = -0.08
    # A esta profundidad de severidad la exposición llega al piso.
    full_dd: float = -0.20
    exposure_floor: float = 0.40
    # Amplitud: mínimo de nombres del book en drawdown sobre el total (broad-based).
    breadth_min: int = 4
    breadth_total: int = 6
    breadth_dd: float = -0.05  # umbral para contar un nombre como "en drawdown"
    # Velocidad: el global debe haber caído al menos esto en velocity_window (caída brusca).
    velocity_window: int = 15
    velocity_max: float = -0.06
    # Histéresis (la aplica el caller): suba máxima de exposición por rebalance.
    max_up_step: float = 0.10
    # Ventana del trailing drawdown de los factores.
    lookback: int = 200
    ar_proxy: str = "GGAL"
    global_proxy: str = "SPY"
    # Modo de señal: "factor_crash" (gate de 4 condiciones en pesos) o "vix" (percentil del VIX).
    signal: str = "factor_crash"
    vix_symbol: str = "VIX"
    vix_venue: str = "CBOE"
    vix_window: int = 252          # ventana del percentil rolling (~52 semanas), sin look-ahead
    vix_start_pct: float = 0.70    # desde acá empieza a bajar la exposición
    vix_full_pct: float = 0.95     # acá llega al piso (caos)


def regime_derisk_config_from_policy_dict(payload: Mapping[str, object] | None) -> RegimeDeriskConfig:
    """Construye el config desde el subárbol ``long_term_engine.regime_derisk`` (o defaults)."""
    p = dict(payload or {})
    return RegimeDeriskConfig(
        enabled=bool(p.get("enabled", False)),
        confirm_dd=float(p.get("confirm_dd", -0.08)),
        full_dd=float(p.get("full_dd", -0.20)),
        exposure_floor=float(p.get("exposure_floor", 0.40)),
        breadth_min=int(p.get("breadth_min", 4)),
        breadth_total=int(p.get("breadth_total", 6)),
        breadth_dd=float(p.get("breadth_dd", -0.05)),
        velocity_window=int(p.get("velocity_window", 15)),
        velocity_max=float(p.get("velocity_max", -0.06)),
        max_up_step=float(p.get("max_up_step", 0.10)),
        lookback=int(p.get("lookback", 200)),
        ar_proxy=str(p.get("ar_proxy", "GGAL")),
        global_proxy=str(p.get("global_proxy", "SPY")),
        signal=str(p.get("signal", "factor_crash")),
        vix_symbol=str(p.get("vix_symbol", "VIX")),
        vix_venue=str(p.get("vix_venue", "CBOE")),
        vix_window=int(p.get("vix_window", 252)),
        vix_start_pct=float(p.get("vix_start_pct", 0.70)),
        vix_full_pct=float(p.get("vix_full_pct", 0.95)),
    )


def validate_regime_derisk_config(cfg: RegimeDeriskConfig) -> None:
    """Falla rápido ante parámetros incoherentes."""
    if cfg.confirm_dd > 0 or cfg.full_dd > 0 or cfg.velocity_max > 0 or cfg.breadth_dd > 0:
        raise ValueError("regime_derisk drawdown/velocity thresholds must be <= 0")
    if cfg.full_dd > cfg.confirm_dd:
        raise ValueError("full_dd must be at least as deep as confirm_dd")
    if not (0.0 <= cfg.exposure_floor <= 1.0):
        raise ValueError("exposure_floor must be in [0, 1]")
    if not (0 < cfg.breadth_min <= cfg.breadth_total):
        raise ValueError("breadth_min must be in (0, breadth_total]")
    if cfg.velocity_window <= 0 or cfg.lookback <= 0:
        raise ValueError("velocity_window and lookback must be positive")
    if not (0.0 < cfg.max_up_step <= 1.0):
        raise ValueError("max_up_step must be in (0, 1]")
    if cfg.signal not in {"factor_crash", "vix"}:
        raise ValueError("regime_derisk signal must be 'factor_crash' or 'vix'")
    if not (0.0 <= cfg.vix_start_pct < cfg.vix_full_pct <= 1.0):
        raise ValueError("vix percentiles must satisfy 0 <= start < full <= 1")
    if cfg.vix_window <= 0:
        raise ValueError("vix_window must be positive")


def is_global_crash(
    *,
    dd_ar: float,
    dd_global: float,
    n_book_down: int,
    recent_global_return: float,
    cfg: RegimeDeriskConfig,
) -> bool:
    """GATE de 4 condiciones. TODAS deben cumplirse para considerar crash global."""
    return (
        dd_ar <= cfg.confirm_dd                       # 1. AR caído
        and dd_global <= cfg.confirm_dd               # 2. global caído
        and n_book_down >= cfg.breadth_min            # 3. amplitud (broad)
        and recent_global_return <= cfg.velocity_max  # 4. velocidad (brusco)
    )


def regime_exposure(
    *,
    dd_ar: float,
    dd_global: float,
    n_book_down: int,
    recent_global_return: float,
    cfg: RegimeDeriskConfig,
) -> float:
    """Exposición objetivo del largo en [exposure_floor, 1.0]. 1.0 salvo crash global confirmado."""
    if not cfg.enabled:
        return 1.0
    if not is_global_crash(
        dd_ar=dd_ar, dd_global=dd_global, n_book_down=n_book_down,
        recent_global_return=recent_global_return, cfg=cfg,
    ):
        return 1.0
    severity = min(abs(dd_ar), abs(dd_global))
    lo, hi = abs(cfg.confirm_dd), abs(cfg.full_dd)
    frac = 1.0 if hi <= lo else (severity - lo) / (hi - lo)
    frac = max(0.0, min(1.0, frac))
    return 1.0 - (1.0 - cfg.exposure_floor) * frac


# --- Modo VIX (percentil del "medidor de miedo", sin look-ahead) ---

def rolling_percentile(window_values, today_value) -> float:
    """Percentil de ``today_value`` dentro de ``window_values`` (la ventana INCLUYE hoy; sin
    mirar el futuro). Devuelve [0, 1]: 0.9 = más alto que el 90% de la ventana."""
    if not window_values:
        return 0.0
    return sum(1 for x in window_values if x <= today_value) / len(window_values)


def vix_regime_exposure(vix_percentile: float, *, start_pct: float, full_pct: float, e_min: float) -> float:
    """Exposición objetivo según el percentil del VIX. 1.0 en calma; baja lineal hasta ``e_min``
    en el caos. Más niebla (percentil alto), menos velocidad (menos exposición)."""
    if vix_percentile <= start_pct:
        return 1.0
    if vix_percentile >= full_pct:
        return e_min
    frac = (vix_percentile - start_pct) / (full_pct - start_pct)
    return 1.0 - (1.0 - e_min) * frac
