# Decisiones técnicas (ADR)

Este documento registra las decisiones técnicas relevantes del proyecto, su contexto, el porqué, consecuencias y alternativas evaluadas.

## Cómo usar este archivo

- Crear una nueva entrada por cada decisión importante.
- Mantener un estado claro: `propuesta`, `aceptada`, `reemplazada`, `descartada`.
- Cuando una decisión cambie, no borrar el historial: marcar la anterior como `reemplazada` y enlazar la nueva.

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
