"""Tests del motor de cobertura del sleeve corto (plan_hedge_short Fase 4).

Afirman el comportamiento DESEADO (ADR-057), no la implementación:
- el modo hedge_static asigna por pesos objetivo, NO por ranking de momentum;
- la regla de des-riesgo a cash dispara SOLO cuando ambos factores caen;
- el rebalanceo respeta la banda de drift y, al des-riesgar, vende a cash.
"""

from __future__ import annotations

import pytest

from core_sim.short_hedge_engine import (
    ShortHedgeConfig,
    build_hedge_orders_intent,
    hedge_target_weights,
    short_hedge_config_from_policy_dict,
    should_derisk_to_cash,
    trailing_drawdown,
    validate_short_hedge_config,
)

_WL = frozenset({"GLD", "KO"})


def _cfg(**over) -> ShortHedgeConfig:
    base = dict(
        enabled=True,
        mode="hedge_static",
        hedge_weight_total=0.20,
        tactical_weight_total=0.10,
        drift_rebalance_threshold_pp=2.0,
        hedge_lines=(("GLD", 0.5), ("KO", 0.5)),
        derisk_enabled=False,
        derisk_ar_drawdown_floor=None,
        derisk_global_drawdown_floor=None,
    )
    base.update(over)
    return ShortHedgeConfig(**base)


# --------------------------------------------------------------------------- #
# Config / validación
# --------------------------------------------------------------------------- #

class TestConfig:
    def test_hedge_lines_must_sum_to_one(self):
        with pytest.raises(ValueError):
            validate_short_hedge_config(_cfg(hedge_lines=(("GLD", 0.5), ("KO", 0.4))))

    def test_derisk_enabled_without_floors_is_error(self):
        with pytest.raises(ValueError):
            validate_short_hedge_config(_cfg(derisk_enabled=True))

    def test_targets_sum_to_one(self):
        t = hedge_target_weights(_cfg())
        assert t == {"GLD": 0.5, "KO": 0.5}

    def test_parse_from_policy_dict(self):
        payload = {
            "enabled": True, "mode": "hedge_static",
            "hedge_weight_total": 0.20, "tactical_weight_total": 0.10,
            "drift_rebalance_threshold_pp": 2.0,
            "hedge_lines": [{"symbol": "gld", "target_weight": 0.5},
                            {"symbol": "ko", "target_weight": 0.5}],
            "derisk_to_cash": {"enabled": False, "ar_factor_drawdown_floor": None,
                               "global_drawdown_floor": None},
        }
        cfg = short_hedge_config_from_policy_dict(payload)
        assert cfg.hedge_lines == (("GLD", 0.5), ("KO", 0.5))
        assert cfg.hedge_weight_total == 0.20 and cfg.tactical_weight_total == 0.10


# --------------------------------------------------------------------------- #
# Regla de des-riesgo a cash (mejora #4)
# --------------------------------------------------------------------------- #

class TestDeriskRule:
    def test_triggers_only_when_both_factors_in_drawdown(self):
        # Ambos por debajo del piso → SÍ des-riesga.
        assert should_derisk_to_cash(
            ar_drawdown=-0.15, global_drawdown=-0.12,
            ar_drawdown_floor=-0.10, global_drawdown_floor=-0.10) is True

    def test_does_not_trigger_when_only_ar_falls(self):
        # Solo AR cae; el global aguanta → la canasta anti-factor todavía sirve.
        assert should_derisk_to_cash(
            ar_drawdown=-0.20, global_drawdown=-0.02,
            ar_drawdown_floor=-0.10, global_drawdown_floor=-0.10) is False

    def test_does_not_trigger_when_only_global_falls(self):
        assert should_derisk_to_cash(
            ar_drawdown=-0.01, global_drawdown=-0.30,
            ar_drawdown_floor=-0.10, global_drawdown_floor=-0.10) is False

    def test_trailing_drawdown_from_peak(self):
        # peak 120, last 90 → -25%
        assert trailing_drawdown([100, 120, 110, 90]) == pytest.approx(-0.25)
        assert trailing_drawdown([]) == 0.0


# --------------------------------------------------------------------------- #
# Rebalanceo por bandas (hedge_static)
# --------------------------------------------------------------------------- #

class TestRebalanceByBands:
    def test_buys_to_reach_targets_from_all_cash(self):
        """Sin posiciones (todo cash) y drift fuera de banda → compra GLD y KO."""
        intents, skips, metrics = build_hedge_orders_intent(
            _cfg(),
            hedge_bucket_mtm=100_000.0, hedge_cash=100_000.0,
            positions_qty={}, prices={"GLD": 100.0, "KO": 50.0},
            whitelist_hedge=_WL,
        )
        sides = {i["symbol"]: i["side"] for i in intents}
        assert sides == {"GLD": "BUY", "KO": "BUY"}
        assert all(i["reason_code"].startswith("hedge_rebalance") for i in intents)
        assert metrics["intents_generated"] == 2

    def test_no_intents_when_within_drift_band(self):
        """Posiciones ya en target (50/50) → drift 0 < banda → no opera."""
        # bucket 100k; GLD 500*100=50k, KO 1000*50=50k → pesos 0.5/0.5
        intents, skips, _ = build_hedge_orders_intent(
            _cfg(),
            hedge_bucket_mtm=100_000.0, hedge_cash=0.0,
            positions_qty={"GLD": 500.0, "KO": 1000.0},
            prices={"GLD": 100.0, "KO": 50.0},
            whitelist_hedge=_WL,
        )
        assert intents == []
        assert any(s["reason"] == "within_drift_band" for s in skips)

    def test_is_weight_driven_not_momentum_ranking(self):
        """Un día de puro hedge NO genera ranking de momentum: solo toca lo que
        está fuera de banda respecto del peso objetivo. KO ya en peso, GLD no."""
        # bucket 100k: KO en target (50k), GLD vacío → solo compra GLD.
        intents, _, _ = build_hedge_orders_intent(
            _cfg(),
            hedge_bucket_mtm=100_000.0, hedge_cash=50_000.0,
            positions_qty={"KO": 1000.0}, prices={"GLD": 100.0, "KO": 50.0},
            whitelist_hedge=_WL,
        )
        assert [i["symbol"] for i in intents] == ["GLD"]
        assert intents[0]["side"] == "BUY"

    def test_derisk_to_cash_sells_everything_no_buys(self):
        """Con derisk_to_cash=True, targets→0: vende toda la canasta, sin compras."""
        intents, _, metrics = build_hedge_orders_intent(
            _cfg(),
            hedge_bucket_mtm=100_000.0, hedge_cash=0.0,
            positions_qty={"GLD": 500.0, "KO": 1000.0},
            prices={"GLD": 100.0, "KO": 50.0},
            whitelist_hedge=_WL, derisk_to_cash=True,
        )
        assert metrics["derisk_to_cash"] is True
        assert {i["symbol"] for i in intents} == {"GLD", "KO"}
        assert all(i["side"] == "SELL" for i in intents)
        assert all(i["reason_code"] == "hedge_derisk_to_cash" for i in intents)

    def test_aborts_cycle_on_missing_price(self):
        intents, skips, _ = build_hedge_orders_intent(
            _cfg(),
            hedge_bucket_mtm=100_000.0, hedge_cash=100_000.0,
            positions_qty={}, prices={"GLD": 100.0},  # falta KO
            whitelist_hedge=_WL,
        )
        assert intents == []
        assert any(s["reason"] == "missing_or_invalid_price_abort_cycle" for s in skips)

    def test_skips_symbol_not_whitelisted(self):
        intents, skips, _ = build_hedge_orders_intent(
            _cfg(),
            hedge_bucket_mtm=100_000.0, hedge_cash=100_000.0,
            positions_qty={}, prices={"GLD": 100.0, "KO": 50.0},
            whitelist_hedge=frozenset({"GLD"}),  # KO no whitelisted
        )
        assert intents == []
        assert any("symbol_not_whitelisted" in s["reason"] for s in skips)
