"""Behavior tests for data/universe_selector.py (ranking + merge; mocked IOL fetches)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from data.iol_api_meter import IolJobBudgetExhausted
from data.schema import OHLCVRow, UniverseSnapshotRow
from data.universe_selector import (
    DynamicUniverseResult,
    merge_fetch_universe,
    metrics_from_bars,
    resolve_ar_universe_for_short_pipeline,
    select_dynamic_universe,
    static_ar_symbols_from_policy,
    window_start_for_volume,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _bar(ts: date, close: float, volume: float, sym: str = "GGAL") -> OHLCVRow:
    return OHLCVRow(
        symbol=sym,
        ts=ts,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        currency="ARS",
        venue="AR",
        imputed=False,
    )


class TestMetricsFromBars:
    def test_should_sum_last_window_volume(self):
        base = date(2024, 1, 1)
        rows = [_bar(base + timedelta(days=i), 10.0, float(i + 1)) for i in range(24)]
        total, _ = metrics_from_bars(rows, 20)
        expected = sum(range(5, 25))
        assert total == pytest.approx(float(expected))


class TestStaticArSymbolsFromPolicy:
    def test_should_include_merval_and_cedear_candidate_pools(self):
        with (_REPO_ROOT / "config" / "policy.v1.yaml").open(encoding="utf-8") as f:
            import yaml

            policy = yaml.safe_load(f)
        symbols = static_ar_symbols_from_policy(_REPO_ROOT, policy)
        assert "GGAL" in symbols
        assert "PAMP" in symbols
        assert "SPY" in symbols
        assert "NVDA" in symbols


class TestMergeFetchUniverse:
    def test_should_union_sort_dedup_with_holdings(self):
        out = merge_fetch_universe(
            ["bbAR", "AAAR"],
            ["ZZD"],
            open_holdings_ar=["aaar", " BBAR "],
        )
        assert out == ["AAAR", "BBAR", "ZZD"]


class TestSelectDynamicUniverse:
    def test_should_return_empty_when_disabled(self):
        policy = {
            "schema_version": 1,
            "symbols": {
                "whitelist_ar_file": "config/symbols/whitelist_ar.yaml",
                "whitelist_cedear_file": "config/symbols/whitelist_cedear.yaml",
                "universe_selection": {
                    "enabled": False,
                    "rebalance_frequency": "weekly",
                    "targets": {"merval_top_n": 2, "cedears_top_n": 2},
                    "volume_window_trading_days": 5,
                    "tiebreakers": ["avg_notional_desc", "symbol_asc"],
                    "api_budget": {
                        "monthly_limit": 25000,
                        "soft_limit_pct": 0.8,
                        "max_calls_per_job": 2000,
                    },
                },
            },
        }
        repo = Path(__file__).resolve().parents[1]
        sel_day = date(2024, 4, 1)
        out = select_dynamic_universe(
            policy,
            repo,
            selection_date=sel_day,
            as_of_date=sel_day,
            fetch_fn=lambda *a, **k: None,
        )
        assert out == DynamicUniverseResult([], [], [], [])

    def test_should_rank_by_total_volume_desc_then_symbol(self):
        policy = {
            "schema_version": 1,
            "symbols": {
                "whitelist_ar_file": "config/symbols/whitelist_ar.yaml",
                "whitelist_cedear_file": "config/symbols/whitelist_cedear.yaml",
                "universe_selection": {
                    "enabled": True,
                    "rebalance_frequency": "weekly",
                    "targets": {"merval_top_n": 2, "cedears_top_n": 1},
                    "volume_window_trading_days": 3,
                    "tiebreakers": ["avg_notional_desc", "symbol_asc"],
                    "api_budget": {
                        "monthly_limit": 25000,
                        "soft_limit_pct": 0.8,
                        "max_calls_per_job": 2000,
                    },
                },
            },
        }
        repo = Path(__file__).resolve().parents[1]
        start = window_start_for_volume(date(2024, 6, 10), 3)

        def fetch_fn(symbol: str, s: date, e: date, timeout: int, iol_only: bool = False):
            assert iol_only is True
            if symbol == "GGAL":
                return [
                    _bar(start, 1.0, 100.0, "GGAL"),
                    _bar(start + timedelta(days=1), 1.0, 100.0, "GGAL"),
                    _bar(start + timedelta(days=2), 1.0, 900.0, "GGAL"),
                ]
            if symbol == "YPF":
                return [
                    _bar(start, 1.0, 500.0, "YPF"),
                    _bar(start + timedelta(days=1), 1.0, 500.0, "YPF"),
                    _bar(start + timedelta(days=2), 1.0, 500.0, "YPF"),
                ]
            if symbol == "MELI":
                return [
                    _bar(start, 1.0, 300.0, "MELI"),
                    _bar(start + timedelta(days=1), 1.0, 300.0, "MELI"),
                    _bar(start + timedelta(days=2), 1.0, 300.0, "MELI"),
                ]
            return []

        sel_day = date(2024, 6, 10)
        out = select_dynamic_universe(
            policy,
            repo,
            selection_date=sel_day,
            as_of_date=sel_day,
            fetch_fn=fetch_fn,
        )
        assert out.merval_symbols[:2] == ["YPF", "GGAL"]
        assert out.cedear_symbols[0] == "AAPL"
        ggal_rows = [r for r in out.snapshot_rows if r.symbol == "GGAL" and r.bucket == "merval"]
        assert len(ggal_rows) == 1
        assert ggal_rows[0].rank == 2
        assert ggal_rows[0].metric_value == pytest.approx(1100.0)

    def test_should_break_tie_with_avg_notional_then_symbol_asc(self, tmp_path: Path):
        """Same total volume → higher avg(close×vol) wins; if still tied → symbol ascending."""
        sym_dir = tmp_path / "config" / "symbols"
        sym_dir.mkdir(parents=True)
        (sym_dir / "whitelist_ar.yaml").write_text(
            "stocks:\n  - BBB\n  - AAA\n", encoding="utf-8"
        )
        (sym_dir / "whitelist_cedear.yaml").write_text("stocks:\n  - ZZ\n", encoding="utf-8")
        policy = {
            "schema_version": 1,
            "symbols": {
                "whitelist_ar_file": "config/symbols/whitelist_ar.yaml",
                "whitelist_cedear_file": "config/symbols/whitelist_cedear.yaml",
                "universe_selection": {
                    "enabled": True,
                    "rebalance_frequency": "weekly",
                    "targets": {"merval_top_n": 2, "cedears_top_n": 1},
                    "volume_window_trading_days": 3,
                    "tiebreakers": ["avg_notional_desc", "symbol_asc"],
                    "api_budget": {
                        "monthly_limit": 25000,
                        "soft_limit_pct": 0.8,
                        "max_calls_per_job": 2000,
                    },
                },
            },
        }
        sel_day = date(2024, 8, 1)
        start = window_start_for_volume(sel_day, 3)

        def fetch_fn(symbol: str, s: date, e: date, timeout: int, iol_only: bool = False):
            if symbol == "AAA":
                return [
                    _bar(start, 10.0, 100.0, "AAA"),
                    _bar(start + timedelta(days=1), 10.0, 100.0, "AAA"),
                    _bar(start + timedelta(days=2), 10.0, 100.0, "AAA"),
                ]
            if symbol == "BBB":
                return [
                    _bar(start, 1.0, 100.0, "BBB"),
                    _bar(start + timedelta(days=1), 1.0, 100.0, "BBB"),
                    _bar(start + timedelta(days=2), 1.0, 100.0, "BBB"),
                ]
            if symbol == "ZZ":
                return [
                    _bar(start, 1.0, 50.0, "ZZ"),
                    _bar(start + timedelta(days=1), 1.0, 50.0, "ZZ"),
                    _bar(start + timedelta(days=2), 1.0, 50.0, "ZZ"),
                ]
            return []

        out = select_dynamic_universe(
            policy,
            tmp_path,
            selection_date=sel_day,
            as_of_date=sel_day,
            fetch_fn=fetch_fn,
        )
        assert out.merval_symbols[:2] == ["AAA", "BBB"]
        assert out.cedear_symbols == ["ZZ"]

    def test_should_rank_symbol_asc_when_volume_and_notional_tie(self, tmp_path: Path):
        sym_dir = tmp_path / "config" / "symbols"
        sym_dir.mkdir(parents=True)
        (sym_dir / "whitelist_ar.yaml").write_text(
            "stocks:\n  - ZEBRA\n  - ADOG\n", encoding="utf-8"
        )
        (sym_dir / "whitelist_cedear.yaml").write_text("stocks:\n  - ZZ\n", encoding="utf-8")
        policy = {
            "schema_version": 1,
            "symbols": {
                "whitelist_ar_file": "config/symbols/whitelist_ar.yaml",
                "whitelist_cedear_file": "config/symbols/whitelist_cedear.yaml",
                "universe_selection": {
                    "enabled": True,
                    "rebalance_frequency": "weekly",
                    "targets": {"merval_top_n": 2, "cedears_top_n": 1},
                    "volume_window_trading_days": 2,
                    "tiebreakers": ["avg_notional_desc", "symbol_asc"],
                    "api_budget": {
                        "monthly_limit": 25000,
                        "soft_limit_pct": 0.8,
                        "max_calls_per_job": 2000,
                    },
                },
            },
        }
        sel_day = date(2024, 9, 2)
        start = window_start_for_volume(sel_day, 2)

        def fetch_fn(symbol: str, s: date, e: date, timeout: int, iol_only: bool = False):
            if symbol in ("ADOG", "ZEBRA"):
                return [
                    _bar(start, 5.0, 200.0, symbol),
                    _bar(start + timedelta(days=1), 5.0, 200.0, symbol),
                ]
            if symbol == "ZZ":
                return [
                    _bar(start, 1.0, 10.0, "ZZ"),
                    _bar(start + timedelta(days=1), 1.0, 10.0, "ZZ"),
                ]
            return []

        out = select_dynamic_universe(
            policy,
            tmp_path,
            selection_date=sel_day,
            as_of_date=sel_day,
            fetch_fn=fetch_fn,
        )
        assert out.merval_symbols[:2] == ["ADOG", "ZEBRA"]

    def test_should_score_failed_fetch_as_zero_volume_without_crashing(self, tmp_path: Path):
        sym_dir = tmp_path / "config" / "symbols"
        sym_dir.mkdir(parents=True)
        (sym_dir / "whitelist_ar.yaml").write_text(
            "stocks:\n  - OKSYM\n  - BADSYM\n", encoding="utf-8"
        )
        (sym_dir / "whitelist_cedear.yaml").write_text("stocks:\n  - ZZ\n", encoding="utf-8")
        policy = {
            "schema_version": 1,
            "symbols": {
                "whitelist_ar_file": "config/symbols/whitelist_ar.yaml",
                "whitelist_cedear_file": "config/symbols/whitelist_cedear.yaml",
                "universe_selection": {
                    "enabled": True,
                    "rebalance_frequency": "weekly",
                    "targets": {"merval_top_n": 2, "cedears_top_n": 1},
                    "volume_window_trading_days": 2,
                    "tiebreakers": ["avg_notional_desc", "symbol_asc"],
                    "api_budget": {
                        "monthly_limit": 25000,
                        "soft_limit_pct": 0.8,
                        "max_calls_per_job": 2000,
                    },
                },
            },
        }
        sel_day = date(2024, 10, 1)
        start = window_start_for_volume(sel_day, 2)

        def fetch_fn(symbol: str, s: date, e: date, timeout: int, iol_only: bool = False):
            if symbol == "OKSYM":
                return [
                    _bar(start, 1.0, 50.0, "OKSYM"),
                    _bar(start + timedelta(days=1), 1.0, 50.0, "OKSYM"),
                ]
            if symbol == "BADSYM":
                return None
            if symbol == "ZZ":
                return [
                    _bar(start, 1.0, 10.0, "ZZ"),
                    _bar(start + timedelta(days=1), 1.0, 10.0, "ZZ"),
                ]
            return []

        out = select_dynamic_universe(
            policy,
            tmp_path,
            selection_date=sel_day,
            as_of_date=sel_day,
            fetch_fn=fetch_fn,
        )
        assert out.merval_symbols[0] == "OKSYM"
        assert "BADSYM" in out.merval_symbols
        assert ("BADSYM", "iol_fetch_failed") in out.skipped

    def test_should_abort_entire_selection_when_job_budget_raises(self, tmp_path: Path):
        sym_dir = tmp_path / "config" / "symbols"
        sym_dir.mkdir(parents=True)
        (sym_dir / "whitelist_ar.yaml").write_text("stocks:\n  - A\n", encoding="utf-8")
        (sym_dir / "whitelist_cedear.yaml").write_text("stocks:\n  - B\n", encoding="utf-8")
        policy = {
            "schema_version": 1,
            "symbols": {
                "whitelist_ar_file": "config/symbols/whitelist_ar.yaml",
                "whitelist_cedear_file": "config/symbols/whitelist_cedear.yaml",
                "universe_selection": {
                    "enabled": True,
                    "rebalance_frequency": "weekly",
                    "targets": {"merval_top_n": 1, "cedears_top_n": 1},
                    "volume_window_trading_days": 2,
                    "tiebreakers": ["avg_notional_desc", "symbol_asc"],
                    "api_budget": {
                        "monthly_limit": 25000,
                        "soft_limit_pct": 0.8,
                        "max_calls_per_job": 2000,
                    },
                },
            },
        }

        def boom(*a, **k):
            raise IolJobBudgetExhausted()

        out = select_dynamic_universe(
            policy,
            tmp_path,
            selection_date=date(2024, 11, 1),
            as_of_date=date(2024, 11, 1),
            fetch_fn=boom,
        )
        assert out.budget_job_aborted is True
        assert out.merval_symbols == []
        assert out.cedear_symbols == []
        assert ("_", "job_budget_exceeded") in out.skipped


class TestResolveArUniverseForShortPipeline:
    def test_static_mode_unions_holdings_even_when_disabled(self):
        from core_sim.ledger import PortfolioLedger

        policy = {
            "symbols": {
                "whitelist_ar_file": "config/symbols/whitelist_ar.yaml",
                "whitelist_cedear_file": "config/symbols/whitelist_cedear.yaml",
                "inline_ar": ["ZZHOLD"],
                "universe_selection": {"enabled": False},
            },
        }
        repo = Path(__file__).resolve().parents[1]
        ledger = PortfolioLedger(starting_cash=10_000.0)
        ledger.apply_fills(
            date(2026, 1, 2),
            [
                {
                    "symbol": "HOLD1",
                    "side": "BUY",
                    "qty": 1.0,
                    "price": 10.0,
                    "market": "AR",
                    "bucket": "short",
                    "fee": 0.0,
                },
            ],
        )
        out = resolve_ar_universe_for_short_pipeline(policy, repo, ledger, db=None)
        assert "HOLD1" in out.symbols_ar_bars
        assert "ZZHOLD" in out.symbols_ar_bars
        assert out.ar_signal_symbols is None
        assert out.universe_meta["mode"] == "static_whitelist"

    def test_dynamic_mode_uses_db_snapshot_and_signal_set(self):
        from unittest.mock import MagicMock

        policy = {
            "symbols": {
                "whitelist_ar_file": "config/symbols/whitelist_ar.yaml",
                "whitelist_cedear_file": "config/symbols/whitelist_cedear.yaml",
                "universe_selection": {"enabled": True},
            },
        }
        repo = Path(__file__).resolve().parents[1]
        ledger = MagicMock()
        ledger.positions = {}
        rows = [
            UniverseSnapshotRow(
                selection_date=date(2026, 1, 5),
                bucket="merval",
                symbol="AAA",
                rank=1,
                metric_value=1.0,
                source="dynamic",
                schema_version=1,
            ),
            UniverseSnapshotRow(
                selection_date=date(2026, 1, 5),
                bucket="cedear",
                symbol="BBB",
                rank=1,
                metric_value=2.0,
                source="dynamic",
                schema_version=1,
            ),
        ]
        db = MagicMock()
        db.get_latest_universe_selection_date.return_value = date(2026, 1, 5)
        db.get_universe_snapshots_for_date.return_value = rows
        out = resolve_ar_universe_for_short_pipeline(policy, repo, ledger, db=db)
        assert set(out.symbols_ar_bars) == {"AAA", "BBB"}
        assert out.ar_signal_symbols == frozenset({"AAA", "BBB"})
        assert out.universe_meta["mode"] == "dynamic"

    def test_dynamic_mode_unions_open_ar_holding_not_in_liquidity_top(self):
        """Bars universe = top ∪ holdings; signal pool stays liquidity-only (no holding drift)."""
        from unittest.mock import MagicMock

        from core_sim.ledger import PortfolioLedger

        policy = {
            "symbols": {
                "whitelist_ar_file": "config/symbols/whitelist_ar.yaml",
                "whitelist_cedear_file": "config/symbols/whitelist_cedear.yaml",
                "universe_selection": {"enabled": True},
            },
        }
        repo = Path(__file__).resolve().parents[1]
        ledger = PortfolioLedger(starting_cash=50_000.0)
        ledger.apply_fills(
            date(2026, 2, 3),
            [
                {
                    "symbol": "NOTINTOP",
                    "side": "BUY",
                    "qty": 10.0,
                    "price": 5.0,
                    "market": "AR",
                    "bucket": "short",
                    "fee": 0.0,
                },
            ],
        )
        rows = [
            UniverseSnapshotRow(
                selection_date=date(2026, 2, 1),
                bucket="merval",
                symbol="TOP1",
                rank=1,
                metric_value=9.0,
                source="dynamic",
                schema_version=1,
            ),
        ]
        db = MagicMock()
        db.get_latest_universe_selection_date.return_value = date(2026, 2, 1)
        db.get_universe_snapshots_for_date.return_value = rows
        out = resolve_ar_universe_for_short_pipeline(policy, repo, ledger, db=db)
        assert out.symbols_ar_bars == ["NOTINTOP", "TOP1"]
        assert out.ar_signal_symbols == frozenset({"TOP1"})
        assert "NOTINTOP" not in (out.ar_signal_symbols or frozenset())
