#!/usr/bin/env python3
"""Run a parametric what-if scenario over market.db (JSON stdout).

READ-ONLY. Reuses ``reporting.scenario.run_scenario`` on the same bars as the
signal-IC layer. Engine overrides and optional report overrides are passed as
JSON objects on the command line.

Usage examples::

  python scripts/run_scenario.py
  python scripts/run_scenario.py --override '{"momentum_lookback_days": 15}'
  python scripts/run_scenario.py --report-override '{"n_min": 8}' --symbol SPY
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core_sim.short_term_day_runner import load_merged_whitelist  # noqa: E402
from data.storage import MarketDB  # noqa: E402
from reporting.scenario import (  # noqa: E402
    confidence_thresholds_from_policy,
    run_scenario,
)
from reporting.signal_ic import (  # noqa: E402
    bars_by_date_from_db,
    short_engine_config_from_policy,
)

DB_PATH = REPO_ROOT / "data" / "market.db"
POLICY_PATH = REPO_ROOT / "config" / "policy.v1.yaml"

DEFAULT_START = date(2025, 4, 28)
DEFAULT_END = date(2026, 6, 2)
DEFAULT_HORIZONS = (1, 2, 3, 5, 8, 10)
DEFAULT_N_MIN = 5


def _parse_json_object(raw: str | None, flag_name: str) -> dict:
    if raw is None:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON for {flag_name}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f"{flag_name} must be a JSON object")
    return parsed


def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise SystemExit(f"Invalid date {raw!r} (expected YYYY-MM-DD)") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a what-if scenario (JSON).")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="Path to market.db")
    parser.add_argument("--policy", type=Path, default=POLICY_PATH, help="Path to policy YAML")
    parser.add_argument("--start", type=_parse_date, default=DEFAULT_START)
    parser.add_argument("--end", type=_parse_date, default=DEFAULT_END)
    parser.add_argument(
        "--override",
        default="{}",
        help='Engine override JSON, e.g. \'{"momentum_lookback_days": 15}\'',
    )
    parser.add_argument(
        "--report-override",
        default="{}",
        help='Report override JSON, e.g. \'{"n_min": 8, "horizons": [1, 5]}\'',
    )
    parser.add_argument("--symbol", default=None, help="Optional single-symbol filter")
    parser.add_argument("--baseline-horizon", type=int, default=1)
    parser.add_argument("--baseline-seed", type=int, default=12345)
    parser.add_argument("--n-min", type=int, default=DEFAULT_N_MIN)
    args = parser.parse_args()

    policy_doc = yaml.safe_load(args.policy.read_text(encoding="utf-8"))
    base_config = short_engine_config_from_policy(policy_doc)
    merged_whitelist = load_merged_whitelist(REPO_ROOT, policy_doc)

    if not args.db.is_file():
        raise SystemExit(f"Database not found: {args.db}")

    db = MarketDB(str(args.db))
    bars_by_date = bars_by_date_from_db(db, args.start, args.end, merged_whitelist)
    trading_days = sorted(bars_by_date.keys())
    if not trading_days:
        raise SystemExit("No bars loaded for the requested date range")

    override = _parse_json_object(args.override, "--override")
    report_override = _parse_json_object(args.report_override, "--report-override")

    report = run_scenario(
        base_config=base_config,
        override=override,
        report_override=report_override or None,
        bars_by_date=bars_by_date,
        merged_whitelist=merged_whitelist,
        trading_days=trading_days,
        horizons=DEFAULT_HORIZONS,
        n_min=args.n_min,
        baseline_horizon=args.baseline_horizon,
        baseline_seed=args.baseline_seed,
        symbol_filter=args.symbol,
        confidence_thresholds=confidence_thresholds_from_policy(policy_doc),
    )
    payload = {
        "range": {"start": args.start.isoformat(), "end": args.end.isoformat()},
        "trading_days": len(trading_days),
        "whitelist_size": len(merged_whitelist),
        **report,
    }
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
