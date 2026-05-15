"""Long-term monthly pipeline: snapshot → engine → risk → broker-shaped orders.

Agent-teams-lite boundaries:
- **Data**: precios del daily_bars del sleeve largo + sesiones de calendario (US o AR) según policy.
- **Engine**: `build_long_term_orders_intent` (targets, drift, rebalance gate, intents).
- **Risk**: whitelist BYMA (pesos) o US según `satellite_markets` en policy.
- **Core sim**: salida compatible con `PaperBrokerSim.fill_orders` (sin `price`).

Los inputs de sleeve (long_bucket_mtm, long_cash, positions_qty_long) vienen del caller
(backtester externo o pipeline_context); este módulo no los resuelve internamente.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Callable

import yaml

from .long_term_engine import (
    LongTermEngineConfig,
    build_long_term_orders_intent,
    long_rebalance_calendar_from_rule,
    long_sleeve_trade_market,
    long_term_engine_config_from_policy_dict,
)
from .risk_guardrails import check_long_risk, log_risk_cycle
from .short_term_day_runner import load_merged_whitelist, orders_intent_to_broker_orders


def _whitelist_long_symbols_us_merged(
    merged: dict[str, str], lt_cfg: LongTermEngineConfig
) -> frozenset[str]:
    """Universo del sleeve largo ∩ tickers marcados US en whitelist combinada."""
    universe = frozenset(
        str(sym).strip().upper() for sym, _ in (*lt_cfg.core_lines, *lt_cfg.satellite_lines)
    )
    return frozenset(sym for sym in universe if merged.get(sym) == "US")


def _whitelist_long_ar_from_symbol_files(
    repo_root: Path,
    policy_doc: dict[str, Any],
    lt_cfg: LongTermEngineConfig,
) -> frozenset[str]:
    """Operables BYMA para el largo: intersección de líneas del policy con AR ∪ CEDEAR.

    El merge global (`load_merged_whitelist`) puede marcar un ticker como US (p. ej. SPY ETF)
    aunque el sleeve largo opere el mismo símbolo como CEDEAR; aquí prevalecen las listas
    `whitelist_ar_file` + `whitelist_cedear_file` para no bloquear el universo largo AR.
    """
    universe = frozenset(
        str(sym).strip().upper() for sym, _ in (*lt_cfg.core_lines, *lt_cfg.satellite_lines)
    )
    sym_cfg = policy_doc["symbols"]
    ar_path = repo_root / str(sym_cfg["whitelist_ar_file"])
    listed: set[str] = set()
    with ar_path.open(encoding="utf-8") as f:
        ar_doc = yaml.safe_load(f) or {}
    for raw in ar_doc.get("stocks", []) or []:
        su = str(raw).strip().upper()
        if su:
            listed.add(su)
    ced_raw = sym_cfg.get("whitelist_cedear_file")
    if ced_raw:
        ced_path = repo_root / str(ced_raw)
        if ced_path.is_file():
            with ced_path.open(encoding="utf-8") as f:
                ced_doc = yaml.safe_load(f) or {}
            for raw in ced_doc.get("stocks", []) or []:
                su = str(raw).strip().upper()
                if su:
                    listed.add(su)
    return frozenset(s for s in universe if s in listed)


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


def _apply_corporate_actions(
    positions_qty_long: dict[str, float],
    corporate_actions: list[dict[str, Any]],
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Aplica corporate actions a las posiciones antes de calcular pesos.

    Retorna (adjusted_qty, applied_list).  No muta el dict original.
    Solo procesa splits; dividends no ajustan qty (efecto en cash resuelto en otro lugar).
    """
    adjusted = dict(positions_qty_long)
    applied: list[dict[str, Any]] = []
    for action in corporate_actions:
        symbol = str(action.get("symbol", "")).strip().upper()
        action_type = str(action.get("action_type", ""))
        value = float(action.get("value", 1.0))
        if symbol not in adjusted:
            continue
        if action_type == "split":
            adjusted[symbol] = adjusted[symbol] * value
            applied.append({"symbol": symbol, "action_type": action_type, "value": value})
        # dividend: no ajusta qty
    return adjusted, applied


def create_long_term_pipeline_handlers(
    policy_doc: dict[str, Any],
    repo_root: Path,
    ledger: Any,
    *,
    calendar_store: Any = None,
    db: Any = None,
) -> dict[str, Callable[..., Any]]:
    """Handlers listos para inyectar en `DailyEventBacktester` (signals/propose/risk).

    El caller pasa en `pipeline_context`:
      - Para US: ``us_sessions: frozenset[date]`` (o derive vía calendar_store).
      - Para AR pesos: ``ar_business_days: frozenset[date]`` (o derive vía calendar_store.ar_business_days).
      - long_bucket_mtm: float
      - long_cash: float
      - positions_qty_long: dict[str, float]
      - halt_long_engine: bool (default False)
      - data_quality_halt: bool (default False)
    """
    merged = load_merged_whitelist(repo_root, policy_doc)
    lt_cfg: LongTermEngineConfig = long_term_engine_config_from_policy_dict(
        policy_doc["long_term_engine"]
    )
    cal_kind = long_rebalance_calendar_from_rule(lt_cfg.rebalance_rule)
    whitelist_long = (
        _whitelist_long_ar_from_symbol_files(repo_root, policy_doc, lt_cfg)
        if cal_kind == "AR"
        else _whitelist_long_symbols_us_merged(merged, lt_cfg)
    )

    trade_mkt_expected = long_sleeve_trade_market(lt_cfg)

    max_daily_long = float(policy_doc["risk"]["max_daily_loss_long_pct"])

    def generate_signals(**ctx: Any) -> dict[str, Any]:
        trading_day: date = ctx["trading_day"]
        daily_bars: dict[str, dict[str, float]] = ctx.get("daily_bars") or {}

        if cal_kind == "US":
            calendar_sessions = ctx.get("us_sessions") or (
                calendar_store.us_sessions if calendar_store is not None else None
            )
            miss_reason = "missing_us_sessions"
        else:
            calendar_sessions = ctx.get("ar_business_days") or (
                calendar_store.ar_business_days if calendar_store is not None else None
            )
            miss_reason = "missing_ar_business_days"

        if calendar_sessions is None:
            return _empty_signal(miss_reason)

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

        # Aplicar corporate actions antes de calcular pesos para evitar rebalanceos fantasma.
        market_open: dict[str, Any] = ctx.get("market_open") or {}
        corporate_actions: list[dict[str, Any]] = market_open.get("corporate_actions") or []
        adjusted_positions_qty, ca_applied = _apply_corporate_actions(
            positions_qty_long, corporate_actions
        )

        # Precios del sleeve: cierre por símbolo según whitelist del largo.
        prices: dict[str, float] = {}
        for sym in whitelist_long:
            bar = daily_bars.get(sym)
            if bar and "close" in bar and float(bar["close"]) > 0:
                prices[sym] = float(bar["close"])

        intents, skips, metrics = build_long_term_orders_intent(
            lt_cfg,
            trading_day=trading_day,
            calendar_sessions=frozenset(calendar_sessions),
            long_bucket_mtm=float(long_bucket_mtm),
            long_cash=float(long_cash),
            positions_qty=adjusted_positions_qty,
            prices=prices,
            whitelist_long=whitelist_long,
            halt_long_engine=halt_long,
            data_quality_halt=data_quality_halt,
        )

        if ca_applied:
            metrics = dict(metrics)
            metrics["corporate_actions_applied"] = ca_applied
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

        snap = ledger.mark_to_market(trading_day=ctx["trading_day"], daily_bars=ctx["daily_bars"])
        long_bucket = snap.get("long_bucket") or {}
        guardrail = check_long_risk(long_bucket, {"max_daily_long": max_daily_long})
        if not guardrail.allowed:
            log_risk_cycle(
                engine="long",
                date=ctx["trading_day"].isoformat(),
                guardrail=guardrail,
                orders_proposed=0,
                orders_filled=0,
                kill_switch_active=False,
            )
            return _empty_proposal(guardrail.reason)

        intents: list[dict[str, Any]] = signals.get("intents") or []
        metrics: dict[str, Any] = signals.get("metrics") or {}

        if not intents:
            # No hay intents (no es día de rebalance, drift dentro de banda, halt, etc.)
            skip_reason = metrics.get("skip_reason") or "no_intents"
            return _empty_proposal(str(skip_reason))

        broker_orders = orders_intent_to_broker_orders(intents)
        log_risk_cycle(
            engine="long",
            date=ctx["trading_day"].isoformat(),
            guardrail=guardrail,
            orders_proposed=len(intents),
            orders_filled=0,
            kill_switch_active=False,
        )
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

        snap = ledger.mark_to_market(trading_day=ctx["trading_day"], daily_bars=ctx["daily_bars"])
        long_bucket = snap.get("long_bucket") or {}
        guardrail = check_long_risk(long_bucket, {"max_daily_long": max_daily_long})
        if not guardrail.allowed:
            return []

        # Filtrar por whitelist del sleeve y mercado esperado según policy.
        approved: list[dict[str, str | float]] = []
        for order in broker_orders:
            sym = str(order.get("symbol", "")).strip().upper()
            if sym in whitelist_long and str(order.get("market", "")).upper() == trade_mkt_expected:
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
    db: Any = None,
) -> Any:
    """Ensambla `DailyEventBacktester` con pipeline largo + broker + ledger."""
    from .event_engine import DailyEventBacktester

    h = create_long_term_pipeline_handlers(policy_doc, repo_root, ledger, calendar_store=calendar_store, db=db)

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
