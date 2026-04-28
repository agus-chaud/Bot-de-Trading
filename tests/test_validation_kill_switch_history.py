"""Behavior tests for the kill_switch_history validation stage.

Tests verify:
- No activations when short bucket monthly DD stays above threshold.
- One activation when DD crosses threshold in a given month.
- skipped=True when trading_days is empty.
- skipped=True when DB has no OHLCV rows for the period.
- Activation dates contain correct ISO dates.
- worst_monthly_dd_short tracks the lowest DD seen.
- months_simulated counts distinct calendar months.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from data.storage import MarketDB
from validation.stages.kill_switch_history import run_kill_switch_history_stage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

POLICY_DOC: dict = {
    "short_kill_switch_monthly_dd": -0.08,
    "schema_version": 1,
}

REPO_ROOT = Path(__file__).resolve().parents[1]


def _make_db(tmp_path: Path) -> MarketDB:
    return MarketDB(str(tmp_path / "test_ks_history.db"))


def _insert_ohlcv(db: MarketDB, symbol: str, day: date, close: float) -> None:
    """Insert a single OHLCV bar directly."""
    with db._conn:
        db._conn.execute(
            """
            INSERT OR REPLACE INTO ohlcv
                (symbol, ts, open, high, low, close, volume, currency, venue, imputed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (symbol, day.isoformat(), close, close, close, close, 1_000_000.0, "USD", "XNYS", 0),
        )


# ---------------------------------------------------------------------------
# Test: empty trading_days → skipped
# ---------------------------------------------------------------------------

class TestEmptyTradingDays:
    def test_skipped_when_no_trading_days(self, tmp_path):
        db = _make_db(tmp_path)

        result = run_kill_switch_history_stage(
            db=db,
            trading_days=[],
            policy_doc=POLICY_DOC,
            repo_root=REPO_ROOT,
            starting_cash=100_000.0,
        )

        assert result.stage == "kill_switch_history"
        assert result.skipped is True
        assert result.passed is True
        assert result.violations == []
        assert result.metrics["kill_switch_activations"] == 0
        assert result.metrics["months_simulated"] == 0
        assert result.metrics["activation_dates"] == []
        assert result.metrics["kill_switch_threshold"] == pytest.approx(-0.08)


# ---------------------------------------------------------------------------
# Test: trading_days provided but DB has no OHLCV → skipped
# ---------------------------------------------------------------------------

class TestNoOHLCVData:
    def test_skipped_when_db_has_no_bars_for_period(self, tmp_path):
        db = _make_db(tmp_path)

        trading_days = [date(2025, 10, 1), date(2025, 10, 2), date(2025, 10, 3)]

        result = run_kill_switch_history_stage(
            db=db,
            trading_days=trading_days,
            policy_doc=POLICY_DOC,
            repo_root=REPO_ROOT,
            starting_cash=100_000.0,
        )

        assert result.skipped is True
        assert result.passed is True
        assert result.metrics["kill_switch_activations"] == 0


# ---------------------------------------------------------------------------
# Test: no activations when DD stays healthy
# ---------------------------------------------------------------------------

class TestNoActivations:
    def test_zero_activations_when_monthly_dd_never_crosses_threshold(self, tmp_path):
        """With no open positions, short equity = 0 and monthly_drawdown = 0.0 always."""
        db = _make_db(tmp_path)

        # Insert bars so the period is non-empty
        days = [
            date(2025, 10, 1),
            date(2025, 10, 2),
            date(2025, 10, 15),
            date(2025, 11, 3),
            date(2025, 11, 4),
        ]
        for d in days:
            _insert_ohlcv(db, "SPY", d, close=100.0)

        result = run_kill_switch_history_stage(
            db=db,
            trading_days=days,
            policy_doc=POLICY_DOC,
            repo_root=REPO_ROOT,
            starting_cash=100_000.0,
        )

        assert result.passed is True
        assert result.skipped is False
        assert result.violations == []
        assert result.metrics["kill_switch_activations"] == 0
        assert result.metrics["activation_dates"] == []
        assert result.metrics["months_simulated"] == 2
        # No positions → short equity = 0 → drawdown = 0.0
        assert result.metrics["worst_monthly_dd_short"] == pytest.approx(0.0)
        assert result.metrics["kill_switch_threshold"] == pytest.approx(-0.08)


# ---------------------------------------------------------------------------
# Test: threshold from policy_doc is respected
# ---------------------------------------------------------------------------

class TestThresholdFromPolicy:
    def test_kill_switch_threshold_comes_from_policy_doc(self, tmp_path):
        db = _make_db(tmp_path)
        days = [date(2025, 11, 5)]
        _insert_ohlcv(db, "SPY", days[0], close=200.0)

        custom_policy = dict(POLICY_DOC)
        custom_policy["short_kill_switch_monthly_dd"] = -0.12

        result = run_kill_switch_history_stage(
            db=db,
            trading_days=days,
            policy_doc=custom_policy,
            repo_root=REPO_ROOT,
            starting_cash=50_000.0,
        )

        assert result.metrics["kill_switch_threshold"] == pytest.approx(-0.12)


# ---------------------------------------------------------------------------
# Test: activation detected — simulated via monkeypatching the ledger
# ---------------------------------------------------------------------------

class TestActivationDetected:
    def test_one_activation_when_monthly_dd_crosses_threshold(self, tmp_path, monkeypatch):
        """Monkeypatch PortfolioLedger.mark_to_market to return a snapshot with
        monthly_drawdown below the threshold on a specific day.
        """
        from core_sim import ledger as ledger_module

        db = _make_db(tmp_path)

        activation_day = date(2025, 11, 15)
        healthy_day = date(2025, 11, 3)

        days = [healthy_day, activation_day]
        for d in days:
            _insert_ohlcv(db, "SPY", d, close=100.0)

        # Build a fake mark_to_market that crosses the threshold on activation_day
        original_mtm = ledger_module.PortfolioLedger.mark_to_market

        def _fake_mtm(self, trading_day: date, daily_bars: dict) -> dict:  # type: ignore[override]
            if trading_day == activation_day:
                return {
                    "trading_day": trading_day.isoformat(),
                    "cash": 100_000.0,
                    "positions": {},
                    "realized_pnl_total": 0.0,
                    "unrealized_pnl_total": 0.0,
                    "equity_total": 100_000.0,
                    "equity_curve_points": [],
                    "short_bucket": {
                        "equity": 0.0,
                        "monthly_peak": 10_000.0,
                        "monthly_drawdown": -0.09,  # below -0.08 threshold
                        "daily_return": -0.09,
                    },
                }
            return original_mtm(self, trading_day=trading_day, daily_bars=daily_bars)

        monkeypatch.setattr(ledger_module.PortfolioLedger, "mark_to_market", _fake_mtm)

        result = run_kill_switch_history_stage(
            db=db,
            trading_days=days,
            policy_doc=POLICY_DOC,
            repo_root=REPO_ROOT,
            starting_cash=100_000.0,
        )

        assert result.passed is True
        assert result.skipped is False
        assert result.metrics["kill_switch_activations"] == 1
        assert activation_day.isoformat() in result.metrics["activation_dates"]
        assert result.metrics["worst_monthly_dd_short"] == pytest.approx(-0.09)

    def test_same_month_counted_only_once(self, tmp_path, monkeypatch):
        """Two days in the same month both below threshold → only 1 activation."""
        from core_sim import ledger as ledger_module

        db = _make_db(tmp_path)

        day1 = date(2025, 11, 10)
        day2 = date(2025, 11, 20)

        for d in [day1, day2]:
            _insert_ohlcv(db, "SPY", d, close=100.0)

        def _fake_mtm(self, trading_day: date, daily_bars: dict) -> dict:  # type: ignore[override]
            return {
                "trading_day": trading_day.isoformat(),
                "cash": 100_000.0,
                "positions": {},
                "realized_pnl_total": 0.0,
                "unrealized_pnl_total": 0.0,
                "equity_total": 100_000.0,
                "equity_curve_points": [],
                "short_bucket": {
                    "equity": 0.0,
                    "monthly_peak": 10_000.0,
                    "monthly_drawdown": -0.10,
                    "daily_return": -0.10,
                },
            }

        monkeypatch.setattr(ledger_module.PortfolioLedger, "mark_to_market", _fake_mtm)

        result = run_kill_switch_history_stage(
            db=db,
            trading_days=[day1, day2],
            policy_doc=POLICY_DOC,
            repo_root=REPO_ROOT,
            starting_cash=100_000.0,
        )

        assert result.metrics["kill_switch_activations"] == 1
        assert len(result.metrics["activation_dates"]) == 1
        assert result.metrics["activation_dates"][0] == day1.isoformat()

    def test_two_different_months_each_crossing_threshold(self, tmp_path, monkeypatch):
        """One activation per month → 2 total when two months both cross the threshold."""
        from core_sim import ledger as ledger_module

        db = _make_db(tmp_path)

        oct_day = date(2025, 10, 15)
        nov_day = date(2025, 11, 15)

        for d in [oct_day, nov_day]:
            _insert_ohlcv(db, "SPY", d, close=100.0)

        def _fake_mtm(self, trading_day: date, daily_bars: dict) -> dict:  # type: ignore[override]
            return {
                "trading_day": trading_day.isoformat(),
                "cash": 100_000.0,
                "positions": {},
                "realized_pnl_total": 0.0,
                "unrealized_pnl_total": 0.0,
                "equity_total": 100_000.0,
                "equity_curve_points": [],
                "short_bucket": {
                    "equity": 0.0,
                    "monthly_peak": 10_000.0,
                    "monthly_drawdown": -0.09,
                    "daily_return": -0.09,
                },
            }

        monkeypatch.setattr(ledger_module.PortfolioLedger, "mark_to_market", _fake_mtm)

        result = run_kill_switch_history_stage(
            db=db,
            trading_days=[oct_day, nov_day],
            policy_doc=POLICY_DOC,
            repo_root=REPO_ROOT,
            starting_cash=100_000.0,
        )

        assert result.metrics["kill_switch_activations"] == 2
        assert oct_day.isoformat() in result.metrics["activation_dates"]
        assert nov_day.isoformat() in result.metrics["activation_dates"]
        assert result.metrics["months_simulated"] == 2


# ---------------------------------------------------------------------------
# Test: result shape invariants
# ---------------------------------------------------------------------------

class TestResultShape:
    def test_stage_name_is_kill_switch_history(self, tmp_path):
        db = _make_db(tmp_path)
        result = run_kill_switch_history_stage(
            db=db,
            trading_days=[],
            policy_doc=POLICY_DOC,
            repo_root=REPO_ROOT,
            starting_cash=100_000.0,
        )
        assert result.stage == "kill_switch_history"

    def test_violations_always_empty(self, tmp_path, monkeypatch):
        """Stage is always informational — violations must always be empty."""
        from core_sim import ledger as ledger_module

        db = _make_db(tmp_path)
        day = date(2025, 10, 1)
        _insert_ohlcv(db, "SPY", day, close=100.0)

        def _fake_mtm(self, trading_day: date, daily_bars: dict) -> dict:  # type: ignore[override]
            return {
                "trading_day": trading_day.isoformat(),
                "cash": 100_000.0,
                "positions": {},
                "realized_pnl_total": 0.0,
                "unrealized_pnl_total": 0.0,
                "equity_total": 100_000.0,
                "equity_curve_points": [],
                "short_bucket": {
                    "equity": 0.0,
                    "monthly_peak": 10_000.0,
                    "monthly_drawdown": -0.99,  # extreme DD
                    "daily_return": -0.99,
                },
            }

        monkeypatch.setattr(ledger_module.PortfolioLedger, "mark_to_market", _fake_mtm)

        result = run_kill_switch_history_stage(
            db=db,
            trading_days=[day],
            policy_doc=POLICY_DOC,
            repo_root=REPO_ROOT,
            starting_cash=100_000.0,
        )

        assert result.passed is True
        assert result.violations == []

    def test_all_required_metric_keys_present(self, tmp_path):
        db = _make_db(tmp_path)
        day = date(2025, 10, 1)
        _insert_ohlcv(db, "SPY", day, close=100.0)

        result = run_kill_switch_history_stage(
            db=db,
            trading_days=[day],
            policy_doc=POLICY_DOC,
            repo_root=REPO_ROOT,
            starting_cash=100_000.0,
        )

        required_keys = {
            "kill_switch_activations",
            "months_simulated",
            "worst_monthly_dd_short",
            "kill_switch_threshold",
            "activation_dates",
        }
        assert required_keys.issubset(set(result.metrics.keys()))
