"""Tests for MarketDB paper-persistence helpers (behavior-focused)."""

from __future__ import annotations

from datetime import date

import pytest

from data.storage import MarketDB


@pytest.fixture
def db():
    """Real MarketDB backed by SQLite in-memory."""
    return MarketDB(":memory:")


class TestGetLastSnapshotDay:
    """get_last_snapshot_day returns the latest trading_day for a given mode."""

    def test_returns_none_on_empty_table(self, db: MarketDB):
        assert db.get_last_snapshot_day("paper_live") is None

    def test_returns_correct_date_after_persist(self, db: MarketDB):
        day = date(2026, 5, 9)
        snap = {
            "equity_total": 1000.0,
            "equity_short": 300.0,
            "equity_long": 700.0,
            "cash": 500.0,
            "realized_pnl_total": 0.0,
            "unrealized_pnl_total": 0.0,
            "costs_day": 0.0,
            "mv_us": 800.0,
            "mv_ar": 200.0,
        }
        db.persist_snapshot("paper_live", day, snap, short_cash=150.0)

        assert db.get_last_snapshot_day("paper_live") == day

    def test_returns_max_across_multiple_days(self, db: MarketDB):
        snap = {
            "equity_total": 1000.0,
            "equity_short": 300.0,
            "equity_long": 700.0,
            "cash": 500.0,
            "realized_pnl_total": 0.0,
            "unrealized_pnl_total": 0.0,
            "costs_day": 0.0,
            "mv_us": 800.0,
            "mv_ar": 200.0,
        }
        db.persist_snapshot("paper_live", date(2026, 5, 7), snap, short_cash=150.0)
        db.persist_snapshot("paper_live", date(2026, 5, 9), snap, short_cash=150.0)
        db.persist_snapshot("paper_live", date(2026, 5, 8), snap, short_cash=150.0)

        assert db.get_last_snapshot_day("paper_live") == date(2026, 5, 9)

    def test_filters_by_mode(self, db: MarketDB):
        snap = {
            "equity_total": 1000.0,
            "equity_short": 300.0,
            "equity_long": 700.0,
            "cash": 500.0,
            "realized_pnl_total": 0.0,
            "unrealized_pnl_total": 0.0,
            "costs_day": 0.0,
            "mv_us": 800.0,
            "mv_ar": 200.0,
        }
        db.persist_snapshot("paper_live", date(2026, 5, 7), snap, short_cash=150.0)
        db.persist_snapshot("backtest", date(2026, 5, 9), snap, short_cash=150.0)

        assert db.get_last_snapshot_day("paper_live") == date(2026, 5, 7)
        assert db.get_last_snapshot_day("backtest") == date(2026, 5, 9)
