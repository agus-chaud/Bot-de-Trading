"""Behavior tests for scripts/export_dashboard_payload.py (F1-01)."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.export_dashboard_payload import (  # noqa: E402
    export_dashboard_payload,
    main,
    validate_payload_shape,
    write_dashboard_payload,
)

_POLICY = REPO_ROOT / "config" / "policy.v1.yaml"
_CALENDAR = REPO_ROOT / "tests" / "fixtures" / "calendars" / "trading_days_stub.v1.yaml"


def _snapshot(equity_total: float) -> dict:
    return {
        "equity_total": equity_total,
        "equity_short": equity_total * 0.3,
        "equity_long": equity_total * 0.7,
        "cash": equity_total * 0.2,
        "realized_pnl_total": 0.0,
        "unrealized_pnl_total": 0.0,
        "costs_day": 0.0,
        "mv_us": equity_total * 0.4,
        "mv_ar": equity_total * 0.4,
        "positions": {},
        "short_bucket": {
            "monthly_peak": equity_total * 0.3,
            "monthly_drawdown": -0.01,
            "daily_return": 0.002,
        },
    }


@pytest.fixture
def seeded_db(tmp_path):
    from data.storage import MarketDB

    db_path = tmp_path / "market.db"
    db = MarketDB(str(db_path))
    db.ensure_portfolio_meta(
        "paper_live",
        starting_cash=1_000_000.0,
        currency="ARS",
        inception_date=date(2026, 5, 1),
    )
    db.persist_snapshot(
        "paper_live", date(2026, 5, 1), _snapshot(1_000_000.0), short_cash=0.0
    )
    db.persist_snapshot(
        "paper_live", date(2026, 5, 2), _snapshot(1_010_000.0), short_cash=5_000.0
    )
    return db_path


def test_export_should_match_dashboard_api_shape(seeded_db):
    payload = export_dashboard_payload(
        db_path=seeded_db,
        policy_path=_POLICY,
        calendar_path=_CALENDAR,
    )
    validate_payload_shape(payload)
    assert payload["export_version"] == "1"
    assert len(payload["equity_curve"]) == 2
    assert payload["meta"]["currency"] == "ARS"
    assert payload["meta"]["last_trading_day"] == "2026-05-02"
    assert payload["kpis"]["status"] == "ok"


def test_cli_should_write_json_file(seeded_db, tmp_path):
    out = tmp_path / "payload.json"
    code = main(
        [
            "--db",
            str(seeded_db),
            "--policy",
            str(_POLICY),
            "--calendar",
            str(_CALENDAR),
            "--out",
            str(out),
            "--pretty",
        ]
    )
    assert code == 0
    assert out.is_file()
    loaded = json.loads(out.read_text(encoding="utf-8"))
    validate_payload_shape(loaded)
    assert loaded["equity_curve"][-1]["equity_total"] == pytest.approx(1_010_000.0)


def test_should_fail_when_db_missing(tmp_path):
    code = main(
        [
            "--db",
            str(tmp_path / "missing.db"),
            "--policy",
            str(_POLICY),
            "--calendar",
            str(_CALENDAR),
            "--out",
            str(tmp_path / "out.json"),
        ]
    )
    assert code == 1


def test_write_dashboard_payload_creates_parent_dirs(tmp_path):
    out = tmp_path / "nested" / "payload.json"
    write_dashboard_payload({"export_version": "1", "ok": True}, out)
    assert out.is_file()


def test_paper_live_workflow_should_export_dashboard_artifact():
    workflow = REPO_ROOT / ".github" / "workflows" / "paper_live_daily.yml"
    text = workflow.read_text(encoding="utf-8")
    assert "export_dashboard_payload.py" in text
    assert "actions/upload-artifact@v4" in text
    assert "dashboard-payload" in text
    assert "data/dashboard_payload.json" in text
    assert "git add -f data/market.db data/dashboard_payload.json" in text
