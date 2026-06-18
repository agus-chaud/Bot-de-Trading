"""Core event engine/backtester for paper-first daily simulation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable

from .calendar_store import TradingCalendarStore
from .corporate_actions import CorporateActionsStore
from .pending_order_queue import PendingOrder, PendingOrderQueue


EventHandler = Callable[..., Any]


def approved_orders_from_risk_check(result: Any) -> list[dict[str, Any]]:
    """Normalize risk_check output: legacy list or enriched dict with approved_orders."""
    if isinstance(result, dict):
        orders = result.get("approved_orders")
        if isinstance(orders, list):
            return orders
    if isinstance(result, list):
        return result
    return []


@dataclass(frozen=True)
class EventStep:
    """Represents a single step in the daily event pipeline."""

    name: str
    payload: Any


class DailyEventBacktester:
    """Runs the deterministic daily event queue for the simulation core.

    Queue order:
        MarketOpen -> SignalGenerated -> OrdersProposed
        -> RiskChecked -> OrdersFilled -> LedgerUpdated
    """

    EVENT_ORDER = (
        "MarketOpen",
        "SignalGenerated",
        "OrdersProposed",
        "RiskChecked",
        "OrdersFilled",
        "LedgerUpdated",
    )

    def __init__(
        self,
        generate_signals: EventHandler,
        propose_orders: EventHandler,
        risk_check: EventHandler,
        fill_orders: EventHandler,
        update_ledger: EventHandler,
        calendar_store: TradingCalendarStore | None = None,
        corporate_actions_store: CorporateActionsStore | None = None,
        execution_mode: str = "auto",
        pending_queue: PendingOrderQueue | None = None,
    ) -> None:
        self.generate_signals = generate_signals
        self.propose_orders = propose_orders
        self.risk_check = risk_check
        self.fill_orders = fill_orders
        self.update_ledger = update_ledger
        self.calendar_store = calendar_store
        self.corporate_actions_store = corporate_actions_store
        self.execution_mode = execution_mode
        self.pending_queue = pending_queue

    def run_day(
        self,
        trading_day: date,
        daily_bars: dict[str, dict[str, float]],
        *,
        pipeline_context: dict[str, Any] | None = None,
    ) -> list[EventStep]:
        """Run one daily simulation cycle and return the event trace.

        `pipeline_context` optional se fusiona en el contexto pasado a
        `generate_signals`, `propose_orders` y `risk_check` (p.ej. historial OHLCV).
        `fill_orders` y `update_ledger` reciben solo los kwargs necesarios.
        """
        events: list[EventStep] = []
        symbols = set(daily_bars)

        us_session = (
            self.calendar_store.is_us_session(trading_day)
            if self.calendar_store is not None
            else True
        )
        ar_business_day = (
            self.calendar_store.is_ar_business_day(trading_day)
            if self.calendar_store is not None
            else True
        )
        raw_actions = (
            self.corporate_actions_store.get_for_day(trading_day, symbols=symbols)
            if self.corporate_actions_store is not None
            else ()
        )
        corporate_actions = [action.as_dict() for action in raw_actions]

        market_open_payload = {
            "trading_day": trading_day.isoformat(),
            "symbols": tuple(sorted(daily_bars)),
            "is_us_session": us_session,
            "is_ar_business_day": ar_business_day,
            "corporate_actions": corporate_actions,
        }
        events.append(EventStep(name="MarketOpen", payload=market_open_payload))

        ctx: dict[str, Any] = {
            "trading_day": trading_day,
            "daily_bars": daily_bars,
            "market_open": market_open_payload,
            **dict(pipeline_context or {}),
        }

        signals = self.generate_signals(**ctx)
        events.append(EventStep(name="SignalGenerated", payload=signals))

        proposed_orders = self.propose_orders(**{**ctx, "signals": signals})
        events.append(EventStep(name="OrdersProposed", payload=proposed_orders))

        risk_check_result = self.risk_check(**{**ctx, "proposed_orders": proposed_orders})
        risk_checked_orders = approved_orders_from_risk_check(risk_check_result)
        events.append(EventStep(name="RiskChecked", payload=risk_check_result))

        if self.execution_mode == "semi_auto" and self.pending_queue is not None:
            _logger = logging.getLogger("event_engine")
            now_iso = datetime.now(tz=timezone.utc).isoformat()

            # Stop loss orders bypass the queue — they must be filled immediately
            stop_loss_orders = [o for o in risk_checked_orders if o.get("reason") == "stop_loss"]
            queued_orders = [o for o in risk_checked_orders if o.get("reason") != "stop_loss"]

            for i, order in enumerate(queued_orders):
                self.pending_queue.add(
                    PendingOrder(
                        order_id=f"{trading_day.isoformat()}_{i}",
                        symbol=str(order.get("symbol", "")),
                        qty=int(float(order.get("qty", 0))),
                        side=str(order.get("side", "")),
                        market=str(order.get("market", "")),
                        engine=str(order.get("bucket", "short")),
                        proposed_at=now_iso,
                        meta=dict(order),
                    )
                )
            _logger.info(
                "orders queued for human approval",
                extra={"count": len(queued_orders), "trading_day": trading_day.isoformat()},
            )
            if stop_loss_orders:
                fills = self.fill_orders(
                    trading_day=trading_day,
                    approved_orders=stop_loss_orders,
                    daily_bars=daily_bars,
                )
                _logger.info(
                    "stop loss orders filled directly",
                    extra={"count": len(stop_loss_orders), "trading_day": trading_day.isoformat()},
                )
            else:
                fills = []
        else:
            fills = self.fill_orders(
                trading_day=trading_day,
                approved_orders=risk_checked_orders,
                daily_bars=daily_bars,
            )
        events.append(EventStep(name="OrdersFilled", payload=fills))

        ledger_snapshot = self.update_ledger(trading_day=trading_day, fills=fills, daily_bars=daily_bars)
        events.append(EventStep(name="LedgerUpdated", payload=ledger_snapshot))

        return events
