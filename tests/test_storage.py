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


class TestUniverseSnapshots:
    def test_replace_and_read_universe_roundtrip(self, db: MarketDB):
        from data.schema import UniverseSnapshotRow

        d = date(2026, 5, 1)
        rows = [
            UniverseSnapshotRow(
                selection_date=d,
                bucket="merval",
                symbol="GGAL",
                rank=1,
                metric_value=1e6,
                source="dynamic",
                schema_version=1,
            ),
            UniverseSnapshotRow(
                selection_date=d,
                bucket="cedear",
                symbol="AAPL",
                rank=1,
                metric_value=2e6,
                source="dynamic",
                schema_version=1,
            ),
        ]
        db.replace_universe_snapshots(d, rows)
        assert db.get_latest_universe_selection_date() == d
        loaded = db.get_universe_snapshots_for_date(d)
        assert len(loaded) == 2
        assert loaded[0].symbol == "AAPL"
        assert loaded[1].symbol == "GGAL"

    def test_replace_clears_prior_rows_for_same_date(self, db: MarketDB):
        from data.schema import UniverseSnapshotRow

        d = date(2026, 5, 2)
        db.replace_universe_snapshots(
            d,
            [
                UniverseSnapshotRow(
                    selection_date=d,
                    bucket="merval",
                    symbol="X",
                    rank=1,
                    metric_value=1.0,
                    source="dynamic",
                    schema_version=1,
                )
            ],
        )
        db.replace_universe_snapshots(d, [])
        assert db.get_universe_snapshots_for_date(d) == []


class TestIolApiUsageMonth:
    """SQLite persistence for IOL call counters (monthly aggregation)."""

    def test_increment_creates_row_and_accumulates(self, db: MarketDB):
        db.increment_iol_api_usage(
            "2026-05",
            token=1,
            refresh=0,
            history=2,
            universe_volume=3,
        )
        row = db.get_iol_api_usage_month("2026-05")
        assert row["token_count"] == 1
        assert row["history_count"] == 2
        assert row["universe_volume_count"] == 3

        db.increment_iol_api_usage("2026-05", history=1)
        row2 = db.get_iol_api_usage_month("2026-05")
        assert row2["token_count"] == 1
        assert row2["history_count"] == 3

    def test_noop_when_all_zero(self, db: MarketDB):
        db.increment_iol_api_usage("2026-04", token=0, refresh=0, history=0, universe_volume=0)
        assert db.get_iol_api_usage_month("2026-04") == {
            "token_count": 0,
            "refresh_count": 0,
            "history_count": 0,
            "universe_volume_count": 0,
        }
