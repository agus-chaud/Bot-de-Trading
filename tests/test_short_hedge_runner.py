"""Tests de comportamiento del runner de cobertura (core_sim/short_hedge_runner.py).

Cubren el cableado del sleeve corto-como-cobertura que se promovió a producción (ADR-064):
- DB real (MarketDB en tmp), ledger y broker reales — sin sobre-mockear (smart-testing).
- Afirman el comportamiento de negocio: comprar la canasta, des-riesgar a cash cuando
  ambos factores caen, y dimensionar el budget desde el equity total.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from core_sim.cost_model import CostModel, MarketCostConfig, SlippageMode
from core_sim.ledger import PortfolioLedger
from core_sim.paper_broker_sim import PaperBrokerSim
from core_sim.short_hedge_engine import ShortHedgeConfig
from core_sim.short_hedge_runner import compute_derisk_to_cash, run_hedge_sleeve_day
from data.schema import OHLCVRow
from data.storage import MarketDB

REPO_ROOT = Path(__file__).resolve().parents[1]
_DAY = date(2025, 6, 2)
_WL = frozenset({"GLD", "WMT"})


class _Pos:
    def __init__(self, bucket: str):
        self.bucket = bucket


def test_bucket_conflict_flags_long_symbol_held_in_short():
    """Freno ADR-064: símbolo que el largo quiere pero ya está en bucket short → conflicto."""
    from scripts.run_paper_live import bucket_conflict_symbols
    positions = {"KO": _Pos("short"), "GLD": _Pos("short"), "GGAL": _Pos("long")}
    long_syms = {"GGAL", "PAMP", "TXAR", "SPY", "QQQ", "KO"}
    assert bucket_conflict_symbols(positions, long_syms) == {"KO"}


def test_bucket_conflict_empty_when_hedge_and_long_disjoint():
    """Canasta hedge (GLD/WMT) no pisa el largo → sin conflicto."""
    from scripts.run_paper_live import bucket_conflict_symbols
    positions = {"GLD": _Pos("short"), "WMT": _Pos("short")}
    assert bucket_conflict_symbols(positions, {"GGAL", "PAMP", "TXAR", "SPY", "QQQ", "KO"}) == set()


def test_production_policy_promotes_hedge_with_gld_wmt():
    """Guard de la promoción (ADR-064): producción corre el corto como cobertura GLD/WMT."""
    import yaml
    from core_sim.short_hedge_engine import short_hedge_config_from_policy_dict

    doc = yaml.safe_load((REPO_ROOT / "config" / "policy.v1.yaml").open(encoding="utf-8"))
    sh = doc.get("short_hedge")
    assert sh is not None and sh.get("enabled") is True
    cfg = short_hedge_config_from_policy_dict(sh)
    assert cfg.enabled is True
    assert {s for s, _ in cfg.hedge_lines} == {"GLD", "WMT"}
    assert cfg.derisk_enabled is True


def _cfg(derisk_enabled: bool = True) -> ShortHedgeConfig:
    return ShortHedgeConfig(
        enabled=True, mode="hedge_static",
        hedge_weight_total=0.30, tactical_weight_total=0.0,
        drift_rebalance_threshold_pp=2.0,
        hedge_lines=(("GLD", 0.5), ("WMT", 0.5)),
        derisk_enabled=derisk_enabled,
        derisk_ar_drawdown_floor=-0.10, derisk_global_drawdown_floor=-0.10,
    )


def _row(sym: str, d: date, close: float) -> OHLCVRow:
    return OHLCVRow(symbol=sym, ts=d, open=close, high=close, low=close,
                    close=close, volume=1_000_000.0, currency="ARS",
                    venue="XBUE", imputed=False)


def _db(tmp_path: Path, extra_rows: list[OHLCVRow] | None = None) -> MarketDB:
    db = MarketDB(str(tmp_path / "hedge.db"))
    rows = [_row("GLD", _DAY, 100.0), _row("WMT", _DAY, 50.0)]
    if extra_rows:
        rows.extend(extra_rows)
    db.upsert_ohlcv(rows)
    return db


def _broker(ledger: PortfolioLedger) -> PaperBrokerSim:
    ar = MarketCostConfig(commission_bps_per_side=15.0, slippage_bps=5.0,
                          slippage_mode=SlippageMode.FIXED_BPS)
    return PaperBrokerSim(ledger=ledger, cost_model=CostModel(market_configs={"AR": ar}))


def _snap(db: MarketDB, day: date, ledger: PortfolioLedger) -> dict:
    """Snapshot de prueba: valúa con las barras XBUE disponibles en la DB ese día."""
    bars = {}
    for sym in ("GLD", "WMT"):
        r = db.get_ohlcv(sym, day, day, "XBUE")
        if r:
            b = r[0]
            bars[sym] = {"open": b.open, "high": b.high, "low": b.low,
                         "close": b.close, "volume": b.volume}
    return ledger.mark_to_market(trading_day=day, daily_bars=bars)


# --------------------------------------------------------------------------- #
# Rebalanceo hacia la canasta
# --------------------------------------------------------------------------- #

class TestHedgeRebalance:
    def test_should_buy_basket_toward_target_from_all_cash(self, tmp_path):
        # Arrange: cartera 100% cash, canasta con barras disponibles.
        ledger = PortfolioLedger(starting_cash=1_000_000.0)
        db = _db(tmp_path)
        # Act
        fills = run_hedge_sleeve_day(
            db=db, day=_DAY, ledger=ledger, broker=_broker(ledger),
            hedge_cfg=_cfg(derisk_enabled=False), hedge_whitelist=_WL,
            weights_short=0.30, resilient_snapshot=_snap,
        )
        # Assert: compra GLD y WMT, en bucket short y mercado AR.
        sides = {f["symbol"]: f["side"] for f in fills}
        assert sides == {"GLD": "BUY", "WMT": "BUY"}
        assert all(f["bucket"] == "short" and f["market"] == "AR" for f in fills)

    def test_budget_is_sized_from_total_equity_not_short_equity(self, tmp_path):
        # Arrange: equity total 1M → budget 30% = 300k → ~150k por línea.
        ledger = PortfolioLedger(starting_cash=1_000_000.0)
        db = _db(tmp_path)
        # Act
        fills = run_hedge_sleeve_day(
            db=db, day=_DAY, ledger=ledger, broker=_broker(ledger),
            hedge_cfg=_cfg(derisk_enabled=False), hedge_whitelist=_WL,
            weights_short=0.30, resilient_snapshot=_snap,
        )
        # Assert: el notional desplegado ronda el 30% del equity (no el short_equity, que es 0).
        notional = sum(f["qty"] * f["price"] for f in fills)
        assert 250_000 < notional <= 300_000

    def test_no_fills_when_basket_has_no_bars_that_day(self, tmp_path):
        # Arrange: DB sin barras de la canasta ese día.
        ledger = PortfolioLedger(starting_cash=1_000_000.0)
        db = MarketDB(str(tmp_path / "empty.db"))
        # Act
        fills = run_hedge_sleeve_day(
            db=db, day=_DAY, ledger=ledger, broker=_broker(ledger),
            hedge_cfg=_cfg(derisk_enabled=False), hedge_whitelist=_WL,
            weights_short=0.30, resilient_snapshot=_snap,
        )
        # Assert: sin precios para valuar/comprar, no opera (aborta el ciclo).
        assert fills == []


# --------------------------------------------------------------------------- #
# Regla de des-riesgo a cash
# --------------------------------------------------------------------------- #

class TestDeriskToCash:
    def _crisis_history(self) -> list[OHLCVRow]:
        """GGAL y SPY con drawdown > 10% desde su pico (ambos factores en crisis)."""
        rows: list[OHLCVRow] = []
        start = _DAY - timedelta(days=120)
        # GGAL: pico 100 → 78 (-22%). SPY: pico 100 → 86 (-14%).
        for i in range(40):
            d = start + timedelta(days=i)
            rows.append(_row("GGAL", d, 100.0 - i * 0.0))  # sube a 100 (pico)
            rows.append(_row("SPY", d, 100.0))
        for i in range(40):
            d = start + timedelta(days=40 + i)
            rows.append(_row("GGAL", d, 100.0 - i * 0.55))  # cae a ~78
            rows.append(_row("SPY", d, 100.0 - i * 0.35))   # cae a ~86
        return rows

    def test_derisk_triggers_when_both_factors_in_drawdown(self, tmp_path):
        db = _db(tmp_path, extra_rows=self._crisis_history())
        assert compute_derisk_to_cash(db, _DAY, _cfg(derisk_enabled=True)) is True

    def test_derisk_off_when_disabled_in_config(self, tmp_path):
        db = _db(tmp_path, extra_rows=self._crisis_history())
        assert compute_derisk_to_cash(db, _DAY, _cfg(derisk_enabled=False)) is False

    def test_sells_basket_to_cash_when_derisk_triggers(self, tmp_path):
        # Arrange: cartera ya con posiciones de cobertura + ambos factores en crisis.
        ledger = PortfolioLedger(starting_cash=1_000_000.0)
        ledger.apply_fills(_DAY - timedelta(days=1), [
            {"symbol": "GLD", "side": "BUY", "qty": 1500.0, "price": 100.0,
             "market": "AR", "bucket": "short", "fee": 0.0},
            {"symbol": "WMT", "side": "BUY", "qty": 3000.0, "price": 50.0,
             "market": "AR", "bucket": "short", "fee": 0.0},
        ])
        db = _db(tmp_path, extra_rows=self._crisis_history())
        # Act
        fills = run_hedge_sleeve_day(
            db=db, day=_DAY, ledger=ledger, broker=_broker(ledger),
            hedge_cfg=_cfg(derisk_enabled=True), hedge_whitelist=_WL,
            weights_short=0.30, resilient_snapshot=_snap,
        )
        # Assert: des-riesga → vende la canasta (sin compras nuevas).
        assert fills, "el des-riesgo debe generar ventas de la canasta"
        assert all(f["side"] == "SELL" for f in fills)
        assert {f["symbol"] for f in fills} <= {"GLD", "WMT"}
