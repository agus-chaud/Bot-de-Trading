#!/usr/bin/env python3
"""Walk-forward long_engine → JSON en validation_reports/ (T4–T6)."""

from __future__ import annotations

import argparse
import io
import sys
from datetime import date, datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.storage import MarketDB  # noqa: E402
from validation.runner import _get_trading_days  # noqa: E402
from validation.wf_long_report import (  # noqa: E402
    build_long_engine_wf_report_model,
    long_engine_wf_report_to_json_dict,
    save_long_engine_wf_report_json,
)
from validation.wf_runner import run_long_engine_wf_windows  # noqa: E402
from validation.wf_windows import generate_wf_windows  # noqa: E402

POLICY_YAML = REPO_ROOT / "config" / "policy.v1.yaml"
REPORTS_DIR = REPO_ROOT / "validation_reports"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Walk-forward del motor largo: ventanas → métricas → reporte JSON."
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Path a MarketDB SQLite (default: data/market.db bajo el repo).",
    )
    parser.add_argument("--cash", type=float, default=100_000.0)
    parser.add_argument(
        "--lookback",
        type=int,
        default=None,
        help="Días hábiles US hasta la fecha de referencia (default: validation_wf.lookback_trading_days).",
    )
    parser.add_argument("--window-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=1)
    parser.add_argument(
        "--date",
        dest="reference_date",
        default=None,
        help="Fecha referencia ISO YYYY-MM-DD (default: hoy).",
    )
    args = parser.parse_args()

    with POLICY_YAML.open(encoding="utf-8") as f:
        policy_doc = yaml.safe_load(f)

    ref = date.fromisoformat(args.reference_date) if args.reference_date else date.today()
    lookback = args.lookback
    if lookback is None:
        lookback = int(policy_doc["validation_wf"]["lookback_trading_days"])

    db_path = args.db or str(REPO_ROOT / "data" / "market.db")
    db = MarketDB(db_path)

    trading_days = _get_trading_days(db, ref, lookback)
    windows = generate_wf_windows(
        trading_days, window_months=args.window_months, step_months=args.step_months
    )
    results = run_long_engine_wf_windows(
        db, windows, policy_doc, REPO_ROOT, args.cash
    )
    model = build_long_engine_wf_report_model(
        windows,
        results,
        policy_version=int(policy_doc["schema_version"]),
        generated_at=datetime.now(tz=timezone.utc).isoformat(),
    )
    payload = long_engine_wf_report_to_json_dict(model)
    out = save_long_engine_wf_report_json(payload, REPORTS_DIR)
    print(f"Ventanas: {len(windows)} | Usadas en agregados: {payload['windows_used_in_aggregates']}")
    print(f"Reporte: {out}")


if __name__ == "__main__":
    main()
