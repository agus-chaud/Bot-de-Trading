"""Tests for scripts/reset_kill_switch.py — behavior-based, real SQLite :memory:."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.storage import MarketDB
from scripts.reset_kill_switch import main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(activated: bool = True, monthly_dd: float = -0.09) -> MarketDB:
    """In-memory DB with optional pre-activated kill switch."""
    db = MarketDB(":memory:")
    if activated:
        db.activate_kill_switch(date(2026, 4, 15), monthly_dd, engine="short")
    return db


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_reset_active_kill_switch_writes_to_db_and_prints_ok(capsys):
    db = _make_db(activated=True, monthly_dd=-0.083)

    with patch("scripts.reset_kill_switch.MarketDB", return_value=db):
        with pytest.raises(SystemExit) as exc:
            main(["--category", "volatility_spike", "--reason", "market normalized", "--db", ":memory:"])

    assert exc.value.code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "reset_ok"
    assert output["engine"] == "short"
    assert output["category"] == "volatility_spike"
    assert output["reason"] == "market normalized"
    assert output["previously_monthly_dd"] == pytest.approx(-0.083)
    assert output["previously_activated_at"] == "2026-04-15"


def test_reset_confirms_kill_switch_inactive_in_db_after_reset():
    """After a successful reset, the DB must reflect active=False."""
    db = _make_db(activated=True, monthly_dd=-0.09)

    with patch("scripts.reset_kill_switch.MarketDB", return_value=db):
        with pytest.raises(SystemExit):
            main(["--category", "strategy_review", "--reason", "manual review done"])

    state = db.get_kill_switch_state("short")
    assert state.active is False
    assert state.reset_category == "strategy_review"
    assert state.reset_reason == "manual review done"


# ---------------------------------------------------------------------------
# No active kill switch
# ---------------------------------------------------------------------------

def test_no_active_kill_switch_exits_1_with_correct_status(capsys):
    db = _make_db(activated=False)

    with patch("scripts.reset_kill_switch.MarketDB", return_value=db):
        with pytest.raises(SystemExit) as exc:
            main(["--category", "other", "--reason", "just checking"])

    assert exc.value.code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "no_active_kill_switch"
    assert output["engine"] == "short"


# ---------------------------------------------------------------------------
# Empty / whitespace reason
# ---------------------------------------------------------------------------

def test_empty_reason_exits_1_with_error_json(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--category", "other", "--reason", ""])

    assert exc.value.code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "reason cannot be empty"


def test_whitespace_only_reason_exits_1_with_error_json(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--category", "other", "--reason", "   "])

    assert exc.value.code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "reason cannot be empty"


# ---------------------------------------------------------------------------
# Engine argument is forwarded correctly
# ---------------------------------------------------------------------------

def test_reset_uses_specified_engine(capsys):
    db = MarketDB(":memory:")
    db.activate_kill_switch(date(2026, 4, 20), -0.10, engine="long")

    with patch("scripts.reset_kill_switch.MarketDB", return_value=db):
        with pytest.raises(SystemExit) as exc:
            main(["--category", "data_error", "--reason", "bad feed fixed", "--engine", "long"])

    assert exc.value.code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["engine"] == "long"

    state = db.get_kill_switch_state("long")
    assert state.active is False
