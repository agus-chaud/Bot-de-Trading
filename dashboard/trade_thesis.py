"""Per-position trade thesis for the dashboard (mejora #2).

El monitor mostraba "por qué FRENÓ (riesgo)" pero nunca "por qué tengo ESTA
posición". Acá se arma una tesis por posición: postura técnica + hechos de la
posición + factores a favor / en contra, interpretados SEGÚN EL LADO (un short
gana cuando el precio cae). La postura final orienta: Mantener / Atención / Revisar.

Honestidad: esta tesis es DERIVADA de los datos de mercado y del estado de la
posición. NO es el razonamiento original del motor al entrar (ese no se persiste
hoy — el campo fills.reason está vacío). Es reconstrucción, no la intención exacta.

Función pura: recibe las posiciones y un contexto de mercado ya cargado de la DB.
"""

from __future__ import annotations

from typing import Any

PositionContext = dict[str, dict[str, Any]]  # symbol -> {"closes": [...], "lag_days": int}


def _trend(momentum_pct: float | None, vs_sma_pct: float | None) -> str:
    if momentum_pct is None or vs_sma_pct is None:
        return "indefinido"
    if momentum_pct > 0.02 and vs_sma_pct > 0:
        return "alcista"
    if momentum_pct < -0.02 and vs_sma_pct < 0:
        return "bajista"
    return "lateral"


def _technical(closes: list[float]) -> dict[str, Any]:
    if len(closes) < 2:
        return {"trend": "indefinido", "momentum_pct": None, "vs_sma_pct": None, "last_close": None}
    last = closes[-1]
    momentum_pct = (last / closes[0] - 1.0) if closes[0] else None
    sma = sum(closes) / len(closes)
    vs_sma_pct = (last / sma - 1.0) if sma else None
    return {
        "trend": _trend(momentum_pct, vs_sma_pct),
        "momentum_pct": momentum_pct,
        "vs_sma_pct": vs_sma_pct,
        "last_close": last,
    }


def _is_short(bucket: str, qty: float) -> bool:
    # El bucket "short" es el sleeve de hedge; qty<0 también indica venta en corto.
    return bucket.lower() == "short" or qty < 0


def build_position_theses(
    positions: list[dict[str, Any]],
    context: PositionContext,
) -> list[dict[str, Any]]:
    """Build a thesis per open position. Orden: por valor de mercado desc."""
    out: list[dict[str, Any]] = []

    for pos in positions:
        symbol = str(pos["symbol"])
        bucket = str(pos.get("bucket") or "")
        qty = float(pos.get("qty") or 0.0)
        short = _is_short(bucket, qty)
        upnl = float(pos.get("unrealized_pnl") or 0.0)
        avg_cost = float(pos.get("avg_cost") or 0.0)
        cost_basis = abs(avg_cost * qty)
        upnl_pct = (upnl / cost_basis) if cost_basis else None

        ctx = context.get(symbol, {})
        tech = _technical(list(ctx.get("closes") or []))
        lag_days = int(ctx.get("lag_days") or 0)

        bull: list[str] = []  # a favor de la posición
        bear: list[str] = []  # en contra

        # Tendencia interpretada según el lado.
        trend = tech["trend"]
        if trend in ("alcista", "bajista"):
            favorable = (short and trend == "bajista") or (not short and trend == "alcista")
            msg = f"Precio en tendencia {trend}"
            (bull if favorable else bear).append(
                msg + (" (favorece el short)" if short else " (favorece el largo)")
                if favorable
                else msg + (" (en contra del short)" if short else " (en contra del largo)")
            )

        # PnL no realizado.
        if upnl_pct is not None:
            if upnl > 0:
                bull.append(f"PnL no realizado +{upnl_pct:.1%}")
            elif upnl < 0:
                bear.append(f"PnL no realizado {upnl_pct:.1%}")

        # Calidad de datos.
        if lag_days >= 2:
            bear.append(f"Cotización atrasada {lag_days} días — valuación incierta")
        if pos.get("stale"):
            bear.append("Marca sobre barra stale/imputada")

        # Postura final.
        if not bull and not bear:
            stance = "Atención"
        elif len(bear) > len(bull) or (upnl_pct is not None and upnl_pct <= -0.05):
            stance = "Revisar"
        elif bull and not bear:
            stance = "Mantener"
        else:
            stance = "Atención"

        out.append(
            {
                "symbol": symbol,
                "bucket": bucket,
                "market": pos.get("market"),
                "side": "short" if short else "long",
                "qty": qty,
                "market_value": pos.get("market_value"),
                "unrealized_pnl": upnl,
                "unrealized_pnl_pct": upnl_pct,
                "stance": stance,
                "technical": tech,
                "bull": bull,
                "bear": bear,
            }
        )

    out.sort(key=lambda t: abs(float(t.get("market_value") or 0.0)), reverse=True)
    return out
