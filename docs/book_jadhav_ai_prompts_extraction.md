# Extracción — "AI Prompts for Financial Analysis" (Tejas Jadhav)

> Minería del libro convertida en ACTIVOS para el bot sistemático de CEDEARs/ADRs.
> NO es un resumen del libro. Clasificación: **[QUANT]** (código) · **[LLM]** (capa de tesis/matriz de riesgo) · **[SKIP]** (no aplica).
> Mercado del libro: India (Rs., NSE, RBI, F&O líquido). Donde un supuesto indio no transfiere a AR/US, está marcado.

---

## 1. Tabla maestra

| Cap | Prompt | Cat | Aplicación al bot | Esfuerzo |
|-----|--------|-----|-------------------|----------|
| 1 | 1.5 Accounting Red Flag Detector | QUANT/LLM | Filtro fundamental de exclusión de universo + flag en tesis | M |
| 1 | 1.6 Stock Screening Criteria | QUANT | Screen cuantitativo → filtro de universo (ROCE/CAGR/deuda) | M |
| 1 | 1.2 Competitive Moat · 1.3 Mgmt Quality | LLM | Sección "calidad" en tesis por posición | S |
| 1 | 1.4 Earnings Call Analysis | LLM | (dup de 11.7) sentiment de earnings → tesis | S |
| 2 | 2.4 Relative Valuation | QUANT | Z-score de múltiplos vs pares → señal value/filtro | M |
| 2 | 2.6 Free Cash Flow Quality | QUANT/LLM | Filtro de calidad (CFO/PAT, FCF/EBITDA) | M |
| 2 | 2.1 DCF · 2.2 WACC · 2.3 Sensitivity · 2.8 Scenario | LLM | Valuación DCF-lite con target ponderado en tesis | L |
| 3 | 3.1 Chart Pattern / multi-timeframe | QUANT | Confirmación de tendencia (no señal primaria) | M |
| 3 | **3.1/3.4 ADX regime filter** | QUANT | **Switch trend-following vs mean-reversion** | S |
| 3 | 3.2 Support & Resistance + confluence | QUANT | Niveles de stop/target, position sizing 1% | M |
| 3 | 3.3 RSI Divergence | QUANT | Señal de agotamiento (filtrada por ADX) | M |
| 3 | 3.4 MACD Framework | QUANT | Confirmación de momentum, histogram divergence | S |
| 3 | **3.5 Bollinger Squeeze / %B** | QUANT | **Detector de compresión de vol → pre-breakout** | S |
| 3 | 3.6 F&O OI/PCR/Max Pain | SKIP | Requiere opciones líquidas — no hay en CEDEARs | — |
| 3 | 3.7 Volume Profile / POC / Value Area | QUANT | Niveles de soporte por volumen | M |
| 3 | **3.8 Sector Relative Strength (RRG)** | QUANT | **Rotación sectorial → tilt de allocation** | M |
| 3 | **3.9 Systematic Trading Strategy (binary rules)** | QUANT | **ATR stops/targets, position sizing, max heat, correlation filter** | M |
| 3 | 3.10 Candlestick Patterns | QUANT | Confirmación de entrada en niveles S/R | S |
| 4 | 4.1 Options Greeks | SKIP* | Solo si hedgean con opciones US sobre ADRs | — |
| 4 | 4.8 Futures Hedging & Sizing | QUANT | Beta-hedge del sleeve (ya tienen hedge_short) | M |
| 4 | **4.10 India VIX Interpretation** | QUANT | **Régimen de vol con ^VIX → escalar exposición** | S |
| 5 | 5.1–5.10 M&A/LBO/Cap Structure | SKIP | Corporate finance discrecional, no bot | — |
| 8 | **8.5/8.9 Valuation: ERP + P/E percentile + señal mecánica** | QUANT | **Régimen de valuación de índice → tilt equity/cash** | M |
| 8 | 8.8 Fed/DXY → mercado | QUANT | Beta a DXY/USD (relevante AR) | M |
| 8 | 8.1 Macro Outlook · 8.10 Weekly Brief | LLM | Comentario macro en dashboard | S |
| 8 | 8.2 Sector Rotation by cycle | LLM | (RRG cuant ya en 3.8) overlay narrativo | S |
| 8 | 8.3 FII/DII Flow · 8.6 Fixed Income | SKIP | India-específico / no es tu asset class | — |
| 9 | **9.1 VaR (param 95/99, √t) + Stressed VaR** | QUANT | **Métrica de riesgo diaria del book** | M |
| 9 | **9.5 Sharpe/Sortino/CVaR/MaxDD/Beta** | QUANT | **Risk-adjusted metrics del portfolio** | S |
| 9 | **9.7 Stress Testing (escenarios)** | QUANT | **Extiende tu what-if sim con shocks** | M |
| 9 | **9.9 Board Risk Dashboard (RAG thresholds)** | LLM/QUANT | **Mapea DIRECTO a tu matriz de riesgo** | S |
| 9 | 9.2/9.3/9.4/9.6 Credit/IRRBB/Counterparty | SKIP | Riesgo bancario, no equity bot | — |
| 10 | 10.1 Multi-AI Research (DCF/SOTP/scenario) | LLM | Paquete de tesis con target ponderado | L |
| 11 | **11.1 Role+Context+Constraint** | LLM | Estructura base de TODOS tus prompts | S |
| 11 | **11.2 Chain-of-Thought + verificación** | LLM | CoT para valuación/riesgo + auto-check | S |
| 11 | **11.3 Multi-step workflow con gates** | LLM | Pipeline de research con verificación entre pasos | M |
| 11 | 11.4 PARE Framework | LLM | Calibrar formato/audiencia de la tesis | S |
| 11 | **11.6 Scenario Planning (KDV, bear-back)** | LLM | Bull/base/bear con variable diferenciadora | S |
| 11 | **11.7 Earnings Transcript Analysis** | LLM | Sentiment, deflection counter, hedging escalation | M |
| 11 | 11.8 Report Writing + Quality Checklist | LLM | Anti-slop checklist para la tesis | S |
| 11 | 11.9 Personal Prompt Library + versioning | LLM | Versionar prompts por modelo (vos ya tenés capa LLM) | S |
| 6 | Wealth Mgmt (todo) | SKIP | Planificación retail/HNI, no bot | — |
| 7 | Mutual Funds (todo) | SKIP | Selección de fondos retail, no bot | — |

\* 4.1 Greeks = SKIP para AR sistemático salvo que agreguen overlay de opciones US sobre ADRs.

---

## 2. Top 5 quick wins (alto impacto / bajo esfuerzo)

1. **ADX como switch de régimen (3.1/3.4)** — `ADX>25` = trend-following habilitado; `ADX<20` = mean-reversion / no aplicar señales de tendencia. Una sola feature que evita el error más caro: aplicar estrategia de tendencia en mercado lateral. Esfuerzo S, impacto enorme.
2. **VIX regime filter (4.10 + 9)** — usar `^VIX` (no India VIX) como gate de exposición: vol alta → bajar `equity_exposure`, subir `allow_cash`. Encastra perfecto con tu sleeve largo+cash ya existente.
3. **RAG matrix con thresholds cuantitativos (9.9)** — tu "matriz de riesgo" ya existe; el libro te da los umbrales numéricos GREEN/AMBER/RED por métrica. Convertir juicio cualitativo en reglas. Esfuerzo S.
4. **Risk-adjusted metrics estándar (9.5)** — Sharpe, Sortino, CVaR (Expected Shortfall 99%), MaxDD, beta. Reportar en el dashboard/sim. Casi todo ya calculable con lo que tenés.
5. **Role+Context+Constraint + CoT+verificación (11.1/11.2)** — re-escribir tus prompts de tesis con rol explícito, datos numéricos, constraints, y un paso de auto-verificación. 3–5x más específico, sin tocar código de trading.

---

## 3. Templates [LLM] (pegar en la capa de tesis / matriz de riesgo)

### 3.1 — Tesis por posición (Role+Context+Constraint + CoT + verificación)
```
ROL: Sos analista cuantitativo senior de un fondo long-only de CEDEARs/ADRs.
CONTEXTO: Posición {ticker}. Datos: precio {px}, ADX(14) {adx}, RSI(14) {rsi},
distancia a 200SMA {pct_200}, vol realizada 20d {rv}, VIX {vix}, exposición
actual del sleeve {exposure}, cash {cash}. Régimen detectado: {regime}.
CONSTRAINT: Producí una tesis de máx 180 palabras. Estructura fija:
(1) Veredicto [MANTENER/SUBIR/BAJAR/SALIR] + por qué en una frase con número.
(2) Señal técnica dominante (con valor).
(3) Riesgo principal + nivel de invalidación (stop técnico).
(4) Una métrica que cambiaría el veredicto si se mueve.
Pensá paso a paso antes de escribir. Toda afirmación lleva un número.
Prohibido: adjetivos vagos, "podría", "parecería", voz pasiva.
VERIFICACIÓN (auto): antes de devolver, revisá: (a) ¿algún número contradice
el régimen {regime}? (b) ¿el stop es coherente con el ATR? (c) un sanity check.
```

### 3.2 — Matriz de riesgo con RAG cuantitativo (de 9.9)
```
Construí la fila de matriz de riesgo para {ticker}/{portfolio} con thresholds:
- Drawdown actual: GREEN <8% | AMBER 8–15% | RED >15%
- VaR utilizado (vs límite): GREEN <60% | AMBER 60–90% | RED >90%
- Concentración posición: GREEN <X% | AMBER | RED >límite
- Vol régimen (VIX pct): GREEN <p30 | AMBER p30–70 | RED >p70
Para cada métrica: valor actual, color, tendencia vs período anterior (↑/↓),
y UNA acción si es AMBER/RED. Principio: "no sorpresas" — todo breach se
reporta con número, no con juicio cualitativo.
```

### 3.3 — Scenario bull/base/bear con KDV (de 11.6)
```
Para {ticker} a {px}, generá 3 escenarios SIN solapamiento:
- BULL (~25%): qué tiene que salir bien que NO está en el precio hoy.
- BASE (~60%): consenso con haircut de ejecución 10–15%.
- BEAR (~15%): downside realista que un inversor razonable debe planear.
Identificá la KDV (Key Differentiating Variable): la ÚNICA variable que, sola,
mueve el caso de Bear a Bull con todo lo demás constante.
Bear-back: arrancá del Bear — ¿qué debería ser cierto sobre la KDV para que se
materialice? ¿Qué indicador adelantado lo confirmaría 1–2 meses antes del precio?
Devolvé: tabla 3×supuestos con probabilidad + "cómo monitorear" cada uno.
```

### 3.4 — Earnings transcript scanner (de 11.7) — opcional, para tickers con calls
```
Acá el transcript Q{q} de {ticker}: [PEGAR]
(1) Contá lenguaje confiado (strong/committed) vs hedging (headwinds/challenging).
    ¿Cuál domina?
(2) Downgrades de guidance comunicados implícitamente ("broadly in line",
    "working through" = no resuelto).
(3) 3 preguntas con respuestas más largas (foco real) y 3 más esquivadas.
(4) Score de sentiment 1–5. Comparar vs trimestre anterior.
Devolvé bullets, cada uno con evidencia textual.
```

> **Anti-slop checklist (11.8)** para validar cualquier salida LLM: todo número verificado contra fuente · ticker correcto · tono consistente · sin filler ("en el dinámico entorno actual", "vale la pena notar", "sin duda").

---

## 4. Backlog [QUANT] (features/guards a codear, por módulo)

### Módulo: régimen de mercado
- [ ] **ADX(14)** como flag de régimen: `trending` (>25) / `ranging` (<20) / `transición`. Gatea qué familia de señales se aplica. (3.1/3.4)
- [ ] **VIX regime** con `^VIX` percentil 52w → multiplicador de `equity_exposure`. (4.10) ⚠️ usar VIX US, NO India VIX.
- [ ] **Valuation regime** del índice/benchmark: ERP = earnings_yield − risk_free; P/E percentile vs 5/10y → zonas BUY/HOLD/REDUCE con señal mecánica de tilt equity↔cash. (8.5/8.9) Encastra con `allow_cash`.

### Módulo: señales técnicas (confirmación, no primarias)
- [ ] **Bollinger squeeze**: band width en percentil <20 → alerta pre-breakout; `%B` + volumen para sesgo direccional. (3.5)
- [ ] **RSI divergence** (regular/hidden) filtrada por ADX. (3.3)
- [ ] **MACD histogram divergence** como early-warning de agotamiento. (3.4)
- [ ] **Volume Profile / POC / Value Area** como niveles de soporte por volumen. (3.7)
- [ ] **Sector Relative Strength / RRG** (1M/3M/6M vs benchmark) → tilt de allocation por cuadrante. (3.8)

### Módulo: ejecución / sizing (de 3.9 — reglas binarias)
- [ ] **ATR(14) stops y targets**: stop = `1.5×ATR`, target = `3×ATR` (R:R 2:1). (3.9/3.10)
- [ ] **Position sizing por riesgo fijo**: `size = risk_$ / stop_distance`, risk_$ = 1–1.5% del equity. (3.2/3.9)
- [ ] **Max heat de portfolio**: suma de riesgo abierto ≤ 10%. (3.9)
- [ ] **Correlation/sector filter**: máx N posiciones del mismo sector. (3.9)

### Módulo: riesgo (extiende what-if sim y matriz)
- [ ] **VaR paramétrico** 95% y 99%, 1-día, escalado √t a 10-día; **Stressed VaR** con vols de crisis (2008/2020). (9.1)
- [ ] **Métricas risk-adjusted**: Sharpe, Sortino, **CVaR / Expected Shortfall 99%**, MaxDD, beta al benchmark. (9.5)
- [ ] **Stress testing** del what-if sim: escenarios COVID-replay, rate-shock, credit-event con shock conjunto equity/FX/oro. ⚠️ tu what-if ya tuvo el bug de valuación USD/ARS (ADR-051) — los shocks deben aplicarse en la capa sim consistente. (9.7)
- [ ] **Diversification benefit** = Σ VaR individuales − VaR portfolio. (9.1)

### Módulo: filtros fundamentales (universo)
- [ ] **Screen cuantitativo** (1.6): ROCE 3y, revenue CAGR, Net Debt/EBITDA, FCF/EBITDA como filtro de elegibilidad de universo.
- [ ] **FCF quality / accounting red flags** (1.5/2.6): CFO/PAT, working capital trend → exclusión o flag.
- [ ] **Relative valuation z-score** (2.4): múltiplos vs pares → señal value secundaria.

---

## Trampas / edge cases (no negociables para AR/US)

- **Opciones (Ch4, 3.6 F&O):** el libro asume F&O líquido (NSE). CEDEARs no tienen cadena de opciones líquida. Greeks/PCR/Max Pain/Iron Condor → SKIP salvo overlay con opciones US sobre el ADR subyacente.
- **VIX:** usar `^VIX` (CBOE), nunca India VIX. Concepto transfiere, el dato no.
- **Doble moneda USD/ARS:** todo cálculo de VaR/stress/retornos debe fijar la moneda. Ya hubo drawdown ficticio por valuar CEDEARs en ARS al cierre USD (ADR-051 / memoria `whatif-ar-holiday-usd-mismark`). Los shocks de 9.7 deben correr en la capa sim corregida.
- **Feriados AR ≠ XNYS:** las barras y el carry-forward difieren. Cualquier feature de volumen/RS multi-timeframe tiene que respetar el calendario correcto.
- **Look-ahead bias:** percentiles (P/E, VIX, band width) deben computarse con ventana rolling SIN incluir el dato futuro.
- **Flujos India (FII/DII/SIP, Ch8):** estructura de mercado india, NO transfiere a AR. SKIP.
- **LLM nunca decide trades:** el libro lo repite (9.10/11.10). La capa LLM hace tesis/comentario; la decisión es del código determinista + humano.
