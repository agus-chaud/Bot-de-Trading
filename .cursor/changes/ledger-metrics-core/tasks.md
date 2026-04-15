# Tasks — ledger-metrics-core

## Fase 1 — Core ledger (complejidad: media)

- [ ] Crear `core_sim/ledger.py` con:
  - `PositionState`
  - `PortfolioLedger.__init__`
  - validaciones de fills
  - `apply_fills`
- [ ] Implementar promedio ponderado en BUY y realized PnL en SELL.
- [ ] Implementar validación de sobreventa (`SELL > qty disponible`).

## Fase 2 — MTM + métricas (complejidad: media)

- [ ] Implementar `mark_to_market` con cálculo por símbolo:
  - `market_value`
  - `unrealized_pnl`
- [ ] Implementar `equity_total` y `equity_curve`.
- [ ] Implementar estado de bucket corto:
  - `equity_short`
  - `monthly_peak`
  - `monthly_drawdown`
  - reset por cambio de mes calendario.

## Fase 3 — Integración y contratos (complejidad: baja)

- [ ] Exponer `PortfolioLedger` en `core_sim/__init__.py`.
- [ ] Preparar snapshot serializable para `LedgerUpdated`.
- [ ] Añadir test de integración mínima en `tests/test_event_engine.py` verificando campos críticos del snapshot.

## Fase 4 — QA de comportamiento (complejidad: media)

- [ ] Crear `tests/test_ledger.py` con escenarios:
  - BUY y MTM diario.
  - BUY + SELL parcial con PnL realizado correcto.
  - error por sobreventa.
  - error por falta de `close`.
  - drawdown mensual del corto + reset de mes.
- [ ] Ejecutar `pytest` del módulo y ajustar regresiones.

## Bloqueadores / unknowns

- Definir si el `fee` llega ya agregado desde broker sim o se calcula en ledger (v1 propuesto: llega por fill, default 0.0).
- Definir precisión/rounding final para reporting (v1 tests con tolerancia `pytest.approx`).
