# Bot IA para escanear wallets en Polymarket + copy trading (reto transparente)

**Tipo:** Resumen de vídeo (base de conocimiento)  
**Fuente:** [YouTube — sPhZqKYUFLQ](https://youtu.be/sPhZqKYUFLQ)  
**Transcripción:** inglés (auto-generada), extraída con `youtube-transcript-api`  
**Fecha de documentación:** 2026-04-14  

> **Tono del vídeo:** experimento con resultados reales (incluye **pérdida** al cierre del reto) + enlaces de afiliado (**Polycup**, comunidad “AI Impact School”). Separar **ideas de sistema** de **marketing**.

---

## Metadatos para indexación (bot / RAG)

| Campo | Valor |
|--------|--------|
| **Temas** | prediction markets, Polymarket, ranking de wallets, filtros, copy trading, riesgo por mercado, slippage, operativa deportiva / geopolítica |
| **Herramientas citadas** | **Claude Code** (bot de informes diarios), **Polymarket** (leaderboard ~500 wallets), sitio **Polymarket Analytics** (~1000 wallets/día en el flujo actualizado), **Polycup** (copy trading) |
| **Capital del reto** | ~**491 USD** iniciales; picos y drawdowns descritos en el vídeo (no son garantías reproducibles). |

---

## STAR (historia central)

| | |
|---|---|
| **Situación** | El autor construyó con **Claude Code** un bot que **cada día** analiza cientos de wallets de **Polymarket** y genera informes con los **~10 mejores** candidatos para **revisión humana** y posible **copy trading**. |
| **Tarea** | Probar el flujo “end-to-end” con **transparencia**: cuánto se gana o pierde copiando wallets seleccionadas por el informe, durante ~**una semana**. |
| **Acción** | Copia en **Polycup** con montos **fijos** por trade (p. ej. 6–10 USD), slippage de compra/venta, **tope por mercado** tras un error (muchas entradas en el mismo mercado), ignorar trades **< 5 USD**; más tarde **dos** informes diarios: leaderboard Polymarket + muestra desde **Polymarket Analytics**. |
| **Resultado** | Hubo días muy positivos y recuperaciones fuertes, pero al **cierre del reto** el saldo quedó por **debajo** del inicial (~**400 USD** vs ~**490 USD**) por malos días y falta de seguimiento en los últimos días; posiciones aún abiertas podrían cambiar el resultado final. El narrador enmarca igual el experimento como **útil** para descubrir wallets y como **caso de uso** de IA para filtrar a escala. |

---

## R-I-S-E (marco de análisis)

### R — Revisión crítica

- **Sesgo de publicación:** el canal muestra el experimento “en vivo”; el **periodo es corto** y depende de eventos (deportes, favoritos a **0,88–0,92** que pierden).
- **Riesgo de concentración:** sin **máximo por mercado**, copiar un wallet que abre muchas veces el mismo mercado puede llevar a **~50% del bank** en un solo tema (error que el autor corrige con tope, p. ej. **50 USD** por mercado en su configuración).
- **Montos heterogéneos en el wallet madre:** por eso elige **fixed size** en lugar de % del trade original (coherente, pero introduce **tracking error** vs el edge del trader original).
- **Copy trading ≠ alpha del scanner:** el filtro IA solo **acota**; el P&L depende de **ejecución** (Polycup: el vídeo afirma **baja latencia**), **slippage**, **límites** y **varianza** de mercados binarios.
- **Operativa humana:** dejar de correr los scans y de vigilar **3 días** coincide con deterioro del equity — el sistema es **semi-manual** y sensible al **mantenimiento**.

### I — Insights clave (replicables para tu bot)

1. **Pipeline en dos etapas:** (A) **ingesta masiva** + scoring/filtros → top-N wallets; (B) **humano** abre el perfil en Polymarket y decide si entra en la lista de copy.
2. **Doble fuente de universo:** además del **leaderboard diario** (~500), muestrear **~1000 wallets** desde una **analítica** externa para diversificar el descubrimiento (menos dependencia de un solo ranking).
3. **Métricas en el informe:** wallets ordenadas por **P&L**, **score** y detalle de posiciones (el vídeo muestra UI de reportes clicables hasta el perfil).
4. **Reglas de riesgo concretas que el autor aplica:** monto fijo por trade, **max per market**, ignorar micro-trades, slippage de compra y de **venta** (menciona **20%** en venta en su ejemplo).
5. **Lección de mercado:** pérdidas grandes en **favoritos** deportivos de alto precio en centavos (mercados “casi resueltos” que revierten) — cola de distribución brutal en predicción.
6. **Honestidad metodológica:** admite **pérdida** neta y **complacencia** operativa; útil para tu KB como recordatorio de **gobernanza** y **journaling**.

### S — Síntesis ejecutiva

El vídeo documenta un **asistente de research** (IA + datos públicos de Polymarket) acoplado a un **ejecutor de copy trading**. El valor mostrado está en **automatizar el cribado** y **estandarizar el reporting**; el P&L del reto demuestra **alta varianza** y dependencia de **reglas de riesgo** y **disciplina** al mantener el proceso.

### E — Extensión práctica (diseño de bot)

- Implementar **límites duros** antes del LLM: max notional por mercado, por wallet, por día; cooldown tras N entradas en el mismo `market_id`.
- Guardar **snapshots** diarios del ranking (para auditar *look-ahead* y estabilidad de métricas).
- Si copiás: mapear **tamaño** (fijo vs proporcional) y simular **impacto** cuando el wallet madre hace **dust** o **all-in** en un evento.
- **Alertas** cuando el equity cae X% o cuando el wallet aumenta correlación (mismo deporte / misma geopolítica).
- Tratar enlaces de terceros (Polycup, cursos) como **opcionales**; tu bot puede usar **otra** vía de ejecución o solo el **módulo de ranking**.

---

## Conceptos y terminología

- **Polymarket:** mercado de predicción; posiciones en eventos binarios o similares.
- **Copy trading:** replicar órdenes de una wallet externa con retardo y tamaño propios.
- **Leaderboard / Analytics:** dos ventanas al universo de wallets; combinarlas reduce **sesgo de una sola lista**.
- **Slippage (compra/venta):** tolerancia de ejecución; en mercados de baja liquidez o cierre, la **venta** puede ser tan crítica como la entrada.
- **Max per market:** tope acumulado de exposición en el mismo mercado/evento.

---

## Conclusión (para política del bot)

Para una **base de conocimiento** de trading bot, el aporte principal del vídeo es **operacional**: cómo estructurar **descubrimiento → filtrado → revisión humana → ejecución con límites**, y cómo sin **gobernanza** (scans diarios, revisión, topes) el sistema **degrada**. El resultado monetario del reto es **una muestra** (N pequeño, varianza alta), no una prueba de ventaja estadística.

---

## Checklist al implementar algo similar

1. [ ] Definir **score** (¿solo P&L reciente?, drawdown, volumen, diversificación de mercados, antigüedad de la cuenta?).
2. [ ] **Anti-concentración:** max por mercado, max por wallet, max correlación temática.
3. [ ] Filtro de **trades mínimos** del líder para no copiar ruido.
4. [ ] **Slippage** y modo de ejecución (market vs limit) documentados por venue.
5. [ ] **Runbook diario:** quién aprueba wallets nuevas y en qué horario se congela la lista.
6. [ ] **Logging** de qué informe diario originó cada wallet copiada (trazabilidad).
7. [ ] **Paper / simulación** de sizing antes de conectar capital real.

---

## Referencias enlazadas

- Vídeo origen: [https://youtu.be/sPhZqKYUFLQ](https://youtu.be/sPhZqKYUFLQ)
