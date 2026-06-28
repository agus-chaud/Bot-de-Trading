"""Tests for scripts/migrate_venue_us_to_xnys.py — behavior-based on real SQLite files."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

from data.storage import MarketDB  # noqa: E402  (path setup above)
from scripts.migrate_venue_us_to_xnys import main, migrate  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path, rows: list[tuple]) -> Path:
    """Create a temp SQLite DB with the canonical ``ohlcv`` schema and seed *rows*.

    Each row tuple is ``(symbol, ts, venue)`` — other OHLCV fields are filled
    with placeholder numerics.
    """
    db_path = tmp_path / "market.db"
    # Use MarketDB to create the canonical schema (PK = (symbol, ts, venue)).
    db = MarketDB(str(db_path))
    conn = db._conn
    with conn:
        for symbol, ts, venue in rows:
            conn.execute(
                """
                INSERT INTO ohlcv
                    (symbol, ts, open, high, low, close, volume, currency, venue, imputed)
                VALUES (?, ?, 1.0, 2.0, 0.5, 1.5, 100.0, 'USD', ?, 0)
                """,
                (symbol, ts, venue),
            )
    return db_path


def _all_rows(db_path: Path) -> list[tuple]:
    """Return every ``(symbol, ts, venue)`` tuple in ``ohlcv``."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("SELECT symbol, ts, venue FROM ohlcv ORDER BY symbol, ts, venue")
        return cur.fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Migration logic — direct ``migrate()`` calls
# ---------------------------------------------------------------------------


def test_migrate_relabels_us_rows_to_xnys(tmp_path):
    """All standalone ``US`` rows are updated to ``XNYS`` with no row loss."""
    db_path = _make_db(
        tmp_path,
        rows=[
            ("SPY", "2024-01-15", "US"),
            ("SPY", "2024-01-16", "US"),
            ("QQQ", "2024-01-15", "US"),
        ],
    )

    rows_updated, rows_dropped = migrate(str(db_path))

    assert rows_updated == 3
    assert rows_dropped == 0
    assert _all_rows(db_path) == [
        ("QQQ", "2024-01-15", "XNYS"),
        ("SPY", "2024-01-15", "XNYS"),
        ("SPY", "2024-01-16", "XNYS"),
    ]


def test_migrate_drops_us_row_when_xnys_duplicate_exists(tmp_path):
    """When both ``US`` and ``XNYS`` exist for ``(symbol, ts)``, the ``US`` row is dropped."""
    db_path = _make_db(
        tmp_path,
        rows=[
            # PK conflict pair — both labels for same (symbol, ts).
            ("SPY", "2024-01-15", "US"),
            ("SPY", "2024-01-15", "XNYS"),
            # US-only row — should be relabeled.
            ("SPY", "2024-01-16", "US"),
            # XNYS-only row — must be untouched.
            ("QQQ", "2024-01-15", "XNYS"),
        ],
    )

    rows_updated, rows_dropped = migrate(str(db_path))

    assert rows_dropped == 1
    assert rows_updated == 1
    rows = _all_rows(db_path)
    assert rows == [
        ("QQQ", "2024-01-15", "XNYS"),
        ("SPY", "2024-01-15", "XNYS"),
        ("SPY", "2024-01-16", "XNYS"),
    ]
    # No legacy 'US' rows remain.
    assert all(venue == "XNYS" for _, _, venue in rows)


def test_migrate_is_idempotent(tmp_path):
    """Running the migration twice is safe: the second run touches nothing."""
    db_path = _make_db(
        tmp_path,
        rows=[
            ("SPY", "2024-01-15", "US"),
            ("SPY", "2024-01-15", "XNYS"),
            ("SPY", "2024-01-16", "US"),
            ("QQQ", "2024-01-15", "XNYS"),
        ],
    )

    first = migrate(str(db_path))
    snapshot_after_first = _all_rows(db_path)

    second = migrate(str(db_path))
    snapshot_after_second = _all_rows(db_path)

    assert first == (1, 1)
    assert second == (0, 0)
    assert snapshot_after_first == snapshot_after_second


def test_migrate_on_clean_db_yields_zero_counts(tmp_path):
    """A DB with only canonical ``XNYS`` rows is left untouched."""
    db_path = _make_db(
        tmp_path,
        rows=[
            ("SPY", "2024-01-15", "XNYS"),
            ("QQQ", "2024-01-15", "XNYS"),
        ],
    )

    rows_updated, rows_dropped = migrate(str(db_path))

    assert (rows_updated, rows_dropped) == (0, 0)
    assert _all_rows(db_path) == [
        ("QQQ", "2024-01-15", "XNYS"),
        ("SPY", "2024-01-15", "XNYS"),
    ]


# ---------------------------------------------------------------------------
# CLI entry point — ``main()``
# ---------------------------------------------------------------------------


def test_main_exits_zero_and_prints_summary(tmp_path, capsys):
    """``main`` returns 0 and prints a JSON payload with the migration counts."""
    db_path = _make_db(
        tmp_path,
        rows=[
            ("SPY", "2024-01-15", "US"),
            ("SPY", "2024-01-16", "US"),
        ],
    )

    code = main(["--db", str(db_path)])

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["event"] == "venue_migration"
    assert out["rows_updated"] == 2
    assert out["rows_dropped_pk_conflict"] == 0
    assert out["db"] == str(db_path)


def test_main_idempotent_second_run_via_cli(tmp_path, capsys):
    """A second CLI invocation reports zero rows changed."""
    db_path = _make_db(tmp_path, rows=[("SPY", "2024-01-15", "US")])

    assert main(["--db", str(db_path)]) == 0
    capsys.readouterr()  # discard first output

    assert main(["--db", str(db_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rows_updated"] == 0
    assert out["rows_dropped_pk_conflict"] == 0


def test_main_returns_nonzero_when_db_missing(tmp_path, capsys):
    """A non-existent ``--db`` path triggers a non-zero exit and an error payload."""
    missing = tmp_path / "does_not_exist.db"

    code = main(["--db", str(missing)])

    assert code != 0
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "db_not_found"
    assert out["db"] == str(missing)


def test_main_returns_nonzero_on_sqlite_error(tmp_path, capsys, monkeypatch):
    """A ``sqlite3.Error`` raised during migration produces a non-zero exit."""
    db_path = _make_db(tmp_path, rows=[("SPY", "2024-01-15", "US")])

    def boom(_db_path: str):
        raise sqlite3.Error("simulated failure")

    monkeypatch.setattr("scripts.migrate_venue_us_to_xnys.migrate", boom)

    code = main(["--db", str(db_path)])

    assert code == 1
    out = json.loads(capsys.readouterr().out)
    assert "simulated failure" in out["error"]
