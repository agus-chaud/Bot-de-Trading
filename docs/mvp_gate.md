# Gate MVP — autorización de capital chico (paso ramp 10%)

**Estado**: definición (no cableado en código todavía).
**Fecha**: 2026-06-16.
**Relación con el gate pleno**: NO lo reemplaza. El gate KPI OOS congelado (**ADR-041**,
burn-in 252 + OOS 60, umbrales pre-registrados) sigue siendo la barrera para escalar a
capital pleno. Este gate MVP es un escalón **anterior y explícitamente más liviano**, solo
para habilitar el **primer 10%** del ramp-up con disciplina, sin esperar 15 meses.

## El problema que resuelve

El gate pleno pide ~312 días hábiles. El track record de paper-live recién arranca, así que
el gate pleno tardaría ~15 meses en tener datos. Eso bloquea cualquier MVP. El gate MVP baja
el listón de evidencia **de forma consciente y documentada** — no lo esconde.

## La idea clave: burn-in histórico + OOS forward

El sistema es **determinístico**: las reglas son fijas, no se ajustan a los datos. Por eso el
tramo de **burn-in** (calentamiento: desplegar capital, juntar historia para indicadores)
**puede usar datos históricos sin contaminar** — no hay overfitting posible porque no hay
ajuste. Lo único que tiene que ser forward (post-congelamiento de reglas, 2026-05-11) es el
tramo de **examen (OOS)**, que es donde se mide la performance sobre datos vírgenes.

```
[ BURN-IN: histórico, ~252 días ]  →  [ OOS: forward paper-live, ~60 días ]
        (calienta la cartera)              (el examen real, post-freeze)
```

**Consecuencia práctica**: no hacen falta 15 meses nuevos. Hace falta **~1 ventana OOS
forward (~60 días hábiles ≈ 2-3 meses)**, con el burn-in cubierto por histórico.

## Criterio del gate MVP

| Parámetro | Gate pleno (ADR-041) | **Gate MVP** |
|-----------|----------------------|--------------|
| Burn-in | 252 días forward | 252 días, **histórico permitido** |
| OOS forward (post-freeze) | varias ventanas | **≥ 1 ventana (~60 días)** |
| `min_oos_windows` | (estricto) | **1** |
| Capital autorizado | hasta 100% (ramp completo) | **solo paso 10%** |
| Umbrales por métrica | los 7 congelados | **los mismos**, sin aflojar |

**Umbrales (NO se aflojan respecto del gate pleno):** Sharpe ≥ 0,30 · Sortino ≥ 0,40 ·
DD total ≥ -18% · DD corto ≥ -10% · DD largo ≥ -25% · turnover largo ≤ 8% · alpha ≥ -2%,
evaluados sobre la ventana OOS forward.

> La única relajación es **cuántas** ventanas forward se exigen (1 en vez de varias) y el
> **techo de capital** (10%, no 100%). Los umbrales de calidad **no se tocan** — bajar el
> listón de evidencia no es bajar el listón de exigencia por trade.

## Qué autoriza y qué NO

- **Autoriza**: operar el **paso 10%** del ramp-up con la cartera diversificada (ADR-063), una
  vez que haya ~60 días hábiles de OOS forward que pasen los umbrales.
- **NO autoriza**: escalar más allá del 10%. Para 25% → 50% → 100% rige el gate pleno
  (más ventanas forward), per **POLICY.md §14**.
- **NO incluye** el sleeve de cobertura (C / hedge): sigue en investigación hasta su check de
  robustez (**ADR-062**).

## Pendiente de cableado

Hoy es una definición. Para hacerlo operativo: agregar un bloque `mvp_gate` en
`config/policy.v1.yaml` (con su validación de schema) y un runner que evalúe la ventana OOS
forward contra estos umbrales, reutilizando `reporting/kpi_walk_forward.py`. Mientras tanto,
la evaluación se hace manual con `scripts/report_kpis_walk_forward.py` sobre la serie de
paper-live, fijando `--wf-burn-in` con histórico y `--wf-oos 60`.

## Ver también

- **ADR-063** (promoción de la cartera diversificada + este gate)
- **ADR-041** (gate KPI OOS congelado), **POLICY.md §13/§14** (umbrales y ramp-up)
- **ADR-062** (hedge en investigación), `docs/hedge_short_criterio_preregistrado.md`
