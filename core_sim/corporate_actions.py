"""US corporate actions helpers (v1: splits and dividends)."""

from __future__ import annotations

import sqlite3
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
    raise TypeError(f"Invalid action date value: {value!r}")


def _validate_action_row(row: dict[str, Any], seen_keys: set[tuple[str, date, str]]) -> None:
    action_date = _to_date(row["date"])
    symbol = str(row["symbol"])
    action_type = str(row["action_type"])
    dedupe_key = (symbol, action_date, action_type)
    if dedupe_key in seen_keys:
        raise ValueError(
            "Duplicate corporate action for symbol/date/type: "
            f"{symbol} {action_date.isoformat()} {action_type}"
        )
    seen_keys.add(dedupe_key)

    if action_type == "dividend":
        cash_amount = float(row["cash_amount"])
        if cash_amount < 0:
            raise ValueError(f"Dividend cash_amount must be >= 0 for {symbol}")
    elif action_type == "split":
        split_ratio = float(row["split_ratio"])
        if split_ratio <= 0:
            raise ValueError(f"Split split_ratio must be > 0 for {symbol}")


@dataclass(frozen=True)
class CorporateAction:
    """Normalized action payload used by the core simulation."""

    action_date: date
    symbol: str
    action_type: str
    value: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.action_date.isoformat(),
            "symbol": self.symbol,
            "action_type": self.action_type,
            "value": self.value,
        }


@dataclass(frozen=True)
class CorporateActionsStore:
    """In-memory lookup of actions keyed by trading day."""

    actions_by_day: dict[date, tuple[CorporateAction, ...]]

    @classmethod
    def from_yaml(cls, path: str | Path) -> "CorporateActionsStore":
        with Path(path).open(encoding="utf-8") as handle:
            payload = yaml.safe_load(handle)

        grouped: dict[date, list[CorporateAction]] = {}
        seen_keys: set[tuple[str, date, str]] = set()
        for row in payload.get("actions", []):
            _validate_action_row(row=row, seen_keys=seen_keys)
            action_type = row["action_type"]
            if action_type == "dividend":
                value = float(row["cash_amount"])
            elif action_type == "split":
                value = float(row["split_ratio"])
            else:
                raise ValueError(f"Unsupported corporate action type: {action_type}")

            event = CorporateAction(
                action_date=_to_date(row["date"]),
                symbol=row["symbol"],
                action_type=action_type,
                value=value,
            )
            grouped.setdefault(event.action_date, []).append(event)

        frozen = {day: tuple(events) for day, events in grouped.items()}
        return cls(actions_by_day=frozen)

    @classmethod
    def from_db(cls, db_path: str) -> "CorporateActionsStore":
        """Load corporate actions from a MarketDB SQLite file.

        The DB schema for corporate_actions is:
            symbol TEXT, ts TEXT, type TEXT, factor REAL
        where *type* maps to action_type and *factor* maps to value.

        Args:
            db_path: Path to the SQLite database created by MarketDB.
        """
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.execute(
                "SELECT symbol, ts, type, factor FROM corporate_actions"
            )
            grouped: dict[date, list[CorporateAction]] = {}
            for row in cursor.fetchall():
                symbol, ts_str, action_type, factor = row
                action_date = date.fromisoformat(ts_str)
                event = CorporateAction(
                    action_date=action_date,
                    symbol=symbol,
                    action_type=action_type,
                    value=float(factor),
                )
                grouped.setdefault(action_date, []).append(event)
        finally:
            conn.close()
        frozen = {day: tuple(events) for day, events in grouped.items()}
        return cls(actions_by_day=frozen)

    def get_for_day(self, trading_day: date, symbols: set[str] | None = None) -> tuple[CorporateAction, ...]:
        events = self.actions_by_day.get(trading_day, ())
        if symbols is None:
            return events
        return tuple(event for event in events if event.symbol in symbols)
