# AGENTS — contexto para humanos y agentes (Bot de Trading, paper-first)

## Propósito del repo

Bot de trading/inversión en **Python**, perfil **moderado**, split **30/70** (corto/largo) y **20/80** (AR/US), **paper trading** con datos reales, riesgo **determinístico** antes que heurísticas opacas o LLM en la ejecución.

Problema que atacamos: no quiero depender de trading manual diario (tiempo + sesgo emocional), pero sí construir un proceso con probabilidad de retorno sostenible. El enfoque es **proceso primero, dinero después**: metodología medible en paper, luego ramp-up controlado a capital real.

Plan maestro: `.cursor/plans/bot_trading_paper-first_155d6f04.plan.md`.

## Fuentes de verdad

| Qué | Dónde |
|-----|--------|
| Política humana (umbrales, matriz de violaciones, mapa de datos) | `POLICY.md` |
| Gate KPI OOS (umbrales pre-registrados, ramp-up) | `POLICY.md` §13-14 + `config/policy.v1.yaml` → `kpi_oos_gate`, `ramp_stage` |
| Contrato parseable (YAML) | `config/policy.v1.yaml` |
| Validación estructural CI | `config/policy.v1.schema.json` + `tests/test_policy_schema.py` |
| Listas de símbolos | `config/symbols/whitelist_us.yaml`, `whitelist_ar.yaml`, `whitelist_cedear.yaml` (largo AR / CEDEAR; ver **ADR-048**) |

Ante conflicto numérico entre `POLICY.md` y YAML, **actualizar ambos en el mismo cambio** y anotar el motivo en el commit.

## Límites duros (todos los agentes)

1. **Sin secretos en el repo**: API keys, tokens, cookies → `.env` o gestor de secretos; nunca en YAML de ejemplo ni en tests commiteados.
2. **Paper-first por defecto**: no integrar ejecución live hasta fases y gates documentados en el plan.
3. **Riesgo en código, no en prompts**: guardarraíles, kill switch y límites por bucket no dependen de LLM.
4. **Cambios acotados**: no refactor masivo fuera del issue; seguir estilo y layout existente.

## Roles sugeridos (agent teams lite)

Usar roles para **acotar** qué toca cada subagente o PR. Solapamiento mínimo.

| Rol | Responsabilidad | Rutas típicas |
|-----|-----------------|---------------|
| **Spec / policy** | `POLICY.md`, `config/*.yaml`, `config/symbols/*`, schema JSON | `POLICY.md`, `config/` |
| **Core sim** | Paper broker, ledger (short_bucket + long_bucket), costos, event engine | `core_sim/paper_broker_sim.py`, `core_sim/ledger.py`, `core_sim/event_engine.py`, `core_sim/cost_model.py` |
| **Data** | Snapshot OHLCV + historial, whitelist, calendario en `MarketOpen`, corporate actions v1 | `core_sim/short_term_day_runner.py`, `core_sim/long_term_engine.py` (input contract), `core_sim/calendar_store.py` |
| **Engines** | Señales → intents; integración diaria corta; motor largo semanal/mensual por bandas (calendario **AR** `ar_business_days` o **US** `us_sessions`); pre-gate walk-forward OOS; orquestación paper-live short→long (con **overlay XBUE** de precios para líneas del largo AR cuando el merge corto etiqueta CEDEAR como US) | `core_sim/short_term_engine.py`, `core_sim/short_term_day_runner.py`, `core_sim/long_term_engine.py`, `core_sim/long_term_monthly_runner.py`, `core_sim/short_term_pre_gate.py`, `scripts/run_short_term_pre_gate.py`, `scripts/run_paper_live.py` |
| **Risk** | Guardrails centralizados en `risk_guardrails.py`: fail-fast data quality, ventanas no-trade, kill switch mensual corto, pérdida diaria bucket corto, stop loss ATR por ticker; allocator 30/70 + 20/80 en sizing; gestión de riesgo motor largo (-1.5% diario) | `core_sim/risk_guardrails.py`, `core_sim/short_term_day_runner.py` (handlers `propose_orders` / `risk_check`), `core_sim/long_term_monthly_runner.py`, `config/policy.v1.yaml` → `risk`, `weights`, `geo`, `stop_loss` |
| **QA / CI** | Tests por **comportamiento** (ver *Smart testing*), schema policy, cobertura `core_sim` en CI; regresión largo AR: `tests/test_long_term_engine.py`, `test_long_term_monthly_runner.py`, `test_validation_long_engine.py`, `test_policy_yaml.py` (rebalance primer hábil AR, stage sin depender de XNYS, SPY CEDEAR con `market: AR`) | `tests/`, `.github/workflows/ci.yml` |

Un agente en rol **Spec** no debería implementar broker simulado; uno en rol **Core sim** no debería reescribir listas blancas sin coordinación con **Spec**.

## Arquitectura de riesgo (Fase 4)

El módulo `core_sim/risk_guardrails.py` es el **punto centralizado de decisiones de riesgo** del sistema:

- **`check_short_risk()`**: ejecuta fail-fast por orden de severidad operativa:
  1. Calidad de datos (`data_quality_flags`).
  2. Ventana no-trade intradía (390 min sesión US).
  3. Kill switch por drawdown mensual bucket corto (-8%).
  4. Límite de pérdida diaria bucket corto.
  - Retorna `GuardrailResult` con decisión binaria (trade / no-trade) y motivo. **No ejecuta**, solo recomienda.

- **`check_long_risk()`**: guardrail efectivo para motor largo:
  - Límite diario del sleeve largo (-1.5% del equity largo).
  - Recibe `long_daily_return` real desde `long_bucket` del snapshot de `mark_to_market` — no depende del default 0.0.
  - Retorna `GuardrailResult` análogo.

- **`check_stop_loss()` + `compute_atr()`**: evaluación de stop loss por ticker:
  - Calcula ATR(14) sobre histórico; si faltan 15 barras, usa fallback porcentaje.
  - **Bypass de otros guardrails**: un stop loss **SIEMPRE sale**, incluso fuera de ventana no-trade.
  - En semi_auto, la orden de stop se ejecuta directo sin pasar a `PendingOrderQueue`.

- **`log_risk_cycle()`**: JSON estructurado con decisión, flags, métricas de MTM y timestamp para auditoría.

**Aclaración arquitectónica**: `risk_guardrails` NO es un ejecutor — es un **componente de recomendación**. El executor real está en:
- `short_term_day_runner.py` (handlers `propose_orders` y `risk_check`): `_check_risk_with_optional_db` es un orquestador liviano que reutiliza `check_short_risk()` para data_quality + no_trade + daily_loss e inyecta `check_and_persist_kill_switch()` cuando hay DB. Sin DB, delega directo a `check_short_risk()`.
- `long_term_monthly_runner.py` (handler de rebalanceo): extrae `snap["long_bucket"]` y pasa `long_daily_return` explícito a `check_long_risk()`.

Esto permite auditar "por qué no se ejecutó" separando la lógica de decisión (guardrails) del flujo operativo (runners).

## Smart testing (criterio de calidad)

Alineado a la skill **smart-testing**: probamos **comportamiento observable**, no detalles de implementación; prioridad **reglas de negocio / riesgo** → integración (`DailyEventBacktester` + pipeline corto) → helpers puros. Evitar sobre-mocking de módulos propios (`ledger`, `PaperBrokerSim`): preferir instancias reales en tests de integración. Los nombres de test describen el efecto (“should…”, “blocks…”, “stops…”). Cobertura es indicador **secundario** a la confianza en reglas; en CI se exige umbral mínimo sobre `core_sim` (ver workflow).

## Comandos útiles

```text
pip install -r requirements.txt
python -m pytest tests/ -v
python -m pytest tests/ -v --cov=core_sim --cov-report=term-missing
```

## Modelo de branches (paper-live)

El proyecto usa dos ramas con responsabilidades distintas:

| Rama | Propósito | Qué se commitea |
|------|-----------|-----------------|
| `main` | Evolución de código, PRs, CI | Solo código y docs |
| `paper-live-data` | Operación diaria automatizada | Código + `data/market.db` (via Git LFS) |

- El **workflow** (`paper_live_daily.yml`) vive en `main` (GitHub lee schedule/dispatch del default branch), pero hace `checkout` de `paper-live-data` para ejecutar.
- **Secretos IOL en GitHub Actions**: `IOL_USER` y `IOL_PASS` deben existir como *repository secrets*. Variables de entorno locales del operador **no** aplican al runner. Sin ellos, fetch AR degrada y el catch-up puede fallar.
- **Política F3** (`run_paper_live.py`): máximo **3** días hábiles de catch-up por corrida; `exit 2` si el gap es mayor (recuperación manual en tandas). Días sin OHLCV en el gap se **saltan** (warning), no abortan el rango completo (**ADR-050**).
- **Sincronización de código**: `git checkout paper-live-data; git merge main` trae cambios de código sin perder la DB.
- **Git LFS**: `data/*.db` en `paper-live-data` se trackea con LFS (`.gitattributes`); en `main` la DB está gitignoreada. Conflictos de merge en `market.db`: resolver puntero con `git checkout --ours|--theirs`, nunca editar `<<<<<<<` en el puntero.
- **Notificación de fallos**: el workflow crea un issue GitHub automáticamente si algún step falla (detección temprana, evita violar F3).
- Decisiones: **ADR-040** (modelo branches + workflow), **ADR-050** (incidente may–jun 2026, runbook).

## Integración largo en paper-live (ADR-044)

- `run_paper_live.py` soporta `--enable-long-engine` (default `false`).
- Con flag activo: ejecuta short primero, luego long sobre el mismo ledger/broker. Fills combinados. Snapshot final post-ambos sleeves.
- Con policy de **calendario AR**, tras el corto se aplican precios **XBUE** en una copia de `daily_bars` para todas las líneas de `long_term_engine` (**ADR-048**): coherencia CEDEAR/pesos vs. etiquetado US del merge del corto.
- Sin flag: flujo idéntico al anterior (solo corto). **Rollback inmediato** sin cambio de código.
- `PortfolioLedger.mark_to_market` retorna `long_bucket` con `long_daily_return` real.
- `check_long_risk()` recibe ese `long_daily_return` explícitamente (no default 0.0).
- `_check_risk_with_optional_db` en el runner corto es un orquestador liviano: reutiliza `check_short_risk` + inyecta `check_and_persist_kill_switch` con DB (sin duplicar la cadena de 4 checks).

## Stage `long_engine` (validación offline, ADR-048)

- Corre el pipeline largo sobre `MarketDB`: si la regla es `first_ar_*`, usa fechas y OHLCV **XBUE**; el stage **no** requiere filas de calendario **XNYS** para política AR.
- `PaperBrokerSim` del stage lleva `CostModel` con **una sola clave** de mercado (`AR` o `US`) según `long_sleeve_trade_market`, leída de `policy["markets"]` (misma semántica que paper-live para comisión/slippage/spread mínimo).
- Si existe `config/calendars/trading_days.v1.yaml`, se pasa `TradingCalendarStore` al backtester del largo (coherencia con paper-live para `ar_business_days` / `us_sessions`).

## Paper-live: calendario obligatorio (ADR-054)

- `scripts/run_paper_live.py` carga el YAML de `policy.calendar.source_of_truth` **antes** del catch-up. Si falta o está vacío → `exit 1` (no degradar con `calendar_store=None`).
- `--no-calendar`: opt-out explícito para tests; desactiva flags de sesión en `MarketOpen` (modo permisivo).
- Regenerar calendario: `python scripts/build_trading_days_yaml.py`. Stub de tests: `tests/fixtures/calendars/trading_days_stub.v1.yaml`.
- Golden replay (T0.2): `tests/fixtures/replay_golden/` + `tests/test_replay_golden.py` — caracterización de `replay_ledger_from_fills` antes de cambios en persistencia de capital.
- **`portfolio_meta` (T1.1)**: tabla SQLite `portfolio_meta` — `starting_cash`, `currency` (`ARS`/`USD`), `inception_date` por `mode`. Primera corrida escribe; siguientes validan. Default CLI: 3_000_000 ARS.

## Convenciones

- **Idioma**: documentación de producto/política en español; código y nombres de módulos en inglés salvo dominio AR/US ya acordado.
- **Versionado de config**: subir `schema_version` y archivo `policy.v{N}.yaml` al romper el contrato; mantener `policy.v1.schema.json` alineado a v1 o renombrar a `policy.v2.schema.json` con el nuevo major.

## Referencias

- Notas en `knowledge-base/` (contexto de producto, no normativa operativa).
- `README.md` (narrativa de negocio y mapa breve de componentes para retomada rápida).
