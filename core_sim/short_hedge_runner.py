"""Ejecución del sleeve corto como COBERTURA (hedge_static) — compartido research/producción.

Convierte el 30% corto en cobertura anti-factor: rebalanceo por bandas hacia la canasta
(GLD/WMT) + regla de des-riesgo a cash cuando el factor AR y el global caen juntos.

Este runner es la ÚNICA fuente de verdad del cableado del hedge: lo usan tanto
``scripts/run_wf_research_sim.py`` (investigación) como ``scripts/run_paper_live.py``
(producción), para que ambos se comporten idéntico. El ``resilient_snapshot`` se inyecta
como dependencia (vive en la capa de orquestación) para no acoplar core_sim a scripts.

Reglas de contabilidad (ver memoria ledger/bucket-cash-accounting):
  - budget = equity_total × weights.short  (NO equity_short, que es PnL).
  - deploya el budget completo, igual que el momentum → misma estructura que la cartera B.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable

from .short_hedge_engine import (
    ShortHedgeConfig,
    build_hedge_orders_intent,
    should_derisk_to_cash,
    trailing_drawdown,
)
from .short_term_day_runner import orders_intent_to_broker_orders

_HEDGE_VENUE = "XBUE"
_DERISK_LOOKBACK_DAYS = 200


def _xbue_closes(db: Any, symbol: str, day: date, lookback_days: int) -> list[float]:
    """Cierres XBUE ascendentes por fecha, para el drawdown de los proxies del des-riesgo."""
    rows = db.get_ohlcv(symbol, day - timedelta(days=lookback_days), day, _HEDGE_VENUE)
    return [float(r.close) for r in sorted(rows, key=lambda r: r.ts)]


def compute_derisk_to_cash(
    db: Any,
    day: date,
    hedge_cfg: ShortHedgeConfig,
    *,
    ar_proxy: str = "GGAL",
    global_proxy: str = "SPY",
) -> bool:
    """True si corresponde des-riesgar a cash (factor AR Y global ambos en drawdown)."""
    if not hedge_cfg.derisk_enabled:
        return False
    ar_dd = trailing_drawdown(_xbue_closes(db, ar_proxy, day, _DERISK_LOOKBACK_DAYS))
    gl_dd = trailing_drawdown(_xbue_closes(db, global_proxy, day, _DERISK_LOOKBACK_DAYS))
    return should_derisk_to_cash(
        ar_drawdown=ar_dd,
        global_drawdown=gl_dd,
        ar_drawdown_floor=float(hedge_cfg.derisk_ar_drawdown_floor),
        global_drawdown_floor=float(hedge_cfg.derisk_global_drawdown_floor),
    )


def run_hedge_sleeve_day(
    *,
    db: Any,
    day: date,
    ledger: Any,
    broker: Any,
    hedge_cfg: ShortHedgeConfig,
    hedge_whitelist: frozenset[str],
    weights_short: float,
    resilient_snapshot: Callable[[Any, date, Any], dict[str, Any]],
    ar_proxy: str = "GGAL",
    global_proxy: str = "SPY",
) -> list[dict]:
    """Corre el sleeve corto como cobertura para un día. Devuelve los fills generados.

    No persiste nada ni valúa el snapshot final: eso lo hace el orquestador caller.
    """
    snap_pre = resilient_snapshot(db, day, ledger)
    equity_total = float(snap_pre.get("equity_total", 0.0)) if isinstance(snap_pre, dict) else 0.0
    budget = equity_total * float(weights_short)
    if budget <= 0:
        return []

    hedge_bars: dict[str, dict[str, float]] = {}
    for sym in hedge_whitelist:
        rows = db.get_ohlcv(sym, day, day, _HEDGE_VENUE)
        if rows:
            b = rows[0]
            hedge_bars[sym] = {"open": b.open, "high": b.high, "low": b.low,
                               "close": b.close, "volume": b.volume}
    prices = {s: hb["close"] for s, hb in hedge_bars.items()}
    positions_qty = {
        s: float(p.get("qty", 0.0))
        for s, p in (snap_pre.get("positions") or {}).items()
        if str(p.get("bucket")) == "short" and s in hedge_whitelist
    }

    derisk = compute_derisk_to_cash(db, day, hedge_cfg, ar_proxy=ar_proxy, global_proxy=global_proxy)

    intents, _skips, _metrics = build_hedge_orders_intent(
        hedge_cfg,
        hedge_bucket_mtm=budget,
        hedge_cash=budget,  # deploya el budget completo (misma estructura que la cartera B)
        positions_qty=positions_qty,
        prices=prices,
        whitelist_hedge=hedge_whitelist,
        derisk_to_cash=derisk,
    )
    if not intents:
        return []
    orders = orders_intent_to_broker_orders(intents)
    fills = broker.fill_orders(day, orders, hedge_bars)
    return list(fills) if isinstance(fills, list) else []


def load_hedge_whitelist(repo_root: Any, policy_doc: dict[str, Any]) -> frozenset[str]:
    """Símbolos de la canasta de cobertura desde ``whitelist_hedge_file`` del policy."""
    import yaml
    from pathlib import Path

    rel = (policy_doc.get("symbols") or {}).get("whitelist_hedge_file")
    if not rel:
        return frozenset()
    doc = yaml.safe_load((Path(repo_root) / str(rel)).open(encoding="utf-8")) or {}
    return frozenset(str(s).strip().upper() for s in (doc.get("hedge") or []))
