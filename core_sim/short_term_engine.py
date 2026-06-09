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
    rsi_lookback: int = 14
    rsi_overbought_entry: float = 80.0
    rsi_exit_threshold: float = 45.0
    allow_leverage: bool = False


@dataclass(frozen=True)
class RiskCaps:
    """Risk caps needed for deterministic sizing."""

    max_position_pct: float
    max_sector_pct: float


def _signal_filter_audit(
    *,
    close: float,
    close_n_days_ago: float,
    volume_percentile: float,
    vol_20d: float,
    rsi: float,
    session_valid: bool,
    config: ShortEngineConfig,
) -> tuple[dict[str, object], list[str], list[str], str | None]:
    """Lateral audit of hard filters; first_fail_reason follows fail-fast order."""
    momentum = (close / close_n_days_ago) - 1.0 if close > 0 and close_n_days_ago > 0 else None
    features: dict[str, object] = {
        "momentum_20d": momentum,
        "rsi": rsi,
        "volume_percentile": volume_percentile,
        "vol_20d": vol_20d,
    }
    passed: list[str] = []
    failed: list[str] = []
    first_fail: str | None = None

    def _fail(code: str) -> None:
        nonlocal first_fail
        if code not in failed:
            failed.append(code)
        if first_fail is None:
            first_fail = code

    if session_valid:
        passed.append("session")
    else:
        _fail("invalid_session")

    if close > 0 and close_n_days_ago > 0:
        passed.append("price")
    else:
        _fail("invalid_price")

    if vol_20d > 0:
        passed.append("volatility_input")
    else:
        _fail("invalid_volatility")

    if volume_percentile >= config.liquidity_percentile_min:
        passed.append("liquidity")
    else:
        _fail("liquidity_below_threshold")

    if vol_20d <= config.volatility_20d_max:
        passed.append("volatility")
    else:
        _fail("volatility_above_threshold")

    if rsi <= config.rsi_overbought_entry:
        passed.append("rsi")
    else:
        _fail("rsi_overbought")

    if momentum is not None and momentum > 0:
        passed.append("momentum")
    elif momentum is not None:
        _fail("non_positive_momentum")

    return features, passed, failed, first_fail


def _attach_signal_drivers(
    payload: dict[str, object],
    *,
    features: dict[str, object],
    passed_filters: list[str],
    failed_filters: list[str],
    skip_reason: str | None,
) -> dict[str, object]:
    drivers: dict[str, object] = dict(features)
    drivers["passed_filters"] = list(passed_filters)
    drivers["failed_filters"] = list(failed_filters)
    if skip_reason is not None:
        drivers["skip_reason"] = skip_reason
    payload["drivers"] = drivers
    return payload


def _sizing_drivers(
    *,
    raw_notional: float,
    symbol_cap: float,
    symbol_headroom: float,
    sector_headroom: float,
    short_cash: float,
    tranche_left: float,
    geo_cap: float | None,
    intent_notional: float,
    price: float,
    lot_size: int,
    rounded_qty: float,
    final_notional: float,
    skip_reason: str | None = None,
) -> dict[str, object]:
    drivers: dict[str, object] = {
        "raw_notional": float(raw_notional),
        "symbol_cap": float(symbol_cap),
        "symbol_headroom": float(symbol_headroom),
        "sector_headroom": float(sector_headroom),
        "short_cash": float(short_cash),
        "intent_notional_pre_round": float(intent_notional),
        "price": float(price),
        "lot_size": int(lot_size),
        "rounded_qty": float(rounded_qty),
        "final_notional": float(final_notional),
    }
    if tranche_left != float("inf"):
        drivers["tranche_headroom"] = float(tranche_left)
    if geo_cap is not None:
        drivers["geo_headroom"] = float(geo_cap)
    if skip_reason is not None:
        drivers["skip_reason"] = skip_reason
    return drivers


def compute_rsi(closes: list[float], lookback: int = 14) -> float | None:
    """Compute RSI in [0,100] from closing prices.

    Uses average gains/losses over the last `lookback` differences.
    Returns None when history is insufficient or contains invalid prices.
    """
    if lookback < 1 or len(closes) < (lookback + 1):
        return None
    window = closes[-(lookback + 1) :]
    if any(float(c) <= 0 for c in window):
        return None

    gains = 0.0
    losses = 0.0
    for i in range(1, len(window)):
        delta = float(window[i]) - float(window[i - 1])
        if delta > 0:
            gains += delta
        elif delta < 0:
            losses += abs(delta)

    avg_gain = gains / float(lookback)
    avg_loss = losses / float(lookback)
    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return float(rsi)


def compute_signal_candidates(
    market_snapshot: list[dict[str, object]],
    config: ShortEngineConfig,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Compute score and apply hard filters, returning candidates + skip reasons."""
    candidates: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []

    for row in market_snapshot:
        symbol = str(row.get("symbol", "")).strip()
        if not symbol:
            skipped.append(
                _attach_signal_drivers(
                    {"symbol": "<missing>", "reason": "missing_symbol"},
                    features={},
                    passed_filters=[],
                    failed_filters=["missing_symbol"],
                    skip_reason="missing_symbol",
                )
            )
            continue

        missing_fields = [
            field
            for field in (
                "market",
                "close",
                "close_n_days_ago",
                "volume_percentile",
                "vol_20d",
                "rsi",
                "session_valid",
            )
            if field not in row
        ]
        if missing_fields:
            reason = f"missing_fields:{','.join(sorted(missing_fields))}"
            skipped.append(
                _attach_signal_drivers(
                    {"symbol": symbol, "reason": reason},
                    features={},
                    passed_filters=[],
                    failed_filters=[reason],
                    skip_reason=reason,
                )
            )
            continue

        close = float(row["close"])
        close_n_days_ago = float(row["close_n_days_ago"])
        volume_percentile = float(row["volume_percentile"])
        vol_20d = float(row["vol_20d"])
        rsi = float(row["rsi"])
        session_valid = bool(row["session_valid"])
        features, passed_filters, failed_filters, _first_fail = _signal_filter_audit(
            close=close,
            close_n_days_ago=close_n_days_ago,
            volume_percentile=volume_percentile,
            vol_20d=vol_20d,
            rsi=rsi,
            session_valid=session_valid,
            config=config,
        )

        if not session_valid:
            skipped.append(
                _attach_signal_drivers(
                    {"symbol": symbol, "reason": "invalid_session"},
                    features=features,
                    passed_filters=passed_filters,
                    failed_filters=failed_filters,
                    skip_reason="invalid_session",
                )
            )
            continue

        if close <= 0 or close_n_days_ago <= 0:
            skipped.append(
                _attach_signal_drivers(
                    {"symbol": symbol, "reason": "invalid_price"},
                    features=features,
                    passed_filters=passed_filters,
                    failed_filters=failed_filters,
                    skip_reason="invalid_price",
                )
            )
            continue
        if vol_20d <= 0:
            skipped.append(
                _attach_signal_drivers(
                    {"symbol": symbol, "reason": "invalid_volatility"},
                    features=features,
                    passed_filters=passed_filters,
                    failed_filters=failed_filters,
                    skip_reason="invalid_volatility",
                )
            )
            continue
        if volume_percentile < config.liquidity_percentile_min:
            skipped.append(
                _attach_signal_drivers(
                    {"symbol": symbol, "reason": "liquidity_below_threshold"},
                    features=features,
                    passed_filters=passed_filters,
                    failed_filters=failed_filters,
                    skip_reason="liquidity_below_threshold",
                )
            )
            continue
        if vol_20d > config.volatility_20d_max:
            skipped.append(
                _attach_signal_drivers(
                    {"symbol": symbol, "reason": "volatility_above_threshold"},
                    features=features,
                    passed_filters=passed_filters,
                    failed_filters=failed_filters,
                    skip_reason="volatility_above_threshold",
                )
            )
            continue
        if rsi > config.rsi_overbought_entry:
            skipped.append(
                _attach_signal_drivers(
                    {"symbol": symbol, "reason": "rsi_overbought"},
                    features=features,
                    passed_filters=passed_filters,
                    failed_filters=failed_filters,
                    skip_reason="rsi_overbought",
                )
            )
            continue

        signal_score = float(features["momentum_20d"])
        if signal_score <= 0:
            skipped.append(
                _attach_signal_drivers(
                    {"symbol": symbol, "reason": "non_positive_momentum"},
                    features=features,
                    passed_filters=passed_filters,
                    failed_filters=failed_filters,
                    skip_reason="non_positive_momentum",
                )
            )
            continue

        candidate = _attach_signal_drivers(
            dict(row),
            features=features,
            passed_filters=passed_filters,
            failed_filters=failed_filters,
            skip_reason=None,
        )
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
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, int]]:
    """Build deterministic orders_intent payloads and diagnostics."""
    current_symbol_notional = current_symbol_notional or {}
    current_sector_exposure_pct = current_sector_exposure_pct or {}
    lot_size_by_market = lot_size_by_market or {}
    skip_reasons: list[dict[str, object]] = []

    metrics = {
        "symbols_selected": len(selected_candidates),
        "intents_generated": 0,
        "symbols_skipped_after_sizing": 0,
    }

    if kill_switch_active:
        return [], [
            {
                "symbol": "*",
                "reason": "short_kill_switch_active",
                "drivers": {"skip_reason": "short_kill_switch_active"},
            }
        ], metrics

    if short_equity <= 0 or short_cash <= 0:
        return [], [
            {
                "symbol": "*",
                "reason": "non_positive_short_budget",
                "drivers": {"skip_reason": "non_positive_short_budget"},
            }
        ], metrics

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
        short_cash_before = short_cash
        tranche_before = tranche_left
        if price <= 0 or vol_20d <= 0:
            skip_reasons.append(
                {
                    "symbol": symbol,
                    "reason": "invalid_price_or_volatility",
                    "drivers": _sizing_drivers(
                        raw_notional=0.0,
                        symbol_cap=0.0,
                        symbol_headroom=0.0,
                        sector_headroom=0.0,
                        short_cash=short_cash_before,
                        tranche_left=tranche_before,
                        geo_cap=None,
                        intent_notional=0.0,
                        price=price,
                        lot_size=1,
                        rounded_qty=0.0,
                        final_notional=0.0,
                        skip_reason="invalid_price_or_volatility",
                    ),
                }
            )
            metrics["symbols_skipped_after_sizing"] += 1
            continue

        raw_notional = risk_budget_trade / vol_20d
        symbol_cap = short_equity * risk_caps.max_position_pct
        existing_symbol_notional = float(current_symbol_notional.get(symbol, 0.0))
        symbol_headroom = max(0.0, symbol_cap - existing_symbol_notional)

        existing_sector_pct = float(current_sector_exposure_pct.get(sector, 0.0))
        sector_headroom = max(0.0, risk_caps.max_sector_pct - existing_sector_pct) * short_equity

        intent_notional = min(raw_notional, symbol_headroom, sector_headroom, short_cash, tranche_left)
        geo_cap: float | None = None
        if geo_left is not None:
            mcap = str(market).upper()
            geo_cap = max(0.0, float(geo_left.get(mcap, 0.0)))
            intent_notional = min(intent_notional, geo_cap)
        if intent_notional <= 0:
            skip_reasons.append(
                {
                    "symbol": symbol,
                    "reason": "no_risk_headroom",
                    "drivers": _sizing_drivers(
                        raw_notional=raw_notional,
                        symbol_cap=symbol_cap,
                        symbol_headroom=symbol_headroom,
                        sector_headroom=sector_headroom,
                        short_cash=short_cash_before,
                        tranche_left=tranche_before,
                        geo_cap=geo_cap,
                        intent_notional=intent_notional,
                        price=price,
                        lot_size=1,
                        rounded_qty=0.0,
                        final_notional=0.0,
                        skip_reason="no_risk_headroom",
                    ),
                }
            )
            metrics["symbols_skipped_after_sizing"] += 1
            continue

        lot_size = int(lot_size_by_market.get(market, 1))
        if lot_size <= 0:
            lot_size = 1
        raw_qty = intent_notional / price
        rounded_qty = floor(raw_qty / lot_size) * lot_size
        if rounded_qty <= 0:
            skip_reasons.append(
                {
                    "symbol": symbol,
                    "reason": "qty_below_lot_size",
                    "drivers": _sizing_drivers(
                        raw_notional=raw_notional,
                        symbol_cap=symbol_cap,
                        symbol_headroom=symbol_headroom,
                        sector_headroom=sector_headroom,
                        short_cash=short_cash_before,
                        tranche_left=tranche_before,
                        geo_cap=geo_cap,
                        intent_notional=intent_notional,
                        price=price,
                        lot_size=lot_size,
                        rounded_qty=rounded_qty,
                        final_notional=0.0,
                        skip_reason="qty_below_lot_size",
                    ),
                }
            )
            metrics["symbols_skipped_after_sizing"] += 1
            continue

        final_notional = rounded_qty * price
        if final_notional > short_cash and not candidate.get("allow_leverage", False):
            skip_reasons.append(
                {
                    "symbol": symbol,
                    "reason": "insufficient_cash",
                    "drivers": _sizing_drivers(
                        raw_notional=raw_notional,
                        symbol_cap=symbol_cap,
                        symbol_headroom=symbol_headroom,
                        sector_headroom=sector_headroom,
                        short_cash=short_cash_before,
                        tranche_left=tranche_before,
                        geo_cap=geo_cap,
                        intent_notional=intent_notional,
                        price=price,
                        lot_size=lot_size,
                        rounded_qty=rounded_qty,
                        final_notional=final_notional,
                        skip_reason="insufficient_cash",
                    ),
                }
            )
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
            "drivers": _sizing_drivers(
                raw_notional=raw_notional,
                symbol_cap=symbol_cap,
                symbol_headroom=symbol_headroom,
                sector_headroom=sector_headroom,
                short_cash=short_cash_before,
                tranche_left=tranche_before,
                geo_cap=geo_cap,
                intent_notional=intent_notional,
                price=price,
                lot_size=lot_size,
                rounded_qty=rounded_qty,
                final_notional=final_notional,
            ),
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
