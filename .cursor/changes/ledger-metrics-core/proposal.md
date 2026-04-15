# SDD Proposal — ledger-metrics-core

## Problema a resolver

El plan paper-first exige que el `ledger` soporte métricas operativas críticas para riesgo y control del sistema:
`cash`, posiciones, valoración MTM, PnL realizado/no realizado, equity curve y drawdown mensual del subportfolio corto.
Hoy el `core_sim` tiene `event_engine` y `cost_model`, pero no existe aún un módulo de `ledger` con contrato estable.

## Enfoque propuesto

Implementar un `core_sim.ledger` determinístico y testeable que procese fills diarios, marque posiciones a mercado y exponga un snapshot único para la etapa `LedgerUpdated` del `DailyEventBacktester`.
El diseño seguirá `agent-teams-lite` para separar decisiones de política/especificación y decisiones de simulación core.

## Scope inicial (fase apply futura)

- Nuevo módulo `core_sim/ledger.py` con estado de caja, inventario por símbolo y métricas.
- Cálculo explícito de:
  - PnL realizado por cierres parciales/totales.
  - PnL no realizado por MTM diario.
  - Equity curve acumulada.
  - Drawdown mensual del bucket corto (base para kill switch).
- Export del contrato en `core_sim/__init__.py`.
- Tests de comportamiento en `tests/test_ledger.py`.
- Integración mínima de smoke test en `tests/test_event_engine.py` usando snapshot de `ledger`.

## Enfoque agent-teams-lite

- **Rol Spec/policy**: fija contrato del snapshot y definición de drawdown mensual del bucket corto.
- **Rol Core sim**: implementa motor de libro contable y reglas de actualización de posiciones/PnL.
- **Rol QA/CI**: valida invariantes (conservación de cash+inventory en MTM, separación realizado/no realizado, DD mensual correcto).

## Archivos/módulos afectados

- `core_sim/ledger.py` (nuevo)
- `core_sim/__init__.py`
- `tests/test_ledger.py` (nuevo)
- `tests/test_event_engine.py`
- `decisiones-tecnicas.md` (nuevo ADR de ledger, si cerramos apply)

## Riesgos identificados

- Ambigüedad en definición de costo base para PnL realizado (promedio vs FIFO).  
  Mitigación: fijar una política explícita v1 (promedio ponderado) en spec.
- Drawdown mensual del bucket corto mal definido respecto al reset mensual.  
  Mitigación: anclar DD a `peak-to-trough` dentro del mismo mes calendario y resetear al cambio de mes.
- Contratos de fill incompletos (falta market/bucket).  
  Mitigación: definir defaults estrictos y validaciones para fallar temprano.

## Estado

- `status`: proposed
- `next`: checkpoint-1-approval
