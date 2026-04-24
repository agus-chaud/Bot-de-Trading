"""Long-term monthly pipeline: snapshot → engine → risk → broker-shaped orders.

Agent-teams-lite boundaries:
- **Data**: precios del daily_bars del sleeve largo + us_sessions del pipeline_context.
- **Engine**: `build_long_term_orders_intent` (targets, drift, rebalance gate, intents).
- **Risk**: whitelist US v1 (solo US en v1).
- **Core sim**: salida compatible con `PaperBrokerSim.fill_orders` (sin `price`).

Los inputs de sleeve (long_bucket_mtm, long_cash, positions_qty_long) vienen del caller
(backtester externo o pipeline_context); este módulo no los resuelve internamente.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Callable

from .long_term_engine import (
    LongTermEngineConfig,
    build_long_term_orders_intent,
    long_term_engine_config_from_policy_dict,
)
from .short_term_day_runner import load_merged_whitelist, orders_intent_to_broker_orders


def _whitelist_us_only(merged: dict[str, str]) -> frozenset[str]:
    """Filtra la whitelist combinada a sólo símbolos del mercado US."""
    return frozenset(sym for sym, mkt in merged.items() if mkt == "US")


def _empty_signal(skip_reason: str) -> dict[str, Any]:
    return {
        "engine": "long_term_v1",
        "intents": [],
        "skips": [{"symbol": "*", "reason": skip_reason}],
        "metrics": {"intents_generated": 0, "skip_reason": skip_reason},
    }


def _empty_proposal(halt_reason: str) -> dict[str, Any]:
    return {
        "orders_intent": [],
        "broker_orders": [],
        "long_metrics": {"intents_generated": 0, "halt_reason": halt_reason},
    }


def create_long_term_pipeline_handlers(
    policy_doc: dict[str, Any],
    repo_root: Path,
    ledger: Any,
) -> dict[str, Callable[..., Any]]:
    """Handlers listos para inyectar en `DailyEventBacktester` (signals/propose/risk).

    El caller pasa en `pipeline_context`:
      - us_sessions: frozenset[date]
      - long_bucket_mtm: float
      - long_cash: float
      - positions_qty_long: dict[str, float]
      - halt_long_engine: bool (default False)
      - data_quality_halt: bool (default False)
    """
    merged = load_merged_whitelist(repo_root, policy_doc)
    whitelist_us = _whitelist_us_only(merged)
    lt_cfg: LongTermEngineConfig = long_term_engine_config_from_policy_dict(
        policy_doc["long_term_engine"]
    )

    def generate_signals(**ctx: Any) -> dict[str, Any]:
        trading_day: date = ctx["trading_day"]
        daily_bars: dict[str, dict[str, float]] = ctx.get("daily_bars") or {}

        us_sessions = ctx.get("us_sessions")
        if us_sessions is None:
            return _empty_signal("missing_us_sessions")

        long_bucket_mtm = ctx.get("long_bucket_mtm")
        if long_bucket_mtm is None:
            return _empty_signal("missing_long_bucket_mtm")

        long_cash = ctx.get("long_cash")
        if long_cash is None:
            return _empty_signal("missing_long_cash")

        positions_qty_long = ctx.get("positions_qty_long")
        if positions_qty_long is None:
            return _empty_signal("missing_positions_qty_long")

        halt_long = bool(ctx.get("halt_long_engine", False))
        data_quality_halt = bool(ctx.get("data_quality_halt", False))

        # Precios del sleeve: cierre de cada símbolo de la whitelist US en daily_bars.
        prices: dict[str, float] = {}
        for sym in whitelist_us:
            bar = daily_bars.get(sym)
            if bar and "close" in bar and float(bar["close"]) > 0:
                prices[sym] = float(bar["close"])

        intents, skips, metrics = build_long_term_orders_intent(
            lt_cfg,
            trading_day=trading_day,
            us_sessions=frozenset(us_sessions),
            long_bucket_mtm=float(long_bucket_mtm),
            long_cash=float(long_cash),
            positions_qty=dict(positions_qty_long),
            prices=prices,
            whitelist_us=whitelist_us,
            halt_long_engine=halt_long,
            data_quality_halt=data_quality_halt,
        )
        return {
            "engine": "long_term_v1",
            "intents": intents,
            "skips": skips,
            "metrics": metrics,
        }

    def propose_orders(**ctx: Any) -> dict[str, Any]:
        signals = ctx.get("signals") or {}
        if not isinstance(signals, dict):
            return _empty_proposal("invalid_signals")

        intents: list[dict[str, Any]] = signals.get("intents") or []
        metrics: dict[str, Any] = signals.get("metrics") or {}

        if not intents:
            # No hay intents (no es día de rebalance, drift dentro de banda, halt, etc.)
            skip_reason = metrics.get("skip_reason") or "no_intents"
            return _empty_proposal(str(skip_reason))

        broker_orders = orders_intent_to_broker_orders(intents)
        return {
            "orders_intent": intents,
            "broker_orders": broker_orders,
            "long_metrics": dict(metrics),
        }

    def risk_check(**ctx: Any) -> list[dict[str, str | float]]:
        proposed = ctx.get("proposed_orders") or {}
        if isinstance(proposed, dict):
            broker_orders: list[dict[str, Any]] = proposed.get("broker_orders") or []
        else:
            broker_orders = list(proposed)

        # v1: long es sólo US → filtrar por whitelist_us
        approved: list[dict[str, str | float]] = []
        for order in broker_orders:
            sym = str(order.get("symbol", "")).strip().upper()
            if sym in whitelist_us and str(order.get("market", "")) == "US":
                approved.append(order)
        return approved

    return {
        "generate_signals": generate_signals,
        "propose_orders": propose_orders,
        "risk_check": risk_check,
    }


def create_long_term_monthly_backtester(
    policy_doc: dict[str, Any],
    repo_root: Path,
    ledger: Any,
    broker: Any,
    calendar_store: Any | None = None,
    corporate_actions_store: Any | None = None,
) -> Any:
    """Ensambla `DailyEventBacktester` con pipeline largo + broker + ledger."""
    from .event_engine import DailyEventBacktester

    h = create_long_term_pipeline_handlers(policy_doc, repo_root, ledger)

    def update_ledger(**kwargs: Any) -> dict[str, Any]:
        return ledger.mark_to_market(trading_day=kwargs["trading_day"], daily_bars=kwargs["daily_bars"])

    return DailyEventBacktester(
        generate_signals=h["generate_signals"],
        propose_orders=h["propose_orders"],
        risk_check=h["risk_check"],
        fill_orders=broker.fill_orders,
        update_ledger=update_ledger,
        calendar_store=calendar_store,
        corporate_actions_store=corporate_actions_store,
    )
