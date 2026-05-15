"""Behavior tests for long_term_monthly_runner (Fase C del long_term_engine)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import yaml

from core_sim.long_term_monthly_runner import (
    create_long_term_monthly_backtester,
    create_long_term_pipeline_handlers,
)
from core_sim.ledger import PortfolioLedger
from core_sim.paper_broker_sim import PaperBrokerSim
from core_sim.cost_model import CostModel, MarketCostConfig, SlippageMode

REPO_ROOT = Path(__file__).resolve().parents[1]

_PX_GGAL = 1000.0
_PX_PAMP = 500.0
_PX_SPY = 200.0

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
    ar_cfg = MarketCostConfig(
        commission_bps_per_side=15.0,
        slippage_bps=5.0,
        slippage_mode=SlippageMode.FIXED_BPS,
    )
    cost_model = CostModel(market_configs={"US": us_cfg, "AR": ar_cfg})
    return PaperBrokerSim(ledger=ledger, cost_model=cost_model)


def _daily_bars() -> dict[str, dict[str, float]]:
    return {
        "GGAL": {"close": _PX_GGAL, "volume": 1_000_000},
        "PAMP": {"close": _PX_PAMP, "volume": 500_000},
        "SPY": {"close": _PX_SPY, "volume": 200_000},
    }


# Primer día hábil AR de la semana ISO (solo mi-mi-j en el set ⇒ 2026-04-01)
_REBALANCE_DAY = date(2026, 4, 1)
_NON_REBALANCE_DAY = date(2026, 4, 2)
_AR_BUSINESS_APRIL = frozenset(
    {
        date(2026, 4, 1),
        date(2026, 4, 2),
        date(2026, 4, 3),
    }
)

_LONG_MTM = 100_000.0

_QTY_ON_TARGET = {
    "GGAL": 0.42 * _LONG_MTM / _PX_GGAL,
    "PAMP": 0.43 * _LONG_MTM / _PX_PAMP,
    "SPY": 0.15 * _LONG_MTM / _PX_SPY,
}

_QTY_OUT_OF_BAND = {
    "GGAL": 900.0,
    "PAMP": 100.0,
    "SPY": 50.0,
}


def _base_ctx(
    trading_day: date = _REBALANCE_DAY,
    ar_business_days: frozenset[date] = _AR_BUSINESS_APRIL,
    long_bucket_mtm: float = _LONG_MTM,
    long_cash: float = 50_000.0,
    positions_qty_long: dict[str, float] | None = None,
    halt_long_engine: bool = False,
    data_quality_halt: bool = False,
) -> dict[str, Any]:
    return {
        "trading_day": trading_day,
        "daily_bars": _daily_bars(),
        "ar_business_days": ar_business_days,
        "long_bucket_mtm": long_bucket_mtm,
        "long_cash": long_cash,
        "positions_qty_long": positions_qty_long if positions_qty_long is not None else dict(_QTY_ON_TARGET),
        "halt_long_engine": halt_long_engine,
        "data_quality_halt": data_quality_halt,
        "market_open": {"is_us_session": False, "is_ar_business_day": True},
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
        positions_qty_long=dict(_QTY_ON_TARGET),
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
    assert sides.get("GGAL") == "SELL"
    assert any(i["side"] == "BUY" for i in intents)

    for bo in broker_orders:
        assert "symbol" in bo
        assert "side" in bo
        assert "qty" in bo
        assert bo.get("bucket") == "long"
        assert bo.get("market") == "AR"

    spy_intents = [i for i in intents if str(i.get("symbol", "")).upper() == "SPY"]
    assert spy_intents, "se espera al menos un intent sobre SPY (satélite CEDEAR en policy)"
    assert all(str(i.get("market", "")).upper() == "AR" for i in spy_intents)


def test_weekly_ar_rebalance_first_business_day_sets_metrics_flag():
    """Paso 5: primer hábil AR semanal → ``is_long_rebalance_day`` True en métricas."""
    policy = _policy_doc()
    ledger = _make_ledger()
    h = create_long_term_pipeline_handlers(policy, REPO_ROOT, ledger)

    ctx_reb = _base_ctx(trading_day=_REBALANCE_DAY, positions_qty_long=dict(_QTY_OUT_OF_BAND))
    signals = h["generate_signals"](**ctx_reb)
    assert (signals.get("metrics") or {}).get("is_long_rebalance_day") is True

    ctx_other = _base_ctx(trading_day=_NON_REBALANCE_DAY, positions_qty_long=dict(_QTY_OUT_OF_BAND))
    signals2 = h["generate_signals"](**ctx_other)
    assert (signals2.get("metrics") or {}).get("is_long_rebalance_day") is False


# ---------------------------------------------------------------------------
# 4. long_bucket_mtm ausente → generate_signals retorna vacío con skip_reason
# ---------------------------------------------------------------------------


def test_missing_long_bucket_mtm_returns_empty_with_skip():
    policy = _policy_doc()
    ledger = _make_ledger()
    h = create_long_term_pipeline_handlers(policy, REPO_ROOT, ledger)

    ctx = _base_ctx()
    del ctx["long_bucket_mtm"]

    signals = h["generate_signals"](**ctx)

    assert signals["intents"] == []
    skips = signals.get("skips") or []
    assert any(s.get("reason") == "missing_long_bucket_mtm" for s in skips)

    proposed = h["propose_orders"](**{**ctx, "signals": signals})
    assert proposed["orders_intent"] == []


# ---------------------------------------------------------------------------
# 5. Integración end-to-end con create_long_term_monthly_backtester
# ---------------------------------------------------------------------------


def test_end_to_end_backtester_runs_and_produces_fills():
    policy = _policy_doc()
    ledger = _make_ledger(cash=200_000.0)
    broker = _make_broker(ledger)

    pre_fills = [
        {"symbol": "GGAL", "side": "BUY", "qty": 900.0, "price": _PX_GGAL, "market": "AR", "bucket": "long", "fee": 0.0},
        {"symbol": "PAMP", "side": "BUY", "qty": 100.0, "price": _PX_PAMP, "market": "AR", "bucket": "long", "fee": 0.0},
        {"symbol": "SPY", "side": "BUY", "qty": 50.0, "price": _PX_SPY, "market": "AR", "bucket": "long", "fee": 0.0},
    ]
    ledger.apply_fills(date(2020, 1, 1), pre_fills)

    backtester = create_long_term_monthly_backtester(
        policy_doc=policy,
        repo_root=REPO_ROOT,
        ledger=ledger,
        broker=broker,
    )

    daily_bars = _daily_bars()
    pipeline_context: dict[str, Any] = {
        "ar_business_days": _AR_BUSINESS_APRIL,
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

    for fill in fills:
        assert fill.get("bucket") == "long"
        assert fill.get("market") == "AR"


# ---------------------------------------------------------------------------
# Extras: risk_check filtra símbolos fuera de whitelist o mercado incorrecto
# ---------------------------------------------------------------------------


def test_risk_check_filters_wrong_market_or_non_whitelisted_symbols():
    policy = _policy_doc()
    ledger = _make_ledger()
    h = create_long_term_pipeline_handlers(policy, REPO_ROOT, ledger)

    broker_orders_mixed = [
        {"symbol": "GGAL", "side": "BUY", "qty": 10.0, "market": "AR", "bucket": "long"},
        {"symbol": "IWM", "side": "BUY", "qty": 5.0, "market": "US", "bucket": "long"},
        {"symbol": "PAMP", "side": "SELL", "qty": 3.0, "market": "AR", "bucket": "long"},
    ]
    ctx = _base_ctx()
    proposed = {"broker_orders": broker_orders_mixed, "orders_intent": [], "long_metrics": {}}
    approved = h["risk_check"](**{**ctx, "proposed_orders": proposed})

    syms = {o["symbol"] for o in approved}
    assert "IWM" not in syms
    assert "GGAL" in syms or "PAMP" in syms


# ---------------------------------------------------------------------------
# Gap 1 — Corporate actions: split aplica qty ajustada
# ---------------------------------------------------------------------------


def test_corporate_action_split_applies_adjusted_qty():
    policy = _policy_doc()
    ledger = _make_ledger()
    h = create_long_term_pipeline_handlers(policy, REPO_ROOT, ledger)

    ggal_qty_pre_split = 21.0
    ggal_price_post_split = 500.0

    positions_pre_split = {
        "GGAL": ggal_qty_pre_split,
        "PAMP": _QTY_ON_TARGET["PAMP"],
        "SPY": _QTY_ON_TARGET["SPY"],
    }

    daily_bars_post_split = {
        "GGAL": {"close": ggal_price_post_split, "volume": 1_000_000},
        "PAMP": {"close": _PX_PAMP, "volume": 500_000},
        "SPY": {"close": _PX_SPY, "volume": 200_000},
    }

    corporate_actions = [
        {"date": str(_REBALANCE_DAY), "symbol": "GGAL", "action_type": "split", "value": 2.0}
    ]

    ctx = _base_ctx(
        trading_day=_REBALANCE_DAY,
        positions_qty_long=positions_pre_split,
    )
    ctx["daily_bars"] = daily_bars_post_split
    ctx["market_open"] = {"is_ar_business_day": True, "corporate_actions": corporate_actions}

    signals = h["generate_signals"](**ctx)

    metrics = signals.get("metrics") or {}
    ca_applied = metrics.get("corporate_actions_applied") or []
    assert any(ca["symbol"] == "GGAL" and ca["action_type"] == "split" for ca in ca_applied), (
        "Se esperaba corporate_actions_applied con el split de GGAL"
    )


def test_split_adjusted_qty_leaves_weights_stable_at_runner_level():
    policy = _policy_doc()
    ledger = _make_ledger()
    h = create_long_term_pipeline_handlers(policy, REPO_ROOT, ledger)

    ggal_price_post_split = 500.0
    ggal_qty_pre_split = _QTY_ON_TARGET["GGAL"]

    positions_pre_split = {
        "GGAL": ggal_qty_pre_split,
        "PAMP": _QTY_ON_TARGET["PAMP"],
        "SPY": _QTY_ON_TARGET["SPY"],
    }

    daily_bars_post_split = {
        "GGAL": {"close": ggal_price_post_split, "volume": 1_000_000},
        "PAMP": {"close": _PX_PAMP, "volume": 500_000},
        "SPY": {"close": _PX_SPY, "volume": 200_000},
    }

    corporate_actions = [
        {"date": str(_REBALANCE_DAY), "symbol": "GGAL", "action_type": "split", "value": 2.0}
    ]

    ctx = _base_ctx(
        trading_day=_REBALANCE_DAY,
        positions_qty_long=positions_pre_split,
        long_bucket_mtm=_LONG_MTM,
    )
    ctx["daily_bars"] = daily_bars_post_split
    ctx["market_open"] = {"is_ar_business_day": True, "corporate_actions": corporate_actions}

    signals = h["generate_signals"](**ctx)
    proposed = h["propose_orders"](**{**ctx, "signals": signals})

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
    policy = _policy_doc()
    ledger = _make_ledger(cash=200_000.0)

    ledger.apply_fills(
        date(2026, 3, 30),
        [{"symbol": "GGAL", "side": "BUY", "qty": 375.0, "price": 520.0,
          "market": "AR", "bucket": "long", "fee": 0.0}],
    )
    ledger.mark_to_market(date(2026, 3, 31), {"GGAL": {"close": 520.0}})
    snap_after_drop = ledger.mark_to_market(date(2026, 4, 1), {"GGAL": {"close": 480.0}})

    long_bucket = snap_after_drop.get("long_bucket") or {}
    assert long_bucket.get("long_daily_return", 0.0) < -0.015, (
        "Precondition: long_daily_return should be < -1.5% después de la caída"
    )

    h = create_long_term_pipeline_handlers(policy, REPO_ROOT, ledger)

    daily_bars_dropped = {
        "GGAL": {"close": 480.0, "volume": 1_000_000},
        "PAMP": {"close": _PX_PAMP, "volume": 500_000},
        "SPY": {"close": _PX_SPY, "volume": 200_000},
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
# Gap 2 — ar_business_days auto-derivación desde calendar_store
# ---------------------------------------------------------------------------


def test_ar_business_days_auto_derived_from_calendar_store():
    policy = _policy_doc()
    ledger = _make_ledger()

    calendar_store = MagicMock()
    calendar_store.ar_business_days = _AR_BUSINESS_APRIL

    h = create_long_term_pipeline_handlers(policy, REPO_ROOT, ledger, calendar_store=calendar_store)

    ctx = _base_ctx(trading_day=_REBALANCE_DAY)
    del ctx["ar_business_days"]

    signals = h["generate_signals"](**ctx)

    skips = signals.get("skips") or []
    assert not any(s.get("reason") == "missing_ar_business_days" for s in skips), (
        "No debe fallar con missing_ar_business_days cuando calendar_store provee ar_business_days"
    )
    assert signals.get("engine") == "long_term_v1"
