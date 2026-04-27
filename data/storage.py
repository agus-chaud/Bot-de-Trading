"""SQLite-backed market data store with optional Supabase sync."""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from data.schema import CorporateActionRow, OHLCVRow


@dataclass(frozen=True)
class KillSwitchState:
    active: bool
    engine: str
    activated_at: date | None
    monthly_dd: float | None
    reset_at: date | None
    reset_category: str | None
    reset_reason: str | None
    auto_reset: bool

logger = logging.getLogger(__name__)

_CREATE_OHLCV = """
CREATE TABLE IF NOT EXISTS ohlcv (
    symbol  TEXT NOT NULL,
    ts      TEXT NOT NULL,
    open    REAL NOT NULL,
    high    REAL NOT NULL,
    low     REAL NOT NULL,
    close   REAL NOT NULL,
    volume  REAL NOT NULL,
    currency TEXT NOT NULL,
    venue   TEXT NOT NULL,
    imputed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (symbol, ts, venue)
);
"""

_CREATE_CORPORATE_ACTIONS = """
CREATE TABLE IF NOT EXISTS corporate_actions (
    symbol  TEXT NOT NULL,
    ts      TEXT NOT NULL,
    type    TEXT NOT NULL,
    factor  REAL NOT NULL,
    PRIMARY KEY (symbol, ts, type)
);
"""

_CREATE_CALENDARS = """
CREATE TABLE IF NOT EXISTS calendars (
    venue TEXT NOT NULL,
    ts    TEXT NOT NULL,
    PRIMARY KEY (venue, ts)
);
"""

_CREATE_FETCH_LOG = """
CREATE TABLE IF NOT EXISTS fetch_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    symbol     TEXT,
    venue      TEXT,
    status     TEXT NOT NULL,
    source     TEXT,
    skip_reason TEXT,
    extra      TEXT
);
"""

_CREATE_KILL_SWITCH_LOG = """
CREATE TABLE IF NOT EXISTS kill_switch_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    engine         TEXT NOT NULL,
    event          TEXT NOT NULL,
    event_date     TEXT NOT NULL,
    monthly_dd     REAL,
    reset_category TEXT,
    reset_reason   TEXT,
    auto_reset     INTEGER DEFAULT 0,
    created_at     TEXT NOT NULL
);
"""


class MarketDB:
    """Local SQLite store for OHLCV bars, corporate actions, and fetch audit logs."""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

        supabase_url = os.environ.get("SUPABASE_URL")
        supabase_key = os.environ.get("SUPABASE_KEY")
        if supabase_url and supabase_key:
            try:
                from supabase import create_client  # type: ignore[import]
                self._supabase = create_client(supabase_url, supabase_key)
            except Exception as exc:  # pragma: no cover
                logger.warning("Supabase client init failed — running offline: %s", exc)
                self._supabase = None
        else:
            self._supabase = None

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        """Create all tables if they do not exist."""
        with self._conn:
            self._conn.executescript(
                _CREATE_OHLCV
                + _CREATE_CORPORATE_ACTIONS
                + _CREATE_CALENDARS
                + _CREATE_FETCH_LOG
                + _CREATE_KILL_SWITCH_LOG
            )

    # ------------------------------------------------------------------
    # OHLCV
    # ------------------------------------------------------------------

    def upsert_ohlcv(self, rows: list[OHLCVRow]) -> None:
        """Insert or replace OHLCV bars; syncs to Supabase when configured."""
        records = [
            (
                r.symbol,
                r.ts.isoformat(),
                r.open,
                r.high,
                r.low,
                r.close,
                r.volume,
                r.currency,
                r.venue,
                int(r.imputed),
            )
            for r in rows
        ]
        with self._conn:
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO ohlcv
                    (symbol, ts, open, high, low, close, volume, currency, venue, imputed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                records,
            )
        self._sync_supabase(
            "ohlcv",
            [
                {
                    "symbol": r.symbol,
                    "ts": r.ts.isoformat(),
                    "open": r.open,
                    "high": r.high,
                    "low": r.low,
                    "close": r.close,
                    "volume": r.volume,
                    "currency": r.currency,
                    "venue": r.venue,
                    "imputed": int(r.imputed),
                }
                for r in rows
            ],
        )

    def get_ohlcv(
        self,
        symbol: str,
        start: date,
        end: date,
        venue: str,
    ) -> list[OHLCVRow]:
        """Return bars for *symbol* at *venue* in [start, end] ordered by ts."""
        cursor = self._conn.execute(
            """
            SELECT symbol, ts, open, high, low, close, volume, currency, venue, imputed
            FROM ohlcv
            WHERE symbol = ? AND venue = ? AND ts BETWEEN ? AND ?
            ORDER BY ts ASC
            """,
            (symbol, venue, start.isoformat(), end.isoformat()),
        )
        return [
            OHLCVRow(
                symbol=row["symbol"],
                ts=date.fromisoformat(row["ts"]),
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
                currency=row["currency"],
                venue=row["venue"],
                imputed=bool(row["imputed"]),
            )
            for row in cursor.fetchall()
        ]

    def get_last_ts(self, symbol: str, venue: str) -> date | None:
        """Return the most recent bar date for *symbol* at *venue*, or None."""
        cursor = self._conn.execute(
            "SELECT MAX(ts) AS last_ts FROM ohlcv WHERE symbol = ? AND venue = ?",
            (symbol, venue),
        )
        row = cursor.fetchone()
        if row and row["last_ts"]:
            return date.fromisoformat(row["last_ts"])
        return None

    # ------------------------------------------------------------------
    # Corporate actions
    # ------------------------------------------------------------------

    def upsert_actions(self, rows: list[CorporateActionRow]) -> None:
        """Insert or replace corporate action rows; syncs to Supabase when configured."""
        records = [
            (r.symbol, r.ts.isoformat(), r.type, r.factor)
            for r in rows
        ]
        with self._conn:
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO corporate_actions (symbol, ts, type, factor)
                VALUES (?, ?, ?, ?)
                """,
                records,
            )
        self._sync_supabase(
            "corporate_actions",
            [
                {
                    "symbol": r.symbol,
                    "ts": r.ts.isoformat(),
                    "type": r.type,
                    "factor": r.factor,
                }
                for r in rows
            ],
        )

    # ------------------------------------------------------------------
    # Calendars
    # ------------------------------------------------------------------

    def upsert_calendars(self, venue: str, days: list[date]) -> None:
        """Insert or replace trading calendar days for *venue*."""
        records = [(venue, d.isoformat()) for d in days]
        with self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO calendars (venue, ts) VALUES (?, ?)",
                records,
            )

    # ------------------------------------------------------------------
    # Fetch log
    # ------------------------------------------------------------------

    def log_fetch(self, entry: dict[str, Any]) -> None:
        """Append a row to fetch_log with created_at set to UTC now."""
        created_at = datetime.now(tz=timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO fetch_log (created_at, symbol, venue, status, source, skip_reason, extra)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created_at,
                    entry.get("symbol"),
                    entry.get("venue"),
                    entry["status"],
                    entry.get("source"),
                    entry.get("skip_reason"),
                    entry.get("extra"),
                ),
            )

    # ------------------------------------------------------------------
    # Kill switch log
    # ------------------------------------------------------------------

    def activate_kill_switch(self, event_date: date, monthly_dd: float, engine: str = "short") -> None:
        """Inserta un evento 'activated'. No verifica estado previo — eso lo hace el caller."""
        created_at = datetime.now(tz=timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO kill_switch_log (engine, event, event_date, monthly_dd, created_at)
                VALUES (?, 'activated', ?, ?, ?)
                """,
                (engine, event_date.isoformat(), monthly_dd, created_at),
            )

    def reset_kill_switch(
        self,
        event_date: date,
        category: str,
        reason: str,
        auto: bool = False,
        engine: str = "short",
    ) -> None:
        """Inserta un evento 'reset'."""
        created_at = datetime.now(tz=timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO kill_switch_log
                    (engine, event, event_date, reset_category, reset_reason, auto_reset, created_at)
                VALUES (?, 'reset', ?, ?, ?, ?, ?)
                """,
                (engine, event_date.isoformat(), category, reason, int(auto), created_at),
            )

    def get_kill_switch_state(self, engine: str = "short") -> KillSwitchState:
        """Retorna el estado actual del kill switch leyendo el último evento para el engine dado."""
        _inactive = KillSwitchState(
            active=False,
            engine=engine,
            activated_at=None,
            monthly_dd=None,
            reset_at=None,
            reset_category=None,
            reset_reason=None,
            auto_reset=False,
        )
        cursor = self._conn.execute(
            """
            SELECT event, event_date, monthly_dd, reset_category, reset_reason, auto_reset
            FROM kill_switch_log
            WHERE engine = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (engine,),
        )
        row = cursor.fetchone()
        if row is None or row["event"] == "reset":
            if row is None:
                return _inactive
            return KillSwitchState(
                active=False,
                engine=engine,
                activated_at=None,
                monthly_dd=None,
                reset_at=date.fromisoformat(row["event_date"]),
                reset_category=row["reset_category"],
                reset_reason=row["reset_reason"],
                auto_reset=bool(row["auto_reset"]),
            )
        # last event is 'activated'
        return KillSwitchState(
            active=True,
            engine=engine,
            activated_at=date.fromisoformat(row["event_date"]),
            monthly_dd=row["monthly_dd"],
            reset_at=None,
            reset_category=None,
            reset_reason=None,
            auto_reset=False,
        )

    # ------------------------------------------------------------------
    # Supabase sync (private, non-blocking)
    # ------------------------------------------------------------------

    def _sync_supabase(self, table: str, rows: list[dict[str, Any]]) -> None:
        """Best-effort upsert to Supabase; logs and continues on any error."""
        if self._supabase is None or not rows:
            return
        try:
            self._supabase.table(table).upsert(rows).execute()
        except Exception as exc:
            logger.warning("Supabase sync failed for table=%s: %s", table, exc)
