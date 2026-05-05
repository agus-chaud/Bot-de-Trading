# Bot de Trading (Paper-First)

Bot de trading/inversión en Python con foco en perfil moderado, arquitectura desacoplada y riesgo determinístico en código.

## Problema de negocio que resuelve

No todos podemos estar mirando mercado todo el día para decidir compras y ventas manuales. El problema real no es "falta de ideas", es **falta de proceso consistente bajo presión**: cuando operas a mano, el sesgo emocional, la falta de tiempo y la ejecución improvisada suelen destruir rentabilidad.

Este proyecto busca resolver eso con una tesis simple: **si quieres ganar plata de forma sostenible, necesitas un sistema repetible, medible y auditable**.

## Objetivo del proyecto

- Priorizar `paper trading` con datos reales antes de cualquier integración live.
- Separar motores por horizonte:
  - `short_term_engine` (diario, 30% objetivo)
  - `long_term_engine` (mensual, 70% objetivo)
- Centralizar controles críticos en un núcleo común: `risk_guardrails`, `allocator`, `paper_broker_sim`, `ledger`.
- Mantener trazabilidad y auditabilidad de decisiones de riesgo y ejecución.

## Enfoque inicial: paper trading para aprender sin perder plata

El arranque en `paper trading` no es miedo

- Permite validar metodología con datos reales y costos simulados antes de exponer capital.
- Obliga a medir resultados netos (incluyendo fricción), no solo señales lindas.
- Hace visibles fallos de lógica/riesgo temprano (kill switch, límites, calidad de datos, rebalanceos).

En resumen: primero se prueba el proceso, despues se escala con plata de verdad.

## Siguiente paso: pasar a capital real (cuando el sistema lo merezca)

La transición a real está planteada como gate, no como salto de fe:

- `pre-gate` estadístico (walk-forward OOS) para bloquear sobreajuste.
- Criterios de riesgo y performance definidos de antemano.
- Ramp-up gradual de exposición (por etapas), manteniendo los mismos guardarraíles.


## Estado actual (abril 2026)

### Implementado

- **Data layer completo** (Fases A-G):
  - `data/schema.py` — `OHLCVRow` + `CorporateActionRow` (frozen dataclasses)
  - `data/storage.py` — `MarketDB` con SQLite local + sync Supabase lazy-init
  - `data/calendar_builder.py` — calendario US (NYSE/XNYS) y AR (XBUE) via `pandas_market_calendars`
  - `data/connectors/us_connector.py` — YFinance con retry exponencial, NetworkError/DataError
  - `data/connectors/ar_connector.py` — IOL REST API primario + fallback Byma (yfinance `.BA`)
  - `data/normalizer.py` — outlier detection (rolling 5d median), forward-fill ≤3 días, `imputed=True`
  - `data/fetcher.py` — pipeline connector→normalize→upsert, FetchReport
  - `scripts/fetch_daily.py` — CLI diario con `--lookback`, `--db`, whitelist desde policy.v1.yaml
  - 202 tests (unitarios + integración end-to-end)

- **Risk kill switch persistente**:
  - `data/storage.py` — `KillSwitchState` + tabla `kill_switch_log` en MarketDB
  - `core_sim/risk_guardrails.py` — `check_and_persist_kill_switch()` con auto-reset mensual
  - `scripts/reset_kill_switch.py` — CLI reset manual con `--category` obligatorio + `--reason`
  - Alert files en `alerts/kill_switch_YYYY-MM-DD.json`
  - `core_sim/short_term_day_runner.py` — cableado con `db` opcional, backward compatible
  - 25 tests (unitarios + integración)

- Política de riesgo y operativa:
  - `POLICY.md`
  - `config/policy.v1.yaml`
  - `config/policy.v1.schema.json`
  - tests de contrato en `tests/test_policy_schema.py`
- Core de simulación (`core_sim/`):
  - `DailyEventBacktester` con pipeline fijo
  - `CostModel` configurable por mercado
  - `PortfolioLedger` con PnL y drawdown mensual del bucket corto
  - `PaperBrokerSim` con interfaz estable y fills determinísticos
  - Stores de calendario y corporate actions (v1)
- `short_term_engine` v1 (Fase B):
  - módulo `core_sim/short_term_engine.py`
  - helpers puros para score/filtros/ranking/sizing
  - generación de `orders_intent` con `skip_reasons` y métricas
  - tests unitarios en `tests/test_short_term_engine.py`
- Pipeline corto integrado (Fase C, v1):
  - `core_sim/short_term_day_runner.py` — Data (snapshot + whitelist) → Engines → Risk → órdenes listas para `PaperBrokerSim`
  - **Riesgo en el mismo runner**: kill switch persistente por DD mensual del bucket corto; límite de **pérdida diaria** del corto (`risk.max_daily_loss_short_pct` + `short_bucket.daily_return` en `PortfolioLedger`); **ventanas no-trade** intradía US (`no_trade_*_minutes` + `session_minutes_from_open` opcional en `pipeline_context`); **`halt_on_data_quality`** con señal `risk_flags` (universo parcial en `daily_bars` permitido; fallan barras vacías o campos inválidos en símbolos presentes).
  - **Allocator 30/70 + 20/80** en el sizing de `build_orders_intent`: tope de tranche corto vs equity total y headroom AR/US por mercado sobre el total, antes de lotes y cash.
  - `create_short_term_daily_backtester(...)` arma un `DailyEventBacktester` cableado al broker y al ledger
  - `DailyEventBacktester.run_day(..., pipeline_context={"history_by_symbol": ...})` admite también `session_minutes_from_open` cuando se quiera simular no-trade intradía
  - tests de integración en `tests/test_short_term_day_runner.py` (E2E, kill switch, no-trade, calidad de datos, pérdida diaria)
- **Pruebas y CI** (criterio *smart-testing*): suite en `tests/` con foco en comportamiento; `pytest-cov` en `requirements.txt`; GitHub Actions ejecuta `pytest` con cobertura mínima sobre `core_sim`.
- **Pre-gate walk-forward (Fase 3)**: `core_sim/short_term_pre_gate.py` + `short_term_pre_gate` en `config/policy.v1.yaml`; script `scripts/run_short_term_pre_gate.py` (demo sintética); tests en `tests/test_short_term_pre_gate.py`.
- **Walk-forward del motor largo (T4-T6)**:
  - `validation/wf_windows.py` — generador de ventanas rolling por meses (`window_months`, `step_months`)
  - `validation/wf_runner.py` — ejecución por ventana de `run_long_engine_stage` (corridas independientes)
  - `validation/wf_long_report.py` — agregación global y serialización JSON del reporte WF largo
  - `scripts/run_long_engine_wf.py` — CLI para producir `validation_reports/long_engine_wf_*.json`
  - tests en `tests/test_wf_windows.py`, `tests/test_wf_runner.py`, `tests/test_wf_long_report.py`

### Pendiente principal

- Integración completa del `long_term_monthly_runner` en `event_engine` operativo diario.
- Informe KPI avanzado (Sharpe, Sortino, Calmar, alpha vs benchmark, etc.) y gate de ramp más allá del reporte base de validation/WF.
- Conectar fuentes de datos reales (feeds, APIs broker) con `PaperBrokerSim` como adaptador, manteniendo interfaces estables.


## Componentes clave y cómo se vinculan

- `calendar_store` + `corporate_actions`: definen dias y horarios válidos US/AR y ajustan posiciones por splits/dividendos para evitar rebalanceos "fantasma".
- `risk_guardrails`: módulo centralizado de decisiones de riesgo (fail-fast por calidad de datos, ventanas no-trade, límites diarios, kill switch); retorna `GuardrailResult`, no ejecuta.
  - `check_short_risk()`: validaciones del motor corto (data quality → no-trade window → kill switch → daily loss).
  - `check_long_risk()`: guardrail diario -1.5% para motor largo.
  - `compute_atr()` + `check_stop_loss()`: stop loss por ticker individual usando ATR(14) con fallback a porcentaje.
  - `log_risk_cycle()`: JSON logging estructurado de decisiones de riesgo.
- `short_term_engine` + `short_term_day_runner`: motor diario de corto plazo (momentum + filtros de liquidez/volatilidad + top-K + sizing por riesgo); integra `risk_guardrails` en pipeline.
- `pending_order_queue`: cola para modo semi_auto, permite validación manual antes de ejecutar.
- `short_term_pre_gate`: walk-forward OOS automático del bloque corto antes de habilitar más capital.
- `long_term_engine` + `long_term_monthly_runner`: motor mensual del sleeve largo (pesos objetivo, bandas de drift, intents de rebalanceo); integra `check_long_risk()`.
- `validation/wf_windows` + `validation/wf_runner` + `validation/wf_long_report`: pipeline WF del bloque largo (ventanas rolling -> stage por ventana -> agregados globales + JSON).
- `event_engine`: orquestador diario con soporte para `execution_mode` (auto/semi_auto) y bypass de stop loss en semi_auto.
- Flujo completo: Data -> Engines -> `risk_guardrails` -> Allocator -> `paper_broker_sim` -> `ledger`; ambos motores convergen en el mismo núcleo para mantener consistencia y auditoría.

## Contratos y documentación clave

- Plan maestro: `.cursor/plans/bot_trading_paper-first_155d6f04.plan.md`
- Política operativa: `POLICY.md`
- Decisiones técnicas (ADR): `decisiones-tecnicas.md`
- Config parseable v1: `config/policy.v1.yaml`
- Schema de validación: `config/policy.v1.schema.json`

## Ejecutar validaciones

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
python -m pytest tests/ -v --cov=core_sim --cov-report=term-missing
python scripts/run_short_term_pre_gate.py
python scripts/run_long_engine_wf.py --window-months 6 --step-months 1
```

Por módulo (desarrollo acotado):

```bash
python -m pytest tests/test_policy_schema.py -v
python -m pytest tests/test_short_term_engine.py -v
python -m pytest tests/test_short_term_day_runner.py -v
python -m pytest tests/test_event_engine.py -v
```

## Principios no negociables

- Sin secretos en repo (`.env` o gestor de secretos).
- Paper-first por defecto.
- Riesgo en código determinístico, no en prompts.
- Cambios acotados y auditables.
