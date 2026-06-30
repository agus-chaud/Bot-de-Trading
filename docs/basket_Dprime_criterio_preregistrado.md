# Criterio de éxito PRE-REGISTRADO — D' (largo con PG/JNJ a peso bajo, hedge = producción GLD/WMT)

**Fecha de congelación**: 2026-06-25
**Estado**: pre-registrado **ANTES** de correr el walk-forward de D'. Misma disciplina que
`docs/basket_D_criterio_preregistrado.md`, ADR-041, ADR-062, ADR-057.

> Sucesor de ADR-069 (corregido): la D original (PG/JNJ a 0,10 + hedge ampliado) no superó a
> producción C. D' prueba la hipótesis residual: **¿una diversificación CHICA del largo con
> PG/JNJ, con menos tilt defensivo, le gana a producción?** Se fija el criterio sin ver el resultado.

---

## 1. El experimento (fijado, reproducible)

`scripts/run_wf_research_sim.py`, mismas ventanas/período/aportes que D.

| Parámetro | Valor |
|-----------|-------|
| DB | `data/market_backfill.db` (QQQ ya backfilleado en XBUE) |
| Período | 2025-01-01 → 2026-06-12 |
| Aporte mensual | 500.000 ARS |
| Ventanas | burn-in 120 / OOS 60 / step 30 |

**Diseño D' (`config/policy.research_basket_Dprime.v1.yaml`) — menos tilt defensivo:**
- **Largo (50/50 AR/global)**: AR GGAL/PAMP/TXAR 0,1667. Global: SPY 0,15, QQQ 0,15, KO 0,10,
  **PG 0,05, JNJ 0,05** (los defensivos entran CHICOS; SPY/QQQ recuperan exposición a rally).
- **Hedge = producción**: GLD 0,50 / WMT 0,50 (NO se amplía — D6 mostró que MCD/PFE diluyen).
  Regla de des-riesgo a cash igual a C.

**Carteras a comparar** (mismo run): B (`research_diversified`), C (`research_hedge_short`,
producción), D' (`research_basket_Dprime`).

---

## 2. Métrica primaria (VINCULANTE)

**Calmar agregado**. Regla de decisión binaria:

- **PASA** si: `Calmar(D') ≥ 1.05 × Calmar(B)`.
- **NO PASA** si: `Calmar(D') < 1.05 × Calmar(B)`.

## 3. Guardrail anti-degenerado (VINCULANTE)

- `TWR(D') ≥ 0.85 × TWR(B)`. Si lo viola → **NO PASA** (ganó matando el retorno).

## 4. Barra práctica de promoción (VINCULANTE para promover, separada del PASA/NO-PASA)

Aunque D' pase vs B, **solo se considera promover si `Calmar(D') > Calmar(C)`** — tiene que
SUPERAR a producción, no empatarla. Si pasa vs B pero no le gana a C: la diversificación del
largo no justifica el cambio.

## 5. Secundarias (informativas)

- Drawdown de V4/V5 (crash global): ¿mejora vs B y vs C?
- Ventanas OOS que pasan. Sharpe/Sortino. maxDD agregado.

## 6. Qué NO se permite después

- Cambiar Calmar, el +5%, el 15% ni la barra vs C tras ver el resultado.
- Promover sin superar a C. El gate congelado (ADR-041) no se toca.

Ver: `docs/basket_D_criterio_preregistrado.md`, ADR-069 (corregido), `config/policy.research_basket_Dprime.v1.yaml`.
