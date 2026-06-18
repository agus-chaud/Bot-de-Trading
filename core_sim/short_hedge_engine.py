"""Motor de COBERTURA del sleeve corto (plan_hedge_short Fase 4).

El sleeve corto (30%) hoy hace momentum-long en otro mercado y termina flat: no cubre.
Este motor lo convierte en cobertura anti-factor explícita, en modo ``hedge_static``:

- Asignación de FACTOR estática hacia una canasta (GLD + KO, ADR-061), no ranking de
  momentum. Rebalanceo por bandas de drift (mismo criterio que el sleeve largo).
- **Regla de des-riesgo a cash** (mejora #4 del plan): cuando el factor AR (GGAL/PAMP)
  **y** el global (SPY) están AMBOS en drawdown, ningún activo de riesgo cubre → se
  vende la canasta y se sube cash. Ataca el régimen que la diversificación AR/global no
  cubre (ventanas 4/5, crash global dic-2025 → abr-2026).

Boundaries (igual que long_term_engine):
- Spec/policy: knobs en config/policy.research_hedge_short.v1.yaml (bloque ``short_hedge``).
- Data: el caller provee precios, posiciones, MTM del bucket y los drawdowns de los
  proxies; este módulo no resuelve datos ni aplica el split 30/70.
- Engine: funciones puras (targets, regla de cash, intents por bandas).

Reusa primitivas del sleeve largo (current_weights_mtm, drift_per_line_pp) para no
duplicar la matemática de pesos/drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Mapping, Sequence

from .long_term_engine import current_weights_mtm, drift_per_line_pp

# Mercado/venue de la canasta de cobertura: CEDEAR en pesos (XBUE/ARS).
_HEDGE_MARKET = "AR"


@dataclass(frozen=True)
class ShortHedgeConfig:
    """Bloque ``short_hedge`` del policy. Pesos como fracción del TOTAL (no del bucket)."""

    enabled: bool
    mode: str  # v1: "hedge_static"
    hedge_weight_total: float       # fracción del total destinada a cobertura (p. ej. 0.20)
    tactical_weight_total: float    # fracción del total para el momentum corto (p. ej. 0.10)
    drift_rebalance_threshold_pp: float
    hedge_lines: tuple[tuple[str, float], ...]  # (symbol, peso dentro del bloque hedge); suman 1.0
    derisk_enabled: bool
    derisk_ar_drawdown_floor: float | None      # negativo, p. ej. -0.10
    derisk_global_drawdown_floor: float | None


def validate_short_hedge_config(config: ShortHedgeConfig) -> None:
    """Fail fast ante policy inconsistente."""
    if config.mode != "hedge_static":
        raise ValueError("short_hedge v1 only supports mode='hedge_static'")
    if config.hedge_weight_total < 0 or config.tactical_weight_total < 0:
        raise ValueError("short_hedge weights must be non-negative")
    if not config.hedge_lines:
        raise ValueError("short_hedge requires at least one hedge line")
    total = sum(float(w) for _, w in config.hedge_lines)
    if abs(total - 1.0) > 1e-6:
        raise ValueError("hedge_lines target weights must sum to 1.0")
    if config.drift_rebalance_threshold_pp <= 0:
        raise ValueError("drift_rebalance_threshold_pp must be positive")
    if config.derisk_enabled:
        if config.derisk_ar_drawdown_floor is None or config.derisk_global_drawdown_floor is None:
            raise ValueError("derisk_to_cash enabled but drawdown floors are null")


def hedge_target_weights(config: ShortHedgeConfig) -> dict[str, float]:
    """Pesos objetivo dentro del bloque hedge (suman 1.0)."""
    return {str(sym): float(w) for sym, w in config.hedge_lines}


def trailing_drawdown(closes: Sequence[float]) -> float:
    """Drawdown actual (fracción ≤ 0) vs el máximo de la serie: close/peak - 1.

    El último valor es el precio de hoy; el peak es el máximo histórico de la ventana.
    Una serie vacía o sin pico positivo devuelve 0.0 (no hay drawdown medible).
    """
    peak = 0.0
    last = 0.0
    for c in closes:
        v = float(c)
        if v > peak:
            peak = v
        last = v
    if peak <= 0:
        return 0.0
    return last / peak - 1.0


def should_derisk_to_cash(
    *,
    ar_drawdown: float,
    global_drawdown: float,
    ar_drawdown_floor: float,
    global_drawdown_floor: float,
) -> bool:
    """True cuando el factor AR **y** el global están ambos en drawdown bajo su umbral.

    Ningún activo de riesgo cubre el crash global; ahí el hedge es CASH. La condición es
    AND (ambos): si solo uno cae, la canasta anti-factor todavía sirve. (plan mejora #4)
    """
    return (ar_drawdown <= ar_drawdown_floor) and (global_drawdown <= global_drawdown_floor)


def build_hedge_orders_intent(
    config: ShortHedgeConfig,
    *,
    hedge_bucket_mtm: float,
    hedge_cash: float,
    positions_qty: Mapping[str, float],
    prices: Mapping[str, float],
    whitelist_hedge: frozenset[str],
    derisk_to_cash: bool = False,
    data_quality_halt: bool = False,
) -> tuple[list[dict[str, object]], list[dict[str, str]], dict[str, object]]:
    """Intents de rebalanceo por bandas de la canasta de cobertura (modo hedge_static).

    - Sin gate de calendario: se rebalancea por bandas (drift > umbral) cualquier día.
    - ``derisk_to_cash=True`` → los targets pasan a 0 (vender la canasta, subir cash).
    - ``hedge_bucket_mtm`` es el presupuesto de cobertura (hedge_weight_total × equity),
      lo calcula el caller. Igual que el largo: si falta un precio, se aborta el ciclo
      (no rebalanceo parcial).
    """
    validate_short_hedge_config(config)
    skips: list[dict[str, str]] = []
    targets_full = hedge_target_weights(config)
    universe = sorted(targets_full.keys())

    metrics: dict[str, object] = {
        "engine": "short_hedge_v1",
        "mode": config.mode,
        "derisk_to_cash": bool(derisk_to_cash),
        "intents_generated": 0,
    }

    if data_quality_halt:
        skips.append({"symbol": "*", "reason": "data_quality_halt"})
        return [], skips, metrics

    for sym in universe:
        if sym not in whitelist_hedge:
            skips.append({"symbol": "*", "reason": f"symbol_not_whitelisted:{sym}"})
            return [], skips, metrics

    # Des-riesgo: objetivo todo a cash → targets en 0 (se venden las posiciones abiertas).
    targets = {s: 0.0 for s in universe} if derisk_to_cash else dict(targets_full)

    if hedge_bucket_mtm <= 0 and not derisk_to_cash:
        skips.append({"symbol": "*", "reason": "non_positive_hedge_bucket_mtm"})
        return [], skips, metrics

    # Precios requeridos para todo símbolo con target > 0 o posición abierta (para valuar/vender).
    needed = [s for s in universe if targets[s] > 0 or float(positions_qty.get(s, 0.0)) > 0]
    for sym in needed:
        if sym not in prices or float(prices[sym]) <= 0:
            skips.append({"symbol": "*", "reason": "missing_or_invalid_price_abort_cycle"})
            return [], skips, metrics

    current = current_weights_mtm(
        long_bucket_mtm=float(hedge_bucket_mtm) if hedge_bucket_mtm > 0 else 1.0,
        positions_qty=positions_qty,
        prices=prices,
        universe=universe,
    )
    drift_pp = drift_per_line_pp(targets, current)
    metrics["drift_pp_by_symbol"] = dict(drift_pp)
    metrics["current_weights"] = dict(current)
    metrics["target_weights"] = dict(targets)

    # Gate de bandas: rebalancear solo si alguna línea se salió de la banda de drift.
    if not any(float(v) > float(config.drift_rebalance_threshold_pp) for v in drift_pp.values()):
        skips.append({"symbol": "*", "reason": "within_drift_band"})
        return [], skips, metrics

    deltas = {s: float(targets[s]) - float(current.get(s, 0.0)) for s in universe}

    def reason_for(sym: str, delta: float) -> str:
        if derisk_to_cash:
            return "hedge_derisk_to_cash"
        return "hedge_rebalance_add" if delta >= 0 else "hedge_rebalance_trim"

    intents: list[dict[str, object]] = []

    # Pass 1: ventas (liberan cash) en orden ascendente de símbolo.
    cash_sim = float(hedge_cash)
    for sym in universe:
        delta_w = deltas[sym]
        if delta_w >= 0:
            continue
        px = float(prices[sym])
        notional = -delta_w * float(hedge_bucket_mtm)
        max_sell_qty = float(positions_qty.get(sym, 0.0))
        sell_qty = float(floor(min(max_sell_qty, notional / px))) if px > 0 else 0.0
        if sell_qty <= 0:
            continue
        trade_notional = sell_qty * px
        cash_sim += trade_notional
        intents.append(
            {
                "symbol": sym, "market": _HEDGE_MARKET, "bucket": "short", "side": "SELL",
                "qty": sell_qty, "intent_notional": float(trade_notional),
                "reason_code": reason_for(sym, delta_w),
                "target_weight": float(targets[sym]), "current_weight": float(current[sym]),
                "drift_pp": float(drift_pp[sym]),
                "risk_snapshot": {"hedge_bucket_mtm": float(hedge_bucket_mtm), "hedge_cash_sim": float(cash_sim)},
            }
        )

    # Pass 2: compras en orden ascendente, acotadas por el cash simulado.
    for sym in universe:
        delta_w = deltas[sym]
        if delta_w <= 0:
            continue
        px = float(prices[sym])
        notional = delta_w * float(hedge_bucket_mtm)
        buy_qty = float(floor(notional / px)) if px > 0 else 0.0
        trade_notional = buy_qty * px
        if trade_notional > cash_sim + 1e-9:
            buy_qty = float(floor(cash_sim / px)) if px > 0 else 0.0
            trade_notional = buy_qty * px
        if buy_qty <= 0:
            skips.append({"symbol": sym, "reason": "insufficient_cash_for_buy"})
            continue
        cash_sim -= trade_notional
        intents.append(
            {
                "symbol": sym, "market": _HEDGE_MARKET, "bucket": "short", "side": "BUY",
                "qty": buy_qty, "intent_notional": float(trade_notional),
                "reason_code": reason_for(sym, delta_w),
                "target_weight": float(targets[sym]), "current_weight": float(current[sym]),
                "drift_pp": float(drift_pp[sym]),
                "risk_snapshot": {"hedge_bucket_mtm": float(hedge_bucket_mtm), "hedge_cash_sim": float(cash_sim)},
            }
        )

    metrics["intents_generated"] = len(intents)
    return intents, skips, metrics


def short_hedge_config_from_policy_dict(payload: Mapping[str, object]) -> ShortHedgeConfig:
    """Construye el config tipado desde el subárbol ``short_hedge`` del policy."""
    lines_raw = payload.get("hedge_lines")
    if not isinstance(lines_raw, list) or not lines_raw:
        raise TypeError("short_hedge.hedge_lines must be a non-empty list")
    hedge_lines = tuple(
        (str(row["symbol"]).strip().upper(), float(row["target_weight"])) for row in lines_raw
    )
    derisk = payload.get("derisk_to_cash") or {}
    if not isinstance(derisk, dict):
        derisk = {}
    ar_floor = derisk.get("ar_factor_drawdown_floor")
    gl_floor = derisk.get("global_drawdown_floor")
    return ShortHedgeConfig(
        enabled=bool(payload.get("enabled", False)),
        mode=str(payload.get("mode", "hedge_static")),
        hedge_weight_total=float(payload.get("hedge_weight_total", 0.0)),
        tactical_weight_total=float(payload.get("tactical_weight_total", 0.0)),
        drift_rebalance_threshold_pp=float(payload.get("drift_rebalance_threshold_pp", 2.0)),
        hedge_lines=hedge_lines,
        derisk_enabled=bool(derisk.get("enabled", False)),
        derisk_ar_drawdown_floor=None if ar_floor is None else float(ar_floor),
        derisk_global_drawdown_floor=None if gl_floor is None else float(gl_floor),
    )
