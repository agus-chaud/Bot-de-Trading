"""Unit tests for PendingOrderQueue."""

from __future__ import annotations

from datetime import datetime, timezone


from core_sim.pending_order_queue import PendingOrder, PendingOrderQueue


def _make_order(order_id: str = "ord-1", symbol: str = "SPY") -> PendingOrder:
    return PendingOrder(
        order_id=order_id,
        symbol=symbol,
        qty=10,
        side="buy",
        market="US",
        engine="short",
        proposed_at=datetime.now(tz=timezone.utc).isoformat(),
    )


def test_pending_order_queue_add_pop():
    q = PendingOrderQueue()
    q.add(_make_order("ord-1", "SPY"))
    q.add(_make_order("ord-2", "IWM"))

    assert q.size() == 2

    orders = q.pop_all()
    assert len(orders) == 2
    assert {o.symbol for o in orders} == {"SPY", "IWM"}

    # Queue must be empty after pop_all
    assert q.size() == 0
    assert q.pop_all() == []


def test_pending_order_queue_peek_does_not_consume():
    q = PendingOrderQueue()
    q.add(_make_order("ord-1"))
    q.add(_make_order("ord-2"))

    peeked = q.peek()
    assert len(peeked) == 2
    # Peek must not consume
    assert q.size() == 2


def test_pending_order_queue_clear():
    q = PendingOrderQueue()
    q.add(_make_order())
    q.clear()
    assert q.size() == 0


def test_pending_order_queue_empty_on_init():
    q = PendingOrderQueue()
    assert q.size() == 0
    assert q.peek() == []


def test_pending_order_fields_are_stored():
    now = datetime.now(tz=timezone.utc).isoformat()
    order = PendingOrder(
        order_id="x-42",
        symbol="QQQ",
        qty=5,
        side="sell",
        market="US",
        engine="long",
        proposed_at=now,
        meta={"bucket": "long"},
    )
    q = PendingOrderQueue()
    q.add(order)

    retrieved = q.pop_all()[0]
    assert retrieved.order_id == "x-42"
    assert retrieved.symbol == "QQQ"
    assert retrieved.qty == 5
    assert retrieved.side == "sell"
    assert retrieved.engine == "long"
    assert retrieved.meta == {"bucket": "long"}
