"""Behavior tests for long_term_monthly_runner (Fase C del long_term_engine)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import yaml
import pytest

from core_sim.long_term_monthly_runner import (
    create_long_term_monthly_backtester,
    create_long_term_pipeline_handlers,
)
from core_sim.ledger import PortfolioLedger
from core_sim.paper_broker_sim import PaperBrokerSim
from core_sim.cost_model import CostModel, MarketCostConfig, SlippageMode

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Fixtures compartidos
# ---------------------------------------------------------------------------


def _policy_doc() -> dict[str, Any]:
    with (REPO_ROOT / "config" / "policy.v1.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _make_ledger(cash: float = 200_000.0) -> PortfolioLedger:
    return PortfolioLedger(starting_cash=cash)


def _make_broker(ledger: PortfolioLedger) -> PaperBrokerSim:
    us_cfg = MarketCostConfig(
        commission_bps_per_side=1.0,
        slippage_bps=2.0,
        slippage_mode=SlippageMode.FIXED_BPS,
    )
    cost_model = CostModel(market_configs={"US": us_cfg})
    return PaperBrokerSim(ledger=ledger, cost_model=cost_model)


def _daily_bars() -> dict[str, dict[str, float]]:
    return {
        "SPY": {"close": 100.0, "volume": 1_000_000},
        "IWM": {"close": 50.0, "volume": 500_000},
        "QQQ": {"close": 300.0, "volume": 200_000},
    }


# Primer día hábil de abril 2026
_REBALANCE_DAY = date(2026, 4, 1)
_NON_REBALANCE_DAY = date(2026, 4, 2)
_US_SESSIONS_APRIL = frozenset(
    {
        date(2026, 4, 1),
        date(2026, 4, 2),
        date(2026, 4, 3),
    }
)

# MTM del sleeve largo perfectamente en target (SPY 55%, IWM 30%, QQQ 15%)
_LONG_MTM = 100_000.0
_PRICES = {"SPY": 100.0, "IWM": 50.0, "QQQ": 300.0}

# Cantidades exactamente en target — no hay drift
_QTY_ON_TARGET = {
    "SPY": 0.55 * _LONG_MTM / _PRICES["SPY"],   # 550.0
    "IWM": 0.30 * _LONG_MTM / _PRICES["IWM"],   # 600.0
    "QQQ": 0.15 * _LONG_MTM / _PRICES["QQQ"],   # 50.0
}

# Cantidades muy desbalanceadas (SPY sobrepondado) — drift fuera de banda
_QTY_OUT_OF_BAND = {
    "SPY": 900.0,
    "IWM": 100.0,
    "QQQ": 50.0,
}


def _base_ctx(
    trading_day: date = _REBALANCE_DAY,
    us_sessions: frozenset[date] = _US_SESSIONS_APRIL,
    long_bucket_mtm: float = _LONG_MTM,
    long_cash: float = 50_000.0,
    positions_qty_long: dict[str, float] | None = None,
    halt_long_engine: bool = False,
    data_quality_halt: bool = False,
) -> dict[str, Any]:
    return {
        "trading_day": trading_day,
        "daily_bars": _daily_bars(),
        "us_sessions": us_sessions,
        "long_bucket_mtm": long_bucket_mtm,
        "long_cash": long_cash,
        "positions_qty_long": positions_qty_long if positions_qty_long is not None else dict(_QTY_ON_TARGET),
        "halt_long_engine": halt_long_engine,
        "data_quality_halt": data_quality_halt,
        "market_open": {"is_us_session": True, "is_ar_business_day": False},
    }


# ---------------------------------------------------------------------------
# 1. No es día de rebalance → cero orders_intent en propose_orders
# ---------------------------------------------------------------------------


def test_non_rebalance_day_produces_no_intents():
    policy = _policy_doc()
    ledger = _make_ledger()
    h = create_long_term_pipeline_handlers(policy, REPO_ROOT, ledger)

    ctx = _base_ctx(trading_day=_NON_REBALANCE_DAY, positions_qty_long=dict(_QTY_OUT_OF_BAND))
    signals = h["generate_signals"](**ctx)

    proposed = h["propose_orders"](**{**ctx, "signals": signals})

    assert proposed["orders_intent"] == []
    assert proposed["broker_orders"] == []
    skips = signals.get("skips") or []
    assert any(s.get("reason") == "not_long_rebalance_day" for s in skips)


# ---------------------------------------------------------------------------
# 2. Es día de rebalance, drift dentro de banda → cero orders_intent
# ---------------------------------------------------------------------------


def test_rebalance_day_within_drift_band_produces_no_intents():
    policy = _policy_doc()
    ledger = _make_ledger()
    h = create_long_term_pipeline_handlers(policy, REPO_ROOT, ledger)

    ctx = _base_ctx(
        trading_day=_REBALANCE_DAY,
        positions_qty_long=dict(_QTY_ON_TARGET),  # perfectamente en target
    )
    signals = h["generate_signals"](**ctx)
    proposed = h["propose_orders"](**{**ctx, "signals": signals})

    assert proposed["orders_intent"] == []
    assert proposed["broker_orders"] == []
    skips = signals.get("skips") or []
    assert any(s.get("reason") == "within_drift_band" for s in skips)


# ---------------------------------------------------------------------------
# 3. Es día de rebalance, drift fuera de banda → intents BUY/SELL + broker_orders
# ---------------------------------------------------------------------------


def test_rebalance_day_out_of_band_generates_intents_and_broker_orders():
    policy = _policy_doc()
    ledger = _make_ledger()
    h = create_long_term_pipeline_handlers(policy, REPO_ROOT, ledger)

    ctx = _base_ctx(
        trading_day=_REBALANCE_DAY,
        positions_qty_long=dict(_QTY_OUT_OF_BAND),
    )
    signals = h["generate_signals"](**ctx)
    assert signals["engine"] == "long_term_v1"

    proposed = h["propose_orders"](**{**ctx, "signals": signals})

    intents = proposed["orders_intent"]
    broker_orders = proposed["broker_orders"]

    assert len(intents) >= 1, "se esperan intents cuando hay drift fuera de banda"
    assert len(broker_orders) == len(intents), "broker_orders debe tener la misma cantidad que intents"

    sides = {i["symbol"]: i["side"] for i in intents}
    # SPY está sobrepondado → debería venderse
    assert sides.get("SPY") == "SELL"
    # Al menos uno de IWM o QQQ debe comprarse
    assert any(i["side"] == "BUY" for i in intents)

    # Cada broker_order debe tener los campos mínimos
    for bo in broker_orders:
        assert "symbol" in bo
        assert "side" in bo
        assert "qty" in bo
        assert bo.get("bucket") == "long"
        assert bo.get("market") == "US"


# ---------------------------------------------------------------------------
# 4. long_bucket_mtm ausente → generate_signals retorna vacío con skip_reason
# ---------------------------------------------------------------------------


def test_missing_long_bucket_mtm_returns_empty_with_skip():
    policy = _policy_doc()
    ledger = _make_ledger()
    h = create_long_term_pipeline_handlers(policy, REPO_ROOT, ledger)

    ctx = _base_ctx()
    del ctx["long_bucket_mtm"]  # simula ausencia del campo

    signals = h["generate_signals"](**ctx)

    assert signals["intents"] == []
    skips = signals.get("skips") or []
    assert any(s.get("reason") == "missing_long_bucket_mtm" for s in skips)

    # propose_orders no debe explotar con señales vacías
    proposed = h["propose_orders"](**{**ctx, "signals": signals})
    assert proposed["orders_intent"] == []


# ---------------------------------------------------------------------------
# 5. Integración end-to-end con create_long_term_monthly_backtester
# ---------------------------------------------------------------------------


def test_end_to_end_backtester_runs_and_produces_fills():
    """Pipeline completo: drift fuera de banda en día de rebalance → fills en OrdersFilled.

    El ledger necesita posiciones previas para poder ejecutar SELLs.  Compramos
    primero SPY al precio de la barra (100) para que el broker sim pueda vender
    sin lanzar ValueError en apply_fills.
    """
    policy = _policy_doc()
    ledger = _make_ledger(cash=200_000.0)
    broker = _make_broker(ledger)

    # Pre-carga posiciones para que el ledger pueda procesar SELLs.
    # _QTY_OUT_OF_BAND tiene SPY=900, IWM=100, QQQ=50.
    # Compramos exactamente esas cantidades al cierre del día anterior.
    pre_fills = [
        {"symbol": "SPY", "side": "BUY", "qty": 900.0, "price": 100.0, "market": "US", "bucket": "long", "fee": 0.0},
        {"symbol": "IWM", "side": "BUY", "qty": 100.0, "price": 50.0, "market": "US", "bucket": "long", "fee": 0.0},
        {"symbol": "QQQ", "side": "BUY", "qty": 50.0, "price": 300.0, "market": "US", "bucket": "long", "fee": 0.0},
    ]
    # Fecha ficticia antes del rebalance: solo precarga ledger; sin MTM ese día no aparece costs en la corrida.
    ledger.apply_fills(date(2020, 1, 1), pre_fills)

    backtester = create_long_term_monthly_backtester(
        policy_doc=policy,
        repo_root=REPO_ROOT,
        ledger=ledger,
        broker=broker,
    )

    daily_bars = _daily_bars()
    pipeline_context: dict[str, Any] = {
        "us_sessions": _US_SESSIONS_APRIL,
        "long_bucket_mtm": _LONG_MTM,
        "long_cash": 50_000.0,
        "positions_qty_long": dict(_QTY_OUT_OF_BAND),
        "halt_long_engine": False,
        "data_quality_halt": False,
    }

    events = backtester.run_day(
        trading_day=_REBALANCE_DAY,
        daily_bars=daily_bars,
        pipeline_context=pipeline_context,
    )

    event_map = {e.name: e.payload for e in events}

    assert "OrdersFilled" in event_map
    fills = event_map["OrdersFilled"]
    assert isinstance(fills, list)
    assert len(fills) >= 1, "se esperan fills cuando hay drift fuera de banda"

    # Todos los fills deben ser del bucket 'long'
    for fill in fills:
        assert fill.get("bucket") == "long"
        assert fill.get("market") == "US"


# ---------------------------------------------------------------------------
# Extras: risk_check filtra símbolos fuera de la whitelist US
# ---------------------------------------------------------------------------


def test_risk_check_filters_non_us_symbols():
    policy = _policy_doc()
    ledger = _make_ledger()
    h = create_long_term_pipeline_handlers(policy, REPO_ROOT, ledger)

    broker_orders_mixed = [
        {"symbol": "SPY", "side": "BUY", "qty": 10.0, "market": "US", "bucket": "long"},
        {"symbol": "GGAL", "side": "BUY", "qty": 5.0, "market": "AR", "bucket": "long"},
        {"symbol": "IWM", "side": "SELL", "qty": 3.0, "market": "US", "bucket": "long"},
    ]
    ctx = _base_ctx()
    proposed = {"broker_orders": broker_orders_mixed, "orders_intent": [], "long_metrics": {}}
    approved = h["risk_check"](**{**ctx, "proposed_orders": proposed})

    syms = {o["symbol"] for o in approved}
    assert "GGAL" not in syms
    assert "SPY" in syms or "IWM" in syms  # al menos uno de los US pasa


# ---------------------------------------------------------------------------
# Gap 1 — Corporate actions: split aplica qty ajustada
# ---------------------------------------------------------------------------


def test_corporate_action_split_applies_adjusted_qty():
    """Un split 2:1 en SPY debe duplicar la qty antes de calcular pesos.

    Con la qty pre-split, el peso calculado sería incorrecto (precio nuevo × qty
    vieja).  Después del split, qty×precio_nuevo debe reflejar el peso real.
    """
    policy = _policy_doc()
    ledger = _make_ledger()
    h = create_long_term_pipeline_handlers(policy, REPO_ROOT, ledger)

    # SPY pre-split: qty = 275, precio post-split = 50 → valor = 275×50 = 13_750
    # Pero la qty ajustada debería ser 275×2 = 550, valor = 550×50 = 27_500
    # SPY target = 55% de 100_000 = 55_000 → con qty pre-split hay drift enorme
    spy_qty_pre_split = 275.0  # la mitad de la qty on-target (275 × 50 = 13_750, muy por debajo)
    spy_price_post_split = 50.0  # precio se divide por 2 en un split 2:1

    positions_pre_split = {
        "SPY": spy_qty_pre_split,
        "IWM": _QTY_ON_TARGET["IWM"],
        "QQQ": _QTY_ON_TARGET["QQQ"],
    }

    daily_bars_post_split = {
        "SPY": {"close": spy_price_post_split, "volume": 1_000_000},
        "IWM": {"close": 50.0, "volume": 500_000},
        "QQQ": {"close": 300.0, "volume": 200_000},
    }

    corporate_actions = [
        {"date": str(_REBALANCE_DAY), "symbol": "SPY", "action_type": "split", "value": 2.0}
    ]

    ctx = _base_ctx(
        trading_day=_REBALANCE_DAY,
        positions_qty_long=positions_pre_split,
    )
    ctx["daily_bars"] = daily_bars_post_split
    ctx["market_open"] = {"is_us_session": True, "corporate_actions": corporate_actions}

    signals = h["generate_signals"](**ctx)

    # La métrica debe registrar que se aplicó el corporate action
    metrics = signals.get("metrics") or {}
    ca_applied = metrics.get("corporate_actions_applied") or []
    assert any(ca["symbol"] == "SPY" and ca["action_type"] == "split" for ca in ca_applied), (
        "Se esperaba corporate_actions_applied con el split de SPY"
    )


def test_split_adjusted_qty_leaves_weights_stable_at_runner_level():
    """Split 2:1 en SPY con posiciones exactamente en target → cero intents (no drift).

    Escenario: antes del split SPY tenía 550 acciones @ 100 (valor = 55_000).
    Después del split: precio cae a 50, qty se duplica a 1100 (valor sigue = 55_000).
    El runner recibe qty pre-split (550) + precio post-split (50) → sin el fix
    calcularía peso 550×50/100_000 = 0.275 (drift enorme).  Con el fix aplica split
    2:1 → 1100×50/100_000 = 0.55 (exactamente on-target → without_drift_band).
    """
    policy = _policy_doc()
    ledger = _make_ledger()
    h = create_long_term_pipeline_handlers(policy, REPO_ROOT, ledger)

    # Precio post-split = 50 (precio original 100 / 2)
    spy_price_post_split = 50.0
    # Qty pre-split = on-target (550); después del 2:1 el engine verá 1100 @ 50
    spy_qty_pre_split = _QTY_ON_TARGET["SPY"]  # 550.0

    positions_pre_split = {
        "SPY": spy_qty_pre_split,
        "IWM": _QTY_ON_TARGET["IWM"],
        "QQQ": _QTY_ON_TARGET["QQQ"],
    }

    # Con el split, el precio baja a 50 pero el valor económico se mantiene igual.
    # MTM ajustado: SPY 1100×50=55_000, IWM 600×50=30_000, QQQ 50×300=15_000 → 100_000
    # (mismo MTM porque el valor económico no cambia en un split)
    daily_bars_post_split = {
        "SPY": {"close": spy_price_post_split, "volume": 1_000_000},
        "IWM": {"close": 50.0, "volume": 500_000},
        "QQQ": {"close": 300.0, "volume": 200_000},
    }

    corporate_actions = [
        {"date": str(_REBALANCE_DAY), "symbol": "SPY", "action_type": "split", "value": 2.0}
    ]

    ctx = _base_ctx(
        trading_day=_REBALANCE_DAY,
        positions_qty_long=positions_pre_split,
        long_bucket_mtm=_LONG_MTM,
    )
    ctx["daily_bars"] = daily_bars_post_split
    ctx["market_open"] = {"is_us_session": True, "corporate_actions": corporate_actions}

    signals = h["generate_signals"](**ctx)
    proposed = h["propose_orders"](**{**ctx, "signals": signals})

    # Con qty ajustada correctamente (1100 @ 50 = 55_000 = 55% de 100_000),
    # no debe haber drift → cero intents
    assert proposed["orders_intent"] == [], (
        "El split 2:1 on-target no debe generar rebalanceo fantasma"
    )
    skips = signals.get("skips") or []
    assert any(s.get("reason") == "within_drift_band" for s in skips), (
        "Se esperaba within_drift_band (no drift después del split)"
    )


# ---------------------------------------------------------------------------
# 6. check_long_risk blocks on real daily breach (Fase 0/1)
# ---------------------------------------------------------------------------


def test_long_risk_guardrail_blocks_on_real_daily_loss():
    """When the long sleeve loses > 1.5% in a day, propose_orders must block.

    Strategy: seed a long position, MTM day 1 at high price, MTM day 2 at lower price
    so the ledger computes a real long_daily_return < -0.015.  The runner must use
    that value (from long_bucket) instead of defaulting to 0.0.
    """
    policy = _policy_doc()
    ledger = _make_ledger(cash=200_000.0)

    ledger.apply_fills(
        date(2026, 3, 30),
        [{"symbol": "SPY", "side": "BUY", "qty": 1500.0, "price": 100.0,
          "market": "US", "bucket": "long", "fee": 0.0}],
    )
    ledger.mark_to_market(date(2026, 3, 31), {"SPY": {"close": 100.0}})
    snap_after_drop = ledger.mark_to_market(date(2026, 4, 1), {"SPY": {"close": 96.0}})

    long_bucket = snap_after_drop.get("long_bucket") or {}
    assert long_bucket.get("long_daily_return", 0.0) < -0.015, (
        "Precondition: long_daily_return should be < -1.5% after a ~4% drop on 75% position"
    )

    h = create_long_term_pipeline_handlers(policy, REPO_ROOT, ledger)

    daily_bars_dropped = {
        "SPY": {"close": 96.0, "volume": 1_000_000},
        "IWM": {"close": 50.0, "volume": 500_000},
        "QQQ": {"close": 300.0, "volume": 200_000},
    }
    ctx = _base_ctx(
        trading_day=_REBALANCE_DAY,
        positions_qty_long=dict(_QTY_OUT_OF_BAND),
    )
    ctx["daily_bars"] = daily_bars_dropped

    signals = h["generate_signals"](**ctx)
    proposed = h["propose_orders"](**{**ctx, "signals": signals})

    assert proposed["orders_intent"] == [], "Long risk guardrail should have blocked all intents"
    halt_reason = proposed.get("long_metrics", {}).get("halt_reason", "")
    assert halt_reason == "long_daily_loss_limit", (
        f"Expected halt_reason='long_daily_loss_limit', got '{halt_reason}'"
    )


# ---------------------------------------------------------------------------
# Gap 2 — us_sessions auto-derivación desde calendar_store
# ---------------------------------------------------------------------------


def test_us_sessions_auto_derived_from_calendar_store():
    """Si us_sessions no está en pipeline_context, debe derivarse de calendar_store."""
    policy = _policy_doc()
    ledger = _make_ledger()

    # Mock de calendar_store con us_sessions
    calendar_store = MagicMock()
    calendar_store.us_sessions = _US_SESSIONS_APRIL

    h = create_long_term_pipeline_handlers(policy, REPO_ROOT, ledger, calendar_store=calendar_store)

    # Contexto SIN us_sessions — el handler debe derivarlo del calendar_store
    ctx = _base_ctx(trading_day=_REBALANCE_DAY)
    del ctx["us_sessions"]

    signals = h["generate_signals"](**ctx)

    # No debe retornar missing_us_sessions
    skips = signals.get("skips") or []
    assert not any(s.get("reason") == "missing_us_sessions" for s in skips), (
        "No debe fallar con missing_us_sessions cuando calendar_store provee us_sessions"
    )
    # El engine debe haber procesado normalmente
    assert signals.get("engine") == "long_term_v1"
