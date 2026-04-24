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
    ledger.apply_fills(pre_fills)

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
