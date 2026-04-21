"""Core event engine/backtester for paper-first daily simulation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from .calendar_store import TradingCalendarStore
from .corporate_actions import CorporateActionsStore


EventHandler = Callable[..., Any]


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
    ) -> None:
        self.generate_signals = generate_signals
        self.propose_orders = propose_orders
        self.risk_check = risk_check
        self.fill_orders = fill_orders
        self.update_ledger = update_ledger
        self.calendar_store = calendar_store
        self.corporate_actions_store = corporate_actions_store

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

        risk_checked_orders = self.risk_check(**{**ctx, "proposed_orders": proposed_orders})
        events.append(EventStep(name="RiskChecked", payload=risk_checked_orders))

        fills = self.fill_orders(
            trading_day=trading_day,
            approved_orders=risk_checked_orders,
            daily_bars=daily_bars,
        )
        events.append(EventStep(name="OrdersFilled", payload=fills))

        ledger_snapshot = self.update_ledger(trading_day=trading_day, fills=fills, daily_bars=daily_bars)
        events.append(EventStep(name="LedgerUpdated", payload=ledger_snapshot))

        return events
