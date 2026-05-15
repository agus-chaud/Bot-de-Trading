"""Long engine validation stage for the validation workflow.

This stage is purely informational — it NEVER blocks GO (v1).
It runs the long-term pipeline over the given trading days and measures:
  - max_drift_observed_pp: maximum drift (pp) observed before any rebalance
  - total_rebalance_cost: cumulative broker fees across all rebalances in the period
  - monthly_drawdown_long: worst monthly drawdown of the long bucket
  - rebalances_executed: number of rebalances that actually fired
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from core_sim.calendar_store import TradingCalendarStore
from core_sim.cost_model import CostModel, MarketCostConfig, SlippageMode
from core_sim.ledger import PortfolioLedger
from core_sim.long_term_engine import (
    LongTermEngineConfig,
    current_weights_mtm,
    drift_per_line_pp,
    is_rebalance_day_by_rule,
    long_rebalance_calendar_from_rule,
    long_sleeve_trade_market,
    long_term_engine_config_from_policy_dict,
    target_weights,
)
from core_sim.long_term_monthly_runner import create_long_term_monthly_backtester
from core_sim.paper_broker_sim import PaperBrokerSim
from data.storage import MarketDB
from validation.report import StageResult

logger = logging.getLogger(__name__)

_STAGE_NAME = "long_engine"
_VENUE_US = "XNYS"
_VENUE_AR = "XBUE"
_MIN_REBALANCE_UNITS_REQUIRED = 2  # need at least 2 units (weeks/months) to observe behavior


def _ar_business_days_in_span(db: MarketDB, span_start: date, span_end: date) -> list[date]:
    """Ordered AR business dates from calendars (XBUE) within [start, end]."""
    cursor = db._conn.execute(
        """
        SELECT ts FROM calendars
        WHERE venue = ? AND ts BETWEEN ? AND ?
        ORDER BY ts ASC
        """,
        (_VENUE_AR, span_start.isoformat(), span_end.isoformat()),
    )
    return [date.fromisoformat(row[0]) for row in cursor.fetchall()]


@dataclass
class StageDetails:
    """Optional per-run artifacts for walk-forward comparison (long sleeve only)."""

    daily_equity: list[dict[str, Any]] = field(default_factory=list)
    fills: list[dict[str, Any]] = field(default_factory=list)
    final_positions: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _market_cost_config_from_policy(policy_doc: dict[str, Any], *, market_tag: str) -> MarketCostConfig:
    """Lee ``markets.US`` o ``markets.AR`` del policy (alineado a ``run_paper_live._cost_model_from_policy``)."""
    mk = market_tag.upper()
    raw_markets: dict[str, Any] = policy_doc.get("markets", {})
    cfg_raw: dict[str, Any] = raw_markets.get(mk, {})
    defaults = (
        {"commission_bps_per_side": 15.0, "slippage_bps": 5.0}
        if mk == "AR"
        else {"commission_bps_per_side": 1.0, "slippage_bps": 2.0}
    )
    return MarketCostConfig(
        commission_bps_per_side=float(cfg_raw.get("commission_bps_per_side", defaults["commission_bps_per_side"])),
        slippage_bps=float(cfg_raw.get("slippage_bps", defaults["slippage_bps"])),
        slippage_mode=SlippageMode.FIXED_BPS,
        min_spread_bps=float(cfg_raw.get("min_spread_bps", 0.5)),
    )


def _build_broker_for_long_sleeve(
    ledger: PortfolioLedger,
    policy_doc: dict[str, Any],
    *,
    long_trade_market: str,
) -> PaperBrokerSim:
    """PaperBrokerSim con costos sólo del mercado del sleeve largo (AR o US).

    Las órdenes del motor largo llevan ``market == long_sleeve_trade_market``; el cost model
    debe exponer exactamente esa clave para evitar mezclar fee US en simulaciones BYMA y
    viceversa (``PaperBrokerSim`` / ``CostModel`` resuelven fee por ``order['market']``).
    """
    mk = str(long_trade_market).strip().upper()
    if mk not in {"AR", "US"}:
        raise ValueError(f"long_trade_market must be AR or US, got {long_trade_market!r}")
    single = _market_cost_config_from_policy(policy_doc, market_tag=mk)
    return PaperBrokerSim(ledger=ledger, cost_model=CostModel(market_configs={mk: single}))


def _load_daily_bars_for_day(
    db: MarketDB,
    trading_day: date,
    symbols: list[str],
    *,
    venue: str,
) -> dict[str, dict[str, float]]:
    """Load close prices from the DB for the given day and symbols."""
    bars: dict[str, dict[str, float]] = {}
    for sym in symbols:
        rows = db.get_ohlcv(sym, trading_day, trading_day, venue)
        if rows:
            row = rows[0]
            bars[sym] = {"close": row.close, "volume": row.volume}
    return bars


def _months_in_period(trading_days: list[date]) -> int:
    """Count distinct calendar months covered by the trading days."""
    if not trading_days:
        return 0
    months = {(d.year, d.month) for d in trading_days}
    return len(months)


def _iso_weeks_in_period(trading_days: list[date]) -> int:
    """Count distinct ISO calendar weeks covered by trading days."""
    if not trading_days:
        return 0
    weeks = {(d.isocalendar().year, d.isocalendar().week) for d in trading_days}
    return len(weeks)


def _compute_long_bucket_mtm(
    ledger: PortfolioLedger,
    daily_bars: dict[str, dict[str, float]],
    starting_cash: float,
) -> tuple[float, float]:
    """Return (long_bucket_mtm, long_cash) from current ledger state.

    long_bucket_mtm = market value of all long positions + undeployed cash
    long_cash = cash available for new long trades (approximated as total cash)
    """
    long_mv = 0.0
    for pos in ledger.positions.values():
        if pos.bucket == "long":
            sym = pos.symbol
            bar = daily_bars.get(sym)
            if bar and "close" in bar and bar["close"] > 0:
                long_mv += pos.qty * float(bar["close"])
    # Treat all cash as available for the long sleeve in this simulation
    long_cash = ledger.cash
    long_bucket_mtm = long_mv + long_cash
    return long_bucket_mtm, long_cash


# ---------------------------------------------------------------------------
# Main stage function
# ---------------------------------------------------------------------------


def run_long_engine_stage(
    db: MarketDB,
    trading_days: list[date],
    policy_doc: dict[str, Any],
    repo_root: Path,
    starting_cash: float,
    *,
    return_details: bool = False,
) -> tuple[StageResult, StageDetails | None]:
    """Run the long engine validation stage.

    Never blocks GO — passed is always True, violations is always [].

    Args:
        db: MarketDB instance to query OHLCV data.
        trading_days: Lista de días de sesión (US o AR) del lookback; puede remapearse internamente
            al calendario AR (XBUE) cuando el policy usa reglas ``first_ar_*``.
        policy_doc: Parsed policy.v1.yaml as a dict.
        repo_root: Repository root path (used to load whitelists).
        starting_cash: Starting portfolio cash for the simulation.
        return_details: When True, return ``StageDetails`` with daily long-sleeve
            equity, fills, and final positions.

    Returns:
        ``(StageResult, StageDetails | None)`` — details are None when
        ``return_details`` is False.
    """
    _skipped_result = StageResult(
        stage=_STAGE_NAME,
        passed=True,
        metrics={
            "max_drift_observed_pp": None,
            "total_rebalance_cost": None,
            "monthly_drawdown_long": None,
            "rebalances_executed": None,
        },
        violations=[],
        skipped=True,
    )

    if not trading_days:
        logger.info('{"event": "long_engine_stage_skipped", "reason": "no_trading_days"}')
        return _skipped_result, None

    # Extract long-engine config and symbols from policy
    lt_cfg: LongTermEngineConfig = long_term_engine_config_from_policy_dict(
        policy_doc["long_term_engine"]
    )
    cal_kind = long_rebalance_calendar_from_rule(lt_cfg.rebalance_rule)
    venue_long = _VENUE_AR if cal_kind == "AR" else _VENUE_US

    span_start, span_end = trading_days[0], trading_days[-1]
    if cal_kind == "AR":
        trading_days_eff = _ar_business_days_in_span(db, span_start, span_end)
    else:
        trading_days_eff = list(trading_days)

    if len(trading_days_eff) < _MIN_REBALANCE_UNITS_REQUIRED:
        logger.info(
            '{"event": "long_engine_stage_skipped", "reason": "insufficient_calendar_days", "have": %d}',
            len(trading_days_eff),
        )
        return _skipped_result, None

    if lt_cfg.rebalance_rule in (
        "first_us_trading_day_of_calendar_week",
        "first_ar_business_day_of_calendar_week",
    ):
        units = _iso_weeks_in_period(trading_days_eff)
        if units < _MIN_REBALANCE_UNITS_REQUIRED:
            logger.info(
                '{"event": "long_engine_stage_skipped", "reason": "insufficient_weeks", "weeks": %d}',
                units,
            )
            return _skipped_result, None
    else:
        units = _months_in_period(trading_days_eff)
        if units < _MIN_REBALANCE_UNITS_REQUIRED:
            logger.info(
                '{"event": "long_engine_stage_skipped", "reason": "insufficient_months", "months": %d}',
                units,
            )
            return _skipped_result, None

    symbols: list[str] = sorted(
        [sym for sym, _ in (*lt_cfg.core_lines, *lt_cfg.satellite_lines)]
    )
    targets = target_weights(lt_cfg)

    # Check that we have data in the DB for at least one symbol (venue sleeve largo)
    period_start = trading_days_eff[0]
    period_end = trading_days_eff[-1]
    sample_rows = db.get_ohlcv(symbols[0], period_start, period_end, venue_long) if symbols else []
    if not sample_rows:
        logger.info(
            '{"event": "long_engine_stage_skipped", "reason": "no_ohlcv_data", "symbol": "%s"}',
            symbols[0] if symbols else "none",
        )
        return _skipped_result, None

    # Set up simulation components
    ledger = PortfolioLedger(starting_cash=starting_cash)
    trade_mkt = long_sleeve_trade_market(lt_cfg)
    broker = _build_broker_for_long_sleeve(ledger, policy_doc, long_trade_market=trade_mkt)

    cal_yaml = repo_root / "config" / "calendars" / "trading_days.v1.yaml"
    calendar_store_bt: TradingCalendarStore | None = None
    if cal_yaml.is_file():
        calendar_store_bt = TradingCalendarStore.from_yaml(str(cal_yaml))

    backtester = create_long_term_monthly_backtester(
        policy_doc=policy_doc,
        repo_root=repo_root,
        ledger=ledger,
        broker=broker,
        calendar_store=calendar_store_bt,
    )

    calendar_sessions: frozenset[date] = frozenset(trading_days_eff)

    # Tracking accumulators
    max_drift_pp: float = 0.0
    total_rebalance_cost: float = 0.0
    rebalances_executed: int = 0
    monthly_long_equity: dict[tuple[int, int], list[float]] = {}
    daily_equity: list[dict[str, Any]] = []
    all_fills: list[dict[str, Any]] = []

    # Run the pipeline day by day, but only on rebalance-candidate days
    # according to policy rebalance_rule to keep it efficient.
    for trading_day in trading_days_eff:
        is_rebalance_day = is_rebalance_day_by_rule(
            trading_day=trading_day,
            rebalance_rule=lt_cfg.rebalance_rule,
            calendar_sessions=calendar_sessions,
        )

        daily_bars = _load_daily_bars_for_day(db, trading_day, symbols, venue=venue_long)
        if not daily_bars:
            continue

        long_bucket_mtm, long_cash = _compute_long_bucket_mtm(ledger, daily_bars, starting_cash)

        if return_details:
            daily_equity.append(
                {"date": trading_day.isoformat(), "equity": round(long_bucket_mtm, 4)}
            )

        # Compute current weights and drift BEFORE the rebalance
        if long_bucket_mtm > 0:
            positions_qty: dict[str, float] = {
                sym: ledger.positions[sym].qty
                for sym in symbols
                if sym in ledger.positions
            }
            current = current_weights_mtm(
                long_bucket_mtm=long_bucket_mtm,
                positions_qty=positions_qty,
                prices={sym: daily_bars[sym]["close"] for sym in daily_bars},
                universe=symbols,
            )
            drift_pp = drift_per_line_pp(targets, current)
            day_max_drift = max(drift_pp.values(), default=0.0)
            if day_max_drift > max_drift_pp:
                max_drift_pp = day_max_drift

        # Track long equity for monthly drawdown
        long_mv = sum(
            ledger.positions[sym].qty * daily_bars[sym]["close"]
            for sym in symbols
            if sym in ledger.positions and sym in daily_bars
        )
        month_key = (trading_day.year, trading_day.month)
        if month_key not in monthly_long_equity:
            monthly_long_equity[month_key] = []
        monthly_long_equity[month_key].append(long_mv)

        # Only run the full pipeline on rebalance days to avoid overhead
        if not is_rebalance_day:
            continue

        pipeline_context: dict[str, Any] = {
            # Calendario de rebalance: US → us_sessions; AR (pesos/BYMA) → ar_business_days (span XBUE).
            (
                "ar_business_days" if cal_kind == "AR" else "us_sessions"
            ): calendar_sessions,
            "long_bucket_mtm": long_bucket_mtm,
            "long_cash": long_cash,
            "positions_qty_long": {
                sym: ledger.positions[sym].qty
                for sym in symbols
                if sym in ledger.positions
            },
            "halt_long_engine": False,
            "data_quality_halt": False,
        }

        try:
            events = backtester.run_day(
                trading_day=trading_day,
                daily_bars=daily_bars,
                pipeline_context=pipeline_context,
            )
        except Exception as exc:
            logger.warning(
                '{"event": "long_engine_stage_day_error", "day": "%s", "error": "%s"}',
                trading_day.isoformat(),
                str(exc),
            )
            continue

        # Check if any fills were generated (= a rebalance happened)
        event_map = {e.name: e.payload for e in events}
        fills = event_map.get("OrdersFilled") or []
        if fills:
            rebalances_executed += 1
            if return_details:
                all_fills.extend(fills)
            for fill in fills:
                total_rebalance_cost += float(fill.get("fee", 0.0))

        logger.debug(
            '{"event": "long_engine_stage_day", "day": "%s", "fills": %d}',
            trading_day.isoformat(),
            len(fills),
        )

    # Compute worst monthly drawdown for the long bucket
    worst_monthly_drawdown: float = 0.0
    for month_key, equity_values in monthly_long_equity.items():
        if not equity_values:
            continue
        peak = equity_values[0]
        for eq in equity_values[1:]:
            peak = max(peak, eq)
            if peak > 0:
                dd = (eq / peak) - 1.0
                if dd < worst_monthly_drawdown:
                    worst_monthly_drawdown = dd

    details: StageDetails | None = None
    if return_details:
        final_positions = {
            sym: ledger.positions[sym].qty
            for sym in symbols
            if sym in ledger.positions and ledger.positions[sym].bucket == "long"
        }
        details = StageDetails(
            daily_equity=daily_equity,
            fills=all_fills,
            final_positions=final_positions,
        )

    return (
        StageResult(
            stage=_STAGE_NAME,
            passed=True,
            metrics={
                "max_drift_observed_pp": round(max_drift_pp, 4),
                "total_rebalance_cost": round(total_rebalance_cost, 4),
                "monthly_drawdown_long": round(worst_monthly_drawdown, 6),
                "rebalances_executed": rebalances_executed,
            },
            violations=[],
        ),
        details,
    )
