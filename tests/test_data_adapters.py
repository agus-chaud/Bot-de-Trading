"""Behavior tests for from_db() adapters in calendar_store and corporate_actions."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from core_sim.calendar_store import TradingCalendarStore
from core_sim.corporate_actions import CorporateActionsStore
from data.schema import CorporateActionRow, OHLCVRow
from data.storage import MarketDB


@pytest.fixture
def db(tmp_path):
    return MarketDB(str(tmp_path / "adapter_test.db"))


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "adapter_test.db")


class TestTradingCalendarStoreFromDb:
    def test_should_load_us_sessions_from_db(self, db, db_path, tmp_path):
        db2 = MarketDB(db_path)
        us_days = [date(2024, 1, 15), date(2024, 1, 16)]
        ar_days = [date(2024, 1, 15)]
        db2.upsert_calendars("XNYS", us_days)
        db2.upsert_calendars("XBUE", ar_days)

        store = TradingCalendarStore.from_db(db_path)

        assert store.is_us_session(date(2024, 1, 15)) is True
        assert store.is_us_session(date(2024, 1, 16)) is True
        assert store.is_us_session(date(2024, 1, 17)) is False

    def test_should_load_ar_business_days_from_db(self, tmp_path):
        path = str(tmp_path / "cal2.db")
        db = MarketDB(path)
        db.upsert_calendars("XBUE", [date(2024, 1, 15)])

        store = TradingCalendarStore.from_db(path)

        assert store.is_ar_business_day(date(2024, 1, 15)) is True
        assert store.is_ar_business_day(date(2024, 1, 16)) is False

    def test_should_return_empty_sets_when_no_calendar_data(self, tmp_path):
        path = str(tmp_path / "empty.db")
        MarketDB(path)  # init schema only

        store = TradingCalendarStore.from_db(path)

        assert store.is_us_session(date(2024, 1, 15)) is False
        assert store.is_ar_business_day(date(2024, 1, 15)) is False

    def test_from_yaml_still_works(self):
        """Regression: from_yaml() loads the test stub fixture (not production calendar)."""
        yaml_path = (
            Path(__file__).resolve().parents[1]
            / "tests"
            / "fixtures"
            / "calendars"
            / "trading_days_stub.v1.yaml"
        )
        store = TradingCalendarStore.from_yaml(yaml_path)
        assert len(store.us_sessions) == 4
        assert len(store.ar_business_days) == 4


class TestCorporateActionsStoreFromDb:
    def test_should_load_dividend_from_db(self, tmp_path):
        path = str(tmp_path / "ca.db")
        db = MarketDB(path)
        db.upsert_actions([
            CorporateActionRow(symbol="SPY", ts=date(2024, 1, 15), type="dividend", factor=1.75)
        ])

        store = CorporateActionsStore.from_db(path)
        actions = store.get_for_day(date(2024, 1, 15))

        assert len(actions) == 1
        assert actions[0].symbol == "SPY"
        assert actions[0].action_type == "dividend"
        assert actions[0].value == pytest.approx(1.75)

    def test_should_load_split_from_db(self, tmp_path):
        path = str(tmp_path / "ca2.db")
        db = MarketDB(path)
        db.upsert_actions([
            CorporateActionRow(symbol="IWM", ts=date(2024, 1, 16), type="split", factor=2.0)
        ])

        store = CorporateActionsStore.from_db(path)
        actions = store.get_for_day(date(2024, 1, 16))

        assert len(actions) == 1
        assert actions[0].action_type == "split"
        assert actions[0].value == pytest.approx(2.0)

    def test_should_filter_by_symbol(self, tmp_path):
        path = str(tmp_path / "ca3.db")
        db = MarketDB(path)
        db.upsert_actions([
            CorporateActionRow(symbol="SPY", ts=date(2024, 1, 15), type="dividend", factor=1.75),
            CorporateActionRow(symbol="QQQ", ts=date(2024, 1, 15), type="dividend", factor=0.90),
        ])

        store = CorporateActionsStore.from_db(path)
        actions = store.get_for_day(date(2024, 1, 15), symbols={"SPY"})

        assert len(actions) == 1
        assert actions[0].symbol == "SPY"

    def test_should_return_empty_when_no_actions_for_day(self, tmp_path):
        path = str(tmp_path / "ca4.db")
        MarketDB(path)

        store = CorporateActionsStore.from_db(path)
        assert store.get_for_day(date(2024, 1, 15)) == ()

    def test_from_yaml_still_works(self):
        """Regression: existing from_yaml() must not be broken by the new adapter."""
        from pathlib import Path
        yaml_path = Path(__file__).resolve().parents[1] / "config" / "corporate_actions" / "us_actions.v1.yaml"
        store = CorporateActionsStore.from_yaml(yaml_path)
        assert isinstance(store.actions_by_day, dict)
