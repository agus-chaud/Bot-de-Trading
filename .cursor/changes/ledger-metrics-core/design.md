# Design — ledger-metrics-core

## Resumen de diseño

Se implementa un componente `PortfolioLedger` dentro de `core_sim` con estado mutable controlado.
El `DailyEventBacktester` seguirá invocando `update_ledger(...)`, y ese handler usará `PortfolioLedger`
para aplicar fills + MTM y devolver un snapshot inmutable.

## Arquitectura (agent-teams-lite)

- **Spec/policy**: define contrato de snapshot e invariantes.
- **Core sim**: implementa `PortfolioLedger` y reglas de cálculo.
- **QA/CI**: valida escenarios de negocio (no solo estructura).

## Componentes propuestos

### `core_sim/ledger.py`

- `PositionState` (dataclass):
  - `symbol`, `market`, `bucket`, `qty`, `avg_cost`
- `PortfolioLedger`:
  - Estado:
    - `cash`
    - `positions: dict[str, PositionState]`
    - `realized_pnl_total`
    - `equity_curve: list[dict[str, float | str]]`
    - estado mensual del corto (`current_month`, `short_monthly_peak`, `short_monthly_drawdown`)
  - API:
    - `apply_fills(trading_day, fills)`
    - `mark_to_market(trading_day, daily_bars)`
    - `update_day(trading_day, fills, daily_bars)` (orquesta apply + mtm y retorna snapshot)

## Flujo de cálculo diario

1. Validar fills de entrada.
2. Aplicar fills en orden recibido:
   - BUY:
     - `cash -= qty*price + fee`
     - actualizar `qty` y `avg_cost` (promedio ponderado)
   - SELL:
     - validar `qty <= posición disponible`
     - `cash += qty*price - fee`
     - `realized = (price - avg_cost) * qty - fee`
     - actualizar qty; si queda 0, eliminar posición
3. MTM:
   - para cada posición abierta: `market_value = qty * close`
     (si falta la barra del día, carry-forward del último close conocido o `avg_cost`,
     marcando `stale` — ver **ADR-051**; nunca crashea ni valúa a 0)
   - `unrealized = (close - avg_cost) * qty`
   - sumar equity total
4. Calcular equity del bucket `short`.
5. Actualizar peak/DD mensual del bucket `short`:
   - si cambió el mes: reset de peak y dd.
   - `peak = max(peak, equity_short)`
   - `dd = (equity_short / peak) - 1` (si peak > 0, si no 0).
6. Persistir punto de equity curve y devolver snapshot.

## Decisiones clave

- **Método de costo**: promedio ponderado en v1 (simple, determinístico y estable para paper).
- **Granularidad de DD corto**: mensual calendario con reset automático.
- **Errores explícitos**: se falla rápido ante fills inválidos. Para *valuación* (MTM),
  en cambio, un hueco de barra NO crashea: se usa carry-forward observable (`stale`) — ver **ADR-051**.

## Integración con código existente

- `tests/test_event_engine.py` ya valida que existe evento `LedgerUpdated`.
- Se agregará test de integración mínima donde payload de `LedgerUpdated` incluya:
  - `equity_total`
  - `realized_pnl_total`
  - `short_bucket.monthly_drawdown`
- `core_sim/__init__.py` exportará `PortfolioLedger`.

## Estrategia de testing

- `tests/test_ledger.py`:
  1. ciclo BUY -> MTM -> snapshot correcto
  2. BUY + SELL parcial -> realized correcto
  3. SELL > posición -> error
  4. falta close con posición abierta -> error
  5. drawdown short cae y resetea al cambio de mes
- Tests de evento existentes siguen verdes (no regresión de pipeline).

## Riesgos técnicos y mitigación

- **Riesgo**: inconsistencia entre `cash`, `market_value` y `equity`.
  - **Mitigación**: aserciones de invariantes en tests.
- **Riesgo**: DD mensual ambiguo con equity_short en 0.
  - **Mitigación**: regla explícita `dd=0` cuando `peak<=0`.
