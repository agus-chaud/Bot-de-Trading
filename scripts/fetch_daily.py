"""CLI script: fetch OHLCV data for configured symbols and store in SQLite.

Usage:
    python scripts/fetch_daily.py [--lookback N] [--db PATH] [--symbols-us S1 S2] [--symbols-ar S1 S2]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Allow imports from the project root regardless of CWD.
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml  # type: ignore[import]

from data.fetcher import fetch_and_store
from data.storage import MarketDB

_DEFAULT_SYMBOLS_US = ["SPY", "QQQ", "IWM"]
_DEFAULT_SYMBOLS_AR = ["GGAL", "YPFD", "BMA", "PAMP", "TXAR"]

_POLICY_PATH = Path(__file__).parent.parent / "config" / "policy.v1.yaml"


def _load_symbols_from_policy() -> tuple[list[str], list[str]]:
    """Read whitelists referenced in policy.v1.yaml; fall back to hardcoded defaults."""
    try:
        with open(_POLICY_PATH) as f:
            policy = yaml.safe_load(f)

        base = Path(__file__).parent.parent

        us_file = policy.get("symbols", {}).get("whitelist_us_file")
        ar_file = policy.get("symbols", {}).get("whitelist_ar_file")

        us_symbols: list[str] = []
        if us_file:
            with open(base / us_file) as f:
                data = yaml.safe_load(f)
            for key in ("etfs", "stocks", "adrs"):
                us_symbols.extend(data.get(key, []))

        ar_symbols: list[str] = []
        if ar_file:
            with open(base / ar_file) as f:
                data = yaml.safe_load(f)
            for key in ("stocks",):
                ar_symbols.extend(data.get(key, []))

        return us_symbols or _DEFAULT_SYMBOLS_US, ar_symbols or _DEFAULT_SYMBOLS_AR

    except Exception:
        return _DEFAULT_SYMBOLS_US, _DEFAULT_SYMBOLS_AR


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch daily OHLCV bars and store in SQLite.")
    parser.add_argument("--lookback", type=int, default=5, help="Days back from today (default: 5)")
    parser.add_argument("--db", default="data/market.db", help="Path to SQLite DB (default: data/market.db)")
    parser.add_argument("--symbols-us", nargs="*", dest="symbols_us", help="US symbols to fetch")
    parser.add_argument("--symbols-ar", nargs="*", dest="symbols_ar", help="AR symbols to fetch")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    today = date.today()
    start_date = today - timedelta(days=args.lookback)
    end_date = today

    if args.symbols_us is not None:
        symbols_us = args.symbols_us
    else:
        symbols_us, _ = _load_symbols_from_policy()

    if args.symbols_ar is not None:
        symbols_ar = args.symbols_ar
    else:
        _, symbols_ar = _load_symbols_from_policy()

    db_path = args.db
    # Ensure parent directory exists for the DB file.
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

    db = MarketDB(db_path)
    report = fetch_and_store(
        symbols_us=symbols_us,
        symbols_ar=symbols_ar,
        start_date=start_date,
        end_date=end_date,
        db=db,
    )

    output = {
        "fetched_us": report.fetched_us,
        "fetched_ar": report.fetched_ar,
        "skipped_us": report.skipped_us,
        "skipped_ar": report.skipped_ar,
        "rows_stored": report.rows_stored,
        "errors": report.errors,
    }
    print(json.dumps(output, indent=2))

    total_fetched = len(report.fetched_us) + len(report.fetched_ar)
    sys.exit(0 if total_fetched > 0 else 1)


if __name__ == "__main__":
    main()
