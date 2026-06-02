"""CLI script: migrate legacy ``venue='AR'`` rows in ``ohlcv`` to ``venue='XBUE'``.

``data/connectors/ar_connector.py`` previously wrote ``venue="AR"`` for BYMA bars;
the canonical label across calendars, validation and ``get_ohlcv`` is ``"XBUE"``.

Behavior (idempotent, single transaction):

1. For every ``(symbol, ts)`` pair that has BOTH ``venue='AR'`` and ``venue='XBUE'``,
   DELETE the ``AR`` row. The ``XBUE`` row is canonical.
2. UPDATE the remaining ``venue='AR'`` rows to ``venue='XBUE'``.

Usage::

    python scripts/migrate_venue_ar_to_xbue.py
    python scripts/migrate_venue_ar_to_xbue.py --db data/market.db
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)

_DEFAULT_DB = "data/market.db"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate ohlcv rows from venue='AR' to venue='XBUE' (idempotent)."
    )
    parser.add_argument(
        "--db",
        default=_DEFAULT_DB,
        help=f"Path to the SQLite MarketDB (default: {_DEFAULT_DB}).",
    )
    return parser.parse_args(argv)


def migrate(db_path: str) -> tuple[int, int]:
    """Run the venue migration. Returns ``(rows_updated, rows_dropped_pk_conflict)``."""
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT ar.symbol, ar.ts
                FROM ohlcv AS ar
                INNER JOIN ohlcv AS xbue
                    ON ar.symbol = xbue.symbol
                   AND ar.ts = xbue.ts
                WHERE ar.venue = 'AR' AND xbue.venue = 'XBUE'
                """
            )
            conflicts = cur.fetchall()
            for symbol, ts in conflicts:
                cur.execute(
                    "DELETE FROM ohlcv WHERE symbol = ? AND ts = ? AND venue = 'AR'",
                    (symbol, ts),
                )
            rows_dropped = len(conflicts)

            cur.execute("UPDATE ohlcv SET venue = 'XBUE' WHERE venue = 'AR'")
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
        "event": "venue_migration_ar_to_xbue",
        "db": args.db,
        "rows_updated": rows_updated,
        "rows_dropped_pk_conflict": rows_dropped,
    }
    logger.info(json.dumps(payload))
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
