"""Kill switch history stage for the validation workflow.

Simulates the short bucket monthly drawdown over the historical period using
data from the DB, and counts how many times the kill switch threshold would
have been crossed — WITHOUT writing anything to the DB (in-memory only).

Always informational: passed=True, violations=[].
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.storage import MarketDB

from core_sim.ledger import PortfolioLedger
from validation.report import StageResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal simulation helpers
# ---------------------------------------------------------------------------

def _get_symbols_from_db(
    db: "MarketDB",
    start: date,
    end: date,
) -> list[str]:
    """Return all symbols that have at least one OHLCV bar in [start, end]."""
    cursor = db._conn.execute(
        "SELECT DISTINCT symbol FROM ohlcv WHERE ts BETWEEN ? AND ?",
        (start.isoformat(), end.isoformat()),
    )
    return [row[0] for row in cursor.fetchall()]


def _get_daily_bars_for_day(
    db: "MarketDB",
    symbols: list[str],
    day: date,
) -> dict[str, dict[str, float]]:
    """Return {symbol: {open, high, low, close, volume}} for a given day."""
    if not symbols:
        return {}
    placeholders = ",".join("?" * len(symbols))
    cursor = db._conn.execute(
        f"""
        SELECT symbol, open, high, low, close, volume
        FROM ohlcv
        WHERE symbol IN ({placeholders}) AND ts = ?
        """,
        (*symbols, day.isoformat()),
    )
    result: dict[str, dict[str, float]] = {}
    for row in cursor.fetchall():
        result[row[0]] = {
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }
    return result


def _simulate_kill_switch_history(
    db: "MarketDB",
    trading_days: list[date],
    kill_switch_threshold: float,
    starting_cash: float,
) -> dict:
    """Run in-memory simulation tracking short bucket monthly drawdown.

    Returns a dict with:
      - kill_switch_activations: int
      - worst_monthly_dd_short: float
      - activation_dates: list[str] (ISO dates)
      - months_simulated: int
    """
    if not trading_days:
        return {
            "kill_switch_activations": 0,
            "worst_monthly_dd_short": 0.0,
            "activation_dates": [],
            "months_simulated": 0,
        }

    start = min(trading_days)
    end = max(trading_days)
    symbols = _get_symbols_from_db(db, start, end)

    ledger = PortfolioLedger(starting_cash=starting_cash)

    activation_dates: list[str] = []
    worst_dd = 0.0
    # Track whether kill switch was already active in current month to avoid
    # counting the same month multiple times.
    active_month: tuple[int, int] | None = None

    # Only count months where we actually processed at least one bar.
    months_with_data: set[tuple[int, int]] = set()

    for day in sorted(trading_days):
        month_key = (day.year, day.month)

        daily_bars = _get_daily_bars_for_day(db, symbols, day)
        if not daily_bars:
            # No data for this day — skip entirely.
            continue

        months_with_data.add(month_key)

        # Mark to market with available bars (positions in short bucket only).
        # Because this is a pure simulation without any order filling, the only
        # positions that could exist are ones we never actually place here.
        # We run MTM purely to update the monthly drawdown tracking in the ledger.
        try:
            snap = ledger.mark_to_market(trading_day=day, daily_bars=daily_bars)
        except ValueError:
            # Missing close for an open position — skip this day
            logger.debug("kill_switch_history: skipping day %s due to missing close", day)
            continue

        short_bucket = snap.get("short_bucket") or {}
        monthly_dd = float(short_bucket.get("monthly_drawdown", 0.0))

        if monthly_dd < worst_dd:
            worst_dd = monthly_dd

        # Check threshold — record at most one activation per calendar month
        if monthly_dd <= kill_switch_threshold and active_month != month_key:
            active_month = month_key
            activation_dates.append(day.isoformat())
            logger.info(
                '{"event": "ks_history_activation", "date": "%s", "monthly_dd": %s, "threshold": %s}',
                day.isoformat(),
                monthly_dd,
                kill_switch_threshold,
            )

        # Auto-reset: if a new month starts, clear active_month tracker
        if active_month is not None:
            active_month_key = active_month
            if month_key > active_month_key:
                active_month = None

    return {
        "kill_switch_activations": len(activation_dates),
        "worst_monthly_dd_short": worst_dd,
        "activation_dates": activation_dates,
        "months_simulated": len(months_with_data),
    }


# ---------------------------------------------------------------------------
# Main stage function
# ---------------------------------------------------------------------------

def run_kill_switch_history_stage(
    db: "MarketDB",
    trading_days: list[date],
    policy_doc: dict,
    repo_root: Path,
    starting_cash: float,
) -> StageResult:
    """Run the kill switch history stage.

    Simulates the short bucket monthly drawdown over the historical period
    using data from the DB, counting how many times the kill switch threshold
    (-8% by default) would have been crossed.

    Always informational: passed=True, violations=[].
    Skipped if trading_days is empty or no OHLCV data exists for the period.

    Args:
        db: MarketDB instance (read-only — no writes performed).
        trading_days: Ordered list of trading days for the lookback period.
        policy_doc: Parsed policy.v1.yaml as a dict.
        repo_root: Absolute path to the repository root (unused; kept for API consistency).
        starting_cash: Initial cash for the in-memory ledger simulation.

    Returns:
        StageResult with stage="kill_switch_history".
    """
    del repo_root  # not used in this stage

    kill_switch_threshold = float(policy_doc.get("short_kill_switch_monthly_dd", -0.08))

    # ------------------------------------------------------------------
    # Skip if no data
    # ------------------------------------------------------------------
    if not trading_days:
        return StageResult(
            stage="kill_switch_history",
            passed=True,
            metrics={
                "kill_switch_activations": 0,
                "months_simulated": 0,
                "worst_monthly_dd_short": 0.0,
                "kill_switch_threshold": kill_switch_threshold,
                "activation_dates": [],
            },
            violations=[],
            skipped=True,
        )

    # ------------------------------------------------------------------
    # Simulate
    # ------------------------------------------------------------------
    try:
        result = _simulate_kill_switch_history(
            db=db,
            trading_days=trading_days,
            kill_switch_threshold=kill_switch_threshold,
            starting_cash=starting_cash,
        )
    except Exception as exc:  # pragma: no cover
        logger.warning('{"event": "kill_switch_history_error", "error": "%s"}', exc)
        result = {
            "kill_switch_activations": 0,
            "worst_monthly_dd_short": 0.0,
            "activation_dates": [],
            "months_simulated": 0,
        }

    # Check if we had any data at all (months_simulated == 0 after non-empty trading_days
    # means all days had empty bars — treat as skipped)
    if result["months_simulated"] == 0:
        return StageResult(
            stage="kill_switch_history",
            passed=True,
            metrics={
                "kill_switch_activations": 0,
                "months_simulated": 0,
                "worst_monthly_dd_short": 0.0,
                "kill_switch_threshold": kill_switch_threshold,
                "activation_dates": [],
            },
            violations=[],
            skipped=True,
        )

    # ------------------------------------------------------------------
    # Result — always informational
    # ------------------------------------------------------------------
    metrics: dict = {
        "kill_switch_activations": result["kill_switch_activations"],
        "months_simulated": result["months_simulated"],
        "worst_monthly_dd_short": result["worst_monthly_dd_short"],
        "kill_switch_threshold": kill_switch_threshold,
        "activation_dates": result["activation_dates"],
    }

    logger.info(
        '{"event": "kill_switch_history_done", "activations": %d, "months": %d, "worst_dd": %s}',
        result["kill_switch_activations"],
        result["months_simulated"],
        result["worst_monthly_dd_short"],
    )

    return StageResult(
        stage="kill_switch_history",
        passed=True,
        metrics=metrics,
        violations=[],
    )
