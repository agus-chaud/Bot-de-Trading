"""T3.1 — Behavior tests for the data-quality / confidence envelope (Mejora 3).

Contract (per v1_outputs plan):
- ``classify_confidence`` is deterministic and monotone in (n_observations, imputed_pct).
- ``build_data_quality_envelope`` reports n_observations, imputed_pct, date_coverage,
  stale_marks, and confidence scoped to the bars that produced the result.

Implementation lives in ``reporting.data_quality_envelope`` (T3.3).
"""

from __future__ import annotations

from datetime import date

import pytest

from reporting.data_quality_envelope import (
    build_data_quality_envelope,
    classify_confidence,
    confidence_ordinal,
)

# Mirrors config/policy.v1.yaml — tests define the contract before wiring policy load.
THRESHOLDS = {
    "high": {"min_n_observations": 60, "max_imputed_pct": 2.0},
    "medium": {"min_n_observations": 20, "max_imputed_pct": 5.0},
}


def _bar(symbol: str, day: date, *, imputed: bool = False) -> dict:
    return {
        "symbol": symbol,
        "ts": day,
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 1_000_000.0,
        "imputed": imputed,
    }


class TestClassifyConfidence:
    """Deterministic, monotone confidence tiers from (n_observations, imputed_pct)."""

    def test_same_inputs_same_label(self):
        a = classify_confidence(45, 3.5, THRESHOLDS)
        b = classify_confidence(45, 3.5, THRESHOLDS)
        assert a == b

    @pytest.mark.parametrize(
        "n_observations, imputed_pct, expected",
        [
            (60, 0.0, "high"),
            (60, 2.0, "high"),
            (100, 1.5, "high"),
            (20, 0.0, "medium"),
            (20, 5.0, "medium"),
            (59, 1.0, "medium"),
            (19, 0.0, "low"),
            (60, 2.01, "medium"),
            (60, 6.0, "low"),
            (0, 0.0, "low"),
        ],
    )
    def test_known_tier_boundaries(self, n_observations, imputed_pct, expected):
        assert classify_confidence(n_observations, imputed_pct, THRESHOLDS) == expected

    def test_monotone_in_n_observations(self):
        imputed_pct = 3.0
        prev = confidence_ordinal(
            classify_confidence(0, imputed_pct, THRESHOLDS)
        )
        for n in range(1, 121):
            level = confidence_ordinal(
                classify_confidence(n, imputed_pct, THRESHOLDS)
            )
            assert level >= prev
            prev = level

    def test_monotone_in_imputed_pct(self):
        n_observations = 40
        prev = confidence_ordinal(
            classify_confidence(n_observations, 0.0, THRESHOLDS)
        )
        for pct_int in range(0, 101):
            imputed_pct = float(pct_int)
            level = confidence_ordinal(
                classify_confidence(n_observations, imputed_pct, THRESHOLDS)
            )
            assert level <= prev
            prev = level


class TestBuildDataQualityEnvelope:
    """Envelope metrics over a known bar set."""

    def test_reports_counts_imputed_pct_and_date_coverage(self):
        bars = [
            _bar("SPY", date(2024, 1, 2)),
            _bar("SPY", date(2024, 1, 3)),
            _bar("SPY", date(2024, 1, 3), imputed=True),
            _bar("QQQ", date(2024, 1, 5)),
            _bar("QQQ", date(2024, 1, 5), imputed=True),
            _bar("QQQ", date(2024, 1, 8)),
        ]
        expected_dates = [
            date(2024, 1, 2),
            date(2024, 1, 3),
            date(2024, 1, 4),
            date(2024, 1, 5),
            date(2024, 1, 8),
        ]

        envelope = build_data_quality_envelope(
            bars,
            stale_marks=["SPY"],
            expected_dates=expected_dates,
            thresholds=THRESHOLDS,
        )

        assert envelope["n_observations"] == 6
        assert envelope["imputed_pct"] == pytest.approx(33.33, abs=0.01)
        assert envelope["stale_marks"] == ["SPY"]
        assert envelope["confidence"] == "low"

        coverage = envelope["date_coverage"]
        assert coverage["first_date"] == "2024-01-02"
        assert coverage["last_date"] == "2024-01-08"
        assert coverage["n_unique_dates"] == 4
        assert coverage["n_expected_dates"] == 5
        assert coverage["coverage_ratio"] == pytest.approx(0.8)

    def test_empty_bars_zero_metrics_and_low_confidence(self):
        envelope = build_data_quality_envelope(
            [],
            stale_marks=[],
            expected_dates=[],
            thresholds=THRESHOLDS,
        )
        assert envelope["n_observations"] == 0
        assert envelope["imputed_pct"] == 0.0
        assert envelope["confidence"] == "low"
        assert envelope["date_coverage"]["n_unique_dates"] == 0
        assert envelope["date_coverage"]["coverage_ratio"] is None
