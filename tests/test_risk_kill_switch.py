"""Behavior tests for check_and_persist_kill_switch."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from core_sim.risk_guardrails import check_and_persist_kill_switch
from data.storage import MarketDB


@pytest.fixture
def db(tmp_path):
    return MarketDB(str(tmp_path / "ks.db"))


_CONFIG = {"kill_dd": -0.08}
_ENGINE = "short"


class TestKillSwitchAboveThreshold:
    def test_should_allow_when_dd_above_threshold(self, db, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sb = {"monthly_drawdown": -0.05}
        result = check_and_persist_kill_switch(sb, _CONFIG, db, _ENGINE, date(2026, 4, 15))
        assert result.allowed is True
        assert result.reason == "ok"
        assert result.meta["monthly_drawdown"] == pytest.approx(-0.05)
        state = db.get_kill_switch_state(_ENGINE)
        assert state.active is False


class TestKillSwitchCrossesThreshold:
    def test_should_block_activate_and_create_alert(self, db, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        today = date(2026, 4, 15)
        sb = {"monthly_drawdown": -0.10}
        result = check_and_persist_kill_switch(sb, _CONFIG, db, _ENGINE, today)
        assert result.allowed is False
        assert result.reason == "short_monthly_kill_switch"
        assert result.meta["persisted"] is False
        assert result.meta["monthly_drawdown"] == pytest.approx(-0.10)

        state = db.get_kill_switch_state(_ENGINE)
        assert state.active is True
        assert state.activated_at == today

    def test_should_write_alert_file_with_correct_fields(self, db, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        today = date(2026, 4, 15)
        sb = {"monthly_drawdown": -0.10}
        check_and_persist_kill_switch(sb, _CONFIG, db, _ENGINE, today)

        alert_path = tmp_path / "alerts" / f"kill_switch_{today.isoformat()}.json"
        assert alert_path.exists()
        payload = json.loads(alert_path.read_text())
        assert payload["engine"] == _ENGINE
        assert payload["monthly_dd"] == pytest.approx(-0.10)
        assert payload["kill_dd"] == pytest.approx(-0.08)
        assert payload["date"] == today.isoformat()
        assert "ts" in payload


class TestKillSwitchAlreadyActiveSameMonth:
    def test_should_return_persisted_true_and_not_reactivate(self, db, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        today = date(2026, 4, 15)
        db.activate_kill_switch(today, -0.12, _ENGINE)

        # DD would cross again but kill switch is already active
        sb = {"monthly_drawdown": -0.15}
        result = check_and_persist_kill_switch(sb, _CONFIG, db, _ENGINE, today)
        assert result.allowed is False
        assert result.meta["persisted"] is True

        # Only one activation row in DB
        cursor = db._conn.execute(
            "SELECT COUNT(*) FROM kill_switch_log WHERE engine=? AND event='activated'", (_ENGINE,)
        )
        assert cursor.fetchone()[0] == 1


class TestKillSwitchAutoReset:
    def test_should_auto_reset_previous_month_and_allow_when_dd_ok(self, db, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Activated in March
        db.activate_kill_switch(date(2026, 3, 20), -0.12, _ENGINE)

        # Called in April with DD above threshold
        sb = {"monthly_drawdown": -0.03}
        result = check_and_persist_kill_switch(sb, _CONFIG, db, _ENGINE, date(2026, 4, 1))
        assert result.allowed is True
        assert result.reason == "ok"

        state = db.get_kill_switch_state(_ENGINE)
        assert state.active is False
        assert state.auto_reset is True
        assert state.reset_category == "auto_month_reset"

    def test_should_auto_reset_then_reactivate_when_dd_crosses(self, db, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Activated in March
        db.activate_kill_switch(date(2026, 3, 20), -0.09, _ENGINE)

        # Called in April with DD crossing threshold again
        sb = {"monthly_drawdown": -0.11}
        result = check_and_persist_kill_switch(sb, _CONFIG, db, _ENGINE, date(2026, 4, 1))
        assert result.allowed is False
        assert result.meta["persisted"] is False

        state = db.get_kill_switch_state(_ENGINE)
        assert state.active is True
        assert state.activated_at == date(2026, 4, 1)
