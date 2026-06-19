"""SQLite-backed market data store with optional Supabase sync."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any

from data.schema import CorporateActionRow, OHLCVRow, PortfolioMeta, UniverseSnapshotRow

if TYPE_CHECKING:
    from core_sim.ledger import PortfolioLedger


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


class PortfolioMetaConflictError(Exception):
    """CLI starting_cash/currency does not match persisted portfolio_meta."""


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

_VENUE_MAP: dict[str, str] = {"US": "XNYS", "AR": "XBUE"}

_CREATE_PAPER_FILLS = """
CREATE TABLE IF NOT EXISTS paper_fills (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL,
    mode             TEXT NOT NULL CHECK(mode IN ('paper_live', 'backtest')),
    trading_day      TEXT NOT NULL,
    ts_fill          TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    venue            TEXT NOT NULL,
    side             TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
    qty              REAL NOT NULL,
    price            REAL NOT NULL,
    bucket           TEXT NOT NULL CHECK(bucket IN ('short', 'long')),
    engine           TEXT NOT NULL,
    reason           TEXT,
    fee              REAL NOT NULL DEFAULT 0.0,
    slippage         REAL NOT NULL DEFAULT 0.0,
    cost_total       REAL NOT NULL DEFAULT 0.0,
    source_bar_close REAL,
    notes            TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pf_mode_day    ON paper_fills(mode, trading_day);
CREATE INDEX IF NOT EXISTS idx_pf_symbol_day  ON paper_fills(symbol, trading_day);
CREATE INDEX IF NOT EXISTS idx_pf_run         ON paper_fills(run_id);
"""

_CREATE_PORTFOLIO_META = """
CREATE TABLE IF NOT EXISTS portfolio_meta (
    mode           TEXT PRIMARY KEY CHECK(mode IN ('paper_live', 'backtest')),
    starting_cash  REAL NOT NULL,
    currency       TEXT NOT NULL CHECK(currency IN ('ARS', 'USD')),
    inception_date TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
"""

_CREATE_PAPER_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS paper_snapshots (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    mode                   TEXT NOT NULL CHECK(mode IN ('paper_live', 'backtest')),
    trading_day            TEXT NOT NULL,
    equity_total           REAL NOT NULL,
    equity_short           REAL NOT NULL,
    equity_long            REAL NOT NULL,
    short_cash             REAL NOT NULL,
    cash                   REAL NOT NULL,
    realized_pnl_total     REAL NOT NULL,
    unrealized_pnl_total   REAL NOT NULL,
    costs_day              REAL NOT NULL,
    mv_us                  REAL NOT NULL,
    mv_ar                  REAL NOT NULL,
    short_monthly_peak     REAL,
    short_monthly_drawdown REAL,
    short_daily_return     REAL,
    kill_switch_active     INTEGER NOT NULL DEFAULT 0,
    num_open_positions     INTEGER NOT NULL DEFAULT 0,
    num_fills_today        INTEGER NOT NULL DEFAULT 0,
    realized_pnl_day       REAL,
    created_at             TEXT NOT NULL,
    UNIQUE(mode, trading_day)
);
"""

_CREATE_UNIVERSE_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS universe_snapshots (
    selection_date TEXT NOT NULL,
    bucket        TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    rank          INTEGER NOT NULL,
    metric_value  REAL,
    source        TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (selection_date, bucket, symbol, source)
);
CREATE INDEX IF NOT EXISTS idx_universe_selection_date ON universe_snapshots(selection_date);
"""

_CREATE_IOL_API_USAGE = """
CREATE TABLE IF NOT EXISTS iol_api_usage (
    month_key               TEXT NOT NULL PRIMARY KEY,
    token_count             INTEGER NOT NULL DEFAULT 0,
    refresh_count           INTEGER NOT NULL DEFAULT 0,
    history_count           INTEGER NOT NULL DEFAULT 0,
    universe_volume_count   INTEGER NOT NULL DEFAULT 0,
    updated_at              TEXT NOT NULL
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
                + _CREATE_PAPER_FILLS
                + _CREATE_PORTFOLIO_META
                + _CREATE_PAPER_SNAPSHOTS
                + _CREATE_UNIVERSE_SNAPSHOTS
                + _CREATE_IOL_API_USAGE
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
    # Universe snapshots (liquidity selection audit)
    # ------------------------------------------------------------------

    def replace_universe_snapshots(
        self,
        selection_date: date,
        rows: list[UniverseSnapshotRow],
    ) -> None:
        """Replace all snapshot rows for *selection_date* (full re-write of that day)."""
        created_at = datetime.now(tz=timezone.utc).isoformat()
        d = selection_date.isoformat()
        with self._conn:
            self._conn.execute("DELETE FROM universe_snapshots WHERE selection_date = ?", (d,))
            if not rows:
                return
            self._conn.executemany(
                """
                INSERT INTO universe_snapshots
                    (selection_date, bucket, symbol, rank, metric_value, source, schema_version, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r.selection_date.isoformat(),
                        r.bucket,
                        r.symbol,
                        r.rank,
                        r.metric_value,
                        r.source,
                        r.schema_version,
                        created_at,
                    )
                    for r in rows
                ],
            )

    def get_latest_universe_selection_date(self) -> date | None:
        """Most recent selection_date present in universe_snapshots, or None."""
        cursor = self._conn.execute("SELECT MAX(selection_date) AS d FROM universe_snapshots")
        row = cursor.fetchone()
        if row and row["d"]:
            return date.fromisoformat(str(row["d"]))
        return None

    def get_universe_snapshots_for_date(self, selection_date: date) -> list[UniverseSnapshotRow]:
        """Return persisted universe rows for *selection_date*, ordered by bucket and rank."""
        cursor = self._conn.execute(
            """
            SELECT selection_date, bucket, symbol, rank, metric_value, source, schema_version
            FROM universe_snapshots
            WHERE selection_date = ?
            ORDER BY bucket ASC, rank ASC, symbol ASC
            """,
            (selection_date.isoformat(),),
        )
        return [
            UniverseSnapshotRow(
                selection_date=date.fromisoformat(str(r["selection_date"])),
                bucket=str(r["bucket"]),
                symbol=str(r["symbol"]),
                rank=int(r["rank"]),
                metric_value=float(r["metric_value"]) if r["metric_value"] is not None else None,
                source=str(r["source"]),
                schema_version=int(r["schema_version"]),
            )
            for r in cursor.fetchall()
        ]

    def get_iol_api_usage_month(self, month_key: str) -> dict[str, int]:
        """Return persisted IOL call counts for calendar month *month_key* (YYYY-MM)."""
        cursor = self._conn.execute(
            """
            SELECT token_count, refresh_count, history_count, universe_volume_count
            FROM iol_api_usage WHERE month_key = ?
            """,
            (month_key,),
        )
        row = cursor.fetchone()
        if not row:
            return {
                "token_count": 0,
                "refresh_count": 0,
                "history_count": 0,
                "universe_volume_count": 0,
            }
        return {
            "token_count": int(row["token_count"]),
            "refresh_count": int(row["refresh_count"]),
            "history_count": int(row["history_count"]),
            "universe_volume_count": int(row["universe_volume_count"]),
        }

    def increment_iol_api_usage(
        self,
        month_key: str,
        *,
        token: int = 0,
        refresh: int = 0,
        history: int = 0,
        universe_volume: int = 0,
    ) -> None:
        """Atomically add successful IOL calls for *month_key* (creates row if missing)."""
        if token == 0 and refresh == 0 and history == 0 and universe_volume == 0:
            return
        now = datetime.now(tz=timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO iol_api_usage
                    (month_key, token_count, refresh_count, history_count, universe_volume_count, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(month_key) DO UPDATE SET
                    token_count = token_count + excluded.token_count,
                    refresh_count = refresh_count + excluded.refresh_count,
                    history_count = history_count + excluded.history_count,
                    universe_volume_count = universe_volume_count + excluded.universe_volume_count,
                    updated_at = excluded.updated_at
                """,
                (month_key, token, refresh, history, universe_volume, now),
            )

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
        extra = entry.get("extra")
        if extra is not None and not isinstance(extra, str):
            extra = json.dumps(extra, sort_keys=True)
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
                    extra,
                ),
            )

    def get_recent_fetch_errors(self, limit: int = 8) -> list[dict[str, Any]]:
        """Return symbols whose *latest* fetch_log row is not ok (not stale history)."""
        cursor = self._conn.execute(
            """
            SELECT f.symbol, f.venue, f.status, f.skip_reason, f.created_at
            FROM fetch_log f
            INNER JOIN (
                SELECT symbol, venue, MAX(id) AS max_id
                FROM fetch_log
                GROUP BY symbol, venue
            ) latest ON f.id = latest.max_id
            WHERE f.status != 'ok'
            ORDER BY f.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

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
    # Paper trading persistence
    # ------------------------------------------------------------------

    def persist_fills(
        self,
        run_id: str,
        mode: str,
        trading_day: date,
        fills: list[dict[str, Any]],
        engine: str = "short_term_v1",
    ) -> None:
        """Insert one row per fill into paper_fills. Extracts slippage from cost_breakdown."""
        if not fills:
            return
        created_at = datetime.now(tz=timezone.utc).isoformat()
        ts_fill = created_at
        records = []
        for fill in fills:
            market = str(fill.get("market", ""))
            venue = _VENUE_MAP.get(market, market)
            cost_bd = fill.get("cost_breakdown") or {}
            slippage = float(cost_bd.get("slippage", 0.0))
            cost_total = float(cost_bd.get("total", fill.get("fee", 0.0)))
            records.append((
                run_id,
                mode,
                trading_day.isoformat(),
                ts_fill,
                str(fill["symbol"]),
                venue,
                str(fill["side"]),
                float(fill["qty"]),
                float(fill["price"]),
                str(fill.get("bucket", "short")),
                engine,
                fill.get("reason"),
                float(fill.get("fee", 0.0)),
                slippage,
                cost_total,
                fill.get("source_bar_close"),
                fill.get("notes"),
                created_at,
            ))
        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO paper_fills
                    (run_id, mode, trading_day, ts_fill, symbol, venue, side, qty,
                     price, bucket, engine, reason, fee, slippage, cost_total,
                     source_bar_close, notes, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                records,
            )

    def persist_snapshot(
        self,
        mode: str,
        trading_day: date,
        snapshot: dict[str, Any],
        short_cash: float,
        kill_switch_active: bool = False,
        num_fills_today: int = 0,
    ) -> None:
        """Upsert one end-of-day portfolio snapshot per (mode, trading_day)."""
        sb = snapshot.get("short_bucket") or {}
        created_at = datetime.now(tz=timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO paper_snapshots
                    (mode, trading_day, equity_total, equity_short, equity_long,
                     short_cash, cash, realized_pnl_total, unrealized_pnl_total,
                     costs_day, mv_us, mv_ar, short_monthly_peak,
                     short_monthly_drawdown, short_daily_return,
                     kill_switch_active, num_open_positions, num_fills_today,
                     realized_pnl_day, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    mode,
                    trading_day.isoformat(),
                    float(snapshot.get("equity_total", 0.0)),
                    float(snapshot.get("equity_short", 0.0)),
                    float(snapshot.get("equity_long", 0.0)),
                    float(short_cash),
                    float(snapshot.get("cash", 0.0)),
                    float(snapshot.get("realized_pnl_total", 0.0)),
                    float(snapshot.get("unrealized_pnl_total", 0.0)),
                    float(snapshot.get("costs_day", 0.0)),
                    float(snapshot.get("mv_us", 0.0)),
                    float(snapshot.get("mv_ar", 0.0)),
                    sb.get("monthly_peak"),
                    sb.get("monthly_drawdown"),
                    sb.get("daily_return"),
                    int(kill_switch_active),
                    len(snapshot.get("positions") or {}),
                    num_fills_today,
                    None,
                    created_at,
                ),
            )

    def get_last_snapshot_day(self, mode: str) -> date | None:
        """Return the most recent trading_day with a snapshot for *mode*, or None."""
        cursor = self._conn.execute(
            "SELECT MAX(trading_day) AS last_day FROM paper_snapshots WHERE mode = ?",
            (mode,),
        )
        row = cursor.fetchone()
        if row and row["last_day"]:
            return date.fromisoformat(row["last_day"])
        return None

    def get_paper_snapshots(
        self,
        mode: str,
        *,
        since: date | None = None,
        until: date | None = None,
    ) -> list[dict[str, Any]]:
        """Return EOD snapshots for *mode* ordered by trading_day ASC."""
        clauses = ["mode = ?"]
        params: list[Any] = [mode]
        if since is not None:
            clauses.append("trading_day >= ?")
            params.append(since.isoformat())
        if until is not None:
            clauses.append("trading_day <= ?")
            params.append(until.isoformat())
        where = " AND ".join(clauses)
        cursor = self._conn.execute(
            f"""
            SELECT trading_day, equity_total, equity_short, equity_long,
                   short_cash, cash, realized_pnl_total, unrealized_pnl_total,
                   costs_day, mv_us, mv_ar, short_monthly_peak,
                   short_monthly_drawdown, short_daily_return,
                   kill_switch_active, num_open_positions, num_fills_today,
                   realized_pnl_day, created_at
            FROM paper_snapshots
            WHERE {where}
            ORDER BY trading_day ASC
            """,
            params,
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_portfolio_meta(self, mode: str) -> PortfolioMeta | None:
        """Return persisted inception capital for *mode*, or None if never initialized."""
        cursor = self._conn.execute(
            "SELECT mode, starting_cash, currency, inception_date FROM portfolio_meta WHERE mode = ?",
            (mode,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return PortfolioMeta(
            mode=str(row["mode"]),
            starting_cash=float(row["starting_cash"]),
            currency=str(row["currency"]),
            inception_date=date.fromisoformat(row["inception_date"]),
        )

    def insert_portfolio_meta(self, meta: PortfolioMeta) -> None:
        """Persist portfolio inception metadata (first run only)."""
        created_at = datetime.now(tz=timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO portfolio_meta (mode, starting_cash, currency, inception_date, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    meta.mode,
                    float(meta.starting_cash),
                    meta.currency.upper(),
                    meta.inception_date.isoformat(),
                    created_at,
                ),
            )

    def ensure_portfolio_meta(
        self,
        mode: str,
        starting_cash: float,
        currency: str,
        inception_date: date,
        *,
        allow_legacy_init: bool = False,
    ) -> PortfolioMeta:
        """Validate CLI capital against DB or initialize on first run."""
        if starting_cash < 0:
            raise ValueError("starting_cash must be >= 0")
        currency_norm = currency.upper()
        if currency_norm not in {"ARS", "USD"}:
            raise ValueError(f"unsupported currency: {currency!r}")

        existing = self.get_portfolio_meta(mode)
        if existing is None:
            if self.get_last_snapshot_day(mode) is not None and not allow_legacy_init:
                raise PortfolioMetaConflictError(
                    f"portfolio_meta missing for mode={mode} but snapshots exist; "
                    "pass --init-portfolio-meta with --initial-cash and --currency "
                    "matching historical inception (one-time legacy bootstrap)"
                )
            if self.get_last_snapshot_day(mode) is not None:
                logger.warning(
                    "Legacy bootstrap: initializing portfolio_meta on existing snapshots "
                    "mode=%s starting_cash=%s %s",
                    mode,
                    starting_cash,
                    currency_norm,
                )
            meta = PortfolioMeta(
                mode=mode,
                starting_cash=float(starting_cash),
                currency=currency_norm,
                inception_date=inception_date,
            )
            self.insert_portfolio_meta(meta)
            logger.info(
                "Initialized portfolio_meta mode=%s starting_cash=%s %s inception=%s",
                mode,
                meta.starting_cash,
                meta.currency,
                meta.inception_date.isoformat(),
            )
            return meta

        if abs(existing.starting_cash - float(starting_cash)) > 1e-6:
            raise PortfolioMetaConflictError(
                f"starting_cash mismatch for mode={mode}: "
                f"DB has {existing.starting_cash}, CLI passed {starting_cash}"
            )
        if existing.currency != currency_norm:
            raise PortfolioMetaConflictError(
                f"currency mismatch for mode={mode}: "
                f"DB has {existing.currency}, CLI passed {currency_norm}"
            )
        return existing

    def get_paper_fills(
        self,
        mode: str,
        since: date | None = None,
    ) -> list[dict[str, Any]]:
        """Return fills for mode ordered by (trading_day ASC, id ASC)."""
        if since is not None:
            cursor = self._conn.execute(
                """
                SELECT * FROM paper_fills
                WHERE mode = ? AND trading_day >= ?
                ORDER BY trading_day ASC, id ASC
                """,
                (mode, since.isoformat()),
            )
        else:
            cursor = self._conn.execute(
                """
                SELECT * FROM paper_fills
                WHERE mode = ?
                ORDER BY trading_day ASC, id ASC
                """,
                (mode,),
            )
        return [dict(row) for row in cursor.fetchall()]

    def replay_ledger_from_fills(
        self,
        mode: str = "paper_live",
        starting_cash: float = 1000.0,
    ) -> "PortfolioLedger":
        """Reconstruct a PortfolioLedger by replaying historical fills in order."""
        from core_sim.ledger import PortfolioLedger

        ledger = PortfolioLedger(starting_cash=starting_cash)
        rows = self.get_paper_fills(mode=mode)
        by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_day[row["trading_day"]].append(row)
        for day_str in sorted(by_day):
            day = date.fromisoformat(day_str)
            fills = [
                {
                    "symbol": r["symbol"],
                    "side": r["side"],
                    "qty": r["qty"],
                    "price": r["price"],
                    "market": next(
                        (k for k, v in _VENUE_MAP.items() if v == r["venue"]),
                        r["venue"],
                    ),
                    "bucket": r["bucket"],
                    "fee": r["fee"],
                }
                for r in by_day[day_str]
            ]
            ledger.apply_fills(day, fills)
        return ledger

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
