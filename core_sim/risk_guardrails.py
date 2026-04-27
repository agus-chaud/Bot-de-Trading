"""Centralized risk guardrail checks for short and long buckets.

Pure functions — no side effects except logging in log_risk_cycle.
check_and_persist_kill_switch is the exception: it has DB side effects by design.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.storage import MarketDB


@dataclass
class GuardrailResult:
    allowed: bool
    reason: str  # "ok" | "short_monthly_kill_switch" | "short_daily_loss_limit" | "no_trade_window" | "halt_data_quality" | "long_daily_loss_limit"
    meta: dict = field(default_factory=dict)


def check_short_risk(
    sb: dict,
    flags: dict,
    config: dict,
    now_minutes_from_open: int | None,
) -> GuardrailResult:
    """Centralize all short bucket risk checks.

    Checks in order (fail-fast): data_quality → no_trade_window → kill_switch_monthly → daily_loss.

    sb: scoreboard dict with 'monthly_drawdown', 'daily_return'
    flags: dict with 'halt_on_data_quality', 'data_quality_ok'
    config: dict with 'kill_dd', 'max_daily_short', 'no_trade_first', 'no_trade_last'
    now_minutes_from_open: minutes since session open (None if not applicable)
    """
    halt_on_dq = bool(flags.get("halt_on_data_quality", True))
    data_ok = bool(flags.get("data_quality_ok", True))
    if halt_on_dq and not data_ok:
        return GuardrailResult(
            allowed=False,
            reason="halt_data_quality",
            meta={"halt_on_data_quality": halt_on_dq, "data_quality_ok": data_ok},
        )

    no_trade_first = int(config.get("no_trade_first", 0))
    no_trade_last = int(config.get("no_trade_last", 0))
    if now_minutes_from_open is not None:
        from .short_term_day_runner import in_no_trade_window, us_regular_session_length_minutes
        if in_no_trade_window(
            no_trade_first=no_trade_first,
            no_trade_last=no_trade_last,
            session_minutes_from_open=now_minutes_from_open,
            session_length_minutes=us_regular_session_length_minutes(),
        ):
            return GuardrailResult(
                allowed=False,
                reason="no_trade_window",
                meta={"now_minutes_from_open": now_minutes_from_open, "no_trade_first": no_trade_first, "no_trade_last": no_trade_last},
            )

    kill_dd = float(config.get("kill_dd", -0.08))
    monthly_dd = float(sb.get("monthly_drawdown", 0.0))
    if monthly_dd <= kill_dd:
        return GuardrailResult(
            allowed=False,
            reason="short_monthly_kill_switch",
            meta={"monthly_drawdown": monthly_dd, "kill_dd": kill_dd},
        )

    max_daily_short = float(config.get("max_daily_short", -0.02))
    daily_ret = float(sb.get("daily_return", 0.0))
    if max_daily_short < 0.0 and daily_ret < max_daily_short:
        return GuardrailResult(
            allowed=False,
            reason="short_daily_loss_limit",
            meta={"daily_return": daily_ret, "limit": max_daily_short},
        )

    return GuardrailResult(
        allowed=True,
        reason="ok",
        meta={"monthly_drawdown": monthly_dd, "daily_return": daily_ret},
    )


def check_long_risk(sb: dict, config: dict) -> GuardrailResult:
    """Check long bucket daily loss limit.

    sb: scoreboard dict with 'long_daily_return' (defaults to 0.0 if absent)
    config: dict with 'max_daily_long' (-0.015 from YAML)
    """
    max_daily_long = float(config.get("max_daily_long", -0.015))
    long_daily_return = float(sb.get("long_daily_return", 0.0))

    if max_daily_long < 0.0 and long_daily_return < max_daily_long:
        return GuardrailResult(
            allowed=False,
            reason="long_daily_loss_limit",
            meta={"long_daily_return": long_daily_return, "limit": max_daily_long},
        )

    return GuardrailResult(
        allowed=True,
        reason="ok",
        meta={"long_daily_return": long_daily_return},
    )


def compute_atr(highs: list[float], lows: list[float], closes: list[float]) -> float | None:
    """Compute ATR(14). Returns None if insufficient history (< 15 bars needed for 14 TRs with prev_close)."""
    if len(closes) < 15:
        return None
    n = len(closes)
    highs_w = highs[-14:]
    lows_w = lows[-14:]
    # closes[-15] is the prev_close before the first of the 14 bars
    prev_closes = closes[-(15):-1]
    true_ranges: list[float] = []
    for i in range(14):
        tr = max(
            highs_w[i] - lows_w[i],
            abs(highs_w[i] - prev_closes[i]),
            abs(lows_w[i] - prev_closes[i]),
        )
        true_ranges.append(tr)
    del n
    return sum(true_ranges) / len(true_ranges)


def check_stop_loss(
    positions: dict[str, dict],
    daily_bars: dict[str, dict],
    price_history: dict[str, list[dict]],
    config: dict,
) -> list[str]:
    """Return list of symbols that triggered stop loss today.

    Uses ATR(14)-based stop when price_history has >= 14 bars, falls back to
    a fixed percentage per market otherwise.
    """
    atr_multiplier = float(config.get("atr_multiplier", 2.0))
    fallback_pct_us = float(config.get("fallback_pct_us", -0.05))
    fallback_pct_ar = float(config.get("fallback_pct_ar", -0.08))

    triggered: list[str] = []
    for symbol, pos in positions.items():
        entry_price = float(pos.get("entry_price", 0.0))
        if entry_price <= 0:
            continue

        bar = daily_bars.get(symbol)
        if bar is None or "close" not in bar:
            continue
        close_today = float(bar["close"])

        history = price_history.get(symbol) or []
        highs = [float(b["high"]) for b in history]
        lows = [float(b["low"]) for b in history]
        closes = [float(b["close"]) for b in history]

        atr = compute_atr(highs, lows, closes)
        if atr is not None:
            stop_price = entry_price - atr_multiplier * atr
        else:
            market = str(pos.get("market", "US")).upper()
            fallback_pct = fallback_pct_us if market == "US" else fallback_pct_ar
            stop_price = entry_price * (1.0 + fallback_pct)

        if close_today <= stop_price:
            triggered.append(symbol)

    return triggered


def check_and_persist_kill_switch(
    sb: dict,
    config: dict,
    db: "MarketDB",
    engine: str,
    today: date,
) -> GuardrailResult:
    """Check monthly kill switch with DB persistence and auto-reset on month boundary.

    Side effects: may write to kill_switch_log and create an alert file under alerts/.
    """
    _log = logging.getLogger("risk_guardrails")

    # Step 1: auto-reset when the activation belongs to a prior month
    state = db.get_kill_switch_state(engine)
    if state.active and state.activated_at is not None:
        if (state.activated_at.year, state.activated_at.month) < (today.year, today.month):
            db.reset_kill_switch(today, category="auto_month_reset", reason="new month started", auto=True, engine=engine)
            _log.info(json.dumps({"event": "kill_switch_auto_reset", "engine": engine, "date": today.isoformat()}))

    # Step 2: read state after potential auto-reset
    state = db.get_kill_switch_state(engine)

    # Step 3: already active this month — block without re-activating
    if state.active:
        return GuardrailResult(
            allowed=False,
            reason="short_monthly_kill_switch",
            meta={"monthly_drawdown": state.monthly_dd, "kill_dd": config.get("kill_dd", -0.08), "persisted": True},
        )

    # Step 4: check current DD
    monthly_dd = float(sb.get("monthly_drawdown", 0.0))
    kill_dd = float(config.get("kill_dd", -0.08))
    if monthly_dd <= kill_dd:
        db.activate_kill_switch(today, monthly_dd, engine)

        alert_dir = Path("alerts")
        alert_dir.mkdir(parents=True, exist_ok=True)
        alert_path = alert_dir / f"kill_switch_{today.isoformat()}.json"
        alert_payload = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "engine": engine,
            "monthly_dd": monthly_dd,
            "kill_dd": kill_dd,
            "date": today.isoformat(),
        }
        alert_path.write_text(json.dumps(alert_payload))

        _log.error(json.dumps({"event": "kill_switch_activated", "engine": engine, "monthly_dd": monthly_dd, "kill_dd": kill_dd, "date": today.isoformat()}))
        return GuardrailResult(
            allowed=False,
            reason="short_monthly_kill_switch",
            meta={"monthly_drawdown": monthly_dd, "kill_dd": kill_dd, "persisted": False},
        )

    # Step 5: all clear
    return GuardrailResult(
        allowed=True,
        reason="ok",
        meta={"monthly_drawdown": monthly_dd},
    )


def log_risk_cycle(
    engine: str,
    date: str,
    guardrail: GuardrailResult,
    orders_proposed: int,
    orders_filled: int,
    kill_switch_active: bool,
    extra: dict | None = None,
) -> None:
    """Emit one JSON line per cycle to the structured risk logger."""
    logger = logging.getLogger("risk_guardrails")

    record: dict = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "engine": engine,
        "date": date,
        "guardrail_reason": guardrail.reason,
        "allowed": guardrail.allowed,
        "orders_proposed": orders_proposed,
        "orders_filled": orders_filled,
        "kill_switch_active": kill_switch_active,
    }

    if extra:
        record.update(extra)

    if logger.isEnabledFor(logging.DEBUG):
        record["meta"] = guardrail.meta

    logger.info(json.dumps(record))
