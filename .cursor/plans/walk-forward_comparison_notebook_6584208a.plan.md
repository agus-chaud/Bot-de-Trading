---
name: Walk-forward comparison notebook
overview: Crear un notebook que compare el motor largo con rebalanceo semanal vs mensual vs buy-and-hold SPY, usando walk-forward con ventanas de 3 meses y costos reales, para validar empíricamente ADR-045.
todos:
  - id: enrich-stage
    content: Agregar `return_details` + `StageDetails` a `run_long_engine_stage` en `validation/stages/long_engine.py`
    status: pending
  - id: adapt-callers
    content: Adaptar callers existentes al nuevo return type tupla (wf_runner, tests)
    status: pending
  - id: notebook-setup
    content: Crear notebook `notebooks/wf_long_comparison.ipynb` con imports, carga de config y helpers
    status: pending
  - id: notebook-orchestrator
    content: "Implementar loop de comparación: weekly vs monthly vs SPY buy-and-hold por ventana"
    status: pending
  - id: notebook-viz
    content: "5 visualizaciones: equity curves, tabla resumen, bar chart, drawdown chart + gráfico continuo bonus"
    status: pending
  - id: run-and-verify
    content: Ejecutar el notebook y verificar que los resultados son coherentes
    status: pending
isProject: false
---

# Walk-Forward Comparison Notebook (semanal vs mensual vs SPY)

## Decisiones acordadas (Grill-me)
- **Objetivo**: Validar ADR-045 — ¿fue buena idea pasar de mensual a semanal?
- **Estrategias**: semanal, mensual, buy-and-hold SPY
- **Ventanas**: 3 meses, paso 1 mes (~9-10 ventanas con ~262 barras disponibles)
- **Datos**: SPY/IWM/QQQ en XNYS (abr-2025 → may-2026)
- **Costos**: Reales, del `cost_model` en policy
- **Equity capturada**: sleeve largo solamente (no total portfolio)
- **Normalización**: base 100 por ventana
- **API del stage**: tupla `(StageResult, StageDetails | None)` siempre; callers existentes adaptados
- **Output**: Notebook `notebooks/wf_long_comparison.ipynb`

## Paso 1 — Enriquecer `run_long_engine_stage`

Archivo: `validation/stages/long_engine.py`

- Agregar parámetro `return_details: bool = False`
- Definir dataclass `StageDetails` con: `daily_equity: list[dict]`, `fills: list[dict]`, `final_positions: dict`
- Dentro del loop diario, acumular equity del long bucket (ya se computa en `_compute_long_bucket_mtm`)
- Retornar siempre tupla `(StageResult, StageDetails | None)` — None cuando `return_details=False`
- Adaptar callers existentes (`validation/wf_runner.py`, tests) al nuevo return type

## Paso 2 — Helper para buy-and-hold SPY

- Función pura en el notebook: dado OHLCV de SPY para la ventana y cash inicial, retorna curva de equity
- Cálculo: `cash * (close[t] / close[0])` por cada día

## Paso 3 — Orquestador de comparación en el notebook

- Generar ventanas con `generate_wf_windows(window_months=3, step_months=1)`
- Por cada ventana, correr `run_long_engine_stage` con `return_details=True` dos veces:
  - `rebalance_rule = first_us_trading_day_of_calendar_week`
  - `rebalance_rule = first_us_trading_day_of_calendar_month`
- Calcular buy-and-hold SPY para la misma ventana
- Acumular resultados en DataFrames

## Paso 4 — Visualizaciones (ventanas independientes)

1. **Equity curves superpuestas**: por ventana, 3 curvas normalizadas a base 100 (subplot grid)
2. **Tabla resumen**: por ventana — retorno total, Sharpe, MDD de cada estrategia
3. **Bar chart comparativo**: retorno promedio across ventanas para cada estrategia
4. **Drawdown chart**: MDD de cada estrategia ventana a ventana

## Paso 5 — Gráfico continuo bonus

- Una corrida de los 12 meses completos sin resetear capital
- Equity curve continua de las 3 estrategias superpuestas
- Muestra "la película completa" de qué hubiera pasado con el capital real

## Archivos a modificar/crear
- `validation/stages/long_engine.py` — agregar `return_details`, `StageDetails`, cambiar return type a tupla
- `validation/wf_runner.py` — adaptar desempaquetado de tupla
- Tests que llaman a `run_long_engine_stage` — adaptar desempaquetado
- `notebooks/wf_long_comparison.ipynb` — notebook nuevo con análisis completo
