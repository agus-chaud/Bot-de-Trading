# Bot de Trading (Paper-First)

Bot de trading/inversión en Python con foco en perfil moderado, arquitectura desacoplada y riesgo determinístico en código.

## Problema de negocio que resuelve

No todos podemos estar mirando mercado todo el día para decidir compras y ventas manuales. El problema real no es "falta de ideas", es **falta de proceso consistente bajo presión**: cuando operas a mano, el sesgo emocional, la falta de tiempo y la ejecución improvisada suelen destruir rentabilidad.

Este proyecto busca resolver eso con una tesis simple: **si quieres ganar plata de forma sostenible, necesitas un sistema repetible, medible y auditable**.

## Objetivo del proyecto

- Priorizar `paper trading` con datos reales antes de cualquier integración live.
- Separar motores por horizonte:
  - `short_term_engine` (diario, 30% objetivo)
  - `long_term_engine` (semanal, 70% objetivo)
- Centralizar controles críticos en un núcleo común: `risk_guardrails`, `allocator`, `paper_broker_sim`, `ledger`.
- Mantener trazabilidad y auditabilidad de decisiones de riesgo y ejecución.

## Enfoque inicial: paper trading para aprender sin perder plata

El arranque en `paper trading` no es miedo a operar: es disciplina de proceso.

- Permite validar metodología con datos reales y costos simulados antes de exponer capital.
- Obliga a medir resultados netos (incluyendo fricción), no solo señales lindas.
- Hace visibles fallos de lógica/riesgo temprano (kill switch, límites, calidad de datos, rebalanceos).

En resumen: primero se prueba el proceso, despues se escala con plata de verdad.

## Siguiente paso: pasar a capital real (cuando el sistema lo merezca)

La transición a real está planteada como gate, no como salto de fe:

- `pre-gate` estadístico (walk-forward OOS) para bloquear sobreajuste.
- **Gate KPI OOS activo** (`kpi_oos_gate.enabled: true`): umbrales pre-registrados (Sharpe ≥ 0.30, DD total ≥ -18%, alpha ≥ -2%, etc.) congelados el 2026-05-11 antes del primer resultado OOS. Detalle en `POLICY.md` §13.
- **Ramp-up gradual** en 5 escalones: paper → 10% → 25% → 50% → 100% del capital, con duración mínima por escalón y criterios de rollback. Detalle en `POLICY.md` §14.


## Estado actual (junio 2026)

### Implementado

- **Data layer completo** (Fases A-G):
  - `data/schema.py` — `OHLCVRow` + `CorporateActionRow` (frozen dataclasses)
  - `data/storage.py` — `MarketDB` con SQLite local + sync Supabase lazy-init
  - `data/calendar_builder.py` — calendario US (NYSE/XNYS) y AR (XBUE) via `pandas_market_calendars` (persiste en tabla `calendars` de SQLite)
  - `scripts/build_trading_days_yaml.py` — exporta el mismo calendario a `config/calendars/trading_days.v1.yaml` (fuente versionada para paper-live y tests de integración; **ADR-054**)
  - `data/connectors/us_connector.py` — YFinance con retry exponencial, NetworkError/DataError
  - `data/connectors/ar_connector.py` — IOL REST API primario + fallback Byma (yfinance `.BA`)
  - `data/normalizer.py` — outlier detection (rolling 5d median), forward-fill ≤3 días, `imputed=True`
  - `data/fetcher.py` — pipeline connector→normalize→upsert, FetchReport
  - `scripts/fetch_daily.py` — CLI diario con `--lookback`, `--db`, whitelist desde policy.v1.yaml
  - **Universo AR híbrido (V2, opcional via policy)**: listas candidatas Merval + CEDEAR (`whitelist_ar.yaml`, `whitelist_cedear.yaml`), ranking por volumen IOL en ventana fija, rebalanceo semanal configurable, persistencia en `universe_snapshots`; la lista efectiva de ingesta OHLCV es **top liquidez ∪ posiciones AR abiertas** (sticky holdings). Presupuesto de API IOL medido por tipo (`token` / `refresh` / `history` / `universe_volume`) con techo mensual y por corrida (`max_calls_per_job`). Resolución compartida con el corto en `data/universe_selector.py` (**ADR-047**).
  - `data/venue_policy.py` — fuente única market tag ↔ venue ↔ moneda (US → XNYS, AR → XBUE); lectores de OHLCV filtran por venue para no mezclar USD/ARS (**ADR-052**).
  - Suite `pytest tests/` — **601** casos (unitarios + integración); cobertura mínima `core_sim` en CI.

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
  - `PortfolioLedger` con PnL, drawdown mensual del bucket corto, **daily return del sleeve largo** (`long_bucket`) y valuación resiliente a huecos (`stale_marks`, carry-forward de último close — **ADR-051**)
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
  - `validation/stages/long_engine.py` — `run_long_engine_stage(..., return_details=True)` devuelve tupla `(StageResult, StageDetails | None)` con curva diaria del sleeve largo, fills y posiciones finales (default: solo `StageResult` vía desempaquetado en callers). Para policy **AR**: fechas efectivas y barras desde **XBUE**; el stage **no depende** de tener calendario **XNYS** en la DB; `CostModel` del broker del stage usa solo el mercado del sleeve (`AR`/`US`). En validación offline, `TradingCalendarStore` desde YAML es opcional; en **paper-live** el calendario es **obligatorio** (**ADR-054**).
  - `notebooks/wf_long_comparison.ipynb` — comparación empírica ADR-045/046: WF por ventanas (3m/1m) + gráficos + corrida continua sin reset; ejecutar todas las celdas desde `notebooks/` (**ADR-046**)
  - tests en `tests/test_wf_windows.py`, `tests/test_wf_runner.py`, `tests/test_wf_long_report.py`, `tests/test_validation_long_engine.py`
- **Informe KPI (smoke, `rpt_kpi.v1`)**:
  - `docs/kpi_report_spec.v1.md` — definiciones operativas (fecha de congelación 2026-05-05).
  - `reporting/kpi_v0.py` — CSV diario §2.1 + fills/trades §2.2 (opcional si el equity trae `costs_day_short`/`costs_day_long`): retorno neto anualizado total §5, max drawdown §7, **Sharpe/Sortino** por segmento (equity total/corto/largo) §6, **hit rate y profit factor** por motor FIFO §8, **drift 30/70 y 20/80** §11, y v3 en bloque largo (**MDD_12m rolling**, **Calmar_12m**, **turnover_long_monthly**) + **alpha vs benchmark** §12.
  - `scripts/report_kpis.py` — `--equity`, `--trades`, `--metadata`, `--benchmark-returns` → `--out-json` y `--out-md`.
  - **Walk-forward OOS del KPI v3 (Fase 5, tabla maestra + gate opcional)**:
    - Bloque **`kpi_oos_gate`** en `config/policy.v1.yaml` (schema en `policy.v1.schema.json`): rejilla **`burn_in` / OOS / step**, agregación **`all`** o **`k_of_last_q`**, umbrales por métrica; **`enabled: true`** con umbrales pre-registrados (ver §13 de POLICY.md).
    - `reporting/kpi_walk_forward.py` — por cada ventana: slice CSV → mismo informe que v3 (`build_kpi_v0_report_from_tables`) → **`master_table`** + pass/fail por ventana si el gate está activo.
    - `core_sim/short_term_pre_gate.py` — **`walk_forward_oos_windows`** (API pública de rejilla de ventanas).
    - `scripts/report_kpis_walk_forward.py` — export JSON consolidado (exit ≠ 0 si falla gate agregado o hay fallos globales).
    - `tests/test_kpi_walk_forward.py`.
    - Detalle normativo en `decisiones-tecnicas.md` (**ADR-034**).
  - **Regresión KPI en CI (60 días fijos, golden)** — Fase 5 ítem 9:
    - `tests/fixtures/kpi_golden/` — equity, trades, benchmark, metadata y `expected_kpis.json` versionados.
    - `tests/test_kpi_regression_golden.py` — compara salida de `build_kpi_v0_report` contra el golden (sin mocks del propio informe).
    - `scripts/regenerate_kpi_golden_fixtures.py` — regeneración manual si cambia el spec o se acepta drift de números (revisar diff del JSON).
    - `decisiones-tecnicas.md` (**ADR-035**).
  - `tests/test_kpi_v0.py` — comportamiento + series sintéticas.
  - Decisiones registradas en `decisiones-tecnicas.md` (**ADR-030**, **ADR-031**, **ADR-032**, **ADR-033**, **ADR-034**, **ADR-035**).

- **Medición de señal y escenarios what-if** (capa de research, sin ejecución):
  - `reporting/signal_ic.py` — IC de ranking, hit rate@K, quantile spread y curva de decay sobre scores del motor corto; respeta venue por market tag (**ADR-052**).
  - `reporting/scenario.py` + `scripts/run_scenario.py` — escenarios paramétricos (overrides de `short_term_engine`) sobre la misma capa determinística.
  - `reporting/data_quality_envelope.py` — envoltorio de confianza (`stale_marks`, `imputed_pct`, umbrales policy) para informes de señal.
  - `scripts/run_signal_ic_now.py` — CLI de medición IC sobre `data/market.db`.
  - Tests en `tests/test_signal_ic.py`, `tests/test_signal_ic_venue_filter.py`, `tests/test_scenario.py`, `tests/test_data_quality_envelope.py`.

- **Integridad de datos y universo ampliado**:
  - **ADR-051** — `mark_to_market` con carry-forward y `stale_marks` (destraba `run_validation_wf` ante huecos puntuales, p. ej. TXAR).
  - **ADR-052** — filtro de venue en lectores de señal (`signal_ic`, pre-gate, validation short_pre_gate); IC a h=1 corregido tras limpiar mezcla USD/ARS.
  - **ADR-053** — +10 símbolos diversificados (Merval: CRES, TECO2, LOMA, MIRG, IRSA; CEDEARs: V, UNH, CAT, PEP, NFLX) para ampliar breadth de la cross-section.
  - `docs/complicaciones-tecnicas.md` — guion de las 8 complicaciones técnicas vividas (IOL 401, mezcla de monedas, breadth, etc.).

- **Paper-live daily orchestrator**:
  - `scripts/run_paper_live.py` — CLI que ejecuta el pipeline día a día contra OHLCV real en SQLite, con catch-up automático y política F3 (gap > 3 días de mercado US∪AR → exit(2), intervención manual).
  - **Calendario obligatorio**: carga `policy.calendar.source_of_truth` antes del catch-up; si falta → `exit 1`. `--no-calendar` solo para tests (**ADR-054**). Regenerar: `python scripts/build_trading_days_yaml.py`.
  - **`portfolio_meta` (T1.1)**: primera corrida persiste `--initial-cash` y `--currency` (default **3_000_000 ARS**); corridas siguientes validan — mismatch → `exit 1`. DB legacy con snapshots sin meta: usar una vez `--init-portfolio-meta --initial-cash 1000 --currency USD` (ajustar al historial real).
  - **`short_cash` en snapshots (T1.3)**: columna `paper_snapshots.short_cash` persiste `ledger.short_cash` (cash real del bucket corto), no `cash × weights.short` (**ADR-039**, **ADR-055**).
  - **Gap F3 con calendario real (T1.4)**: el conteo de días para F3 usa la **unión** de sesiones US (XNYS) y días hábiles AR (XBUE) del YAML; el calendario se carga **antes** del check F3. Evita falsos `exit 2` en feriados US y no omite días AR-only (**ADR-055**).
  - Con `--enable-long-engine`, tras el **corto** se construye una copia de `daily_bars` y se **sobrescriben** los precios de las líneas del **`long_term_engine`** con OHLCV **XBUE** cuando el policy usa calendario **AR**, de modo que CEDEAR/pesos (p. ej. `SPY`) no usen cierres **XNYS** por el merge global — ver **ADR-048**.
  - **Soporte para ambos sleeves**: con ese flag se ejecuta short → long sobre el mismo ledger/broker; fills combinados y snapshot final con MTM usando la copia de barras cuando el largo está activo. Sin flag (default), solo corto.
  - `data/storage.py` — `get_last_snapshot_day(mode)` para detectar último día procesado.
  - `.github/workflows/paper_live_daily.yml` — cron Lun–Vie 10:00 UTC (post-cierre US) + `workflow_dispatch` (input opcional `date`); opera sobre branch `paper-live-data` y commitea la DB tras cada corrida. Incluye step de **fetch OHLCV** previo al pipeline (`fetch_daily.py --lookback 5`) y **notificación automática** (issue GitHub) ante fallos.
  - **Secretos GitHub (obligatorio para CI)**: `IOL_USER` y `IOL_PASS` en *Settings → Secrets and variables → Actions*. Variables locales de Windows **no** alimentan el runner. Diagnóstico: `python scripts/diagnose_iol_auth.py`.
  - **Política F3**: catch-up automático de hasta **3** días de mercado por corrida (unión US + AR según calendario; ver arriba); gap mayor → `exit(2)` y recuperación manual en tandas (`workflow_dispatch` con `date` o local + push). Días sin barras (feriados) se **saltan con warning** sin abortar todo el rango (**ADR-050**).
  - **Branch `paper-live-data`**: rama dedicada para datos operativos diarios (DB + fills + snapshots); `main` se mantiene limpio para evolución de código. Git LFS para `data/*.db` evita inflación del repo por commits binarios diarios. Conflictos en `data/market.db` al hacer merge: resolver puntero LFS con `git checkout --ours` o `--theirs`, no editar marcadores `<<<<<<<` a mano.
  - Tests en `tests/test_data_storage.py`, `tests/test_run_paper_live.py` (gap detection, F3 exit code, single/multi-day integration, idempotencia, **calendario obligatorio**, **feature flag long on/off**) y `tests/test_replay_golden.py` (golden fixture fills → ledger replay).
  - Decisiones registradas en `decisiones-tecnicas.md` (**ADR-040**, **ADR-044**, **ADR-048**, **ADR-050**, **ADR-054**, **ADR-055**).

- **Simulación what-if de cartera** (research / análisis, no paper-live productivo):
  - `scripts/run_whatif_sim.py` — corre estrategia **30/70** (short + long) día a día sobre **copia aislada** de `market.db`; reporta equity, fills y posiciones. No aplica gate F3 (backtests multi-mes intencionales). Default fin **2026-06-02** (último día con OHLCV XBUE completo al jun 2026).
  - Diferencia de `run_scenario.py`: este mide **IC de señal** con overrides paramétricos; `run_whatif_sim.py` simula **ejecución completa** con broker, ledger y persistencia en DB scratch (`data/_sim/`).

- **Gate KPI OOS activo** (Fase 5, gate-ramp):
  - `kpi_oos_gate.enabled: true` en `config/policy.v1.yaml` con 7 umbrales bloqueantes pre-registrados (2026-05-11): Sharpe ≥ 0.30, Sortino ≥ 0.40, DD total ≥ -18%, DD corto ≥ -10%, DD largo ≥ -25%, turnover largo ≤ 8%, alpha ≥ -2%.
  - 2 métricas informativas (Calmar 12m, MDD 12m rolling largo): `null` — no bloqueantes en v1.
  - `ramp_stage: paper` en YAML, validado por schema (enum: `paper`, `ramp_10`, `ramp_25`, `ramp_50`, `live_100`).
  - Protocolo ramp-up documentado en `POLICY.md` §14: 5 escalones con criterios de entrada, duración mínima y rollback.
  - Decisión registrada en `decisiones-tecnicas.md` (**ADR-041**).

### Pendiente principal

- **Acumular datos paper-live**: el gate KPI OOS requiere mínimo 312 días hábiles (~15 meses); hoy hay ~120 días históricos. El workflow diario está activo y acumulando.
- **Re-medir señal con universo ampliado (ADR-053)**: tras corregir mezcla de monedas (**ADR-052**), la cross-section quedó demasiado fina (~1 símbolo/día mediana); falta re-correr IC/hit rate con el universo expandido.
- **Activar `--enable-long-engine` en producción**: el largo está cableado, testeado y con guardrail efectivo, pero el flag está apagado por defecto en `run_paper_live.py` y en el workflow CI. Activar tras validar en paper que el snapshot final refleja ambos sleeves correctamente.
- **Bug IOL de mapeo de keys** (mitigado por fallback Byma; ver `docs/complicaciones-tecnicas.md` §3): pendiente de fix definitivo en el connector.
- Completar restantes del informe KPI respecto del spec (p. ej. cobertura de turnover por otros segmentos además de largo) — ver `docs/kpi_report_spec.v1.md`.
- Agregar observabilidad explícita para operación diaria del largo (`fills_long_count`, `long_risk_block_count`).
- Conectar fuentes de datos reales (feeds, APIs broker) con `PaperBrokerSim` como adaptador, manteniendo interfaces estables.


## Componentes clave y cómo se vinculan

- `calendar_store` + `corporate_actions`: definen dias y horarios válidos US/AR y ajustan posiciones por splits/dividendos para evitar rebalanceos "fantasma". En paper-live el YAML de calendario es **obligatorio**; sin él el orquestador aborta (**ADR-054**).
- `risk_guardrails`: módulo centralizado de decisiones de riesgo (fail-fast por calidad de datos, ventanas no-trade, límites diarios, kill switch); retorna `GuardrailResult`, no ejecuta.
  - `check_short_risk()`: validaciones del motor corto (data quality → no-trade window → kill switch → daily loss). `_check_risk_with_optional_db` en el runner corto es un orquestador liviano que reutiliza `check_short_risk` para los checks comunes e inyecta `check_and_persist_kill_switch` cuando hay DB.
  - `check_long_risk()`: guardrail diario -1.5% para motor largo. Recibe `long_daily_return` real desde `long_bucket` del snapshot (no default implícito 0.0).
  - `compute_atr()` + `check_stop_loss()`: stop loss por ticker individual usando ATR(14) con fallback a porcentaje.
  - `log_risk_cycle()`: JSON logging estructurado de decisiones de riesgo.
- `short_term_engine` + `short_term_day_runner`: motor diario de corto plazo (momentum + filtros de liquidez/volatilidad + top-K + sizing por riesgo); integra `risk_guardrails` en pipeline.
- `pending_order_queue`: cola para modo semi_auto, permite validación manual antes de ejecutar.
- `short_term_pre_gate`: walk-forward OOS automático del bloque corto antes de habilitar más capital.
- `long_term_engine` + `long_term_monthly_runner`: motor semanal del sleeve largo (pesos objetivo, bandas de drift, intents de rebalanceo); integra `check_long_risk()`.
- `validation/wf_windows` + `validation/wf_runner` + `validation/wf_long_report`: pipeline WF del bloque largo (ventanas rolling -> stage por ventana -> agregados globales + JSON). Series diarias del largo vía `return_details` en el stage; análisis comparativo semanal/mensual/SPY en `notebooks/wf_long_comparison.ipynb` (**ADR-046**).
- `reporting/kpi_v0` + `scripts/report_kpis.py`: informe JSON/Markdown según `docs/kpi_report_spec.v1.md` (lectura post-corrida del export equity + fills).
- `reporting/signal_ic` + `reporting/scenario` + `scripts/run_signal_ic_now.py` / `run_scenario.py`: medición de señal (IC, hit rate, quantile spread) y escenarios what-if sobre la misma capa determinística — sin tocar ejecución paper-live.
- `data/venue_policy.py`: contrato único de venue por market tag; evita mezcla USD/ARS en lectores de señal y pre-gate (**ADR-052**).
- `kpi_oos_gate` + `reporting/kpi_walk_forward` + `scripts/report_kpis_walk_forward.py`: varias ventanas OOS sobre la misma serie, mismo informe v3 por tramo, tabla maestra y gate reproducible opcional (**ADR-034**).
- `tests/fixtures/kpi_golden` + `tests/test_kpi_regression_golden.py`: regresión con dataset fijo de 60 días y JSON golden en CI (**ADR-035**).
- `scripts/run_paper_live.py`: orquestador diario paper-live de ambos sleeves — gap detection (F3 con calendario US∪AR), catch-up idempotente, **calendario obligatorio** (`load_required_calendar_store`, `--no-calendar` para tests), replay de ledger, persistencia de fills/snapshots (`short_cash` desde `ledger.short_cash`, **T1.3**). Flag `--enable-long-engine` (default off) activa ejecución short → long con **overlay de precios XBUE** para el universo del largo AR (**ADR-048**), fills combinados y MTM consistente con CEDEAR/pesos.
- `.github/workflows/paper_live_daily.yml`: cron + dispatch sobre branch `paper-live-data`; fetch OHLCV + pipeline + commit DB + issue on failure.
- `event_engine`: orquestador diario con soporte para `execution_mode` (auto/semi_auto) y bypass de stop loss en semi_auto.
- Flujo completo: Data -> Engines -> `risk_guardrails` -> Allocator -> `paper_broker_sim` -> `ledger`; ambos motores convergen en el mismo núcleo para mantener consistencia y auditoría.

## Contratos y documentación clave

- Plan maestro: `.cursor/plans/bot_trading_paper-first_155d6f04.plan.md`
- Política operativa: `POLICY.md`
- Overview técnico (defensa oral): `docs/project-overview.md`
- Complicaciones técnicas (guion): `docs/complicaciones-tecnicas.md`
- Spec informe KPI: `docs/kpi_report_spec.v1.md`
- Decisiones técnicas (ADR): `decisiones-tecnicas.md` (53 ADRs)
- Config parseable v1: `config/policy.v1.yaml`
- Schema de validación: `config/policy.v1.schema.json`

## Ejecutar validaciones

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
python -m pytest tests/ -v --cov=core_sim --cov-report=term-missing
python scripts/migrate_venue_ar_to_xbue.py --db data/market.db   # una vez si hay filas legacy venue=AR
python scripts/migrate_venue_us_to_xnys.py --db data/market.db   # una vez si hay filas legacy venue=US
python scripts/run_validation_wf.py --db data/market.db
python scripts/run_short_term_pre_gate.py --db data/market.db
python scripts/run_long_engine_wf.py --window-months 3 --step-months 1
# Notebook comparativo largo (ejecutar todas las celdas desde notebooks/):
#   notebooks/wf_long_comparison.ipynb  → pasos 3–4: orquestador + gráficos ADR-045/046
python scripts/report_kpis.py --equity path/to/equity.csv --trades path/to/fills.csv --benchmark-returns path/to/benchmark_returns.csv --out-json kpi.json --out-md kpi.md
python scripts/report_kpis_walk_forward.py --equity path/to/equity.csv --trades path/to/fills.csv --out-json wf_kpi_oos.json
# Si el CSV tiene pocos días y policy usa burn_in=252: --wf-burn-in 0 --wf-oos 60 --wf-step 60 (ajustar al largo real del CSV)
python scripts/regenerate_kpi_golden_fixtures.py   # solo tras cambio consciente del spec / KPIs (actualiza tests/fixtures/kpi_golden/)
python scripts/build_trading_days_yaml.py   # regenerar config/calendars/trading_days.v1.yaml (XNYS + XBUE)
python scripts/run_paper_live.py --date 2026-05-09 --db data/market.db   # paper-live (default 3M ARS)
python scripts/run_paper_live.py --date 2026-05-09 --db data/market.db --initial-cash 3000000 --currency ARS
python scripts/run_paper_live.py --date 2026-05-09 --db data/market.db --enable-long-engine   # short + long
python scripts/fetch_daily.py --lookback 120 --db data/market.db   # backfill OHLCV antes de recuperar gap largo
python scripts/diagnose_iol_auth.py   # validar credenciales IOL (no imprime secretos)
python scripts/run_signal_ic_now.py   # medición IC de señal sobre market.db
python scripts/run_scenario.py --override '{"momentum_lookback_days": 15}'   # escenario what-if (IC/señal)
python scripts/run_whatif_sim.py --start 2026-03-01 --end 2026-06-02 --currency ARS   # sim cartera 30/70 aislada
```

**Recuperación paper-live tras fallos de CI** (resumen; detalle en `docs/project-overview.md` §8 y **ADR-050**):

1. Configurar `IOL_USER` / `IOL_PASS` en GitHub Secrets.
2. `git checkout paper-live-data && git pull && git lfs pull`.
3. Si gap > 3 días de mercado (calendario US∪AR): `fetch_daily --lookback 120` y varias corridas de `run_paper_live --date <último día del bloque>` (≤3 por tanda) o `workflow_dispatch` equivalente.
4. `git push origin paper-live-data`.

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
