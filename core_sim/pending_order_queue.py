"""Queue for orders pending human approval in semi_auto execution mode."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class PendingOrder:
    order_id: str
    symbol: str
    qty: int
    side: str          # "buy" | "sell"
    market: str        # "US" | "AR"
    engine: str        # "short" | "long"
    proposed_at: str   # ISO datetime
    meta: dict = field(default_factory=dict)


class PendingOrderQueue:
    """Queue of orders pending human approval (semi_auto mode)."""

    def __init__(self) -> None:
        self._orders: list[PendingOrder] = []

    def add(self, order: PendingOrder) -> None:
        self._orders.append(order)

    def pop_all(self) -> list[PendingOrder]:
        orders = list(self._orders)
        self._orders.clear()
        return orders

    def peek(self) -> list[PendingOrder]:
        """Return current orders without consuming them."""
        return list(self._orders)

    def size(self) -> int:
        return len(self._orders)

    def clear(self) -> None:
        self._orders.clear()
