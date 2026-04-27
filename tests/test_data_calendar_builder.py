"""Behavior tests for calendar_builder — mocks pandas_market_calendars."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from data.calendar_builder import build_calendar
from data.storage import MarketDB


@pytest.fixture
def db(tmp_path):
    return MarketDB(str(tmp_path / "cal_test.db"))


def _make_mock_calendar(dates: list[date]):
    """Return a mock mcal calendar whose valid_days returns a DatetimeIndex."""
    dt_index = pd.DatetimeIndex([pd.Timestamp(d) for d in dates])
    mock_cal = MagicMock()
    mock_cal.valid_days.return_value = dt_index
    return mock_cal


class TestBuildCalendar:
    def test_should_persist_nyse_sessions_under_xnys_venue(self, db):
        us_days = [date(2024, 1, 15), date(2024, 1, 16)]
        ar_days = [date(2024, 1, 15)]

        with patch("data.calendar_builder.mcal") as mock_mcal:
            mock_mcal.get_calendar.side_effect = lambda exchange: (
                _make_mock_calendar(us_days) if exchange == "NYSE"
                else _make_mock_calendar(ar_days)
            )
            build_calendar(date(2024, 1, 1), date(2024, 1, 31), db)

        cursor = db._conn.execute("SELECT ts FROM calendars WHERE venue='XNYS' ORDER BY ts")
        stored = [row[0] for row in cursor.fetchall()]
        assert stored == ["2024-01-15", "2024-01-16"]

    def test_should_persist_xbue_sessions_under_xbue_venue(self, db):
        us_days = [date(2024, 1, 15)]
        ar_days = [date(2024, 1, 15), date(2024, 1, 16), date(2024, 1, 17)]

        with patch("data.calendar_builder.mcal") as mock_mcal:
            mock_mcal.get_calendar.side_effect = lambda exchange: (
                _make_mock_calendar(us_days) if exchange == "NYSE"
                else _make_mock_calendar(ar_days)
            )
            build_calendar(date(2024, 1, 1), date(2024, 1, 31), db)

        cursor = db._conn.execute("SELECT COUNT(*) FROM calendars WHERE venue='XBUE'")
        assert cursor.fetchone()[0] == 3

    def test_should_be_idempotent_on_repeated_calls(self, db):
        us_days = [date(2024, 1, 15)]
        ar_days = [date(2024, 1, 15)]

        with patch("data.calendar_builder.mcal") as mock_mcal:
            mock_mcal.get_calendar.return_value = _make_mock_calendar(us_days)
            build_calendar(date(2024, 1, 1), date(2024, 1, 31), db)
            build_calendar(date(2024, 1, 1), date(2024, 1, 31), db)

        cursor = db._conn.execute("SELECT COUNT(*) FROM calendars WHERE venue='XNYS'")
        assert cursor.fetchone()[0] == 1
