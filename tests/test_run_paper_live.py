"""Tests for scripts/run_paper_live.py — behavior-focused, smart-testing aligned.

Priority: business logic (gap detection, F3 policy) → integration (full pipeline run).
Uses real MarketDB(:memory:), real PortfolioLedger, real PaperBrokerSim — no mocking core_sim.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.schema import OHLCVRow
from data.storage import MarketDB
from scripts.run_paper_live import (
    _build_long_pipeline_context,
    _overlay_ar_long_sleeve_bars_from_db,
    compute_trading_days_gap,
    main,
    run_catch_up,
)

import yaml


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db():
    """Real MarketDB backed by SQLite in-memory."""
    return MarketDB(":memory:")


@pytest.fixture
def policy_doc():
    """Load actual policy for integration tests."""
    policy_path = REPO_ROOT / "config" / "policy.v1.yaml"
    with policy_path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _seed_ohlcv(db: MarketDB, symbols: list[str], days: list[date], venue: str = "XNYS"):
    """Insert synthetic OHLCV bars for each symbol on each day."""
    rows = []
    for i, day in enumerate(days):
        for sym in symbols:
            rows.append(OHLCVRow(
                symbol=sym,
                ts=day,
                open=100.0 + i * 0.5,
                high=101.0 + i * 0.5,
                low=99.0 + i * 0.5,
                close=100.0 + i * 0.5,
                volume=1_000_000.0,
                currency="USD",
                venue=venue,
                imputed=False,
            ))
    db.upsert_ohlcv(rows)


def _weekdays_from(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# ===========================================================================
# 3.1 — Gap detection (pure function)
# ===========================================================================


class TestComputeTradingDaysGap:
    """compute_trading_days_gap returns correct weekday lists."""

    def test_gap_zero_when_already_at_target(self):
        """If last_day == target_day, gap is empty."""
        target = date(2026, 5, 9)  # Friday
        result = compute_trading_days_gap(target, target)
        assert result == []

    def test_gap_two_days_within_week(self):
        """Wednesday→Friday = 2 days (Thu, Fri)."""
        last = date(2026, 5, 6)   # Wednesday
        target = date(2026, 5, 8)  # Friday
        result = compute_trading_days_gap(last, target)
        assert result == [date(2026, 5, 7), date(2026, 5, 8)]

    def test_gap_five_days_across_weekend(self):
        """Wednesday→next Wednesday = 5 weekdays (skips Sat/Sun)."""
        last = date(2026, 5, 6)    # Wednesday
        target = date(2026, 5, 13)  # next Wednesday
        result = compute_trading_days_gap(last, target)
        expected = [
            date(2026, 5, 7),   # Thu
            date(2026, 5, 8),   # Fri
            date(2026, 5, 11),  # Mon
            date(2026, 5, 12),  # Tue
            date(2026, 5, 13),  # Wed
        ]
        assert result == expected

    def test_first_run_returns_target_only(self):
        """If last_day is None (first run), gap = [target_day]."""
        target = date(2026, 5, 9)
        result = compute_trading_days_gap(None, target)
        assert result == [target]

    def test_weekend_skipped(self):
        """Friday→Monday = 1 weekday (Monday only)."""
        last = date(2026, 5, 8)   # Friday
        target = date(2026, 5, 11)  # Monday
        result = compute_trading_days_gap(last, target)
        assert result == [date(2026, 5, 11)]


# ===========================================================================
# 3.2 — F3 exit code
# ===========================================================================


class TestF3ExitCode:
    """F3 policy gate: gap > 3 trading days → sys.exit(2)."""

    def test_gap_exceeds_f3_returns_exit_2(self, db: MarketDB, tmp_path: Path):
        """main() returns 2 when gap > 3 and logs F3 violation."""
        db_path = tmp_path / "test.db"
        real_db = MarketDB(str(db_path))

        last = date(2026, 5, 2)
        snap = {
            "equity_total": 1000.0, "equity_short": 300.0,
            "equity_long": 700.0, "cash": 500.0,
            "realized_pnl_total": 0.0, "unrealized_pnl_total": 0.0,
            "costs_day": 0.0, "mv_us": 800.0, "mv_ar": 200.0,
        }
        real_db.persist_snapshot("paper_live", last, snap, short_cash=150.0)

        target = date(2026, 5, 9)  # 5 weekdays gap

        test_args = [
            "run_paper_live.py",
            "--date", target.isoformat(),
            "--db", str(db_path),
            "--policy", str(REPO_ROOT / "config" / "policy.v1.yaml"),
        ]
        with patch.object(sys, "argv", test_args):
            result = main()

        assert result == 2

    def test_gap_within_f3_does_not_exit_2(self, tmp_path: Path):
        """main() does NOT return 2 when gap <= 3."""
        db_path = tmp_path / "test.db"
        real_db = MarketDB(str(db_path))

        target = date(2026, 5, 8)
        last = date(2026, 5, 7)
        snap = {
            "equity_total": 1000.0, "equity_short": 300.0,
            "equity_long": 700.0, "cash": 500.0,
            "realized_pnl_total": 0.0, "unrealized_pnl_total": 0.0,
            "costs_day": 0.0, "mv_us": 800.0, "mv_ar": 200.0,
        }
        real_db.persist_snapshot("paper_live", last, snap, short_cash=150.0)

        symbols = ["SPY", "QQQ"]
        days = _weekdays_from(date(2026, 3, 1), 80)
        _seed_ohlcv(real_db, symbols, days)

        test_args = [
            "run_paper_live.py",
            "--date", target.isoformat(),
            "--db", str(db_path),
            "--policy", str(REPO_ROOT / "config" / "policy.v1.yaml"),
        ]
        with patch.object(sys, "argv", test_args):
            result = main()

        assert result != 2


# ===========================================================================
# 3.3 — Integration: single-day run
# ===========================================================================


class TestSingleDayRun:
    """Single-day paper-live run persists fills and snapshot correctly."""

    def test_single_day_creates_snapshot_row(self, tmp_path: Path, policy_doc):
        """After a single-day run, paper_snapshots has one row for that day."""
        db_path = tmp_path / "test.db"
        db = MarketDB(str(db_path))

        target = date(2026, 4, 15)  # Tuesday
        symbols = ["SPY", "QQQ"]
        days = _weekdays_from(date(2026, 2, 1), 80)
        _seed_ohlcv(db, symbols, days)

        gap_days = [target]
        run_catch_up(db, gap_days, policy_doc, initial_cash=1000.0)

        assert db.get_last_snapshot_day("paper_live") == target

        cursor = db._conn.execute(
            "SELECT COUNT(*) AS cnt FROM paper_snapshots WHERE mode = 'paper_live'"
        )
        assert cursor.fetchone()["cnt"] == 1

    def test_single_day_snapshot_has_correct_mode(self, tmp_path: Path, policy_doc):
        """Snapshot is stored with mode='paper_live'."""
        db_path = tmp_path / "test.db"
        db = MarketDB(str(db_path))

        target = date(2026, 4, 15)
        symbols = ["SPY", "QQQ"]
        days = _weekdays_from(date(2026, 2, 1), 80)
        _seed_ohlcv(db, symbols, days)

        run_catch_up(db, [target], policy_doc, initial_cash=1000.0)

        cursor = db._conn.execute(
            "SELECT mode, trading_day FROM paper_snapshots"
        )
        row = cursor.fetchone()
        assert row["mode"] == "paper_live"
        assert row["trading_day"] == target.isoformat()


# ===========================================================================
# 3.4 — Integration: catch-up 2 days
# ===========================================================================


class TestCatchUpTwoDays:
    """Catch-up of 2 days produces 2 snapshots with correct replay."""

    def test_two_day_catchup_creates_two_snapshots(self, tmp_path: Path, policy_doc):
        """Running 2 gap days results in 2 snapshot rows."""
        db_path = tmp_path / "test.db"
        db = MarketDB(str(db_path))

        symbols = ["SPY", "QQQ"]
        days = _weekdays_from(date(2026, 2, 1), 80)
        _seed_ohlcv(db, symbols, days)

        gap_days = [date(2026, 4, 14), date(2026, 4, 15)]
        run_catch_up(db, gap_days, policy_doc, initial_cash=1000.0)

        cursor = db._conn.execute(
            "SELECT COUNT(*) AS cnt FROM paper_snapshots WHERE mode = 'paper_live'"
        )
        assert cursor.fetchone()["cnt"] == 2

        assert db.get_last_snapshot_day("paper_live") == date(2026, 4, 15)

    def test_second_day_replays_first_days_fills(self, tmp_path: Path, policy_doc):
        """Second day's ledger reflects fills from first day (replay correctness)."""
        db_path = tmp_path / "test.db"
        db = MarketDB(str(db_path))

        symbols = ["SPY", "QQQ"]
        days = _weekdays_from(date(2026, 2, 1), 80)
        _seed_ohlcv(db, symbols, days)

        gap_days = [date(2026, 4, 14), date(2026, 4, 15)]
        run_catch_up(db, gap_days, policy_doc, initial_cash=1000.0)

        fills = db.get_paper_fills("paper_live")
        trading_days_in_fills = sorted(set(f["trading_day"] for f in fills))

        snapshots = db._conn.execute(
            "SELECT trading_day, equity_total FROM paper_snapshots WHERE mode = 'paper_live' ORDER BY trading_day"
        ).fetchall()
        assert len(snapshots) == 2
        assert snapshots[0]["trading_day"] < snapshots[1]["trading_day"]


# ===========================================================================
# 3.5 — Idempotency
# ===========================================================================


class TestIdempotency:
    """Re-running the same day does not duplicate rows."""

    def test_rerun_does_not_duplicate_snapshot(self, tmp_path: Path, policy_doc):
        """Running the same day twice produces exactly 1 snapshot row."""
        db_path = tmp_path / "test.db"
        db = MarketDB(str(db_path))

        symbols = ["SPY", "QQQ"]
        days = _weekdays_from(date(2026, 2, 1), 80)
        _seed_ohlcv(db, symbols, days)

        target = date(2026, 4, 15)
        run_catch_up(db, [target], policy_doc, initial_cash=1000.0)
        run_catch_up(db, [target], policy_doc, initial_cash=1000.0)

        cursor = db._conn.execute(
            "SELECT COUNT(*) AS cnt FROM paper_snapshots WHERE mode = 'paper_live'"
        )
        assert cursor.fetchone()["cnt"] == 1

    def test_rerun_does_not_duplicate_fills(self, tmp_path: Path, policy_doc):
        """Running the same day twice does not add extra fill rows."""
        db_path = tmp_path / "test.db"
        db = MarketDB(str(db_path))

        symbols = ["SPY", "QQQ"]
        days = _weekdays_from(date(2026, 2, 1), 80)
        _seed_ohlcv(db, symbols, days)

        target = date(2026, 4, 15)
        run_catch_up(db, [target], policy_doc, initial_cash=1000.0)
        fills_count_1 = len(db.get_paper_fills("paper_live"))

        run_catch_up(db, [target], policy_doc, initial_cash=1000.0)
        fills_count_2 = len(db.get_paper_fills("paper_live"))

        assert fills_count_1 == fills_count_2


# ===========================================================================
# 3.6 — Long engine feature flag
# ===========================================================================


class TestLongEngineFeatureFlag:
    """enable_long_engine flag controls long sleeve execution."""

    def test_flag_off_produces_same_result_as_before(self, tmp_path: Path, policy_doc):
        """With enable_long_engine=False (default), short-only behavior is identical."""
        db_path = tmp_path / "test.db"
        db = MarketDB(str(db_path))

        symbols = ["SPY", "QQQ"]
        days = _weekdays_from(date(2026, 2, 1), 80)
        _seed_ohlcv(db, symbols, days)

        target = date(2026, 4, 15)
        run_catch_up(db, [target], policy_doc, initial_cash=1000.0, enable_long_engine=False)

        assert db.get_last_snapshot_day("paper_live") == target
        cursor = db._conn.execute(
            "SELECT COUNT(*) AS cnt FROM paper_snapshots WHERE mode = 'paper_live'"
        )
        assert cursor.fetchone()["cnt"] == 1

        fills = db.get_paper_fills("paper_live")
        for fill in fills:
            assert fill.get("bucket") == "short", (
                "With long engine off, only short fills expected"
            )

    def test_flag_on_runs_both_sleeves(self, tmp_path: Path, policy_doc):
        """With enable_long_engine=True, the pipeline processes both sleeves without error."""
        db_path = tmp_path / "test.db"
        db = MarketDB(str(db_path))

        symbols = ["SPY", "QQQ"]
        days = _weekdays_from(date(2026, 2, 1), 80)
        _seed_ohlcv(db, symbols, days)

        target = date(2026, 4, 15)
        run_catch_up(db, [target], policy_doc, initial_cash=1000.0, enable_long_engine=True)

        assert db.get_last_snapshot_day("paper_live") == target
        snap_row = db._conn.execute(
            "SELECT equity_total FROM paper_snapshots WHERE mode = 'paper_live'"
        ).fetchone()
        assert snap_row is not None
        assert float(snap_row["equity_total"]) > 0


# ===========================================================================
# 3.7 — AR long sleeve: overlay XBUE over merge-US CEDEAR tickers
# ===========================================================================


class TestArLongXBUEOverlay:
    """Long AR sleeve must price CEDEAR lines from XBUE even if merge etiqueta US."""

    def test_overlay_replaces_merge_close_with_xbue_for_spy(self, tmp_path: Path, policy_doc):
        """SPY aparece como US en el merge pero el largo opera CEDEAR — overlay usa XBUE."""
        lt = policy_doc.get("long_term_engine") or {}
        if not str(lt.get("rebalance_rule", "")).startswith("first_ar_business_day_of_"):
            pytest.skip("policy fixture is not AR-calendar long sleeve")

        db_path = tmp_path / "overlay.db"
        db = MarketDB(str(db_path))
        day = date(2026, 4, 15)

        def row(sym: str, close: float, venue: str, cur: str) -> OHLCVRow:
            return OHLCVRow(
                symbol=sym,
                ts=day,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=1_000_000.0,
                currency=cur,
                venue=venue,
                imputed=False,
            )

        db.upsert_ohlcv(
            [
                row("SPY", 100.0, "XNYS", "USD"),
                row("SPY", 777.0, "XBUE", "ARS"),
            ]
        )

        daily_bars = {
            "SPY": {
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "volume": 1_000_000.0,
            }
        }
        _overlay_ar_long_sleeve_bars_from_db(db, day, policy_doc, daily_bars)
        assert daily_bars["SPY"]["close"] == 777.0
