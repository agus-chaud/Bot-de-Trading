# Especificación del informe KPI — `rpt_kpi.v1`

Documento de **definiciones operativas** para el informe automático de métricas (Fase 5 del plan). Objetivo: que dos implementaciones distintas del mismo `report_kpis` produzcan **los mismos números** dado el mismo CSV de entrada y metadata de corrida.

---

## 1. Metadatos de versión

| Campo | Valor |
|--------|--------|
| Identificador | `rpt_kpi.v1` |
| Fecha de congelación | **2026-05-05** |
| Vigencia | Válida hasta una nueva versión explícita (`rpt_kpi.v2`, etc.). Cambiar reglas **sin** subir versión está prohibido para gates y comparación entre corridas. |

---

## 2. Entradas al informe (contrato mínimo)

### 2.1 Serie diaria (obligatoria)

Archivo CSV (o equivalente) con **una fila por día de trading** `ts` (fecha de sesión, ISO-8601 date o datetime UTC alineado al cierre de la sesión de referencia del backtest).

Columnas mínimas:

| Columna | Significado |
|---------|-------------|
| `ts` | Día de valoración (sesión) |
| `equity_total` | Patrimonio neto **total** paper (cash + activos valorizados), **ya en moneda de informe** (§3) |
| `equity_short` | Patrimonio atribuible al bucket **corto** (mismo convenio contable que el allocator) |
| `equity_long` | Patrimonio atribuible al bucket **largo** |
| `cash` | Efectivo total (opcional para KPIs v0; obligatorio si el informe valida consistencia) |
| `costs_day` | Costos del día (comisión + slippage + otros del `cost_model`), **moneda de informe** |

**Nota:** si en una corrida `equity_short + equity_long` no iguala `equity_total` por diseño (efectivo no asignado, cuentas puente), el informe debe declarar en **metadata** el convenio; para `rpt_kpi.v1` se asume **asignación completa** salvo que `unallocated_cash` venga en columnas extra documentadas en esa corrida.

### 2.2 Log de operaciones (obligatoria desde v1 en adelante)

Tabla de **fills** u órdenes ejecutadas con, como mínimo: `ts`, `symbol`, `side`, `qty`, `price`, `fees`, `slippage` (o costo agregado), **`motor`** o tag equivalente (`short` | `long`), y **clasificación geográfica** del símbolo (`AR` | `US`) para drift geo.

### 2.3 Metadata de corrida (obligatoria)

Archivo pequeño (JSON/YAML) por corrida con al menos:

- `spec_id`: `rpt_kpi.v1`
- `reporting_ccy`: `USD`
- `trading_days_per_year`: `252`
- Referencia de **FX** usada en el export del ledger (§3), o confirmación de que todas las columnas monetarias ya vienen en USD.

---

## 3. Moneda de consolidación y FX (decisión cerrada)

| Decisión | Valor |
|----------|--------|
| Moneda del informe | **USD** (dólares estadounidenses). |
| Responsable de conversión | El **export del ledger / post-backtest**, no el script `report_kpis`. |
| Tipo de cambio | **Cierre diario** acordado con la fuente de datos del run (ej. `FX_close` del mismo `ts` que la valoración de activos AR). El identificador exacto de la serie FX debe figurar en la metadata (`fx_pair` o `fx_source_id`). |
| No reconversión intra-día | `rpt_kpi.v1` usa solo cifras ya convertidas; evita doble FX. |

---

## 4. Segmentos reportados

Para **cada KPI** que aplique por tramo temporal, se reportan columnas o filas para:

1. **Total** — `equity_total`
2. **Corto** — `equity_short`
3. **Largo** — `equity_long`

**Geo (recomendado para drift y alpha desagregado):** pesos **AR** y **US** calculados sobre **patrimonio total** al cierre de cada `ts`, usando la clasificación `AR` | `US` del símbolo en metadata de universo (no inferir por sufijo sin regla documentada).

---

## 5. Retornos y annualización

| Decisión | Valor |
|----------|--------|
| Retorno diario simple | \( r_t = \frac{E_t}{E_{t-1}} - 1 \) sobre la curva de equity del segmento (excluir el primer \(t\) sin \(E_{t-1}\)). |
| Días de trading por año | **252** |
| Retorno neto anualizado (CAGR-style sobre el tramo) | \( (E_T / E_0)^{252/N} - 1 \) donde \(N\) es número de retornos diarios en el tramo \([0,T]\). Si \(E_0 \le 0\) o \(E_T \le 0\): métrica **NA** y motivo en el informe. |

Los costos ya deben estar reflejados en la curva de equity **o** el informe debe documentar que el retorno es “neto de costos explícitos en `costs_day`”; para `rpt_kpi.v1` se asume **equity ya neta de costos** (coherente con el ledger).

---

## 6. Riesgo libre y ratios (Sharpe / Sortino)

| Parámetro | Valor |
|-----------|--------|
| Tasa libre de riesgo anual \(r_f\) | **0 %** (paper-first; sin carry del cash modelado en el ratio). |
| \(r_f\) diaria | **0** (no escalar; \(r_{f,daily} = 0\)). |
| Sharpe (anualizado) | \( \sqrt{252} \cdot \frac{\mathrm{mean}(r_t)}{\mathrm{std}(r_t)} \) con \(r_t\) en ventana del tramo; si \(\mathrm{std}(r_t)=0\): **NA**. |
| Sortino — umbral “MAR” | **0** (mismo criterio que \(r_f\) en paper). |
| Desviación downside | Desviación estándar de solo los \(r_t < \mathrm{MAR}\) diaria; si no hay retornos bajo MAR: Sortino = **NA** (no sustituir por Sharpe). |
| Sortino (anualizado) | \( \sqrt{252} \cdot \frac{\mathrm{mean}(r_t)}{\mathrm{dd}} \) con `dd` = desviación downside anterior. |

---

## 7. Drawdown y calidad del largo plazo

| Métrica | Definición |
|---------|------------|
| **Max drawdown** (tramo reportado) | Sobre la serie \(E_t\) del segmento: \(\min_t (E_t / \mathrm{peak}_t - 1)\), con \(\mathrm{peak}_t = \max_{s \le t} E_s\). Reportar como **número negativo** (ej. −0,18 = −18 %). |
| **MDD\(_{12m}\) rolling** | Solo para segmento **largo**. En cada día \(t\) (con histórico suficiente), tomar ventana de los **últimos 252** retornos diarios de `equity_long` y calcular el max drawdown **dentro de esa ventana**. En el informe de un tramo OOS: reportar el valor en el **último día del tramo** y, si se pide serie, la serie completa. Si hay &lt; 252 sesiones: **NA** con etiqueta `insufficient_history`. |
| **Calmar\(_{12m}\)** | Solo **largo**. Sobre la misma ventana de 252 sesiones que el MDD\(_{12m}\) en \(t\): \(\text{Calmar} = R_{ann} / |\text{MDD}_{window}|\), donde \(R_{ann} = (E_t/E_{t-252})^{252/252} - 1\) y \(\text{MDD}_{window}\) es el max drawdown en esa ventana. Si \(|\text{MDD}_{window}| < 10^{-8}\): **NA** (`mdd_near_zero`). |

---

## 8. Hit rate y profit factor (decisión cerrada)

| Decisión | Valor |
|----------|--------|
| Unidad | **Por operación cerrada (round-trip)** dentro de cada **motor** (`short` / `long`), no por día calendario. |
| Emparejamiento | **FIFO** por (`motor`, `symbol`): cada compra aumenta cola; cada venta consume cola; al cerrar cantidad a cero se cierra un round-trip. |
| PnL del round-trip | Suma de *(proceeds − cost basis − costos de ejecución atribuibles a esos fills)* en USD coherente con §3. |
| **Hit rate** | \(\frac{\#\{\text{round-trips con PnL} > 0\}}{\#\{\text{round-trips}\}}\). Si no hay round-trips: **NA**. |
| **Profit factor** | \(\frac{\sum \max(\text{PnL}, 0)}{\sum |\min(\text{PnL}, 0)|}\). Si el denominador es 0 y el numerador &gt; 0: **+∞** (reportar como string `inf` o cap documentado); si no hay pérdidas ni ganancias: **NA**. |

---

## 9. Turnover

### 9.1 Turnover mensual del largo (`turnover_long_monthly`)

Para cada **mes calendario** \(M\) (según calendario de fechas `ts` del backtest):

\[
\text{turnover}_{long,M} =
\frac{\sum_{fills \in M,\, motor=long} |notional_{fill}|}
{2 \cdot \overline{\mathrm{equity\_long}}_M}
\]

donde:

- \(|notional_{fill}| = |qty \times price|\) en **USD** al fill (coherente con §3).
- \(\overline{\mathrm{equity\_long}}_M\) = media aritmética de `equity_long` en los días `ts` que caen en \(M\).

Si no hubo fills largos en \(M\): turnover = **0**. Si \(\overline{\mathrm{equity\_long}}_M = 0\): **NA**.

### 9.2 Turnover agregado / corto (misma convención)

Para el bucket **corto** o **total**, usar la misma fórmula sustituyendo `motor` y el denominador por \(\overline{\mathrm{equity\_short}}_M\) o \(\overline{\mathrm{equity\_total}}_M\) según se indique en la tabla de salida.

---

## 10. Costos por motor

\(\text{costo\_motor} = \sum_{days} \text{costs\_day}\) atribuible al motor cuando el ledger exporte desglose; si solo existe `costs_day` global, el informe debe usar **columnas `costs_day_short` y `costs_day_long`** en el CSV (recomendado) o repartir con regla explícita en metadata — para `rpt_kpi.v1` se exige **desglose por motor en el export** para evitar ambigüedad.

---

## 11. Mandato: drift 30/70 y 20/80

Targets fijos (alineados a `POLICY` / `config/policy.v1.yaml` para perfil moderado por defecto):

| Target | Valor |
|--------|--------|
| Peso largo sobre total | \(w^*_{long} = 0{,}70\) |
| Peso corto sobre total | \(w^*_{short} = 0{,}30\) |
| Peso AR sobre total | \(w^*_{AR} = 0{,}20\) |
| Peso US sobre total | \(w^*_{US} = 0{,}80\) |

En cada cierre \(t\):

- \(w_{long}(t) = \mathrm{equity\_long}(t) / \mathrm{equity\_total}(t)\) (si `equity_total` ≤ 0 → NA).
- **Drift 70/30 (largo):** \(\Delta_{70}(t) = \big(w_{long}(t) - w^*_{long}\big) \times 100\) **puntos porcentuales** (pp).
- \(w_{AR}(t)\) = valor de mercado de posiciones clasificadas AR / `equity_total(t)`.
- **Drift 20/80 (AR):** \(\Delta_{20}(t) = \big(w_{AR}(t) - w^*_{AR}\big) \times 100\) **pp**.

Las **bandas** ±X pp no forman parte de esta spec numérica: se documentan en `POLICY` y solo se usan en el informe como comparación (pass/fail opcional en el gate, no en `rpt_kpi.v1` core).

---

## 12. Alpha vs benchmark mixto 20/80

| Decisión | Valor |
|----------|--------|
| Definición | \(\alpha = R^{net}_{segment} - R^{bench}\) en la **misma ventana** y moneda **USD**, ambos retornos **netos** del mismo tipo (log o simple). Para `rpt_kpi.v1`: **retorno simple compuesto** del tramo: \(E_T/E_0 - 1\) menos el del benchmark. |
| Benchmark | Pesos y símbolos **congelados** en archivo de corrida (`benchmark_static.yaml` / CSV) — no reoptimizar mirando el equity del bot. |
| Alineación | Solo fechas presentes en **ambas** series; si hay gaps, política del run: **inner join** de fechas o forward-fill del benchmark prohibido en `rpt_kpi.v1` (preferir inner join y reportar `n_obs`). |

---

## 13. Salida del informe

Formato mínimo: **JSON** y/o **Markdown** con:

- `spec_id`, `run_id`, rango `[ts_start, ts_end]`
- Tabla de KPIs por segmento
- Lista de **NA** con motivo (`insufficient_history`, `zero_std`, etc.)

---

## 14. Relación con umbrales de gate

Los **umbrales numéricos de aprobación** (Sharpe mínimo, etc.) **no** se definen en este documento: van en anexo de gate fechado, según Fase 5 del plan, para evitar p-hacking.

---

## 15. Referencias internas

- Política de producto, límites y **§12 Informe KPI / decisiones técnicas**: `POLICY.md`
- Contrato numérico parseable: `config/policy.v1.yaml`
- Plan de ejecución: `.cursor/plans/bot_trading_paper-first_155d6f04.plan.md`
