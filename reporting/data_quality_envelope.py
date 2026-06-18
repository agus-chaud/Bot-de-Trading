"""Data-quality / confidence envelope for v1 deterministic outputs.

Informational only (non-blocking), aligned with ``validation.stages.data_quality``:
``imputed_pct`` uses the same formula as that stage. Confidence tiers are read from
policy ``data_quality_confidence``.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping, Sequence

_CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


def confidence_ordinal(label: str) -> int:
    """Map confidence label to a sortable integer (low=0, high=2)."""
    return _CONFIDENCE_ORDER[label]


def compute_imputed_pct(imputed_bars: int, total_bars: int) -> float:
    """Same formula as ``validation.stages.data_quality.run_data_quality_stage``."""
    return (
        round(imputed_bars / total_bars * 100, 2)
        if total_bars > 0
        else 0.0
    )


def thresholds_from_policy(policy_doc: Mapping[str, Any]) -> dict[str, dict[str, float | int]]:
    """Load ``data_quality_confidence`` tiers from a parsed policy document."""
    raw = policy_doc["data_quality_confidence"]
    return {
        "high": dict(raw["high"]),
        "medium": dict(raw["medium"]),
    }


def classify_confidence(
    n_observations: int,
    imputed_pct: float,
    thresholds: Mapping[str, Mapping[str, float | int]],
) -> str:
    """Classify confidence from bar count and imputed share (high → medium → low)."""
    high = thresholds["high"]
    medium = thresholds["medium"]
    if (
        n_observations >= int(high["min_n_observations"])
        and imputed_pct <= float(high["max_imputed_pct"])
    ):
        return "high"
    if (
        n_observations >= int(medium["min_n_observations"])
        and imputed_pct <= float(medium["max_imputed_pct"])
    ):
        return "medium"
    return "low"


def _bar_is_imputed(bar: Mapping[str, Any]) -> bool:
    val = bar.get("imputed", False)
    if isinstance(val, bool):
        return val
    return bool(int(val))


def _bar_date(bar: Mapping[str, Any]) -> date:
    ts = bar.get("ts")
    if isinstance(ts, date):
        return ts
    if isinstance(ts, str):
        return date.fromisoformat(ts)
    raise ValueError(f"bar missing date ts: {bar!r}")


def build_date_coverage(
    unique_dates: set[date],
    expected_dates: Sequence[date],
) -> dict[str, Any]:
    """Summarize calendar coverage for the bars that produced a result."""
    n_expected = len(expected_dates)
    if not unique_dates:
        return {
            "first_date": None,
            "last_date": None,
            "n_unique_dates": 0,
            "n_expected_dates": n_expected,
            "coverage_ratio": None,
        }

    sorted_unique = sorted(unique_dates)
    coverage_ratio = (
        len(unique_dates) / n_expected
        if n_expected > 0
        else None
    )
    return {
        "first_date": sorted_unique[0].isoformat(),
        "last_date": sorted_unique[-1].isoformat(),
        "n_unique_dates": len(unique_dates),
        "n_expected_dates": n_expected,
        "coverage_ratio": coverage_ratio,
    }


def flatten_bars_by_date(
    bars_by_date: Mapping[date, Mapping[str, Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """Expand ``date -> symbol -> bar`` into flat bar dicts with ``ts`` + ``symbol``."""
    out: list[dict[str, Any]] = []
    for day in sorted(bars_by_date.keys()):
        by_symbol = bars_by_date[day]
        for symbol, bar in by_symbol.items():
            out.append({"symbol": symbol, "ts": day, **dict(bar)})
    return out


def build_data_quality_envelope(
    bars: Iterable[Mapping[str, Any]],
    *,
    stale_marks: Sequence[str] | None = None,
    expected_dates: Sequence[date] | None = None,
    thresholds: Mapping[str, Mapping[str, float | int]],
) -> dict[str, Any]:
    """Build the five-field envelope scoped to the bars consumed."""
    bar_list = list(bars)
    n_observations = len(bar_list)
    imputed_bars = sum(1 for bar in bar_list if _bar_is_imputed(bar))
    imputed_pct = compute_imputed_pct(imputed_bars, n_observations)
    unique_dates = {_bar_date(bar) for bar in bar_list}
    expected = list(expected_dates or [])

    return {
        "n_observations": n_observations,
        "imputed_pct": imputed_pct,
        "date_coverage": build_date_coverage(unique_dates, expected),
        "stale_marks": list(stale_marks or []),
        "confidence": classify_confidence(n_observations, imputed_pct, thresholds),
    }
