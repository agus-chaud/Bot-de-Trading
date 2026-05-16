"""Behavior tests for MarketDB — SQLite storage and Supabase sync."""

from __future__ import annotations

import json
from datetime import date

import pytest

from core_sim.ledger import PortfolioLedger
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


def _fetch_log_rows(db: MarketDB) -> list[dict]:
    cursor = db._conn.execute(
        "SELECT symbol, venue, status, source, skip_reason, extra FROM fetch_log ORDER BY id"
    )
    return [dict(row) for row in cursor.fetchall()]


class TestFetchLog:
    def test_should_log_successful_fetch(self, db):
        db.log_fetch({"symbol": "SPY", "venue": "XNYS", "status": "ok", "source": "yfinance"})

    def test_should_log_skip_with_reason(self, db):
        db.log_fetch({
            "symbol": "GGAL", "venue": "XBUE",
            "status": "skip", "skip_reason": "outlier_price",
        })

    def test_should_roundtrip_provider_iol_only_and_rows_in_extra(self, db):
        extra_payload = {
            "provider": "iol",
            "iol_only": True,
            "attempts": 3,
            "start_date": "2024-03-04",
            "end_date": "2024-03-06",
            "rows": 0,
            "rows_by_source": {"iol": 0, "byma": 0},
        }
        db.log_fetch({
            "symbol": "GGAL",
            "venue": "XBUE",
            "status": "skip",
            "source": "iol",
            "skip_reason": "credentials_missing",
            "extra": json.dumps(extra_payload, sort_keys=True),
        })

        rows = _fetch_log_rows(db)
        assert len(rows) == 1
        row = rows[0]
        assert row["symbol"] == "GGAL"
        assert row["venue"] == "XBUE"
        assert row["status"] == "skip"
        assert row["source"] == "iol"
        assert row["skip_reason"] == "credentials_missing"
        stored_extra = json.loads(row["extra"])
        assert stored_extra["provider"] == "iol"
        assert stored_extra["iol_only"] is True
        assert stored_extra["rows"] == 0

    def test_should_persist_mixed_source_attribution_in_extra(self, db):
        extra_payload = {
            "provider": "iol",
            "attempts": 5,
            "start_date": "2024-03-04",
            "end_date": "2024-03-06",
            "rows": 3,
            "effective_source": "mixed",
            "partial_fallback": True,
            "rows_by_source": {"iol": 1, "byma": 2},
        }
        db.log_fetch({
            "symbol": "GGAL",
            "venue": "XBUE",
            "status": "ok",
            "source": "mixed",
            "skip_reason": "fallback_used",
            "extra": json.dumps(extra_payload, sort_keys=True),
        })

        row = _fetch_log_rows(db)[0]
        stored_extra = json.loads(row["extra"])
        assert row["status"] == "ok"
        assert row["skip_reason"] == "fallback_used"
        assert stored_extra["effective_source"] == "mixed"
        assert stored_extra["rows_by_source"] == {"iol": 1, "byma": 2}


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


# ---------------------------------------------------------------------------
# Helpers for paper persistence tests
# ---------------------------------------------------------------------------

def _fill(
    symbol: str = "SPY",
    market: str = "US",
    side: str = "BUY",
    qty: float = 10.0,
    price: float = 100.0,
    bucket: str = "short",
    fee: float = 0.5,
    slippage: float = 0.42,
) -> dict:
    return {
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": price,
        "market": market,
        "bucket": bucket,
        "fee": fee,
        "cost_breakdown": {
            "slippage": slippage,
            "commission": fee - slippage,
            "spread": 0.0,
            "total": fee,
            "notional": qty * price,
            "market": market,
        },
    }


def _snapshot(
    equity_total: float = 1000.0,
    equity_short: float = 200.0,
    equity_long: float = 800.0,
    cash: float = 500.0,
) -> dict:
    return {
        "equity_total": equity_total,
        "equity_short": equity_short,
        "equity_long": equity_long,
        "cash": cash,
        "realized_pnl_total": 0.0,
        "unrealized_pnl_total": 0.0,
        "costs_day": 1.0,
        "mv_us": 400.0,
        "mv_ar": 100.0,
        "positions": {},
        "short_bucket": {
            "monthly_peak": 200.0,
            "monthly_drawdown": -0.01,
            "daily_return": 0.005,
        },
    }


# ---------------------------------------------------------------------------
# REQ-1 / REQ-2: Table creation
# ---------------------------------------------------------------------------

class TestPaperTablesCreation:
    def test_paper_fills_table_created_on_fresh_db(self, db):
        cursor = db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='paper_fills'"
        )
        assert cursor.fetchone() is not None

    def test_paper_snapshots_table_created_on_fresh_db(self, db):
        cursor = db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='paper_snapshots'"
        )
        assert cursor.fetchone() is not None

    def test_tables_are_created_idempotently_on_existing_db(self, tmp_path):
        path = str(tmp_path / "idempotent.db")
        db1 = MarketDB(path)
        db1._conn.close()
        db2 = MarketDB(path)
        cursor = db2._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='paper_fills'"
        )
        assert cursor.fetchone() is not None


# ---------------------------------------------------------------------------
# REQ-3: persist_fills
# ---------------------------------------------------------------------------

class TestPersistFills:
    def test_persists_two_fills_with_correct_venue_mapping(self, db):
        fills = [_fill(symbol="SPY", market="US"), _fill(symbol="GGAL", market="AR")]
        db.persist_fills("run-1", "paper_live", date(2026, 1, 2), fills)
        cursor = db._conn.execute("SELECT symbol, venue FROM paper_fills ORDER BY symbol")
        rows = cursor.fetchall()
        assert len(rows) == 2
        by_sym = {r["symbol"]: r["venue"] for r in rows}
        assert by_sym["SPY"] == "XNYS"
        assert by_sym["GGAL"] == "XBUE"

    def test_us_market_maps_to_xnys(self, db):
        db.persist_fills("run-1", "paper_live", date(2026, 1, 2), [_fill(market="US")])
        cursor = db._conn.execute("SELECT venue FROM paper_fills")
        assert cursor.fetchone()["venue"] == "XNYS"

    def test_ar_market_maps_to_xbue(self, db):
        db.persist_fills("run-1", "paper_live", date(2026, 1, 2), [_fill(market="AR")])
        cursor = db._conn.execute("SELECT venue FROM paper_fills")
        assert cursor.fetchone()["venue"] == "XBUE"

    def test_slippage_extracted_from_cost_breakdown(self, db):
        db.persist_fills("run-1", "paper_live", date(2026, 1, 2), [_fill(slippage=0.42)])
        cursor = db._conn.execute("SELECT slippage FROM paper_fills")
        assert cursor.fetchone()["slippage"] == pytest.approx(0.42)

    def test_empty_fills_list_inserts_nothing(self, db):
        db.persist_fills("run-1", "paper_live", date(2026, 1, 2), [])
        cursor = db._conn.execute("SELECT COUNT(*) FROM paper_fills")
        assert cursor.fetchone()[0] == 0


# ---------------------------------------------------------------------------
# REQ-4: persist_snapshot
# ---------------------------------------------------------------------------

class TestPersistSnapshot:
    _D = date(2026, 1, 2)

    def test_first_snapshot_inserts_one_row(self, db):
        db.persist_snapshot("paper_live", self._D, _snapshot(), short_cash=100.0)
        cursor = db._conn.execute("SELECT COUNT(*) FROM paper_snapshots")
        assert cursor.fetchone()[0] == 1

    def test_second_call_same_day_replaces_not_duplicates(self, db):
        db.persist_snapshot("paper_live", self._D, _snapshot(equity_total=1000.0), short_cash=100.0)
        db.persist_snapshot("paper_live", self._D, _snapshot(equity_total=1050.0), short_cash=120.0)
        cursor = db._conn.execute("SELECT COUNT(*), equity_total FROM paper_snapshots")
        row = cursor.fetchone()
        assert row[0] == 1
        assert row["equity_total"] == pytest.approx(1050.0)


# ---------------------------------------------------------------------------
# REQ-5: get_paper_fills
# ---------------------------------------------------------------------------

class TestGetPaperFills:
    def test_returns_fills_ordered_by_trading_day_then_id(self, db):
        db.persist_fills("run-1", "paper_live", date(2026, 1, 3), [_fill(symbol="B")])
        db.persist_fills("run-1", "paper_live", date(2026, 1, 2), [_fill(symbol="A")])
        rows = db.get_paper_fills(mode="paper_live")
        assert [r["symbol"] for r in rows] == ["A", "B"]

    def test_returns_empty_list_when_no_fills(self, db):
        assert db.get_paper_fills(mode="paper_live") == []

    def test_filters_by_mode(self, db):
        db.persist_fills("run-1", "paper_live", date(2026, 1, 2), [_fill(symbol="SPY")])
        db.persist_fills("run-2", "backtest", date(2026, 1, 2), [_fill(symbol="QQQ")])
        rows = db.get_paper_fills(mode="paper_live")
        assert all(r["mode"] == "paper_live" for r in rows)
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# REQ-6: replay_ledger_from_fills
# ---------------------------------------------------------------------------

class TestReplayLedgerFromFills:
    def test_replay_produces_same_state_as_live_run(self, db):
        live_ledger = PortfolioLedger(starting_cash=1000.0)
        fills_d1 = [
            {"symbol": "SPY", "side": "BUY", "qty": 5.0, "price": 100.0,
             "market": "US", "bucket": "short", "fee": 0.5},
        ]
        fills_d2 = [
            {"symbol": "SPY", "side": "SELL", "qty": 5.0, "price": 105.0,
             "market": "US", "bucket": "short", "fee": 0.5},
        ]
        live_ledger.apply_fills(date(2026, 1, 2), fills_d1)
        live_ledger.apply_fills(date(2026, 1, 3), fills_d2)

        db_fills_d1 = [_fill(symbol="SPY", market="US", side="BUY", qty=5.0,
                             price=100.0, fee=0.5, slippage=0.0)]
        db_fills_d2 = [_fill(symbol="SPY", market="US", side="SELL", qty=5.0,
                             price=105.0, bucket="short", fee=0.5, slippage=0.0)]
        db.persist_fills("run-1", "paper_live", date(2026, 1, 2), db_fills_d1)
        db.persist_fills("run-1", "paper_live", date(2026, 1, 3), db_fills_d2)

        replayed = db.replay_ledger_from_fills(mode="paper_live", starting_cash=1000.0)

        assert replayed.cash == pytest.approx(live_ledger.cash)
        assert replayed.realized_pnl_total == pytest.approx(live_ledger.realized_pnl_total)
        assert set(replayed.positions.keys()) == set(live_ledger.positions.keys())

    def test_empty_fills_returns_fresh_ledger(self, db):
        ledger = db.replay_ledger_from_fills(mode="paper_live", starting_cash=1000.0)
        assert ledger.cash == pytest.approx(1000.0)
        assert ledger.positions == {}


# ---------------------------------------------------------------------------
# REQ-7: long_term_monthly_runner db param
# ---------------------------------------------------------------------------

class TestLongTermRunnerDbParam:
    def test_create_backtester_without_db_param_still_works(self, tmp_path):
        from pathlib import Path
        from unittest.mock import MagicMock
        import yaml
        from core_sim.long_term_monthly_runner import create_long_term_monthly_backtester
        from core_sim.ledger import PortfolioLedger
        from core_sim.paper_broker_sim import PaperBrokerSim
        from core_sim.cost_model import CostModel, MarketCostConfig, SlippageMode

        repo_root = Path(__file__).resolve().parents[1]
        with (repo_root / "config" / "policy.v1.yaml").open(encoding="utf-8") as f:
            policy_doc = yaml.safe_load(f)

        ledger = PortfolioLedger(starting_cash=200_000.0)
        cost_model = CostModel(market_configs={
            "US": MarketCostConfig(
                commission_bps_per_side=1.0,
                slippage_bps=2.0,
                slippage_mode=SlippageMode.FIXED_BPS,
            )
        })
        broker = PaperBrokerSim(ledger=ledger, cost_model=cost_model)
        backtester = create_long_term_monthly_backtester(policy_doc, repo_root, ledger, broker)
        assert backtester is not None
