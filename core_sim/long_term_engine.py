"""Deterministic long-term sleeve helpers (v1).

Agent-teams-lite boundaries (see AGENTS.md):
- Spec/policy: numeric knobs live in POLICY.md + config/policy.v1.yaml (validated by schema).
- Data: caller supplies OHLCV snapshot / prices; missing inputs -> documented skip (no silent fills).
- Engines: pure functions in this module (targets, drift, rebalance gate, intents).
- Risk/Core sim: allocator + guardrails consume intents downstream; this module does not apply 30/70 or 20/80.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import floor
from typing import Iterable, Mapping


@dataclass(frozen=True)
class SatelliteLimits:
    max_satellite_weight_total: float
    max_weight_per_satellite_line: float
    max_satellite_names: int


@dataclass(frozen=True)
class LongTermEngineConfig:
    """Long sleeve policy inside the 70% bucket (weights here sum to 1.0 of the long sleeve)."""

    core_lines: tuple[tuple[str, float], ...]
    satellite_lines: tuple[tuple[str, float], ...]
    satellite_limits: SatelliteLimits
    drift_rebalance_threshold_pp: float
    drift_convention: str  # v1: "per_line" only (see POLICY.md)
    rebalance_rule: str  # v1: calendar rule (monthly/weekly)
    max_long_rebalance_turnover_pct: float | None
    satellite_markets: frozenset[str]  # v1 default: US only


def is_first_us_trading_day_of_month(trading_day: date, us_sessions: Iterable[date]) -> bool:
    """True when `trading_day` is the earliest US session date in its calendar month."""
    sessions = frozenset(us_sessions)
    if trading_day not in sessions:
        return False
    month_sessions = [d for d in sessions if d.year == trading_day.year and d.month == trading_day.month]
    if not month_sessions:
        return False
    return trading_day == min(month_sessions)


def is_first_us_trading_day_of_week(trading_day: date, us_sessions: Iterable[date]) -> bool:
    """True when `trading_day` is the earliest US session date in its ISO calendar week."""
    sessions = frozenset(us_sessions)
    if trading_day not in sessions:
        return False
    iso_year, iso_week, _ = trading_day.isocalendar()
    week_sessions = [
        d for d in sessions if d.isocalendar()[:2] == (iso_year, iso_week)
    ]
    if not week_sessions:
        return False
    return trading_day == min(week_sessions)


def is_rebalance_day_by_rule(
    *,
    trading_day: date,
    us_sessions: Iterable[date],
    rebalance_rule: str,
) -> bool:
    """Evaluate rebalance day according to configured calendar rule."""
    if rebalance_rule == "first_us_trading_day_of_calendar_month":
        return is_first_us_trading_day_of_month(trading_day, us_sessions)
    if rebalance_rule == "first_us_trading_day_of_calendar_week":
        return is_first_us_trading_day_of_week(trading_day, us_sessions)
    raise ValueError(f"unsupported rebalance_rule: {rebalance_rule}")


def _targets_from_config(config: LongTermEngineConfig) -> dict[str, float]:
    out: dict[str, float] = {}
    for symbol, w in (*config.core_lines, *config.satellite_lines):
        out[str(symbol)] = float(w)
    return out


def validate_long_term_engine_config(config: LongTermEngineConfig) -> None:
    """Fail fast on inconsistent policy (sum of weights, satellite caps)."""
    if config.drift_convention != "per_line":
        raise ValueError("long_term_engine v1 only supports drift_convention='per_line'")

    core = dict(config.core_lines)
    sat = dict(config.satellite_lines)
    if len(core) < 2 or len(core) > 3:
        raise ValueError("long_term_engine v1 expects 2–3 core lines")

    if len(sat) > int(config.satellite_limits.max_satellite_names):
        raise ValueError("satellite line count exceeds max_satellite_names")

    sat_sum = sum(float(w) for w in sat.values())
    if sat_sum - float(config.satellite_limits.max_satellite_weight_total) > 1e-9:
        raise ValueError("satellite target weights exceed max_satellite_weight_total")

    for sym, w in sat.items():
        if float(w) - float(config.satellite_limits.max_weight_per_satellite_line) > 1e-9:
            raise ValueError(f"satellite line {sym} exceeds max_weight_per_satellite_line")

    total = sum(float(w) for w in (*core.values(), *sat.values()))
    if abs(total - 1.0) > 1e-6:
        raise ValueError("long sleeve target weights must sum to 1.0")

    if config.drift_rebalance_threshold_pp <= 0:
        raise ValueError("drift_rebalance_threshold_pp must be positive")

    valid_rules = {
        "first_us_trading_day_of_calendar_month",
        "first_us_trading_day_of_calendar_week",
    }
    if config.rebalance_rule not in valid_rules:
        raise ValueError(
            "long_term_engine v1 rebalance_rule must be one of: "
            "first_us_trading_day_of_calendar_month, "
            "first_us_trading_day_of_calendar_week"
        )

    for m in config.satellite_markets:
        if str(m).upper() != "US":
            raise ValueError("long_term_engine v1 satellite_markets must be US-only")


def target_weights(config: LongTermEngineConfig) -> dict[str, float]:
    validate_long_term_engine_config(config)
    return _targets_from_config(config)


def current_weights_mtm(
    *,
    long_bucket_mtm: float,
    positions_qty: Mapping[str, float],
    prices: Mapping[str, float],
    universe: Iterable[str],
) -> dict[str, float]:
    """Current sleeve weights from MTM (qty already reflects corporate actions applied upstream)."""
    if long_bucket_mtm <= 0:
        return {s: 0.0 for s in universe}
    weights: dict[str, float] = {}
    for sym in universe:
        qty = float(positions_qty.get(sym, 0.0))
        px = float(prices.get(sym, 0.0))
        weights[sym] = (qty * px) / long_bucket_mtm if long_bucket_mtm > 0 else 0.0
    return weights


def drift_per_line_pp(target: Mapping[str, float], current: Mapping[str, float]) -> dict[str, float]:
    """Absolute drift in percentage points (pp) for each symbol in the union of keys."""
    keys = sorted(frozenset(target.keys()) | frozenset(current.keys()))
    return {k: abs(float(target.get(k, 0.0)) - float(current.get(k, 0.0))) * 100.0 for k in keys}


def should_rebalance_long(
    *,
    is_rebalance_day: bool,
    drift_pp_by_symbol: Mapping[str, float],
    drift_threshold_pp: float,
) -> bool:
    """Calendar gate: rebalance day AND any line drift exceeds threshold (per_line convention)."""
    if not is_rebalance_day:
        return False
    if not drift_pp_by_symbol:
        return False
    return any(float(v) > float(drift_threshold_pp) for v in drift_pp_by_symbol.values())


def scale_weight_deltas_for_turnover(
    target: Mapping[str, float],
    current: Mapping[str, float],
    max_turnover_weight: float,
) -> dict[str, float]:
    """Scale (target-current) weights so sum(abs(delta)) <= max_turnover_weight."""
    symbols = sorted(frozenset(target.keys()) | frozenset(current.keys()))
    raw = {s: float(target.get(s, 0.0)) - float(current.get(s, 0.0)) for s in symbols}
    sum_abs = sum(abs(v) for v in raw.values())
    if sum_abs <= 1e-15 or max_turnover_weight <= 0:
        return {s: 0.0 for s in symbols}
    if sum_abs <= max_turnover_weight + 1e-12:
        return raw
    scale = max_turnover_weight / sum_abs
    return {s: float(raw[s]) * scale for s in symbols}


def build_long_term_orders_intent(
    config: LongTermEngineConfig,
    *,
    trading_day: date,
    us_sessions: Iterable[date],
    long_bucket_mtm: float,
    long_cash: float,
    positions_qty: Mapping[str, float],
    prices: Mapping[str, float],
    whitelist_us: frozenset[str],
    halt_long_engine: bool = False,
    data_quality_halt: bool = False,
) -> tuple[list[dict[str, object]], list[dict[str, str]], dict[str, object]]:
    """Produce `orders_intent` for the long sleeve on a candidate rebalance day.

    Policy for missing prices on a rebalance attempt: abort the whole long cycle (no partial
    rebalance) — see POLICY.md §11.
    """
    validate_long_term_engine_config(config)
    skips: list[dict[str, str]] = []
    metrics: dict[str, object] = {
        "trading_day": trading_day.isoformat(),
        "rebalance_rule": config.rebalance_rule,
        "is_long_rebalance_day": is_rebalance_day_by_rule(
            trading_day=trading_day,
            us_sessions=us_sessions,
            rebalance_rule=config.rebalance_rule,
        ),
        "intents_generated": 0,
    }
    # Backward-compat metric key kept for old downstream consumers.
    metrics["is_first_us_trading_day_of_month"] = is_first_us_trading_day_of_month(
        trading_day, us_sessions
    )

    if halt_long_engine:
        skips.append({"symbol": "*", "reason": "halt_long_engine"})
        return [], skips, metrics

    if data_quality_halt:
        skips.append({"symbol": "*", "reason": "data_quality_halt"})
        return [], skips, metrics

    if long_bucket_mtm <= 0:
        skips.append({"symbol": "*", "reason": "non_positive_long_bucket_mtm"})
        return [], skips, metrics

    targets = target_weights(config)
    universe = sorted(targets.keys())

    for sym in universe:
        if sym not in whitelist_us:
            skips.append({"symbol": "*", "reason": f"symbol_not_whitelisted:{sym}"})
            return [], skips, metrics

    is_day = bool(metrics["is_long_rebalance_day"])
    if not is_day:
        skips.append({"symbol": "*", "reason": "not_long_rebalance_day"})
        return [], skips, metrics

    for sym in universe:
        if sym not in prices or float(prices[sym]) <= 0:
            skips.append({"symbol": "*", "reason": "missing_or_invalid_price_abort_cycle"})
            return [], skips, metrics

    current = current_weights_mtm(
        long_bucket_mtm=long_bucket_mtm,
        positions_qty=positions_qty,
        prices=prices,
        universe=universe,
    )
    drift_pp = drift_per_line_pp(targets, current)
    metrics["drift_pp_by_symbol"] = drift_pp
    metrics["current_weights"] = dict(current)
    metrics["target_weights"] = dict(targets)

    if not should_rebalance_long(
        is_rebalance_day=True,
        drift_pp_by_symbol=drift_pp,
        drift_threshold_pp=config.drift_rebalance_threshold_pp,
    ):
        skips.append({"symbol": "*", "reason": "within_drift_band"})
        return [], skips, metrics

    deltas_weights: dict[str, float] = {
        s: float(targets[s]) - float(current.get(s, 0.0)) for s in universe
    }
    if config.max_long_rebalance_turnover_pct is not None:
        deltas_weights = scale_weight_deltas_for_turnover(
            targets,
            current,
            float(config.max_long_rebalance_turnover_pct),
        )
        metrics["targets_scaled_for_turnover_cap"] = True
    else:
        metrics["targets_scaled_for_turnover_cap"] = False

    sat_set = frozenset(sym for sym, _ in config.satellite_lines)

    deltas = {s: float(deltas_weights.get(s, 0.0)) for s in universe}
    intents: list[dict[str, object]] = []

    def reason_for(sym: str) -> str:
        if sym in sat_set:
            return "long_satellite_add" if deltas[sym] >= 0 else "long_satellite_trim"
        return "long_rebalance_core" if deltas[sym] >= 0 else "long_rebalance_core_trim"

    # Pass 1: sells (raise cash deterministically) in ascending symbol order.
    cash_sim = float(long_cash)
    for sym in sorted(universe):
        px = float(prices[sym])
        delta_w = float(deltas[sym])
        if delta_w >= 0:
            continue
        notional = -delta_w * float(long_bucket_mtm)
        if notional <= 0:
            continue
        max_sell_qty = float(positions_qty.get(sym, 0.0))
        raw_qty = notional / px
        sell_qty = float(floor(min(max_sell_qty, raw_qty)))
        if sell_qty <= 0:
            continue
        trade_notional = sell_qty * px
        cash_sim += trade_notional
        drift_sym = float(drift_pp[sym])
        intents.append(
            {
                "symbol": sym,
                "market": "US",
                "bucket": "long",
                "side": "SELL",
                "qty": sell_qty,
                "intent_notional": float(trade_notional),
                "reason_code": reason_for(sym),
                "target_weight": float(targets[sym]),
                "current_weight": float(current[sym]),
                "drift_pp": drift_sym,
                "risk_snapshot": {
                    "long_bucket_mtm": float(long_bucket_mtm),
                    "long_cash_sim": float(cash_sim),
                    "drift_rebalance_threshold_pp": float(config.drift_rebalance_threshold_pp),
                },
            }
        )

    # Pass 2: buys in ascending symbol order, constrained by simulated cash.
    for sym in sorted(universe):
        px = float(prices[sym])
        delta_w = float(deltas[sym])
        if delta_w <= 0:
            continue
        notional = delta_w * float(long_bucket_mtm)
        if notional <= 0:
            continue
        raw_qty = notional / px
        buy_qty = float(floor(raw_qty))
        trade_notional = buy_qty * px
        if buy_qty <= 0:
            continue
        if trade_notional > cash_sim + 1e-9:
            # Clip to cash (deterministic); skip remainder until next cycle.
            buy_qty = float(floor(cash_sim / px)) if px > 0 else 0.0
            trade_notional = buy_qty * px
        if buy_qty <= 0:
            skips.append({"symbol": sym, "reason": "insufficient_cash_for_buy"})
            continue
        cash_sim -= trade_notional
        drift_sym = float(drift_pp[sym])
        intents.append(
            {
                "symbol": sym,
                "market": "US",
                "bucket": "long",
                "side": "BUY",
                "qty": buy_qty,
                "intent_notional": float(trade_notional),
                "reason_code": reason_for(sym),
                "target_weight": float(targets[sym]),
                "current_weight": float(current[sym]),
                "drift_pp": drift_sym,
                "risk_snapshot": {
                    "long_bucket_mtm": float(long_bucket_mtm),
                    "long_cash_sim": float(cash_sim),
                    "drift_rebalance_threshold_pp": float(config.drift_rebalance_threshold_pp),
                },
            }
        )

    metrics["intents_generated"] = len(intents)
    return intents, skips, metrics


def long_term_engine_config_from_policy_dict(payload: Mapping[str, object]) -> LongTermEngineConfig:
    """Build a typed config from the `long_term_engine` subtree of policy.v1.yaml."""
    core_raw = payload["core_lines"]
    sat_raw = payload["satellite_lines"]
    lim = payload["satellite_limits"]

    def _lines(raw: object) -> tuple[tuple[str, float], ...]:
        if not isinstance(raw, list):
            raise TypeError("lines must be a list")
        out: list[tuple[str, float]] = []
        for row in raw:
            if not isinstance(row, dict):
                raise TypeError("line rows must be mappings")
            sym = str(row["symbol"]).strip()
            out.append((sym, float(row["target_weight"])))
        return tuple(out)

    markets = payload.get("satellite_markets", ["US"])
    if not isinstance(markets, list):
        raise TypeError("satellite_markets must be a list")
    return LongTermEngineConfig(
        core_lines=_lines(core_raw),
        satellite_lines=_lines(sat_raw),
        satellite_limits=SatelliteLimits(
            max_satellite_weight_total=float(lim["max_satellite_weight_total"]),
            max_weight_per_satellite_line=float(lim["max_weight_per_satellite_line"]),
            max_satellite_names=int(lim["max_satellite_names"]),
        ),
        drift_rebalance_threshold_pp=float(payload["drift_rebalance_threshold_pp"]),
        drift_convention=str(payload["drift_convention"]),
        rebalance_rule=str(payload["rebalance_rule"]),
        max_long_rebalance_turnover_pct=(
            None
            if payload.get("max_long_rebalance_turnover_pct") is None
            else float(payload["max_long_rebalance_turnover_pct"])
        ),
        satellite_markets=frozenset(str(m).upper() for m in markets),
    )
