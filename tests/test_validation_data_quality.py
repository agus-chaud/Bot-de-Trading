"""Behavior tests for the data_quality validation stage."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from data.schema import OHLCVRow
from validation.stages.data_quality import run_data_quality_stage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ohlcv(symbol: str, ts: date, venue: str = "XNYS", imputed: bool = False) -> OHLCVRow:
    return OHLCVRow(
        symbol=symbol,
        ts=ts,
        open=100.0,
        high=105.0,
        low=98.0,
        close=102.0,
        volume=1_000_000.0,
        currency="USD",
        venue=venue,
        imputed=imputed,
    )


def _make_policy(symbols_us: list[str], symbols_ar: list[str] | None = None) -> dict:
    """Build a minimal policy_doc that bypasses YAML file loading via inline lists."""
    return {
        "schema_version": 1,
        "symbols": {
            "whitelist_us_file": "",
            "whitelist_ar_file": "",
            "inline_us": symbols_us,
            "inline_ar": symbols_ar or [],
        },
        "validation_wf": {
            "lookback_trading_days": 90,
        },
    }


def _mock_db(rows_by_symbol: dict[str, list[OHLCVRow]]) -> MagicMock:
    """Return a MarketDB mock whose get_ohlcv dispatches by symbol."""
    db = MagicMock()

    def _get_ohlcv(symbol: str, start: date, end: date, venue: str) -> list[OHLCVRow]:
        return rows_by_symbol.get(symbol, [])

    db.get_ohlcv.side_effect = _get_ohlcv
    return db


# ---------------------------------------------------------------------------
# Trading days fixture
# ---------------------------------------------------------------------------

_TRADING_DAYS = [
    date(2024, 1, 2),
    date(2024, 1, 3),
    date(2024, 1, 4),
    date(2024, 1, 5),
    date(2024, 1, 8),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDataQualityStageCleanData:
    """Complete data, no gaps, no imputed bars → passed=True, violations=[]."""

    def test_passed_is_always_true(self):
        rows = {
            "SPY": [_ohlcv("SPY", d) for d in _TRADING_DAYS],
            "AAPL": [_ohlcv("AAPL", d) for d in _TRADING_DAYS],
        }
        db = _mock_db(rows)
        policy = _make_policy(["SPY", "AAPL"])

        result = run_data_quality_stage(db, _TRADING_DAYS, policy)

        assert result.passed is True

    def test_no_violations_when_clean(self):
        rows = {
            "SPY": [_ohlcv("SPY", d) for d in _TRADING_DAYS],
            "AAPL": [_ohlcv("AAPL", d) for d in _TRADING_DAYS],
        }
        db = _mock_db(rows)
        policy = _make_policy(["SPY", "AAPL"])

        result = run_data_quality_stage(db, _TRADING_DAYS, policy)

        assert result.violations == []

    def test_metrics_total_bars(self):
        rows = {
            "SPY": [_ohlcv("SPY", d) for d in _TRADING_DAYS],
            "AAPL": [_ohlcv("AAPL", d) for d in _TRADING_DAYS],
        }
        db = _mock_db(rows)
        policy = _make_policy(["SPY", "AAPL"])

        result = run_data_quality_stage(db, _TRADING_DAYS, policy)

        assert result.metrics["total_bars_checked"] == 10  # 2 symbols × 5 days
        assert result.metrics["imputed_bars"] == 0
        assert result.metrics["imputed_pct"] == 0.0
        assert result.metrics["symbols_with_gaps"] == []
        assert result.metrics["symbols_checked"] == 2
        assert result.metrics["trading_days_checked"] == 5

    def test_stage_name(self):
        db = _mock_db({"SPY": [_ohlcv("SPY", d) for d in _TRADING_DAYS]})
        result = run_data_quality_stage(db, _TRADING_DAYS, _make_policy(["SPY"]))
        assert result.stage == "data_quality"

    def test_skipped_is_false(self):
        db = _mock_db({"SPY": [_ohlcv("SPY", d) for d in _TRADING_DAYS]})
        result = run_data_quality_stage(db, _TRADING_DAYS, _make_policy(["SPY"]))
        assert result.skipped is False


class TestDataQualityStageHighImputed:
    """More than 5% imputed bars → passed=True but violation warning present."""

    def _build_rows_with_imputed_pct(self, symbol: str, pct: float) -> list[OHLCVRow]:
        """Build a bar list where `pct` fraction of bars are imputed."""
        rows = []
        n_imputed = round(len(_TRADING_DAYS) * pct)
        for i, d in enumerate(_TRADING_DAYS):
            rows.append(_ohlcv(symbol, d, imputed=(i < n_imputed)))
        return rows

    def test_passed_still_true_when_high_imputed(self):
        # 4 out of 5 bars imputed = 80%
        rows = {"SPY": [_ohlcv("SPY", d, imputed=(i >= 1)) for i, d in enumerate(_TRADING_DAYS)]}
        db = _mock_db(rows)
        policy = _make_policy(["SPY"])

        result = run_data_quality_stage(db, _TRADING_DAYS, policy)

        assert result.passed is True

    def test_violation_present_when_imputed_pct_exceeds_threshold(self):
        # 4 out of 5 bars imputed = 80%
        rows = {"SPY": [_ohlcv("SPY", d, imputed=(i >= 1)) for i, d in enumerate(_TRADING_DAYS)]}
        db = _mock_db(rows)
        policy = _make_policy(["SPY"])

        result = run_data_quality_stage(db, _TRADING_DAYS, policy)

        assert any("imputed_pct" in v and "exceeds 5%" in v for v in result.violations)

    def test_violation_contains_actual_percentage(self):
        # 4 out of 5 = 80%
        rows = {"SPY": [_ohlcv("SPY", d, imputed=(i >= 1)) for i, d in enumerate(_TRADING_DAYS)]}
        db = _mock_db(rows)
        policy = _make_policy(["SPY"])

        result = run_data_quality_stage(db, _TRADING_DAYS, policy)

        assert result.metrics["imputed_pct"] == 80.0

    def test_no_violation_when_imputed_pct_at_threshold(self):
        # Exactly 5% — 5 bars, 0 imputed (can't get exact 5% with 5 bars, use 0%)
        # Use 20 trading days to get exact 5% (1 out of 20)
        twenty_days = [
            date(2024, 1, 2 + i) for i in range(20)
        ]
        rows = {"SPY": [_ohlcv("SPY", d, imputed=(i == 0)) for i, d in enumerate(twenty_days)]}
        db = _mock_db(rows)
        policy = _make_policy(["SPY"])

        result = run_data_quality_stage(db, twenty_days, policy)

        # 1/20 = 5.0% — exactly at threshold, NOT exceeding it
        imputed_violation = [v for v in result.violations if "imputed_pct" in v]
        assert imputed_violation == []


class TestDataQualityStageGaps:
    """Symbols with calendar gaps → passed=True but violation lists the symbols."""

    def test_passed_still_true_with_gaps(self):
        # AAPL missing all days — gap for every trading day
        rows: dict[str, list[OHLCVRow]] = {"AAPL": []}
        db = _mock_db(rows)
        policy = _make_policy(["AAPL"])

        result = run_data_quality_stage(db, _TRADING_DAYS, policy)

        assert result.passed is True

    def test_violation_lists_symbol_with_gap(self):
        rows: dict[str, list[OHLCVRow]] = {"AAPL": []}
        db = _mock_db(rows)
        policy = _make_policy(["AAPL"])

        result = run_data_quality_stage(db, _TRADING_DAYS, policy)

        gap_violations = [v for v in result.violations if "symbols_with_gaps" in v]
        assert len(gap_violations) == 1
        assert "AAPL" in gap_violations[0]

    def test_violation_lists_multiple_symbols_with_gaps(self):
        # Both symbols missing data
        rows: dict[str, list[OHLCVRow]] = {"AAPL": [], "MSFT": []}
        db = _mock_db(rows)
        policy = _make_policy(["AAPL", "MSFT"])

        result = run_data_quality_stage(db, _TRADING_DAYS, policy)

        gap_violations = [v for v in result.violations if "symbols_with_gaps" in v]
        assert len(gap_violations) == 1
        assert "AAPL" in gap_violations[0]
        assert "MSFT" in gap_violations[0]

    def test_no_gap_violation_when_all_days_present(self):
        rows = {"SPY": [_ohlcv("SPY", d) for d in _TRADING_DAYS]}
        db = _mock_db(rows)
        policy = _make_policy(["SPY"])

        result = run_data_quality_stage(db, _TRADING_DAYS, policy)

        gap_violations = [v for v in result.violations if "symbols_with_gaps" in v]
        assert gap_violations == []

    def test_metrics_symbols_with_gaps_populated(self):
        rows: dict[str, list[OHLCVRow]] = {"AAPL": [], "SPY": [_ohlcv("SPY", d) for d in _TRADING_DAYS]}
        db = _mock_db(rows)
        policy = _make_policy(["AAPL", "SPY"])

        result = run_data_quality_stage(db, _TRADING_DAYS, policy)

        assert "AAPL" in result.metrics["symbols_with_gaps"]
        assert "SPY" not in result.metrics["symbols_with_gaps"]

    def test_partial_gap_detected(self):
        # AAPL has data for only 3 of 5 days — 2 days missing
        partial_rows = [_ohlcv("AAPL", d) for d in _TRADING_DAYS[:3]]
        db = _mock_db({"AAPL": partial_rows})
        policy = _make_policy(["AAPL"])

        result = run_data_quality_stage(db, _TRADING_DAYS, policy)

        assert "AAPL" in result.metrics["symbols_with_gaps"]


class TestDataQualityStageEdgeCases:
    """Edge cases: empty trading_days, empty symbol list, AR symbols."""

    def test_empty_trading_days_returns_clean_result(self):
        db = _mock_db({})
        policy = _make_policy(["SPY"])

        result = run_data_quality_stage(db, [], policy)

        assert result.passed is True
        assert result.violations == []
        assert result.metrics["trading_days_checked"] == 0
        assert result.metrics["total_bars_checked"] == 0

    def test_empty_symbol_list(self):
        db = _mock_db({})
        policy = _make_policy([])

        result = run_data_quality_stage(db, _TRADING_DAYS, policy)

        assert result.passed is True
        assert result.violations == []
        assert result.metrics["symbols_checked"] == 0

    def test_ar_symbols_use_byma_venue(self):
        """AR symbols should be queried with BYMA venue."""
        db = MagicMock()
        db.get_ohlcv.return_value = [_ohlcv("GGAL", d, venue="BYMA") for d in _TRADING_DAYS]
        policy = _make_policy([], ["GGAL"])

        run_data_quality_stage(db, _TRADING_DAYS, policy)

        # All calls should use BYMA venue
        for call in db.get_ohlcv.call_args_list:
            _, kwargs = call
            # call_args can be positional — check positional arg at index 3 (venue)
            args, _ = call
            assert args[3] == "BYMA"

    def test_both_us_and_ar_symbols(self):
        rows = {
            "SPY": [_ohlcv("SPY", d) for d in _TRADING_DAYS],
            "GGAL": [_ohlcv("GGAL", d, venue="BYMA") for d in _TRADING_DAYS],
        }
        db = _mock_db(rows)
        policy = _make_policy(["SPY"], ["GGAL"])

        result = run_data_quality_stage(db, _TRADING_DAYS, policy)

        assert result.passed is True
        assert result.metrics["symbols_checked"] == 2
        assert result.metrics["total_bars_checked"] == 10
