"""Tests for scripts/migrate_venue_ar_to_xbue.py."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.storage import MarketDB  # noqa: E402
from scripts.migrate_venue_ar_to_xbue import main, migrate  # noqa: E402


def _make_db(tmp_path: Path, rows: list[tuple]) -> Path:
    db_path = tmp_path / "market.db"
    db = MarketDB(str(db_path))
    with db._conn:
        for symbol, ts, venue in rows:
            db._conn.execute(
                """
                INSERT INTO ohlcv
                    (symbol, ts, open, high, low, close, volume, currency, venue, imputed)
                VALUES (?, ?, 1.0, 2.0, 0.5, 1.5, 100.0, 'ARS', ?, 0)
                """,
                (symbol, ts, venue),
            )
    return db_path


def _all_rows(db_path: Path) -> list[tuple]:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("SELECT symbol, ts, venue FROM ohlcv ORDER BY symbol, ts, venue")
        return cur.fetchall()
    finally:
        conn.close()


def test_migrate_relabels_ar_rows_to_xbue(tmp_path):
    db_path = _make_db(
        tmp_path,
        [
            ("GGAL", "2024-01-15", "AR"),
            ("SPY", "2024-01-15", "AR"),
        ],
    )
    rows_updated, rows_dropped = migrate(str(db_path))
    assert rows_updated == 2
    assert rows_dropped == 0
    assert _all_rows(db_path) == [
        ("GGAL", "2024-01-15", "XBUE"),
        ("SPY", "2024-01-15", "XBUE"),
    ]


def test_migrate_drops_ar_when_xbue_duplicate_exists(tmp_path):
    db_path = _make_db(
        tmp_path,
        [
            ("GGAL", "2024-01-15", "AR"),
            ("GGAL", "2024-01-15", "XBUE"),
            ("GGAL", "2024-01-16", "AR"),
        ],
    )
    rows_updated, rows_dropped = migrate(str(db_path))
    assert rows_dropped == 1
    assert rows_updated == 1
    assert _all_rows(db_path) == [
        ("GGAL", "2024-01-15", "XBUE"),
        ("GGAL", "2024-01-16", "XBUE"),
    ]


def test_migrate_is_idempotent(tmp_path):
    db_path = _make_db(tmp_path, [("GGAL", "2024-01-15", "AR")])
    assert migrate(str(db_path)) == (1, 0)
    assert migrate(str(db_path)) == (0, 0)


def test_main_exits_zero(tmp_path, capsys):
    db_path = _make_db(tmp_path, [("GGAL", "2024-01-15", "AR")])
    assert main(["--db", str(db_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["event"] == "venue_migration_ar_to_xbue"
    assert out["rows_updated"] == 1
