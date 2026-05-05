"""Paper broker simulator with broker-like interface."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import date, datetime

from .cost_model import CostModel
from .ledger import PortfolioLedger


@dataclass(frozen=True)
class FillReport:
    """Execution report returned by place_order."""

    symbol: str
    side: str
    qty: float
    price: float
    market: str
    bucket: str
    fee: float
    cost_breakdown: dict[str, float | str]


class PaperBrokerSim:
    """Deterministic paper broker adapter used by strategy engines."""

    def __init__(self, ledger: PortfolioLedger, cost_model: CostModel) -> None:
        self._ledger = ledger
        self._cost_model = cost_model
        self._fills: list[FillReport] = []

    def place_order(
        self,
        order: dict[str, str | float],
        *,
        trading_day: date,
    ) -> dict[str, float | str | dict[str, float | str]]:
        """Simulate immediate fill and return execution details."""
        normalized = self._validate_order(order)
        cost = self._cost_model.compute_fill_cost(
            market=normalized["market"],
            side=normalized["side"],
            qty=normalized["qty"],
            price=normalized["price"],
            adv=normalized.get("adv"),
        )
        fee = cost.total
        fill_payload = {
            "symbol": normalized["symbol"],
            "side": normalized["side"],
            "qty": normalized["qty"],
            "price": normalized["price"],
            "market": normalized["market"],
            "bucket": normalized["bucket"],
            "fee": fee,
        }
        self._ledger.apply_fills(trading_day, [fill_payload])

        report = FillReport(
            symbol=normalized["symbol"],
            side=normalized["side"],
            qty=normalized["qty"],
            price=normalized["price"],
            market=normalized["market"],
            bucket=normalized["bucket"],
            fee=fee,
            cost_breakdown={
                "market": cost.market,
                "notional": cost.notional,
                "commission": cost.commission,
                "slippage": cost.slippage,
                "spread": cost.spread,
                "total": cost.total,
            },
        )
        self._fills.append(report)
        return asdict(report)

    def get_positions(self) -> dict[str, dict[str, float | str]]:
        """Return current open positions."""
        return {
            symbol: {
                "symbol": position.symbol,
                "market": position.market,
                "bucket": position.bucket,
                "qty": position.qty,
                "avg_cost": position.avg_cost,
            }
            for symbol, position in self._ledger.positions.items()
        }

    def get_cash(self) -> float:
        """Return available cash."""
        return self._ledger.cash

    def get_fills(self) -> list[dict[str, float | str | dict[str, float | str]]]:
        """Return copy of execution reports."""
        return [deepcopy(asdict(fill)) for fill in self._fills]

    def fill_orders(
        self,
        trading_day: object,
        approved_orders: list[dict[str, str | float]],
        daily_bars: dict[str, dict[str, float]],
    ) -> list[dict[str, str | float]]:
        """Backtester adapter: execute approved orders as daily fills.

        Rolls fees into ``PortfolioLedger`` for the given ``trading_day`` (date,
        ISO string, or timezone-aware datetime) so end-of-session MTM can emit
        ``costs_day`` on the equity curve.
        """
        session_day = self._coerce_trading_day(trading_day)
        fills: list[dict[str, str | float]] = []
        for order in approved_orders:
            fill_price = self._resolve_order_price(order=order, daily_bars=daily_bars)
            payload = dict(order)
            payload["price"] = fill_price
            report = self.place_order(payload, trading_day=session_day)
            fills.append(
                {
                    "symbol": str(report["symbol"]),
                    "side": str(report["side"]),
                    "qty": float(report["qty"]),
                    "price": float(report["price"]),
                    "market": str(report["market"]),
                    "bucket": str(report["bucket"]),
                    "fee": float(report["fee"]),
                }
            )
        return fills

    def _validate_order(self, order: dict[str, str | float]) -> dict[str, str | float]:
        required_keys = {"symbol", "side", "qty", "price", "market", "bucket"}
        missing = required_keys - set(order)
        if missing:
            raise ValueError(f"order missing required keys: {sorted(missing)}")

        symbol = str(order["symbol"])
        side = str(order["side"])
        qty = float(order["qty"])
        price = float(order["price"])
        market = str(order["market"])
        bucket = str(order["bucket"])
        adv = float(order["adv"]) if "adv" in order else None

        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if qty <= 0:
            raise ValueError("qty must be > 0")
        if price <= 0:
            raise ValueError("price must be > 0")
        if bucket not in {"short", "long"}:
            raise ValueError("bucket must be short or long")
        if not symbol:
            raise ValueError("symbol must be non-empty")
        if not market:
            raise ValueError("market must be non-empty")
        if adv is not None and adv <= 0:
            raise ValueError("adv must be > 0 when provided")

        normalized: dict[str, str | float] = {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": price,
            "market": market,
            "bucket": bucket,
        }
        if adv is not None:
            normalized["adv"] = adv
        return normalized

    def _resolve_order_price(
        self,
        order: dict[str, str | float],
        daily_bars: dict[str, dict[str, float]],
    ) -> float:
        if "price" in order:
            price = float(order["price"])
            if price <= 0:
                raise ValueError("price must be > 0")
            return price

        symbol = str(order.get("symbol", ""))
        bar = daily_bars.get(symbol)
        if bar is None or "close" not in bar:
            raise ValueError(f"missing close price for symbol {symbol}")
        close = float(bar["close"])
        if close <= 0:
            raise ValueError(f"close must be > 0 for symbol {symbol}")
        return close

    @staticmethod
    def _coerce_trading_day(trading_day: object) -> date:
        if isinstance(trading_day, date):
            return trading_day
        if isinstance(trading_day, datetime):
            return trading_day.date()
        if isinstance(trading_day, str):
            return date.fromisoformat(trading_day[:10])
        raise TypeError(f"unsupported trading_day type: {type(trading_day)!r}")
