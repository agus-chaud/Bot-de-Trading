"""Behavior tests for dashboard risk matrix (#4) and trade thesis (#2)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dashboard.risk_matrix import build_risk_matrix  # noqa: E402
from dashboard.trade_thesis import build_position_theses  # noqa: E402


def _pos(symbol, *, bucket="short", qty=100.0, mv=10_000.0, upnl=0.0, avg=50.0, stale=False):
    return {
        "symbol": symbol,
        "qty": qty,
        "avg_cost": avg,
        "market": "US",
        "bucket": bucket,
        "market_value": mv,
        "unrealized_pnl": upnl,
        "stale": stale,
    }


# ---------------- Risk matrix ----------------


def _by_code(matrix, code):
    return next(r for r in matrix if r["code"] == code)


def test_stale_market_data_should_fire_critical_when_lag_large():
    matrix = build_risk_matrix(
        latest_snapshot={"short_monthly_drawdown": 0.0},
        positions=[_pos("KO")],
        max_data_lag_days=40,
        fetch_issue_count=0,
        ks_active=False,
        ks_floor=-0.08,
    )
    stale = _by_code(matrix, "stale_market_data")
    assert stale["severity"] == "critical"
    assert stale["probability"] == "alta"
    assert stale["status"] == "activo"


def test_stale_market_data_should_be_controlled_when_fresh():
    matrix = build_risk_matrix(
        latest_snapshot={"short_monthly_drawdown": 0.0},
        positions=[_pos("KO")],
        max_data_lag_days=0,
        fetch_issue_count=0,
        ks_active=False,
        ks_floor=-0.08,
    )
    assert _by_code(matrix, "stale_market_data")["severity"] == "ok"


def test_concentration_should_flag_single_position():
    matrix = build_risk_matrix(
        latest_snapshot=None,
        positions=[_pos("KO", mv=10_000.0)],
        max_data_lag_days=0,
        fetch_issue_count=0,
        ks_active=False,
        ks_floor=-0.08,
    )
    conc = _by_code(matrix, "concentration")
    assert conc["probability"] == "alta"
    assert "top 100%" in conc["status"]


def test_drawdown_near_kill_switch_should_escalate():
    matrix = build_risk_matrix(
        latest_snapshot={"short_monthly_drawdown": -0.07},  # 0.875 del piso -0.08
        positions=[_pos("KO")],
        max_data_lag_days=0,
        fetch_issue_count=0,
        ks_active=False,
        ks_floor=-0.08,
    )
    dd = _by_code(matrix, "drawdown_kill_switch")
    assert dd["severity"] == "warning"
    assert dd["status"] == "activo"


def test_matrix_should_sort_critical_first():
    matrix = build_risk_matrix(
        latest_snapshot={"short_monthly_drawdown": 0.0},
        positions=[_pos("KO")],
        max_data_lag_days=40,  # critical
        fetch_issue_count=0,
        ks_active=False,
        ks_floor=-0.08,
    )
    assert matrix[0]["severity"] == "critical"


# ---------------- Trade thesis ----------------


def _down_closes() -> list[float]:
    return [100.0 - i for i in range(20)]  # 100 -> 81, bajista


def _up_closes() -> list[float]:
    return [80.0 + i for i in range(20)]  # 80 -> 99, alcista


def test_short_with_downtrend_should_be_bullish_for_position():
    theses = build_position_theses(
        [_pos("KO", bucket="short", upnl=500.0)],
        {"KO": {"closes": _down_closes(), "lag_days": 0}},
    )
    t = theses[0]
    assert t["side"] == "short"
    assert any("bajista" in b and "favorece el short" in b for b in t["bull"])
    assert t["technical"]["trend"] == "bajista"


def test_long_with_uptrend_should_be_bullish_for_position():
    theses = build_position_theses(
        [_pos("AAPL", bucket="long", qty=10.0, upnl=200.0)],
        {"AAPL": {"closes": _up_closes(), "lag_days": 0}},
    )
    t = theses[0]
    assert t["side"] == "long"
    assert any("favorece el largo" in b for b in t["bull"])


def test_negative_pnl_should_push_stance_to_revisar():
    theses = build_position_theses(
        [_pos("KO", bucket="short", upnl=-900.0, avg=50.0, qty=100.0)],
        {"KO": {"closes": _up_closes(), "lag_days": 0}},  # sube => en contra del short
    )
    t = theses[0]
    assert t["stance"] == "Revisar"
    assert any("PnL" in b for b in t["bear"])


def test_stale_lag_should_add_bear_factor():
    theses = build_position_theses(
        [_pos("KO", bucket="short")],
        {"KO": {"closes": _down_closes(), "lag_days": 5}},
    )
    assert any("atrasada" in b for b in theses[0]["bear"])
