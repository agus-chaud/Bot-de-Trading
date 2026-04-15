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
| Máx. **pérdida diaria** — bucket **largo plazo** | **-1,5 %** del equity inicial del día del bucket | Idem para bucket largo. |
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

---

## 8. Control de versiones y gobernanza

- Toda modificación de umbrales, listas blancas o N minutos debe registrarse con **fecha, autor y motivo**.
- En entornos colaborativos, se recomienda **pull request** o registro equivalente antes de cambiar `POLICY.md` o la configuración enlazada.

---

*Fin del documento — Fase 1 (especificación paper).*
