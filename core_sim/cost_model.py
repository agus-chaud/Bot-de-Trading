"""Deterministic fill cost model configurable by market."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SlippageMode(str, Enum):
    """Supported slippage calculation modes."""

    FIXED_BPS = "fixed_bps"
    ADV_LINEAR = "adv_linear"


@dataclass(frozen=True)
class MarketCostConfig:
    """Cost configuration for one market."""

    commission_bps_per_side: float
    slippage_mode: SlippageMode = SlippageMode.FIXED_BPS
    slippage_bps: float = 0.0
    adv_slope_bps: float = 0.0
    min_spread_bps: float = 0.0


@dataclass(frozen=True)
class CostBreakdown:
    """Detailed cost output for one fill."""

    market: str
    notional: float
    commission: float
    slippage: float
    spread: float

    @property
    def total(self) -> float:
        return self.commission + self.slippage + self.spread


class CostModel:
    """Deterministic cost model for paper fills."""

    def __init__(self, market_configs: dict[str, MarketCostConfig]) -> None:
        if not market_configs:
            raise ValueError("At least one market config is required")
        self._configs = market_configs

    def compute_fill_cost(
        self,
        market: str,
        side: str,
        qty: float,
        price: float,
        adv: float | None = None,
    ) -> CostBreakdown:
        del side  # Reserved for future asymmetry (buy/sell spread).

        if qty <= 0:
            raise ValueError("qty must be > 0")
        if price <= 0:
            raise ValueError("price must be > 0")
        if market not in self._configs:
            raise ValueError(f"Unknown market: {market}")

        config = self._configs[market]
        notional = qty * price
        commission = notional * (config.commission_bps_per_side / 10_000)
        spread = notional * (config.min_spread_bps / 10_000)
        slippage_bps = self._resolve_slippage_bps(config=config, qty=qty, adv=adv)
        slippage = notional * (slippage_bps / 10_000)

        return CostBreakdown(
            market=market,
            notional=notional,
            commission=commission,
            slippage=slippage,
            spread=spread,
        )

    def _resolve_slippage_bps(
        self,
        config: MarketCostConfig,
        qty: float,
        adv: float | None,
    ) -> float:
        if config.slippage_mode is SlippageMode.FIXED_BPS:
            return config.slippage_bps

        if config.slippage_mode is SlippageMode.ADV_LINEAR:
            if adv is None or adv <= 0:
                return 0.0
            participation = qty / adv
            return config.adv_slope_bps * participation

        raise ValueError(f"Unsupported slippage mode: {config.slippage_mode}")
