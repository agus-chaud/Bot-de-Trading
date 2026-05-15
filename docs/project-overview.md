# Project Overview — Bot de Trading (paper-first)

Este documento esta pensado para dos usos:

- Servir como guion tecnico para una defensa oral.
- Permitir lectura simple para alguien que entra por primera vez al repo.

No busca reemplazar la documentacion normativa (`POLICY.md`) ni el registro de decisiones (`decisiones-tecnicas.md`), sino unir arquitectura, criterio y estado actual.

## 1) El problema y la filosofia

El problema que resuelve el proyecto es evitar decisiones de inversion manuales impulsivas y no reproducibles. El foco esta en construir un proceso estable, medible y auditable.

La filosofia base es:

- **Paper-first**: primero validar en simulacion con datos reales.
- **Riesgo deterministico en codigo**: los guardrails no dependen de prompts ni heuristicas opacas.
- **Proceso antes que rentabilidad**: subir exposicion solo despues de pasar gates definidos.
- **Trazabilidad completa**: cada regla relevante queda versionada en YAML, validada por schema y explicada en politica.

## 2) Arquitectura general

La arquitectura separa responsabilidades: data, engines, riesgo, ejecucion simulada, contabilidad y reporting. Esto permite evolucionar un bloque sin romper el resto.

```mermaid
flowchart LR
    A[Data Layer<br/>connectors + normalizer + storage] --> B[Short Term Engine]
    A --> C[Long Term Engine]
    B --> D[Risk Guardrails]
    C --> D
    D --> E[Allocator]
    E --> F[PaperBrokerSim]
    F --> G[PortfolioLedger]
    G --> H[Reporting KPI]
    G --> I[Validation Workflow]
    J[POLICY + YAML + Schema] -. contratos .-> A
    J -. contratos .-> B
    J -. contratos .-> C
    J -. limites .-> D
```

### Decisiones clave de arquitectura

- Se separan **engines por horizonte** (`short_term_engine` diario y `long_term_engine` semanal) para no mezclar logicas con distintos tiempos de decision.
- El modulo `risk_guardrails` concentra reglas de bloqueo para tener un unico punto auditable.
- `event_engine` coordina el pipeline diario y evita que cada modulo defina su propio "orden de pasos".
- `paper_broker_sim` y `ledger` modelan ejecucion y PnL de forma estable antes de hablar de capital real.

## 3) Gestion de riesgo

El riesgo se implementa como reglas operativas explicitas, no como recomendaciones "blandas". El objetivo es que ante la misma entrada, la decision sea siempre la misma.

### Guardrails del bloque corto

`check_short_risk()` evalua en orden de severidad:

1. Calidad de datos.
2. Ventana no-trade intradia.
3. Kill switch mensual del bucket corto.
4. Limite de perdida diaria del bucket corto.

Si una regla bloquea, no se ejecutan entradas nuevas. La salida de cada evaluacion queda en logs estructurados para auditoria.

### Guardrails del bloque largo

`check_long_risk()` aplica un limite diario del sleeve largo (-1.5% del equity largo) y evita rebalanceos cuando el contexto excede el riesgo permitido. El insumo `long_daily_return` se calcula en el `PortfolioLedger` (comparacion contra MTM del dia habil anterior) y se expone via `long_bucket` en el snapshot de `mark_to_market`. El runner largo extrae esta key explicitamente — no depende de un default implicito 0.0.

### Stop loss por instrumento

`check_stop_loss()` usa ATR(14) cuando hay historia suficiente y fallback porcentual cuando no la hay. Un stop loss es una salida de riesgo y tiene prioridad operativa frente a bloqueos de nuevas entradas.

```mermaid
flowchart TD
    A[Inicio check_short_risk] --> B{Data quality OK?}
    B -- No --> X[BLOCK: data_quality]
    B -- Si --> C{Dentro de no-trade window?}
    C -- Si --> Y[BLOCK: no_trade_window]
    C -- No --> D{Kill switch mensual activo?}
    D -- Si --> Z[BLOCK: short_kill_switch]
    D -- No --> E{Perdida diaria excedida?}
    E -- Si --> W[BLOCK: daily_loss_short]
    E -- No --> F[ALLOW]
```

## 4) Motor de corto plazo

El corto plazo esta pensado para decisiones diarias y control estricto de exposicion:

- Genera candidatos con momentum y filtros de liquidez/volatilidad.
- **Filtra sobrecompra con RSI(14)**: si `RSI > rsi_overbought_entry` (default 70), el candidato se descarta con motivo `rsi_overbought`. Esto evita entrar en tickers cuyo momentum es positivo pero cuya velocidad de suba sugiere reversion.
- Rankea por mercado y limita seleccion (`top_k_per_market`).
- Construye `orders_intent` con sizing por presupuesto de riesgo.
- Pasa por `risk_guardrails` antes de llegar al broker simulado.
- **Salida anticipada por RSI**: para posiciones abiertas del bucket corto, si RSI cruza descendentemente el umbral `rsi_exit_threshold` (default 45) — es decir, ayer >= umbral y hoy < umbral — se genera una orden SELL con motivo `rsi_momentum_exhausted`. El crossover evita salidas falsas cuando RSI simplemente esta bajo y estable.
- **Contadores de auditoria**: cada ventana OOS reporta `entries_blocked_by_rsi`, `exits_by_rsi` y `exits_by_stop_loss` para explicar por que cambio el resultado.

En terminos de defensa oral, la idea central es: el motor no "adivina", **propone**; quien habilita o bloquea finalmente es el stack de riesgo. RSI complementa al momentum (no lo reemplaza): momentum dice "sube", RSI dice "se paso de rosca".

```mermaid
flowchart TD
  A[Tickers whitelist] --> B{Barra OHLCV hoy?}
  B -->|No| X1[Descartado]
  B -->|Si| C{20+ dias historial?}
  C -->|No| X2[Descartado]
  C -->|Si| D{Sesion valida?}
  D -->|No| X3[Descartado]
  D -->|Si| E{Liquidez >= p60?}
  E -->|No| X4[Descartado]
  E -->|Si| F{Volatilidad <= 4%?}
  F -->|No| X5[Descartado]
  F -->|Si| G{Momentum > 0?}
  G -->|No| X6[Descartado]
  G -->|Si| H{"RSI(14) < 70?"}
  H -->|No| X7["Descartado: sobrecompra"]
  H -->|Si| I[Top K por mercado]
  I --> J[Sizing + caps]
  J --> K[Orden BUY]
```

## 5) Motor de largo plazo

El largo plazo trabaja con rebalanceo por bandas de drift en cadencia **semanal o mensual** según policy:

- Parte de pesos objetivo definidos en policy/config (`long_term_engine`).
- En el **default del repo**: calendario **AR** (`first_ar_business_day_of_calendar_week` o mensual), datos diarios y calendario de validación en venue **XBUE** (BYMA / pesos), core en acciones locales y satélite en **CEDEAR** (p. ej. **SPY** como proxy del índice en ARS). Variante documentada: sleeve **US** con `first_us_trading_day_of_*` y **XNYS**.
- Mide desvio entre peso actual y objetivo.
- Solo rebalancea si es dia valido de calendario (sesiones AR o US según regla) y el drift supera el umbral.
- En v1, el satelite esta acotado y controlado por limites explicitos cuando aplica (`satellite_limits`).
- **Guardrail efectivo**: `check_long_risk()` recibe `long_daily_return` real desde `long_bucket` del ledger. Si el sleeve largo pierde mas de 1.5% en el dia, se bloquean nuevos rebalanceos.

El **stage informativo** `validation/stages/long_engine.run_long_engine_stage` ejecuta ese pipeline sobre SQLite: para policy AR usa **solo** XBUE para fechas y OHLCV (no exige tabla `calendars` XNYS). En **paper-live** con `--enable-long-engine`, las barras que ve el largo AR se alinean a **XBUE** por ticker de policy aunque el merge del corto marque el mismo símbolo como US (**ADR-048**).

Este bloque apunta a estabilidad de cartera y menor rotacion relativa, complementando al motor corto que es mas tactico.

## 6) Datos, mercado y APIs

Esta seccion es critica porque sin calidad de datos no hay señal confiable ni riesgo valido.

### Fuentes y criterio de uso

- **US OHLCV**: `yfinance` con retry exponencial (`data/connectors/us_connector.py`).
- **AR OHLCV**: IOL REST API como primario y fallback Byma/yfinance (`data/connectors/ar_connector.py`).
- **Calendarios**: `pandas_market_calendars` para sesiones US (XNYS) y AR (XBUE) (`data/calendar_builder.py`).
- **Persistencia**: SQLite en `MarketDB` (`data/storage.py`), con tablas para OHLCV, logs, fills, snapshots y kill switch.
- **Benchmark**: `data/benchmark_returns.py` genera retornos de un benchmark mixto 20/80 (AR/US) point-in-time, sin lookahead, usando cierres disponibles hasta cada fecha de valoracion. Se usa en el informe KPI para calcular alpha vs pasivo.

### Por que esta estrategia

- Evita dependencia unica de proveedor en AR mediante fallback.
- Separa errores de red de errores de datos para diagnostico claro.
- Mantiene pipeline reproducible: fetch -> normalize -> store (`data/fetcher.py` + `data/normalizer.py`).

### Universo AR hibrido (liquidez + holdings)

Cuando `symbols.universe_selection.enabled` esta activo en `config/policy.v1.yaml`:

- **Ranking**: sobre candidatos Merval y CEDEAR (YAML dedicados), se ordena por volumen total en una ventana fija de dias habiles usando historia **solo IOL** (sin distorsion Byma/yfinance en la seleccion). Desempates deterministas: notional medio descendente (`close × volume`), luego ticker ascendente.
- **Rebalanceo**: cadencia policy-driven (por defecto **semanal**): si la corrida no debe refrescar el ranking, se **reutiliza la ultima seleccion persistida** en `universe_snapshots` (fallback controlado frente a presupuesto o calendario).
- **Ingesta OHLCV**: la lista AR que consume `fetch_and_store` no es solo el top por volumen: es **`merge(top_merval, top_cedear, holdings_AR_abiertos)`** ordenado y sin duplicados. Si un ticker sigue en cartera AR (`qty ≠ 0` replay desde fills en DB paper-live), **sigue descargandose aunque haya salido del top**, para MTM, riesgo y salidas coherentes.
- **Motor corto vs barras**: `resolve_ar_universe_for_short_pipeline` alimenta `load_merged_whitelist(..., ar_operational_symbols=...)` con ese conjunto de barras; el ranking de candidatos puede limitarse al top de liquidez (`ar_signal_symbols`) para no ensanchar senales con ilícitos fuera del universo operativo.
- **Presupuesto API**: contadores por tipo en SQLite (`iol_api_usage`), limite mensual (warning al pasar umbral soft, sin ranking nuevo si hard), y **tope duro por job** que aborta la seleccion dinamica y cae a ultimo snapshot o whitelist estatica.

Detalle y fuentes en **ADR-047** (`decisiones-tecnicas.md`).

### Tratamiento de calidad

- Deteccion de outliers (rolling 5d median) y forward-fill acotado (≤3 dias, marcado como `imputed=True`).
- Flags de degradacion para no ocultar problemas.
- Regla operativa: sin datos confiables, no se aumenta riesgo.

## 7) Paper broker y ledger

El `PaperBrokerSim` permite validar ejecucion sin riesgo de capital real:

- Simula fills deterministas.
- Aplica costos (comision, slippage, spread) con `CostModel`.
- Devuelve reportes de fill trazables.

El `PortfolioLedger` centraliza:

- Estado de posiciones.
- PnL realizado/no realizado.
- Curva de equity.
- Drawdown mensual del bucket corto (`short_bucket`).
- **Daily return del sleeve largo** (`long_bucket` con `long_daily_return` y `long_equity`), calculado como variacion vs MTM del dia habil anterior.

```mermaid
flowchart LR
    A[Daily bars snapshot] --> B[Short/Long intent generation]
    B --> C[Risk checks]
    C --> D[Allocator]
    D --> E[PaperBrokerSim fills]
    E --> F[PortfolioLedger update]
    F --> G[KPIs / Validation / Logs]
```

## 8) Paper-live y modelo de branches

### Orquestador diario

`scripts/run_paper_live.py` ejecuta el pipeline dia a dia contra OHLCV real en SQLite:

- Detecta el ultimo dia procesado y hace catch-up idempotente de los dias faltantes.
- Aplica politica F3: si el gap supera 3 dias habiles, exit(2) y requiere intervencion manual.
- Persiste fills y snapshots en `data/market.db` bajo mode `paper_live`.
- Replay de ledger desde fills anteriores para mantener estado coherente.
- **Soporte para ambos sleeves**: con `--enable-long-engine` se ejecuta el pipeline corto primero y luego el largo sobre el mismo ledger/broker. Los fills de ambos sleeves se persisten juntos. Sin el flag (default), el flujo es solo corto — rollback inmediato sin cambio de codigo.
- Tras el corto, si el largo esta activo y el policy usa calendario **AR**, una copia de `daily_bars` recibe precios **XBUE** por cada simbolo de `long_term_engine` (motor CEDEAR/pesos; **ADR-048**). El snapshot final usa esa copia para MTM cuando el flag esta encendido.
- Orden fijo **short → long**: el largo consume la caja que quedo despues del corto.

### Workflow automatizado

`.github/workflows/paper_live_daily.yml` corre de lunes a viernes a las 10:00 UTC (post-cierre US):

1. Fetch OHLCV de los ultimos 5 dias (`fetch_daily.py --lookback 5`).
2. Ejecucion del pipeline (`run_paper_live.py`).
3. Commit automatico de la DB actualizada.
4. Notificacion automatica (issue GitHub) ante cualquier fallo.

### Modelo de branches

| Rama | Proposito | Que se commitea |
|------|-----------|-----------------|
| `main` | Evolucion de codigo, PRs, CI | Solo codigo y docs |
| `paper-live-data` | Operacion diaria automatizada | Codigo + `data/market.db` (via Git LFS) |

El workflow vive en `main` (GitHub lee schedule/dispatch del default branch), pero hace checkout de `paper-live-data` para ejecutar. Git LFS para `data/*.db` evita inflar el repo con commits binarios diarios (~250/año).

## 9) Validation workflow (GO/NO-GO)

El modulo `validation/` implementa un pipeline de validacion automatica que evalua si el sistema esta en condiciones operativas. Produce un `ValidationReport` con decision binaria GO/NO-GO.

### Stages

Cada stage es independiente y retorna `StageResult` con metricas, violaciones y flag de skip:

1. **`data_quality`**: verifica integridad y frescura de datos OHLCV.
2. **`short_pre_gate`**: ejecuta walk-forward OOS del bloque corto y evalua metricas contra umbrales.
3. **`long_engine`**: valida comportamiento del motor largo (drift, rebalanceos, turnover). `run_long_engine_stage` retorna siempre `(StageResult, StageDetails | None)`; con `return_details=True` expone curva diaria de equity del **sleeve largo**, fills y posiciones finales para analisis fuera del JSON agregado (**ADR-046**).
4. **`risk_audit`**: audita que los guardrails actuaron correctamente en la historia reciente.
5. **`kill_switch_history`**: verifica historial de activaciones/resets del kill switch.

El runner (`validation/runner.py`) orquesta las 5 etapas y agrega la decision global. Script: `scripts/run_validation_wf.py`.

### Walk-forward del largo (CLI) vs comparacion en notebook

| Artefacto | Proposito | Salida |
|-----------|-----------|--------|
| `validation/wf_runner.py` + `scripts/run_long_engine_wf.py` | Varias ventanas rolling; metricas agregadas por ventana y summary global | `validation_reports/long_engine_wf_*.json` (**ADR-027**) |
| `notebooks/wf_long_comparison.ipynb` | Evidencia empirica de **ADR-045**: semanal vs mensual vs buy-and-hold SPY (abr-2025 → may-2026) | WF 3m/paso 1m + viz por ventana; corrida continua sin reset (`continuous_equity_df`) (**ADR-046**) |

El CLI WF no cambia de contrato: `wf_runner` sigue consumiendo solo el `StageResult` (`[0]` de la tupla). El notebook pide detalle con `return_details=True` y, para la regla mensual, una copia del `policy_doc` con `rebalance_rule` sobrescrito por corrida.

Flujo del notebook (**ADR-046**, pasos 3–4):

1. Carga `data/market.db` y ventanas `generate_wf_windows(3, 1)` sobre calendario XNYS.
2. Por ventana: stage largo semanal, mensual y curva SPY → `equity_df` + `windows_df`.
3. Métricas por ventana: retorno total, Sharpe anualizado (√252), MDD desde equity diaria.
4. Cuatro vistas: subplot grid base 100, tabla pivote, barra de retorno promedio, MDD agrupado por ventana.
5. Corrida continua sobre todo el calendario (paso 5): tres curvas superpuestas en USD y en base 100.

## 10) Testing y calidad

La estrategia de testing prioriza comportamiento observable:

- Reglas de riesgo y bloqueos operativos.
- Integraciones del pipeline diario.
- Contrato de policy (YAML + schema + tests).
- Regresion de KPI con fixtures golden.
- Validation stages (data quality, risk audit, kill switch history, motores corto/largo).

El repo cuenta con ~44 archivos de test, abarcando unitarios, integracion y regresion. El objetivo no es "testear por cobertura", sino reducir riesgo de regresiones en decisiones de negocio (riesgo, sizing, ejecucion y validacion).

## 11) Decisiones tecnicas clave

Las decisiones se documentan en ADRs dentro de `decisiones-tecnicas.md` (46 ADRs a la fecha). Los ejes principales son:

- Paper-first como estrategia de construccion.
- Riesgo deterministico y centralizado.
- Motores desacoplados con nucleo comun.
- Contratos versionados (`policy.v1.yaml` + schema).
- Gate KPI OOS con umbrales pre-registrados y ramp-up gradual en 5 escalones (**ADR-041**).
- RSI(14) como filtro de entrada y señal de salida del motor corto (**ADR-042**): mejoro avg max drawdown de -0.134% a -0.098% y redujo turnover de 1.69 a 1.26 en walk-forward 180d.
- Modelo de branches `main` / `paper-live-data` con LFS y notificaciones (**ADR-040**).
- ADRs argentinos (MELI, YPF, TGS, GGAL) incorporados al whitelist US con precedencia de tag y categoría `adrs` separada (**ADR-043**).
- Integración del largo en paper-live con guardrail efectivo, dedup de riesgo corto y feature flag de rollback (**ADR-044**).
- Rebalanceo largo semanal vs mensual: cambio operativo en policy (**ADR-045**); evidencia en notebook WF + corrida continua (**ADR-046**).

Para defensa oral, esta seccion muestra que la arquitectura no salio de una implementacion improvisada, sino de decisiones acumuladas y justificadas.

## 12) Metodologia con IA

La IA se uso como acelerador de implementacion y exploracion, no como reemplazo de criterio tecnico.

### Principios de uso

- Las decisiones de arquitectura, riesgo y policy se tomaron de forma explicita y versionada por el proyecto.
- La IA ayudo a iterar codigo, tests y estructura documental mas rapido.
- Cada cambio relevante se valido con contrato (schema/tests) y trazabilidad (ADR/changelog/policy).

### Controles de calidad sobre asistencia IA

- No se delega al modelo la logica de riesgo en runtime.
- Se evita acoplar comportamiento critico a prompts.
- Se exige validacion automatica y lectura critica humana antes de consolidar decisiones.

En una defensa oral, el punto central es demostrar gobernanza: **la IA fue herramienta**, el sistema de decisiones siguio siendo ingenieria controlada.

## 12.1) Ultimo cambio estructural: integracion del largo (ADR-044)

 se arreglaron tres cosas que estaban rotas o a medias:

**El guardrail del largo no funcionaba.** El sistema tenia un limite de perdida diaria del -1.5% para el sleeve largo, pero en la practica nunca se activaba. El codigo buscaba un dato (`long_daily_return`) que nadie calculaba, asi que siempre valia 0.0 y el limite nunca disparaba. Ahora el ledger calcula ese retorno diario de verdad (comparando equity largo hoy vs ayer) y el runner largo lo usa para decidir si bloquea rebalanceos.

**El motor largo no corria en paper-live.** El orquestador diario (`run_paper_live.py`) solo ejecutaba el pipeline corto. Ahora puede correr ambos — primero el corto, luego el largo sobre el mismo ledger — con un flag `--enable-long-engine`. Si algo sale mal, se apaga el flag y se vuelve a solo-corto sin tocar codigo.

**La logica de riesgo corto estaba duplicada.** Habia dos lugares en el codigo con los mismos 4 checks de riesgo copiados a mano (calidad de datos, ventana no-trade, kill switch, limite diario). Si se cambiaba uno, habia que acordarse de cambiar el otro. Ahora uno llama al otro — una sola fuente de verdad.

## 12.2) LangChain, CrewAI y capas de confianza (probabilidad vs matematica fija)

Esta seccion aclara por que el proyecto **no** usa frameworks de agentes LLM en el path de ejecucion, y como podrian complementar el sistema mas adelante sin romper la filosofia paper-first.

### Que son (breve)

- **LangChain** ([documentacion](https://docs.langchain.com/oss/python/langchain/overview)): framework open source para conectar modelos de lenguaje con datos externos, herramientas (tools) y flujos de agente. Su foco es estandarizar integraciones y orquestar pasos donde el LLM decide que herramienta usar (patron tipo ReAct).
- **CrewAI** ([introduccion](https://docs.crewai.com/en/introduction)): framework orientado a **equipos de agentes** con rol, objetivo y colaboracion. Combina **Flows** (orquestacion y estado) con **Crews** (grupos de agentes que resuelven tareas delegadas).

Ambos son utiles cuando el problema es **lenguaje natural, ambiguedad o investigacion abierta**. No reemplazan reglas de trading versionadas y testeables.

### Ventajas que podrian aportar (fuera del nucleo)

| Ventaja | Ejemplo de uso compatible con este repo |
|---------|----------------------------------------|
| Velocidad en tareas textuales | Resumir filings, notas o logs de paper-live para revision humana |
| Multi-agente para research | Un crew "analista + revisor" que contrasta un borrador de `POLICY.md` o un diff de policy |
| Integracion con muchas APIs | Prototipos de copiloto que leen fuentes heterogeneas sin cablear cada conector a mano |
| Exploracion de hipotesis | Brainstorm de indicadores o escenarios en notebook, **sin** tocar `run_paper_live.py` (p. ej. `notebooks/wf_long_comparison.ipynb` para ADR-045/046) |

### Por que no se usan en ejecucion ni en riesgo

La decision formal esta en **ADR-002** (`decisiones-tecnicas.md`): los limites de riesgo (kill switch, perdida diaria, ventanas sin operar, etc.) estan escritos como **reglas fijas en codigo**, no como instrucciones a un modelo de lenguaje.

En una defensa oral conviene usar esta imagen: hay dos tipos de "empleados" en el sistema.

| | **Capa que "opina"** (LLM / LangChain / CrewAI) | **Capa que "cuenta"** (reglas del bot) |
|---|---|---|
| Pregunta que responde | "Que significa este titular o este texto?" | "Con estos numeros de hoy, opero o no?" |
| Misma situacion manana | Puede cambiar la redaccion o el enfasis | Debe dar la **misma** respuesta |
| Si se equivoca | Perdes tiempo revisando un resumen | Podrias operar cuando no debias, o no frenar a tiempo |

Por eso LangChain y CrewAI **no** van en el camino que termina en ordenes (ni siquiera en paper): solo sirven como **copiloto de investigacion**, nunca como caja fuerte.

**Tres motivos, en lenguaje llano:**

1. **Reproducibilidad** — El proyecto promete: mismos datos + misma politica = misma decision. Un modelo de lenguaje es probabilistico: puede responder distinto aunque los precios no cambien. Eso impide repetir un experimento con confianza y complica las pruebas automaticas (walk-forward, tests en CI).
2. **Auditoria** — Si algo sale mal, hay que explicar *que regla* actuo. El bot deja motivos explicitos en logs (por ejemplo: "bloqueado por perdida diaria del bucket corto"). Decir "la IA lo interpreto asi" no es una auditoria defendible ante un jurado ni ante uno mismo seis meses despues.
3. **Riesgo de interpretacion** — Los modelos son fuertes con texto ambiguo y debiles si no hay red de seguridad numerica: pueden confundir magnitudes, inventar un dato que no estaba en el mercado, o llamar mal a una herramienta externa. Un resumen mal hecho molesta; un error en tamano de posicion o en un freno de riesgo duele.

Un cuarto motivo practico: **costo y tiempo** — un equipo de agentes hace muchas llamadas al modelo por dia; el pipeline paper-live debe ser rapido y estable.

**Aclaracion:** los "roles" de `AGENTS.md` (Spec, Risk, Engines) organizan **como trabajamos en el repo** (humanos y asistente del IDE). No son agentes CrewAI corriendo cada manana en produccion.

### Que capa confias a la probabilidad y cual a la matematica fija

**Regla de oro para la defensa:** *el LLM propone ideas y texto; las reglas escritas y el codigo deciden el dinero* (aunque sea simulado en paper).

#### Capa probabilistica — confias en la interpretacion, no en la ejecucion

Aca va todo lo que **sugiere** y **ayuda a pensar**, siempre con revision humana antes de que pese en el sistema:

- Leer y resumir noticias, informes o logs largos.
- Proponer etiquetas ("macro", "resultados trimestrales", "riesgo regulatorio").
- Ayudar a redactar o revisar la politica del proyecto.
- Explorar hipotesis ("y si probamos otro filtro?") en chat o notebook.

**Nada de esto mueve una orden** hasta que una persona lo traduce a reglas versionadas y testeadas.

En el repo hoy: IA al construir el proyecto (seccion 12), notas en `knowledge-base/`, y en el futuro un posible copiloto de noticias **offline** (ver mas abajo).

#### Capa matematica fija — confias en numeros y reglas, no en opiniones

Aca va todo lo que **debe repetirse igual** cada dia y dejar rastro claro:

- Politica y limites versionados (`POLICY.md`, `config/policy.v1.yaml`).
- Calidad de los datos de mercado (precios, sesiones, alertas si faltan datos).
- Senales del motor corto y largo (momentum, RSI, bandas de rebalanceo, etc.).
- Frenos de riesgo centralizados (`risk_guardrails.py`): kill switch, limites diarios, ventanas sin operar.
- Simulacion de compra/venta y contabilidad (`PaperBrokerSim`, `PortfolioLedger`).
- Validacion global GO / NO-GO antes de confiar en el sistema.

Si manana preguntas "por que no compro?", la respuesta debe ser un **umbral o una regla**, no "el modelo lo sintio".

```mermaid
flowchart TB
  subgraph probabilidad["Capa que OPINA — copiloto, no ejecuta ordenes"]
    L[LangChain / CrewAI opcional]
    N[Noticias y resumenes]
    K[Notas y borradores para humano]
    L --> N --> K
  end
  subgraph fija["Capa que CUENTA — unica fuente de verdad operativa"]
    P[Politica escrita y versionada]
    D[Datos de mercado verificados]
    E[Motores: reglas con numeros]
    R[Frenos de riesgo]
    X[Simulador y libro contable]
    V[Validacion GO / NO-GO]
    P --> D --> E --> R --> X --> V
  end
  K -. el humano revisa y solo entonces .-> P
```

| Para explicar en oral | Capa probabilistica | Capa matematica fija |
|----------------------|---------------------|----------------------|
| Analogia | Asistente de investigacion en el escritorio | Caja fuerte con combinacion en el contrato |
| Que confias | Criterio sobre texto y prioridades para revisar | Umbrales, bloqueos, tamanos, simulacion, gates |
| Donde vive en el repo | Seccion 12, `knowledge-base/`, futuro research LLM | Policy YAML, motores, `risk_guardrails`, broker paper, `validation/` |

### Mejora futura posible (investigacion, sin senal directa)

Escenario acotado, alineado a ADR-002:

1. **Ingesta diaria** de titulares o notas (API/RSS/archivo), fuera del horario critico de `run_paper_live`.
2. **Crew o agente LangChain** que resume eventos y propone etiquetas (`earnings`, `regulatorio`, `macro`, etc.) y una lectura cualitativa de posible impacto en sectores o tickers del whitelist.
3. **Validacion humana obligatoria** antes de persistir: nada entra a motores ni a `policy.v1.yaml` sin revision y, si aplica, ADR + evidencia walk-forward.
4. **Salida permitida**: entradas en `knowledge-base/`, issues de seguimiento o borradores de policy — **no** scores que muevan ranking RSI/momentum ni ordenes en el broker simulado.

Si algun dia se quisiera que etiquetas influyan en senales, seria un **cambio de contrato** del motor, con pre-gate OOS y ADR dedicado; hoy queda fuera de alcance.

## 13) Trabajo pendiente

El proyecto esta funcional en paper-first con ambos sleeves (corto y largo) integrados en el loop diario. Gate KPI OOS activo. Frentes abiertos:

1. **Acumular datos paper-live**: el gate KPI OOS requiere minimo 312 dias habiles (~15 meses); hoy hay ~120 dias historicos. El workflow diario esta activo y acumulando.
2. **Activar `--enable-long-engine` en produccion**: el largo esta cableado y testeado, pero el flag esta apagado por defecto. Activar tras validar en paper que el snapshot final refleja ambos sleeves correctamente.
3. Cerrar brechas entre policy y ejecucion en puntos puntuales (por ejemplo, controles de concentracion sectorial en runtime si se habilitan como bloqueantes).
4. Explorar indicadores complementarios al RSI si el drawdown mensual del bucket corto sigue siendo cuello de botella (4/13 windows passed en walk-forward 180d).
5. Extender controles CI de cobertura y regresion a modulos fuera de `core_sim` con la misma disciplina (especialmente `validation/` y `reporting/`).
6. Agregar observabilidad explicita para operacion diaria del largo: metricas minimas por dia (`fills_long_count`, `long_risk_block_count`, `snapshot_long_equity_present`).
7. **Copiloto de noticias (investigacion)**: pipeline offline con LangChain/CrewAI para resumir eventos diarios y etiquetado **human-validated** hacia `knowledge-base/` — sin acoplar a motores ni a ejecucion (ver seccion 12.2).

Este capitulo existe para evitar una narrativa "cerrada". El sistema se presenta como una base robusta en evolucion, con backlog tecnico explicitado.

---

## Como usar este documento en defensa oral

- Abrir con secciones 1 y 2 (problema + arquitectura) para marcar contexto.
- Profundizar en 3, 4, 5 y 6 para explicar decisiones tecnicas de motores y datos.
- Usar 8 y 9 para mostrar operacion diaria real (paper-live) y validacion automatica.
- Usar 12 para explicar metodologia de construccion con IA; 12.2 para delimitar LangChain/CrewAI (copiloto vs nucleo deterministico).
- Cerrar con 13 para mostrar criterio, honestidad tecnica y roadmap.

Documentos complementarios:

- Politica operativa: `POLICY.md`
- Contrato parseable: `config/policy.v1.yaml`
- Validacion estructural: `config/policy.v1.schema.json`
- Registro de decisiones: `decisiones-tecnicas.md` (44 ADRs)
- KPI spec: `docs/kpi_report_spec.v1.md`
- Listas blancas: `config/symbols/whitelist_us.yaml` (ETFs, stocks, ADRs), `config/symbols/whitelist_ar.yaml`
