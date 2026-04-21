"""Deterministic portfolio ledger for paper simulation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class PositionState:
    """One open position tracked by the ledger."""

    symbol: str
    market: str
    bucket: str
    qty: float
    avg_cost: float


class PortfolioLedger:
    """Tracks cash, positions and daily metrics for paper trading."""

    def __init__(self, starting_cash: float) -> None:
        if starting_cash < 0:
            raise ValueError("starting_cash must be >= 0")

        self.cash = float(starting_cash)
        self.positions: dict[str, PositionState] = {}
        self.realized_pnl_total = 0.0
        self.equity_curve_points: list[dict[str, float | str]] = []
        self._current_short_month: tuple[int, int] | None = None
        self._short_monthly_peak = 0.0
        self._short_monthly_drawdown = 0.0
        # short MV por fecha (última escritura gana) para `daily_return` con varias MTM en un día
        self._short_eod_by_trading_date: dict[date, float] = {}

    def apply_fills(self, fills: list[dict[str, str | float]]) -> None:
        """Apply one day of fills in order."""
        for fill in fills:
            normalized_fill = self._validate_fill(fill)
            side = normalized_fill["side"]
            if side == "BUY":
                self._apply_buy(normalized_fill)
            else:
                self._apply_sell(normalized_fill)

    def mark_to_market(
        self,
        trading_day: date,
        daily_bars: dict[str, dict[str, float]],
    ) -> dict[str, object]:
        """Mark open positions to daily close and return a snapshot."""
        positions_snapshot: dict[str, dict[str, float | str]] = {}
        market_value_total = 0.0
        unrealized_pnl_total = 0.0
        short_equity = 0.0

        for symbol, position in self.positions.items():
            close_price = self._extract_close(symbol=symbol, daily_bars=daily_bars)
            market_value = position.qty * close_price
            unrealized = (close_price - position.avg_cost) * position.qty

            positions_snapshot[symbol] = {
                "qty": position.qty,
                "avg_cost": position.avg_cost,
                "market": position.market,
                "bucket": position.bucket,
                "market_value": market_value,
                "unrealized_pnl": unrealized,
            }
            market_value_total += market_value
            unrealized_pnl_total += unrealized
            if position.bucket == "short":
                short_equity += market_value

        equity_total = self.cash + market_value_total
        day_key = trading_day.isoformat()
        curve_point = {"trading_day": day_key, "equity_total": equity_total}
        if self.equity_curve_points and self.equity_curve_points[-1]["trading_day"] == day_key:
            self.equity_curve_points[-1] = curve_point
        else:
            self.equity_curve_points.append(curve_point)
        short_bucket = self._update_short_drawdown(
            trading_day=trading_day,
            short_equity=short_equity,
        )
        _, short_bucket = self._attach_short_daily_return(
            trading_day=trading_day,
            short_bucket=short_bucket,
            short_equity=short_equity,
        )

        return {
            "trading_day": trading_day.isoformat(),
            "cash": self.cash,
            "positions": positions_snapshot,
            "realized_pnl_total": self.realized_pnl_total,
            "unrealized_pnl_total": unrealized_pnl_total,
            "equity_total": equity_total,
            "equity_curve_points": list(self.equity_curve_points),
            "short_bucket": short_bucket,
        }

    def update_day(
        self,
        trading_day: date,
        fills: list[dict[str, str | float]],
        daily_bars: dict[str, dict[str, float]],
    ) -> dict[str, object]:
        """Apply fills and return the end-of-day snapshot."""
        self.apply_fills(fills=fills)
        return self.mark_to_market(trading_day=trading_day, daily_bars=daily_bars)

    def _apply_buy(self, fill: dict[str, str | float]) -> None:
        symbol = fill["symbol"]
        qty = float(fill["qty"])
        price = float(fill["price"])
        fee = float(fill["fee"])
        market = str(fill["market"])
        bucket = str(fill["bucket"])

        existing = self.positions.get(symbol)
        if existing is None:
            self.positions[symbol] = PositionState(
                symbol=symbol,
                market=market,
                bucket=bucket,
                qty=qty,
                avg_cost=price,
            )
        else:
            if existing.market != market:
                raise ValueError(f"market mismatch for symbol {symbol}")
            if existing.bucket != bucket:
                raise ValueError(f"bucket mismatch for symbol {symbol}")
            total_qty = existing.qty + qty
            existing.avg_cost = ((existing.qty * existing.avg_cost) + (qty * price)) / total_qty
            existing.qty = total_qty

        self.cash -= (qty * price) + fee

    def _apply_sell(self, fill: dict[str, str | float]) -> None:
        symbol = fill["symbol"]
        qty = float(fill["qty"])
        price = float(fill["price"])
        fee = float(fill["fee"])
        market = str(fill["market"])
        bucket = str(fill["bucket"])

        position = self.positions.get(symbol)
        if position is None:
            raise ValueError(f"cannot sell without open position: {symbol}")
        if position.market != market:
            raise ValueError(f"market mismatch for symbol {symbol}")
        if position.bucket != bucket:
            raise ValueError(f"bucket mismatch for symbol {symbol}")
        if qty > position.qty:
            raise ValueError(f"cannot sell more than available qty for symbol {symbol}")

        realized = (price - position.avg_cost) * qty - fee
        self.realized_pnl_total += realized
        self.cash += (qty * price) - fee

        remaining_qty = position.qty - qty
        if remaining_qty == 0:
            del self.positions[symbol]
            return
        position.qty = remaining_qty

    def _validate_fill(self, fill: dict[str, str | float]) -> dict[str, str | float]:
        required_keys = {"symbol", "side", "qty", "price", "market", "bucket"}
        missing = required_keys - set(fill)
        if missing:
            raise ValueError(f"fill missing required keys: {sorted(missing)}")

        symbol = str(fill["symbol"])
        side = str(fill["side"])
        qty = float(fill["qty"])
        price = float(fill["price"])
        market = str(fill["market"])
        bucket = str(fill["bucket"])
        fee = float(fill.get("fee", 0.0))

        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if qty <= 0:
            raise ValueError("qty must be > 0")
        if price <= 0:
            raise ValueError("price must be > 0")
        if fee < 0:
            raise ValueError("fee must be >= 0")
        if bucket not in {"short", "long"}:
            raise ValueError("bucket must be short or long")
        if not symbol:
            raise ValueError("symbol must be non-empty")
        if not market:
            raise ValueError("market must be non-empty")

        return {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": price,
            "market": market,
            "bucket": bucket,
            "fee": fee,
        }

    def _extract_close(self, symbol: str, daily_bars: dict[str, dict[str, float]]) -> float:
        bar = daily_bars.get(symbol)
        if bar is None or "close" not in bar:
            raise ValueError(f"missing close price for symbol {symbol}")
        close = float(bar["close"])
        if close <= 0:
            raise ValueError(f"close must be > 0 for symbol {symbol}")
        return close

    def _update_short_drawdown(self, trading_day: date, short_equity: float) -> dict[str, float]:
        month_key = (trading_day.year, trading_day.month)
        if self._current_short_month != month_key:
            self._current_short_month = month_key
            self._short_monthly_peak = short_equity
            self._short_monthly_drawdown = 0.0
        else:
            self._short_monthly_peak = max(self._short_monthly_peak, short_equity)

        if self._short_monthly_peak > 0:
            self._short_monthly_drawdown = (short_equity / self._short_monthly_peak) - 1.0
        else:
            self._short_monthly_drawdown = 0.0

        return {
            "equity": short_equity,
            "monthly_peak": self._short_monthly_peak,
            "monthly_drawdown": self._short_monthly_drawdown,
        }

    def _attach_short_daily_return(
        self,
        trading_day: date,
        short_bucket: dict[str, float],
        short_equity: float,
    ) -> tuple[float, dict[str, float]]:
        """Rend. diario del bucket corto vs. última MTM con fecha estrictamente anterior a `trading_day`."""
        prior_dates = [d for d in self._short_eod_by_trading_date if d < trading_day]
        if not prior_dates:
            daily = 0.0
        else:
            d_prev = max(prior_dates)
            prev = float(self._short_eod_by_trading_date[d_prev])
            daily = 0.0 if prev <= 0.0 else (float(short_equity) - prev) / prev
        self._short_eod_by_trading_date[trading_day] = float(short_equity)
        out: dict[str, float] = dict(short_bucket)
        out["daily_return"] = float(daily)
        return float(daily), out
