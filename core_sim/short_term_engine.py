"""Deterministic short-term engine helpers (v1)."""

from __future__ import annotations

from dataclasses import dataclass
from math import floor


@dataclass(frozen=True)
class ShortEngineConfig:
    """Configuration knobs for the short-term engine v1."""

    momentum_lookback_days: int
    liquidity_percentile_min: float
    volatility_20d_max: float
    top_k_per_market: int
    risk_budget_trade_pct: float
    allow_leverage: bool = False


@dataclass(frozen=True)
class RiskCaps:
    """Risk caps needed for deterministic sizing."""

    max_position_pct: float
    max_sector_pct: float


def compute_signal_candidates(
    market_snapshot: list[dict[str, object]],
    config: ShortEngineConfig,
) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    """Compute score and apply hard filters, returning candidates + skip reasons."""
    candidates: list[dict[str, object]] = []
    skipped: list[dict[str, str]] = []

    for row in market_snapshot:
        symbol = str(row.get("symbol", "")).strip()
        if not symbol:
            skipped.append({"symbol": "<missing>", "reason": "missing_symbol"})
            continue

        missing_fields = [
            field
            for field in (
                "market",
                "close",
                "close_n_days_ago",
                "volume_percentile",
                "vol_20d",
                "session_valid",
            )
            if field not in row
        ]
        if missing_fields:
            skipped.append(
                {
                    "symbol": symbol,
                    "reason": f"missing_fields:{','.join(sorted(missing_fields))}",
                }
            )
            continue

        if not bool(row["session_valid"]):
            skipped.append({"symbol": symbol, "reason": "invalid_session"})
            continue

        close = float(row["close"])
        close_n_days_ago = float(row["close_n_days_ago"])
        volume_percentile = float(row["volume_percentile"])
        vol_20d = float(row["vol_20d"])
        if close <= 0 or close_n_days_ago <= 0:
            skipped.append({"symbol": symbol, "reason": "invalid_price"})
            continue
        if vol_20d <= 0:
            skipped.append({"symbol": symbol, "reason": "invalid_volatility"})
            continue
        if volume_percentile < config.liquidity_percentile_min:
            skipped.append({"symbol": symbol, "reason": "liquidity_below_threshold"})
            continue
        if vol_20d > config.volatility_20d_max:
            skipped.append({"symbol": symbol, "reason": "volatility_above_threshold"})
            continue

        signal_score = (close / close_n_days_ago) - 1.0
        if signal_score <= 0:
            skipped.append({"symbol": symbol, "reason": "non_positive_momentum"})
            continue

        candidate = dict(row)
        candidate["signal_score"] = signal_score
        candidates.append(candidate)

    return candidates, skipped


def rank_top_k_by_market(
    candidates: list[dict[str, object]],
    top_k_per_market: int,
) -> list[dict[str, object]]:
    """Sort by signal score and keep top-k symbols per market."""
    grouped: dict[str, list[dict[str, object]]] = {}
    for candidate in candidates:
        market = str(candidate["market"])
        grouped.setdefault(market, []).append(candidate)

    selected: list[dict[str, object]] = []
    for market_candidates in grouped.values():
        ranked = sorted(
            market_candidates,
            key=lambda item: (float(item["signal_score"]), str(item["symbol"])),
            reverse=True,
        )
        selected.extend(ranked[:top_k_per_market])
    return selected


def build_orders_intent(
    selected_candidates: list[dict[str, object]],
    *,
    short_equity: float,
    short_cash: float,
    risk_budget_trade_pct: float,
    risk_caps: RiskCaps,
    current_symbol_notional: dict[str, float] | None = None,
    current_sector_exposure_pct: dict[str, float] | None = None,
    lot_size_by_market: dict[str, int] | None = None,
    kill_switch_active: bool = False,
    short_tranche_headroom: float | None = None,
    geo_headroom: dict[str, float] | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, str]], dict[str, int]]:
    """Build deterministic orders_intent payloads and diagnostics."""
    current_symbol_notional = current_symbol_notional or {}
    current_sector_exposure_pct = current_sector_exposure_pct or {}
    lot_size_by_market = lot_size_by_market or {}
    skip_reasons: list[dict[str, str]] = []

    metrics = {
        "symbols_selected": len(selected_candidates),
        "intents_generated": 0,
        "symbols_skipped_after_sizing": 0,
    }

    if kill_switch_active:
        return [], [{"symbol": "*", "reason": "short_kill_switch_active"}], metrics

    if short_equity <= 0 or short_cash <= 0:
        return [], [{"symbol": "*", "reason": "non_positive_short_budget"}], metrics

    tranche_left: float
    if short_tranche_headroom is not None:
        tranche_left = max(0.0, float(short_tranche_headroom))
    else:
        tranche_left = float("inf")
    geo_left: dict[str, float] | None = {k: float(v) for k, v in (geo_headroom or {}).items()} or None

    intents: list[dict[str, object]] = []
    risk_budget_trade = short_equity * risk_budget_trade_pct

    for candidate in selected_candidates:
        symbol = str(candidate["symbol"])
        market = str(candidate["market"])
        price = float(candidate["close"])
        vol_20d = float(candidate["vol_20d"])
        sector = str(candidate.get("sector", "UNKNOWN"))
        if price <= 0 or vol_20d <= 0:
            skip_reasons.append({"symbol": symbol, "reason": "invalid_price_or_volatility"})
            metrics["symbols_skipped_after_sizing"] += 1
            continue

        raw_notional = risk_budget_trade / vol_20d
        symbol_cap = short_equity * risk_caps.max_position_pct
        existing_symbol_notional = float(current_symbol_notional.get(symbol, 0.0))
        symbol_headroom = max(0.0, symbol_cap - existing_symbol_notional)

        existing_sector_pct = float(current_sector_exposure_pct.get(sector, 0.0))
        sector_headroom = max(0.0, risk_caps.max_sector_pct - existing_sector_pct) * short_equity

        intent_notional = min(raw_notional, symbol_headroom, sector_headroom, short_cash, tranche_left)
        if geo_left is not None:
            mcap = str(market).upper()
            intent_notional = min(
                intent_notional,
                max(0.0, float(geo_left.get(mcap, 0.0))),
            )
        if intent_notional <= 0:
            skip_reasons.append({"symbol": symbol, "reason": "no_risk_headroom"})
            metrics["symbols_skipped_after_sizing"] += 1
            continue

        lot_size = int(lot_size_by_market.get(market, 1))
        if lot_size <= 0:
            lot_size = 1
        raw_qty = intent_notional / price
        rounded_qty = floor(raw_qty / lot_size) * lot_size
        if rounded_qty <= 0:
            skip_reasons.append({"symbol": symbol, "reason": "qty_below_lot_size"})
            metrics["symbols_skipped_after_sizing"] += 1
            continue

        final_notional = rounded_qty * price
        if final_notional > short_cash and not candidate.get("allow_leverage", False):
            skip_reasons.append({"symbol": symbol, "reason": "insufficient_cash"})
            metrics["symbols_skipped_after_sizing"] += 1
            continue

        short_cash -= final_notional
        if tranche_left != float("inf"):
            tranche_left = max(0.0, tranche_left - final_notional)
        if geo_left is not None:
            mcap = str(market).upper()
            if mcap in geo_left:
                geo_left[mcap] = max(0.0, float(geo_left[mcap]) - final_notional)
        intent = {
            "symbol": symbol,
            "market": market,
            "bucket": "short",
            "side": "BUY",
            "qty": float(rounded_qty),
            "intent_notional": float(final_notional),
            "reason_code": "signal_entry",
            "signal_score": float(candidate["signal_score"]),
            "risk_snapshot": {
                "short_equity": short_equity,
                "short_cash_after_order": short_cash,
                "max_position_pct": risk_caps.max_position_pct,
                "max_sector_pct": risk_caps.max_sector_pct,
                "short_tranche_headroom_after_order": tranche_left,
                "geo_headroom_after_order": dict(geo_left) if geo_left is not None else None,
            },
        }
        intents.append(intent)
        metrics["intents_generated"] += 1

    return intents, skip_reasons, metrics
