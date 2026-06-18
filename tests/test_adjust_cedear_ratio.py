"""Tests for scripts/adjust_cedear_ratio.py (back-adjust por cambio de ratio CEDEAR)."""

from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.storage import MarketDB  # noqa: E402
from scripts.adjust_cedear_ratio import adjust, main  # noqa: E402

_EX_DATE = date(2026, 5, 29)


def _make_db(tmp_path: Path, rows: list[tuple[str, str, float, float, str]]) -> Path:
    """rows: (symbol, ts, close, volume, venue) — open/high/low derivados de close."""
    db_path = tmp_path / "market.db"
    db = MarketDB(str(db_path))
    with db._conn:
        for symbol, ts, close, volume, venue in rows:
            db._conn.execute(
                """
                INSERT INTO ohlcv
                    (symbol, ts, open, high, low, close, volume, currency, venue, imputed)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'ARS', ?, 0)
                """,
                (symbol, ts, close, close * 1.01, close * 0.99, close, volume, venue),
            )
    return db_path


def _closes(db_path: Path, symbol: str, venue: str) -> list[tuple[str, float, float]]:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "SELECT ts, close, volume FROM ohlcv WHERE symbol = ? AND venue = ? ORDER BY ts",
            (symbol, venue),
        )
        return cur.fetchall()
    finally:
        conn.close()


def test_adjust_divides_pre_ex_date_prices_and_multiplies_volume(tmp_path):
    db_path = _make_db(
        tmp_path,
        [
            ("SPY", "2026-05-27", 55650.0, 100.0, "XBUE"),
            ("SPY", "2026-05-28", 56000.0, 100.0, "XBUE"),
            ("SPY", "2026-05-29", 18750.0, 300.0, "XBUE"),
        ],
    )
    rows_adjusted, already = adjust(str(db_path), "SPY", "XBUE", _EX_DATE, 3.0)
    assert rows_adjusted == 2
    assert already is False

    series = _closes(db_path, "SPY", "XBUE")
    assert series[0] == ("2026-05-27", 18550.0, 300.0)
    assert series[1] == ("2026-05-28", pytest.approx(56000.0 / 3), 300.0)
    # La fila del ex-date NO se toca.
    assert series[2] == ("2026-05-29", 18750.0, 300.0)


def test_adjust_is_idempotent_via_corporate_actions_marker(tmp_path):
    db_path = _make_db(tmp_path, [("SPY", "2026-05-28", 56000.0, 100.0, "XBUE")])

    assert adjust(str(db_path), "SPY", "XBUE", _EX_DATE, 3.0) == (1, False)
    # Segunda corrida: marker presente, serie intacta.
    assert adjust(str(db_path), "SPY", "XBUE", _EX_DATE, 3.0) == (0, True)
    assert _closes(db_path, "SPY", "XBUE")[0][1] == pytest.approx(56000.0 / 3)


def test_adjust_registers_inert_corporate_action(tmp_path):
    db_path = _make_db(tmp_path, [("SPY", "2026-05-28", 56000.0, 100.0, "XBUE")])
    adjust(str(db_path), "SPY", "XBUE", _EX_DATE, 3.0)

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT symbol, ts, type, factor FROM corporate_actions"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("SPY", "2026-05-29", "cedear_ratio", 3.0)


def test_adjust_does_not_touch_other_symbols_or_venues(tmp_path):
    db_path = _make_db(
        tmp_path,
        [
            ("SPY", "2026-05-28", 56000.0, 100.0, "XBUE"),
            ("SPY", "2026-05-28", 684.0, 100.0, "XNYS"),
            ("GGAL", "2026-05-28", 7000.0, 100.0, "XBUE"),
        ],
    )
    rows_adjusted, _ = adjust(str(db_path), "SPY", "XBUE", _EX_DATE, 3.0)
    assert rows_adjusted == 1
    assert _closes(db_path, "SPY", "XNYS")[0][1] == 684.0
    assert _closes(db_path, "GGAL", "XBUE")[0][1] == 7000.0


def test_adjust_rejects_non_positive_factor(tmp_path):
    db_path = _make_db(tmp_path, [("SPY", "2026-05-28", 56000.0, 100.0, "XBUE")])
    with pytest.raises(ValueError):
        adjust(str(db_path), "SPY", "XBUE", _EX_DATE, 0.0)


def test_main_exit_codes(tmp_path, capsys):
    assert main(["--symbol", "SPY", "--ex-date", "2026-05-29", "--factor", "3.0",
                 "--db", str(tmp_path / "missing.db")]) == 2

    db_path = _make_db(tmp_path, [("SPY", "2026-05-28", 56000.0, 100.0, "XBUE")])
    assert main(["--symbol", "SPY", "--ex-date", "not-a-date", "--factor", "3.0",
                 "--db", str(db_path)]) == 2
    assert main(["--symbol", "SPY", "--ex-date", "2026-05-29", "--factor", "3.0",
                 "--db", str(db_path)]) == 0
