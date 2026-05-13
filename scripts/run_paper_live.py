#!/usr/bin/env python3
"""Daily paper-live orchestrator: runs short-term pipeline on real OHLCV data.

Exit codes:
  0 — success, all days processed
  1 — runtime error (data missing, pipeline crash)
  2 — gap > 3 trading days (F3 policy), manual intervention needed
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core_sim.calendar_store import TradingCalendarStore  # noqa: E402
from core_sim.cost_model import CostModel, MarketCostConfig  # noqa: E402
from core_sim.ledger import PortfolioLedger  # noqa: E402
from core_sim.long_term_monthly_runner import create_long_term_monthly_backtester  # noqa: E402
from core_sim.paper_broker_sim import PaperBrokerSim  # noqa: E402
from core_sim.short_term_day_runner import create_short_term_daily_backtester  # noqa: E402
from core_sim.short_term_pre_gate import build_history_before_day  # noqa: E402
from data.storage import MarketDB  # noqa: E402

logger = logging.getLogger(__name__)

F3_MAX_GAP = 3


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def compute_trading_days_gap(last_day: date | None, target_day: date) -> list[date]:
    """Return weekdays from last_day+1 through target_day inclusive.

    If last_day is None (first run), returns [target_day].
    """
    if last_day is None:
        return [target_day]
    days: list[date] = []
    d = last_day + timedelta(days=1)
    while d <= target_day:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------


def _cost_model_from_policy(policy_doc: dict[str, Any]) -> CostModel:
    m = policy_doc["markets"]
    return CostModel(
        market_configs={
            "US": MarketCostConfig(
                commission_bps_per_side=float(m["US"]["commission_bps_per_side"]),
                slippage_bps=float(m["US"]["slippage_bps"]),
                min_spread_bps=0.5,
            ),
            "AR": MarketCostConfig(
                commission_bps_per_side=float(m["AR"]["commission_bps_per_side"]),
                slippage_bps=float(m["AR"]["slippage_bps"]),
                min_spread_bps=0.5,
            ),
        }
    )


def _build_history_from_db(
    db: MarketDB,
    symbol: str,
    trading_day: date,
    venue: str,
    lookback_days: int = 60,
) -> list[dict[str, float]]:
    """Build history list from DB OHLCV for a symbol, strictly before trading_day."""
    start = trading_day - timedelta(days=lookback_days * 2)
    end = trading_day - timedelta(days=1)
    rows = db.get_ohlcv(symbol, start, end, venue)
    return [{"close": r.close, "volume": r.volume} for r in rows][-lookback_days:]


_VENUE_MAP: dict[str, str] = {"US": "XNYS", "AR": "XBUE"}


def _build_long_pipeline_context(
    ledger: PortfolioLedger,
    snap: dict[str, Any],
    calendar_store: TradingCalendarStore | None,
) -> dict[str, Any]:
    """Build pipeline_context keys required by the long-term engine from current ledger state."""
    positions_qty_long: dict[str, float] = {}
    for sym, pos_data in (snap.get("positions") or {}).items():
        if str(pos_data.get("bucket")) == "long":
            positions_qty_long[sym] = float(pos_data.get("qty", 0.0))

    ctx: dict[str, Any] = {
        "long_bucket_mtm": float(snap.get("equity_long", 0.0)),
        "long_cash": float(snap.get("cash", 0.0)) - float(getattr(ledger, "short_cash", 0.0)),
        "positions_qty_long": positions_qty_long,
    }
    if calendar_store is not None:
        ctx["us_sessions"] = calendar_store.us_sessions
    return ctx


def run_catch_up(
    db: MarketDB,
    gap_days: list[date],
    policy_doc: dict[str, Any],
    initial_cash: float,
    *,
    enable_long_engine: bool = False,
) -> None:
    """Process each gap day sequentially: replay → load bars → run pipeline → persist.

    When enable_long_engine=True, runs short pipeline first, then long pipeline
    on the same ledger/broker. Fills from both sleeves are persisted together.
    """
    mode = "paper_live"
    momentum = int(policy_doc["short_term_engine"]["momentum_lookback_days"])
    history_cap = max(momentum + 30, 60)

    from core_sim.short_term_day_runner import load_merged_whitelist
    merged_whitelist = load_merged_whitelist(REPO_ROOT, policy_doc)

    calendar_store: TradingCalendarStore | None = None
    cal_path = REPO_ROOT / "config" / "calendars" / "trading_days.v1.yaml"
    if cal_path.exists():
        calendar_store = TradingCalendarStore.from_yaml(str(cal_path))

    for day in gap_days:
        existing = db.get_last_snapshot_day(mode)
        if existing is not None and existing >= day:
            logger.info("Day %s already has snapshot — skipping (idempotent).", day)
            continue

        ledger = db.replay_ledger_from_fills(mode, starting_cash=initial_cash)
        cost_model = _cost_model_from_policy(policy_doc)
        broker = PaperBrokerSim(ledger=ledger, cost_model=cost_model)
        short_backtester = create_short_term_daily_backtester(
            policy_doc=policy_doc,
            repo_root=REPO_ROOT,
            ledger=ledger,
            broker=broker,
            calendar_store=calendar_store,
            corporate_actions_store=None,
            db=db,
        )

        daily_bars: dict[str, dict[str, float]] = {}
        for sym, market in merged_whitelist.items():
            venue = _VENUE_MAP.get(market, market)
            rows = db.get_ohlcv(sym, day, day, venue)
            if rows:
                bar = rows[0]
                daily_bars[sym] = {
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                }

        if not daily_bars:
            raise RuntimeError(f"No OHLCV bars found for {day} — cannot proceed.")

        history_by_symbol: dict[str, list[dict[str, float]]] = {}
        for sym, market in merged_whitelist.items():
            if sym not in daily_bars:
                continue
            venue = _VENUE_MAP.get(market, market)
            history_by_symbol[sym] = _build_history_from_db(
                db, sym, day, venue, lookback_days=history_cap
            )

        short_events = short_backtester.run_day(
            trading_day=day,
            daily_bars=daily_bars,
            pipeline_context={"history_by_symbol": history_by_symbol},
        )

        short_fills = short_events[4].payload
        all_fills: list[dict] = list(short_fills) if isinstance(short_fills, list) else []
        long_fills_count = 0

        if enable_long_engine:
            short_snap = ledger.mark_to_market(trading_day=day, daily_bars=daily_bars)
            long_ctx = _build_long_pipeline_context(ledger, short_snap, calendar_store)

            long_backtester = create_long_term_monthly_backtester(
                policy_doc=policy_doc,
                repo_root=REPO_ROOT,
                ledger=ledger,
                broker=broker,
                calendar_store=calendar_store,
                db=db,
            )
            long_events = long_backtester.run_day(
                trading_day=day,
                daily_bars=daily_bars,
                pipeline_context=long_ctx,
            )
            long_fills = long_events[4].payload
            if isinstance(long_fills, list) and long_fills:
                all_fills.extend(long_fills)
                long_fills_count = len(long_fills)

        snap = ledger.mark_to_market(trading_day=day, daily_bars=daily_bars)
        run_id = f"paper_live_{day.isoformat()}_{uuid.uuid4().hex[:8]}"

        if all_fills:
            db.persist_fills(run_id, mode, day, all_fills)

        if isinstance(snap, dict):
            short_cash = float(snap.get("cash", 0.0)) * float(
                policy_doc.get("weights", {}).get("short", 0.3)
            )
            db.persist_snapshot(
                mode,
                day,
                snap,
                short_cash=short_cash,
                kill_switch_active=bool(
                    snap.get("short_bucket", {}).get("kill_switch_active", False)
                ),
                num_fills_today=len(all_fills),
            )

        logger.info(
            "Day %s processed — run_id=%s, short_fills=%d, long_fills=%d",
            day, run_id,
            len(all_fills) - long_fills_count,
            long_fills_count,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _previous_weekday(d: date) -> date:
    """Return the most recent weekday <= d."""
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Paper-live daily orchestrator (short-term bucket)"
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Target trading day (YYYY-MM-DD). Defaults to previous weekday.",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=str(REPO_ROOT / "data" / "market.db"),
        help="Path to SQLite database.",
    )
    parser.add_argument(
        "--initial-cash",
        type=float,
        default=1000.0,
        help="Starting cash for ledger replay.",
    )
    parser.add_argument(
        "--policy",
        type=str,
        default=str(REPO_ROOT / "config" / "policy.v1.yaml"),
        help="Path to policy YAML.",
    )
    parser.add_argument(
        "--enable-long-engine",
        action="store_true",
        default=False,
        help="Enable long-term sleeve execution (default: disabled for short-only rollback).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.date:
        target_day = date.fromisoformat(args.date)
    else:
        target_day = _previous_weekday(date.today() - timedelta(days=1))

    if target_day > date.today():
        logger.error("Target day %s is in the future.", target_day)
        return 1

    db_path = Path(args.db)
    if not db_path.exists():
        logger.error("Database not found at %s", db_path)
        return 1

    policy_path = Path(args.policy)
    if not policy_path.exists():
        logger.error("Policy file not found at %s", policy_path)
        return 1

    with policy_path.open(encoding="utf-8") as f:
        policy_doc = yaml.safe_load(f)

    db = MarketDB(str(db_path))

    last_day = db.get_last_snapshot_day("paper_live")
    gap_days = compute_trading_days_gap(last_day, target_day)

    if len(gap_days) > F3_MAX_GAP:
        logger.error(
            "F3 VIOLATION: gap is %d trading days (max %d). "
            "Manual intervention required. Last snapshot: %s, target: %s",
            len(gap_days),
            F3_MAX_GAP,
            last_day,
            target_day,
        )
        return 2

    if not gap_days:
        logger.info("No gap — target day %s already processed.", target_day)
        return 0

    logger.info(
        "Processing %d day(s): %s → %s",
        len(gap_days),
        gap_days[0],
        gap_days[-1],
    )

    try:
        run_catch_up(
            db, gap_days, policy_doc, args.initial_cash,
            enable_long_engine=args.enable_long_engine,
        )
    except Exception as exc:
        logger.exception("Runtime error during paper-live run: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
