"""CLI script: back-adjust OHLCV series for a CEDEAR ratio change (idempotent).

Caso origen: el CEDEAR de SPY (XBUE) cambió de ratio el 2026-05-29 — el close
pasó de 56.000 a 18.750 ARS (factor 3.0) sin que la serie almacenada se
ajustara. Una caida nominal de ~3x sin evento registrado contamina cualquier
valuacion o señal que cruce la fecha ex.

Behavior (idempotent, single transaction):

1. Registra el evento en ``corporate_actions`` con ``type='cedear_ratio'``.
   Si ya existe ese registro, el script sale sin tocar nada (ya aplicado).
   El tipo ``cedear_ratio`` es inerte para los motores: el runner largo solo
   ajusta qty para ``action_type == 'split'``.
2. Back-adjust de todas las filas con ``ts < ex_date``: OHLC ÷ factor,
   volume × factor (mas nominales en circulacion, precios menores).

Usage::

    python scripts/adjust_cedear_ratio.py --symbol SPY --ex-date 2026-05-29 --factor 3.0
    python scripts/adjust_cedear_ratio.py --symbol SPY --venue XBUE --ex-date 2026-05-29 --factor 3.0 --db data/market.db
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)

_DEFAULT_DB = "data/market.db"
_ACTION_TYPE = "cedear_ratio"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Back-adjust OHLCV for a CEDEAR ratio change (idempotent)."
    )
    parser.add_argument("--symbol", required=True, help="Simbolo CEDEAR (ej: SPY).")
    parser.add_argument(
        "--venue", default="XBUE", help="Venue de la serie a ajustar (default: XBUE)."
    )
    parser.add_argument(
        "--ex-date",
        required=True,
        help="Fecha ex del cambio de ratio (YYYY-MM-DD). Las filas ANTERIORES se ajustan.",
    )
    parser.add_argument(
        "--factor",
        type=float,
        required=True,
        help="Factor del cambio (ej: 3.0 para 1:3 — precios pre ÷ 3, volumen × 3).",
    )
    parser.add_argument(
        "--db",
        default=_DEFAULT_DB,
        help=f"Path to the SQLite MarketDB (default: {_DEFAULT_DB}).",
    )
    return parser.parse_args(argv)


def adjust(
    db_path: str,
    symbol: str,
    venue: str,
    ex_date: date,
    factor: float,
) -> tuple[int, bool]:
    """Run the ratio back-adjustment.

    Returns ``(rows_adjusted, already_applied)``. When the action is already
    registered in ``corporate_actions``, returns ``(0, True)`` without touching
    the OHLCV series.
    """
    if factor <= 0:
        raise ValueError(f"factor must be > 0, got {factor}")

    symbol = symbol.strip().upper()
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM corporate_actions WHERE symbol = ? AND ts = ? AND type = ?",
                (symbol, ex_date.isoformat(), _ACTION_TYPE),
            )
            if cur.fetchone() is not None:
                return 0, True

            cur.execute(
                "INSERT INTO corporate_actions (symbol, ts, type, factor) VALUES (?, ?, ?, ?)",
                (symbol, ex_date.isoformat(), _ACTION_TYPE, factor),
            )
            cur.execute(
                """
                UPDATE ohlcv
                SET open   = open / :f,
                    high   = high / :f,
                    low    = low / :f,
                    close  = close / :f,
                    volume = volume * :f
                WHERE symbol = :symbol AND venue = :venue AND ts < :ex_date
                """,
                {
                    "f": factor,
                    "symbol": symbol,
                    "venue": venue,
                    "ex_date": ex_date.isoformat(),
                },
            )
            rows_adjusted = cur.rowcount or 0
        return rows_adjusted, False
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if not Path(args.db).exists():
        logger.error(
            '{"event": "cedear_ratio_adjust_failed", "db": "%s", "error": "db_not_found"}',
            args.db,
        )
        print(json.dumps({"error": "db_not_found", "db": args.db}))
        return 2

    try:
        ex_date = date.fromisoformat(args.ex_date)
    except ValueError:
        print(json.dumps({"error": "invalid_ex_date", "ex_date": args.ex_date}))
        return 2

    try:
        rows_adjusted, already_applied = adjust(
            args.db, args.symbol, args.venue, ex_date, args.factor
        )
    except (sqlite3.Error, ValueError) as exc:
        logger.error(
            '{"event": "cedear_ratio_adjust_failed", "db": "%s", "error": "%s"}',
            args.db,
            exc,
        )
        print(json.dumps({"error": str(exc), "db": args.db}))
        return 1

    payload = {
        "event": "cedear_ratio_adjusted",
        "db": args.db,
        "symbol": args.symbol.strip().upper(),
        "venue": args.venue,
        "ex_date": ex_date.isoformat(),
        "factor": args.factor,
        "rows_adjusted": rows_adjusted,
        "already_applied": already_applied,
    }
    logger.info(json.dumps(payload))
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
