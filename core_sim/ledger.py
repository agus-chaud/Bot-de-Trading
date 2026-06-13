"""Deterministic portfolio ledger for paper simulation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


# Columnas mínimas de serie diaria para informes KPI (`docs/kpi_report_spec.v1.md` §2.1).
DAILY_EQUITY_KPI_COLUMNS = (
    "ts",
    "trading_day",
    "equity_total",
    "equity_short",
    "equity_long",
    "cash",
    "costs_day",
    "mv_us",
    "mv_ar",
)


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

    def __init__(self, starting_cash: float, *, short_allocation: float = 0.0) -> None:
        if starting_cash < 0:
            raise ValueError("starting_cash must be >= 0")
        if short_allocation < 0:
            raise ValueError("short_allocation must be >= 0")

        self.cash = float(starting_cash)
        self.short_cash = 0.0  # cash asignado al bucket corto (aumenta con SELLs, disminuye con BUYs)
        self.positions: dict[str, PositionState] = {}
        self.realized_pnl_total = 0.0
        self.equity_curve_points: list[dict[str, float | str]] = []
        self._costs_by_trading_day: dict[date, float] = {}
        self._short_allocation = float(short_allocation)
        self._current_short_month: tuple[int, int] | None = None
        self._short_monthly_peak = 0.0
        self._short_monthly_drawdown = 0.0
        # short MV por fecha (última escritura gana) para `daily_return` con varias MTM en un día
        self._short_eod_by_trading_date: dict[date, float] = {}
        # long equity por fecha para daily return del sleeve largo
        self._long_eod_by_trading_date: dict[date, float] = {}
        # último close válido visto por símbolo, para carry-forward de valuación
        # cuando un día falta la barra (hueco de datos). Evita crashear la corrida.
        self._last_mark: dict[str, float] = {}

    def reset_last_marks(self) -> None:
        """Vaciar el carry-forward. La capa sim lo usa para descartar marks
        intermedios (p. ej. una barra en moneda equivocada que un motor valuó) antes
        de re-hidratar con el último close legítimo y producir el snapshot autoritativo."""
        self._last_mark.clear()

    def seed_last_mark(self, symbol: str, price: float) -> None:
        """Sembrar el último mark conocido para carry-forward (no-op si ya hay uno).

        La capa de orquestación reconstruye un ledger nuevo por día (replay de fills),
        por lo que `_last_mark` arranca vacío y el carry-forward del ADR-051 no tendría
        de dónde arrastrar. Este setter permite hidratarlo con el último close conocido
        antes de valuar, sin meter lógica de precios en el ledger. No pisa un mark
        aprendido en el día (p. ej. de una barra fresca)."""
        if price > 0 and symbol not in self._last_mark:
            self._last_mark[symbol] = float(price)

    def apply_fills(
        self,
        trading_day: date,
        fills: list[dict[str, str | float]],
    ) -> None:
        """Apply one session's fills in order and roll up execution costs for that day."""
        day_total = 0.0
        for fill in fills:
            normalized_fill = self._validate_fill(fill)
            day_total += float(normalized_fill["fee"])
            side = normalized_fill["side"]
            if side == "BUY":
                self._apply_buy(normalized_fill)
            else:
                self._apply_sell(normalized_fill)
        if day_total != 0.0:
            self._costs_by_trading_day[trading_day] = (
                self._costs_by_trading_day.get(trading_day, 0.0) + day_total
            )

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
        long_equity_mv = 0.0
        mv_us = 0.0
        mv_ar = 0.0

        stale_marks: list[str] = []
        for symbol, position in self.positions.items():
            close_price, is_stale = self._resolve_mark_price(
                symbol=symbol, position=position, daily_bars=daily_bars
            )
            if is_stale:
                stale_marks.append(symbol)
            market_value = position.qty * close_price
            unrealized = (close_price - position.avg_cost) * position.qty

            positions_snapshot[symbol] = {
                "qty": position.qty,
                "avg_cost": position.avg_cost,
                "market": position.market,
                "bucket": position.bucket,
                "market_value": market_value,
                "unrealized_pnl": unrealized,
                "stale": is_stale,
            }
            market_value_total += market_value
            unrealized_pnl_total += unrealized
            market_tag = position.market.upper()
            if market_tag == "US":
                mv_us += market_value
            elif market_tag == "AR":
                mv_ar += market_value
            if position.bucket == "short":
                short_equity += market_value
            else:
                long_equity_mv += market_value

        equity_short = float(self.short_cash) + short_equity
        equity_long = float(self.cash - self.short_cash) + long_equity_mv
        equity_total = float(self.cash + market_value_total)

        costs_day = float(self._costs_by_trading_day.get(trading_day, 0.0))
        day_key = trading_day.isoformat()
        curve_point: dict[str, Any] = {
            "ts": day_key,
            "trading_day": day_key,
            "equity_total": equity_total,
            "equity_short": equity_short,
            "equity_long": equity_long,
            "cash": float(self.cash),
            "costs_day": costs_day,
            "mv_us": mv_us,
            "mv_ar": mv_ar,
        }
        if self.equity_curve_points and self.equity_curve_points[-1]["trading_day"] == day_key:
            self.equity_curve_points[-1] = curve_point
        else:
            self.equity_curve_points.append(curve_point)
        short_bucket = self._update_short_drawdown(
            trading_day=trading_day,
            short_equity=short_equity,
            short_cash=self.short_cash,
        )
        _, short_bucket = self._attach_short_daily_return(
            trading_day=trading_day,
            short_bucket=short_bucket,
            short_equity=short_equity,
        )

        long_daily_return, long_bucket = self._attach_long_daily_return(
            trading_day=trading_day,
            long_equity=equity_long,
        )

        return {
            "trading_day": trading_day.isoformat(),
            "cash": self.cash,
            "positions": positions_snapshot,
            "realized_pnl_total": self.realized_pnl_total,
            "unrealized_pnl_total": unrealized_pnl_total,
            "equity_total": equity_total,
            "equity_short": equity_short,
            "equity_long": equity_long,
            "costs_day": costs_day,
            "equity_curve_points": list(self.equity_curve_points),
            "short_bucket": short_bucket,
            "long_bucket": long_bucket,
            "stale_marks": stale_marks,
        }

    def update_day(
        self,
        trading_day: date,
        fills: list[dict[str, str | float]],
        daily_bars: dict[str, dict[str, float]],
    ) -> dict[str, object]:
        """Apply fills and return the end-of-day snapshot."""
        self.apply_fills(trading_day=trading_day, fills=fills)
        return self.mark_to_market(trading_day=trading_day, daily_bars=daily_bars)

    def daily_equity_series_for_kpi_export(self) -> list[dict[str, float | str]]:
        """Serie diaria estable para CSV / `rpt_kpi.v1` §2.1 (copia superficial ordenada)."""
        rows: list[dict[str, float | str]] = []
        for pt in self.equity_curve_points:
            rows.append({key: pt[key] for key in DAILY_EQUITY_KPI_COLUMNS})
        return rows

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
        if bucket == "short":
            self.short_cash -= (qty * price) + fee

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
        if bucket == "short":
            self.short_cash += (qty * price) - fee

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

    def _resolve_mark_price(
        self,
        symbol: str,
        position: PositionState,
        daily_bars: dict[str, dict[str, float]],
    ) -> tuple[float, bool]:
        """Precio para valuar una posición abierta, resiliente a huecos de datos.

        Devuelve `(precio, is_stale)`. Prioridad:
        1) close válido (>0) del día → fresco; actualiza el último mark conocido.
        2) último mark conocido (carry-forward) → stale.
        3) `avg_cost` de la posición → stale (nunca se vio precio de mercado).

        Nunca valúa a 0 ni crashea: un hueco de datos en un símbolo no debe tirar
        abajo toda la corrida de validación. El flag `is_stale` deja el evento
        observable para la capa de calidad de datos.
        """
        bar = daily_bars.get(symbol)
        if bar is not None and "close" in bar:
            close = float(bar["close"])
            if close > 0:
                self._last_mark[symbol] = close
                return close, False
        last = self._last_mark.get(symbol)
        if last is not None and last > 0:
            return last, True
        return float(position.avg_cost), True

    def _update_short_drawdown(
        self,
        trading_day: date,
        short_equity: float,
        short_cash: float = 0.0,
    ) -> dict[str, float]:
        """Drawdown mensual del bucket corto medido sobre ADJUSTED EQUITY.

        `adjusted_equity = short_cash + short_equity + short_allocation` donde
        `short_allocation` es el capital nominal asignado al bucket (inyectado al
        construir el ledger). El peak es el running max de `adjusted_equity` dentro
        del mes calendario, reseteado al primer call de cada mes nuevo.
        DD = max(-1.0, adjusted_equity / peak - 1), clampado a 0 cuando peak <= 0.
        La key `equity` del dict retornado MANTIENE `short_equity` (MV) por contrato
        con `_attach_short_daily_return` (REQ-5).
        """
        bucket_equity = float(short_cash) + float(short_equity)
        adjusted_equity = bucket_equity + self._short_allocation
        month_key = (trading_day.year, trading_day.month)

        if self._current_short_month != month_key:
            self._current_short_month = month_key
            self._short_monthly_peak = adjusted_equity
            self._short_monthly_drawdown = 0.0
        else:
            self._short_monthly_peak = max(self._short_monthly_peak, adjusted_equity)

        if self._short_monthly_peak > 0:
            self._short_monthly_drawdown = max(
                -1.0, (adjusted_equity / self._short_monthly_peak) - 1.0
            )
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

    def _attach_long_daily_return(
        self,
        trading_day: date,
        long_equity: float,
    ) -> tuple[float, dict[str, float]]:
        """Daily return del sleeve largo vs. última MTM con fecha estrictamente anterior a `trading_day`."""
        prior_dates = [d for d in self._long_eod_by_trading_date if d < trading_day]
        if not prior_dates:
            daily = 0.0
        else:
            d_prev = max(prior_dates)
            prev = float(self._long_eod_by_trading_date[d_prev])
            daily = 0.0 if prev <= 0.0 else (float(long_equity) - prev) / prev
        self._long_eod_by_trading_date[trading_day] = float(long_equity)
        return float(daily), {"long_daily_return": float(daily), "long_equity": float(long_equity)}
