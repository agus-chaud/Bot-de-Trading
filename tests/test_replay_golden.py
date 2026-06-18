"""Characterization tests: replay_ledger_from_fills vs golden fixture (T0.2).

Golden fills mirror a minimal multi-day short sleeve sequence (BUY/BUY/SELL).
If replay order, venue mapping, or ledger accounting changes, this test breaks
before paper-live history is silently rewritten.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from core_sim.calendar_store import TradingCalendarStore
from data.storage import MarketDB

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "replay_golden"
PROD_CALENDAR = REPO_ROOT / "config" / "calendars" / "trading_days.v1.yaml"
STUB_CALENDAR = REPO_ROOT / "tests" / "fixtures" / "calendars" / "trading_days_stub.v1.yaml"


@pytest.fixture
def golden_fills() -> dict:
    with (FIXTURES / "fills.json").open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture
def golden_expected() -> dict:
    with (FIXTURES / "expected_ledger.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def _seed_db_from_golden(db: MarketDB, spec: dict) -> None:
    run_id = spec["run_id"]
    mode = spec["mode"]
    for day_block in spec["days"]:
        trading_day = date.fromisoformat(day_block["trading_day"])
        db.persist_fills(run_id, mode, trading_day, day_block["fills"])


def test_production_calendar_is_full_not_test_stub() -> None:
    """Guard: config calendar must not be the 4-day unit-test stub."""
    assert PROD_CALENDAR.is_file(), "missing config/calendars/trading_days.v1.yaml"
    store = TradingCalendarStore.from_yaml(PROD_CALENDAR)
    assert len(store.us_sessions) >= 200
    assert len(store.ar_business_days) >= 200
    assert store.is_us_session(date(2026, 4, 15)) is True
    assert store.is_ar_business_day(date(2026, 4, 15)) is True

    stub = TradingCalendarStore.from_yaml(STUB_CALENDAR)
    assert len(stub.us_sessions) == 4
    assert len(store.us_sessions) != len(stub.us_sessions)


def test_replay_matches_golden_ledger_state(
    tmp_path: Path,
    golden_fills: dict,
    golden_expected: dict,
) -> None:
    db = MarketDB(str(tmp_path / "replay_golden.db"))
    _seed_db_from_golden(db, golden_fills)

    ledger = db.replay_ledger_from_fills(
        mode=golden_fills["mode"],
        starting_cash=golden_fills["starting_cash"],
    )

    assert ledger.cash == pytest.approx(golden_expected["cash"])
    assert ledger.short_cash == pytest.approx(golden_expected["short_cash"])
    assert ledger.realized_pnl_total == pytest.approx(golden_expected["realized_pnl_total"])
    assert set(ledger.positions.keys()) == set(golden_expected["positions"].keys())

    for symbol, expected_pos in golden_expected["positions"].items():
        actual = ledger.positions[symbol]
        assert actual.qty == pytest.approx(expected_pos["qty"])
        assert actual.avg_cost == pytest.approx(expected_pos["avg_cost"])
        assert actual.market == expected_pos["market"]
        assert actual.bucket == expected_pos["bucket"]


def test_replay_golden_is_deterministic_across_two_passes(
    tmp_path: Path,
    golden_fills: dict,
) -> None:
    db = MarketDB(str(tmp_path / "replay_twice.db"))
    _seed_db_from_golden(db, golden_fills)

    first = db.replay_ledger_from_fills(
        mode=golden_fills["mode"],
        starting_cash=golden_fills["starting_cash"],
    )
    second = db.replay_ledger_from_fills(
        mode=golden_fills["mode"],
        starting_cash=golden_fills["starting_cash"],
    )

    assert first.cash == pytest.approx(second.cash)
    assert first.realized_pnl_total == pytest.approx(second.realized_pnl_total)
    assert set(first.positions.keys()) == set(second.positions.keys())
