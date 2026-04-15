"""Trading calendar helpers for deterministic market-day checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml


def _to_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError(f"Invalid date value: {value!r}")


@dataclass(frozen=True)
class TradingCalendarStore:
    """Single source of truth for US sessions and AR business days."""

    us_sessions: frozenset[date]
    ar_business_days: frozenset[date]

    @classmethod
    def from_yaml(cls, path: str | Path) -> "TradingCalendarStore":
        with Path(path).open(encoding="utf-8") as handle:
            payload = yaml.safe_load(handle)

        us_sessions = frozenset(_to_date(day) for day in payload["us"]["sessions"])
        ar_business_days = frozenset(_to_date(day) for day in payload["ar"]["business_days"])
        return cls(us_sessions=us_sessions, ar_business_days=ar_business_days)

    def is_us_session(self, trading_day: date) -> bool:
        return trading_day in self.us_sessions

    def is_ar_business_day(self, trading_day: date) -> bool:
        return trading_day in self.ar_business_days
