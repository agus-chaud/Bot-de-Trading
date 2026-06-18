"""Tests del módulo TWR + walk-forward.

El test central (`TestContributionsDoNotInflateReturns`) es la prueba de que el TWR
NO se rompe con los aportes — exactamente la preocupación que motivó el módulo.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from reporting.twr_walk_forward import (
    DailyPoint,
    annualized_sharpe,
    cumulative_twr,
    evaluate_walk_forward,
    max_drawdown,
    money_weighted_return,
    time_weighted_daily_returns,
    twr_index,
    walk_forward_windows,
)


def _days(n: int, start: date = date(2025, 1, 1)) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


# ---------------------------------------------------------------------------
# EL test que importa: los aportes no inflan el retorno
# ---------------------------------------------------------------------------

class TestContributionsDoNotInflateReturns:
    def test_pure_contribution_day_with_no_market_move_is_zero_return(self):
        """Día con aporte grande pero CERO movimiento de mercado → retorno TWR = 0.

        El equity salta 100k→600k SOLO por el aporte. El retorno crudo
        (V_t/V_{t-1}-1) leería +500% (artefacto). El TWR debe leer 0%.
        """
        d = _days(2)
        series = [
            DailyPoint(d[0], equity=100_000.0, contribution=0.0),
            DailyPoint(d[1], equity=600_000.0, contribution=500_000.0),  # solo aporte
        ]
        twr = time_weighted_daily_returns(series)
        assert twr[0][1] == pytest.approx(0.0, abs=1e-9)

        # Contraste: el retorno crudo SÍ mostraría el artefacto.
        naive = series[1].equity / series[0].equity - 1.0
        assert naive == pytest.approx(5.0)  # +500% falso

    def test_contribution_plus_real_gain_isolates_only_the_gain(self):
        """Aporte 500k + el mercado sube 10% sobre la base → TWR = 10%, no más."""
        d = _days(2)
        # base del día 2 = 100k previo + 100k aporte = 200k; sube 10% → 220k
        series = [
            DailyPoint(d[0], equity=100_000.0),
            DailyPoint(d[1], equity=220_000.0, contribution=100_000.0),
        ]
        twr = time_weighted_daily_returns(series)
        assert twr[0][1] == pytest.approx(0.10)

    def test_two_portfolios_same_strategy_different_contributions_same_twr(self):
        """Misma estrategia (mismos % diarios), distintos aportes → MISMO TWR.

        Es la propiedad que define al TWR: no depende del timing del dinero.
        """
        d = _days(3)
        # Estrategia: +5% día 1, -2% día 2. Sin aportes.
        a = [
            DailyPoint(d[0], 1000.0),
            DailyPoint(d[1], 1050.0),
            DailyPoint(d[2], 1029.0),
        ]
        # Misma estrategia pero con un aporte de 1000 el día 2 (al inicio).
        # día1: 1000→1050 (+5%); día2 base=1050+1000=2050, -2% → 2009.0
        b = [
            DailyPoint(d[0], 1000.0),
            DailyPoint(d[1], 1050.0),
            DailyPoint(d[2], 2009.0, contribution=1000.0),
        ]
        twr_a = cumulative_twr(time_weighted_daily_returns(a))
        twr_b = cumulative_twr(time_weighted_daily_returns(b))
        assert twr_a == pytest.approx(twr_b)
        assert twr_a == pytest.approx(1.05 * 0.98 - 1.0)

    def test_drawdown_not_masked_by_contributions(self):
        """El drawdown se mide sobre el índice TWR: los aportes no lo esconden."""
        d = _days(3)
        # Mercado cae 20% el día 2, pero un aporte hace SUBIR el equity crudo.
        # día1: 1000; día2 base=1000+5000=6000, -20% → 4800 (equity sube por el aporte)
        series = [
            DailyPoint(d[0], 1000.0),
            DailyPoint(d[1], 4800.0, contribution=5000.0),
            DailyPoint(d[2], 4800.0),
        ]
        mdd = max_drawdown(time_weighted_daily_returns(series))
        assert mdd == pytest.approx(-0.20)  # la caída real, no escondida


# ---------------------------------------------------------------------------
# Métricas básicas
# ---------------------------------------------------------------------------

class TestMetrics:
    def test_twr_index_compounds(self):
        d = _days(3)
        series = [DailyPoint(d[0], 100.0), DailyPoint(d[1], 110.0), DailyPoint(d[2], 99.0)]
        idx = twr_index(time_weighted_daily_returns(series))
        assert idx[0] == 100.0
        assert idx[-1] == pytest.approx(99.0)  # 100 * 1.1 * 0.9

    def test_sharpe_none_when_no_volatility(self):
        d = _days(4)
        # retorno constante → desvío 0 → Sharpe indefinido
        series = [DailyPoint(d[i], 100.0 * (1.01 ** i)) for i in range(4)]
        assert annualized_sharpe(time_weighted_daily_returns(series)) is None

    def test_sharpe_positive_for_upward_noisy_series(self):
        d = _days(6)
        eq = [100, 102, 101, 104, 103, 106]
        series = [DailyPoint(d[i], float(eq[i])) for i in range(6)]
        s = annualized_sharpe(time_weighted_daily_returns(series))
        assert s is not None and s > 0


class TestMoneyWeightedReturn:
    def test_single_deposit_doubling_in_one_year(self):
        cf = [(date(2025, 1, 1), -1000.0)]
        mwr = money_weighted_return(cf, ending_value=2000.0, ending_day=date(2026, 1, 1))
        assert mwr is not None and mwr == pytest.approx(1.0, abs=0.01)  # +100% anual

    def test_no_growth_is_zero(self):
        cf = [(date(2025, 1, 1), -1000.0)]
        mwr = money_weighted_return(cf, ending_value=1000.0, ending_day=date(2026, 1, 1))
        assert mwr is not None and abs(mwr) < 0.01


# ---------------------------------------------------------------------------
# Walk-forward
# ---------------------------------------------------------------------------

class TestWalkForward:
    def test_windows_need_burn_in_plus_oos(self):
        # 179 días con 120+60 → no alcanza (necesita 180)
        assert walk_forward_windows(_days(179), 120, 60, 30) == []
        # 180 días → exactamente una ventana
        assert len(walk_forward_windows(_days(180), 120, 60, 30)) == 1

    def test_windows_roll_by_step(self):
        # 360 días, 120+60 paso 30 → (360-180)/30 + 1 = 7 ventanas
        ws = walk_forward_windows(_days(360), 120, 60, 30)
        assert len(ws) == 7
        assert ws[0].index == 0
        # la OOS de la ventana 0 arranca en el día 120 (índice 120)
        assert ws[0].oos_start == _days(360)[120]

    def test_insufficient_data_reports_honestly(self):
        series = [DailyPoint(d, 1000.0) for d in _days(100)]
        rep = evaluate_walk_forward(series, burn_in=120, oos=60, step=30)
        assert rep["insufficient_data"] is True
        assert rep["num_windows"] == 0
        assert rep["aggregate_passed"] is False

    def test_evaluate_marks_research_mode(self):
        series = [DailyPoint(d, 1000.0 * (1.001 ** i)) for i, d in enumerate(_days(200))]
        rep = evaluate_walk_forward(series, burn_in=120, oos=60, step=30)
        assert rep["mode"] == "research"
        assert rep["num_windows"] >= 1
