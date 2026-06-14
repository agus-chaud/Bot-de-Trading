# Decisiones técnicas (ADR)

Este documento registra las decisiones técnicas relevantes del proyecto, su contexto, el porqué, consecuencias y alternativas evaluadas.

**Última actualización**: 2026-06-13 — **59 ADRs** aceptadas (001–060). Las complicaciones técnicas vividas (encadenadas) se narran en `docs/complicaciones-tecnicas.md`; el overview de arquitectura y estado operativo está en `docs/project-overview.md`.

## Cómo usar este archivo

- Crear una nueva entrada por cada decisión importante.
- Mantener un estado claro: `propuesta`, `aceptada`, `reemplazada`, `descartada`.
- Cuando una decisión cambie, no borrar el historial: marcar la anterior como `reemplazada` y enlazar la nueva.
- Ante conflicto numérico entre `POLICY.md` y `config/policy.v1.yaml`, actualizar ambos en el mismo cambio y anotar el motivo aquí o en el ADR afectado.

## Índice por tema (59 ADRs)

| Tema | ADRs |
|------|------|
| Filosofía y arquitectura | 001–004, 014 |
| Riesgo y guardrails | 002, 005, 015, 020, 022, 026, 036, 041, 042, 044, 051 |
| Motores corto / largo | 011–013, 016–017, 042, 043, 045–048 |
| Data layer y calidad | 021, 037, 047, 049, 052, 053, 056 |
| Simulación y ledger | 008–010, 018–019, 025, 039, 051 |
| KPI, validación y gates | 027–035, 041 |
| Investigación walk-forward y perfil de riesgo | 058, 059, 060 |
| Paper-live y operación | 040, 044, 048, 050, 054, 055 |
| Señal y medición offline | 042, 052, 053 (+ `reporting/signal_ic.py`, `reporting/scenario.py`; ver ADR-052) |
| Connector AR / IOL | 049, 056 |
| Testing y convenciones | 057 |
| Tooling / fixes menores | 023–024, 038 |

---

## ADR-001 — Enfoque paper-first antes de live trading

- **Fecha**: 2026-04-15
- **Estado**: aceptada
- **Contexto**: El proyecto busca construir un bot de trading moderado, pero al inicio no existe infraestructura de ejecución real ni evidencias estadísticas suficientes para operar con capital real.
- **Decisión**: Priorizar simulación (`paper trading`) con datos reales y postergar la integración live hasta superar gates de validación definidos.
- **Por qué**:
  - Reduce riesgo operativo y financiero temprano.
  - Permite validar motores, riesgo y costos en condiciones realistas.
  - Evita acoplar lógica de estrategia con APIs de broker prematuramente.
- **Consecuencias**:
  - Se necesita un `paper_broker_sim` robusto con costos y slippage configurables.
  - El time-to-market para live aumenta, pero con menor probabilidad de errores críticos.
  - El foco inicial está en confiabilidad y observabilidad.
- **Alternativas consideradas**:
  - **Ir directo a live con capital pequeño**: descartada por riesgo técnico sin validación suficiente.
  - **Backtest offline sin paper runtime**: descartada porque no ejercita flujo operativo end-to-end.

---

## ADR-002 — Riesgo determinístico en código (no dependiente de LLM)

- **Fecha**: 2026-04-15
- **Estado**: aceptada
- **Contexto**: El sistema puede incorporar IA como copiloto, pero los límites de riesgo deben ser auditables y predecibles.
- **Decisión**: Implementar guardarraíles de riesgo y kill switch como reglas determinísticas en código, independientes de prompts o decisiones de LLM.
- **Por qué**:
  - Aumenta trazabilidad y auditabilidad.
  - Reduce comportamientos no deterministas en momentos críticos.
  - Facilita testing y cumplimiento de políticas.
- **Consecuencias**:
  - Mayor esfuerzo inicial en diseño de matriz de riesgo explícita.
  - Menor flexibilidad "ad hoc" en ejecución, pero mayor seguridad operacional.
- **Alternativas consideradas**:
  - **Delegar gestión de riesgo a recomendaciones de IA**: descartada por baja predictibilidad y dificultad de auditoría.
  - **Aplicar riesgo solo a nivel portfolio total**: descartada por necesidad de control por bucket (corto/largo).

---

## ADR-003 — Arquitectura desacoplada por motores + núcleo común

- **Fecha**: 2026-04-15
- **Estado**: aceptada
- **Contexto**: El sistema combina horizontes distintos (corto diario y largo mensual) con asignaciones objetivo 30/70 y 20/80.
- **Decisión**: Separar `short_term_engine` y `long_term_engine`, y centralizar control en un núcleo común (`risk_guardrails`, `allocator`, `paper_broker_sim`, `ledger`).
- **Por qué**:
  - Aísla la complejidad de cada horizonte temporal.
  - Evita duplicar lógica de riesgo, ejecución y métricas.
  - Facilita pruebas unitarias e integración incremental.
- **Consecuencias**:
  - Se requiere definir contratos claros de entrada/salida entre componentes.
  - Mayor disciplina de interfaces, menor acoplamiento accidental.
- **Alternativas consideradas**:
  - **Motor único con estrategias mezcladas**: descartada por complejidad y riesgo de regresiones cruzadas.
  - **Motores con ejecución propia**: descartada por duplicación de lógica crítica y mayor superficie de error.

---

## ADR-004 — Configuración de política versionada y validada por schema

- **Fecha**: 2026-04-15
- **Estado**: aceptada
- **Contexto**: Las reglas de negocio/riesgo deben mantenerse consistentes y verificables en CI.
- **Decisión**: Usar contrato parseable en `config/policy.v1.yaml` validado contra `config/policy.v1.schema.json`, manteniendo alineación con `POLICY.md`.
- **Por qué**:
  - Previene drift entre documentación y configuración ejecutable.
  - Permite validación automática estructural.
  - Hace explícitos cambios de política mediante versionado.
- **Consecuencias**:
  - Cambios numéricos exigen actualización coordinada de YAML y documentación.
  - Necesidad de tests de schema y disciplina de versionado.
- **Alternativas consideradas**:
  - **Solo documentación en Markdown**: descartada por falta de validación automática.
  - **Solo config sin documento explicativo**: descartada por menor claridad para revisión humana.

---

## ADR-005 — Kill switch del bucket corto por drawdown mensual

- **Fecha**: 2026-04-15
- **Estado**: aceptada
- **Contexto**: El módulo de corto plazo tiene mayor sensibilidad a régimen y puede degradarse rápido.
- **Decisión**: Congelar `short_term_engine` cuando su drawdown mensual sea menor o igual a `-8%`, según política configurada.
- **Por qué**:
  - Limita pérdidas en escenarios de deterioro del edge.
  - Mantiene operativo el módulo de largo plazo, evitando apagar todo el sistema.
  - Refuerza perfil moderado con control específico por bucket.
- **Consecuencias**:
  - Requiere cálculo confiable de drawdown mensual por subportfolio.
  - Puede reducir retorno potencial en recuperaciones rápidas, a cambio de acotar cola de riesgo.
- **Alternativas consideradas**:
  - **Kill switch global del portfolio completo**: descartada por ser demasiado agresiva para el objetivo 30/70.
  - **Sin kill switch, solo reducción gradual**: descartada por respuesta más lenta ante deterioro abrupto.

---

## ADR-006 — Event engine diario con pipeline determinístico

- **Fecha**: 2026-04-15
- **Estado**: aceptada
- **Contexto**: En Fase 2 se necesita un `core` de simulación que ejecute el ciclo diario de forma auditable y reproducible antes de integrar estrategias, costos y broker simulado en detalle.
- **Decisión**: Implementar un `DailyEventBacktester` con cola de eventos fija `MarketOpen -> SignalGenerated -> OrdersProposed -> RiskChecked -> OrdersFilled -> LedgerUpdated`, desacoplando cada etapa mediante handlers inyectables.
- **Por qué**:
  - Garantiza orden estable de ejecución para backtesting y paper runtime.
  - Mejora trazabilidad operativa al devolver traza completa de eventos/payloads por ciclo.
  - Permite evolucionar componentes (`engines`, `risk_guardrails`, `paper_broker_sim`, `ledger`) sin acoplarlos al orquestador.
- **Consecuencias**:
  - El pipeline queda explícito y testeable desde el inicio.
  - Cualquier cambio de orden o contrato entre etapas debe reflejarse en tests de integración.
  - Se habilita incorporación incremental de calendario, corporate actions y costos sobre una base estable.
- **Alternativas consideradas**:
  - **Backtester monolítico sin eventos explícitos**: descartada por baja auditabilidad y mayor acoplamiento entre estrategia, riesgo y ejecución.
  - **Flujo implícito dentro de cada motor**: descartada por duplicación de lógica de ciclo diario y mayor riesgo de divergencia entre corto y largo plazo.

---

## ADR-007 — Fuente única para calendario y corporate actions (v1)

- **Fecha**: 2026-04-15
- **Estado**: aceptada
- **Contexto**: La Fase 2 exige soporte mínimo de `splits/dividendos US` y definición explícita de sesiones US y días hábiles AR para evitar reglas de mercado implícitas y no auditables.
- **Decisión**: Centralizar la fuente de verdad en archivos versionados de `config/`: `config/calendars/trading_days.v1.yaml` y `config/corporate_actions/us_actions.v1.yaml`, y exponer esos datos en `MarketOpen` del `DailyEventBacktester` mediante stores inyectables.
- **Por qué**:
  - Mantiene el enfoque paper-first con decisiones determinísticas y reproducibles.
  - Evita drift entre configuración, tests y runtime del simulador.
  - Habilita evolución incremental a conectores reales sin cambiar contratos del core.
- **Consecuencias**:
  - El contrato `policy.v1.yaml` incorpora secciones `calendar` y `corporate_actions`.
  - El event engine ahora puede enriquecer eventos de apertura con estado de sesión y acciones corporativas aplicables.
  - Requiere mantener actualizados los archivos de calendario/acciones como artefactos de datos operativos.
- **Alternativas consideradas**:
  - **Calendario inferido dinámicamente desde librerías externas**: descartada en v1 por menor control y reproducibilidad.
  - **Corporate actions hardcodeadas en código**: descartada por baja mantenibilidad y peor trazabilidad.

---

## ADR-008 — Modelo de costos determinístico y configurable por mercado

- **Fecha**: 2026-04-15
- **Estado**: aceptada
- **Contexto**: La Fase 2 exige que el simulador aplique costos realistas por fill (`commission`, `slippage`, `spread`) con parámetros diferentes entre US y AR, manteniendo trazabilidad y reproducibilidad en paper trading.
- **Decisión**: Incorporar `core_sim.cost_model.CostModel` con configuración tipada por mercado (`MarketCostConfig`) y salida desglosada (`CostBreakdown`) para calcular costos de forma determinística. El slippage soporta modo fijo en bps y modo lineal por participación relativa al ADV.
- **Por qué**:
  - Alinea el core con el requisito de costos configurables del plan paper-first.
  - Hace auditable cada componente de costo (comisión/slippage/spread) en lugar de un costo agregado opaco.
  - Permite integrar `paper_broker_sim` y `ledger` sin cambiar el contrato del cálculo de costos.
- **Consecuencias**:
  - El contrato del core incorpora validaciones explícitas de inputs (`qty`, `price`, mercado conocido).
  - Si falta ADV en modo lineal, el modelo cae a slippage 0 de forma segura y determinística.
  - Queda habilitada una integración incremental con config YAML en la siguiente iteración.
- **Alternativas consideradas**:
  - **Costos hardcodeados en el handler de fills**: descartada por acoplamiento y baja testabilidad.
  - **Modelo no determinístico/heurístico (LLM)**: descartada por no cumplir los guardarraíles del proyecto.

---

## ADR-009 — Ledger determinístico con PnL separado y DD mensual del bucket corto

- **Fecha**: 2026-04-15
- **Estado**: aceptada
- **Contexto**: El plan paper-first requiere que la etapa `LedgerUpdated` reporte estado contable y métricas auditables para habilitar guardarraíles, especialmente el kill switch del módulo corto (`-8%` mensual).
- **Decisión**: Implementar `core_sim.ledger.PortfolioLedger` con actualización diaria determinística (`apply_fills` + `mark_to_market`), separación de `realized_pnl` vs `unrealized_pnl`, `equity_curve` y cálculo de drawdown mensual del bucket `short` con reset por mes calendario.
- **Por qué**:
  - Provee una única fuente de verdad para estado financiero del simulador.
  - Evita ambigüedades al fijar costo promedio ponderado en v1 para compras/ventas parciales.
  - Entrega métricas directamente consumibles por riesgo sin acoplar lógica de policy al ledger.
- **Consecuencias**:
  - Los fills deben incluir contrato mínimo (`symbol`, `side`, `qty`, `price`, `market`, `bucket`, `fee?`).
  - El ciclo diario falla explícitamente si falta `close` para posiciones abiertas.
  - El event engine puede testear snapshots ricos en `LedgerUpdated` sin cambiar su orquestación.
- **Alternativas consideradas**:
  - **Ledger implícito dentro de `event_engine`**: descartada por acoplamiento y baja testabilidad.
  - **Método FIFO en v1**: descartada por mayor complejidad inicial para esta fase; se prioriza simplicidad determinística con promedio ponderado.

---

## ADR-010 — Paper broker sim con interfaz estable para motores

- **Fecha**: 2026-04-15
- **Estado**: aceptada
- **Contexto**: El plan de Fase 2 exige un `paper_broker_sim` sin API real que exponga la misma interfaz base del broker futuro para evitar reescrituras en `short_term_engine` y `long_term_engine`.
- **Decisión**: Incorporar `core_sim.paper_broker_sim.PaperBrokerSim` con métodos `place_order` y `get_positions`, apoyado en `CostModel` (costos determinísticos por fill) y `PortfolioLedger` (estado de caja y posiciones).
- **Por qué**:
  - Define frontera clara entre motores (órdenes intent) y ejecución.
  - Mantiene compatibilidad hacia broker real con contrato de métodos estable.
  - Preserva auditabilidad al devolver `FillReport` con desglose de costos.
- **Consecuencias**:
  - Las órdenes deben incluir contrato mínimo (`symbol`, `side`, `qty`, `price`, `market`, `bucket`; `adv` opcional).
  - `place_order` ejecuta fill inmediato (v1) y aplica `fee` calculada por `CostModel`.
  - Se expone historial de fills para debugging y validación.
- **Alternativas consideradas**:
  - **Ejecutar órdenes directo desde engines sin adapter de broker**: descartada por acoplamiento y mayor costo de migración a live.
  - **Broker sim sin costos**: descartada por no cumplir realismo mínimo del paper trading.

---

## ADR-011 — Short-term engine v1 con contrato explícito y funciones puras

- **Fecha**: 2026-04-15
- **Estado**: aceptada
- **Contexto**: El bloque de `short_term_engine` estaba definido a nivel conceptual en el plan, pero sin contrato operativo detallado para señal, filtrado, sizing, trazabilidad y fallbacks de datos.
- **Decisión**: Formalizar v1 del motor corto con contrato explícito y helpers determinísticos en `core_sim.short_term_engine`: cálculo de candidatos (`compute_signal_candidates`), ranking por mercado (`rank_top_k_by_market`) y generación de `orders_intent` (`build_orders_intent`) con `skip_reasons` y métricas de ciclo.
- **Por qué**:
  - Evita ambigüedad de implementación entre equipos/roles (`Spec`, `Data`, `Engines`, `Risk`, `QA`).
  - Permite testeo unitario de reglas críticas sin acoplarse al runtime completo.
  - Refuerza auditabilidad: cada no-operación queda codificada por motivo explícito.
- **Consecuencias**:
  - El flujo del motor corto queda más predecible y mantenible, con interfaces claras.
  - Se exige disciplina de contratos para evitar drift entre policy, engine y tests.
  - Se habilita integración incremental con `event_engine` y `risk_guardrails` sin reescribir lógica.
- **Alternativas consideradas**:
  - **Implementación monolítica dentro del backtester**: descartada por acoplamiento alto y menor testabilidad.
  - **Heurística ad hoc sin contratos de salida**: descartada por baja auditabilidad y riesgo operativo.

---

## ADR-012 — Sincronización obligatoria POLICY/YAML/schema para parámetros del motor corto

- **Fecha**: 2026-04-15
- **Estado**: aceptada
- **Contexto**: Al introducir parámetros operativos de `short_term_engine` (`N`, `p_min`, `vol_max`, `K`, `risk_budget_trade_pct`), existe riesgo de divergencia entre documentación humana y configuración parseable usada por código/CI.
- **Decisión**: Incorporar bloque `short_term_engine` en `config/policy.v1.yaml`, volverlo obligatorio en `config/policy.v1.schema.json`, y documentar los mismos valores en `POLICY.md` con regla explícita de actualización en el mismo cambio.
- **Por qué**:
  - Mantiene coherencia entre política, runtime y validación automática.
  - Previene errores silenciosos por claves faltantes o tipos inválidos.
  - Facilita gobernanza de cambios de riesgo con evidencia verificable.
- **Consecuencias**:
  - Cualquier ajuste de parámetros del motor corto debe tocar tres artefactos coordinados.
  - Los tests de schema se vuelven guardrail central para CI.
  - Mayor costo de cambio inicial, menor probabilidad de drift en producción.
- **Alternativas consideradas**:
  - **Solo documentar en `POLICY.md`**: descartada por falta de validación automática.
  - **Solo YAML sin narrativa en policy**: descartada por menor claridad para revisión humana y auditoría funcional.

---

## ADR-013 — Pipeline diario corto integrado al `DailyEventBacktester`

- **Fecha**: 2026-04-21
- **Estado**: aceptada
- **Contexto**: El motor corto v1 existía como funciones puras (`short_term_engine`) pero faltaba el lazo **datos → señal → órdenes → riesgo → broker sim** con whitelist, calendario y kill switch alineados al plan.
- **Decisión**:
  - Añadir `core_sim.short_term_day_runner` con límites **agent-teams-lite** (Data / Engines / Risk / Core sim) documentados en el módulo.
  - Exponer `create_short_term_daily_backtester`, `create_short_term_pipeline_handlers`, `load_merged_whitelist` y `orders_intent_to_broker_orders`.
  - Extender `DailyEventBacktester.run_day(..., pipeline_context=...)` para inyectar `history_by_symbol` y contexto sin acoplar el core a un solo motor.
  - Hacer **idempotente** `PortfolioLedger.mark_to_market` respecto de `equity_curve_points` cuando se repite el mismo `trading_day`, permitiendo refrescar DD del bucket corto antes del `risk_check` y otra vez en `LedgerUpdated` sin duplicar puntos.
- **Por qué**:
  - El paper-first exige trazabilidad end-to-end; sin integración, el motor corto no es operable ni testeable como sistema.
  - El `pipeline_context` mantiene el event engine genérico y evita hardcodear historial en la firma mínima (`daily_bars` del día).
  - La idempotencia del ledger evita contaminar la curva de equity al llamar MTM más de una vez en el mismo día de simulación.
- **Consecuencias**:
  - Los handlers de señal/propuesta/riesgo deben tolerar kwargs extra (`market_open`, `history_by_symbol`, …) o usar `**kwargs`.
  - El percentil de volumen cross-sectional con un solo símbolo líquido se define como **1.0** (evita bloqueo artificial por `p_min`).
- **Alternativas consideradas**:
  - **Subclases de `DailyEventBacktester` por motor**: descartada por duplicación y menor reutilización.
  - **Sin idempotencia en MTM**: descartada por duplicar puntos en la curva al refrescar riesgo intradía.

---

## ADR-014 — Sistema de documentación: lector humano primero, memoria de agente en Engram

- **Fecha**: 2026-04-21
- **Estado**: aceptada
- **Contexto**: El repo combina normas operativas (`AGENTS.md`), decisiones con historial (`decisiones-tecnicas.md`), política ejecutable (`POLICY.md` + YAML + schema) y plan vivo en `.cursor/plans/`. Hacía falta explicitar **para quién** se escribe cada capa y cómo los agentes retienen convenciones entre sesiones.
- **Decisión**:
  - Priorizar la documentación para **el dueño del repo** (comprensión y retomada después de tiempo sin tocar el código).
  - Los agentes deben **persistir en Engram** decisiones, convenciones y preferencias relevantes (p. ej. vía `mem_save` cuando el MCP Engram esté disponible), sin sustituir ADRs ni fuentes de verdad versionadas en git.
  - Mantener un **mapa de retomada breve** en `AGENTS.md` (orden de lectura y enlaces); si la narrativa de “cómo retomar” crece, extraer el detalle a `docs/` y enlazar desde `AGENTS.md` en lugar de inflar el archivo de reglas.
- **Por qué**:
  - Separar “recordar el sistema” (humano + git) de “recordar el hilo de trabajo del agente” (Engram) reduce fricción y evita duplicar en markdown lo que ya vive en ADR o en policy versionada.
  - Un índice corto en `AGENTS.md` aprovecha el archivo que ya se abre por convención en sesiones con IA.
- **Consecuencias**:
  - Nuevas convenciones de documentación deben reflejarse aquí como ADR cuando cambien el modelo mental del proyecto (no como notas sueltas sin trazabilidad).
  - Si Engram no está conectado en una sesión, el agente no pierde la fuente de verdad: sigue siendo este repo y `AGENTS.md`.
- **Alternativas consideradas**:
  - **Solo Engram, sin ADR para convenciones de docs**: descartada porque la memoria externa no reemplaza historial versionado ni revisión en PR.
  - **Un solo documento largo mezclando ADR y guía humana**: descartada por dificultad de mantenimiento y de saber qué es norma vs historia.

---

## ADR-015 — Riesgo extendido + allocator 30/70·20/80 en pipeline corto; pruebas por comportamiento

- **Fecha**: 2026-04-21
- **Estado**: aceptada
- **Contexto**: El plan paper-first exige matriz de riesgo determinística y reparto 30/70 (corto/largo) y 20/80 (AR/US) antes de escalar a más motores. El pipeline corto ya existía con kill switch y whitelist, pero faltaban límites diarios, ventanas no-trade, calidad de datos explícita y allocator en el lazo de órdenes.
- **Decisión**:
  - **Ledger**: exponer `short_bucket.daily_return` como variación del MV del bucket corto respecto del último EOD con **fecha de trading anterior** (mapa `date → short MV`), de modo que varias MTM el mismo día no corrompen el ratio.
  - **Runner** (`short_term_day_runner`): en `propose_orders` y `risk_check`, aplicar (en orden de severidad operativa) `halt_on_data_quality` + `risk_flags`, ventanas no-trade US (390 min; `session_minutes_from_open` opcional en `pipeline_context`), pérdida diaria corta vs `max_daily_loss_short_pct`, kill switch mensual, y defensa por whitelist/mercado.
  - **Calidad de datos**: `not_in_daily_bars` para símbolos fuera del `daily_bars` del día **no** dispara halt (universo parcial); sí halt con `daily_bars` vacío o skips estructurales (close/volumen/histórico inválido, etc.).
  - **Allocator**: `short_tranche_headroom = max(0, weights.short * equity − MV corto)` y `geo_headroom[M] = max(0, geo[M] * equity − MV total en M)` inyectados en `build_orders_intent` junto con cash y caps de riesgo por ticker/sector.
  - **Pruebas (smart-testing)**: tests de integración con `PortfolioLedger` + `PaperBrokerSim` reales; nombres orientados a comportamiento; CI con `pytest-cov` sobre `core_sim` y umbral mínimo de cobertura.
- **Por qué**:
  - El riesgo tiene que fallar **antes** de ejecutar, con la misma fuente de verdad que el informe (`ledger` + policy).
  - El allocator sobre totales respeta el plan hasta que exista `long_term_engine` (el headroom AR/US ya considera posiciones long cuando existan).
  - Cobertura en CI como red de seguridad sin sustituir tests mal diseñados (la confianza sigue en reglas y escenarios).
- **Consecuencias**:
  - Quien integre intradía debe pasar `session_minutes_from_open`; sin él, el backtest diario EOD no aplica no-trade (comportamiento explícito).
  - Cualquier cambio de semántica de `halt_on_data_quality` debe coordinarse con tests en `test_short_term_day_runner.py`.
- **Alternativas consideradas**:
  - **Exigir barra diaria para toda la whitelist**: descartada — rompe feeds parciales y no distingue “falta de universo” de “dato corrupto”.
  - **Allocator solo dentro del 30% corto sin mirar geo global**: descartada por desalineación con `POLICY.md` / plan (20/80 sobre total).

---

## ADR-017 — `long_term_engine` v1 (policy + funciones puras + contrato de intents)

- **Fecha**: 2026-04-21
- **Estado**: aceptada
- **Contexto**: El plan paper-first define el sleeve largo (70 % global) con core pasivo US, satélite acotado, rebalanceo mensual con bandas y salida `orders_intent`, pero faltaba contrato ejecutable alineado a `POLICY.md` + YAML + CI.
- **Decisión**:
  - Añadir sección **§10** en `POLICY.md` y bloque obligatorio `long_term_engine` en `config/policy.v1.yaml` validado por `policy.v1.schema.json` (core 2–3 líneas, satélite con topes, `drift_convention: per_line`, `rebalance_rule` inequívoca).
  - Implementar `core_sim.long_term_engine` con funciones puras: `target_weights`, `current_weights_mtm`, `drift_per_line_pp`, `should_rebalance_long`, `is_first_us_trading_day_of_month`, `build_long_term_orders_intent` y `long_term_engine_config_from_policy_dict`.
  - **Día de rebalance**: primer día de sesión US del mes calendario (entrada explícita `us_sessions` desde `TradingCalendarStore` u orquestador).
  - **Drift**: por línea; disparo si algún `drift_pp` **>** `drift_rebalance_threshold_pp` en día de rebalance.
  - **Datos**: precio faltante o no válido para cualquier símbolo del universo en día de rebalance → **abortar ciclo completo** (`missing_or_invalid_price_abort_cycle`), sin rebalanceo parcial.
  - **Corporate actions**: el engine **no** aplica splits; asume `positions_qty` ya ajustadas antes del cómputo MTM (coherente con Fase 2).
  - **Turnover opcional**: `max_long_rebalance_turnover_pct` acota `sum(|Δw|)` escalando proporcionalmente los deltas de peso antes de generar órdenes.
  - **QA (smart-testing)**: tests de comportamiento en `tests/test_long_term_engine.py` (banda, día no rebalance, abort por precio, post-split estable, tope de turnover, whitelist).
- **Por qué**:
  - Mantiene el motor **determinístico y auditable**, desacoplado del 30/70 y 20/80 (allocator), y coherente con agent-teams-lite (Spec vs Engines vs Data).
  - La convención “abortar ciclo” evita estados parcialmente rebalanceados cuando falta un precio crítico.
- **Consecuencias**:
  - Falta integrar el **runner mensual** en `DailyEventBacktester` / pipeline (tarea Fase C del plan SDD `engine-long-v1`); este ADR cubre Fase A–B + QA base.
  - Cualquier cambio de pesos o topes exige commit coordinado `POLICY.md` + YAML + schema.
- **Alternativas consideradas**:
  - **Rebalance parcial si falta un ticker**: descartada por riesgo de cartera inconsistente vs objetivos.
  - **Drift agregado solo sobre el bloque core**: descartada en v1 para evitar doble conteo y ambigüedad con satélite; se deja `per_line` como única convención schema.

- **Nota (2026-05-15)**: el diseño original describe largo **solo US** y día de rebalance con sesiones US; la extensión **BYMA en pesos**, reglas `first_ar_*`, `satellite_markets: [AR]` y resolución whitelist CEDEAR frente al merge global quedan registradas en **ADR-048**.

---

## ADR-016 — Pre-gate walk-forward del bloque corto (costos, turnover, DD mensual)

- **Fecha**: 2026-04-21
- **Estado**: aceptada
- **Contexto**: El plan Fase 3 exige validación mínima pre-gate con walk-forward, costos, turnover y DD mensual del corto, y rechazo automático según política antes de considerar capital real.
- **Decisión**:
  - Añadir `short_term_pre_gate` opcional en `config/policy.v1.yaml` (validado por schema) con `walk_forward.{oos_trading_days,step_trading_days,min_oos_windows}` y `thresholds.{monthly_short_drawdown_floor|null,max_fee_pct_of_initial_per_window,max_turnover_annualized}`.
  - Implementar `core_sim.short_term_pre_gate.run_short_term_pre_gate`: ventanas OOS independientes (cada una con ledger+broker nuevos), mismo `create_short_term_daily_backtester`, historial solo de días anteriores por símbolo.
  - Exponer `scripts/run_short_term_pre_gate.py` para CI local / demo sintética; criterio de fallo si **alguna** ventana viola umbrales.
- **Por qué**:
  - Cumple el entregable sin mezclar tuning de hiperparámetros (motor v1 fijo): el “walk-forward” aquí es **evaluación OOS consecutiva** sobre la misma política.
  - Reutiliza el pipeline paper para que costos y riesgo sean los mismos que en operación simulada.
- **Consecuencias**:
  - Hace falta un calendario de trading con suficientes días para `burn_in` (lookback + margen) + ventanas; si no, falla con `insufficient_oos_windows`.
  - El proxy de turnover es deliberadamente simple; si se exige otro definición, hay que versionar policy y código juntos.
- **Alternativas consideradas**:
  - **Notebook único sin API en código**: descartada por poca repetibilidad en CI.
  - **Una sola ventana hold-out**: descartada por no cumplir la intención de varias OOS del plan.

---

## ADR-018 — Framing de negocio y secuencia de despliegue (paper -> real)

- **Fecha**: 2026-04-24
- **Estado**: aceptada
- **Contexto**: El proyecto ya tenía arquitectura y controles técnicos bien definidos, pero faltaba fijar explícitamente el problema de negocio en la documentación operativa: no hay tiempo para trading manual consistente, y operar discrecionalmente bajo presión introduce sesgo y errores de ejecución.
- **Decisión**:
  - Establecer como objetivo de negocio un sistema de decisión **repetible, auditable y medible**, no señales ad-hoc.
  - Mantener como secuencia obligatoria: **paper trading con datos reales -> pre-gate/gates de riesgo y performance -> ramp-up gradual a capital real**.
  - Documentar este framing en `README.md` y `AGENTS.md` para alinear trabajo humano y de agentes con la misma tesis operativa.
- **Por qué**:
  - Sin marco de negocio explícito, el equipo puede optimizar código sin responder al problema real (disciplina de ejecución con tiempo limitado).
  - La transición por gates reduce riesgo de sobreajuste y de salto prematuro a dinero real.
- **Consecuencias**:
  - Cambios futuros deben justificar cómo mejoran la robustez del proceso, no solo métricas aisladas de backtest.
  - La discusión de “cuándo pasar a real” queda subordinada a criterios ex-ante y evidencia reproducible.
- **Alternativas consideradas**:
  - **Narrativa implícita solo en chats/notas sueltas**: descartada por pérdida de contexto entre sesiones.
  - **Ir a real apenas el paper da positivo en corto plazo**: descartada por fragilidad estadística y mayor riesgo operacional.

---

## ADR-020 — Stop Loss ATR vs Porcentaje Fijo

- **Fecha**: 2026-04-24
- **Estado**: aceptada
- **Contexto**: El motor corto operaba con protecciones a nivel de drawdown mensual y límite diario, pero faltaba mecanismo de stop loss por posición individual (per-ticker) para salidas rápidas en movimientos adversos. Paper trading con barras diarias requiere aproximación robusta a volatilidad.
- **Decisión**:
  - Implementar stop loss **individual por ticker** en motor corto usando **ATR(14)** con fallback a porcentaje fijo.
  - El multiplicador ATR (ej. 1.5x) y porcentaje fallback (ej. -5% US / -8% AR) viven en `config/policy.v1.yaml` bajo `risk.stop_loss`.
  - Stop loss es un **guardrail especial** en `risk_guardrails.check_stop_loss()`: retorna `GuardrailDecision` indicando si la posición debe cerrarse.
  - En modo **auto**, la orden de stop loss **bypasea otros guardrails** (ventana no-trade, kill switch) — siempre sale; no entra.
  - En modo **semi_auto**, la orden de stop loss se ejecuta directamente sin pasar a `PendingOrderQueue`.
- **Por qué**:
  - ATR captura volatilidad local y se adapta dinámicamente, mejor que porcentaje fijo para múltiples régimenes de mercado.
  - El stop por ticker es **quirúrgico**: cierramos solo la posición problemática, no el bucket completo ni el portfolio.
  - Bypass de otros guardrails refleja realidad operativa: un stop loss debe ejecutarse aunque esté fuera de ventana no-trade (protección > horario).
- **Consecuencias**:
  - Requiere **15+ barras previas** para calcular ATR; si faltan, se cae a porcentaje fallback sin error fatal.
  - Las órdenes de stop loss **no respetan ventana no-trade**, lo cual está auditado y es intencional.
  - En semi_auto, el operador ve la recomendación de stop pero NO puede rechazarla vía `PendingOrderQueue` — se ejecuta directa.
  - Caída del ATR a fallback queda logeada en JSON estructurado para auditoría.
- **Alternativas consideradas**:
  - **Porcentaje fijo para todos (-5% US/-8% AR)**: simple pero insensible a volatilidad creciente; rechazada.
  - **Stop por bucket completo**: menos precisa, impacta posiciones sanas; rechazada.
  - **Stop por low intradiario**: requiere datos intradía; rechazada en v1 paper-first con barras diarias.
  - **Integrar stop loss dentro del risk_guardrails.check_short_risk()**: descartada para separar concerns (guardrail fail-fast vs protección por posición).

---

## ADR-021 — Data layer modular con conectores reales + normalización robusta

- **Fecha**: 2026-04-27
- **Estado**: aceptada
- **Contexto**: El paper-first exigía datos reales (no sintéticos), pero el pipeline de ingestión no existía. Se necesitaba separar schema → storage → conectores → normalización sin acoplamiento, permitiendo evolucionar cada capa sin reescribir las otras.
- **Decisión**:
  - Implementar `data/` con 7 capas desacopladas:
    1. **schema.py** — `OHLCVRow` y `CorporateActionRow` como frozen dataclasses (contrato único)
    2. **storage.py** — `MarketDB` con SQLite local + sync Supabase lazy-init (sin crash si faltan credenciales)
    3. **calendar_builder.py** — `build_calendar()` via `pandas_market_calendars` (NYSE/XNYS, XBUE)
    4. **connectors/us_connector.py** — YFinance + retry exponencial (1s/2s/4s), distinción NetworkError/DataError
    5. **connectors/ar_connector.py** — IOL REST API (no existe `iol-client` en PyPI) + fallback Byma
    6. **normalizer.py** — outlier detection (rolling 5d median), forward-fill ≤3 días, `imputed=True`
    7. **fetcher.py** — orquestador isolando errores por símbolo
  - Conectores retornan `list[OHLCVRow] | None`, **nunca lanzan** al caller. El engine no se puede romper por un dato faltante.
  - Storage idempotente (upsert); repetir fetch mismo símbolo no genera duplicados.
  - Normalización excluye outliers del gap-fill (comportamiento quirúrgico).
- **Por qué**:
  - Sin datos confiables, todo lo demás es teatro.
  - Separación por capas permite testear cada una independientemente (mocks en conectores, SQLite `:memory:` en storage).
  - La distinción NetworkError/DataError en conectores clarifica reintentos (red: reintentar; datos: fallar rápido).
- **Consecuencias**:
  - Cualquier nueva fuente de datos (Bloomberg, otro broker) se agrega en `connectors/` sin tocar las otras capas.
  - Si `iol-client` existiera en PyPI mañana, se reemplaza la implementación HTTP sin cambiar interfaz pública.
  - El normalizer es opinado: outliers se excluyen, no se "corrigen". Si se necesita otra semántica, versionar policy + normalizer juntos.
- **Alternativas consideradas**:
  - **Archivos CSV estáticos**: rechazada por no permitir evolucionar a fuentes reales sin reescribir.
  - **Todo en SQL sin abstracción de conectores**: rechazada por acoplamiento y dificultad de testeo.

---

## ADR-022 — Kill switch persistente con reset manual categorizado

- **Fecha**: 2026-04-27
- **Estado**: aceptada
- **Contexto**: El guardrail de DD mensual -8% existía, pero era stateless: si el equity se recuperaba intradía, se desbloqueaba solo. Faltaba capacidad de auditoría del bloqueo y control manual sobre reseteo.
- **Decisión**:
  - Persistir **estado del kill switch en SQLite** bajo `kill_switch_log` (no en memoria, no en YAML).
  - Bloqueo hasta **reset manual explícito** con `--category` obligatorio (volatility_spike | data_error | strategy_review | other) + `--reason` libre.
  - **Auto-reset único** al inicio de cada mes nuevo, con log de trazabilidad.
  - Posiciones abiertas se **mantienen** cuando el kill switch dispara (no liquidar automáticamente).
  - **Notificación dual**: log JSON nivel ERROR + archivo en `alerts/kill_switch_YYYY-MM-DD.json`.
  - `scripts/reset_kill_switch.py` CLI para reset manual con validación de categoría.
  - `check_and_persist_kill_switch()` reemplaza check stateless; auto-reset por mes nuevo comparando tuples `(year, month)`.
- **Por qué**:
  - El -8% no es ruido. Si dispara, algo salió mal ese mes — recuperaciones intradía pueden ser falsas señales.
  - Reset manual **categorizado** crea evidencia auditable: "revisé y fue volatility_spike puntual" deja rastro.
  - El auto-reset mensual es lógica limpia: cada mes es una hoja en blanco.
  - Mantener posiciones vivas es precisión quirúrgica: solo bloqueamos *entradas nuevas*, permitiendo que posiciones existentes se recuperen o cerrar manualmente.
- **Consecuencias**:
  - Si el proceso se cae con kill switch activo y se reinicia, **sigue bloqueado** (safety by default). El operador debe resetear explícitamente.
  - El reset script abre su propia conexión SQLite — post-reset, hay que instanciar nuevo `MarketDB(db_path)` para verificar estado.
  - Auto-reset genera evento en DB; no es silencioso (auditable).
- **Alternativas consideradas**:
  - **Auto-unlock si DD > -8%**: rechazada por riesgo de oscilaciones intradía.
  - **Reset solo manual, sin auto-reset mensual**: rechazada porque fuerza overhead operativo innecesario en mes nuevo.
  - **Liquidar todo al activar**: rechazada por ser sobreactuar; precisión > potencia.

---

## ADR-023 — Bug fix: stop-loss side lowercase en `short_term_day_runner`

- **Fecha**: 2026-04-28
- **Estado**: aceptada
- **Contexto**: Al correr el validation-wf por primera vez con datos reales, el pre-gate crasheó con `ValueError: side must be BUY or SELL`. El error no era reproducible con datos sintéticos y solo emergió en el flujo end-to-end con el broker sim real.
- **Decisión**: Corregir `"side": "sell"` → `"side": "SELL"` en el path de generación de la orden de stop-loss dentro de `core_sim/short_term_day_runner.py`. Agregar test de regresión `test_stop_loss_order_side_is_uppercase_sell` para fijar el contrato.
- **Por qué**:
  - El contrato del sistema establece que `side` siempre es uppercase (`{"BUY", "SELL"}`); `PaperBrokerSim._validate_order` lo valida y rechaza cualquier variante.
  - `build_orders_intent()` en `short_term_engine.py` ya lo respetaba. Solo el path de stop-loss en el runner estaba roto.
  - El bug era silencioso en tests con datos sintéticos y solo explotó al integrar datos reales — justifica agregar el test de regresión para que CI lo cubra en adelante.
- **Consecuencias**:
  - El test `test_stop_loss_order_side_is_uppercase_sell` actúa como guardrail permanente en CI.
  - Cualquier futura orden generada por código (no por el engine) debe respetar el mismo contrato uppercase; documentarlo aquí sirve de referencia para revisiones de PR.
- **Alternativas consideradas**:
  - **Normalizar a uppercase en `_validate_order` en vez de exigirlo en el caller**: descartada — el contrato debe cumplirse en origen; normalizar silenciosamente en el validador oculta bugs upstream.
  - **Agregar cast en `PaperBrokerSim` solo para stop-loss**: descartada por mismo motivo que la anterior.

---

## ADR-024 — Encoding fix: emojis en Windows cp1252 en scripts de consola

- **Fecha**: 2026-04-28
- **Estado**: aceptada
- **Contexto**: `scripts/run_validation_wf.py` usaba emojis (`✅ ❌ ⏭`) en el output de consola. En Windows, `sys.stdout` usa cp1252 por defecto, lo que lanzaba `UnicodeEncodeError` al intentar imprimir caracteres fuera de ese rango.
- **Decisión**: Agregar al inicio de cualquier script de consola que use caracteres no-ASCII:
  ```python
  if hasattr(sys.stdout, "reconfigure"):
      sys.stdout.reconfigure(encoding="utf-8", errors="replace")
  ```
  La guarda `hasattr` mantiene compatibilidad con entornos que no exponen `reconfigure` (ej. pipes, CI sin TTY).
- **Por qué**:
  - En Windows, `sys.stdout` usa la codepage del sistema (cp1252 por defecto); cualquier carácter fuera de ese rango falla con `UnicodeEncodeError`.
  - Forzar UTF-8 explícitamente al inicio del script es la solución mínima y no invasiva: no toca los emojis ni el resto del código.
  - `errors="replace"` garantiza que, si el reencoding falla en algún entorno exótico, el script no crashea sino que sustituye el carácter problemático.
- **Consecuencias**:
  - Todo script nuevo en `scripts/` que imprima caracteres no-ASCII debe incluir este bloque al inicio — se convierte en convención del proyecto.
  - En CI Linux/Mac con UTF-8 por defecto, el bloque es no-op (no tiene efectos negativos).
- **Alternativas consideradas**:
  - **Eliminar emojis y usar solo ASCII**: descartada — degrada legibilidad del output sin necesidad técnica real.
  - **Configurar `PYTHONIOENCODING=utf-8` a nivel de entorno**: descartada como única solución — no garantiza que todos los entornos donde corra el script tengan esa variable, y pone la responsabilidad fuera del código.
  - **`io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")`**: descartada por ser más verbosa y menos idiomática que `reconfigure`.

---

## ADR-025 — Bug fix: drawdown mensual del bucket corto excluía cash realizado

- **Fecha**: 2026-04-28
- **Estado**: reemplazada → ver ADR-036
- **Contexto**: `_update_short_drawdown` en `ledger.py` calculaba el drawdown sobre `market_value` de posiciones abiertas únicamente. Al ejecutar un stop-loss que cerraba la última posición del bucket corto, el cash obtenido volvía a `self.cash` global pero era invisible para la métrica. Resultado: `drawdown = (0 / peak) - 1 = -1.0` aunque la pérdida real era ~0.11%. El error se detectó cuando el validation-wf corrió con datos reales por primera vez — la ventana 2 mostraba -100% espurio.
- **Decisión**: Introducir `self.short_cash` y `self._short_month_start_cash` en `PortfolioLedger`. Cuando `short_equity == 0` y el mes tuvo actividad, calcular `effective_value = peak + (short_cash - month_start_cash)` para reflejar el PnL neto realizado en lugar de asumir valor cero.
- **Por qué**:
  - El ledger necesita distinguir entre "no hay posiciones abiertas porque no se operó" y "no hay posiciones abiertas porque se liquidaron". En el primer caso el drawdown es 0; en el segundo debe reflejar el resultado neto del cash movido.
  - El atributo `short_cash` es la forma más directa de rastrear el cash específico del bucket sin cambiar la firma de los callers.
- **Consecuencias**:
  - `PortfolioLedger` tiene estado adicional (`short_cash`, `_short_month_start_cash`) que debe resetearse correctamente al inicio de cada mes.
  - Tests nuevos en `test_ledger.py` cubren el escenario de liquidación completa del bucket con pérdida parcial.
- **Alternativas consideradas**:
  - **Pasar `short_cash` como parámetro a `_update_short_drawdown`**: descartada — requería cambiar la firma en múltiples callers y rompía encapsulamiento del ledger.
  - **Usar el valor inicial del mes como referencia fija**: descartada — no captura el peak real del período; subestima drawdowns en meses con picos intermedios.
- **Archivos**: `core_sim/ledger.py`, `tests/test_ledger.py`

---

## ADR-026 — Decisión: separar floor del pre-gate del threshold del kill switch

- **Fecha**: 2026-04-28
- **Estado**: aceptada
- **Contexto**: `monthly_short_drawdown_floor: null` en `config/policy.v1.yaml` hacía que el pre-gate walk-forward usara el kill switch operativo (`short_kill_switch_monthly_dd: -0.08`) como criterio de validación histórica. Esto es conceptualmente incorrecto: el kill switch es un freno de emergencia para producción; el floor del pre-gate es una auditoría estadística de la estrategia. En un período bajista genuino (SPY -6.3%, Feb–Abr 2026, crash aranceles) la estrategia momentum long puede generar -25% en el bucket short — comportamiento esperado del sistema que el kill switch hubiera detenido, pero que no implica que la estrategia esté rota.
- **Decisión**: Fijar `monthly_short_drawdown_floor: -0.25`, independiente de `short_kill_switch_monthly_dd: -0.08`.
- **Por qué**:
  - El kill switch protege capital en producción con criterio conservador (-8%).
  - El floor del pre-gate valida que la estrategia no sea catastrófica en backtesting (-25%).
  - Mezclar ambos conceptos hace el pre-gate imposible de pasar en cualquier período bajista, aunque el sistema se hubiera protegido correctamente activando el kill switch.
  - El valor -0.25 equilibra permisividad para períodos bajistas genuinos y rechazo de estrategias con drawdowns realmente destructivos.
- **Consecuencias**:
  - El pre-gate puede ahora aprobar ventanas bajistas donde el kill switch habría actuado — esto es correcto: la validación histórica y la protección en vivo son capas independientes.
  - Futuros ajustes a cualquiera de los dos thresholds deben hacerse con conciencia explícita de que son parámetros distintos con propósitos distintos.
- **Alternativas consideradas**:
  - **Mantener `null` (fallback al kill switch)**: descartada — confunde responsabilidades y hace el pre-gate prácticamente inútil en mercados bajistas.
  - **Deshabilitar el pre-gate en períodos bajistas conocidos**: descartada — introduce lógica ad-hoc que rompe la reproductibilidad del workflow.
- **Archivos**: `config/policy.v1.yaml`, `config/policy.v1.schema.json`

---

## ADR-027 — Walk-forward del motor largo con agregación sobre ventanas válidas

- **Fecha**: 2026-04-28
- **Estado**: aceptada
- **Contexto**: El `long_engine` ya tenía stage de validación por período (`run_long_engine_stage`), pero faltaba cerrar el flujo completo del plan para T4-T6: ejecutar varias ventanas rolling, consolidar métricas globales y emitir un JSON consumible en `validation_reports/`.
- **Decisión**:
  - Implementar `validation/wf_runner.py` para iterar ventanas de `validation/wf_windows.generate_wf_windows(...)` y ejecutar una corrida independiente del stage largo por cada ventana.
  - Implementar `validation/wf_long_report.py` para:
    - construir `per_window` con métricas homogéneas del stage (`max_drift_observed_pp`, `total_rebalance_cost`, `monthly_drawdown_long`, `rebalances_executed`);
    - calcular summary global con reglas explícitas:
      - `worst_monthly_drawdown_long`: mínimo entre ventanas válidas;
      - `avg_rebalance_cost`: promedio entre ventanas válidas;
      - `total_rebalances_executed`: suma de ventanas válidas;
      - `max_drift_observed_pp`: máximo + localización (`max_drift_window_index`, `period_start`, `period_end`);
    - listar `windows_skipped` con motivo (`empty_window`, `stage_skipped`, `incomplete_metrics`).
  - Definir “ventana válida” como: no `skipped`, no vacía y con métricas completas (sin `None`).
  - Exponer `scripts/run_long_engine_wf.py` para generar `validation_reports/long_engine_wf_YYYY-MM-DD_HH-MM.json`.
- **Por qué**:
  - Mantiene coherencia con el principio paper-first: el reporte global se calcula sobre corridas efectivamente ejecutadas, sin mezclar faltantes con ceros.
  - Evita ambigüedad semántica entre “sin dato” y “resultado numérico”.
  - Hace trazable dónde ocurrió el peor drift, útil para debugging y auditoría.
- **Consecuencias**:
  - Los agregados no representan “todas las ventanas generadas” sino “ventanas usables”; por eso el reporte incluye `windows_total` y `windows_used_in_aggregates`.
  - Si todas las ventanas quedan excluidas, los agregados numéricos salen `null` y `total_rebalances_executed=0` (comportamiento explícito).
  - Se preserva separación de responsabilidades: `run_long_engine_stage` no conoce reglas de reporte global.
- **Alternativas consideradas**:
  - **Incluir ventanas skipped como cero en agregados**: descartada por sesgo estadístico y pérdida de interpretabilidad.
  - **Recalcular métricas globales directamente desde barras concatenadas**: descartada en v1 por solape de ventanas y riesgo de doble conteo; se prioriza agregación por ventana.
  - **Archivos**: `validation/wf_windows.py`, `validation/wf_runner.py`, `validation/wf_long_report.py`, `scripts/run_long_engine_wf.py`, `tests/test_wf_windows.py`, `tests/test_wf_runner.py`, `tests/test_wf_long_report.py`

---

## ADR-028 — Spec de informe KPI `rpt_kpi.v1`

- **Fecha**: 2026-05-05
- **Estado**: aceptada
- **Contexto**: Falta contrato único para cómputo de métricas del informe automático (Sharpe, turnover, drift, alpha, etc.), con riesgo de comparar corridas con definiciones distintas.
- **Decisión**: La fuente de verdad de fórmulas y contratos de export es **`docs/kpi_report_spec.v1.md`** (`spec_id: rpt_kpi.v1`). Resumen y gobernanza en **`POLICY.md` §12** (incluye tabla de decisiones técnicas).
- **Por qué**: Centralizar definiciones evita p-hacking informal y hace CI/golden tests posibles; política humana remite al spec sin duplicar ecuaciones.
- **Consecuencias**: Cambiar una definición KPI implica nueva versión del spec + registro; umbrales de gate siguen en anexo fechado aparte (Fase 5 del plan).
- **Referencias**: `docs/kpi_report_spec.v1.md`, `POLICY.md` §12, `.cursor/plans/bot_trading_paper-first_155d6f04.plan.md` (Fase 5).

---

## ADR-029 — Benchmark mixto 20/80 estático y retornos alineados sin lookahead

- **Fecha**: 2026-05-05
- **Estado**: aceptada
- **Contexto**: El plan Fase 5 (`rpt_kpi.v1` §12) exige un benchmark **congelado** antes del run (mismos pesos geo 20/80) y retornos comparables con la curva del backtest **sin mirar precios futuros** al valorar cada fecha.
- **Decisión**:
  - Tabla versionada en **`config/benchmark_mix_20_80.v1.yaml`**: proxies públicos en **USD** — **ARGT** (exposición AR listada US) 20 % y **SPY** 80 %; pesos suman 1,0.
  - Lógica en **`data/benchmark_returns.py`**: carga YAML/CSV; **`asof_close`** = último cierre con `fecha_barra ≤ fecha_valuación`; **`align_benchmark_simple_returns`** construye retornos simples entre pares consecutivos de fechas del backtest ponderando pesos; **`filter_inner_join_returns`** deja solo tramos completos (coherente con inner join del spec).
  - **`fetch_benchmark_into_db`**: reutiliza **`fetch_and_store`** por `venue` (`XNYS`/`XBUE`).
- **Por qué**: El alpha solo tiene sentido si la vara se fija *antes* de mirar el equity del bot; el merge “asof” evita leakage de barras posteriores al punto de valoración. USD unifica con `reporting_ccy` del informe.
- **Consecuencias**: Cambiar proxies o pesos implica **nuevo archivo/versionado** y mención en metadata del run; proxies alternativos (BYMA + FX) son posibles pero exigen documentar FX explícitamente.
- **Alternativas consideradas**:
  - **Forward-fill de retornos sobre calendario denso**: descartada donde el spec prohíba ffill para KPI finales; el enfoque PIT + inner join es explícito y auditable.
  - **Sin tabla estática (optimizar benchmark al ver resultados)**: descartada por invalidar la interpretación de alpha.
- **Referencias**: `docs/kpi_report_spec.v1.md` §12, `data/benchmark_returns.py`, `tests/test_benchmark_returns.py`.

---

## ADR-030 — Informe KPI v0 (`scripts/report_kpis`)

- **Fecha**: 2026-05-05
- **Estado**: aceptada
- **Contexto**: Tras congelar **`rpt_kpi.v1`** (ADR-028), faltaba una implementación mínima reproducible para “smoke” de pipeline: leer export de equity y costos por motor sin redefinir métricas en cada script suelto.
- **Decisión**:
  - Módulo **`reporting/kpi_v0.py`**: carga CSV de equity (columnas mínimas §2.1), ordena por `ts`; **retorno neto anualizado** total según §5 \((E_T/E_0)^{252/N}-1\); **max drawdown** total según §7; **costos por motor** vía **`costs_day_short`/`costs_day_long`** en equity **o** CSV de trades con **`motor`/`bucket`** y **`fee`** *o* **`fees`** (no ambos) + `slippage` opcional.
  - CLI **`scripts/report_kpis.py`**: `--equity` obligatorio; `--trades` opcional si equity trae columnas de costo por motor; **`--metadata`** YAML/JSON opcional (`run_id`, `trading_days_per_year`, etc.); salidas obligatorias **`--out-json`** y **`--out-md`** para diff humano + consumo automático.
- **Por qué**: Separa **cálculo puro** (testeable) de **CLI**; alinea números al spec para que dos corridas comparables usen las mismas definiciones; el ledger hoy exporta `costs_day` agregado — el desglose corto/largo sigue siendo **trades** hasta extender el export.
- **Consecuencias**: Extender el ledger con `costs_day_short`/`costs_day_long` en el CSV diario eliminaría la necesidad de CSV de fills solo para costos. Las métricas de riesgo y ejecución adicionales alineadas a **`rpt_kpi.v1`** (Sharpe/Sortino, hit rate, profit factor) quedan registradas en **ADR-031**; el drift de mandato 70/30·20/80 en informe queda registrado en **ADR-032**; la ampliación v3 (MDD/Calmar/turnover/alpha) queda en **ADR-033**.
- **Alternativas consideradas**:
  - **Solo notebook o script ad-hoc sin spec**: descartada por riesgo de definiciones divergentes entre corridas.
  - **Inferir motor solo desde símbolo**: descartada en v0; el spec pide tag explícito en fills o columnas de costo por motor.
- **Referencias**: `docs/kpi_report_spec.v1.md` §2.1, §5, §7, §10; `reporting/kpi_v0.py`, `scripts/report_kpis.py`, `tests/test_kpi_v0.py`.

---

## ADR-031 — KPI informe: Sharpe/Sortino (equity) + hit rate / profit factor (fills FIFO)

- **Fecha**: 2026-05-05
- **Estado**: aceptada
- **Contexto**: El plan Fase 5 y **`rpt_kpi.v1`** definen Sharpe/Sortino sobre retornos diarios simples por segmento (§5–§6) y hit rate / profit factor sobre operaciones cerradas con emparejamiento FIFO por `(motor, symbol)` (§8). El informe v0 inicial solo exponía CAGR-style, drawdown y costos por motor (**ADR-030**).
- **Decisión**:
  - **`reporting/kpi_v0.py`**: Sharpe anualizado \(\sqrt{252}\,\mu/\sigma\) y Sortino con desviación muestral sólo de retornos diarios \(r_t < 0\) (MAR = 0, \(r_f\) diaria = 0), sobre las series **`equity_total`**, **`equity_short`** y **`equity_long`** del CSV §2.1.
  - **Hit rate / profit factor**: desde filas del CSV de fills con **`ts`**, **`motor`/`bucket`**, **`symbol`**, **`side`**, **`qty`**, **`price`**, **`fee`/`fees`** (excluyentes) y **`slippage`** opcional; orden temporal estable; FIFO por símbolo y motor; profit factor \(+\infty\) serializado como la cadena **`"inf"`** en JSON para cumplir JSON estricto.
  - Filas **sin** `qty`/`price` válidos siguen contribuyendo a **costos por motor** pero **no** entran al cómputo FIFO (compatibilidad con CSV mínimos de solo comisión).
  - Salida JSON con **`segment.total`**, **`segment.short`**, **`segment.long`**, cada uno con `sharpe_annualized`, `sortino_annualized`, motivos de NA, `hit_rate`, `profit_factor`, `n_round_trips` (además de los campos previos en `total` como retorno anualizado y MDD).
- **Por qué**: Respeta el spec: los ratios de riesgo-rendimiento se definen sobre la curva de patrimonio; las estadísticas de acierto y factor de beneficio requieren el log de ejecución. Separar fuentes evita redefinir Sharpe “desde PnL de trades”, que no está en `rpt_kpi.v1`.
- **Consecuencias**: Para KPIs §8 hace falta export de fills con detalle de ejecución. El drift mandato 70/30·20/80 se incorpora en **ADR-032** y la capa v3 (benchmark/alpha + métricas rolling del largo) en **ADR-033**. Tests de regresión en `tests/test_kpi_v0.py` fijan series sintéticas conocidas.
- **Alternativas consideradas**:
  - **Sharpe solo desde PnL agregado de trades**: descartada por desalinear con **`docs/kpi_report_spec.v1.md`** §6.
  - **LIFO o promedio en vez de FIFO para round-trips**: descartada; el spec exige FIFO §8.
- **Referencias**: `docs/kpi_report_spec.v1.md` §5–§8; `reporting/kpi_v0.py`, `scripts/report_kpis.py`, `tests/test_kpi_v0.py`.

---

## ADR-032 — KPI informe v2: drift mandato 30/70 y 20/80 (serie + snapshot)

- **Fecha**: 2026-05-05
- **Estado**: aceptada
- **Contexto**: El plan Fase 5 (v2) exige materializar en el informe el drift del mandato en cada snapshot OOS (y opcional serie), comparando pesos reales MTM contra targets 30/70 y 20/80, sin disparar acciones automáticas desde el script.
- **Decisión**:
  - Extender **`reporting/kpi_v0.py`** con bloque **`mandate_drift`** en salida JSON: `targets`, `series` diaria y `snapshot_last_ts` (último `ts` de la ventana).
  - Tomar targets de prioridad: **`--policy`** (`weights` + `geo` de `config/policy.v1.yaml`) → metadata (`weights`/`geo`) → defaults 0.30/0.70/0.20/0.80.
  - Calcular drift en **puntos porcentuales**: \((w_{real} - w_{target}) \times 100\) para corto/largo sobre `equity_total`; para geo AR/US usar columnas opcionales `equity_ar` y `equity_us`.
  - Si faltan columnas geo en equity, reportar `geo_na_reason: missing_equity_ar_equity_us_columns` sin bloquear drift corto/largo.
  - Permitir bandas opcionales en metadata (`mandate_drift_bands_pp`) solo para comparación informativa; exponer `outside_band_axes` sin lógica de rebalance ni pass/fail automático.
  - Actualizar CLI **`scripts/report_kpis.py`**: flags `--policy` (default `config/policy.v1.yaml`) y `--no-policy`.
- **Por qué**: Hace auditable el cumplimiento de mandato por fecha con un contrato estable y reproducible; separa observabilidad (drift) de ejecución/riesgo para evitar acoplar decisiones automáticas en la etapa de reporte.
- **Consecuencias**:
  - El informe v2 pasa a `report_version: report_kpis_v2` y mantiene compatibilidad con corridas sin geo (declarando NA explícito).
  - Para drift geo completo, el export de equity debe incluir `equity_ar` y `equity_us` en moneda de reporte.
  - Se reduce ambigüedad en revisiones OOS: snapshot final y serie diaria quedan normalizados en el mismo payload.
- **Alternativas consideradas**:
  - **Calcular drift geo solo desde fills**: descartada por ser más frágil para MTM diario y más costosa de reconstruir.
  - **Aplicar bandas con acción automática en el script de reporte**: descartada; viola separación de responsabilidades (reporting vs allocator/engines/risk).
- **Referencias**: `.cursor/plans/bot_trading_paper-first_155d6f04.plan.md` (Fase 5, v2), `docs/kpi_report_spec.v1.md` §11, `reporting/kpi_v0.py`, `scripts/report_kpis.py`, `tests/test_kpi_v0.py`.

---

## ADR-033 — KPI informe v3: MDD_12m, Calmar_12m, turnover mensual largo y alpha benchmark

- **Fecha**: 2026-05-05
- **Estado**: aceptada
- **Contexto**: El plan Fase 5 (v3) pide completar el bloque largo del informe con métricas rolling de calidad y fricción, más alpha contra benchmark mixto alineado temporalmente.
- **Decisión**:
  - Subir `reporting/kpi_v0.py` a `report_version: report_kpis_v3`.
  - Agregar en `segment.long`: `mdd_12m_rolling_last`, `calmar_12m_last`, y `mdd_12m_rolling_series` (rolling de 252 retornos / 253 puntos).
  - Agregar `turnover_long_monthly` (serie por mes) y resumen último mes: `turnover_long_monthly_last` + `turnover_long_monthly_last_month`.
  - Agregar bloque `alpha_vs_benchmark` (total y largo), calculado con retornos simples compuestos sobre fechas en común (`inner join` por `ts`) usando CSV explícito de benchmark (`ts`, `benchmark_return`).
  - Extender CLI `scripts/report_kpis.py` con flag `--benchmark-returns`.
- **Por qué**: Cierra la trazabilidad de mandato + calidad del largo en un único reporte reproducible, evitando comparar corridas con definiciones incompletas y evitando leakage temporal en alpha.
- **Consecuencias**:
  - El reporte JSON ahora soporta lectura directa de KPI v3 sin post-procesos externos.
  - Alpha queda desacoplada de la descarga de mercado en runtime: el script consume retornos benchmark precomputados/alineables y mantiene contrato simple.
  - Se incrementa la exigencia del pipeline de datos para producir benchmark returns consistentes por `ts`.
- **Alternativas consideradas**:
  - **Calcular alpha con forward-fill de benchmark en el reporte**: descartada por riesgo de sesgo y conflicto con criterio de inner join del spec.
  - **Turnover largo solo agregado total (sin serie mensual)**: descartada por perder capacidad de auditoría temporal y de diagnóstico de picos de rotación.
- **Referencias**: `.cursor/plans/bot_trading_paper-first_155d6f04.plan.md` (Fase 5, v3), `docs/kpi_report_spec.v1.md` §7, §9, §12, `reporting/kpi_v0.py`, `scripts/report_kpis.py`, `tests/test_kpi_v0.py`.

---

## ADR-034 — Walk-forward OOS del informe KPI v3 + tabla maestra y gate opcional (`kpi_oos_gate`)

- **Fecha**: 2026-05-06
- **Estado**: aceptada
- **Contexto**: El plan Fase 5 — tras cerrar **`rpt_kpi.v1`**, el smoke `report_kpis` y la capa **v3** (**ADR-030**–**033**) — pide una **serie de tramos out-of-sample** donde se ejecute ese mismo informe por ventana, se **consolide una tabla única** y se pueda marcar **pass/fail frente a umbrales declarados antes** (spirit del “punto 3” del gate a producción), sin mezclar tuning de motor con KPI ad hoc en notebooks.
- **Decisión**:
  - Añadir en **`config/policy.v1.yaml`** el bloque **`kpi_oos_gate`**: shape de walk-forward (**`burn_in_trading_days`**, **`oos_trading_days`**, **`step_trading_days`**, **`min_oos_windows`**), **`aggregate`** (`rule: all | k_of_last_q` con **`k_pass`** / **`last_q_windows`** si aplica) y **`thresholds`** opcionales (solo claves informadas aplican chequeo): por ejemplo **`min_sharpe_annualized_total`**, **`max_drawdown_total_floor`** (drawdown negativo — pasa si el real no es “peor” que el floor), **`min_calmar_12m_long`**, **`max_mdd_12m_rolling_long_floor`**, **`max_turnover_long_monthly_last`**, **`min_alpha_simple_return_total`**. Por defecto **`enabled: false`** para no forzar CI hasta que los umbrales estén fichados en anexo político/POLICY según proceso del plan.
  - Implementar **`reporting/kpi_walk_forward.py`**: recorre ventanas públicas **`core_sim.short_term_pre_gate.walk_forward_oos_windows`**, corta equity/trades/benchmark por fechas del tramo, llama **`build_kpi_v0_report_from_tables`**, arma **`master_table`** y evalúa gate por ventana si está habilitado.
  - Exponer **`build_kpi_v0_report_from_tables`** en **`reporting/kpi_v0.py`** (el CLI **`report_kpis`** sigue leyendo paths y delegando).
  - CLI **`scripts/report_kpis_walk_forward.py`** escribe JSON agregado; exit code ≠ 0 si **`global_failures`** o **`aggregate_passed`** es false.
  - Contrato schema **`config/policy.v1.schema.json`** alineado al bloque.
- **Por qué**:
  - Reutiliza **las mismas definiciones numéricas** que el informe v3 (**sin duplicar fórmulas**); el walk-forward aquí es “cortá la serie como te diga política → corré el mismo reporte → juzgá”.
  - **`walk_forward`** se lee **siempre** desde `kpi_oos_gate` (aun con gate desactivado), evitando caer por error en **`burn_in` 252 por defecto** sobre series cortas cuando solo se quería tabular ventanas sin umbral.
- **Consecuencias**:
  - Los umbrales numéricos congelados para gate “de verdad” siguen viviendo en **POLICY / anexo fechado**; el YAML es solo el **hook ejecutable**.
  - Tramos muy cortos dejan KPI v3 como **NA** (p. ej. rolling 12m); si un threshold exige ese número, la ventaña **falla** con violación explícita.
- **Alternativas consideradas**:
  - **Ventanas igual al `short_term_pre_gate` corto**: descartada como única opción — el burn-in corto (`momentum` + margen) no es el mismo problema que tener 252 puntos para MDD rolling del largo; se prefirieron parámetros explícitos bajo **`kpi_oos_gate.walk_forward`**.
  - **Solo tabla sin gate**: descartada para el entregable del plan — el agregador **`all` / `k_of_last_q`** cierra la narrativa operativa tipo “no dependemos de una sola ventana”.
- **Referencias**: `.cursor/plans/bot_trading_paper-first_155d6f04.plan.md` (Fase 5, punto 8), `reporting/kpi_walk_forward.py`, `scripts/report_kpis_walk_forward.py`, `tests/test_kpi_walk_forward.py`.

---

## ADR-035 — CI mínimo: regresión KPI con dataset fijo de 60 días (golden values)

- **Fecha**: 2026-05-06
- **Estado**: aceptada
- **Contexto**: El plan Fase 5 (ítem 9) pide un **test de regresión** sobre KPIs con un dataset **fijo de 60 días** en `tests/fixtures/` y valores **golden**, para que CI detecte cambios accidentales en fórmulas o en el pipeline del informe sin depender de corridas externas ni de datos mutables.
- **Decisión**:
  - Directorio **`tests/fixtures/kpi_golden/`**: `equity_60d.csv`, `trades_60d.csv`, `benchmark_returns_60d.csv`, `metadata.yaml` y **`expected_kpis.json`** (salida serializada de `build_kpi_v0_report`, sin series largas de rolling 12m ni serie diaria de drift — solo lo necesario para asserts estables).
  - Script **`scripts/regenerate_kpi_golden_fixtures.py`**: regenera fixtures + golden de forma **100 % determinística** (sin RNG no controlado); uso **manual** cuando cambie el spec o se acepte un cambio de números con ADR/commit explícito.
  - Tests **`tests/test_kpi_regression_golden.py`**: ejecutan el informe real sobre los CSV, comparan contra el JSON golden con **`pytest.approx(rel=1e-9)`** en floats y igualdad estricta en razones NA y conteos; nombres orientados a **comportamiento** (ventana de 60 días, Sharpe total, FIFO por motor, turnover mensual largo, drift dentro de bandas, alpha inner join, metadata de spec).
- **Por qué**:
  - Separa **contrato de salida** del informe de **detalles de implementación**: un refactor interno que preserve números no rompe CI; un cambio de definición en `rpt_kpi.v1` sí debe romper y forzar regeneración consciente.
  - Evita sobre-mocking del propio `reporting/kpi_v0`: el test ejercita el mismo camino que producción (`build_kpi_v0_report` + CSV reales).
- **Consecuencias**:
  - Cualquier cambio legítimo en fórmulas KPI exige **`python scripts/regenerate_kpi_golden_fixtures.py`** + revisión de diff del JSON + mención en changelog/ADR según gravedad.
  - Con 60 días, **MDD_12m / Calmar rolling** siguen en **NA** por diseño del spec; el golden no fija esos campos como números finitos en este tramo.
- **Alternativas consideradas**:
  - **Solo asserts “no es None” sin golden**: descartada — no detecta drift silencioso en magnitudes.
  - **Golden generado en runtime del test**: descartada — rompe reproducibilidad entre máquinas y dificulta revisar el diff en PR.
- **Referencias**: `.cursor/plans/bot_trading_paper-first_155d6f04.plan.md` (Fase 5, ítem 9), `docs/kpi_report_spec.v1.md`, `tests/test_kpi_regression_golden.py`.

---

## ADR-036 — Short bucket: drawdown mensual sobre bucket equity (actualización de ADR-025)

- **Fecha**: 2026-05-08
- **Estado**: aceptada (reemplaza ADR-025)
- **Contexto**: ADR-025 introdujo `short_cash` y `_short_month_start_cash` para capturar el cash realizado al cerrar posiciones. Sin embargo, la implementación seguía calculando el peak y el drawdown sobre el MV de posiciones abiertas, con un ajuste ad-hoc para el caso de `short_equity == 0`. Esto producía resultados incorrectos en escenarios con cierre parcial o mid-month: el drawdown podía superar el -100% teórico, y el peak se calculaba sobre una base diferente al valor con el que se comparaba. La causa raíz era que el "valor del bucket" no tenía una definición unificada.
- **Decisión**:
  - Definir `bucket_equity_t = short_cash_t + sum(market_value de posiciones short abiertas a t)` como la base única de cómputo para peak y drawdown mensual del bucket corto.
  - `monthly_peak = max(bucket_equity_t)` durante el mes corriente; resetea el primer día hábil de cada mes al `bucket_equity` de ese momento.
  - `monthly_drawdown = (bucket_equity_t / monthly_peak) - 1` si `peak > 0`, else `0`.
  - Eliminar `_short_month_start_cash` (ya no es necesario el ajuste ad-hoc).
  - El campo `daily_return` del bucket short **sigue** calculado sobre MV de posiciones abiertas (mide calidad de stock-picking; base distinta al drawdown es intencional).
- **Por qué**:
  - El cálculo previo (MV-only) generaba drawdown de -100% al cerrar una posición con ganancia: el MV caía a 0 pero el cash subía — pérdida contable falsa. Esto contaminaba el gate del validation-wf (`floor -0.25`) con señales falsas.
  - `bucket_equity` captura correctamente el valor total del bucket bajo la mecánica BUY-first del ledger: cuando se vende (cierre), el cash sube y el MV baja; la suma permanece estable si no hubo pérdida real.
  - `daily_return` y `monthly_drawdown` miden cosas distintas: calidad de selección (MV open) vs. riesgo de capital comprometido (bucket equity). Tener bases distintas es una decisión explícita documentada aquí.
- **Consecuencias**:
  - Fixtures de tests del kill switch (`test_short_term_day_runner.py`, `test_short_term_day_runner_kill_switch.py`) requirieron recalibración al cambiar la semántica del DD; se recalibraron solo fixtures, no thresholds ni lógica.
  - La recalibración de thresholds operativos (`-0.08` kill switch, `-0.25` floor pre-gate) queda pendiente post-baseline paper-live con datos reales.
- **Alternativas consideradas**:
  - **Mantener ajuste ad-hoc de ADR-025 (`effective_value`)**: descartada — no cubría escenarios mid-month con posiciones parcialmente cerradas; la corrección era puntual para `equity == 0`, no para el caso general.
  - **Usar `short_cash - month_start_cash` como proxy de PnL**: descartada — requería rastrear el cash inicial del mes, complicaba el reset y era frágil ante múltiples ciclos de apertura/cierre en el mismo mes.
- **Archivos**: `core_sim/ledger.py` (`_update_short_drawdown` reescrito, `_short_month_start_cash` eliminado), `tests/test_ledger.py` (1 rediseñado, 1 ajustado, 4 nuevos: SCN-3/5/7/10)
- **Commit**: `e724378`

---

## ADR-037 — Venue: código MIC para conectores de mercado (US = XNYS)

- **Fecha**: 2026-05-08
- **Estado**: aceptada
- **Contexto**: `data/connectors/us_connector.py` hardcodeaba `venue="US"` al construir `OHLCVRow`. Todo el resto del sistema — schema SQLite, calendar builder, fetcher logs, queries de validación — usaba `"XNYS"` (código MIC de NYSE). El mismatch era silencioso: no crasheaba en unit tests pero hubiera roto la capa de persistencia desde el primer día de paper-live (las filas insertadas con `venue="US"` nunca serían encontradas por queries que filtran `venue="XNYS"`).
- **Decisión**:
  - Introducir constante `_VENUE = "XNYS"` en `data/connectors/us_connector.py`; todos los `OHLCVRow` retornados usan ese valor.
  - Actualizar labels de log en `data/fetcher.py` para que digan `XNYS` donde antes decían `US`.
  - Agregar script idempotente `scripts/migrate_venue_us_to_xnys.py` para backfill de datos existentes en SQLite (exit codes: 0 OK, 1 error DB, 2 DB no encontrada).
  - **Regla de equipo**: cualquier connector nuevo usa el código MIC como venue. US = `"XNYS"` (NYSE), AR = `"XBUE"` (BYMA). Nunca strings informales como `"US"` o `"AR"`.
- **Por qué**:
  - Un mismatch de venue en la capa de persistencia es un bug silencioso de consecuencias graves: datos correctamente descargados son invisibles para el sistema.
  - Los códigos MIC son estándar ISO 10383, sin ambigüedad. Strings informales crean fricciones cuando se agregan nuevos mercados o fuentes.
  - La migración idempotente permite corregir datos existentes sin riesgo de duplicados (maneja conflictos de PK).
- **Consecuencias**:
  - Antes de iniciar paper-live con una DB existente, correr `python scripts/migrate_venue_us_to_xnys.py --db data/market.db`.
  - Tests de integración en `test_data_us_connector.py` y `test_data_integration.py` actualizados para assertar `"XNYS"` en lugar de `"US"`.
- **Alternativas consideradas**:
  - **Normalizar venue en la capa de storage (MarketDB) en lugar del conector**: descartada — el contrato dice que el caller produce el `OHLCVRow` correcto; normalizar en storage oculta bugs upstream (mismo argumento que ADR-023 para side uppercase).
  - **Enum para venue en lugar de string**: postergada para v2 — agregar un enum en v1 requeriría cambiar la firma de los conectores y el schema en el mismo commit que el bug fix; el costo supera el beneficio inmediato.
- **Archivos**: `data/connectors/us_connector.py`, `data/fetcher.py`, `scripts/migrate_venue_us_to_xnys.py` (nuevo), `tests/test_data_us_connector.py`, `tests/test_data_integration.py`, `tests/test_migrate_venue_us_to_xnys.py` (nuevo)
- **Commits**: `ef3bd8b`, `b333381`

---

## ADR-038 — Desviaciones documentadas del change ledger-dd-fix

- **Fecha**: 2026-05-08
- **Estado**: aceptada
- **Contexto**: Durante la aplicación del change `ledger-dd-fix` se materializaron dos desviaciones respecto al diseño acordado. Se documentan aquí siguiendo la convención del proyecto de trazar historial sin borrar.

### Desviación 1: re-calibración de fixtures en kill-switch tests

- **Escenario**: el cambio semántico del DD (ADR-036) rompió fixtures en `tests/test_short_term_day_runner.py` y `tests/test_short_term_day_runner_kill_switch.py`. Los tests asumían números basados en MV-only que dejaron de ser válidos con la nueva base de bucket equity.
- **Resolución**: se re-calibraron los fixtures (datos de entrada de los tests) para que sean consistentes con la nueva definición de DD. La lógica del kill switch, los thresholds (`-0.08`) y el floor del validation-wf (`-0.25`) no fueron modificados.
- **Pendiente**: recalibración de thresholds operativos post-baseline paper-live (ver `paper-live/plan`). La calibración actual es coherente internamente pero no ha sido validada contra distribución de retornos reales.

### Desviación 2: exit code 2 en script de migración

- **Escenario**: el diseño especificaba "non-zero en error de DB". El implementador agregó `exit code 2` específico para "la DB no existe" (pre-check antes de abrir SQLite).
- **Resolución**: mejora aceptada. El exit code 2 previene que Python cree silenciosamente un archivo SQLite vacío ante un typo en el path `--db`. El spirit del diseño (avisar si algo sale mal) se honra con más precisión. Convención: exit 0 = OK, exit 1 = error durante operación, exit 2 = precondición no cumplida (DB no encontrada).

---

## ADR-039 — Persistencia de fills y snapshots en SQLite (paper-persistence)

- **Fecha**: 2026-05-08
- **Estado**: aceptada
- **Contexto**: Sin persistencia, cada corrida del bot arranca de cero — sin memoria de posiciones anteriores ni histórico de P&L. Para arrancar paper-live es imprescindible acumular fills y snapshots diarios en la DB existente (`data/market.db`).
- **Decisión**:
  - Agregar dos tablas nuevas en `MarketDB`: `paper_fills` (un registro por fill ejecutado) y `paper_snapshots` (un snapshot de equity/DD/drift por día hábil, UNIQUE por `mode + trading_day`).
  - Persisitir desde la capa del caller (runner/orquestador) después de `backtester.run_day()`, leyendo fills de `broker.get_fills()` (full `FillReport` con slippage) y snapshot de `ledger.mark_to_market()` + `ledger.short_cash` (attr directo, no está en el dict de retorno).
  - Venue derivada en persist time: `market="US"` → `"XNYS"`, `market="AR"` → `"XBUE"`.
  - Replay-from-fills como estrategia de reanudación: ledger vacío → `apply_fills()` por día en orden → estado reconstruido determinísticamente.
  - `paper_snapshots` usa `INSERT OR REPLACE` para tolerar reinicios mid-day.
  - Wiring del param `db: MarketDB | None = None` en `long_term_monthly_runner` (le faltaba).
- **Por qué**:
  - El mismo patrón ya usado por `kill_switch_log`: la capa de core_sim no conoce storage; el caller persiste. Cero contaminación de `core_sim`.
  - `INSERT OR REPLACE` en snapshots es idempotente — un crash y restart no genera duplicados ni viola UNIQUE.
  - Replay-from-fills es determinístico y O(n fills): suficiente para paper trading (estimado <50 fills/día). Snapshots son cache derivado, no fuente de verdad.
  - `broker.get_fills()` es necesario (no `fill_orders()` return) porque `fill_orders()` strip the `cost_breakdown` — slippage se pierde en el retorno simplificado.
- **Consecuencias**:
  - `short_cash` del ledger debe leerse como attr (`ledger.short_cash`), no desde el dict de `mark_to_market()`.
  - Las tablas se crean automáticamente en `_init_schema()` via `CREATE TABLE IF NOT EXISTS` — no requiere migration script manual.
  - Backtests siguen escribiendo CSV; no tocan estas tablas.
  - Supabase sync para paper tables: postergado (future).
- **Alternativas consideradas**:
  - **Persist dentro de `PaperBrokerSim`**: descartada — rompe SRP, acopla core_sim al data layer.
  - **Callback hook en `DailyEventBacktester`**: descartada — overkill para un solo consumer; agrega complejidad sin beneficio inmediato.
- **Archivos**: `data/storage.py`, `core_sim/long_term_monthly_runner.py`, `tests/test_data_storage.py`
- **Change**: SDD #2 `paper-persistence` (propuesta, spec, diseño y tasks en Engram)

---

## ADR-040 — Activación paper-live: branch dedicada, Git LFS y workflow robusto

- **Fecha**: 2026-05-11
- **Estado**: aceptada
- **Contexto**: El orquestador paper-live (`scripts/run_paper_live.py`, ADR-039) ya existía pero no podía funcionar en producción real por tres gaps: (1) el workflow no descargaba OHLCV antes de ejecutar el pipeline; (2) `git add data/market.db` era ignorado por `.gitignore`; (3) no había notificación ante fallos, lo que dejaba ventana para violar la política F3 (gap > 3 días hábiles) sin detección.
- **Decisión**:
  - **Branch `paper-live-data`** separada de `main`: código operativo + DB persistida. `main` evoluciona limpio (solo código); `paper-live-data` acumula artefactos operativos diarios. Sincronización de código vía `git merge main` desde `paper-live-data`.
  - **Git LFS** para `data/*.db` en `paper-live-data` (`.gitattributes` con filtro LFS): evita que commits diarios de binario SQLite inflen el repo (~250 commits/año × tamaño creciente de DB).
  - **`.gitignore` con negación** `!data/market.db` solo en `paper-live-data`: permite tracking de la DB sin afectar `main`.
  - **Workflow** (`.github/workflows/paper_live_daily.yml` en `main`):
    - Step `Fetch latest OHLCV` (`fetch_daily.py --lookback 5`) antes de `run_paper_live.py`, con env opcionales `IOL_USER`/`IOL_PASS` desde secrets.
    - `git add -f data/market.db` en lugar de `git add` para robustez ante gitignore.
    - Step `Notify on failure` (`actions/github-script@v7`): crea issue GitHub automático al fallar, con link a logs del run.
  - **Seed inicial local**: `fetch_daily.py --lookback 120` para poblar historial mínimo antes del primer cron.
- **Por qué**:
  - Sin fetch en el workflow, la DB no tiene barras del día y `run_paper_live.py` falla con "No OHLCV bars found".
  - Sin LFS, el repo crece ~1 GB/año en commits binarios — impacta clones, CI y mantenimiento.
  - Sin notificación, un fallo silencioso de 3+ días activa F3 y requiere intervención manual sin aviso previo.
  - Separar branches evita contaminar `main` con ~250 commits/año de DB binaria y preserva historial limpio para PRs y revisiones de código.
- **Consecuencias**:
  - GitHub LFS tiene 1 GB storage + 1 GB bandwidth gratis; suficiente para paper trading.
  - Para usar IOL directo (AR), configurar `IOL_USER`/`IOL_PASS` en **GitHub Actions secrets** (obligatorio para CI; variables locales de Windows no aplican al runner). Sin ellos, fallback Byma/yfinance puede operar pero el fetch AR queda degradado. Runbook ampliado en **ADR-050**.
  - Cambios de código en `main` deben mergearse a `paper-live-data` para que el cron los use.
  - El workflow YAML vive en `main` (GitHub lee schedule/dispatch del default branch); el checkout ejecuta contra `paper-live-data`.
- **Alternativas consideradas**:
  - **Todo en `main`**: descartada por ruido de commits diarios de DB en historial de código.
  - **Artifact storage externo (S3/GCS)**: descartada por complejidad adicional sin beneficio claro para paper trading.
  - **`git add -f` sin LFS**: descartada por inflación de repo a mediano plazo.
- **Archivos**: `.github/workflows/paper_live_daily.yml`, `.gitattributes` (nuevo en `paper-live-data`), `.gitignore` (modificado en `paper-live-data`)
- **Commits**: `9546f2b` (workflow en `main`)

---

## ADR-041 — Gate KPI OOS con umbrales pre-registrados y protocolo ramp-up

- **Fecha**: 2026-05-11
- **Estado**: aceptada
- **Contexto**: El plan maestro quedó con 12/12 todos completados en código, pero faltaba el último entregable de Fase 5: umbrales numéricos congelados **antes** del primer resultado OOS agregado, y el protocolo de transición paper → capital real. Sin esto, el gate es una infraestructura vacía (thresholds en `null`, `enabled: false`).
- **Decisión**:
  - **Activar `kpi_oos_gate.enabled: true`** en `config/policy.v1.yaml` y rellenar 7 umbrales bloqueantes + 2 informativos:
    - `min_sharpe_annualized_total: 0.30` — piso modesto; no destruir valor ajustado por riesgo vs ETFs pasivos (Sharpe histórico SPY ~0.4–0.5).
    - `min_sortino_annualized_total: 0.40` — con kill switch y límites diarios, downside debería estar más acotado que upside.
    - `max_drawdown_total_floor: -0.18` — peor caso razonable: largo 70% × -25% + corto 30% × -8% ≈ -20%.
    - `max_drawdown_short_floor: -0.10` — kill switch se auto-resetea por mes; en ventana OOS de ~3 meses puede acumular dos activaciones.
    - `max_drawdown_long_floor: -0.25` — tolerar bear market normal sin culpar al bot; detectar errores de rebalanceo.
    - `max_turnover_long_monthly_last: 0.08` — con bandas drift 2pp, turnover esperado 1–5%; techo de 8% detecta bugs de churn.
    - `min_alpha_simple_return_total: -0.02` — tolerar hasta -2% anuales vs benchmark pasivo como costo del bloque corto activo.
    - `min_calmar_12m_long: null` + `max_mdd_12m_rolling_long_floor: null` — informativas, no bloqueantes (dependen del mercado, no del bot).
  - **Agregar `ramp_stage: paper`** al YAML con enum validado en schema (`paper`, `ramp_10`, `ramp_25`, `ramp_50`, `live_100`).
  - **Anexo fechado §13** en `POLICY.md` (`gate.v1`, 2026-05-11): tabla de umbrales con justificación, regla de agregación (`all`), parámetros walk-forward (burn-in 252, OOS 60, step 30), y gobernanza de versiones.
  - **Protocolo ramp-up §14** en `POLICY.md`: 5 escalones (paper → 10% → 25% → 50% → 100%) con criterio de entrada, duración mínima por escalón (30–60 días), criterios de rollback (DD real > 1.5× peor OOS), y regla de rollback a paper (DD > 2× peor OOS). Subir de escalón es decisión humana; bajar puede ser automático.
- **Por qué**:
  - Pre-registrar umbrales evita "tirar hasta acertar" — si los fijás después de ver resultados, no tenés gate, tenés confirmation bias.
  - Separar métricas de mercado (Calmar/MDD largo) de métricas del bot (Sharpe/alpha/turnover) da señal limpia.
  - El ramp gradual con checkpoints reduce riesgo de overshoot si el paper sobreestima calidad de ejecución real (slippage real > simulado, etc.).
- **Consecuencias**:
  - Datos mínimos para la primera evaluación: 312 días hábiles (~15 meses de paper-live). Hoy hay ~120 días históricos; el gate no puede correrse hasta acumular suficiente serie.
  - Cambiar cualquier umbral requiere `gate.v2` con fecha y motivo en POLICY.md y YAML simultáneamente.
  - El `ramp_stage` es trazabilidad pura; no tiene lógica automática en el código hoy (el runner no cambia comportamiento por escalón).
- **Alternativas consideradas**:
  - **Umbrales más agresivos (Sharpe >= 1.0, alpha >= 0)**: descartadas — un bot v1 moderado con 70% pasivo no debería aspirar a Sharpe > 1; un piso agresivo invalida el gate ante cualquier régimen normal.
  - **Calmar/MDD como bloqueantes**: descartadas para el largo pasivo — detectan mercado, no bot; activarlos generaría falsos positivos en bear markets.
  - **Ramp sin escalones intermedios (paper → 100%)**: descartada por riesgo operativo; los escalones permiten detectar discrepancias paper vs real a escala menor.
- **Archivos**: `config/policy.v1.yaml`, `config/policy.v1.schema.json`, `POLICY.md` (§13, §14)

---

## ADR-042 — RSI(14) como filtro de entrada y señal de salida del motor corto

- **Fecha**: 2026-05-12
- **Estado**: aceptada
- **Contexto**: El walk-forward OOS de 180 días mostraba 9/13 ventanas fallando por `monthly_short_drawdown` debajo del floor (-0.25). El motor corto entraba en tickers sobrecomprados (momentum positivo pero RSI alto) y solo salía por stop-loss ATR, acumulando drawdowns innecesarios.
- **Decisión**: Agregar RSI(14) al motor corto con dos roles:
  1. **Filtro de entrada**: descartar candidatos con RSI > `rsi_overbought_entry` (default **70** en el ADR original; ver actualización abajo). Reason: `rsi_overbought`.
  2. **Señal de salida por crossover descendente**: vender posiciones del bucket short cuando `rsi_yesterday >= rsi_exit_threshold` y `rsi_today < rsi_exit_threshold` (default 45). Reason: `rsi_momentum_exhausted`. El crossover evita salidas falsas cuando RSI simplemente está bajo y estable.
  3. **Contadores de auditoría**: cada ventana OOS reporta `entries_blocked_by_rsi`, `exits_by_rsi`, `exits_by_stop_loss` para explicar por qué cambió el resultado.
- **Por qué**:
  - RSI es complementario al momentum (no redundante): momentum dice "sube", RSI dice "se pasó de rosca".
  - Solo 2 umbrales tunables reales (`rsi_overbought_entry`, `rsi_exit_threshold`); `rsi_lookback=14` es estándar fijo.
  - Fórmula determinística y auditable; sin ventanas adaptativas ni ML.
  - Alternativas con más parámetros (MACD: 3, cruces de medias: 2 lookbacks) excedían la complejidad mínima pedida.
- **Consecuencias**:
  - Walk-forward 180d con RSI: `avg_max_dd` mejoró de -0.134% a -0.098%; turnover bajó de 1.69 a 1.26; RSI bloqueó 130 entradas y disparó 5 salidas anticipadas.
  - `windows_passed` se mantuvo en 4/13 — el drawdown mensual del bucket corto sigue siendo el cuello de botella, pero la mejora en DD total indica dirección correcta.
  - Si `rsi_overbought_entry=70` resulta muy restrictivo en tendencias fuertes, se puede subir a 75–80 sin cambiar arquitectura.
  - Deduplicación implementada: si RSI ya generó SELL, stop-loss no duplica la orden para el mismo símbolo.
- **Actualización 2026-06**: `rsi_overbought_entry` en `config/policy.v1.yaml` quedó en **80.0** (menos restrictivo en tendencias fuertes; sin cambio de arquitectura ni de la lógica de crossover de salida).
- **Alternativas consideradas**:
  - **MACD**: 3 parámetros nuevos (fast, slow, signal) — más complejidad de la pedida.
  - **Bollinger Bands**: resuelve solo entradas, no salidas.
  - **RSI con umbral fijo para salida** (sin crossover): genera salidas falsas cuando RSI está bajo pero estable (pullback sano en tendencia alcista).
- **Archivos**: `core_sim/short_term_engine.py`, `core_sim/short_term_day_runner.py`, `core_sim/short_term_pre_gate.py`, `scripts/run_short_term_pre_gate.py`, `config/policy.v1.yaml`, `config/policy.v1.schema.json`, `core_sim/__init__.py`

---

## ADR-043 — ADRs argentinos en whitelist US con precedencia de market tag

- **Fecha**: 2026-05-13
- **Estado**: aceptada
- **Contexto**: Los tickers MELI, YPF, TGS y GGAL estaban solo en `whitelist_ar.yaml` como acciones BYMA. Sin embargo, son ADRs listados en NYSE/NASDAQ y el sistema debería poder operarlos como instrumentos US (sesión NYSE, costos US, horario US). Además, `fetch_daily.py` solo leía las claves `etfs` y `stocks` del YAML US, por lo que una nueva categoría sería ignorada sin fix.
- **Decisión**:
  - Agregar sección **`adrs`** en `config/symbols/whitelist_us.yaml` con MELI, YPF, TGS y GGAL.
  - Extender la tupla de buckets en `load_merged_whitelist` (`short_term_day_runner.py`) y en `_load_symbols_from_policy` (`fetch_daily.py`) de `("etfs", "stocks")` a `("etfs", "stocks", "adrs")`.
  - **Invertir el orden de carga** en `load_merged_whitelist`: AR se carga primero, US después. Esto asegura que para tickers presentes en ambas listas, el tag US (ADR) tiene precedencia (last-write-wins).
  - No se modificó `whitelist_ar.yaml`: los mismos tickers siguen presentes como acciones BYMA para el caso en que se quieran operar localmente en el futuro con tickers diferenciados (e.g. `GGAL.BA`).
- **Por qué**:
  - Los ADRs operan en horario US, con costos US y sesión NYSE — tagearlos como "AR" haría que el sistema les aplique sesión AR, costos AR y horario AR, lo cual es incorrecto.
  - El orden anterior de carga (US primero, AR después) hacía que AR sobrescribiera el tag US para tickers duplicados, anulando silenciosamente el efecto de agregar ADRs al whitelist US.
  - Separar la categoría `adrs` del `stocks` hace explícita la naturaleza del instrumento y facilita filtrados futuros (e.g. reportes por tipo de instrumento).
- **Consecuencias**:
  - Los 4 tickers quedan como `"US"` en el dict `merged`. El motor corto les aplica sesión US, el risk los evalúa con fallback stop-loss US, y el allocator los cuenta dentro del headroom geo US.
  - Si en el futuro se quieren operar las versiones locales BYMA en paralelo, se necesitarán tickers diferenciados en `whitelist_ar.yaml` (e.g. `GGAL.BA` vs `GGAL`).
  - `long_term_monthly_runner.py` no requirió cambio porque reutiliza `load_merged_whitelist` del day runner.
- **Alternativas consideradas**:
  - **Agregar los ADRs directamente en `stocks` del US whitelist**: descartada — mezcla la categoría y pierde la distinción semántica entre acciones US nativas y ADRs argentinos.
  - **Eliminar los tickers de `whitelist_ar.yaml`**: descartada — preservarlos permite operar versiones BYMA en el futuro con tickers diferenciados sin perder la configuración.
  - **Mantener el orden original de carga (US antes que AR)**: descartada — hacía que los ADRs fueran sobrescritos como "AR", anulando el propósito del cambio.
- **Archivos**: `config/symbols/whitelist_us.yaml`, `core_sim/short_term_day_runner.py`, `scripts/fetch_daily.py`, `tests/test_short_term_day_runner.py`

---

## ADR-044 — Integración largo en paper-live, guardrail largo efectivo, dedup riesgo corto

- **Fecha**: 2026-05-13
- **Estado**: aceptada
- **Contexto**: Tres brechas operativas: (1) el motor largo no estaba integrado en el loop diario paper-live, (2) `check_long_risk` era no-op porque el runner pasaba el snapshot completo como scoreboard (key `long_daily_return` ausente, default 0.0), y (3) `_check_risk_with_optional_db` duplicaba los 4 pasos de `check_short_risk` manualmente.
- **Decisión**:
  1. **Ledger**: agregar `_long_eod_by_trading_date` y `_attach_long_daily_return()` para computar el daily return del sleeve largo, incluyendo `long_bucket` en el return de `mark_to_market`.
  2. **long_term_monthly_runner**: `propose_orders` y `risk_check` extraen `snap["long_bucket"]` y pasan ese dict a `check_long_risk` (no el snapshot completo).
  3. **run_paper_live**: cablear `create_long_term_monthly_backtester` con feature flag `--enable-long-engine` (default false). Orden fijo: short → long. Fills combinados. Snapshot final post-ambos sleeves. DB y calendar_store inyectados al short backtester.
  4. **Dedup riesgo corto**: `_check_risk_with_optional_db` refactorizado a orquestador liviano: reutiliza `check_short_risk` con config override para data_quality+no_trade, luego `check_and_persist_kill_switch`, luego `check_short_risk` para daily_loss.
- **Por qué**:
  - Sin `long_daily_return` el guardrail largo nunca disparaba — riesgo silencioso.
  - La duplicación de la cadena de 4 pasos hacía que cualquier cambio en `check_short_risk` requiriera sincronización manual en `_check_risk_with_optional_db`.
  - El flag `enable_long_engine=false` permite rollback inmediato a short-only sin cambios de código.
- **Consecuencias**:
  - `mark_to_market` ahora retorna `long_bucket` con `long_daily_return` y `long_equity`.
  - Orden de ejecución short→long fijo; el largo consume la caja que quedó después del corto.
  - El flag es CLI (`--enable-long-engine`); desactivación inmediata sin deploy.
- **Alternativas consideradas**:
  - **Exponer long_daily_return desde un snap genérico**: descartada — el snapshot no tenía esa key, forzaba al caller a calcular manualmente.
  - **Feature flag en YAML de policy**: descartada por ahora — un flag CLI es más simple para paper-live y evita tocar el schema de policy.
  - **No deduplicar el riesgo corto (mantener copia)**: descartada — violaría la regla de single source of truth para la cadena de riesgo.
- **Archivos**: `core_sim/ledger.py`, `core_sim/long_term_monthly_runner.py`, `core_sim/short_term_day_runner.py`, `scripts/run_paper_live.py`, tests correspondientes.

---

## ADR-045 — Rebalanceo del motor largo: de mensual a semanal

- **Fecha**: 2026-05-13
- **Estado**: aceptada (actualiza ADR-017 en lo referente a `rebalance_rule`)
- **Contexto**: El motor largo usaba `rebalance_rule: first_us_trading_day_of_calendar_month`, evaluando drift y ejecutando rebalanceos solo una vez al mes. En mercados volátiles (ej. crash arancelario Feb–Abr 2026, SPY -6.3%), un mes de latencia puede acumular desvíos significativos antes de corregir. Además, el guardrail `check_long_risk()` (-1.5% diario) solo se evaluaba cuando el motor largo corría, es decir, una vez al mes.
- **Decisión**:
  - Cambiar `rebalance_rule` a `first_us_trading_day_of_calendar_week` en `config/policy.v1.yaml`.
  - Cambiar `cadence.long` de `monthly` a `weekly`.
  - Implementar `is_first_us_trading_day_of_week()` y `is_rebalance_day_by_rule()` en `core_sim/long_term_engine.py` como funciones puras que resuelven el día de rebalanceo según regla configurada.
  - Actualizar `validate_long_term_engine_config()` para aceptar ambas reglas (`week` y `month`).
  - Actualizar `config/policy.v1.schema.json`: `cadence.long` acepta `["weekly", "monthly"]`; `rebalance_rule` pasa a enum explícito.
  - Actualizar `validation/stages/long_engine.py` para evaluar suficiencia temporal según la regla (semanas para weekly, meses para monthly).
  - Actualizar `POLICY.md` §10.3 y tabla §10.6 para reflejar rebalanceo semanal.
- **Por qué**:
  - Semanal reduce la latencia de corrección de drift de ~22 días hábiles a ~5, atrapando desvíos grandes antes de que se acumulen.
  - El guardrail `check_long_risk()` pasa de evaluarse ~1x/mes a ~4x/mes, mejorando la protección real del sleeve largo.
  - Con ETFs pasivos y bandas de drift de 2pp, la mayoría de las semanas seguirá siendo un no-op (drift dentro de banda); el costo operativo extra es marginal.
- **Consecuencias**:
  - El turnover mensual del largo puede subir levemente respecto de mensual puro; el techo de 8% en `kpi_oos_gate` sigue como guardrail.
  - La función `is_first_us_trading_day_of_month()` se mantiene para backward-compat y métricas legacy, pero ya no controla el gate de rebalanceo.
  - Tests y fixtures de `test_long_term_engine.py`, `test_validation_runner.py` y `test_kpi_walk_forward.py` actualizados a la nueva regla.
- **Alternativas consideradas**:
  - **Mantener mensual**: descartada — demasiada latencia en mercados volátiles; el guardrail largo se evaluaba muy poco.
  - **Diario**: descartada — genera ruido operativo (29/30 días no-op) y logs innecesarios sin beneficio real para ETFs pasivos.
  - **Bandas de drift más estrechas con cadencia mensual**: descartada — no resuelve la baja frecuencia de evaluación del guardrail.
- **Archivos**: `core_sim/long_term_engine.py`, `core_sim/__init__.py`, `config/policy.v1.yaml`, `config/policy.v1.schema.json`, `POLICY.md`, `validation/stages/long_engine.py`, `AGENTS.md`, `README.md`, `docs/project-overview.md`, tests correspondientes.
- **Validación empírica**: comparación semanal vs mensual vs SPY en walk-forward → **ADR-046** (`notebooks/wf_long_comparison.ipynb`; pasos 3–4 implementados; corrida continua 12m pendiente).

---

## ADR-046 — Notebook walk-forward comparativo del motor largo (evidencia ADR-045)

- **Fecha**: 2026-05-15
- **Estado**: aceptada (plan del notebook completo: pasos 1–5)
- **Contexto**: **ADR-045** pasó el rebalanceo largo de mensual a semanal por argumentos de latencia de drift y frecuencia del guardrail diario, pero sin una corrida controlada que compare ambas reglas y un benchmark en las mismas ventanas, costos y datos. El pipeline WF del largo (**ADR-027**, `run_long_engine_wf.py`) agrega métricas por ventana (`max_drift_observed_pp`, costos, etc.) pero no exporta la **curva diaria de equity del sleeve largo**, necesaria para superponer estrategias y normalizar a base 100.
- **Decisión**:
  - Extender `validation/stages/long_engine.run_long_engine_stage` con parámetro opcional `return_details: bool = False` y dataclass `StageDetails`:
    - `daily_equity`: lista de `{"date", "equity"}` por día hábil con barras (MTM del sleeve largo vía `_compute_long_bucket_mtm`);
    - `fills`: fills acumulados de rebalanceos en el período;
    - `final_positions`: cantidades finales por símbolo en bucket `long`.
  - Retorno **siempre** tupla `(StageResult, StageDetails | None)`; si `return_details=False`, el segundo elemento es `None`. Callers existentes adaptados: `validation/runner.py` desempaqueta; `validation/wf_runner.py` usa solo `[0]` por ventana (comportamiento del CLI sin cambios).
  - Notebook `notebooks/wf_long_comparison.ipynb`:
    - Helper `spy_buy_and_hold_equity(bars, initial_cash)` → `equity_t = initial_cash × (close_t / close_0)`.
    - **Orquestador (paso 3)**: calendario US abr-2025 → may-2026; `generate_wf_windows(3, 1)`; por ventana corre `run_long_engine_stage(..., return_details=True)` con `policy_with_rebalance_rule` (semanal vs mensual) + SPY; acumula `equity_df` y `windows_df`.
    - **Visualizaciones (paso 4)**: grid de curvas base 100 por ventana; tabla pivote retorno % / Sharpe (252d) / MDD %; barra de retorno promedio cross-ventanas; barras agrupadas de MDD ventana a ventana.
    - **Gráfico continuo (paso 5)**: una corrida sobre todo `trading_days` (sin reset entre ventanas WF); `continuous_equity_df` + panel dual (USD nominal y base 100).
  - Costos reales del `cost_model` en policy (vía stage); equity solo del sleeve largo.
- **Por qué**:
  - Reutilizar el mismo stage que validation-wf y WF CLI evita duplicar simulación, broker y costos en un script ad hoc del notebook.
  - Separar **métricas agregadas** (JSON `validation_reports/`, ADR-027) de **series temporales** (notebook) mantiene reportes livianos y trazables.
  - La tupla obliga opt-in explícito al detalle sin romper el contrato `StageResult` usado por GO/NO-GO.
  - Buy-and-hold SPY en la misma ventana y cash inicial es el piso de referencia mínimo para preguntar “¿valió la pena rebalancear?”.
- **Consecuencias**:
  - Paso 5 no sustituye paso 4: ventanas independientes miden estabilidad OOS; la corrida continua muestra compounding y costos acumulados en un solo capital.
  - Sharpe en el notebook es **exploratorio** (retornos diarios simples × √252); no reemplaza `rpt_kpi.v1` ni el gate OOS.
  - Para comparar `rebalance_rule` distintas, el notebook inyecta la regla en **copia** del `policy_doc` (no muta el YAML commiteado).
  - `daily_equity` refleja sleeve largo, no equity total del portfolio (coherente con el scope del motor largo).
- **Alternativas consideradas**:
  - **Re-simular solo en el notebook**: descartada — riesgo de drift respecto al stage y de costos distintos.
  - **Incluir `daily_equity` en `long_engine_wf_*.json`**: descartada en v1 por tamaño de artefacto y mezcla de responsabilidades con ADR-027.
  - **Función separada `run_long_engine_stage_with_details`**: descartada — duplicaría firma y lógica; un flag es suficiente.
- **Archivos**: `validation/stages/long_engine.py`, `validation/runner.py`, `validation/wf_runner.py`, `notebooks/wf_long_comparison.ipynb`, `tests/test_validation_long_engine.py`, `tests/test_validation_runner.py`, `tests/test_wf_runner.py`, `README.md`, `docs/project-overview.md`

---

## ADR-047 — Universo AR dinámico (Merval + CEDEAR), overlay de holdings y presupuesto IOL

- **Fecha**: 2026-05-15
- **Estado**: aceptada
- **Contexto**: El sleeve corto AR necesita liquidez realista sin inflar llamadas a IOL ni divergir entre “lo que se descarga” y “lo que el ledger sigue marcando”. Un whitelist estático no replica rotación de volumen; ignorar posiciones abiertas fuera del top rompe datos para stops y MTM.
- **Decisión**:
  - **Modelo híbrido**: candidatos en YAML (`whitelist_ar.yaml`, `whitelist_cedear.yaml`) + selección dinámica por volumen en ventana `volume_window_trading_days`, con targets `merval_top_n` / `cedears_top_n`.
  - **Fórmula de ranking (determinística)**: para cada candidato, sumar volumen en los últimos *N* días de barras disponibles (cola temporal ordenada); orden global `(−sum_volume, −avg(close×volume), symbol)` para empates por liquidez y ticker.
  - **Fallback**: si no corresponde refrescar (cadencia semanal/mensual según policy), si el **tope mensual hard** bloquea dinámica, o si el **job** agota `max_calls_per_job`, no se recalcula el ranking en esa corrida y se usa **último snapshot** en `universe_snapshots`; si no hay snapshot, **whitelist estática** legacy (`inline_ar` ∪ stocks AR).
  - **Overlay de holdings**: la lista efectiva de símbolos AR para ingesta OHLCV es `merge_fetch_universe(top_merval, top_cedear, open_ar_positions)` (orden lexicográfico, dedup). Las posiciones AR abiertas se obtienen de replay de fills en `MarketDB` en fetch diario y del ledger en el runner corto.
  - **Barras vs señales**: misma resolución base (`symbols_ar_bars`) para whitelist operativa; en modo dinámico `ar_signal_symbols` restringe el universo pasado a `compute_signal_candidates` al top de liquidez persistido, sin perder barras de holdings fuera del top.
  - **Metering / guardrails**: cada llamada IOL exitosa contabiliza `token`, `refresh`, `history` o `universe_volume` (`data/iol_api_meter.py` + `increment_iol_api_usage`). El total mensual incluye los cuatro contadores; por encima del umbral soft se degrada cadencia (rebalanceo efectivo mensual dentro del mes); por encima del hard no se ejecuta selección dinámica hasta el siguiente mes contable.
- **Por qué**: una sola fuente de verdad para fetch y corto reduce drift operativo; el overlay de holdings acota sorpresas de datos en posiciones reales; presupuesto explícito evita incidentes de rate/costo y fuerza degradación auditable.
- **Consecuencias**:
  - Mayor complejidad en `scripts/fetch_daily.py` y dependencia de tablas `universe_snapshots` / `iol_api_usage`.
  - Tests de comportamiento en `tests/test_universe_selector.py`, `tests/test_fetch_daily_universe_resolution.py` (cadencia, `monthly_hard_cap`, `aborted_job_budget`), `tests/test_iol_api_meter.py`, `tests/test_short_term_day_runner.py`; trazabilidad de fetch en **ADR-049**.
- **Alternativas consideradas**:
  - **Solo whitelist estática**: descartada — no captura liquidez cambiante en BYMA/CEDEARs.
  - **Ranking con fallback Byma/yfinance**: descartada para selección — distorsiona métricas respecto del venue operativo IOL.
  - **Incluir holdings en pool de señales**: descartada — ensancha entradas tácticas; se prefiere mantener datos sin ampliar candidatos de entrada.
- **Archivos**: `config/policy.v1.yaml`, `config/policy.v1.schema.json`, `data/universe_selector.py`, `data/iol_api_meter.py`, `data/connectors/ar_connector.py`, `data/storage.py`, `scripts/fetch_daily.py`, `core_sim/short_term_day_runner.py`, tests citados, `README.md`, `docs/project-overview.md`.

---

## ADR-048 — Motor largo multi-mercado: calendario AR, BYMA pesos y whitelist CEDEAR (colisión SPY)

- **Fecha**: 2026-05-15
- **Estado**: aceptada (extiende **ADR-017** en calendario/universo del largo; **ADR-045** sigue vigente en la intención semanal del rebalanceo — el default del repo pasa a régimen **AR semanal**)
- **Contexto**: El sleeve largo estaba modelado como core US + satélite US con `us_sessions` y `rebalance_rule` solo `first_us_trading_day_of_*`. Para operar el **70 % largo en pesos (BYMA)** con **CEDEAR** como satélite — p. ej. `SPY` como proxy de índice — hacía falta: (1) reglas de rebalanceo sobre **días hábiles AR**, (2) intents con `market: AR`, (3) OHLCV/`calendars` en **XBUE**, y (4) resolver la **colisión de ticker**: `SPY` aparece como ETF en `whitelist_us.yaml` (merge global → `US`) y como CEDEAR en `whitelist_cedear.yaml` (operación local).
- **Decisión**:
  1. **Policy + schema**: `rebalance_rule` admite `first_ar_business_day_of_calendar_week` y `first_ar_business_day_of_calendar_month`; `satellite_markets` admite `"US"` o `"AR"` (lista de un elemento). `config/policy.v1.yaml` de ejemplo: largo AR semanal, `satellite_markets: [AR]`, líneas **GGAL / PAMP / SPY** con pesos que suman 1.0 en el sleeve. `whitelist_cedear.yaml` incluye explícitamente **SPY** para alinear con la línea satélite.
  2. **Engine (`core_sim/long_term_engine.py`)**: firma basada en `calendar_sessions` (US o AR según regla); `long_sleeve_trade_market(config)` → `US`/`AR`; validación cruzada `rebalance_rule` ↔ `satellite_markets` (reglas US exigen `[US]`; AR exigen `[AR]`).
  3. **Runner largo (`core_sim/long_term_monthly_runner.py`)**: el contexto espera `ar_business_days` o `us_sessions` según política (o derivación desde `TradingCalendarStore`). **Whitelist operativa del largo AR**: intersección de los símbolos declarados en `long_term_engine` con la unión de listas **`whitelist_ar_file` ∪ `whitelist_cedear_file`**, sin usar solo `load_merged_whitelist` para AR — así **SPY CEDEAR** puede operarse en el largo aunque el merge corto etiquete `SPY` como US.
  4. **Paper-live (`scripts/run_paper_live.py`)**: `_build_long_pipeline_context` inyecta `ar_business_days` además de `us_sessions` cuando existe `calendar_store`. Tras el pipeline **corto**, si el largo está activo y el calendario del largo es AR, **`_overlay_ar_long_sleeve_bars_from_db`** escribe sobre una **copia** de `daily_bars` los OHLCV **XBUE** de cada símbolo de `long_term_engine` (MTM final y ejecución del largo usan esa copia).
  5. **Stage validación (`validation/stages/long_engine.py`)**: si la regla es AR, proyecto de fechas efectivas vía tabla `calendars` **XBUE** y barras `_load_daily_bars_for_day(..., venue=XBUE)`. **No** se requiere calendario **XNYS** en la DB para que el stage corra en política AR. El **`PaperBrokerSim`** del stage usa `CostModel` con **una sola clave** de mercado (`AR` o `US`) según `long_sleeve_trade_market`, leyendo `policy["markets"]` con defaults alineados a paper-live (`min_spread_bps` 0.5 si no viene en YAML). Opcionalmente se pasa **`TradingCalendarStore.from_yaml`** a `create_long_term_monthly_backtester` si existe `config/calendars/trading_days.v1.yaml`.
  6. **Documentación**: `POLICY.md` §10 y tablas relacionadas sincronizadas con YAML (sleeve en pesos, calendario AR, variante US documentada como alternativa soportada en schema/código).
  7. **Regresión (audit fase 1, tests)**: cobertura en `tests/test_long_term_engine.py` (rebalance mensual AR), `test_long_term_monthly_runner.py` (métrica `is_long_rebalance_day`, intents SPY con `market: AR`), `test_validation_long_engine.py` (stage con DB **solo XBUE**, fills SPY con `market: AR`), `test_policy_yaml.py` (`satellite_markets: [AR]`, SPY en `whitelist_cedear`).
- **Por qué**:
  - Un solo mapa `symbol → market` no puede representar bien el mismo ticker en NYSE vs panel bCBA sin romper corto US o largo AR.
  - El largo debe auditar contra listas reguladas de liquidación/local y CEDEAR, no contra el etiquetado del merge destinado al pipeline corto.
- **Consecuencias**:
  - Nueva dependencia para el stage informative: debe existir calendario **XBUE** en DB cuando se valida política AR; sin filas AR, el stage puede omitirse por `insufficient_calendar_days`.
  - Quien cambie símbolos en `long_term_engine` debe asegurarlos en **`whitelist_ar` o `whitelist_cedear`**; si no, el motor aborta con `symbol_not_whitelisted`.
  - Tests y fixtures actualizados: `tests/test_long_term_engine.py`, `tests/test_long_term_monthly_runner.py`, `tests/test_validation_long_engine.py` (XBUE + `upsert_calendars`; DB sin XNYS; overlay y costos cubiertos en la misma suite), `tests/test_policy_yaml.py`, `tests/test_run_paper_live.py` (overlay XBUE sobre merge-US para líneas del largo).
- **Alternativas consideradas**:
  - **Forzar SPY exclusivamente US o exclusivamente AR en el merge global**: descartada — rompe corto largo combinado en paper-live con el mismo ticker en dos venues conceptuales.
  - **Ticker distinto para CEDEAR vs ETF** (p. ej. `SPYD`): descartada por ahora — fricción operativa en IOL y en datos; el diseño por archivos separados evita renombrar.
  - **Solo mensual AR**: descartada en el default repo — se mantiene cadencia semanal (**ADR-045**) para latencia de drift y guardrail diario largo.

---

## ADR-049 — Trazabilidad de ingesta OHLCV en `fetch_log` y atribución de fuente (IOL / Byma / yfinance)

- **Fecha**: 2026-05-16
- **Estado**: aceptada (Fase 2 auditoría IOL; complementa **ADR-021** y **ADR-047**)
- **Contexto**: El pre-gate y el paper-live dependen de OHLCV reales, pero no había registro persistido por símbolo/rango de **qué proveedor** respondió, si hubo **fallback** IOL→Byma, ni conteos auditables. Sin eso, el notebook de diagnóstico y la revisión de calidad IOL quedaban acoplados a listas hardcodeadas y logs efímeros.
- **Decisión**:
  1. **Tabla existente `fetch_log`** (`data/storage.py`): una fila por intento de fetch por símbolo en el job diario, con `symbol`, `venue` (`XNYS` / `XBUE`), `status`, `source`, `skip_reason`, `extra` (JSON).
  2. **Taxonomía única** en `data/fetch_trace.py`: `status` ∈ `ok` | `skip` | `error`; `skip_reason` estandarizado (`empty_data`, `connector_returned_none`, `fallback_used`, `max_retries_exceeded`, `credentials_missing`, `budget_exhausted`, `data_error`, `unexpected_error`); fuentes `iol`, `byma`, `yfinance`, `mixed`.
  3. **Puerta única de persistencia**: `persist_fetch_trace()` → `MarketDB.log_fetch()`; instrumentados `data/fetcher.py` (US + AR), `data/connectors/ar_connector.py` (`fetch_ar_ohlcv_with_trace`) y `scripts/fetch_daily.py` (pasa `iol_only` desde env `FETCH_IOL_ONLY`).
  4. **Atribución en `extra`**: `provider`, `iol_only`, `attempts`, `start_date`, `end_date`, `rows`, `rows_by_source` (conteo de barras por proveedor), `partial_fallback`, `effective_source`.
  5. **Fallback parcial AR (MVP)**: si IOL devuelve barras pero faltan sesiones respecto del calendario **XBUE** explícito (`expected_dates` desde `fetch_and_store`), se consulta Byma y se hace **merge por fecha** (IOL gana en colisión). Solo se activa cuando el fetcher pasa calendario; sin `expected_dates` (p. ej. ranking en `universe_selector`) se mantiene el comportamiento previo (éxito IOL = retorno inmediato).
  6. **Fuera de alcance v1 (fase 2.1 opcional)**: metadato de origen **por barra** en tabla `ohlcv` y migración asociada — no implementado; la auditoría diaria queda a nivel job en `fetch_log`.
- **Por qué**: observabilidad reproducible en SQLite (misma DB que paper-live), sin rediseñar `ohlcv`; el notebook de pre-gate puede medir tasa de éxito/fallback y símbolos problemáticos sin depender de `WHITELIST_SYMBOLS` fijo.
- **Consecuencias**:
  - Cada corrida de `fetch_daily.py` appendea filas en `fetch_log` (crecimiento acotado por símbolos × corridas; no reemplaza OHLCV).
  - Métricas de calidad IOL deben leer `fetch_log`, no inferir solo desde barras almacenadas.
  - **Regresión de trazabilidad (paso 4, Fase 2)** — tests por comportamiento observable (sin red):
    - `tests/test_fetch_trace.py`: atribución `mixed` / `rows_by_source` en helpers puros.
    - `tests/test_data_ar_connector.py`: éxito IOL (`provider`/`source`/`status=ok`); `iol_only` sin credenciales → `credentials_missing` y sin yfinance; fallback Byma tras agotar IOL; merge parcial IOL+Byma; budget job (`IolJobBudgetExhausted`) → `budget_detail` en `extra` + fallback Byma, o re-raise si `iol_only`.
    - `tests/test_data_fetcher.py` (`TestFetchLogPersistence`): cada símbolo US/AR llama `log_fetch` con `source`, `skip_reason`, `provider` e `iol_only` en `extra` (éxito IOL, fallback `mixed`, skip US `max_retries_exceeded`).
    - `tests/test_data_storage.py`: round-trip SQLite de columnas `fetch_log` + JSON `extra` (`provider`, `iol_only`, `rows_by_source`, `effective_source`).
    - `tests/test_fetch_daily_universe_resolution.py`: `monthly_hard_cap` y `aborted_job_budget` en `universe_report` (sin fetch de red; presupuesto IOL a nivel universo, complementa el budget por símbolo del conector).
  - **Matiz `budget_exhausted`**: la constante `SKIP_BUDGET_EXHAUSTED` está en la taxonomía; hoy el conector ante `IolJobBudgetExhausted` registra `budget_detail` en `extra` y, si no es `iol_only`, continúa con Byma (`skip_reason=fallback_used` si hay datos). Con `iol_only=True` propaga la excepción (el fetcher puede persistir `unexpected_error`). Unificar `skip_reason=budget_exhausted` queda como mejora opcional si el notebook lo exige.
- **Alternativas consideradas**:
  - **Columna `source` en `ohlcv` por barra**: descartada en v1 — migración y backfill más costosos; reservada como fase 2.1.
  - **Solo logs estructurados sin DB**: descartada — no alimenta notebook ni SQL en `market.db` de paper-live.
  - **Merge parcial siempre por días hábiles inferidos (lun–vie)**: descartada como default — falsos huecos en feriados AR; se usa calendario XBUE del fetcher cuando aplica.
- **Archivos**: `data/fetch_trace.py`, `data/fetcher.py`, `data/connectors/ar_connector.py`, `data/storage.py`, `scripts/fetch_daily.py`, `tests/test_fetch_trace.py`, `tests/test_data_fetcher.py`, `tests/test_data_ar_connector.py`, `tests/test_data_storage.py`, `tests/test_fetch_daily_universe_resolution.py`, `tests/test_data_integration.py` (mocks `fetch_ar_ohlcv_with_trace`).

---

## ADR-050 — Incidente paper-live CI (may–jun 2026): secretos GitHub, F3, feriados y conflictos LFS

- **Fecha**: 2026-06-02
- **Estado**: aceptada
- **Contexto**: El workflow `paper_live_daily.yml` falló de forma continua desde 2026-05-26 tras el último run verde (2026-05-25). Cadena observada en logs de Actions:
  1. `IOL_USER` / `IOL_PASS` **vacíos en GitHub** (credenciales solo en variables de entorno locales de Windows) → `iol_credentials_missing` o fetch AR degradado.
  2. Sin barras del día → `No OHLCV bars found for YYYY-MM-DD` y abort del catch-up.
  3. Varios días sin snapshot → **F3** (`gap > 3` días hábiles, `exit 2` en `run_paper_live.py`).
  4. Día **2026-05-25** (feriado AR) sin barras hacía fallar el bloque aunque el resto del rango fuera recuperable.
  5. Al hacer `git pull` en `paper-live-data`, conflicto en **puntero LFS** de `data/market.db` (`<<<<<<<` dentro del archivo puntero, no mergeable como texto).
  6. Tras configurar secrets en GitHub: login IOL OK (`POST /token` 200) pero **serie histórica HTTP 401** (`iol_unauthorized`); el job sigue con fallback Byma/yfinance.
- **Decisión**:
  1. **Secretos obligatorios en CI**: `IOL_USER` y `IOL_PASS` deben existir en **Settings → Secrets and variables → Actions** del repo. Variables locales (`setx`, panel de Windows) **no** alimentan GitHub Actions. Validación: `python scripts/diagnose_iol_auth.py` en local; en CI, revisar que el step Fetch muestre `IOL_USER: ***` (no vacío).
  2. **Política F3** (sin cambio de umbral): máximo **3** días hábiles de catch-up por corrida; si `len(gap_days) > 3` → `exit 2` e intervención manual. Recuperación: varios `workflow_dispatch` con input `date` apuntando al **último día de cada bloque** de ≤3 días (p. ej. `2026-05-19`, `2026-05-22`, `2026-05-27`, `2026-06-01`), o el equivalente local + `git push` a `paper-live-data`.
  3. **Feriados / sin barras**: en `run_catch_up`, si un día del gap no tiene ninguna barra en whitelist, **registrar warning y continuar** con el siguiente día (no `raise RuntimeError` que aborta todo el rango). El snapshot de ese día no se crea; el siguiente run puede reintentar si llegan datos.
  4. **Conflictos LFS en `data/market.db`**: resolver el puntero con `git checkout --ours data/market.db` (mantener DB local reconstruida) o `--theirs` (mantener remoto), luego `git add data/market.db` y commit de merge. **No** editar a mano marcadores `<<<<<<<` dentro del puntero LFS.
  5. **Backfill de OHLCV previo al catch-up**: si la DB quedó vieja, correr `python scripts/fetch_daily.py --lookback 120 --db data/market.db` antes de `run_paper_live.py` en bloques F3-safe.
  6. **IOL 401 en histórico**: documentado como incidente conocido; operación diaria puede seguir en verde vía fallback. Seguimiento: permisos de cuenta IOL / soporte API; no bloquear CI mientras `fetch_log` y fallback sean aceptables para paper.
- **Por qué**: separar causas (secretos vs F3 vs feriado vs LFS) evita “arreglar” solo el síntoma; F3 protege contra catch-up masivo no auditado; saltar feriados evita un solo día no operable que tumbe una semana de recuperación.
- **Consecuencias**:
  - Run de verificación 2026-06-02 (`workflow_dispatch` #26826413712): **success**, mensaje `No gap — target day 2026-06-01 already processed`, commit LFS `be72f1a` en `paper-live-data`.
  - Operadores deben **mergear `main` → `paper-live-data`** para que el cron use el fix de feriados en `run_paper_live.py`.
  - Rotar contraseña IOL si estuvo expuesta en logs locales de diagnóstico.
- **Alternativas consideradas**:
  - **Subir F3 a 10 días en CI**: descartada — debilita control operativo; mejor dispatch manual en tandas.
  - **Forzar `FETCH_IOL_ONLY` en workflow**: descartada mientras histórico devuelva 401 — tumbaría el job entero.
  - **Resolver conflicto LFS fusionando binarios a mano**: descartada — Git LFS no mergea SQLite; elegir `--ours` o `--theirs` explícitamente.
- **Archivos**: `.github/workflows/paper_live_daily.yml`, `scripts/run_paper_live.py`, `scripts/diagnose_iol_auth.py`, `docs/project-overview.md`, `docs/complicaciones-tecnicas.md`, `README.md`, `AGENTS.md`, `POLICY.md` §15, `CHANGELOG.md`
- **Ver también**: **ADR-040** (modelo branches + workflow), **ADR-049** (`fetch_log` / fallback), `docs/complicaciones-tecnicas.md` (§1, §4, runbook operativo)

---

## ADR-051 — Valuación resiliente a huecos de datos en `mark_to_market` (carry-forward)

- **Fecha**: 2026-06-02
- **Estado**: aceptada
- **Contexto**: `PortfolioLedger.mark_to_market` valúa **todas** las posiciones abiertas llamando, por símbolo, a `_extract_close`, que lanzaba `ValueError: missing close price for symbol {sym}` cuando faltaba la barra del día. Un único hueco de datos (ej. `TXAR` en pre-gate corto) abortaba **toda** la corrida de `run_validation_wf`, impidiendo medir la calidad de los motores. El crash venía de la valuación (MTM de cartera), no del broker: las órdenes solo se generan para símbolos presentes en `daily_bars`.
- **Decisión**: Reemplazar `_extract_close` por `_resolve_mark_price(symbol, position, daily_bars) -> (precio, is_stale)` con prioridad: (1) close válido `>0` del día → fresco, actualiza `self._last_mark[symbol]`; (2) último mark conocido (carry-forward) → `stale`; (3) `avg_cost` de la posición → `stale` (nunca se vio precio de mercado). Nunca valúa a `0` ni crashea. El snapshot de `mark_to_market` ahora incluye `stale_marks: list[str]` y un flag `stale: bool` por posición.
- **Por qué**: Un dato faltante **no es un dato cero**. Crashear tira abajo la medición; valuar a cero corrompe equity, drawdown y retornos (la posición “desaparece”). El carry-forward es el comportamiento estándar de sistemas de cartera (stale price). El flag `stale` mantiene el evento **observable** para la capa de calidad de datos sin esconderlo.
- **Consecuencias**:
  - `mark_to_market`/`update_day` ya no lanzan por barras faltantes; la validación sobrevive a huecos.
  - Nuevo contrato de snapshot: `stale_marks` + `positions[sym]["stale"]`. Consumidores existentes (paper_broker, day_runner, pre_gate, walk-forward, validation_runner) verificados sin cambios de contrato.
  - `stale_marks` queda disponible pero **no** cableado a `halt_on_data_quality` (decisión de política pendiente: cuándo un MTM stale debe frenar operación).
- **Alternativas consideradas**:
  - **Valuar a 0 si falta barra**: descartada — corrompe equity/DD/retornos; la peor opción.
  - **Mantener el crash**: descartada — un hueco en un símbolo no debe invalidar toda la corrida de evaluación.
  - **Cablear `stale_marks` a halt inmediato**: pospuesta — requiere política (¿1 stale frena? ¿umbral?); por ahora solo observable.
- **Archivos**: `core_sim/ledger.py`, `tests/test_ledger.py`
- **Ver también**: **ADR-018/019** (ledger paper-first, `mark_to_market`), **ADR-050** (incidente CI: feriados sin barras, IOL 401)

---

## ADR-052 — Señal sin mezcla de monedas: lectores de `ohlcv` honran el venue del market tag (`data/venue_policy.py`)

- **Fecha**: 2026-06-03
- **Estado**: aceptada
- **Contexto**: Los lectores de `ohlcv` que reconstruyen series por símbolo (medición de señal y pre-gate corto) hacían `SELECT ... WHERE symbol = ? AND ts BETWEEN ...` **sin filtrar venue**. Para los símbolos *dual-listed* —presentes en `ohlcv` tanto en **XNYS/US** (USD) como en **XBUE** (ARS)— eso colapsaba dos monedas distintas en una misma serie con semántica *last-write-wins* por timestamp. El retorno entre un cierre USD y un cierre ARS del mismo ticker es físicamente imposible: el caso testigo fue **KO** con "+30000%" (22519 ARS / 74 USD − 1). El bug afectaba a **13 símbolos** de la whitelist activa (AAPL, GGAL, IWM, JNJ, JPM, KO, MELI, MSFT, PG, QQQ, SPY, WMT, XOM), por lo que contaminaba **dos capas a la vez**: el sim/KPIs **pre-gate** y la **capa de medición de señal** (`reporting/signal_ic.py`). El salto artificial USD↔ARS inflaba el edge aparente sin que ningún test lo detectara (los unit tests usaban un único venue por símbolo).
- **Decisión**:
  - Crear `data/venue_policy.py` como **fuente única de verdad** de qué venue corresponde a cada market tag:
    - `venues_for_market("US") -> ("XNYS", "US")`: ambos en USD; `"US"` es legacy de la migración **ADR-030/037** y queda como fallback con menor precedencia que `XNYS`.
    - `venues_for_market("AR") -> ("XBUE",)`: ARS, serie única.
    - `pick_venue_bar(market, bars_by_venue)`: colapsa los venues de un símbolo-día a la barra correcta del market tag, o `None` si no existe (omite el día; **nunca** sustituye con otra moneda).
  - Los **tres** lectores filtran ahora por el venue que matchea el `market` tag que ya asigna `load_merged_whitelist` (precedencia US definida en **ADR-043**), sin hardcodear venues: todos pasan por el helper.
    - `reporting/signal_ic.py` (`bars_by_date_from_db`)
    - `scripts/run_short_term_pre_gate.py` (`_bars_from_db`)
    - `validation/stages/short_pre_gate.py` (`_bars_from_db`)
  - **Regla dura**: el venue se fija **por SERIE, no día por día**. Si falta la barra del venue correcto un día, ese día se **OMITE**; nunca se rellena con la barra del otro venue (un fallback día-a-día recrearía exactamente el bug).
  - **Decisión de arquitectura asociada**: la señal/análisis de los dual-listed se computa en **USD (XNYS)** para que el CCL / tipo de cambio no contamine el momentum; los **AR-nativos** (Merval) usan **ARS (XBUE)**, única serie. **No** se re-etiquetó nada: los tags US existentes ya son correctos para la señal.
- **Por qué**:
  - Un dato en otra moneda **no es el mismo dato**: mezclar USD y ARS en una serie produce retornos imposibles que corrompen la medición de edge y el backtest, no un ruido tolerable.
  - Centralizar en `data/venue_policy.py` evita que cada lector reinvente (y desincronice) la regla venue↔moneda; es el mismo argumento de fuente única de los conectores en **ADR-037**.
  - Computar la señal en USD aísla el momentum del ruido cambiario: el CCL puede moverse fuerte sin que el instrumento subyacente lo haga, y no queremos que ese movimiento se cuele como "señal".
- **Consecuencias**:
  - Contaminación por moneda **eliminada** en ambas capas (sim/KPIs y medición de señal).
  - Al limpiar, el **IC de señal a h=1 cayó de 0.146 (sucio) a 0.087 (limpio)**: ~40 % del edge aparente era el salto artificial USD↔ARS, no momentum real.
  - La limpieza reveló que la **cross-section es muy fina** (mediana ~1 símbolo/día; solo 89/278 días con ≥5 nombres): la medición de señal queda **inconclusa por falta de breadth**, problema separado a resolver ampliando universo (no es un defecto de este fix).
  - La **ejecución en pesos sobre el CEDEAR** queda como **paso POSTERIOR, no implementado**: requerirá mapeo US→cedear + ratio de conversión + precio ARS + valuación en pesos. Este ADR cubre solo la señal/medición.
  - Tests nuevos: `tests/test_venue_policy.py`, `tests/test_signal_ic_venue_filter.py`, `tests/test_validation_short_pre_gate_venue.py`.
  - Capa de medición offline ampliada (sin ADR separado): `reporting/signal_ic.py` (IC, hit rate@K, quantile spread), `reporting/scenario.py` + `scripts/run_scenario.py` (what-if paramétrico), `reporting/data_quality_envelope.py`, `scripts/run_signal_ic_now.py`.
  - Suite completa del repo: **601** tests recolectados (`pytest --collect-only`, jun 2026).
- **Alternativas consideradas**:
  - **ARS-first: re-etiquetar todo a XBUE y operar en pesos**: descartada — genera ripple innecesario en allocator y calendario (geo 20/80, sesiones), cuando para la **señal** los tags US ya son correctos; la ejecución en pesos es un paso futuro acotado, no un re-tag global.
  - **Fallback venue día-a-día (usar el otro venue si falta la barra del correcto)**: descartada — recrea exactamente el bug de mezcla de monedas que estamos eliminando.
  - **Purgar ahora el venue legacy `"US"` de la DB**: descartada — es higiene de datos aparte; `venues_for_market` ya lo tolera como fallback de menor precedencia sin mezclar moneda.
- **Archivos**: `data/venue_policy.py` (nuevo), `reporting/signal_ic.py`, `scripts/run_short_term_pre_gate.py`, `validation/stages/short_pre_gate.py`, `tests/test_venue_policy.py` (nuevo), `tests/test_signal_ic_venue_filter.py` (nuevo), `tests/test_validation_short_pre_gate_venue.py` (nuevo), `docs/complicaciones-tecnicas.md` (§6–7)
- **Ver también**: **ADR-030/037** (US→XNYS, código MIC), **ADR-043** (precedencia del market tag US en `load_merged_whitelist`), **ADR-051** (carry-forward en ledger: hueco ≠ cero, misma filosofía de no inventar datos), **ADR-053** (breadth insuficiente revelado tras este fix)

---

## ADR-053 — Ampliación del universo (+10 símbolos diversificados por industria) para destrabar la medición de señal

- **Fecha**: 2026-06-03
- **Estado**: aceptada
- **Contexto**: La medición de señal limpia (**ADR-052**) reveló **breadth insuficiente** para evaluar un ranking cross-seccional: la cross-section mediana es de **~1 símbolo/día** y solo **89 de 278 días** alcanzan ≥5 nombres, lo que dejó el veredicto de la señal **inconcluso** (no es un defecto del fix de venue, es falta de amplitud). Además, las whitelists estaban **concentradas por sector**: el Merval pesaba en bancos (GGAL/BMA/SUPV) y energía (YPFD/PAMP/CEPU/TGSU2), y los CEDEARs pesaban en tech, con salud flaca (solo PFE) e industriales (solo BA). Con tan pocos nombres por día y sectores correlacionados, no hay cross-section sobre la cual rankear.
- **Decisión**: Ampliar el universo en **+10 símbolos**, diversificando deliberadamente por industria para ensanchar la cross-section:
  - **Merval (+5, market `AR`, venue XBUE/ARS)**: `CRES` (agro), `TECO2` (telecom), `LOMA` (construcción/cemento), `MIRG` (electrónica/industrial), `IRSA` (real estate).
  - **CEDEARs (+5, tag `US` para que la señal se compute en USD vía XNYS, registrados también en `whitelist_cedear` para ejecución futura en pesos)**: `V` (pagos), `UNH` (salud), `CAT` (industrial), `PEP` (consumo masivo), `NFLX` (streaming).
- **Por qué**:
  - No se puede rankear una lista de un elemento: la **amplitud del universo es prerequisito** de cualquier medición cross-seccional de señal. Ampliar es la palanca de menor riesgo para conseguir breadth.
  - Diversificar por industria (no agregar más bancos/energía) **descorrelaciona** la cross-section, que es justo lo que un ranking necesita para discriminar.
  - Etiquetar los CEDEARs como `US` mantiene coherencia con la decisión **#401 / ADR-052**: la señal de los dual-listed se computa en **USD (XNYS)** para que el CCL no contamine el momentum; el registro paralelo en `whitelist_cedear` deja lista la pata de ejecución en pesos sin forzarla ahora.
- **Consecuencias**:
  - Datos cargados para los 10 nuevos símbolos en el rango **2025-03-20 → 2026-06-02** (~297 filas AR, ~302 US), con **warmup antes del día 1** de la ventana de medición para que los indicadores arranquen calientes.
  - Coherente con **ADR-052** (señal dual-listed en USD); **no** se re-etiquetó nada existente.
  - **Pendiente (U2)**: re-correr la medición de señal sobre la cross-section completa con `scripts/run_signal_ic_now.py` y/o `scripts/run_scenario.py` para ver si la breadth mejora y el veredicto deja de estar inconcluso. Narrativa de la cadena de complicaciones #6→#8 en `docs/complicaciones-tecnicas.md`.
- **Alternativas consideradas**:
  - **Relajar los filtros del motor (p. ej. `p_min`, liquidez) en vez de ampliar el universo**: descartada como **primer paso** — tocar los filtros afecta el riesgo real de trading; es mejor medir primero sobre la cross-section completa U2 con un universo más ancho, y solo después considerar ajustes de motor con evidencia.
  - **Cargar ya la pata ARS de los CEDEARs**: descartada — la señal va en USD y la ejecución en pesos (mapeo US→cedear + ratio + precio ARS + valuación) es un **paso futuro acotado**, no un prerequisito de la medición.
- **Archivos**: `config/symbols/whitelist_ar.yaml`, `config/symbols/whitelist_us.yaml`, `config/symbols/whitelist_cedear.yaml`, `data/market.db`, `docs/complicaciones-tecnicas.md` (§8), `README.md`, `docs/project-overview.md`
- **Ver también**: **ADR-052** (señal sin mezcla de monedas; breadth insuficiente como problema separado), **ADR-043** (precedencia del market tag US en `load_merged_whitelist`), señal dual-listed en USD (decisión asociada en ADR-052)

---

## ADR-054 — Calendario paper-live obligatorio, YAML completo y separación del stub de tests

- **Fecha**: 2026-06-10
- **Estado**: aceptada
- **Contexto**: Auditoría técnica (jun 2026) identificó **C2**: `config/calendars/trading_days.v1.yaml` contenía solo **4 días** (fixture de unit tests en ruta de producción). Además, `run_paper_live.py` hacía `if cal_path.exists()` → si faltaba el archivo, `calendar_store=None` y `event_engine` asumía `is_us_session=True` siempre — guardrails de sesión/no-trade operaban en modo permisivo sin avisar. Simulaciones de defensa oral renombraron el YAML a `.defensa-bak` para evitar el stub, exponiendo ambos modos de fallo.
- **Decisión**:
  1. **YAML de producción completo**: `scripts/build_trading_days_yaml.py` genera `config/calendars/trading_days.v1.yaml` desde `pandas_market_calendars` (NYSE → XNYS, XBUE → BYMA), rango configurable (default 2024-01-01..2027-12-31).
  2. **Stub aislado**: fixture mínimo en `tests/fixtures/calendars/trading_days_stub.v1.yaml` para tests que fijan fechas (p. ej. 2026-04-15).
  3. **Fail-fast en paper-live**: `load_required_calendar_store()` lee `policy.calendar.source_of_truth`; ausencia o calendario vacío → `exit 1`. Flag explícito `--no-calendar` solo para tests/diagnóstico (con warning).
  4. **Golden replay (T0.2)**: `tests/fixtures/replay_golden/` + `tests/test_replay_golden.py` caracterizan `replay_ledger_from_fills` antes de cambios en persistencia de capital.
- **Por qué**: Un calendario incorrecto o ausente corrompe riesgo operativo en silencio; es preferible abortar que operar con flags de sesión inventados. Separar stub de `config/` evita repetir el incidente.
- **Consecuencias**:
  - CI paper-live y corridas manuales requieren el YAML commiteado; regenerar al extender horizonte.
  - Stages de validación offline (`long_engine`) siguen pudiendo omitir YAML si no existe — paper-live no.
  - Tests de integración usan calendario real o `--no-calendar` / stub explícito.
- **Alternativas consideradas**:
  - **Cargar calendario solo desde SQLite (`calendars` table)**: descartada como única fuente — el YAML versionado es el contrato ADR-007 alineado a policy.
  - **Mantener degradación silenciosa con default permisivo**: descartada — origen del hallazgo C2.
- **Archivos**: `scripts/build_trading_days_yaml.py`, `config/calendars/trading_days.v1.yaml`, `tests/fixtures/calendars/trading_days_stub.v1.yaml`, `scripts/run_paper_live.py`, `tests/test_run_paper_live.py`, `tests/test_replay_golden.py`, `config/README.md`, `README.md`, `AGENTS.md`, `docs/project-overview.md`, `docs/complicaciones-tecnicas.md`
- **Ver también**: **ADR-007** (fuente única calendario), **ADR-050** (feriados sin barras), **ADR-055** (auditoría T1.1–T1.4 persistencia + F3)

---

## ADR-055 — Auditoría paper-live T1.1–T1.4: persistencia de capital y gap F3 con calendario real

- **Fecha**: 2026-06-10
- **Estado**: aceptada
- **Contexto**: Auditoría técnica (jun 2026) sobre persistencia y catch-up de `run_paper_live.py` identificó tres hallazgos operativos además de **C2** (calendario stub, resuelto en **ADR-054**):
  - **C1 / T1.1**: replay podía reescribir capital silenciosamente — sin `portfolio_meta` bloqueando `starting_cash`/`currency`.
  - **C3 / T1.3**: `paper_snapshots.short_cash` se persistía como `cash × weights.short` en lugar de `ledger.short_cash`, incoherente con kill switch y bucket equity (**ADR-039**).
  - **H3 / T1.4**: el gate **F3** contaba lun–vie genérico; feriados US (p. ej. Memorial Day) generaban falsos `exit 2` y días AR-only (US cerrado, AR abierto) no contaban para catch-up.
- **Decisión**:
  1. **T1.1 `portfolio_meta`**: tabla SQLite + `ensure_portfolio_meta()` — primera corrida persiste CLI; siguientes validan; mismatch → `exit 1`. Default **3_000_000 ARS**.
  2. **T1.3 `short_cash`**: `persist_snapshot(..., short_cash=float(ledger.short_cash))` — attr directo del ledger, no proxy por peso.
  3. **T1.4 gap F3**: `compute_trading_days_gap()` con `TradingCalendarStore` cuenta días donde `is_us_session(d) OR is_ar_business_day(d)`. Calendario cargado **antes** del check F3. Fallback lun–vie solo con `--no-calendar`.
  4. **What-if cartera**: `scripts/run_whatif_sim.py` — sim 30/70 sobre copia aislada de DB; bypass F3 intencional para backtests multi-mes; no escribe en `paper_live` productivo.
- **Por qué**: Coherencia entre ledger, snapshots históricos y política operativa; F3 debe reflejar días en que **cualquier** sleeve puede operar (US/CEDEAR vía XNYS, panel AR vía XBUE).
- **Consecuencias**:
  - `POLICY.md` §15.1 y docs de runbook actualizados (ya no “solo lun–vie”).
  - Simulaciones históricas deben respetar cobertura XBUE (al jun 2026 termina 2026-06-02) y corporate actions CEDEAR pendientes.
- **Alternativas consideradas**:
  - **Solo sesiones US para F3**: descartada — omite días AR-only con operación local.
  - **Solo lun–vie**: descartada — origen del hallazgo H3.
- **Archivos**: `data/storage.py`, `scripts/run_paper_live.py`, `scripts/run_whatif_sim.py`, `tests/test_run_paper_live.py`, `tests/test_data_storage.py`, `POLICY.md`, `README.md`, `AGENTS.md`, `docs/project-overview.md`, `CHANGELOG.md`
- **Ver también**: **ADR-039** (persistencia fills/snapshots), **ADR-050** (runbook F3), **ADR-054** (calendario obligatorio)

---

## ADR-056 — Robustez del connector IOL: alias de campos + fallback Byma ante respuesta vacía

- **Fecha**: 2026-06-11
- **Estado**: aceptada
- **Contexto**: El workflow paper-live (run #27, 2026-06-11) traía data AR **solo por Byma**: IOL no aportaba una sola fila. Dos bugs encadenados en `data/connectors/ar_connector.py`:
  - **B1 — mapeo de campos**: `_normalize_iol` exigía las keys `fecha` y `volumen`, pero el endpoint `seriehistorica` real de IOL devuelve `fechaHora` y `volumenNominal`. Cada barra lanzaba `DataError "Missing keys"` → IOL devolvía `[]` para **todos** los símbolos AR.
  - **B2 — fallback solo ante error de red**: `fetch_ar_ohlcv_with_trace` caía a Byma únicamente cuando IOL fallaba por red (`result is None`). Si IOL respondía `200` con **lista vacía** (o `data_error` → `result == []`), retornaba `[]` **sin** consultar Byma. IOL no sirve varios CEDEARs (y algunos Merval como BMA/LOMA) en ese endpoint y devuelve `[]`; el connector aceptaba ese vacío y nunca probaba Byma, que **sí** tiene la serie.
- **Decisión**:
  1. **Alias por campo** en `_normalize_iol` (`_IOL_*_KEYS`, primer nombre presente gana): `fechaHora|fecha`, `volumenNominal|volumen`, `ultimoPrecio|cierre`, `apertura`/`maximo`/`minimo`. El volumen usa **solo** `volumenNominal|volumen` (cantidad de nominales), **nunca** `montoOperado` ($), para no corromper el notional `close × volume` del ranking de universo (**ADR-047**). El `DataError` ahora **lista las keys recibidas** → desajuste futuro autodiagnosticable.
  2. **Fallback ante vacío**: IOL `[]` (sin datos o `data_error`) ya no retorna temprano; cae al fallback Byma (salvo `iol_only`, que respeta su contrato). La atribución de fuente en `fetch_log` mantiene el fallback **visible** (**ADR-049**), evitando el enmascaramiento de la complicación #4.
- **Por qué**: IOL es **selectivo** sobre qué símbolos sirve en `seriehistorica`; el corte no es "CEDEAR vs Merval" (BMA y LOMA también caen vacíos). Un fallback robusto por símbolo es la solución correcta, no una lista de excepciones. Aceptar el vacío de la fuente primaria como respuesta final ocultaba datos que la secundaria tenía.
- **Consecuencias**:
  - Tras el fix (verificado en run_dispatch 2026-06-11): **40/41** símbolos AR con `source=iol`; AR-nativos (GGAL, YPFD, PAMP, ALUA, TXAR…) frescos hasta 2026-06-10 (`rows_by_source={byma:0, iol:3}`). El "corte XBUE 2026-06-02" era consecuencia de B1, no una limitación real.
  - Los CEDEARs (IOL vacío) ahora se rellenan por Byma; histórico ARS de CEDEAR disponible vía fallback.
  - Costo: una llamada Byma extra cuando IOL viene vacío. Aceptable frente a la pérdida de datos.
- **Alternativas consideradas**:
  - **Renombrar la key fija `fecha`→`fechaHora`**: descartada — frágil; si IOL cambia de nuevo, vuelve a romper. Los alias toleran ambos contratos.
  - **Whitelist de símbolos "IOL-no-sirve"**: descartada — IOL es selectivo y cambiante; mantener la lista a mano es deuda. El fallback por vacío lo cubre genéricamente.
  - **`montoOperado` como volumen**: descartada — corrompe el notional (es $, no nominales).
- **Archivos**: `data/connectors/ar_connector.py`, `tests/test_data_ar_connector.py`
- **Ver también**: **ADR-049** (fetch_log / atribución de fuente), **ADR-057** (lección de testing), complicaciones #3 y #12

---

## ADR-057 — Convención de testing: el test afirma el comportamiento deseado, no la suposición del código

- **Fecha**: 2026-06-11
- **Estado**: aceptada
- **Contexto**: Tres bugs de producción **pasaron CI en verde** porque el test fue escrito desde la **misma suposición equivocada** que el código:
  - **Mezcla de monedas (#6/ADR-052)**: los unit tests usaban un único venue por símbolo, así que la mezcla USD/ARS nunca aparecía en pruebas; el IC inflado (0.146) se veía sano.
  - **Mapeo de keys IOL (#3/ADR-056)**: el fixture `_IOL_PAYLOAD` usaba `fecha`/`volumen` — las keys que el código asumía, no las que devuelve la API (`fechaHora`/`volumenNominal`). Test verde, producción sin una fila de IOL.
  - **Fallback ante vacío (#12/ADR-056)**: dos tests **afirmaban `result == []`** ante IOL `data_error` — es decir, afirmaban el bug como si fuera el contrato deseado.
- **Decisión**: Convención obligatoria para tests nuevos y al tocar tests existentes:
  1. El test afirma el **comportamiento de negocio deseado** (qué debería pasar), no replica lo que el código hace hoy.
  2. Los **fixtures reflejan la realidad de la fuente externa** (contrato real de la API/feed), no la conveniencia del parser. Si no se conoce el contrato real, el test debe documentarlo como supuesto explícito y el error de runtime debe ser autodiagnosticable (volcar lo recibido).
  3. Ante un caso límite (vacío, error, dato faltante), el test afirma la **acción de recuperación esperada** (p. ej. "cae al fallback"), no el síntoma del bug (p. ej. "retorna vacío").
- **Por qué**: un test verde **no garantiza nada** si valida la suposición y no la realidad; da una falsa sensación de seguridad y deja pasar exactamente la clase de bug más cara (datos silenciosamente equivocados). Es deuda peor que la falta de test, porque **parece** cubierto.
- **Consecuencias**:
  - Al arreglar #3 y #12 se **reescribieron** los tests que codificaban el bug (de "afirma `[]`" a "afirma fallback Byma"); fixture `_IOL_PAYLOAD` corregido al contrato real.
  - Refuerza el criterio *smart-testing* (testear comportamiento, no implementación) ya usado en el repo.
- **Alternativas consideradas**:
  - **Solo subir cobertura**: descartada — cobertura sobre suposiciones equivocadas es ruido; el problema es la *intención* del assert, no la cantidad.
- **Archivos**: convención transversal; ejemplos en `tests/test_data_ar_connector.py`, `tests/test_signal_ic_venue_filter.py`
- **Ver también**: **ADR-052** (#6), **ADR-056** (#3, #12), `docs/complicaciones-tecnicas.md`

---

## ADR-058 — Simulador walk-forward de investigación: aportes mensuales + TWR (separado del gate)

- **Fecha**: 2026-06-13
- **Estado**: aceptada
- **Contexto**: Para entender la estrategia hacía falta (a) un modelo de capital más realista que "monto inicial y nunca más" — el usuario aporta plata todos los meses (DCA) — y (b) poder explorar ventanas walk-forward libres (p. ej. 120+60) sin tocar el gate congelado. Dos riesgos: los **aportes rompen las métricas** si se miden ingenuamente (un depósito se lee como ganancia gigante — misma familia de artefacto que #3/#6/#11), y aflojar el gate por conveniencia sería **p-hacking** (lo que el gate congelado previene, POLICY.md §13).
- **Decisión**: Separar **dos modos** explícitamente:
  1. **Modo investigación** (este ADR): `scripts/run_wf_research_sim.py` + `reporting/twr_walk_forward.py`. Corre el pipeline 30/70 día a día sobre una **copia aislada** (por defecto la backfilleada), con **aportes mensuales** (primer día hábil de cada mes el `starting_cash` crece y los motores despliegan la plata nueva). Métricas con **TWR** (time-weighted): cada aporte se **excluye de la base** antes de medir (`r_t = V_t/(V_{t-1}+C_t)-1`). Drawdown sobre el **índice TWR** (no sobre el equity, que los aportes esconden). MWR/TIR como secundario (experiencia real en pesos). Ventanas walk-forward **configurables** (default 120/60/30).
  2. **Modo compromiso** (gate, **ADR-041**): sigue **congelado** (252+60, pre-registrado). El simulador imprime y marca `mode: research` en todos sus outputs para que nadie confunda una corrida exploratoria con el gate.
- **Por qué**: el TWR es el estándar para medir habilidad de la estrategia con flujos de caja externos; sin él, los aportes inflan Sharpe y esconden drawdown. La separación research/gate permite explorar libremente **sin** erosionar la integridad anti-overfitting del gate. Si alguna vez se quiere un gate 120+60, el camino legítimo es `gate.v2` pre-registrado + ADR — no bajar el congelado tras ver resultados.
- **Consecuencias**:
  - Módulo `reporting/twr_walk_forward.py` puro y testeado (la corrección del TWR ante aportes se prueba sin correr el bot: un día de puro aporte con mercado plano da retorno 0, no un spike).
  - Reusa la valuación resiliente por venue nativo (`_resilient_snapshot`, fix de feriados AR) — el simulador no colapsa en feriados.
  - Vive en rama `research/wf-sim` (worktree); no se mezcla con el pipeline productivo.
- **Alternativas consideradas**:
  - **Bajar el gate a 120+60**: descartada — p-hacking; el gate se cambia con pre-registro, no por conveniencia.
  - **Medir con retorno crudo del equity**: descartada — los aportes lo envenenan (artefacto demostrado en tests).
  - **`montoOperado`/equity con aportes para drawdown**: descartada — esconde las pérdidas reales.
- **Archivos**: `reporting/twr_walk_forward.py`, `scripts/run_wf_research_sim.py`, `tests/test_twr_walk_forward.py`
- **Ver también**: **ADR-041** (gate OOS congelado), **ADR-051** (valuación resiliente), **ADR-057** (lección de testing)

---

## ADR-059 — Hallazgo: la estrategia es una apuesta concentrada a un solo factor (equity AR)

- **Fecha**: 2026-06-13
- **Estado**: aceptada (hallazgo + decisiones derivadas)
- **Contexto**: El simulador walk-forward de investigación (**ADR-058**) corrido sobre 360 días backfilleados (2025-01 → 2026-06, aportes 500k/mes, 120+60 paso 30) dio TWR acumulado **+24,75%** pero **NO pasa el agregado**: 3 de 7 ventanas OOS pasan, 4 fallan. El análisis del régimen mostró la causa raíz, no un detalle de implementación.
- **Hallazgo**:
  - El destino de la estrategia está **pegado a GGAL y PAMP** (las dos acciones AR del sleeve largo). Ventanas que fallan = selloffs de equity argentino (GGAL **-20,5%** ago-2025, **-19,0%** feb-2026); ventana que más rinde (+56% TWR, Sharpe 3,87) = rally de octubre-2025 (GGAL **+111%** en el mes).
  - **Concentración**: el largo es 70% del capital; core GGAL 42% + PAMP 43% = **85% del largo ≈ 60% del total** en dos nombres.
  - **Factor único**: GGAL (banco) y PAMP (energía) parecen diversificadas pero **caen juntas** en los selloffs (mismo factor: riesgo-país AR). La correlación destruye la diversificación aparente.
  - **Diversificadores insuficientes**: SPY (satélite, 15% del largo) fue el único que aguantó en meses malos (ago +2,6%, abr +10,4% mientras GGAL caía), pero su peso es muy chico para compensar. El sleeve corto US (30%) termina casi flat — no aporta retorno descorrelacionado.
  - Conclusión: **no es un sistema diversificado, es una apuesta direccional apalancada-en-criterio a equity argentino, con adornos.** El walk-forward lo expuso: -25% de drawdown en regímenes bajistas locales.
- **Decisiones derivadas**:
  1. **Mantener en paper**: el resultado **refuerza** el valor del gate congelado (**ADR-041**). Aflojarlo para "pasar" habría sido autoengaño; el gate dijo la verdad incómoda.
  2. **Antes de cualquier capital real**, trabajar tres frentes (ver backlog): bajar concentración del largo, diversificar el factor (más peso a riesgo global vía CEDEARs), y que el corto genere cobertura real o se reduzca su asignación.
- **Por qué (registrarlo)**: es el hallazgo de riesgo más importante del proyecto. Para defensa oral, demuestra criterio: entender *de qué depende* el retorno (y el drawdown) vale más que el número.
- **Alternativas consideradas**:
  - **Reportar solo el +24,75%**: descartada — esconde el perfil de riesgo; deshonesto.
  - **Aflojar el gate para que pase**: descartada — p-hacking (**ADR-057**, **ADR-041**).
- **Archivos**: análisis sobre `data/market_backfill.db` + `data/_sim/wf_research_report.json`; narrativa en `docs/project-overview.md` y `docs/complicaciones-tecnicas.md` (#13).
- **Ver también**: **ADR-058** (simulador), **ADR-041** (gate), **ADR-053** (ampliación de universo — primer paso de diversificación)

---

## ADR-060 — Sleeve largo diversificado 50% AR + 50% global (variante en evaluación)

- **Fecha**: 2026-06-13
- **Estado**: propuesta — **evaluación positiva** (drawdown -25,7%→-11,5%, retorno +24,75%→+39,46%); NO promovida al default todavía (sigue 5/7, falta cubrir el régimen global con el corto).
- **Contexto**: **ADR-059** mostró que el sleeve largo concentraba ~60% del capital en GGAL+PAMP, mismo factor (riesgo-país AR), con drawdowns de -25% en selloffs locales. Decisión de rediseño para romper la concentración mono-factor.
- **Decisión** (variante de investigación, `config/policy.research_diversified.v1.yaml`):
  1. **50% AR + 50% global**, mínimo **3 títulos por lado**, sectores distintos:
     - AR (riesgo-país): **GGAL** (banco) 0,167, **PAMP** (energía) 0,167, **TXAR** (acero/materiales) 0,166.
     - Global (riesgo mundial vía CEDEAR en ARS): **SPY** (broad) 0,167, **QQQ** (tech) 0,167, **KO** (consumo defensivo) 0,166.
  2. Las 6 líneas van en `core_lines` (satélites vacío). Se amplió el tope del motor de **3 a 8 core lines** (`validate_long_term_engine_config`, mínimo se mantiene en 2 para no romper el default).
  3. **Medir, no asumir** la diversificación: `scripts/measure_correlation.py` calcula la matriz de correlación de retornos (XBUE/ARS).
- **Evidencia de correlación** (2025-01 → 2026-06, 347 días):
  - GGAL–PAMP **0,77** (confirmado: mismo factor).
  - **AR ↔ global: 0,02** (prácticamente nulo → el bloque global SÍ diversifica el factor).
  - KO–GGAL **-0,31** (KO se mueve a contramano de GGAL: cobertura real).
  - Observación: SPY–QQQ **0,96** (casi idénticos; QQQ aporta poco sobre SPY — candidato a revisar).
- **Por qué (metodología)**: la variante se evalúa con el walk-forward de investigación (**ADR-058**) **antes** de promoverla al default. Cambiar el default rompería ~8 tests que asumen la cartera de 3 nombres; promover sólo si el drawdown de las ventanas malas baja de forma material. El gate congelado (**ADR-041**) no se toca.
- **Resultado del walk-forward** (concentrada vs diversificada, mismo período/aportes):
  | Métrica | Concentrada (baseline) | Diversificada |
  |---------|------------------------|---------------|
  | TWR acumulado | +24,75% | **+39,46%** |
  | Ventanas OOS que pasan | 3/7 | **5/7** |
  | Peor drawdown OOS | -25,7% | **-11,5%** |
  | Ventanas 0/1 (selloff AR mid-2025) | -25,7% DD | **+8,2% / +3,1%** (positivas) |
  - **Veredicto**: la diversificación **cumplió** — cortó el peor drawdown a la mitad (-25,7% → -11,5%) y subió el retorno. Las ventanas que antes morían en el selloff argentino ahora aguantan (el bloque global descorrelacionado, corr 0,02, cubrió).
  - **Matiz honesto**: sigue sin pasar el agregado (5/7). Las ventanas **4 y 5** (dic-2025 → abr-2026) todavía fallan (-10,7% / -2,1% TWR, DD ~-11,5%): fue un período donde cayeron **AR y global a la vez** (riesgo global, no solo país), que la diversificación AR/global no cubre. Para esos regímenes haría falta el tercer frente: que el **sleeve corto cubra de verdad** (hoy no aporta retorno descorrelacionado).
  - **Bug encontrado y corregido en el camino**: QQQ no estaba en `whitelist_cedear.yaml` → el motor largo abortaba el ciclo (`symbol_not_whitelisted`) y no invertía. Agregado a la whitelist.
- **Consecuencias**:
  - Producción (`policy.v1.yaml`) **intacta** mientras se evalúa. Promoción futura = ADR de seguimiento + actualizar tests dependientes + (opcional) subir el mínimo de core lines a 3.
  - Requiere QQQ en XBUE (backfilleado en la DB de investigación).
- **Alternativas consideradas**:
  - **Cambiar el default directo**: descartada — romper tests + promover sin medir es el anti-patrón que venimos evitando.
  - **Diversificar por sector dentro de AR solamente**: descartada — sectores AR comparten factor país (corr alta); hace falta el eje AR/global.
- **Archivos**: `config/policy.research_diversified.v1.yaml`, `core_sim/long_term_engine.py` (tope core 3→8), `scripts/run_wf_research_sim.py` (`--policy`), `scripts/measure_correlation.py`
- **Ver también**: **ADR-059** (hallazgo), **ADR-058** (simulador), **ADR-041** (gate)

---

## Plantilla para nuevas decisiones

```markdown
## ADR-XXX — Título corto de la decisión

- **Fecha**: YYYY-MM-DD
- **Estado**: propuesta | aceptada | reemplazada | descartada
- **Contexto**: ¿Qué problema, restricción o necesidad motivó esta decisión?
- **Decisión**: ¿Qué se decidió concretamente?
- **Por qué**: ¿Qué trade-offs justifican esta opción?
- **Consecuencias**: ¿Qué efectos positivos y negativos genera?
- **Alternativas consideradas**:
  - **Opción A**: motivo para descartarla o no elegirla.
  - **Opción B**: motivo para descartarla o no elegirla.
```
