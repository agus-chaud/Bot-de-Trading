"""Short-term daily pipeline: Data → Engine → Risk → broker-shaped orders.

Agent-teams-lite boundaries:
- **Data**: historial OHLCV + whitelist + percentil de volumen cross-section.
- **Engines**: `short_term_engine` (candidatos, ranking, intents).
- **Risk**: kill switch (DD mensual), pérdida diaria bucket corto, ventanas no-trade
  (intradía vía `session_minutes_from_open` opcional), `halt_on_data_quality`.
- **Allocator**: caps 30/70 (corto vs total) y 20/80 (AR/US sobre total) en el sizing.
- **Core sim**: salida compatible con `PaperBrokerSim.fill_orders` (sin `price`).
"""

from __future__ import annotations

import statistics
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import yaml

if TYPE_CHECKING:
    from data.storage import MarketDB

from .allocator import AllocationGeo, AllocationWeights, compute_allocation
from .risk_guardrails import check_and_persist_kill_switch, check_short_risk, check_stop_loss, log_risk_cycle
from .short_term_engine import (
    RiskCaps,
    ShortEngineConfig,
    build_orders_intent,
    compute_signal_candidates,
    rank_top_k_by_market,
)


def load_merged_whitelist(repo_root: Path, policy_doc: dict[str, Any]) -> dict[str, str]:
    """Return `symbol_upper -> market` for US and AR combined (+ inline lists)."""
    sym_cfg = policy_doc["symbols"]
    merged: dict[str, str] = {}
    for raw in sym_cfg.get("inline_us", []) or []:
        merged[str(raw).strip().upper()] = "US"
    for raw in sym_cfg.get("inline_ar", []) or []:
        merged[str(raw).strip().upper()] = "AR"

    us_path = repo_root / str(sym_cfg["whitelist_us_file"])
    ar_path = repo_root / str(sym_cfg["whitelist_ar_file"])
    with us_path.open(encoding="utf-8") as f:
        us_doc = yaml.safe_load(f) or {}
    with ar_path.open(encoding="utf-8") as f:
        ar_doc = yaml.safe_load(f) or {}

    for bucket in ("etfs", "stocks"):
        for raw in us_doc.get(bucket, []) or []:
            merged[str(raw).strip().upper()] = "US"
    for raw in ar_doc.get("stocks", []) or []:
        merged[str(raw).strip().upper()] = "AR"
    return merged


def cross_sectional_volume_percentiles(
    daily_bars: dict[str, dict[str, float]],
    symbols: list[str],
) -> dict[str, float]:
    """Percentil [0,1] del volumen del día entre los símbolos con dato válido."""
    pairs: list[tuple[str, float]] = []
    for sym in symbols:
        bar = daily_bars.get(sym)
        if bar is None or "volume" not in bar:
            continue
        vol = float(bar["volume"])
        if vol < 0 or not (vol == vol):  # NaN
            continue
        pairs.append((sym, vol))
    if not pairs:
        return {s: 0.0 for s in symbols}
    ordered = sorted(pairs, key=lambda item: item[1])
    n = len(ordered)
    out: dict[str, float] = {s: 0.0 for s in symbols}
    if n == 1:
        out[ordered[0][0]] = 1.0
        return out
    for i, (sym, _) in enumerate(ordered):
        out[sym] = (i + 0.5) / n
    return out


def _volatility_20d_from_closes(closes: list[float]) -> float | None:
    """Desviación estándar de los últimos 20 retornos simples diarios (21 cierres)."""
    if len(closes) < 21:
        return None
    window = closes[-21:]
    rets: list[float] = []
    for i in range(1, len(window)):
        prev = window[i - 1]
        cur = window[i]
        if prev <= 0 or cur <= 0:
            return None
        rets.append((cur / prev) - 1.0)
    last20 = rets[-20:]
    if len(last20) < 20:
        return None
    return float(statistics.pstdev(last20))


def build_market_snapshot_rows(
    *,
    trading_day: date,
    daily_bars: dict[str, dict[str, float]],
    history_by_symbol: dict[str, list[dict[str, float]]],
    merged_whitelist: dict[str, str],
    market_open: dict[str, Any],
    ste_cfg: ShortEngineConfig,
    sector_map: dict[str, str] | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    """Arma filas listas para `compute_signal_candidates` + skips de whitelist/datos."""
    sector_map = sector_map or {}
    skipped: list[dict[str, str]] = []
    symbols_in_bars = [s for s in merged_whitelist if s in daily_bars]
    vol_pct = cross_sectional_volume_percentiles(daily_bars, symbols_in_bars)

    rows: list[dict[str, object]] = []
    for sym in sorted(merged_whitelist):
        market = merged_whitelist[sym]
        if sym not in daily_bars:
            skipped.append({"symbol": sym, "reason": "not_in_daily_bars"})
            continue
        bar = daily_bars[sym]
        if "close" not in bar or "volume" not in bar:
            skipped.append({"symbol": sym, "reason": "missing_close_or_volume"})
            continue
        close_today = float(bar["close"])
        if close_today <= 0:
            skipped.append({"symbol": sym, "reason": "invalid_close"})
            continue

        hist = history_by_symbol.get(sym, [])
        if len(hist) < ste_cfg.momentum_lookback_days:
            skipped.append({"symbol": sym, "reason": "insufficient_history"})
            continue

        closes_hist = [float(h["close"]) for h in hist]
        if any(c <= 0 for c in closes_hist):
            skipped.append({"symbol": sym, "reason": "invalid_history_close"})
            continue

        combined = closes_hist + [close_today]
        close_n_days_ago = combined[-(ste_cfg.momentum_lookback_days + 1)]
        vol_20d = _volatility_20d_from_closes(combined)
        if vol_20d is None:
            skipped.append({"symbol": sym, "reason": "insufficient_returns_for_vol"})
            continue

        session_valid = (
            bool(market_open.get("is_us_session")) if market == "US" else bool(market_open.get("is_ar_business_day"))
        )

        rows.append(
            {
                "symbol": sym,
                "market": market,
                "close": close_today,
                "close_n_days_ago": float(close_n_days_ago),
                "volume_percentile": float(vol_pct.get(sym, 0.0)),
                "vol_20d": float(vol_20d),
                "session_valid": session_valid,
                "sector": sector_map.get(sym, "UNKNOWN"),
            }
        )

    del trading_day  # reservado para logs futuros
    return rows, skipped


def us_regular_session_length_minutes() -> int:
    """Sesión regular US 9:30–16:00 (NY), sin contar extended hours."""
    return 390


def in_no_trade_window(
    *,
    no_trade_first: int,
    no_trade_last: int,
    session_minutes_from_open: int | None,
    session_length_minutes: int | None = None,
) -> bool:
    """True si, con reloj de sesión, no se debe operar (primeros/últimos N min)."""
    if session_minutes_from_open is None:
        return False
    slen = int(session_length_minutes or us_regular_session_length_minutes())
    m = int(session_minutes_from_open)
    if m < 0 or m >= slen:
        return True
    if m < int(no_trade_first):
        return True
    if m >= slen - int(no_trade_last):
        return True
    return False


def portfolio_market_value_by_market(snapshot: dict[str, Any]) -> dict[str, float]:
    """Suma de MV por venue (cualquier bucket) para 20/80."""
    by_m = {"US": 0.0, "AR": 0.0}
    for pos in (snapshot.get("positions") or {}).values():
        m = str(pos.get("market", "")).strip().upper()
        v = float(pos.get("market_value", 0.0))
        if m in by_m:
            by_m[m] += v
    return by_m


def _data_quality_broken(
    skipped_whitelist: list[dict[str, str]],
) -> bool:
    """Fallo de calidad en barras *presentes* (close/volumen/histórico inválido).

    `not_in_daily_bars` se ignora: un universo parcial en `daily_bars` es operativo, no
    se exige fila por cada símbolo de la lista blanca en cada corrida.
    """
    bad = {
        "missing_close_or_volume",
        "invalid_close",
        "invalid_history_close",
        "invalid_price",
        "insufficient_history",
        "insufficient_returns_for_vol",
    }
    return any(s.get("reason") in bad for s in (skipped_whitelist or []))


def _short_bucket_exposure(snapshot: dict[str, Any]) -> tuple[float, dict[str, float], dict[str, float]]:
    """(mv_total_short, notional_by_symbol, sector_pct_within_short_mv)."""
    positions = snapshot.get("positions") or {}
    sector_mv: dict[str, float] = {}
    notionals: dict[str, float] = {}
    mv_total = 0.0
    for sym, pos in positions.items():
        if str(pos.get("bucket")) != "short":
            continue
        mv = float(pos.get("market_value", 0.0))
        notionals[sym] = mv
        mv_total += mv
        sec = "UNKNOWN"
        sector_mv[sec] = sector_mv.get(sec, 0.0) + mv
    sector_pct: dict[str, float] = {}
    if mv_total > 0:
        for sec, mv in sector_mv.items():
            sector_pct[sec] = mv / mv_total
    return mv_total, notionals, sector_pct


def orders_intent_to_broker_orders(
    intents: list[dict[str, Any]],
) -> list[dict[str, str | float]]:
    """Mapea `orders_intent` a payload mínimo de `PaperBrokerSim.fill_orders`."""
    out: list[dict[str, str | float]] = []
    for intent in intents:
        out.append(
            {
                "symbol": str(intent["symbol"]),
                "side": str(intent["side"]),
                "qty": float(intent["qty"]),
                "market": str(intent["market"]),
                "bucket": str(intent.get("bucket", "short")),
            }
        )
    return out


def create_short_term_pipeline_handlers(
    policy_doc: dict[str, Any],
    repo_root: Path,
    ledger: Any,
    db: "MarketDB | None" = None,
) -> dict[str, Callable[..., Any]]:
    """Handlers listos para inyectar en `DailyEventBacktester` (signals/propose/risk)."""
    merged = load_merged_whitelist(repo_root, policy_doc)
    ste_raw = policy_doc["short_term_engine"]
    ste = ShortEngineConfig(
        momentum_lookback_days=int(ste_raw["momentum_lookback_days"]),
        liquidity_percentile_min=float(ste_raw["liquidity_percentile_min"]),
        volatility_20d_max=float(ste_raw["volatility_20d_max"]),
        top_k_per_market=int(ste_raw["top_k_per_market"]),
        risk_budget_trade_pct=float(ste_raw["risk_budget_trade_pct"]),
        allow_leverage=bool(ste_raw.get("allow_leverage", False)),
    )
    risk_cfg = policy_doc["risk"]
    risk_caps = RiskCaps(
        max_position_pct=float(risk_cfg["max_notional_per_ticker_pct"]),
        max_sector_pct=float(risk_cfg["max_sector_pct"]),
    )
    short_w = float(policy_doc["weights"]["short"])
    g_ar = float(policy_doc["geo"]["AR"])
    g_us = float(policy_doc["geo"]["US"])
    kill_dd = float(policy_doc["short_kill_switch_monthly_dd"])
    max_daily_short = float(risk_cfg["max_daily_loss_short_pct"])
    halt_on_dq = bool(risk_cfg.get("halt_on_data_quality", True))
    no_trade_first = int(risk_cfg["no_trade_first_minutes"])
    no_trade_last = int(risk_cfg["no_trade_last_minutes"])
    _sl_raw = risk_cfg.get("stop_loss") or {}
    stop_loss_config = {
        "atr_multiplier": float(_sl_raw.get("atr_multiplier", 2.0)),
        "atr_lookback": int(_sl_raw.get("atr_lookback", 14)),
        "fallback_pct_us": float(_sl_raw.get("fallback_pct_us", -0.05)),
        "fallback_pct_ar": float(_sl_raw.get("fallback_pct_ar", -0.08)),
    }

    def _empty_proposal(halt_reason: str) -> dict[str, Any]:
        return {
            "orders_intent": [],
            "broker_orders": [],
            "skip_sizing": [],
            "sizing_metrics": {
                "intents_generated": 0,
                "symbols_skipped_after_sizing": 0,
                "symbols_selected": 0,
                "halt_reason": halt_reason,
            },
        }

    from .risk_guardrails import GuardrailResult

    def _check_risk_with_optional_db(
        sb: dict,
        flags: dict,
        risk_config: dict,
        now_minutes_from_open: int | None,
        trading_day: date,
    ) -> "GuardrailResult":
        """When db is available, replace the stateless kill switch check with the persisted one.

        Checks run in the same order as check_short_risk:
        data_quality → no_trade_window → kill_switch → daily_loss.
        The only difference is that kill_switch uses check_and_persist_kill_switch when db is set.
        """
        if db is None:
            return check_short_risk(sb, flags, risk_config, now_minutes_from_open)

        # data_quality
        halt_on_dq = bool(flags.get("halt_on_data_quality", True))
        data_ok = bool(flags.get("data_quality_ok", True))
        if halt_on_dq and not data_ok:
            return GuardrailResult(
                allowed=False,
                reason="halt_data_quality",
                meta={"halt_on_data_quality": halt_on_dq, "data_quality_ok": data_ok},
            )

        # no_trade_window
        no_trade_first = int(risk_config.get("no_trade_first", 0))
        no_trade_last = int(risk_config.get("no_trade_last", 0))
        if now_minutes_from_open is not None and in_no_trade_window(
            no_trade_first=no_trade_first,
            no_trade_last=no_trade_last,
            session_minutes_from_open=now_minutes_from_open,
            session_length_minutes=us_regular_session_length_minutes(),
        ):
            return GuardrailResult(
                allowed=False,
                reason="no_trade_window",
                meta={"now_minutes_from_open": now_minutes_from_open},
            )

        # kill_switch — persisted path
        ks_result = check_and_persist_kill_switch(sb, risk_config, db, engine="short", today=trading_day)
        if not ks_result.allowed:
            return ks_result

        # daily_loss
        max_daily_short = float(risk_config.get("max_daily_short", -0.02))
        daily_ret = float(sb.get("daily_return", 0.0))
        if max_daily_short < 0.0 and daily_ret < max_daily_short:
            return GuardrailResult(
                allowed=False,
                reason="short_daily_loss_limit",
                meta={"daily_return": daily_ret, "limit": max_daily_short},
            )

        monthly_dd = float(sb.get("monthly_drawdown", 0.0))
        return GuardrailResult(allowed=True, reason="ok", meta={"monthly_drawdown": monthly_dd, "daily_return": daily_ret})

    def generate_signals(**ctx: Any) -> dict[str, Any]:
        history_by_symbol = ctx.get("history_by_symbol") or {}
        if not isinstance(history_by_symbol, dict):
            history_by_symbol = {}
        sector_map = ctx.get("sector_map") or {}
        if not isinstance(sector_map, dict):
            sector_map = {}

        rows, skipped_whitelist = build_market_snapshot_rows(
            trading_day=ctx["trading_day"],
            daily_bars=ctx["daily_bars"],
            history_by_symbol=history_by_symbol,
            merged_whitelist=merged,
            market_open=ctx["market_open"],
            ste_cfg=ste,
            sector_map=sector_map,
        )
        candidates, skipped_signal = compute_signal_candidates(rows, ste)
        selected = rank_top_k_by_market(candidates, ste.top_k_per_market)
        daily = ctx.get("daily_bars") or {}
        data_ok = bool(daily) and not _data_quality_broken(skipped_whitelist)
        return {
            "engine": "short_term_v1",
            "selected": selected,
            "skipped_whitelist_or_data": skipped_whitelist,
            "skipped_signal": skipped_signal,
            "metrics": {
                "whitelist_size": len(merged),
                "snapshot_rows": len(rows),
                "candidates": len(candidates),
                "selected": len(selected),
            },
            "risk_flags": {
                "data_quality_ok": data_ok,
                "halt_on_data_quality": halt_on_dq,
            },
        }

    def propose_orders(**ctx: Any) -> dict[str, Any]:
        signals = ctx["signals"]
        if not isinstance(signals, dict):
            return _empty_proposal("invalid_signals")
        flags = signals.get("risk_flags") or {}

        selected = signals.get("selected") or []
        snap = ledger.mark_to_market(trading_day=ctx["trading_day"], daily_bars=ctx["daily_bars"])
        equity_total = float(snap["equity_total"])
        short_mv, notionals, sector_pct = _short_bucket_exposure(snap)
        short_equity = max(short_mv, equity_total * short_w, 1.0)
        short_cash = min(float(snap["cash"]), short_equity)

        sb = snap.get("short_bucket") or {}
        risk_config = {
            "kill_dd": kill_dd,
            "max_daily_short": max_daily_short,
            "no_trade_first": no_trade_first,
            "no_trade_last": no_trade_last,
        }
        guardrail = _check_risk_with_optional_db(sb, flags, risk_config, ctx.get("session_minutes_from_open"), ctx["trading_day"])
        if not guardrail.allowed:
            log_risk_cycle(
                engine="short",
                date=ctx["trading_day"].isoformat(),
                guardrail=guardrail,
                orders_proposed=0,
                orders_filled=0,
                kill_switch_active=(guardrail.reason == "short_monthly_kill_switch"),
            )
            return _empty_proposal(guardrail.reason)

        alloc = compute_allocation(
            equity_total=equity_total,
            positions_snapshot=snap.get("positions") or {},
            cash=float(snap.get("cash", 0.0)),
            weights=AllocationWeights(short=short_w, long=1.0 - short_w),
            geo=AllocationGeo(AR=g_ar, US=g_us),
        )
        short_tranche_headroom = alloc.headroom_by_bucket["short-AR"] + alloc.headroom_by_bucket["short-US"]
        geo_headroom = {
            "US": alloc.headroom_by_bucket["short-US"],
            "AR": alloc.headroom_by_bucket["short-AR"],
        }

        lot_raw = ctx.get("lot_size_by_market") or {}
        lot_size_by_market = lot_raw if isinstance(lot_raw, dict) else {}

        intents, skip_sizing, metrics = build_orders_intent(
            selected,
            short_equity=short_equity,
            short_cash=short_cash,
            risk_budget_trade_pct=ste.risk_budget_trade_pct,
            risk_caps=risk_caps,
            current_symbol_notional=notionals,
            current_sector_exposure_pct=sector_pct,
            lot_size_by_market={str(k): int(v) for k, v in lot_size_by_market.items()},
            kill_switch_active=False,
            short_tranche_headroom=short_tranche_headroom,
            geo_headroom=geo_headroom,
        )
        broker_orders = orders_intent_to_broker_orders(intents)

        # Evaluate stop loss for every open short position regardless of guardrail state
        all_positions = snap.get("positions") or {}
        sl_positions: dict[str, dict] = {}
        for sym, pos in all_positions.items():
            if str(pos.get("bucket")) == "short":
                sl_positions[sym] = {
                    "entry_price": float(pos.get("avg_cost", 0.0)),
                    "qty": float(pos.get("qty", 0.0)),
                    "market": str(pos.get("market", "US")),
                }
        ohlcv_history: dict[str, list[dict]] = ctx.get("ohlcv_history") or {}
        sl_triggered = check_stop_loss(
            positions=sl_positions,
            daily_bars=ctx["daily_bars"],
            price_history=ohlcv_history,
            config=stop_loss_config,
        )
        for sym in sl_triggered:
            pos_info = sl_positions[sym]
            bar = ctx["daily_bars"].get(sym) or {}
            broker_orders.append(
                {
                    "symbol": sym,
                    "side": "sell",
                    "qty": float(pos_info["qty"]),
                    "price": float(bar.get("close", 0.0)),
                    "market": pos_info["market"],
                    "bucket": "short",
                    "reason": "stop_loss",
                }
            )

        log_risk_cycle(
            engine="short",
            date=ctx["trading_day"].isoformat(),
            guardrail=guardrail,
            orders_proposed=len(intents),
            orders_filled=0,  # fills happen later in the event pipeline
            kill_switch_active=False,
        )
        return {
            "orders_intent": intents,
            "broker_orders": broker_orders,
            "skip_sizing": skip_sizing,
            "sizing_metrics": metrics,
        }

    def risk_check(**ctx: Any) -> list[dict[str, str | float]]:
        proposed = ctx["proposed_orders"]
        if isinstance(proposed, dict):
            broker_orders = proposed.get("broker_orders") or []
        else:
            broker_orders = proposed

        # Stop loss orders always pass — split them out before the guardrail check
        stop_loss_orders = [o for o in broker_orders if o.get("reason") == "stop_loss"]
        normal_orders = [o for o in broker_orders if o.get("reason") != "stop_loss"]

        signals = ctx.get("signals") or {}
        flags = signals.get("risk_flags") or {}

        snap = ledger.mark_to_market(trading_day=ctx["trading_day"], daily_bars=ctx["daily_bars"])
        sb = snap.get("short_bucket") or {}
        risk_config = {
            "kill_dd": kill_dd,
            "max_daily_short": max_daily_short,
            "no_trade_first": no_trade_first,
            "no_trade_last": no_trade_last,
        }
        guardrail = _check_risk_with_optional_db(sb, flags, risk_config, ctx.get("session_minutes_from_open"), ctx["trading_day"])

        approved: list[dict[str, str | float]] = list(stop_loss_orders)
        if guardrail.allowed:
            for order in normal_orders:
                sym = str(order["symbol"]).strip().upper()
                if sym not in merged:
                    continue
                if merged[sym] != str(order.get("market", "")):
                    continue
                approved.append(order)
        return approved

    return {
        "generate_signals": generate_signals,
        "propose_orders": propose_orders,
        "risk_check": risk_check,
    }


def create_short_term_daily_backtester(
    policy_doc: dict[str, Any],
    repo_root: Path,
    ledger: Any,
    broker: Any,
    calendar_store: Any | None = None,
    corporate_actions_store: Any | None = None,
    db: "MarketDB | None" = None,
) -> Any:
    """Ensambla `DailyEventBacktester` con pipeline corto + broker + ledger."""
    from .event_engine import DailyEventBacktester

    h = create_short_term_pipeline_handlers(policy_doc, repo_root, ledger, db=db)

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
