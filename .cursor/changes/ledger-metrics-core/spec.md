# Spec — ledger-metrics-core

## Objetivo

Definir el contrato funcional del `ledger` para el core de simulación paper-first:
actualización diaria de caja/posiciones, valoración MTM, PnL (realizado y no realizado),
equity curve y drawdown mensual del subportfolio corto.

## Alcance

- Cubre procesamiento de fills de compra/venta y snapshot diario.
- Cubre métricas de portfolio total y bucket corto.
- No cubre ejecución live ni integración de broker real.
- No cubre reglas de risk guardrails (solo expone métricas para que riesgo decida).

## Requisitos funcionales

1. El ledger debe mantener estado interno con:
   - `cash`
   - posiciones por símbolo (`qty`, `avg_cost`, `market`, `bucket`)
   - acumulados de PnL realizado
   - historial de equity diaria
2. Ante cada fill validado:
   - BUY: incrementa posición y recalcula `avg_cost` por promedio ponderado.
   - SELL: reduce posición y registra PnL realizado contra `avg_cost`.
3. En cada cierre diario (MTM):
   - calcula PnL no realizado por símbolo usando `close`.
   - calcula `equity = cash + market_value_total`.
   - actualiza equity curve.
4. Debe calcular drawdown mensual del bucket `short`:
   - `dd = (equity_short / peak_month_short) - 1`
   - peak y drawdown se resetean al cambiar mes calendario.
5. Debe retornar snapshot serializable para `LedgerUpdated`.

## Contrato de entrada

### Fills (lista)

Cada fill requiere:
- `symbol: str`
- `side: "BUY" | "SELL"`
- `qty: float` (> 0)
- `price: float` (> 0)
- `market: str` (ej. `US`, `AR`)
- `bucket: "short" | "long"`
- `fee: float` (>= 0, opcional default 0.0)

### Daily bars (dict por símbolo)

- Debe contener al menos `close` para símbolos con posición abierta o fills del día.

## Contrato de salida (snapshot)

- `trading_day`
- `cash`
- `positions` (qty, avg_cost, market, bucket, market_value, unrealized_pnl)
- `realized_pnl_total`
- `unrealized_pnl_total`
- `equity_total`
- `equity_curve_points`
- `short_bucket`:
  - `equity`
  - `monthly_peak`
  - `monthly_drawdown`

## Reglas e invariantes

- No se permite vender más cantidad de la disponible por símbolo.
- Si falta `close` para símbolo con posición abierta, el ciclo debe fallar explícitamente.
- El cálculo de realizado/no realizado no debe mezclar buckets.
- `equity_total` debe ser consistente con `cash + sum(market_value)`.

## Criterios de aceptación

1. Se pueden simular múltiples días con fills mixtos BUY/SELL y obtener snapshot consistente.
2. Se valida separación correcta de PnL realizado vs no realizado.
3. Drawdown mensual del bucket corto:
   - cae cuando equity_short pierde contra su peak mensual,
   - se resetea al pasar de mes.
4. Tests fallan ante:
   - fill inválido,
   - venta mayor a posición,
   - falta de `close` para símbolo abierto.
