# Asistente de trading con IA: Claude Code + Telegram (demo y arquitectura)

**Tipo:** Resumen de vídeo (base de conocimiento)  
**Fuente:** [YouTube — tsCI72TWzsg](https://youtu.be/tsCI72TWzsg)  
**Transcripción:** inglés (auto-generada), extraída con `youtube-transcript-api`  
**Fecha de documentación:** 2026-04-14  

> **Tono del vídeo:** tutorial / promoción de un paquete de configuración de pago. Tratar los rendimientos y la “seguridad” como **claims del autor**, no como hechos verificados en tu entorno.

---

## Metadatos para indexación (bot / RAG)

| Campo | Valor |
|--------|--------|
| **Temas** | agentes, ejecución por lenguaje natural, Telegram como UI, perpetuals/futures, funding rates, webhooks TradingView, datos on-chain públicos, riesgo operativo |
| **Stack citado** | Anthropic **Claude**, **Claude Code** (modo “agentic”), **Telegram**, exchange (no nombre fijo en la transcripción; demo con apalancamiento), **TradingView**, **Hyperliquid**, **Hyperdash** |
| **Precio mencionado** | Paquete de documentación/prompts **~27 USD** (dato del vídeo; puede cambiar). |

---

## STAR (historia central)

| | |
|---|---|
| **Situación** | El narrador afirma que el “edge” histórico fue de instituciones con sistemas 24/7; los LLM permitirían a una persona acercarse con un asistente que monitorea y ejecuta. |
| **Tarea** | Mostrar un **asistente de trading con IA** que combine **Claude Code** (acciones reales, no solo chat) y **Telegram** para comandos desde el móvil. |
| **Acción** | Demos en vivo: apertura de posiciones con una frase, SL/TP, informes de cartera, escáner de **funding**, órdenes múltiples, integración con **capturas** (Hyperdash + TradingView) + datos de mercado, y **webhook** desde TradingView hacia Claude para ejecución automática. |
| **Resultado** | Propuesta: control remoto del exchange, reportes tipo “mesa pro”, automatización de señales; cierre con **CTA** a guía/prompts de pago y disclaimer de riesgo. |

---

## R-I-S-E (marco de análisis)

### R — Revisión crítica

- **Riesgo de confianza en el LLM:** ejecutar órdenes reales desde lenguaje natural multiplica errores de interpretación, alucinaciones o ambigüedad del prompt.
- **Riesgo de producto:** el vídeo vende un **paquete**; separar **ideas reutilizables** (arquitectura) de **marketing** (“smarter than any human”, “massive edge”).
- **Funding “free money”:** los funding rates altos suelen ir con **riesgo de precio** y liquidaciones; “farmear el rate” no elimina el riesgo direccional ni de cola.
- **Copiar wallets top en Hyperliquid:** datos públicos ≠ **alpha sostenible**; puede haber **sesgo de supervivencia**, wallets que cambian de estrategia, o posiciones no replicables a tu tamaño/slippage.
- **Seguridad:** “todo local, la API no sale del dispositivo” mejora frente a un SaaS opaco, pero **API keys en una máquina con agente** sigue siendo superficie de ataque (malware, prompts inyectados, bugs en scripts).

### I — Insights clave (técnicos / de producto)

1. **Patrón arquitectónico:** **agente** (Claude Code) con herramientas → exchange + **Telegram** como capa de comando/alertas (bot en background en PC del usuario).
2. **Ejecución declarativa:** ejemplo “5x long ETH $10, SL -3%, TP +8%” en una sola instrucción; mismo patrón para **varios** activos en un mensaje.
3. **Reporting:** generación de **dashboard** (equity, P&L, funding por moneda, distancia a liquidación) a partir de datos de la cuenta — útil como plantilla de “capa de presentación” sobre tu API de broker.
4. **Funding scanner:** barrido de pares de futuros/perps, ranking por tasa, **anualización** y sugerencia de **lado** para cobrar funding — automatizable sin LLM; el LLM aquí es orquestador/UI.
5. **Multimodal + contexto:** flujo con **screenshots** (Hyperdash + gráfico BTC) + prompt para sintetizar y abrir trade con gestión de riesgo descrita en texto.
6. **TradingView → webhook → agente:** señal de estrategia dispara ejecución (entrada, SL, TP) sin intervención manual — patrón clásico de **signal-driven execution** con el agente como puente.
7. **Controles mencionados:** permisos de **retiro deshabilitados** en la API, **log** de acciones — buenas prácticas si son ciertas en la implementación concreta.

### S — Síntesis ejecutiva

El vídeo describe un **copiloto/agente** que une **interfaz conversacional** (Telegram), **razonamiento** (Claude) y **conectores** al exchange y a fuentes externas (charts, on-chain público, webhooks). Para tu bot, lo reutilizable es el **mapa de componentes** (ingestión → decisión → ejecución → auditoría → notificaciones), no la promesa de rentabilidad.

### E — Extensión práctica (diseño de bot)

- **Desacoplar:** reglas de riesgo (tamaño, apalancamiento máximo, SL obligatorio) en **código determinístico**; usar el LLM para **interpretación** o **resúmenes** con límites explícitos.
- **Idempotencia y confirmación:** para órdenes grandes, exigir **confirmación humana** o segundo factor tras el intent del agente.
- **Funding como módulo:** implementar el escáner como job programado + base de datos de tasas; el LLM solo formatea o prioriza según políticas que vos fijás.
- **Webhook TradingView:** validar firma/secreto, **whitelist** de símbolos y tamaños, y **cooldown** anti-spam de señales.
- **Observabilidad:** logs estructurados (prompt, herramienta invocada, respuesta del exchange, latencia) — alineado con lo que el vídeo menciona como logging.

---

## Conceptos y terminología

- **Claude Code:** en el vídeo, uso “agentic” de Claude que **invoca acciones** (no solo texto) contra el entorno configurado.
- **Funding rate (perps):** pagos periódicos entre largos y cortos según el desequilibrio del mercado; **positivo** suele implicar que largos pagan cortos (convención puede variar por exchange; verificar en la doc del venue).
- **Hyperliquid / Hyperdash:** DEX / agregación donde el libro de posiciones de wallets puede ser **público** (input para “smart money” heurístico).
- **Webhook:** HTTP callback desde TradingView al ejecutar tu estrategia.

---

## Conclusión (para política del bot)

Tratar el sistema como **automatización potente y peligrosa**: útil para **prototipar** y **operar** con disciplina si encajás **límites de riesgo**, **tests** y **supervisión**. Evitar asumir “edge masivo” solo por IA; el vídeo mezcla **demostración técnica** con **narrativa comercial** — documentalo en tu KB como **referencia de integración**, no como garantía de performance.

---

## Checklist al implementar algo similar

1. [ ] API keys con **mínimos permisos**; sin retiros; rotación y almacenamiento seguro.
2. [ ] **Límites** hardcoded: max notional, max leverage, lista blanca de símbolos.
3. [ ] **Preflight** de cada orden (precio, margen, distancia a liquidación simulada).
4. [ ] **Confirmación** humana para tamaños > umbral o para primer arranque del agente.
5. [ ] **Logging** y alertas de fallo (rate limit, rechazo de orden, desincronización).
6. [ ] Backtest / paper del **módulo de señal** (p. ej. TradingView) independiente del LLM.
7. [ ] Si usás “copy” de wallets públicos: medir **slippage**, **latencia** y **sobreposición** de cohort (sesgo de supervivencia).

---

## Referencias enlazadas

- Vídeo origen: [https://youtu.be/tsCI72TWzsg](https://youtu.be/tsCI72TWzsg)
