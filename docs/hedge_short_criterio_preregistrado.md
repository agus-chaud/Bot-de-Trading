# Criterio de éxito PRE-REGISTRADO — sleeve corto como cobertura (plan_hedge_short Fase 3)

**Fecha de congelación**: 2026-06-15
**Estado**: pre-registrado **ANTES** de correr el walk-forward comparativo (Fase 5).
**Por qué existe**: anti p-hacking. El criterio se fija ahora, sin haber visto el
resultado del experimento con cobertura. En Fase 5/6 NO se cambia: se compara contra esto.
Misma disciplina que el gate KPI OOS congelado (ADR-041) y la lección del test verde (ADR-057).

> Si después de medir quisiera "ajustar" el umbral para que pase, este documento (versionado,
> con fecha previa al resultado) deja en evidencia que sería autoengaño. Ese es el punto.

---

## 1. El experimento (fijado, reproducible)

Walk-forward de investigación con aportes mensuales (TWR), `scripts/run_wf_research_sim.py`.

| Parámetro | Valor congelado |
|-----------|-----------------|
| Base de datos | `data/market_backfill.db` |
| Período | 2025-01-01 → 2026-06-12 (último día XBUE limpio) |
| Aporte mensual | 500.000 ARS (primer día hábil de mes) |
| Ventanas | burn-in 120 / OOS 60 / step 30 (días de mercado) |
| Métricas | `reporting/twr_walk_forward.py` (TWR, no equity con aportes) |

**Tres carteras a comparar** (mismas ventanas, mismo período, mismos aportes):

| Cartera | Policy | Commit congelado |
|---------|--------|------------------|
| A — Concentrada (baseline original) | `config/policy.v1.yaml` (largo 3 nombres) | producción |
| B — Diversificada (ADR-060) | `config/policy.research_diversified.v1.yaml` | `e13999f` |
| C — Diversificada + hedge | `config/policy.research_hedge_short.v1.yaml` | `e13999f` |

> C requiere el motor `short_hedge_engine.py` (Fase 4), que aún no existe. Esta
> pre-registración se escribe antes de implementarlo — como corresponde.

---

## 2. Métrica primaria (VINCULANTE)

**Calmar agregado** sobre la serie diaria continua de toda la corrida, calculado
**idéntico** para las tres carteras con funciones que ya existen:

```
Calmar = annualized_twr(serie)  ÷  |max_drawdown(índice_TWR de la serie)|
```

(`annualized_twr` y `max_drawdown` de `reporting/twr_walk_forward.py`. El drawdown se
mide sobre el índice TWR, que excluye los aportes — medirlo sobre el equity con aportes
lo escondería.)

### Regla de decisión (binaria, sin zona gris discrecional)

- **PASA** si: `Calmar(C) ≥ 1.05 × Calmar(B)`
  (la cobertura mejora el retorno ajustado por riesgo al menos **+5% relativo** vs la
  diversificada — margen mínimo para no festejar ruido).
- **NO PASA** si: `Calmar(C) < 1.05 × Calmar(B)` (incluye empate y mejora marginal).

**Por qué Calmar y no "bajó el drawdown":** pasar a efectivo baja el drawdown
trivialmente sin que eso sea una buena estrategia. Calmar premia bajar el drawdown
*sin* matar el retorno. Es el criterio que castiga la solución tramposa.

---

## 3. Guardrail anti-degenerado (VINCULANTE)

Calmar se puede inflar llevando casi todo a cash (retorno→0, drawdown→0). Para
bloquear esa salida degenerada:

- **TWR acumulado de C ≥ 0.85 × TWR acumulado de B** (la cobertura no puede destruir
  más del **15% relativo** del retorno de la diversificada).

Si C mejora Calmar **pero** viola este guardrail → **NO PASA** (ganó por matar el retorno,
no por cubrir mejor).

---

## 4. Métricas secundarias (INFORMATIVAS, no deciden)

Se reportan para entender el resultado, pero **no** cambian el veredicto:

- Ventanas OOS que pasan: B da 5/7; se observa si C llega a ≥6/7.
- **Drawdown de las ventanas 4 y 5** (dic-2025 → abr-2026, el crash GLOBAL): son las que
  la cobertura apunta. Se observa si mejoran (menos negativo) vs B.
- TWR acumulado total de cada cartera.
- Sharpe / Sortino agregados (contexto de forma de la curva).

---

## 5. Qué se decide en Fase 6 según el resultado

| Resultado | Acción |
|-----------|--------|
| Primaria PASA **y** guardrail OK | ADR-062 + plan de promoción gradual (sin tocar el gate ADR-041). |
| DD baja pero Calmar no sube, **o** guardrail violado | Documentar honestamente: la cobertura cuesta más de lo que aporta. NO promover. |
| Ventanas 4/5 (crash global) no mejoran | Confirma que el factor AR no las cubre → la respuesta es la regla de des-riesgo a **cash** (Fase 4) o reducir el corto, no más activos AR. |

**Referencia (NO vinculante, contexto ADR-060)**: la diversificada dio TWR +39,46%, peor
drawdown de ventana -11,5%, 5/7. La comparación que decide es **mismo-run B vs C**, no
contra estos números (evita comparar contra una corrida vieja).

---

## 6. Lo que este documento NO permite hacer después

- Cambiar la métrica primaria de Calmar a otra porque "Calmar no dio".
- Mover el umbral de +5% o el guardrail de 15% después de ver el resultado.
- Promover C al default sin que la primaria pase (el gate congelado no se toca).

Ver: `docs/plan_hedge_short.md` (Fases), ADR-058 (simulador), ADR-060 (diversificada),
ADR-061 (canasta GLD+KO), ADR-041 (gate congelado), ADR-057 (test verde).
