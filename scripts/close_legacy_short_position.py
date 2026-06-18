#!/usr/bin/env python3
"""Cierra posiciones SHORT legacy que chocan con el sleeve largo (ADR-064).

El corto viejo (momentum) dejó posiciones en el bucket short (p. ej. KO en XNYS). El largo
diversificado ahora quiere esos símbolos en el bucket long. El ledger prohíbe el mismo
símbolo en dos buckets → el largo queda salteado por el freno defensivo. Este script cierra
las posiciones short conflictivas con un SELL al último cierre de su venue, para liberar el
símbolo. Auditable (engine=cleanup_legacy_short) e idempotente (no hace nada si ya está en 0).

Uso::
  python scripts/close_legacy_short_position.py --db data/market.db            # dry-run
  python scripts/close_legacy_short_position.py --db data/market.db --apply
"""
from __future__ import annotations

import argparse
import sys
import uuid
from datetime import date
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.storage import MarketDB, _VENUE_MAP  # noqa: E402

PAPER_LIVE_MODE = "paper_live"


def _long_symbols(policy_doc: dict) -> set[str]:
    lt = policy_doc.get("long_term_engine", {})
    return {
        str(it["symbol"]).strip().upper()
        for it in (lt.get("core_lines", []) + lt.get("satellite_lines", []))
    }


def _net_short_positions(db: MarketDB) -> dict[str, dict]:
    """Por símbolo en bucket short: net_qty y venue (del último fill)."""
    rows = db._conn.execute(
        """
        SELECT symbol, venue,
               SUM(CASE WHEN side='BUY' THEN qty ELSE -qty END) AS net_qty
        FROM paper_fills WHERE mode=? AND bucket='short'
        GROUP BY symbol, venue
        """,
        (PAPER_LIVE_MODE,),
    ).fetchall()
    out: dict[str, dict] = {}
    for sym, venue, net in rows:
        if net and net > 1e-9:
            out[str(sym).upper()] = {"venue": venue, "net_qty": float(net)}
    return out


def _last_close(db: MarketDB, symbol: str, venue: str) -> float | None:
    r = db._conn.execute(
        "SELECT close FROM ohlcv WHERE symbol=? AND venue=? ORDER BY ts DESC LIMIT 1",
        (symbol, venue),
    ).fetchone()
    return float(r[0]) if r else None


def main() -> int:
    p = argparse.ArgumentParser(description="Cierra posiciones short legacy en conflicto con el largo")
    p.add_argument("--db", default="data/market.db")
    p.add_argument("--date", type=lambda s: date.fromisoformat(s), default=date.today())
    p.add_argument("--apply", action="store_true", help="Sin esto, solo dry-run")
    args = p.parse_args()

    db = MarketDB(args.db)
    policy_doc = yaml.safe_load((REPO_ROOT / "config" / "policy.v1.yaml").open(encoding="utf-8"))
    long_syms = _long_symbols(policy_doc)
    short_pos = _net_short_positions(db)
    conflicts = {s: info for s, info in short_pos.items() if s in long_syms}

    if not conflicts:
        print("OK: no hay posiciones short en conflicto con el largo. Nada que cerrar.")
        return 0

    print(f"Posiciones short en conflicto con el largo: {sorted(conflicts)}")
    for sym, info in sorted(conflicts.items()):
        venue = info["venue"]
        qty = info["net_qty"]
        px = _last_close(db, sym, venue)
        if px is None or px <= 0:
            print(f"  [!] {sym}: sin precio en {venue} — NO se cierra.")
            continue
        market = next((k for k, v in _VENUE_MAP.items() if v == venue), venue)
        print(f"  {sym}: vender {qty:.2f} @ {px} ({venue}/{market}) bucket short")
        if args.apply:
            run_id = f"cleanup_{sym}_{uuid.uuid4().hex[:8]}"
            db.persist_fills(
                run_id, PAPER_LIVE_MODE, args.date,
                [{
                    "symbol": sym, "side": "SELL", "qty": qty, "price": px,
                    "market": market, "bucket": "short", "fee": 0.0,
                    "reason": "adr064_bucket_conflict_cleanup",
                }],
                engine="cleanup_legacy_short",
            )
            print(f"    -> fill de cierre insertado (run_id={run_id})")

    if not args.apply:
        print("\nDRY-RUN. Re-correr con --apply para insertar los fills de cierre.")
    else:
        print("\nLimpieza aplicada. El largo ya puede operar esos símbolos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
