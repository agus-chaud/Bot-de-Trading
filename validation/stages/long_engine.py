"""Long engine validation stage for the validation workflow.

This stage is purely informational — it NEVER blocks GO (v1).
It runs the monthly long-term pipeline over the given trading days and measures:
  - max_drift_observed_pp: maximum drift (pp) observed before any rebalance
  - total_rebalance_cost: cumulative broker fees across all rebalances in the period
  - monthly_drawdown_long: worst monthly drawdown of the long bucket
  - rebalances_executed: number of rebalances that actually fired
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from core_sim.cost_model import CostModel, MarketCostConfig, SlippageMode
from core_sim.ledger import PortfolioLedger
from core_sim.long_term_engine import (
    LongTermEngineConfig,
    current_weights_mtm,
    drift_per_line_pp,
    is_first_us_trading_day_of_month,
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
_MIN_MONTHS_REQUIRED = 2  # need at least 2 months to observe one rebalance + drawdown


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_broker(ledger: PortfolioLedger, policy_doc: dict[str, Any]) -> PaperBrokerSim:
    """Build a PaperBrokerSim from the policy doc cost config."""
    markets_cfg: dict[str, Any] = policy_doc.get("markets", {})
    us_cfg_raw: dict[str, Any] = markets_cfg.get("US", {})
    commission_bps = float(us_cfg_raw.get("commission_bps_per_side", 1.0))
    slippage_bps = float(us_cfg_raw.get("slippage_bps", 2.0))
    us_market_cfg = MarketCostConfig(
        commission_bps_per_side=commission_bps,
        slippage_bps=slippage_bps,
        slippage_mode=SlippageMode.FIXED_BPS,
    )
    cost_model = CostModel(market_configs={"US": us_market_cfg})
    return PaperBrokerSim(ledger=ledger, cost_model=cost_model)


def _load_daily_bars_for_day(
    db: MarketDB,
    trading_day: date,
    symbols: list[str],
) -> dict[str, dict[str, float]]:
    """Load close prices from the DB for the given day and symbols."""
    bars: dict[str, dict[str, float]] = {}
    for sym in symbols:
        rows = db.get_ohlcv(sym, trading_day, trading_day, _VENUE_US)
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
) -> StageResult:
    """Run the long engine validation stage.

    Never blocks GO — passed is always True, violations is always [].

    Args:
        db: MarketDB instance to query OHLCV data.
        trading_days: Ordered list of US trading days for the lookback period.
        policy_doc: Parsed policy.v1.yaml as a dict.
        repo_root: Repository root path (used to load whitelists).
        starting_cash: Starting portfolio cash for the simulation.

    Returns:
        StageResult with stage="long_engine", passed=True, and 4 metrics populated.
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
        return _skipped_result

    months = _months_in_period(trading_days)
    if months < _MIN_MONTHS_REQUIRED:
        logger.info(
            '{"event": "long_engine_stage_skipped", "reason": "insufficient_months", "months": %d}',
            months,
        )
        return _skipped_result

    # Extract long-engine config and symbols from policy
    lt_cfg: LongTermEngineConfig = long_term_engine_config_from_policy_dict(
        policy_doc["long_term_engine"]
    )
    symbols: list[str] = sorted(
        [sym for sym, _ in (*lt_cfg.core_lines, *lt_cfg.satellite_lines)]
    )
    targets = target_weights(lt_cfg)

    # Check that we have data in the DB for at least one symbol
    period_start = trading_days[0]
    period_end = trading_days[-1]
    sample_rows = db.get_ohlcv(symbols[0], period_start, period_end, _VENUE_US) if symbols else []
    if not sample_rows:
        logger.info(
            '{"event": "long_engine_stage_skipped", "reason": "no_ohlcv_data", "symbol": "%s"}',
            symbols[0] if symbols else "none",
        )
        return _skipped_result

    # Set up simulation components
    ledger = PortfolioLedger(starting_cash=starting_cash)
    broker = _build_broker(ledger, policy_doc)

    backtester = create_long_term_monthly_backtester(
        policy_doc=policy_doc,
        repo_root=repo_root,
        ledger=ledger,
        broker=broker,
    )

    us_sessions: frozenset[date] = frozenset(trading_days)

    # Tracking accumulators
    max_drift_pp: float = 0.0
    total_rebalance_cost: float = 0.0
    rebalances_executed: int = 0
    monthly_long_equity: dict[tuple[int, int], list[float]] = {}

    # Run the pipeline day by day, but only on rebalance-candidate days
    # (first US trading day of each month) to keep it efficient
    for trading_day in trading_days:
        is_rebalance_day = is_first_us_trading_day_of_month(trading_day, us_sessions)

        daily_bars = _load_daily_bars_for_day(db, trading_day, symbols)
        if not daily_bars:
            continue

        long_bucket_mtm, long_cash = _compute_long_bucket_mtm(ledger, daily_bars, starting_cash)

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

        # Capture broker fills before running to compute rebalance cost delta
        fills_before = len(broker._fills)

        pipeline_context: dict[str, Any] = {
            "us_sessions": us_sessions,
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
            # Sum all fees from fills in this rebalance
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

    return StageResult(
        stage=_STAGE_NAME,
        passed=True,
        metrics={
            "max_drift_observed_pp": round(max_drift_pp, 4),
            "total_rebalance_cost": round(total_rebalance_cost, 4),
            "monthly_drawdown_long": round(worst_monthly_drawdown, 6),
            "rebalances_executed": rebalances_executed,
        },
        violations=[],
    )
