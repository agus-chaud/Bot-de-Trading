#!/usr/bin/env python3
"""Ejecuta validación pre-gate walk-forward del bloque corto (exit 0 = pass, 1 = fail)."""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core_sim.short_term_pre_gate import run_short_term_pre_gate  # noqa: E402


def _weekdays_from(start: date, n: int) -> list[date]:
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _demo_bars(days: list[date]) -> dict[date, dict[str, dict[str, float]]]:
    bars: dict[date, dict[str, dict[str, float]]] = {}
    for i, d in enumerate(days):
        spy_close = 100.0 + float(i) * 0.35
        qqq_close = 200.0 - float(i) * 0.2
        bars[d] = {
            "SPY": {
                "open": spy_close,
                "high": spy_close + 0.5,
                "low": spy_close - 0.5,
                "close": spy_close,
                "volume": 80_000_000.0,
            },
            "QQQ": {
                "open": qqq_close,
                "high": qqq_close + 0.5,
                "low": qqq_close - 0.5,
                "close": qqq_close,
                "volume": 30_000_000.0,
            },
        }
    return bars


def main() -> int:
    p = argparse.ArgumentParser(description="Short-term walk-forward pre-gate")
    p.add_argument(
        "--policy",
        type=Path,
        default=REPO_ROOT / "config" / "policy.v1.yaml",
        help="Ruta a policy YAML",
    )
    p.add_argument(
        "--demo-days",
        type=int,
        default=90,
        help="Con --demo: cantidad de días hábiles sintéticos SPY/QQQ",
    )
    args = p.parse_args()

    with args.policy.open(encoding="utf-8") as f:
        policy_doc = yaml.safe_load(f)

    days = _weekdays_from(date(2026, 1, 5), max(60, args.demo_days))
    bars = _demo_bars(days)
    report = run_short_term_pre_gate(
        policy_doc=policy_doc,
        repo_root=REPO_ROOT,
        bars_by_date=bars,
        trading_days=days,
    )

    if report.global_failures:
        print("GLOBAL FAIL:", report.global_failures)
        return 1
    for i, w in enumerate(report.windows):
        print(f"window_{i}", w.metrics, "OK" if w.passed else w.violations)
    print("pre_gate_passed:", report.passed)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
