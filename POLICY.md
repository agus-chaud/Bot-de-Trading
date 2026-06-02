# POLICY — Política de riesgo y operativa (Fase 1, paper trading)

## 1. Propósito y alcance

Este documento define **límites operativos, listas permitidas, reglas de no-operación y respuestas ante incumplimientos** para un bot de trading en **modo paper (simulado)**, con fines **educativos y de experimentación**.

- **Paper-first**: no se ejecutan órdenes reales en mercado; el objetivo es aprender flujos, gobernanza de riesgo y calidad de datos.
- **Alcance**: aplica a motores de señalización (corto y largo plazo), guardarraíles, asignación de capital y simulación de broker en papel, según el plan del proyecto.
- **No constituye asesoramiento financiero, legal ni fiscal**. Las cifras son **valores por defecto razonables**; el operador debe ajustarlas a su jurisdicción, perfil y normativa aplicable. La responsabilidad del uso del software recae en quien lo despliega y configura.

---

## 2. Perfil de riesgo: moderado (umbrales numéricos por defecto)

Los siguientes valores son **defaults** para un perfil **moderado**; deben versionarse en configuración y auditarse ante cambios.

| Parámetro | Valor por defecto | Notas |
|-----------|-------------------|--------|
| Máx. % nocional por **un solo ticker** (sobre equity paper del bucket o cartera según configuración del allocator) | **8 %** | Por operación o exposición agregada en ese símbolo: usar la definición del allocator; si ambas existen, prevalece la **más restrictiva**. |
| Máx. concentración por **sector GICS** (o taxonomía equivalente si no hay GICS) | **25 %** del valor de mercado del bucket afectado | Si falta clasificación sectorial, tratar como **dato faltante** (ver sección 6). |
| Máx. **pérdida diaria** — bucket **corto plazo** | **-2,0 %** del equity inicial del día del bucket | Medido sobre P&L diario realizado + no realizado del bucket corto. |
| Máx. **pérdida diaria** — bucket **largo plazo** | **-1,5 %** del equity inicial del día del bucket | Idem para bucket largo. Implementado en `check_long_risk()` con insumo `long_daily_return` real desde `long_bucket` del ledger (**ADR-044**). |
| Máx. **pérdida diaria** — **cartera total** (paper) | **-3,0 %** del equity inicial del día total | Dispara acción más severa si se viola antes que los límites por bucket (ver matriz §5). |
| Máx. **pérdida mensual** — bucket **corto plazo** | **-8,0 %** | Alineado con el *kill switch* mensual del plan (`short_kill_switch_monthly_dd`: **-8 %**). |
| Máx. **pérdida mensual** — bucket **largo plazo** | **-6,0 %** | Menor tolerancia relativa a volatilidad típica de posiciones más largas en perfil moderado. |
| Máx. **pérdida mensual** — **cartera total** | **-10,0 %** | Tope global mensual; puede activarse antes que la suma informal de buckets. |

**Correlación explícita con `short_kill_switch_monthly_dd`:** si el drawdown mensual del bucket **corto plazo** es **≤ −8 %**, aplica el comportamiento de **kill switch** descrito en la §7 (congelación del motor corto), además de cualquier acción de la matriz de riesgo para “pérdida mensual bucket corto”.

---

## 3. Listas blancas (whitelist)

Solo se permite enviar órdenes paper para símbolos **explícitamente** listados en la whitelist activa (por mercado / archivo de configuración). Cualquier otro símbolo: **rechazo de orden** (salvo modo mantenimiento deshabilitado en Fase 1).

### 3.1 Estados Unidos — ETFs (siempre explícitos)

| Ticker | Descripción breve |
|--------|-------------------|
| SPY | S&P 500 ETF |
| QQQ | Nasdaq-100 ETF |
| IWM | Russell 2000 ETF |

### 3.2 Estados Unidos — acciones (ejemplos sustituibles)

La siguiente tabla es **solo plantilla de ejemplo**. Los tickers deben **reemplazarse** por la lista definitiva acordada por el operador; no implica recomendación de inversión.

| Ticker (ejemplo) |
|------------------|
| AAPL |
| MSFT |
| JPM |
| XOM |
| KO |
| WMT |
| JNJ |
| PG |

### 3.3 Argentina (BYMA / estilo local) — símbolos de ejemplo (configurables)

Lista **placeholder**, **configurable** por archivo de política. Formato típico mercado local; verificar lotes y códigos vigentes en el proveedor de datos.

| Símbolo (ejemplo) |
|-------------------|
| GGAL |
| YPF |
| PAMP |
| BMA |
| CEPU |
| TGS |
| TXAR |
| ALUA |
| SUPV |
| MELI |

---

## 4. Reglas de no-trading (cuándo no operar)

| Regla | Parámetro por defecto | Comportamiento |
|--------|------------------------|----------------|
| **Ventana de apertura** | **N = 15** minutos | No abrir **nuevas** posiciones en los **primeros 15 minutos** de la sesión regular del mercado correspondiente. Ajustes de riesgo ya acordados (p. ej. reducción por clip) pueden seguir política del `risk_guardrails`; la regla por defecto es **no nuevas entradas**. |
| **Ventana de cierre** | **N = 15** minutos | No abrir **nuevas** posiciones en los **últimos 15 minutos** antes del cierre oficial de la sesión regular. |
| **Ventana de noticias** (reservado) | *Placeholder* | Futura opción: bloqueo configurable alrededor de eventos de calendario (earnings, CPI, etc.). En Fase 1: **sin implementación obligatoria**; si `news_window_enabled = false`, no aplica. |
| **Calidad de datos** | Flags definidos en pipeline | Si **halt_on_data_quality** está activo y el sistema marca **serie corrupta, timestamps desordenados, hueco > umbral configurable, o precio/volumen no finito**: **pausa del motor afectado** (corto y/o largo según tabla §5) y **rechazo** de nuevas órdenes hasta recuperación o reset manual. |

**Notas:** Los valores de **N** deben ser **enteros ≥ 0** y expresados en **minutos de reloj de la sesión**. Mercados con sesiones fragmentadas (p. ej. distintos tramos): la política debe mapearse por **calendario de sesión** por mercado.

---

## 5. Matriz de respuesta ante riesgo

Para cada tipo de límite, la acción es **determinística** y **priorizada** si varios límites incumplen a la vez: prevalece la acción **más restrictiva** de la fila (de arriba hacia abajo en la columna “prioridad sugerida”).

| Tipo de límite / evento | Rechazar orden | Recortar tamaño (clip) | Pausa motor corto | Pausa todos los motores | Requiere reset manual |
|-------------------------|:--------------:|:----------------------:|:-----------------:|:------------------------:|:---------------------:|
| Excede % nocional por ticker | ✓ (nueva orden) | ✓ (si el allocator puede reducir tamaño dentro del tope) | — | — | — |
| Excede concentración sectorial | ✓ | ✓ | — | — | — |
| Pérdida diaria bucket **corto** | ✓ nuevas entradas cortas | ✓ aumentos de exposición | ✓ | — | — |
| Pérdida diaria bucket **largo** | ✓ nuevas entradas largas | ✓ | — | — | — |
| Pérdida diaria **cartera total** | ✓ | ✓ | ✓ | ✓ | — |
| Pérdida mensual bucket **largo** | ✓ | ✓ | — | — | Tras **3** sesiones consecutivas en pausa por este motivo (opcional configuración) → ✓ |
| Pérdida mensual bucket **corto** (tope **−8 %**; alineado con `short_kill_switch_monthly_dd`) | ✓ | ✓ | ✓ congelado | — | Ver §7 |
| Pérdida mensual **cartera total** | ✓ | ✓ | ✓ | ✓ | ✓ |
| Símbolo fuera de whitelist | ✓ | — | — | — | — |
| Ventana primeros/últimos N minutos | ✓ (solo nuevas entradas) | — | — | — | — |
| Flag de calidad de datos (severidad alta) | ✓ | — | ✓ | ✓ | ✓ si el flag persiste > **1** sesión completa |

**Nota:** la fila de **pérdida mensual bucket corto** (−8 %) es el **kill switch** operativo (`short_kill_switch_monthly_dd`); ver §7 para duración de la congelación.

**Convenciones:**

- **Rechazar orden**: la orden no se acepta en el `paper_broker_sim` (motivo codificado en log).
- **Clip size**: el allocator reduce cantidad/nocional para cumplir el tope; si no es posible cumplir sin violar otros límites → **rechazo**.
- **Pausa**: el motor deja de emitir señales nuevas; gestión de salidas según política explícita del motor (por defecto Fase 1: **solo salidas ya programadas** si existen en estado; si no, **congelar**).

---

## 6. Mapa de dependencias de datos

Comportamiento **determinístico** ante datos faltantes: sin excepciones silenciosas que asuman precios “buenos”.

### 6.1 Tabla por componente

| Componente | Mínimo requerido (campos / series) | Calendario | Liquidez / ADV | Si falta liquidez o ADV | Si hay huecos en la serie OHLCV |
|------------|--------------------------------------|------------|----------------|-------------------------|----------------------------------|
| `short_term_engine` | OHLCV **1 m** (o timeframe configurado) alineado; `open`, `high`, `low`, `close`, `volume`; timestamps monótonos UTC | Sesiones del mercado; feriados explícitos | **ADV20** (media volumen diario 20 sesiones) o proxy aprobado | Si ADV < umbral mínimo (**5e5** acciones/día equivalente paper, configurable) → **no nueva entrada**; señales existentes: **solo salida** si la regla del motor lo permite; si no → **hold** sin ampliar riesgo | Interpolación **prohibida** para precios. Si hueco ≤ **1** barra: usar **último close válido** para features que lo permitan y marcar `data_quality_degraded`; si hueco > **1** barra o > **5 %** de barras del día: **halt** del motor corto (ver §5) |
| `long_term_engine` | OHLCV **diario** mínimo; mismas columnas; series ajustadas o no según configuración **documentada** | Calendario diario por mercado | ADV20 a nivel diario; opcional **dollar_volume** | Si liquidez insuficiente: **excluir** del universo activo ese ticker hasta próximo rebalance; si ya hay posición: **no aumentar**; salidas según reglas del motor | Si faltan > **2** días consecutivos en ventana de lookback: **excluir** ticker del rebalance actual; si posición abierta: **congelar aumentos**; si gap en el día de decisión: **no rebalancear** ese ticker hasta próximo día con OHLCV completo |
| `risk_guardrails` | Último precio válido, equity por bucket, exposición por ticker/sector, P&L día/mes | Misma sesión que el mercado del activo | ADV y/o spread estimado si disponible | Si falta ADV: asumir **peor caso** — tratar liquidez como **baja** (bloqueo de entradas y solo reducción) | Si incoherencia OHLC (ej. high < low): **rechazo** de cualquier orden en ese símbolo hasta corrección |
| `allocator` | Límites de política, equity, precios de marca, restricciones sector/ticker | N/A para cómputo principal | Límites de tamaño por liquidez | Reducir orden a **máx** permitido por liquidez; si orden residual < tamaño mínimo del broker sim → **rechazo** | Si precio de marca ausente: **no asignar** capital nuevo |
| `paper_broker_sim` | Precio de ejecución simulado (last/mid según reglas), comisiones, slippage modelo | Calendario de ejecución | Opcional tamaño máximo por % del volumen de la barra | Si orden > **1 %** del volumen de la barra sin ADV: **recortar** a 1 % o rechazar si queda por debajo del mínimo | Si barra ausente en momento de fill: **posponer** fill a siguiente barra válida; si no llega en **M** intentos (**M=3**): **cancelar** orden con motivo `data_gap` |

### 6.2 Reglas globales determinísticas

1. **Sin datos, no hay aumento de riesgo** (entradas / scaling in prohibidos).
2. **Nunca** rellenar OHLCV inventado para generar señal.
3. Toda degradación debe producir **flag en log** (`data_quality_ok`, `data_quality_degraded`, `data_quality_halt`).

---

## 7. Kill switch (bucket corto, drawdown mensual)

**Condición:** drawdown mensual del bucket **corto plazo** **≤ −8 %** (consistente con `short_kill_switch_monthly_dd = -8%`).

### 7.1 Comportamiento por defecto (elegido)

- **Congelar** por completo el `short_term_engine` (sin nuevas señales ni ampliaciones).
- La congelación permanece hasta **reset manual** explícito por el operador (comando o flag en configuración firmada), **incluso si** el mes calendario termina y el DD mejora por marcación de mercado.

**Motivación del default:** evita reactivación automática tras volatilidad extrema sin revisión humana del incidente y de los datos.

### 7.2 Alternativa documentada

- **Congelar** el `short_term_engine` hasta el **cierre del último día de sesión del mes calendario** del mercado de referencia del bucket (o hasta **manual reset** si ocurre antes).
- A inicio del mes siguiente, el motor puede reanudarse **solo si** no hay flags `data_quality_halt` y el operador no ha dejado bloqueo administrativo activo.

En ambos modos, el **largo plazo** y el **allocator** no deben **incrementar** indirectamente el riesgo del bucket corto (no hay “bypass” por otros componentes).

**Nota de implementación (ADR-044):** en `run_paper_live.py`, el orden de ejecución es **short → long** con feature flag `--enable-long-engine` (default `false`). El largo consume la caja que quedó después del corto. El guardrail largo (`check_long_risk`) recibe `long_daily_return` real desde el ledger. El flag permite rollback inmediato a short-only sin cambio de código.

---

## 8. Control de versiones y gobernanza

- Toda modificación de umbrales, listas blancas o N minutos debe registrarse con **fecha, autor y motivo**.
- En entornos colaborativos, se recomienda **pull request** o registro equivalente antes de cambiar `POLICY.md` o la configuración enlazada.

---

## 9. Parámetros de referencia `short_term_engine` v1 (obligatorio sincronizar con YAML)

Estos parámetros definen el comportamiento mínimo del motor corto en Fase 3 y deben mantenerse idénticos en `config/policy.v1.yaml`.

| Parámetro | Valor por defecto | Uso |
|-----------|-------------------|-----|
| `momentum_lookback_days` | **20** | Ventana `N` para retorno acumulado del score de momentum. |
| `liquidity_percentile_min` | **0,60** | Umbral `p_min` del percentil de volumen; por debajo, no entra al ranking. |
| `volatility_20d_max` | **0,04** | Techo de volatilidad diaria de 20 ruedas para filtrar activos inestables en v1. |
| `top_k_per_market` | **5** | Cantidad máxima de símbolos seleccionados por mercado en cada corrida diaria. |
| `risk_budget_trade_pct` | **0,005** | Presupuesto de riesgo por trade (0,5 % del bucket corto) usado por sizing. |
| `allow_leverage` | **false** | En v1 no se permite apalancamiento ni bypass de este flag. |

**Regla de gobernanza:** si cambia cualquier valor de esta tabla, el mismo cambio debe actualizar **en el mismo commit** `POLICY.md` y `config/policy.v1.yaml`.

---

## 10. `long_term_engine` v1 (sleeve largo dentro del 70 %)

El **sleeve largo** es la fracción de cartera asignada al horizonte largo dentro del objetivo **30/70** global. Los **pesos objetivo declarados en esta sección suman 1,0 (100 %)** *solo dentro del sleeve largo*; el motor **no** recalcula el 30/70 ni el 20/80 (eso corresponde al `allocator`).

En la configuración por defecto del repo, el sleeve largo opera en **BYMA en pesos (ARS)**: líneas **core** en **acciones locales** y **satélite** en **CEDEAR** (mismo segmento operativo IOL bCBA). El benchmark de referencia narrativa es **S&P 500 vía CEDEAR `SPY`**; el arranque de pesos en YAML es ilustrativo y gobernado en el mismo commit que esta sección.

### 10.1 Core (acciones locales AR)

- **Cantidad de líneas core:** entre **2 y 3** símbolos con `target_weight` explícito en `config/policy.v1.yaml` → `long_term_engine.core_lines`, todos **operables en AR** (lista blanca `whitelist_ar.yaml` + política de símbolos).
- **Criterio v1 por defecto:** diversificar con **dos líneas locales de alta liquidez** (p. ej. banca y energía en el YAML de ejemplo) sin solapar el mismo subsector de forma redundante; el detalle de tesis queda en nota operativa, no en el motor.
- **Cambios de universo core:** solo en **fecha de rebalance** según `rebalance_rule`, salvo **procedimiento manual documentado** (commit + nota operativa) para cambios off-cycle.

### 10.2 Satélite (CEDEAR, lista acotada)

Parámetros en YAML bajo `long_term_engine.satellite_limits` y líneas en `satellite_lines`:

| Parámetro | Rol |
|-----------|-----|
| `max_satellite_weight_total` | Suma máxima de pesos objetivo del satélite. |
| `max_weight_per_satellite_line` | Techo por línea satélite. |
| `max_satellite_names` | Número máximo de tickers satélite simultáneos. |

**Mercado v1 (config actual):** `satellite_markets: [AR]` — el satélite son **CEDEAR** admitidos en `whitelist_cedear.yaml` (p. ej. `SPY` como proxy de índice). Una variante **solo US** sigue soportada en schema y código con `satellite_markets: [US]` y reglas `first_us_trading_day_of_*`.

**Gobernanza off-cycle:** variar tickers o pesos del satélite fuera del día de rebalance solo con **cambio coordinado** de `POLICY.md`, YAML y revisión humana (no automático en v1).

### 10.3 Calendario y disparador de rebalanceo

- **Día de revisión (config actual):** **primer día hábil AR** del calendario versionado (BYMA / `XBUE` en OHLCV) de cada **semana calendario** (`rebalance_rule: first_ar_business_day_of_calendar_week`). Alternativa mensual: `first_ar_business_day_of_calendar_month`. Para sleeve **US**, equivalentes `first_us_trading_day_of_calendar_week` / `first_us_trading_day_of_calendar_month` sobre `XNYS`.
- **Paper-live (motor largo AR, `--enable-long-engine`)**: el orquestador construye primero `daily_bars` según el merge del **corto**; para el largo en calendario AR se usa una **copia** de ese mapa donde los símbolos declarados en `long_term_engine` se **reemplazan** por cierres **XBUE** del mismo día. Así un CEDEAR (p. ej. `SPY`) no se valora ni se rebalancea contra el ETF **XNYS** cuando el merge global etiqueta el ticker como US. El **MTM** y el snapshot final del día usan esa copia cuando el largo está activo.
- **Stage informativo de validación (`run_long_engine_stage`)** con policy AR: las fechas efectivas y el universo de barras se resuelven contra **`calendars` + OHLCV en venue `XBUE`**; **no** se exige calendario **XNYS** en la DB para que el stage corra. El broker simulado del stage usa **un solo bloque** de `CostModel` (`markets.AR` o `markets.US` del policy, según `satellite_markets`), alineado a las órdenes del largo (`market: AR` o `US`).
- **Bandas anti-turnover:** convención **por línea** (`drift_convention: per_line`). Para cada símbolo del universo largo, \( \text{drift\_pp} = |\,w_{\text{obj}} - w_{\text{MTM}}\,| \times 100 \). Solo se considera emitir órdenes si **es día de rebalance** **y** existe al menos una línea con `drift_pp` **estrictamente mayor** que `drift_rebalance_threshold_pp`.
- **halt / datos incompletos / sin sesión de mercado del sleeve:** si el ciclo largo no puede valorar de forma fiable el universo (p. ej. `halt_on_data_quality`, día fuera del calendario AR/US según regla, o **precio faltante o no finito** para cualquier símbolo del universo en el día de rebalance), la política v1 es **no operar el ciclo completo** y registrar motivo estructurado (`missing_or_invalid_price_abort_cycle`, etc.) — **sin** rebalanceo parcial a ciegas.

### 10.4 Corporate actions y pesos

Antes de calcular pesos MTM, las **cantidades** deben reflejar el pipeline v1 de **splits/dividendos** (ver plan Fase 2). El `long_term_engine` asume posiciones ya ajustadas por el orquestador; no sustituye al store de corporate actions.

### 10.5 Salida: `orders_intent`

Cada intent incluye al menos: `symbol`, `market`, `bucket: long`, `side`, `qty`, `intent_notional`, `reason_code` (`long_rebalance_core`, `long_rebalance_core_trim`, `long_satellite_add`, `long_satellite_trim`), `target_weight`, `current_weight`, `drift_pp`, `risk_snapshot`. No se incluye `signal_score` (no aplica en v1).

### 10.6 Parámetros por defecto (obligatorio sincronizar con YAML)

| Parámetro | Valor por defecto | Uso |
|-----------|-------------------|-----|
| `drift_rebalance_threshold_pp` | **2,0** | Umbral en puntos porcentuales por línea. |
| `drift_convention` | **per_line** | Drift por símbolo vs objetivo. |
| `rebalance_rule` | **first_ar_business_day_of_calendar_week** | Día de revisión semanal (calendario AR / BYMA). |
| `max_long_rebalance_turnover_pct` | **null** | Sin tope de sum(|Δw|) en engine v1 si es `null`. |

**Regla de gobernanza:** mismos valores y semántica en `POLICY.md` y `config/policy.v1.yaml` en un único commit.

---

## 11. Pre-gate walk-forward (bloque corto, antes de subir capital)

Objetivo: **rechazo automático** si en ventanas out-of-sample consecutivas el simulador con costos viola límites operativos. Los umbrales numéricos viven en `config/policy.v1.yaml` bajo `short_term_pre_gate.thresholds`; el piso de drawdown mensual del bucket corto usa por defecto el mismo valor que `short_kill_switch_monthly_dd` si `monthly_short_drawdown_floor` es `null`.

Métricas mínimas por ventana: **costos totales** (fees del broker sim) respecto del capital inicial de la ventana; **proxy de turnover anualizado** a partir de nocional comprado y equity media; **mínimo** del drawdown mensual del bucket corto observado en los EOD de la ventana. La forma de las ventanas (`oos_trading_days`, `step_trading_days`, `min_oos_windows`) es configurable; con `enabled: false` el pre-gate no ejecuta validación (útil en entornos de desarrollo).

---

## 12. Informe KPI — spec `rpt_kpi.v1` (Fase 5 / validación)

La **fuente de verdad parseable** para fórmulas, columnas de export, segmentación (total / corto / largo), benchmark mixto 20/80, y reglas de NA del informe automático es el archivo **`docs/kpi_report_spec.v1.md`**. Este `POLICY.md` **no duplica** cada ecuación: solo fija que **toda corrida comparable** debe declarar `spec_id: rpt_kpi.v1` (o una versión mayor explícita) y cumplir el contrato de salida descrito en ese documento.

**Gobernanza:** cambiar definiciones de KPIs (Sharpe, turnover, drift, alpha, etc.) implica **nueva versión** del spec (`rpt_kpi.v2`, …) y entrada en changelog del repo con fecha y motivo. Los **umbrales numéricos de gate** (p. ej. “Sharpe OOS ≥ …”) siguen siendo **anexo separado** fechado, según plan Fase 5 — no se graban improvisados en código sin registro.

### 12.1 Decisiones técnicas (para no olvidar qué está acordado)

Resumen ejecutivo; el detalle autoritativo sigue en `docs/kpi_report_spec.v1.md`:

| Área | Decisión |
|------|----------|
| Moneda del informe | **USD**; conversión AR en el **export del ledger**, no en el script de informe. |
| Días de trading / annualización | **252**; retornos diarios simples sobre curvas de equity por segmento. |
| Tasa libre `r_f` (paper) | **0 %** para Sharpe y MAR de Sortino en `rpt_kpi.v1`. |
| Hit rate / profit factor | Por **round-trip** cerrado, emparejamiento **FIFO** por (`motor`, `symbol`). |
| Turnover mensual (largo) | \(\sum \|notional\|\) del motor largo en el mes / \((2 \times \text{media equity largo en el mes})\). |
| Drift mandato 30/70 y 20/80 | Objetivos alineados a `weights` y `geo` del YAML; drift en **puntos porcentuales** vs `equity_total`. |
| MDD\(_{12m}\) / Calmar | Ventana **252** sesiones sobre equity **solo del largo**; reglas de histórico corto y MDD≈0 en spec. |
| Alpha vs benchmark | Retorno simple del segmento menos benchmark **misma ventana**, fechas en **inner join** (sin forward-fill del benchmark). |
| Costos en informe | Desglose **`costs_day` por motor** en el export (evita reparto ambiguo). |

**Regla de memoria:** si alguien pregunta “cómo calculamos el Sharpe del informe”, la respuesta es: **`docs/kpi_report_spec.v1.md` + §12 de `POLICY.md`**.

---

## 13. Gate KPI OOS — umbrales pre-registrados (Fase 5)

**Fecha de registro: 2026-05-11.**
**Versión: gate.v1.**
Cualquier cambio posterior requiere **nueva versión** (`gate.v2`, …) con fecha y motivo en el commit.

### 13.1 Propósito

Lista cerrada de umbrales que cada ventana OOS del walk-forward debe cumplir **antes** de avanzar a capital real. Definidos **antes del primer resultado OOS agregado** para evitar sesgo de confirmación (p-hacking financiero).

### 13.2 Umbrales bloqueantes

| Métrica | Umbral | Justificación |
|---------|--------|---------------|
| Sharpe anualizado (total) | **≥ 0,30** | 70 % del portfolio es sleeve largo menos activo; Sharpe de referencia amplia (p. ej. S&P vía CEDEAR) suele situarse ~0,4–0,5. Un piso de 0,30 evita destruir valor ajustado por riesgo frente a ese ancla. |
| Sortino anualizado (total) | **≥ 0,40** | Con kill switch (-8 %) y límite diario (-2 % corto, -3 % total), el downside debería estar más acotado que el upside; Sortino > Sharpe es la expectativa. |
| Max drawdown total | **≥ -18 %** | Peor caso razonable: largo 0,70 × -25 % ≈ -17,5 % + corto 0,30 × -8 % ≈ -2,4 %. Un piso de -18 % detecta fallas estructurales sin disparar falsos positivos por bear market moderado. |
| Max drawdown bucket corto | **≥ -10 %** | Kill switch congela a -8 % mensual pero se auto-resetea a inicio de mes; en una ventana OOS de ~3 meses puede acumular dos activaciones. -10 % da 2 pp de margen. |
| Max drawdown bucket largo | **≥ -25 %** | Sleeve largo BYMA en pesos (core + CEDEAR). Referencia amplia: índice EE.UU. ~-25 % en 2022. El umbral detecta errores de rebalanceo, no solo shocks locales. |
| Turnover mensual largo (último) | **≤ 8 %** | Con bandas de drift 2,0 pp y rebalanceo semanal, el turnover esperado sube vs mensual; el techo de 8 % sigue como guardrail para detectar churn anómalo. |
| Alpha simple vs benchmark 20/80 (total) | **≥ -2 %** | El alpha real viene del 30 % corto (momentum v1). Pedir α > 0 en v1 es optimista; -2 % dice "no destruyas más de 2 pp anuales respecto al benchmark pasivo". Si pierde más, el bloque corto no justifica su costo operativo. |

### 13.3 Métricas informativas (sin umbral bloqueante en v1)

| Métrica | Umbral | Motivo |
|---------|--------|--------|
| Calmar 12m (largo) | **null** | Depende casi 100 % del mercado en sleeve pasivo. Se calcula y reporta pero no bloquea. |
| MDD 12m rolling (largo) | **null** | Misma lógica que Calmar; informativo para monitoreo. |

### 13.4 Regla de agregación

**`rule: all`** — todos los tramos OOS deben pasar todos los umbrales bloqueantes. Si un tramo falla por shock externo y se considera que no refleja un defecto del bot, se puede pasar a `rule: k_of_last_q` en una versión futura (`gate.v2`), con fecha y motivo documentados.

### 13.5 Walk-forward del gate

| Parámetro | Valor |
|-----------|-------|
| Burn-in | **252** días hábiles (~1 año) |
| Ventana OOS | **60** días hábiles (~3 meses) |
| Step | **30** días hábiles (~1,5 meses) |
| Mínimo de ventanas OOS | **1** |

Datos mínimos para la primera evaluación: **312 días hábiles** (~15 meses de operación paper-live).

### 13.6 Gobernanza

- Los umbrales viven como fuente parseable en `config/policy.v1.yaml` → `kpi_oos_gate.thresholds`.
- Este anexo en `POLICY.md` es la **fuente de verdad humana** con justificación.
- Para cambiar un umbral: nueva versión del anexo (`gate.v2`, …), nuevo commit con fecha y motivo, actualización simultánea de YAML y POLICY.

---

## 14. Protocolo de ramp-up (paper → capital real)

### 14.1 Propósito

Graduar la exposición a capital real en escalones con checkpoints de revisión. Evitar pasar de 0 a 100 % de golpe aunque el gate pase.

### 14.2 Escalones

| Escalón | % del capital asignado al bot | Criterio de entrada | Duración mínima | Criterio de rollback |
|---------|-------------------------------|---------------------|------------------|----------------------|
| **paper** | 0 % (simulado) | Estado inicial | Sin mínimo; hasta que gate pase | N/A |
| **ramp_10** | 10 % | Gate KPI OOS pasado (`rule: all`) + CI verde 5 días consecutivos | **30 días** de operación real | DD mensual real > 1,5× peor DD OOS observado **o** fallo de CI en paper-live |
| **ramp_25** | 25 % | 30 días en `ramp_10` sin rollback + gate re-evaluado con datos reales nuevos | **30 días** | Idem |
| **ramp_50** | 50 % | 30 días en `ramp_25` sin rollback + revisión manual de drift y costos reales vs simulados | **60 días** | Idem + desvío costos reales vs simulados > 50 % |
| **live_100** | 100 % | 60 días en `ramp_50` sin rollback + revisión completa de KPIs reales | Indefinido | Cualquier criterio de rollback anterior + revisión mensual obligatoria |

### 14.3 Reglas de operación

1. **Subir de escalón es decisión humana**: el bot no auto-promueve. El operador revisa los datos, decide y cambia `ramp_stage` en el YAML con commit documentado.
2. **Bajar de escalón puede ser automático**: si el criterio de rollback se activa, el sistema (o el operador) retrocede al escalón anterior y registra motivo.
3. **Rollback a paper**: si en cualquier escalón el DD mensual real supera **2× el peor DD OOS observado**, se vuelve a `paper` hasta nueva revisión completa.
4. **Paper-live sigue corriendo**: incluso en `live_100`, el paper-live paralelo sigue operando para comparar simulado vs real y detectar divergencias.

### 14.4 Trazabilidad

- El campo `ramp_stage` en `config/policy.v1.yaml` refleja el escalón actual.
- Valores válidos: `paper`, `ramp_10`, `ramp_25`, `ramp_50`, `live_100`.
- Cada transición de escalón se registra como commit con fecha y motivo (ej. "ramp_10 → ramp_25: 30d sin rollback, gate re-evaluado 2026-08-15").

---

## 15. Operación paper-live automatizada (cron y recuperación)

Esta sección norma el orquestador diario `scripts/run_paper_live.py` y el workflow `.github/workflows/paper_live_daily.yml`. Detalle técnico en `decisiones-tecnicas.md` (**ADR-040**, **ADR-050**).

### 15.1 Política F3 (catch-up máximo)

| Regla | Valor | Comportamiento |
|-------|-------|----------------|
| **F3** | Máximo **3** días hábiles (lun–vie) entre el último `paper_snapshots.trading_day` y el día objetivo | Si el gap es mayor, el script termina con **código 2** y mensaje de intervención manual. No se procesa catch-up masivo en una sola corrida. |
| Recuperación | Tandas de ≤3 días | `workflow_dispatch` con input `date` = último día de cada bloque, o ejecución local equivalente + push a `paper-live-data`. |
| Día sin barras | Feriado / mercado cerrado / fetch incompleto | **Warning y continuar** con el siguiente día del gap; no abortar todo el rango por un solo día sin OHLCV (**ADR-050**). |

El día objetivo por defecto (sin `--date`) es el **último día hábil anterior** a la fecha UTC del runner.

### 15.2 Credenciales y datos en CI

- **`IOL_USER` / `IOL_PASS`**: deben configurarse como **secretos del repositorio en GitHub Actions**. No bastan variables de entorno en la máquina del operador.
- Sin credenciales en CI, la ingesta AR puede omitir IOL (`iol_credentials_missing`) y depender de fallback; el paper-live puede fallar si faltan barras del día.
- **Diagnóstico local** (sin imprimir contraseñas): `python scripts/diagnose_iol_auth.py`.
- **IOL histórico 401** (login OK, serie 401): incidente conocido; el job diario puede seguir con fallback Byma/yfinance mientras se revisan permisos de cuenta IOL.

### 15.3 Ramas y persistencia

- Código y workflow en **`main`**; ejecución y DB operativa en **`paper-live-data`** (`data/market.db` vía Git LFS).
- Tras cambios de código en `main`, sincronizar: `git checkout paper-live-data && git merge main`.
- Conflictos en `data/market.db` al integrar remoto: resolver el **puntero LFS** con `git checkout --ours` o `--theirs`, luego `git add` y commit de merge.

### 15.4 Backfill previo a recuperación larga

Si la DB quedó desactualizada, antes de tandas F3-safe ejecutar:

`python scripts/fetch_daily.py --lookback 120 --db data/market.db`

---

*Fin del documento — Fase 1 (especificación paper) + Fase 5 (informe KPI, gate, ramp-up) + §15 operación paper-live.*
