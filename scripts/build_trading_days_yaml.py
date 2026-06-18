#!/usr/bin/env python3
"""Regenerate config/calendars/trading_days.v1.yaml from pandas_market_calendars.

Production calendar must cover real paper-live / backtest ranges — not the 4-day
test stub (see tests/fixtures/calendars/trading_days_stub.v1.yaml).
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas_market_calendars as mcal
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "config" / "calendars" / "trading_days.v1.yaml"
DEFAULT_START = date(2024, 1, 1)
DEFAULT_END = date(2027, 12, 31)
MIN_US_SESSIONS = 200
MIN_AR_DAYS = 200


def _valid_days(exchange: str, start: date, end: date) -> list[str]:
    calendar = mcal.get_calendar(exchange)
    index = calendar.valid_days(
        start_date=start.isoformat(),
        end_date=end.isoformat(),
    )
    return sorted(dt.date().isoformat() for dt in index)


def build_payload(start: date, end: date) -> dict:
    us_sessions = _valid_days("NYSE", start, end)
    ar_days = _valid_days("XBUE", start, end)
    if len(us_sessions) < MIN_US_SESSIONS:
        raise RuntimeError(
            f"XNYS sessions too few ({len(us_sessions)}); check range {start}..{end}"
        )
    if len(ar_days) < MIN_AR_DAYS:
        raise RuntimeError(
            f"XBUE business days too few ({len(ar_days)}); check range {start}..{end}"
        )
    return {
        "schema_version": 1,
        "description": (
            "Fuente unica de verdad para sesiones US y dias habiles AR. "
            f"Generado por scripts/build_trading_days_yaml.py ({start}..{end})."
        ),
        "us": {"market_code": "XNYS", "sessions": us_sessions},
        "ar": {"market_code": "BYMA", "business_days": ar_days},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    parser.add_argument("--end", type=date.fromisoformat, default=DEFAULT_END)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    payload = build_payload(args.start, args.end)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=True)

    print(
        f"wrote {args.out} "
        f"(us={len(payload['us']['sessions'])}, ar={len(payload['ar']['business_days'])})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
