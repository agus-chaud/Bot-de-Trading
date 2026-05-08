"""CLI script: migrate legacy ``venue='US'`` rows in ``ohlcv`` to ``venue='XNYS'``.

The connector ``data/connectors/us_connector.py`` was previously writing
``venue="US"`` for NYSE bars; the canonical label across the rest of the data
layer (calendars, schema docs, joins) is the ISO MIC ``"XNYS"``. This script
brings any pre-existing local DB into line with that convention.

Behavior (idempotent, single transaction):

1. For every ``(symbol, ts)`` pair that has BOTH a ``venue='US'`` row and a
   ``venue='XNYS'`` row, DELETE the ``US`` row. The ``XNYS`` row is canonical.
2. UPDATE the remaining ``venue='US'`` rows to ``venue='XNYS'``.

Outputs a structured JSON line via stdlib ``logging`` with row counts.
Exits 0 on success (including a clean DB with nothing to migrate), non-zero on
``sqlite3.Error`` or invalid ``--db`` path.

Usage::

    python scripts/migrate_venue_us_to_xnys.py
    python scripts/migrate_venue_us_to_xnys.py --db data/market.db
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

# Allow running as a script (``python scripts/migrate_venue_us_to_xnys.py``).
sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)

_DEFAULT_DB = "data/market.db"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate ohlcv rows from venue='US' to venue='XNYS' (idempotent)."
    )
    parser.add_argument(
        "--db",
        default=_DEFAULT_DB,
        help=f"Path to the SQLite MarketDB (default: {_DEFAULT_DB}).",
    )
    return parser.parse_args(argv)


def migrate(db_path: str) -> tuple[int, int]:
    """Run the venue migration against the SQLite DB at *db_path*.

    Returns a tuple ``(rows_updated, rows_dropped_pk_conflict)``. Both counts
    are 0 on a fully migrated (or empty) DB — the operation is idempotent.

    Raises:
        sqlite3.Error: on any DB-level failure (caller decides exit code).
    """
    conn = sqlite3.connect(db_path)
    try:
        with conn:  # implicit transaction; commit on success, rollback on error
            cur = conn.cursor()

            # 1. Identify (symbol, ts) pairs that have BOTH labels — drop the US row.
            cur.execute(
                """
                SELECT us.symbol, us.ts
                FROM ohlcv AS us
                INNER JOIN ohlcv AS xnys
                    ON us.symbol = xnys.symbol
                   AND us.ts = xnys.ts
                WHERE us.venue = 'US' AND xnys.venue = 'XNYS'
                """
            )
            conflicts = cur.fetchall()
            for symbol, ts in conflicts:
                cur.execute(
                    "DELETE FROM ohlcv WHERE symbol = ? AND ts = ? AND venue = 'US'",
                    (symbol, ts),
                )
            rows_dropped = len(conflicts)

            # 2. Relabel any remaining US rows.
            cur.execute("UPDATE ohlcv SET venue = 'XNYS' WHERE venue = 'US'")
            rows_updated = cur.rowcount or 0

        return rows_updated, rows_dropped
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if not Path(args.db).exists():
        logger.error(
            '{"event": "venue_migration_failed", "db": "%s", "error": "db_not_found"}',
            args.db,
        )
        # Mirror the structured-log style on stdout for CLI consumers.
        print(json.dumps({"error": "db_not_found", "db": args.db}))
        return 2

    try:
        rows_updated, rows_dropped = migrate(args.db)
    except sqlite3.Error as exc:
        logger.error(
            '{"event": "venue_migration_failed", "db": "%s", "error": "%s"}',
            args.db,
            exc,
        )
        print(json.dumps({"error": str(exc), "db": args.db}))
        return 1

    payload = {
        "event": "venue_migration",
        "db": args.db,
        "rows_updated": rows_updated,
        "rows_dropped_pk_conflict": rows_dropped,
    }
    logger.info(json.dumps(payload))
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
