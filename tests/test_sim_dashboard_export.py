"""Behavior tests for scripts/export_sim_dashboard_payload.py (Opción B).

La traducción serie->payload se testea con una serie sintética (rápido). La corrida
real de la sim (run_research_sim) es pesada y se cubre vía run_wf_research_sim.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from reporting.twr_walk_forward import DailyPoint  # noqa: E402
from scripts.export_dashboard_payload import validate_payload_shape  # noqa: E402
from scripts.export_sim_dashboard_payload import build_sim_payload  # noqa: E402
from scripts.run_wf_research_sim import DEFAULT_DB  # noqa: E402


def _series(n: int = 60, *, daily_growth: float = 0.002) -> list[DailyPoint]:
    start = date(2025, 1, 2)
    equity = 500_000.0
    pts: list[DailyPoint] = [DailyPoint(day=start, equity=equity, contribution=500_000.0)]
    for i in range(1, n):
        contrib = 500_000.0 if i % 21 == 0 else 0.0  # aporte mensual aprox
        # Tendencia alcista con caídas periódicas => hay drawdown (Calmar definido).
        growth = -0.01 if i % 10 == 0 else daily_growth
        # El aporte entra como caja (se suma al equity) y NO es ganancia; luego el
        # portfolio crece. Así el TWR ve un retorno diario limpio, sin el aporte.
        equity = equity * (1.0 + growth) + contrib
        pts.append(DailyPoint(day=start + timedelta(days=i), equity=equity, contribution=contrib))
    return pts


def test_build_sim_payload_should_match_dashboard_contract():
    payload = build_sim_payload(_series(), contribution=500_000.0)
    validate_payload_shape(payload)
    assert payload["meta"]["mode"] == "research_sim"
    assert payload["export_version"] == "1"
    assert len(payload["equity_curve"]) == 60
    assert payload["kpis"]["n_days"] == 60
    # Curva creciente => Calmar y retorno anualizado positivos.
    assert payload["kpis"]["calmar_total"] is not None
    assert payload["kpis"]["net_return_annualized"] > 0
    assert payload["alerts"][0]["code"] == "research_sim"


def test_build_sim_payload_should_carry_total_contributed_in_alert():
    payload = build_sim_payload(_series(), contribution=500_000.0)
    # _series aporta en i=0 y cada 21 días => al menos 2 aportes de 500k.
    assert "total aportado" in payload["alerts"][0]["detail"]
    assert "Calmar" in payload["alerts"][0]["detail"]


def test_build_sim_payload_should_reject_empty_series():
    import pytest

    with pytest.raises(ValueError):
        build_sim_payload([], contribution=500_000.0)


def test_adapter_default_db_must_be_backfill_not_paper_live():
    # Regresión: usar data/market.db (paper-live, con data US stale) envenena la
    # valuación y explota el equity. La sim debe correr sobre market_backfill.db.
    assert DEFAULT_DB.name == "market_backfill.db"
