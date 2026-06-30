# Criterio PRE-REGISTRADO — VIX de-risk CONSERVADOR (banda 80→97) + criterio Calmar

**Fecha de congelación**: 2026-06-25
**Estado**: pre-registrado **ANTES** de la evaluación. Experimento **NUEVO** (no retoca el de
ADR-072). Motivado por la lección de ADR-072: el criterio por-ventana disfavorece a un de-risk;
acá se usa el lente correcto (**Calmar agregado con margen real**) y una banda **más conservadora**
(des-riesga menos seguido).

> Params y criterio fijados acá, sin ver el resultado. En la evaluación NO se cambian.

---

## 1. La estrategia a evaluar (fija)

Cartera **C** (diversificada + hedge GLD/WMT) + des-riesgo del largo por percentil del VIX, igual
que ADR-072 pero con **banda más conservadora**:

| Parámetro | ADR-072 (descartado) | **Este (conservador)** |
|-----------|----------------------|------------------------|
| `vix_start_pct` | 0.70 | **0.80** (des-riesga menos seguido) |
| `vix_full_pct` | 0.95 | **0.97** (piso solo en pánico extremo) |
| `exposure_floor` | 0.40 | 0.40 |
| `vix_window` / `max_up_step` | 252 / 0.10 | 252 / 0.10 |

Policy: `config/policy.research_vix_derisk_conservative.v1.yaml`.

## 2. El experimento (fijo)

`scripts/run_wf_research_sim.py`, `data/market_backfill.db` (con `^VIX`), 2025-01-01 → 2026-06-12,
500k/mes, ventanas 120/60/30. Carteras: **C** vs **VIX-conservador**.

## 3. Criterio primario (VINCULANTE) — Calmar agregado con margen real

- **PASA** si: `Calmar(VIX) ≥ 1.05 × Calmar(C)` (mejora ≥ **+5% relativo** del retorno ajustado
  por riesgo — margen que descarta el ruido, consistente con el criterio del hedge y de D').
- **NO PASA** si: `Calmar(VIX) < 1.05 × Calmar(C)` (incluye empate y mejora marginal).

**Por qué Calmar agregado y no Sharpe por ventana:** un de-risk sacrifica las calmas para
proteger en el crash — por diseño es inconsistente ventana a ventana. El Calmar agregado mide lo
que un seguro debe dar: **mejor rendimiento ajustado por riesgo en el total**, premiando bajar el
drawdown sin matar el retorno.

## 4. Guardrail anti-degenerado (VINCULANTE)

- `TWR(VIX) ≥ 0.85 × TWR(C)`. Un de-risk puede inflar el Calmar bajando el retorno (todo a cash):
  este guardrail bloquea esa salida tramposa. Si lo viola → **NO PASA**.

## 5. Secundarias (informativas, NO deciden)

- Max drawdown agregado (¿bajó vs C?), drawdown de V4/V5.
- Ventanas que mejora (contexto, NO criterio).
- Días que des-riesgó y `e` mínimo (¿la banda conservadora frena menos seguido?).

## 6. Qué NO se permite después

- Cambiar la banda (80→97), el piso, el +5% ni el guardrail 15% tras ver el resultado.
- Cambiar la métrica primaria porque "no dio".
- Promover si no pasa. Gate congelado (ADR-041) intacto.

Ver: ADR-072 (la iteración descartada y su lección), `config/policy.research_vix_derisk_conservative.v1.yaml`.
