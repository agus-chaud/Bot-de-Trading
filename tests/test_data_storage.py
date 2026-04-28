"""Behavior tests for MarketDB — SQLite storage and Supabase sync."""

from __future__ import annotations

from datetime import date

import pytest

from data.schema import CorporateActionRow, OHLCVRow
from data.storage import KillSwitchState, MarketDB


@pytest.fixture
def db(tmp_path):
    """Fresh in-memory-like MarketDB using a temp file per test."""
    return MarketDB(str(tmp_path / "test_market.db"))


def _ohlcv(symbol="SPY", ts=date(2024, 1, 15), venue="XNYS", imputed=False) -> OHLCVRow:
    return OHLCVRow(
        symbol=symbol, ts=ts, open=460.0, high=465.0, low=458.0,
        close=463.0, volume=80_000_000.0, currency="USD",
        venue=venue, imputed=imputed,
    )


def _action(symbol="SPY", ts=date(2024, 1, 15), type="dividend", factor=1.75) -> CorporateActionRow:
    return CorporateActionRow(symbol=symbol, ts=ts, type=type, factor=factor)


class TestOHLCVUpsert:
    def test_should_persist_and_retrieve_bar(self, db):
        db.upsert_ohlcv([_ohlcv()])
        rows = db.get_ohlcv("SPY", date(2024, 1, 1), date(2024, 1, 31), "XNYS")
        assert len(rows) == 1
        assert rows[0].symbol == "SPY"
        assert rows[0].close == pytest.approx(463.0)

    def test_should_be_idempotent_on_repeated_upsert(self, db):
        db.upsert_ohlcv([_ohlcv()])
        db.upsert_ohlcv([_ohlcv()])
        rows = db.get_ohlcv("SPY", date(2024, 1, 1), date(2024, 1, 31), "XNYS")
        assert len(rows) == 1

    def test_should_preserve_imputed_flag(self, db):
        db.upsert_ohlcv([_ohlcv(imputed=True)])
        rows = db.get_ohlcv("SPY", date(2024, 1, 1), date(2024, 1, 31), "XNYS")
        assert rows[0].imputed is True

    def test_should_return_bars_ordered_by_date(self, db):
        db.upsert_ohlcv([
            _ohlcv(ts=date(2024, 1, 17)),
            _ohlcv(ts=date(2024, 1, 15)),
            _ohlcv(ts=date(2024, 1, 16)),
        ])
        rows = db.get_ohlcv("SPY", date(2024, 1, 1), date(2024, 1, 31), "XNYS")
        dates = [r.ts for r in rows]
        assert dates == sorted(dates)

    def test_should_filter_by_venue(self, db):
        db.upsert_ohlcv([
            _ohlcv(symbol="GGAL", venue="XBUE"),
            _ohlcv(symbol="SPY", venue="XNYS"),
        ])
        us = db.get_ohlcv("SPY", date(2024, 1, 1), date(2024, 1, 31), "XNYS")
        ar = db.get_ohlcv("GGAL", date(2024, 1, 1), date(2024, 1, 31), "XBUE")
        assert len(us) == 1 and us[0].symbol == "SPY"
        assert len(ar) == 1 and ar[0].symbol == "GGAL"

    def test_should_filter_by_date_range(self, db):
        db.upsert_ohlcv([
            _ohlcv(ts=date(2024, 1, 10)),
            _ohlcv(ts=date(2024, 1, 15)),
            _ohlcv(ts=date(2024, 1, 20)),
        ])
        rows = db.get_ohlcv("SPY", date(2024, 1, 12), date(2024, 1, 18), "XNYS")
        assert len(rows) == 1
        assert rows[0].ts == date(2024, 1, 15)


class TestGetLastTs:
    def test_should_return_none_when_no_data(self, db):
        assert db.get_last_ts("SPY", "XNYS") is None

    def test_should_return_most_recent_date(self, db):
        db.upsert_ohlcv([
            _ohlcv(ts=date(2024, 1, 10)),
            _ohlcv(ts=date(2024, 1, 20)),
            _ohlcv(ts=date(2024, 1, 15)),
        ])
        assert db.get_last_ts("SPY", "XNYS") == date(2024, 1, 20)

    def test_should_scope_by_venue(self, db):
        db.upsert_ohlcv([_ohlcv(ts=date(2024, 1, 20), venue="XNYS")])
        assert db.get_last_ts("SPY", "XBUE") is None


class TestCorporateActionsUpsert:
    def test_should_persist_dividend(self, db):
        db.upsert_actions([_action(type="dividend", factor=1.75)])
        # verify via from_db adapter downstream; here check no exception raised
        db.upsert_actions([_action(type="dividend", factor=1.75)])  # idempotent

    def test_should_persist_split(self, db):
        db.upsert_actions([_action(type="split", factor=2.0)])
        db.upsert_actions([_action(type="split", factor=2.0)])  # idempotent


class TestFetchLog:
    def test_should_log_successful_fetch(self, db):
        db.log_fetch({"symbol": "SPY", "venue": "XNYS", "status": "ok", "source": "yfinance"})

    def test_should_log_skip_with_reason(self, db):
        db.log_fetch({
            "symbol": "GGAL", "venue": "XBUE",
            "status": "skip", "skip_reason": "outlier_price",
        })


class TestCalendarsUpsert:
    def test_should_persist_and_be_readable(self, db):
        days = [date(2024, 1, 15), date(2024, 1, 16), date(2024, 1, 17)]
        db.upsert_calendars("XNYS", days)
        cursor = db._conn.execute("SELECT COUNT(*) FROM calendars WHERE venue='XNYS'")
        assert cursor.fetchone()[0] == 3

    def test_should_be_idempotent(self, db):
        days = [date(2024, 1, 15)]
        db.upsert_calendars("XNYS", days)
        db.upsert_calendars("XNYS", days)
        cursor = db._conn.execute("SELECT COUNT(*) FROM calendars WHERE venue='XNYS'")
        assert cursor.fetchone()[0] == 1


class TestKillSwitch:
    _D = date(2024, 3, 15)

    def test_should_return_inactive_when_no_history(self, db):
        state = db.get_kill_switch_state("short")
        assert state.active is False
        assert state.activated_at is None
        assert state.monthly_dd is None
        assert state.reset_at is None
        assert state.reset_category is None
        assert state.reset_reason is None
        assert state.auto_reset is False

    def test_should_be_active_after_activation(self, db):
        db.activate_kill_switch(self._D, monthly_dd=-0.12, engine="short")
        state = db.get_kill_switch_state("short")
        assert state.active is True
        assert state.activated_at == self._D
        assert state.monthly_dd == pytest.approx(-0.12)

    def test_should_be_inactive_after_manual_reset(self, db):
        db.activate_kill_switch(self._D, monthly_dd=-0.15, engine="short")
        db.reset_kill_switch(self._D, category="manual", reason="trader override", auto=False, engine="short")
        state = db.get_kill_switch_state("short")
        assert state.active is False
        assert state.reset_category == "manual"
        assert state.reset_reason == "trader override"
        assert state.auto_reset is False

    def test_should_flag_auto_reset_correctly(self, db):
        db.activate_kill_switch(self._D, monthly_dd=-0.15, engine="short")
        db.reset_kill_switch(date(2024, 4, 1), category="month_change", reason="new month", auto=True, engine="short")
        state = db.get_kill_switch_state("short")
        assert state.active is False
        assert state.auto_reset is True

    def test_should_reflect_last_event_wins(self, db):
        # activate → reset → activate again: last event is 'activated'
        db.activate_kill_switch(self._D, monthly_dd=-0.10, engine="short")
        db.reset_kill_switch(date(2024, 4, 1), category="manual", reason="ok", engine="short")
        db.activate_kill_switch(date(2024, 4, 10), monthly_dd=-0.20, engine="short")
        state = db.get_kill_switch_state("short")
        assert state.active is True
        assert state.activated_at == date(2024, 4, 10)

    def test_should_isolate_engines(self, db):
        db.activate_kill_switch(self._D, monthly_dd=-0.18, engine="short")
        long_state = db.get_kill_switch_state("long")
        short_state = db.get_kill_switch_state("short")
        assert long_state.active is False
        assert short_state.active is True
