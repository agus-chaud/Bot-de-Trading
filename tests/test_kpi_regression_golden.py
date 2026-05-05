"""Regresi\u00f3n KPI: dataset fijo de 60 d\u00edas + golden values (Fase 5 plan, \u00edtem 9).

Filosof\u00eda smart-testing aplicada:

* **Comportamiento, no implementaci\u00f3n**: probamos que ``build_kpi_v0_report`` con un
  CSV fijo produce **exactamente** los KPIs publicados en ``expected_kpis.json``.
  Si alguien renombra una funci\u00f3n interna, los tests siguen verdes; si alguien cambia
  una f\u00f3rmula del spec sin actualizar el golden, los tests rompen aqu\u00ed.
* **AAA**: cada test arma (load fixtures), act\u00faa (corre el reporte) y verifica un
  bloque concreto del JSON.
* **Sin mocks**: usamos los CSV reales y el c\u00f3digo real; los fixtures viven
  versionados en ``tests/fixtures/kpi_golden/``.
* **Tolerancia expl\u00edcita**: floats con ``rel=1e-9`` (sub-ULP); reasons NA con igualdad.

Si el spec cambia leg\u00edtimamente: regenerar con
``python scripts/regenerate_kpi_golden_fixtures.py`` y revisar el diff del JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reporting.kpi_v0 import build_kpi_v0_report

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "kpi_golden"

EQUITY_PATH = FIXTURES_DIR / "equity_60d.csv"
TRADES_PATH = FIXTURES_DIR / "trades_60d.csv"
BENCHMARK_PATH = FIXTURES_DIR / "benchmark_returns_60d.csv"
METADATA_PATH = FIXTURES_DIR / "metadata.yaml"
GOLDEN_PATH = FIXTURES_DIR / "expected_kpis.json"

REL_TOL = 1e-9


@pytest.fixture(scope="module")
def golden_report() -> dict:
    rep = build_kpi_v0_report(
        EQUITY_PATH,
        TRADES_PATH,
        metadata_path=METADATA_PATH,
        policy_path=None,
        benchmark_returns_path=BENCHMARK_PATH,
    )
    payload = rep.to_json_dict()
    return payload


@pytest.fixture(scope="module")
def golden_expected() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def _approx(value: float) -> object:
    return pytest.approx(value, rel=REL_TOL, abs=1e-12)


def test_fixture_files_are_present_and_versioned() -> None:
    for p in (EQUITY_PATH, TRADES_PATH, BENCHMARK_PATH, METADATA_PATH, GOLDEN_PATH):
        assert p.is_file(), f"missing fixture: {p}"


def test_equity_fixture_keeps_60_trading_days_and_known_window(golden_report: dict) -> None:
    seg = golden_report["segment"]["total"]
    assert seg["n_trading_days"] == 60
    assert seg["n_return_steps"] == 59
    assert golden_report["ts_start"] == "2024-01-02"
    assert golden_report["ts_end"] == "2024-03-25"


def test_total_segment_returns_drawdown_and_risk_match_golden(
    golden_report: dict, golden_expected: dict
) -> None:
    actual = golden_report["segment"]["total"]
    exp = golden_expected["segment"]["total"]

    assert actual["net_return_annualized"] == _approx(exp["net_return_annualized"])
    assert actual["net_return_annualized_na_reason"] == exp["net_return_annualized_na_reason"]
    assert actual["max_drawdown"] == _approx(exp["max_drawdown"])
    assert actual["sharpe_annualized"] == _approx(exp["sharpe_annualized"])
    assert actual["sharpe_na_reason"] == exp["sharpe_na_reason"]
    assert actual["sortino_annualized"] == _approx(exp["sortino_annualized"])
    assert actual["sortino_na_reason"] == exp["sortino_na_reason"]


def test_per_motor_fifo_round_trip_stats_match_golden(
    golden_report: dict, golden_expected: dict
) -> None:
    for motor in ("total", "short", "long"):
        actual = golden_report["segment"][motor]
        exp = golden_expected["segment"][motor]
        assert actual["n_round_trips"] == exp["n_round_trips"], motor
        assert actual["hit_rate"] == _approx(exp["hit_rate"]), motor
        assert actual["profit_factor"] == _approx(exp["profit_factor"]), motor
        assert actual["hit_rate_na_reason"] == exp["hit_rate_na_reason"], motor
        assert actual["profit_factor_na_reason"] == exp["profit_factor_na_reason"], motor


def test_costs_by_motor_match_golden(golden_report: dict, golden_expected: dict) -> None:
    actual = golden_report["costs_by_motor"]
    exp = golden_expected["costs_by_motor"]
    assert actual["short"] == _approx(exp["short"])
    assert actual["long"] == _approx(exp["long"])
    assert golden_report["costs_na_reason"] is None


def test_long_segment_reports_insufficient_history_for_12m_rolling(
    golden_report: dict, golden_expected: dict
) -> None:
    long_seg = golden_report["segment"]["long"]
    exp = golden_expected["segment"]["long"]

    assert long_seg["mdd_12m_rolling_last"] is None
    assert long_seg["mdd_12m_rolling_na_reason"] == "insufficient_history"
    assert long_seg["calmar_12m_last"] is None
    assert long_seg["calmar_12m_na_reason"] == "insufficient_history"

    assert long_seg["mdd_12m_rolling_na_reason"] == exp["mdd_12m_rolling_na_reason"]
    assert long_seg["calmar_12m_na_reason"] == exp["calmar_12m_na_reason"]


def test_long_monthly_turnover_matches_golden_per_month(
    golden_report: dict, golden_expected: dict
) -> None:
    actual = golden_report["segment"]["long"]["turnover_long_monthly"]
    exp = golden_expected["segment"]["long"]["turnover_long_monthly"]
    assert set(actual.keys()) == set(exp.keys())
    for month in exp:
        assert actual[month]["turnover_long_monthly"] == _approx(
            exp[month]["turnover_long_monthly"]
        ), month
        assert actual[month]["sum_abs_notional_long"] == _approx(
            exp[month]["sum_abs_notional_long"]
        ), month
        assert actual[month]["avg_equity_long"] == _approx(
            exp[month]["avg_equity_long"]
        ), month
    assert (
        golden_report["segment"]["long"]["turnover_long_monthly_last"]
        == _approx(golden_expected["segment"]["long"]["turnover_long_monthly_last"])
    )
    assert (
        golden_report["segment"]["long"]["turnover_long_monthly_last_month"]
        == golden_expected["segment"]["long"]["turnover_long_monthly_last_month"]
    )


def test_mandate_drift_snapshot_stays_within_declared_bands(
    golden_report: dict, golden_expected: dict
) -> None:
    snap = golden_report["mandate_drift"]["snapshot_last_ts"]
    exp_snap = golden_expected["mandate_drift"]["snapshot_last_ts"]

    assert snap["ts"] == exp_snap["ts"]
    for key in ("drift_short_pp", "drift_long_pp", "drift_ar_pp", "drift_us_pp"):
        assert snap[key] == _approx(exp_snap[key]), key

    assert snap["outside_band_axes"] is None
    bands = snap["bands_half_width_pp"]
    for axis_key, band_key in (
        ("drift_short_pp", "short"),
        ("drift_long_pp", "long"),
        ("drift_ar_pp", "AR"),
        ("drift_us_pp", "US"),
    ):
        assert abs(snap[axis_key]) <= bands[band_key] + 1e-12, axis_key


def test_alpha_vs_benchmark_total_and_long_match_golden(
    golden_report: dict, golden_expected: dict
) -> None:
    actual = golden_report["alpha_vs_benchmark"]
    exp = golden_expected["alpha_vs_benchmark"]
    for segment in ("total", "long"):
        assert actual[segment]["n_obs"] == exp[segment]["n_obs"], segment
        assert actual[segment]["alpha_simple_return"] == _approx(
            exp[segment]["alpha_simple_return"]
        ), segment
        assert actual[segment]["bot_simple_return_aligned"] == _approx(
            exp[segment]["bot_simple_return_aligned"]
        ), segment
        assert actual[segment]["benchmark_simple_return_aligned"] == _approx(
            exp[segment]["benchmark_simple_return_aligned"]
        ), segment
        assert actual[segment]["alpha_na_reason"] is None


def test_report_metadata_stays_versioned(golden_report: dict, golden_expected: dict) -> None:
    assert golden_report["spec_id"] == golden_expected["spec_id"] == "rpt_kpi.v1"
    assert golden_report["report_version"] == golden_expected["report_version"]
    assert golden_report["reporting_ccy"] == golden_expected["reporting_ccy"] == "USD"
    assert golden_report["trading_days_per_year"] == golden_expected["trading_days_per_year"] == 252
