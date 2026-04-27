"""Behavior tests for data layer schema value objects."""

from __future__ import annotations

from datetime import date

import pytest

from data.schema import CorporateActionRow, OHLCVRow


def _sample_ohlcv(**overrides) -> OHLCVRow:
    defaults = dict(
        symbol="SPY",
        ts=date(2024, 1, 15),
        open=460.0,
        high=465.0,
        low=458.0,
        close=463.0,
        volume=80_000_000.0,
        currency="USD",
        venue="XNYS",
        imputed=False,
    )
    defaults.update(overrides)
    return OHLCVRow(**defaults)


def _sample_action(**overrides) -> CorporateActionRow:
    defaults = dict(symbol="SPY", ts=date(2024, 1, 15), type="dividend", factor=1.75)
    defaults.update(overrides)
    return CorporateActionRow(**defaults)


class TestOHLCVRow:
    def test_should_hold_all_fields(self):
        row = _sample_ohlcv()
        assert row.symbol == "SPY"
        assert row.ts == date(2024, 1, 15)
        assert row.close == pytest.approx(463.0)
        assert row.currency == "USD"
        assert row.venue == "XNYS"
        assert row.imputed is False

    def test_should_be_immutable(self):
        row = _sample_ohlcv()
        with pytest.raises((AttributeError, TypeError)):
            row.close = 999.0  # type: ignore[misc]

    def test_should_support_imputed_flag(self):
        row = _sample_ohlcv(imputed=True)
        assert row.imputed is True

    def test_should_support_ar_venue(self):
        row = _sample_ohlcv(symbol="GGAL", currency="ARS", venue="XBUE")
        assert row.venue == "XBUE"
        assert row.currency == "ARS"


class TestCorporateActionRow:
    def test_should_hold_split_fields(self):
        row = _sample_action(type="split", factor=2.0)
        assert row.symbol == "SPY"
        assert row.type == "split"
        assert row.factor == pytest.approx(2.0)

    def test_should_hold_dividend_fields(self):
        row = _sample_action(type="dividend", factor=1.75)
        assert row.type == "dividend"
        assert row.factor == pytest.approx(1.75)

    def test_should_be_immutable(self):
        row = _sample_action()
        with pytest.raises((AttributeError, TypeError)):
            row.factor = 999.0  # type: ignore[misc]
