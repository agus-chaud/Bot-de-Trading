# Criterio de éxito PRE-REGISTRADO — canasta ampliada D (largo +PG/JNJ, hedge +MCD/PFE)

**Fecha de congelación**: 2026-06-25
**Estado**: pre-registrado **ANTES** de correr el walk-forward comparativo.
**Por qué existe**: anti p-hacking. Misma disciplina que el gate KPI OOS (ADR-041), el criterio
del hedge GLD/WMT (`docs/hedge_short_criterio_preregistrado.md`, ADR-062) y la lección del test
verde (ADR-057). El criterio se fija ahora, sin haber visto el resultado del experimento.

> Si después de medir quisiera "ajustar" el umbral para que pase, este documento (versionado,
> con fecha previa al resultado) deja en evidencia que sería autoengaño.

---

## 0. Fase 1 ya superada (inclusión por correlación en crisis)

Medido el 2026-06-25 con `scripts/measure_correlation.py --hedge` (ventanas de selloff
ago-sep 2025 + feb 2026), corr media en crisis vs factor AR (GGAL/PAMP):

| Candidato | Corr en crisis | Destino | Veredicto |
|-----------|----------------|---------|-----------|
| JNJ | **-0,40** | largo (global) | ✅ <= 0 |
| MCD | -0,34 | hedge | ✅ <= 0 |
| PG | -0,30 | largo (global) | ✅ <= 0 |
| PFE | -0,21 | hedge | ✅ <= 0 |

Los cuatro pasan el criterio de Fase 1. (PG/JNJ requirieron backfill histórico en
`market_backfill.db` para cubrir las ventanas de crisis; antes daban `nan`.)

---

## 1. El experimento (fijado, reproducible)

Walk-forward de investigación con aportes mensuales (TWR), `scripts/run_wf_research_sim.py`.

| Parámetro | Valor congelado |
|-----------|-----------------|
| Base de datos | `data/market_backfill.db` |
| Período | 2025-01-01 → 2026-06-12 |
| Aporte mensual | 500.000 ARS |
| Ventanas | burn-in 120 / OOS 60 / step 30 (días de mercado) |
| Métricas | `reporting/twr_walk_forward.py` (TWR, no equity con aportes) |

**Tres carteras a comparar** (mismas ventanas, mismo período, mismos aportes):

| Cartera | Policy | Descripción |
|---------|--------|-------------|
| B — Diversificada (baseline) | `config/policy.research_diversified.v1.yaml` | ADR-060, corto = momentum |
| C — Diversificada + hedge GLD/WMT | `config/policy.research_hedge_short.v1.yaml` | ADR-062/064 (producción actual) |
| **D — Canasta ampliada** | `config/policy.research_basket_D.v1.yaml` | largo +PG/JNJ, hedge +MCD/PFE |

---

## 2. Métrica primaria (VINCULANTE)

**Calmar agregado** = `annualized_twr(serie) ÷ |max_drawdown(índice_TWR)|`, idéntico para las tres.

### Regla de decisión (binaria)

- **PASA** si: `Calmar(D) ≥ 1.05 × Calmar(B)` (mejora ≥ +5% relativo vs la diversificada).
- **NO PASA** si: `Calmar(D) < 1.05 × Calmar(B)` (incluye empate y mejora marginal).

(Se compara contra **B** —no contra C— por consistencia con el criterio del hedge previo:
B es la base diversificada sin apuesta direccional. C se reporta como contexto, no decide.)

## 3. Guardrail anti-degenerado (VINCULANTE)

- **TWR acumulado de D ≥ 0.85 × TWR acumulado de B.** Si D mejora Calmar pero viola esto
  (ganó matando el retorno) → **NO PASA**.

## 4. Métricas secundarias (INFORMATIVAS, no deciden)

- Ventanas OOS que pasan (¿D mejora el 5/7 de B?).
- **Drawdown de las ventanas V4 y V5** (dic-2025 → abr-2026, el crash GLOBAL): el objetivo
  explícito de esta ampliación. Se observa si mejoran vs B y vs C.
- TWR acumulado y max drawdown agregado de cada cartera.
- Sharpe / Sortino agregados.

## 5. Qué se decide según el resultado

| Resultado | Acción |
|-----------|--------|
| Primaria PASA **y** guardrail OK **y** V4/V5 mejoran | ADR + plan de promoción gradual (gate ADR-041 intacto). |
| Calmar no sube o guardrail violado | Documentar honestamente: la ampliación no aporta. NO promover. |
| Calmar sube pero V4/V5 NO mejoran | Sospechar que el win es direccional (como el oro en ADR-062): pedir robustez antes de promover. |

## 6. Lo que este documento NO permite hacer después

- Cambiar la métrica primaria (Calmar) ni el umbral (+5%) ni el guardrail (15%) tras ver el resultado.
- Promover D sin que la primaria pase. El gate congelado (ADR-041) no se toca.

Ver: `docs/hedge_short_criterio_preregistrado.md` (criterio análogo C), ADR-060/062/064,
`config/policy.research_basket_D.v1.yaml`, `config/symbols/whitelist_hedge_D.yaml`.
