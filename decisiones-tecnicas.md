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
