# Criterio PRE-REGISTRADO — des-riesgo del largo por régimen VIX (SDD long-regime-derisk)

**Fecha de congelación**: 2026-06-25
**Estado**: pre-registrado **ANTES** de la evaluación walk-forward. Misma disciplina anti
p-hacking que ADR-041, ADR-062, ADR-069/070, y la lección del test verde (ADR-057).

> Los parámetros y el criterio se fijan acá, sin haber visto el resultado de la corrida
> que decide. En la evaluación NO se cambian; se compara contra esto.

---

## 1. La estrategia a evaluar (fija)

Cartera **C** (diversificada + hedge GLD/WMT, producción) **+ des-riesgo del sleeve largo por
percentil del VIX**, sobre el cimiento de cash de **ADR-071**.

Señal: el VIX (`^VIX` de CBOE) — "medidor de miedo". Cada día se mira el **percentil rolling**
del VIX de hoy dentro de su rango de las últimas 52 semanas (252 ruedas, **sin look-ahead**).
A mayor percentil (más miedo), menor exposición del largo (más cash). Histéresis: sube lento.

**Parámetros CONGELADOS** (`config/policy.research_vix_derisk.v1.yaml`):

| Parámetro | Valor |
|-----------|-------|
| `signal` | `vix` |
| `vix_window` | 252 |
| `vix_start_pct` | **0.70** (empieza a des-riesgar) |
| `vix_full_pct` | **0.95** (piso en el caos) |
| `exposure_floor` | **0.40** |
| `max_up_step` | 0.10 (histéresis, sube lento) |

## 2. El experimento (fijo, reproducible)

`scripts/run_wf_research_sim.py`, mismas ventanas/período/aportes que el resto.

| Parámetro | Valor |
|-----------|-------|
| DB | `data/market_backfill.db` (con `^VIX` backfilleado, venue CBOE) |
| Período | 2025-01-01 → 2026-06-12 |
| Aporte mensual | 500.000 ARS |
| Ventanas | burn-in 120 / OOS 60 / step 30 |

Carteras: **C** (`policy.research_hedge_short.v1.yaml`) vs **VIX** (`policy.research_vix_derisk.v1.yaml`).

## 3. Criterio primario (VINCULANTE) — decidido por el operador (opción B)

**La nueva (VIX) tiene que GANARLE a C en ≥ 4 de las 7 ventanas OOS.**

- "Gana la ventana" = el **Sharpe anualizado** de esa ventana de la VIX **≥** el de C en la
  misma ventana (rendimiento ajustado por riesgo, ventana por ventana).
- **PASA** si gana en ≥ 4/7. **NO PASA** si gana en ≤ 3/7.
- Es una **red de consistencia**, NO un margen arbitrario: cachea el ruido tipo D' (que empataba),
  porque exige ganar de forma repetida, no en una sola ventana con suerte.

## 4. Secundarias (informativas, NO deciden)

- Calmar agregado, max drawdown agregado, TWR (¿bajó el drawdown sin matar el retorno?).
- Drawdown de las ventanas V4/V5 (el crash global) vs C.
- Cuántos días el VIX des-riesgó (e<1) y el e mínimo alcanzado.

## 5. Qué NO se permite después

- Cambiar la banda (70→95), el piso (0.40) ni el criterio (4/7) tras ver el resultado.
- Cambiar el Sharpe-por-ventana por otra métrica porque "no dio".
- Promover si no gana 4/7. El gate congelado (ADR-041) no se toca.

Ver: `config/policy.research_vix_derisk.v1.yaml`, `core_sim/long_regime_derisk.py`, ADR-071 (cimiento).
