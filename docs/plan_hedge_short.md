# Plan: sleeve corto como cobertura real (anti-factor) — investigación

**Estado**: IMPLEMENTADO y promovido a producción. Este documento describe el plan original;
el resultado vive en los ADRs. El motor `core_sim/short_hedge_engine.py` + runner compartido
existen (suite verde), la canasta GLD/WMT pasó el criterio pre-registrado (**ADR-062**) y se
promovió a producción por decisión explícita (**ADR-064**, `short_hedge.enabled: true`). La
ampliación de canasta GLD/WMT/MCD/PFE + largo PG/JNJ (experimento D) **NO se promovió** y su
resultado inicial fue corregido (**ADR-069**): ampliar el hedge con MCD/PFE no supera a producción
C; ampliar el largo con PG/JNJ rompe el motor largo (bug de calendario) → bloqueado hasta fix.
Sucesor natural de **ADR-059/060** (diversificación del largo). Objetivo: que el 30% "corto"
deje de ser peso muerto (antes hacía momentum-long en otro mercado) y pase a **cubrir** — subir
o aguantar cuando el largo pierde.

Contexto: el largo diversificado (ADR-060) bajó el peor drawdown de -25,7% a -11,5%, pero
sigue 5/7 ventanas. Las que fallan (4/5, dic-2025→abr-2026) son **selloffs globales**: cayó
AR y global juntos. Ese régimen es el que el corto-cobertura tiene que atacar.

---

## Principios (de las 4 mejoras)

1. **Solo instrumentos que el pipeline pueda traer.** Verificar disponibilidad ANTES de
   diseñar la canasta. (GLD.BA existe; AL30/GD30 NO están en yfinance; DXY no es comprable.)
2. **Correlación medida EN CRISIS, no en promedio.** Las correlaciones se van a 1 en los
   crashes. El número que importa es la correlación durante los selloffs, no la del período.
3. **Cuantificar el costo del hedge.** Una cobertura arrastra retorno en los rallies. Criterio
   de éxito = **rendimiento ajustado por riesgo** (Calmar / Sharpe), no "bajó el drawdown".
4. **Para el crash global, el hedge es CASH.** Ningún activo de riesgo cubre cuando cae todo.
   Regla de des-riesgo a efectivo cuando AR **y** global están ambos en drawdown.

---

## Fases

### Fase 0 — Disponibilidad de instrumentos (medio día)
- **Qué**: confirmar qué candidatos de cobertura tienen datos reales (XBUE/ARS) en el pipeline.
- **Candidatos a verificar**: GLD (oro, CEDEAR — ✅ existe en yfinance), algún CEDEAR
  dólar-linked, KO (defensivo, corr -0,31 vs GGAL ya medida). Bonos (AL30/GD30) → requieren
  conector nuevo (IOL Titulos, otros códigos); **fuera de alcance v1** salvo que se construya.
- **Dónde**: backfill a `data/market_backfill.db` con `scripts/fetch_daily.py --symbols-ar`.
- **Criterio**: lista final de 3–4 instrumentos con ≥300 días XBUE limpios.

### Fase 1 — Correlación condicional a crisis (medio día) — *mejora #2*
- **Qué**: extender `scripts/measure_correlation.py` para calcular correlación **en ventanas de
  selloff** (ago-sep 2025, feb 2026) además del promedio.
- **Criterio de aceptación**: un candidato sirve como hedge solo si su correlación con
  GGAL/PAMP **durante los selloffs** es ≤ 0 (o cercana). Descartar los que se correlacionan en crisis.

### Fase 2 — Universo y policy de investigación (medio día)
- **Qué**: `config/symbols/whitelist_hedge.yaml` (solo instrumentos de Fase 0/1) +
  `config/policy.research_hedge_short.v1.yaml` (variante, como ADR-060).
- **Split interno del 30%** (no tocar `weights.short` todavía): `hedge` ~20% + `tactical` ~10%.
- **Pesos del hedge**: estáticos al inicio (asignación de factor, no ranking de momentum),
  rebalanceo por bandas (copiar `drift_rebalance_threshold_pp` del largo).

### Fase 3 — Pre-registrar el criterio de éxito (1 hora) — *mejora #3*
- **Qué**: escribir, ANTES de correr nada, qué resultado cuenta como "mejor" (anti-p-hacking).
- **Métrica primaria**: **Calmar** (retorno ÷ |max drawdown|) del walk-forward agregado debe
  subir vs el baseline diversificado (ADR-060). Bajar drawdown a costa de matar el retorno NO cuenta.
- **Secundarias**: ventanas que pasan (¿5/7 → 6/7?), drawdown de ventanas 4/5, TWR total.

### Fase 4 — Motor hedge estático + regla de cash (1–2 días) — *mejora #4*
- **Qué**: nuevo path/módulo (`short_hedge_engine.py`, separado y limpio) elegido por policy:
  - **Modo hedge_static**: lee pesos objetivo, rebalancea por bandas (mini-largo dentro del 30%).
  - **Regla de des-riesgo a cash**: si el factor AR (GGAL+PAMP) **y** el global (SPY) están
    ambos en drawdown (umbral pre-registrado), subir cash en vez de comprar riesgo. Ataca 4/5.
- **Cableado**: `core_sim/short_term_day_runner.py` bifurca hedge vs táctico; reusar
  `_resilient_snapshot` (feriados) y excluir símbolos del largo del corto (ya hecho en el sim).
- **Tests**: TWR no se rompe; un día de puro hedge sin momentum no genera ranking; la regla de
  cash dispara cuando ambos factores caen.

### Fase 5 — Walk-forward comparativo (medio día)
- **Qué**: `python scripts/run_wf_research_sim.py --policy config/policy.research_hedge_short.v1.yaml`
- **Comparar 3 carteras**: concentrada (baseline) → diversificada (ADR-060) → diversificada+hedge.
- **Mirar las ventanas 4/5 específicamente**: ¿el cash/hedge las salvó?

### Fase 6 — Decisión y ADR (1 hora)
- Si la **métrica primaria pre-registrada (Calmar) mejora** → ADR-061 + plan de promoción gradual.
- Si baja el drawdown pero también el retorno (Calmar no mejora) → documentar honestamente que
  el hedge cuesta más de lo que aporta; quizá el problema 4/5 no se resuelve con activos AR.
- **El gate congelado (ADR-041) no se toca.** Promoción al default = trabajo aparte (rompe tests).

---

## Lo que NO hacer (heredado de la sesión)
- No meter bonos sin conector real (AL30/GD30 no están en el pipeline).
- No medir solo correlación promedio (se va a 1 en crisis).
- No declarar éxito por "bajó el drawdown" (cash baja el drawdown y no es estrategia).
- No promover al default sin ADR ni sin que la métrica ajustada por riesgo mejore.

## Frase para defensa oral
> "El corto no cubría porque hacía momentum-long en otro mercado, no exposición anti-factor.
> Lo rediseñamos como cobertura explícita, midiendo la correlación **en las crisis** (no en
> promedio), cuantificando su costo en los rallies, y con una regla de des-riesgo a cash para
> el único régimen que ningún activo cubre: el crash global."
