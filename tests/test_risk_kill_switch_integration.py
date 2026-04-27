"""Integration tests: kill switch full lifecycle across all layers.

Verifies that MarketDB (SQLite), check_and_persist_kill_switch, and the reset
script main() cooperate correctly end-to-end in realistic multi-day scenarios.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

from core_sim.risk_guardrails import check_and_persist_kill_switch
from data.storage import MarketDB

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from reset_kill_switch import main as reset_main  # noqa: E402

_CONFIG_SHORT = {"kill_dd": -0.08}
_CONFIG_LONG = {"kill_dd": -0.08}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db(tmp_path: Path, name: str = "ks.db") -> MarketDB:
    return MarketDB(str(tmp_path / name))


def _all_events(db: MarketDB, engine: str) -> list[dict]:
    cursor = db._conn.execute(
        "SELECT event, event_date, auto_reset FROM kill_switch_log WHERE engine=? ORDER BY id ASC",
        (engine,),
    )
    return [dict(row) for row in cursor.fetchall()]


# ---------------------------------------------------------------------------
# Escenario 1: Activación → bloqueo → reset manual → reactivación
# ---------------------------------------------------------------------------

class TestFullLifecycle:
    def test_full_activation_block_reset_reactivation_cycle(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        db = _db(tmp_path)
        db_path = str(tmp_path / "ks.db")

        # Día 1: DD saludable — permitido, sin eventos en DB
        r1 = check_and_persist_kill_switch(
            {"monthly_drawdown": -0.05}, _CONFIG_SHORT, db, "short", date(2026, 4, 1)
        )
        assert r1.allowed is True
        assert _all_events(db, "short") == []

        # Día 2: DD cruza umbral → activa, bloquea, escribe alert file
        r2 = check_and_persist_kill_switch(
            {"monthly_drawdown": -0.09}, _CONFIG_SHORT, db, "short", date(2026, 4, 2)
        )
        assert r2.allowed is False
        assert r2.meta["persisted"] is False
        events = _all_events(db, "short")
        assert len(events) == 1
        assert events[0]["event"] == "activated"
        alert_file = tmp_path / "alerts" / "kill_switch_2026-04-02.json"
        assert alert_file.exists()

        # Día 3: DD sigue bajo umbral → bloqueado, SIN nuevo evento en DB
        r3 = check_and_persist_kill_switch(
            {"monthly_drawdown": -0.09}, _CONFIG_SHORT, db, "short", date(2026, 4, 3)
        )
        assert r3.allowed is False
        assert r3.meta["persisted"] is True
        assert len(_all_events(db, "short")) == 1  # aún solo el activated original

        # Reset manual vía script — debe salir con código 0
        with pytest.raises(SystemExit) as exc_info:
            reset_main(["--category", "volatility_spike", "--reason", "revisado", "--db", db_path])
        assert exc_info.value.code == 0

        # Día 4: DD vuelve a rango sano → auto-reset ya ocurrió (reset manual), permite operar
        # Necesitamos una nueva conexión para reflejar el estado post-reset
        db2 = MarketDB(db_path)
        r4 = check_and_persist_kill_switch(
            {"monthly_drawdown": -0.02}, _CONFIG_SHORT, db2, "short", date(2026, 4, 4)
        )
        assert r4.allowed is True

        state = db2.get_kill_switch_state("short")
        assert state.active is False
        events_final = _all_events(db2, "short")
        event_types = [e["event"] for e in events_final]
        assert "activated" in event_types
        assert "reset" in event_types


# ---------------------------------------------------------------------------
# Escenario 2: Auto-reset al inicio del mes siguiente con DD sano
# ---------------------------------------------------------------------------

class TestAutoResetNewMonth:
    def test_auto_reset_on_new_month_when_dd_is_healthy(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        db = _db(tmp_path)

        # Mes M, Día 15: activa kill switch
        db.activate_kill_switch(date(2026, 3, 15), -0.09, "short")

        # Mes M+1, Día 1: DD sano → auto-reset + allowed=True
        r = check_and_persist_kill_switch(
            {"monthly_drawdown": -0.02}, _CONFIG_SHORT, db, "short", date(2026, 4, 1)
        )
        assert r.allowed is True
        assert r.reason == "ok"

        state = db.get_kill_switch_state("short")
        assert state.active is False
        assert state.auto_reset is True
        assert state.reset_category == "auto_month_reset"

        events = _all_events(db, "short")
        event_types = [e["event"] for e in events]
        assert event_types.count("reset") >= 1
        reset_evt = next(e for e in events if e["event"] == "reset")
        assert reset_evt["auto_reset"] == 1


# ---------------------------------------------------------------------------
# Escenario 3: Auto-reset + DD vuelve a cruzar en el nuevo mes
# ---------------------------------------------------------------------------

class TestAutoResetThenReactivate:
    def test_auto_reset_followed_by_immediate_reactivation_when_dd_crosses(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        db = _db(tmp_path)

        # Mes M, Día 20: activa kill switch
        db.activate_kill_switch(date(2026, 3, 20), -0.09, "short")

        # Mes M+1, Día 1: DD cruza de nuevo → auto-reset + re-activación en el mismo ciclo
        r = check_and_persist_kill_switch(
            {"monthly_drawdown": -0.10}, _CONFIG_SHORT, db, "short", date(2026, 4, 1)
        )
        assert r.allowed is False
        assert r.meta["persisted"] is False  # nueva activación, no persisted

        state = db.get_kill_switch_state("short")
        assert state.active is True
        assert state.activated_at == date(2026, 4, 1)

        events = _all_events(db, "short")
        event_types = [e["event"] for e in events]
        # Debe haber: activated (Mar) + reset (auto) + activated (Apr)
        assert event_types.count("activated") == 2
        assert event_types.count("reset") == 1

        # Alert file del mes nuevo debe existir
        alert_file = tmp_path / "alerts" / "kill_switch_2026-04-01.json"
        assert alert_file.exists()


# ---------------------------------------------------------------------------
# Escenario 4: Alert file — contenido correcto y persiste post-reset
# ---------------------------------------------------------------------------

class TestAlertFile:
    def test_alert_file_has_correct_fields_when_kill_switch_activates(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        db = _db(tmp_path)
        today = date(2026, 5, 10)

        check_and_persist_kill_switch(
            {"monthly_drawdown": -0.09}, _CONFIG_SHORT, db, "short", today
        )

        alert_path = tmp_path / "alerts" / f"kill_switch_{today.isoformat()}.json"
        assert alert_path.exists()
        payload = json.loads(alert_path.read_text())

        assert payload["ts"]  # ISO timestamp presente
        assert payload["engine"] == "short"
        assert payload["monthly_dd"] == pytest.approx(-0.09)
        assert payload["kill_dd"] == pytest.approx(-0.08)
        assert payload["date"] == today.isoformat()

    def test_alert_file_is_preserved_after_manual_reset(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        db = _db(tmp_path)
        db_path = str(tmp_path / "ks.db")
        today = date(2026, 5, 10)

        check_and_persist_kill_switch(
            {"monthly_drawdown": -0.09}, _CONFIG_SHORT, db, "short", today
        )
        alert_path = tmp_path / "alerts" / f"kill_switch_{today.isoformat()}.json"
        assert alert_path.exists()

        with pytest.raises(SystemExit) as exc_info:
            reset_main(["--category", "other", "--reason", "revisado manualmente", "--db", db_path])
        assert exc_info.value.code == 0

        # El alert file es evidencia histórica — NO debe borrarse
        assert alert_path.exists()


# ---------------------------------------------------------------------------
# Escenario 5: Reset script rechaza si no hay kill switch activo
# ---------------------------------------------------------------------------

class TestResetScriptGuards:
    def test_reset_script_exits_with_error_when_no_active_kill_switch(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        db_path = str(tmp_path / "empty.db")
        MarketDB(db_path)  # crea la DB vacía con schema

        with pytest.raises(SystemExit) as exc_info:
            reset_main(["--category", "other", "--reason", "test", "--db", db_path])
        assert exc_info.value.code == 1

    def test_reset_script_output_contains_no_active_kill_switch_status(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        db_path = str(tmp_path / "empty.db")
        MarketDB(db_path)

        with pytest.raises(SystemExit):
            reset_main(["--category", "other", "--reason", "test", "--db", db_path])

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["status"] == "no_active_kill_switch"


# ---------------------------------------------------------------------------
# Escenario 6: Dos engines son completamente independientes
# ---------------------------------------------------------------------------

class TestEngineIsolation:
    def test_short_kill_switch_does_not_affect_long_engine(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        db = _db(tmp_path)
        today = date(2026, 4, 15)

        # Engine "short" cruza umbral → kill switch activo
        r_short = check_and_persist_kill_switch(
            {"monthly_drawdown": -0.09}, _CONFIG_SHORT, db, "short", today
        )
        assert r_short.allowed is False

        # Engine "long" con DD sano → no bloqueado
        r_long = check_and_persist_kill_switch(
            {"monthly_drawdown": -0.02}, _CONFIG_LONG, db, "long", today
        )
        assert r_long.allowed is True

        state_short = db.get_kill_switch_state("short")
        state_long = db.get_kill_switch_state("long")
        assert state_short.active is True
        assert state_long.active is False

    def test_resetting_short_does_not_change_long_state(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        db = _db(tmp_path)
        db_path = str(tmp_path / "ks.db")
        today = date(2026, 4, 15)

        # Activar "short"; dejar "long" limpio
        check_and_persist_kill_switch(
            {"monthly_drawdown": -0.09}, _CONFIG_SHORT, db, "short", today
        )

        with pytest.raises(SystemExit) as exc_info:
            reset_main(["--category", "other", "--reason", "ok", "--engine", "short", "--db", db_path])
        assert exc_info.value.code == 0

        db2 = MarketDB(db_path)
        assert db2.get_kill_switch_state("short").active is False
        # "long" nunca tuvo eventos — debe permanecer inactivo
        assert db2.get_kill_switch_state("long").active is False
        assert _all_events(db2, "long") == []
