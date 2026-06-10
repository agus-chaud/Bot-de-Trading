#!/usr/bin/env python3
"""What-if portfolio simulation over historical OHLCV (READ-ONLY on source DB).

Runs the full short + long (30/70) paper-trading pipeline day-by-day from a start
date, on an isolated *copy* of market.db, so production `paper_live` data is never
touched. Reports what the bot would have bought and the resulting equity/P&L.

This is a what-if analysis tool, NOT the production paper-live runner. It bypasses
the F3 catch-up gate on purpose (a multi-month backtest is intentional here), and
each scenario gets its own throwaway DB seeded only with OHLCV + calendar data.

Usage::

  python scripts/run_whatif_sim.py
  python scripts/run_whatif_sim.py --start 2026-03-01 --end 2026-06-09
  python scripts/run_whatif_sim.py --scenario 500000:500k --scenario 1000000:1m
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core_sim.short_term_day_runner import load_merged_whitelist  # noqa: E402
from data.storage import MarketDB  # noqa: E402
from scripts.run_paper_live import (  # noqa: E402
    PAPER_LIVE_MODE,
    _VENUE_MAP,
    _overlay_ar_long_sleeve_bars_from_db,
    compute_trading_days_gap,
    load_required_calendar_store,
    run_catch_up,
)

logger = logging.getLogger("whatif_sim")

# AR (XBUE) OHLCV currently ends 2026-06-02; running past it would value ARS
# positions at USD (XNYS) fallback prices. Keep the default end on a complete AR day.
DEFAULT_START = date(2026, 3, 1)
DEFAULT_END = date(2026, 6, 2)
SIM_DIR = REPO_ROOT / "data" / "_sim"


def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise SystemExit(f"Invalid date {raw!r} (expected YYYY-MM-DD)") from exc


def _parse_scenario(raw: str) -> tuple[float, str]:
    """Parse 'amount:label' (e.g. '500000:500k')."""
    amount_str, _, label = raw.partition(":")
    try:
        amount = float(amount_str)
    except ValueError as exc:
        raise SystemExit(f"Invalid scenario amount in {raw!r}") from exc
    return amount, (label or amount_str)


def _prepare_sim_db(source_db: Path, label: str) -> Path:
    """Copy source DB to an isolated sim file and wipe any paper trading state."""
    SIM_DIR.mkdir(parents=True, exist_ok=True)
    sim_path = SIM_DIR / f"sim_{label}.db"
    if sim_path.exists():
        sim_path.unlink()
    shutil.copy2(source_db, sim_path)

    db = MarketDB(str(sim_path))
    with db._conn:
        db._conn.execute("DELETE FROM paper_snapshots")
        db._conn.execute("DELETE FROM paper_fills")
        db._conn.execute("DELETE FROM portfolio_meta")
    return sim_path


def _end_day_bars(
    db: MarketDB,
    end_day: date,
    merged_whitelist: dict[str, str],
    policy_doc: dict[str, Any],
) -> dict[str, dict[str, float]]:
    """Build end-day bars and apply the XBUE overlay (same as the live runner).

    Without the overlay, dual-listed CEDEAR positions (e.g. SPY) bought in ARS would
    be revalued at their USD (XNYS) close — mixing currencies. This mirrors the
    valuation the run itself used (ADR-048).
    """
    bars: dict[str, dict[str, float]] = {}
    for sym, market in merged_whitelist.items():
        venue = _VENUE_MAP.get(market, market)
        rows = db.get_ohlcv(sym, end_day, end_day, venue)
        if rows:
            last = rows[-1]
            bars[sym] = {
                "open": last.open,
                "high": last.high,
                "low": last.low,
                "close": last.close,
                "volume": last.volume,
            }
    _overlay_ar_long_sleeve_bars_from_db(db, end_day, policy_doc, bars)
    return bars


def _last_persisted_snapshot(db: MarketDB) -> dict[str, Any] | None:
    cur = db._conn.execute(
        "SELECT * FROM paper_snapshots WHERE mode = ? ORDER BY trading_day DESC LIMIT 1",
        (PAPER_LIVE_MODE,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _fmt_money(value: float, currency: str) -> str:
    return f"{value:,.0f} {currency}"


def _summarize(
    db: MarketDB,
    initial_cash: float,
    currency: str,
    start: date,
    end: date,
    policy_doc: dict[str, Any],
) -> dict[str, Any]:
    """Build a human-readable summary from persisted fills + final ledger state."""
    fills = db.get_paper_fills(PAPER_LIVE_MODE)
    merged_whitelist = load_merged_whitelist(REPO_ROOT, policy_doc)

    # Headline equity comes from the snapshot the run persisted on the last day
    # (computed with the XBUE overlay). Positions detail is recomputed with the
    # same overlay so dual-listed names are valued in ARS, not USD.
    ledger = db.replay_ledger_from_fills(PAPER_LIVE_MODE, starting_cash=initial_cash)
    bars = _end_day_bars(db, end, merged_whitelist, policy_doc)
    snap = ledger.mark_to_market(trading_day=end, daily_bars=bars)

    persisted = _last_persisted_snapshot(db)
    if persisted is not None:
        equity_total = float(persisted["equity_total"])
        realized_pnl = float(persisted["realized_pnl_total"])
        unrealized_pnl = float(persisted["unrealized_pnl_total"])
        cash_end = float(persisted["cash"])
        equity_short = float(persisted["equity_short"])
        equity_long = float(persisted["equity_long"])
    else:
        equity_total = float(snap["equity_total"])
        realized_pnl = float(snap.get("realized_pnl_total", 0.0))
        unrealized_pnl = float(snap.get("unrealized_pnl_total", 0.0))
        cash_end = float(snap.get("cash", 0.0))
        equity_short = float(snap.get("equity_short", 0.0))
        equity_long = float(snap.get("equity_long", 0.0))

    abs_return = equity_total - initial_cash
    pct_return = (abs_return / initial_cash * 100.0) if initial_cash else 0.0

    positions = snap.get("positions") or {}
    total_costs = sum(float(f.get("cost_total") or f.get("fee") or 0.0) for f in fills)

    buys = [f for f in fills if str(f["side"]).upper() == "BUY"]
    sells = [f for f in fills if str(f["side"]).upper() == "SELL"]
    invested_by_symbol: dict[str, float] = {}
    for f in buys:
        invested_by_symbol[f["symbol"]] = invested_by_symbol.get(f["symbol"], 0.0) + (
            float(f["qty"]) * float(f["price"])
        )

    return {
        "initial_cash": initial_cash,
        "currency": currency,
        "equity_total": equity_total,
        "abs_return": abs_return,
        "pct_return": pct_return,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "cash_end": cash_end,
        "equity_short": equity_short,
        "equity_long": equity_long,
        "total_costs": total_costs,
        "num_fills": len(fills),
        "num_buys": len(buys),
        "num_sells": len(sells),
        "positions": positions,
        "invested_by_symbol": invested_by_symbol,
        "fills": fills,
    }


def _print_report(label: str, s: dict[str, Any], start: date, end: date) -> None:
    cur = s["currency"]
    print("\n" + "=" * 72)
    print(f"  SIMULACION '{label}'  |  {start} -> {end}")
    print("=" * 72)
    print(f"  Capital inicial : {_fmt_money(s['initial_cash'], cur)}")
    print(f"  Equity final    : {_fmt_money(s['equity_total'], cur)}")
    sign = "+" if s["abs_return"] >= 0 else ""
    print(
        f"  Resultado       : {sign}{_fmt_money(s['abs_return'], cur)} "
        f"({sign}{s['pct_return']:.2f}%)"
    )
    print(f"    - PnL realizado   : {_fmt_money(s['realized_pnl'], cur)}")
    print(f"    - PnL no realizado: {_fmt_money(s['unrealized_pnl'], cur)}")
    print(f"    - Costos totales  : {_fmt_money(s['total_costs'], cur)}")
    print(f"  Caja al final   : {_fmt_money(s['cash_end'], cur)}")
    print(
        f"  Sleeves         : corto {_fmt_money(s['equity_short'], cur)} | "
        f"largo {_fmt_money(s['equity_long'], cur)}"
    )
    print(
        f"  Operaciones     : {s['num_fills']} fills "
        f"({s['num_buys']} compras / {s['num_sells']} ventas)"
    )

    positions = s["positions"]
    if positions:
        print("\n  POSICIONES ABIERTAS AL CIERRE:")
        print(
            f"    {'Simbolo':<10}{'Bucket':<7}{'Mkt':<5}{'Cant':>10}"
            f"{'Costo prom':>14}{'Valor mkt':>16}{'PnL no real':>16}"
        )
        for sym in sorted(positions):
            p = positions[sym]
            print(
                f"    {sym:<10}{str(p.get('bucket','')):<7}{str(p.get('market','')):<5}"
                f"{float(p.get('qty',0)):>10.2f}{float(p.get('avg_cost',0)):>14.2f}"
                f"{float(p.get('market_value',0)):>16.2f}{float(p.get('unrealized_pnl',0)):>16.2f}"
            )
    else:
        print("\n  POSICIONES ABIERTAS AL CIERRE: ninguna")

    if s["invested_by_symbol"]:
        print("\n  CAPITAL COMPRADO POR SIMBOLO (suma de compras, bruto):")
        for sym, amt in sorted(
            s["invested_by_symbol"].items(), key=lambda kv: kv[1], reverse=True
        ):
            print(f"    {sym:<10}{_fmt_money(amt, cur):>22}")


def _write_artifacts(label: str, s: dict[str, Any]) -> Path:
    """Persist fills CSV for later inspection in the sim dir."""
    import csv

    out = SIM_DIR / f"sim_{label}_fills.csv"
    fills = s["fills"]
    if fills:
        cols = list(fills[0].keys())
        with out.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=cols)
            writer.writeheader()
            writer.writerows(fills)
    return out


def run_one(
    label: str,
    initial_cash: float,
    *,
    source_db: Path,
    policy_doc: dict[str, Any],
    start: date,
    end: date,
    currency: str,
) -> dict[str, Any]:
    sim_db_path = _prepare_sim_db(source_db, label)
    db = MarketDB(str(sim_db_path))
    calendar_store = load_required_calendar_store(policy_doc)

    gap_days = compute_trading_days_gap(
        start - timedelta(days=1), end, calendar_store=calendar_store
    )
    if not gap_days:
        raise SystemExit("No trading days in the requested range")

    logger.info(
        "[%s] simulando %d dias operativos (%s -> %s), capital %s %s",
        label, len(gap_days), gap_days[0], gap_days[-1], f"{initial_cash:,.0f}", currency,
    )
    run_catch_up(
        db,
        gap_days,
        policy_doc,
        initial_cash,
        currency=currency,
        calendar_store=calendar_store,
        enable_long_engine=True,
    )

    summary = _summarize(db, initial_cash, currency, gap_days[0], gap_days[-1], policy_doc)
    _print_report(label, summary, gap_days[0], gap_days[-1])
    artifact = _write_artifacts(label, summary)
    logger.info("[%s] fills guardados en %s", label, artifact)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="What-if portfolio simulation (30/70).")
    parser.add_argument("--db", type=Path, default=REPO_ROOT / "data" / "market.db")
    parser.add_argument("--policy", type=Path, default=REPO_ROOT / "config" / "policy.v1.yaml")
    parser.add_argument("--start", type=_parse_date, default=DEFAULT_START)
    parser.add_argument("--end", type=_parse_date, default=DEFAULT_END)
    parser.add_argument("--currency", type=str, default="ARS", choices=("ARS", "USD"))
    parser.add_argument(
        "--scenario",
        action="append",
        type=_parse_scenario,
        help="Repeatable 'amount:label', e.g. 500000:500k. Defaults to 500k + 1M ARS.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not args.db.is_file():
        raise SystemExit(f"Database not found: {args.db}")

    policy_doc = yaml.safe_load(args.policy.read_text(encoding="utf-8"))
    scenarios = args.scenario or [(500_000.0, "500k"), (1_000_000.0, "1M")]

    summaries: list[tuple[str, dict[str, Any]]] = []
    for amount, label in scenarios:
        summary = run_one(
            label,
            amount,
            source_db=args.db,
            policy_doc=policy_doc,
            start=args.start,
            end=args.end,
            currency=args.currency,
        )
        summaries.append((label, summary))

    if len(summaries) > 1:
        print("\n" + "=" * 72)
        print("  COMPARATIVA")
        print("=" * 72)
        print(f"  {'Escenario':<12}{'Inicial':>18}{'Final':>18}{'Retorno %':>14}")
        for label, s in summaries:
            print(
                f"  {label:<12}{s['initial_cash']:>18,.0f}{s['equity_total']:>18,.0f}"
                f"{s['pct_return']:>13.2f}%"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
