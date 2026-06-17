#!/usr/bin/env python3
"""Daily paper-live orchestrator: runs short-term pipeline on real OHLCV data.

Exit codes:
  0 — success, all days processed
  1 — runtime error (missing trading calendar, portfolio meta conflict, data missing, pipeline crash)
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
from core_sim.long_term_engine import (  # noqa: E402
    long_rebalance_calendar_from_rule,
    long_term_engine_config_from_policy_dict,
)
from core_sim.long_term_monthly_runner import create_long_term_monthly_backtester  # noqa: E402
from core_sim.paper_broker_sim import PaperBrokerSim  # noqa: E402
from core_sim.short_hedge_engine import short_hedge_config_from_policy_dict  # noqa: E402
from core_sim.short_hedge_runner import (  # noqa: E402
    load_hedge_whitelist,
    run_hedge_sleeve_day,
)
from core_sim.short_term_day_runner import create_short_term_daily_backtester  # noqa: E402
from core_sim.short_term_pre_gate import build_history_before_day  # noqa: E402
from data.storage import MarketDB, PortfolioMetaConflictError  # noqa: E402

logger = logging.getLogger(__name__)

F3_MAX_GAP = 3
PAPER_LIVE_MODE = "paper_live"
DEFAULT_STARTING_CASH_ARS = 3_000_000.0
DEFAULT_REPORTING_CURRENCY = "ARS"


class CalendarConfigError(Exception):
    """Raised when the trading calendar YAML exists but is unusable."""


def _calendar_yaml_path(policy_doc: dict[str, Any]) -> Path:
    """Resolve calendar YAML path from policy (repo-relative)."""
    cal_cfg = policy_doc.get("calendar") or {}
    rel = cal_cfg.get("source_of_truth")
    if not rel:
        return REPO_ROOT / "config" / "calendars" / "trading_days.v1.yaml"
    return REPO_ROOT / str(rel)


def load_required_calendar_store(policy_doc: dict[str, Any]) -> TradingCalendarStore:
    """Load TradingCalendarStore or fail fast — paper-live must not run blind."""
    cal_path = _calendar_yaml_path(policy_doc)
    if not cal_path.is_file():
        raise FileNotFoundError(
            f"Trading calendar required but missing at {cal_path}. "
            "Regenerate with: python scripts/build_trading_days_yaml.py"
        )
    store = TradingCalendarStore.from_yaml(str(cal_path))
    if not store.us_sessions:
        raise CalendarConfigError(f"Calendar {cal_path} defines no US sessions")
    if not store.ar_business_days:
        raise CalendarConfigError(f"Calendar {cal_path} defines no AR business days")
    return store


def _resolve_calendar_store(
    policy_doc: dict[str, Any],
    *,
    calendar_store: TradingCalendarStore | None = None,
    no_calendar: bool = False,
) -> TradingCalendarStore | None:
    if no_calendar:
        return None
    if calendar_store is not None:
        return calendar_store
    return load_required_calendar_store(policy_doc)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _is_operational_trading_day(d: date, calendar_store: TradingCalendarStore) -> bool:
    """True when at least one paper-live market operates (US/CEDEAR or AR local)."""
    return calendar_store.is_us_session(d) or calendar_store.is_ar_business_day(d)


def compute_trading_days_gap(
    last_day: date | None,
    target_day: date,
    *,
    calendar_store: TradingCalendarStore | None = None,
) -> list[date]:
    """Return trading days from last_day+1 through target_day inclusive.

    With *calendar_store*: union of US sessions (US + CEDEAR short signal) and AR
    business days (panel local). Without calendar: weekdays (Mon–Fri) fallback.
    If last_day is None (first run), returns [target_day].
    """
    if last_day is None:
        return [target_day]
    days: list[date] = []
    d = last_day + timedelta(days=1)
    while d <= target_day:
        if calendar_store is not None:
            if _is_operational_trading_day(d, calendar_store):
                days.append(d)
        elif d.weekday() < 5:
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


def _overlay_ar_long_sleeve_bars_from_db(
    db: MarketDB,
    day: date,
    policy_doc: dict[str, Any],
    daily_bars: dict[str, dict[str, float]],
) -> None:
    """In-place: precios BYMA (XBUE) para líneas del sleeve largo en pesos.

    El merge global puede etiquetar un CEDEAR (p. ej. SPY) como US; el motor largo AR
    debe ver cierres de ``whitelist_cedear``/BYMA, no XNYS.
    """
    lt_cfg = long_term_engine_config_from_policy_dict(policy_doc["long_term_engine"])
    if long_rebalance_calendar_from_rule(lt_cfg.rebalance_rule) != "AR":
        return
    venue = _VENUE_MAP["AR"]
    for sym, _ in (*lt_cfg.core_lines, *lt_cfg.satellite_lines):
        su = str(sym).strip().upper()
        rows = db.get_ohlcv(su, day, day, venue)
        if not rows:
            continue
        bar = rows[0]
        daily_bars[su] = {
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }


def _mtm_bars_for_ledger(
    db: MarketDB,
    day: date,
    ledger: PortfolioLedger,
) -> dict[str, dict[str, float]]:
    """Barras para valuar posiciones ABIERTAS, cada una en el venue en que se operó.

    El ``daily_bars`` que consumen los motores keya los CEDEAR por su tag del merge
    (US→XNYS), así que un feriado AR (con US abierto) una posición en pesos quedaría
    revaluada a su cierre USD de XNYS (colapso ~24-147×). Acá valuamos por
    ``position.market`` (AR→XBUE): una posición AR sólo se precia desde XBUE; si XBUE
    no tiene barra ese día (feriado), el símbolo simplemente queda ausente y el ledger
    arrastra el último close conocido (ADR-051) en vez de colapsar.
    """
    bars: dict[str, dict[str, float]] = {}
    for symbol, position in ledger.positions.items():
        market = str(position.market).upper()
        venue = _VENUE_MAP.get(market, market)
        rows = db.get_ohlcv(symbol, day, day, venue)
        if rows:
            bar = rows[0]
            bars[symbol] = {
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
    return bars


def _hydrate_last_marks_from_db(
    db: MarketDB,
    day: date,
    ledger: PortfolioLedger,
    *,
    lookback_days: int = 120,
) -> None:
    """Sembrar ``ledger._last_mark`` con el close más reciente anterior a ``day``,
    en el venue nativo de cada posición abierta, para que el carry-forward sobreviva
    a la reconstrucción diaria del ledger (el replay aplica fills pero nunca valúa)."""
    start = day - timedelta(days=lookback_days)
    end = day - timedelta(days=1)
    for symbol, position in ledger.positions.items():
        market = str(position.market).upper()
        venue = _VENUE_MAP.get(market, market)
        rows = db.get_ohlcv(symbol, start, end, venue)
        if rows:
            ledger.seed_last_mark(symbol, float(rows[-1].close))


def _resilient_snapshot(
    db: MarketDB,
    day: date,
    ledger: PortfolioLedger,
) -> dict[str, Any]:
    """Snapshot de valuación autoritativo, robusto a barras en moneda equivocada.

    Los motores valúan varias veces dentro de ``run_day`` con un ``daily_bars`` que keya
    los CEDEAR por su tag del merge (US→XNYS, en USD), lo que puede envenenar el
    carry-forward (``_last_mark``) de una posición en pesos. Acá reseteamos ese estado y
    lo re-hidratamos desde el último close en el venue NATIVO de cada posición, y valuamos
    cada posición en su propio venue. Resultado: un feriado AR arrastra el close XBUE
    (stale) en vez de colapsar al cierre USD de XNYS (ADR-051)."""
    ledger.reset_last_marks()
    _hydrate_last_marks_from_db(db, day, ledger)
    return ledger.mark_to_market(
        trading_day=day, daily_bars=_mtm_bars_for_ledger(db, day, ledger)
    )


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
        ctx["ar_business_days"] = calendar_store.ar_business_days
    return ctx


def run_catch_up(
    db: MarketDB,
    gap_days: list[date],
    policy_doc: dict[str, Any],
    initial_cash: float,
    *,
    currency: str = DEFAULT_REPORTING_CURRENCY,
    calendar_store: TradingCalendarStore | None = None,
    no_calendar: bool = False,
    enable_long_engine: bool = False,
    init_portfolio_meta: bool = False,
) -> None:
    """Process each gap day sequentially: replay → load bars → run pipeline → persist.

    When enable_long_engine=True, runs short pipeline first, then long pipeline
    on the same ledger/broker. Fills from both sleeves are persisted together.
    """
    mode = PAPER_LIVE_MODE
    meta = db.ensure_portfolio_meta(
        mode=mode,
        starting_cash=initial_cash,
        currency=currency,
        inception_date=gap_days[0],
        allow_legacy_init=init_portfolio_meta,
    )
    initial_cash = meta.starting_cash
    momentum = int(policy_doc["short_term_engine"]["momentum_lookback_days"])
    history_cap = max(momentum + 30, 60)

    from core_sim.short_term_day_runner import load_merged_whitelist
    merged_whitelist = load_merged_whitelist(REPO_ROOT, policy_doc)

    # Sleeve corto como cobertura (ADR-064): si short_hedge.enabled, el corto se maneja
    # como hedge_static (GLD/WMT + regla de cash) y REEMPLAZA al momentum táctico.
    sh_raw = policy_doc.get("short_hedge") or {}
    hedge_enabled = bool(sh_raw.get("enabled", False))
    hedge_cfg = short_hedge_config_from_policy_dict(sh_raw) if hedge_enabled else None
    hedge_whitelist = load_hedge_whitelist(REPO_ROOT, policy_doc) if hedge_enabled else frozenset()
    weights_short = float(policy_doc["weights"]["short"])

    calendar_store = _resolve_calendar_store(
        policy_doc,
        calendar_store=calendar_store,
        no_calendar=no_calendar,
    )

    for day in gap_days:
        existing = db.get_last_snapshot_day(mode)
        if existing is not None and existing >= day:
            logger.info("Day %s already has snapshot — skipping (idempotent).", day)
            continue

        ledger = db.replay_ledger_from_fills(mode, starting_cash=initial_cash)
        cost_model = _cost_model_from_policy(policy_doc)
        broker = PaperBrokerSim(ledger=ledger, cost_model=cost_model)
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
            logger.warning(
                "No OHLCV bars found for %s — skipping day (likely holiday/non-trading day).",
                day,
            )
            continue

        if hedge_enabled:
            # Sleeve corto como COBERTURA (ADR-064): hedge_static reemplaza al momentum.
            all_fills: list[dict] = run_hedge_sleeve_day(
                db=db, day=day, ledger=ledger, broker=broker,
                hedge_cfg=hedge_cfg, hedge_whitelist=hedge_whitelist,
                weights_short=weights_short, resilient_snapshot=_resilient_snapshot,
            )
        else:
            short_backtester = create_short_term_daily_backtester(
                policy_doc=policy_doc,
                repo_root=REPO_ROOT,
                ledger=ledger,
                broker=broker,
                calendar_store=calendar_store,
                corporate_actions_store=None,
                db=db,
            )
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
            all_fills = list(short_fills) if isinstance(short_fills, list) else []
        long_fills_count = 0

        bars_for_long_and_mtm = daily_bars
        if enable_long_engine:
            bars_for_long_and_mtm = dict(daily_bars)
            _overlay_ar_long_sleeve_bars_from_db(db, day, policy_doc, bars_for_long_and_mtm)

            snap_for_long = _resilient_snapshot(db, day, ledger)
            long_ctx = _build_long_pipeline_context(ledger, snap_for_long, calendar_store)

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
                daily_bars=bars_for_long_and_mtm,
                pipeline_context=long_ctx,
            )
            long_fills = long_events[4].payload
            if isinstance(long_fills, list) and long_fills:
                all_fills.extend(long_fills)
                long_fills_count = len(long_fills)

        snap = _resilient_snapshot(db, day, ledger)
        run_id = f"paper_live_{day.isoformat()}_{uuid.uuid4().hex[:8]}"

        if all_fills:
            db.persist_fills(run_id, mode, day, all_fills)

        if isinstance(snap, dict):
            short_cash = float(ledger.short_cash)
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
        default=DEFAULT_STARTING_CASH_ARS,
        help=f"Starting cash for ledger replay ({DEFAULT_REPORTING_CURRENCY} default).",
    )
    parser.add_argument(
        "--currency",
        type=str,
        default=DEFAULT_REPORTING_CURRENCY,
        choices=("ARS", "USD"),
        help="Reporting currency for portfolio_meta (locked on first run).",
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
    parser.add_argument(
        "--no-calendar",
        action="store_true",
        default=False,
        help="Skip trading calendar (tests only). Disables session-aware no-trade checks.",
    )
    parser.add_argument(
        "--init-portfolio-meta",
        action="store_true",
        default=False,
        help=(
            "One-time bootstrap when snapshots exist without portfolio_meta "
            "(legacy DB). Requires matching --initial-cash and --currency."
        ),
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

    calendar_store: TradingCalendarStore | None = None
    if args.no_calendar:
        logger.warning(
            "--no-calendar: running without TradingCalendarStore; "
            "session flags default to permissive mode and F3 uses weekday fallback."
        )
    else:
        try:
            calendar_store = load_required_calendar_store(policy_doc)
        except (FileNotFoundError, CalendarConfigError) as exc:
            logger.error("Calendar configuration error: %s", exc)
            return 1

    last_day = db.get_last_snapshot_day("paper_live")
    gap_days = compute_trading_days_gap(
        last_day,
        target_day,
        calendar_store=calendar_store,
    )

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
            db,
            gap_days,
            policy_doc,
            args.initial_cash,
            currency=args.currency,
            calendar_store=calendar_store,
            no_calendar=args.no_calendar,
            enable_long_engine=args.enable_long_engine,
            init_portfolio_meta=args.init_portfolio_meta,
        )
    except PortfolioMetaConflictError as exc:
        logger.error("Portfolio meta conflict: %s", exc)
        return 1
    except Exception as exc:
        logger.exception("Runtime error during paper-live run: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
