"""CLI script: manually reset the kill switch for a given engine.

Usage:
    python scripts/reset_kill_switch.py --category volatility_spike --reason "mercado volvió a la normalidad"
    python scripts/reset_kill_switch.py --category other --reason "revisión manual" --engine long --db data/market.db
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.storage import MarketDB

_VALID_CATEGORIES = ["volatility_spike", "data_error", "strategy_review", "other"]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manually reset an active kill switch.")
    parser.add_argument(
        "--category",
        required=True,
        choices=_VALID_CATEGORIES,
        help="Reason category for the reset.",
    )
    parser.add_argument(
        "--reason",
        required=True,
        help="Free-text explanation. Must not be empty.",
    )
    parser.add_argument(
        "--engine",
        default="short",
        help="Which engine's kill switch to reset (default: short).",
    )
    parser.add_argument(
        "--db",
        default="data/market.db",
        help="Path to SQLite DB (default: data/market.db).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    if not args.reason.strip():
        print(json.dumps({"error": "reason cannot be empty"}))
        sys.exit(1)

    try:
        db = MarketDB(args.db)
        state = db.get_kill_switch_state(args.engine)

        if not state.active:
            print(json.dumps({"status": "no_active_kill_switch", "engine": args.engine}))
            sys.exit(1)

        today = date.today()
        db.reset_kill_switch(today, args.category, args.reason, auto=False, engine=args.engine)

        result = {
            "status": "reset_ok",
            "engine": args.engine,
            "category": args.category,
            "reason": args.reason,
            "reset_date": today.isoformat(),
            "previously_activated_at": state.activated_at.isoformat() if state.activated_at else None,
            "previously_monthly_dd": state.monthly_dd,
        }
        print(json.dumps(result))
        sys.exit(0)

    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
