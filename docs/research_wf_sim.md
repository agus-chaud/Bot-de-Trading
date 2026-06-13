# Simulador walk-forward de investigación (TWR + aportes mensuales)

> **Modo INVESTIGACIÓN — no es el gate de producción ni el paper-live productivo.**
> Sirve para entender cómo se comporta la estrategia con un modelo de capital realista.
> El gate KPI OOS congelado (252+60, POLICY.md §13) sigue siendo el único juez del paso
> a capital real. Ver **ADR-058**.

## Qué resuelve

Dos cosas que el what-if simple (`run_whatif_sim.py`) no cubre:

1. **Aportes mensuales (DCA)**: en vez de "monto inicial y nunca más", modela aportar
   capital todos los meses (lo realista: invertir el ahorro). Cada primer día hábil de
   mes el capital acumulado crece y los motores despliegan la plata nueva según el 30/70.
2. **Métricas TWR (time-weighted)**: con aportes, el equity crudo **miente** — un depósito
   se leería como ganancia gigante. El TWR **excluye el aporte de la base** antes de medir,
   así aísla el rendimiento de la estrategia del timing de tus depósitos.

## Por qué TWR (y no el retorno del equity)

Si el 1 de mes inyectás 500k, el equity salta de 1.000.000 a 1.500.000 sin que la
estrategia haya ganado nada. El retorno crudo (`V_t/V_{t-1}-1`) leería **+50%** (artefacto,
misma familia que los bugs #3/#6/#11). El TWR calcula `r_t = V_t/(V_{t-1}+C_t)-1`: el aporte
`C_t` entra a la base pero no cuenta como retorno → mide la estrategia, no el depósito.

| Métrica | Qué mide | Cuándo usarla |
|---------|----------|---------------|
| **TWR** (time-weighted) | Rendimiento de la estrategia, sin efecto de aportes | Juzgar la estrategia / comparar vs benchmark |
| **MWR / TIR** (money-weighted) | Tu experiencia real en pesos, sensible al timing | "Cuánta plata terminé teniendo" |

El **drawdown** se mide sobre el **índice TWR**, no sobre el equity: si lo midieras sobre el
equity, los aportes lo esconderían (la curva sube porque metés plata, tapando las pérdidas).

## Walk-forward

Ventanas rolling `[burn_in | oos]` avanzando `step` días. Cada ventana OOS (out-of-sample)
se puntúa contra umbrales (réplica de los del gate, en modo exploratorio). Necesita al menos
`burn_in + oos` días; si no alcanza, lo reporta honestamente (no inventa ventanas).

Los parámetros son **libres** acá (default 120/60/30). Eso NO es aflojar el gate: el gate de
producción está congelado (252+60). Si quisieras un gate distinto, el camino legítimo es
pre-registrarlo + ADR, no bajarlo tras ver resultados (eso sería *p-hacking*).

## Uso

```bash
# Default: backfill DB, 2025-01-01 -> hoy, 500k/mes, 120+60 paso 30
python scripts/run_wf_research_sim.py

# Parámetros explícitos
python scripts/run_wf_research_sim.py \
    --db data/market_backfill.db \
    --start 2025-01-01 --end 2026-06-12 \
    --contrib 500000 --burn-in 120 --oos 60 --step 30 \
    --out-json data/_sim/wf_research_report.json
```

Corre sobre una **copia aislada** de la DB (`data/_sim/wf_research.db`); nunca toca
`market.db` productivo. Requiere historia suficiente (usar la DB backfilleada, no la de
producción que es forward-only y corta).

## Arquitectura

| Pieza | Rol |
|-------|-----|
| `reporting/twr_walk_forward.py` | Módulo **puro** (sin I/O): TWR, índice, Sharpe/Sortino, max drawdown, MWR/TIR, ventanas walk-forward. Testeado de forma aislada. |
| `scripts/run_wf_research_sim.py` | CLI: corre el pipeline 30/70 día a día con aportes, arma la serie y la pasa por el módulo puro. |
| `tests/test_twr_walk_forward.py` | Tests del módulo, con la prueba clave de que **los aportes no inflan el retorno**. |

Reusa la valuación resiliente por venue nativo (`_resilient_snapshot`) — no colapsa en
feriados AR. Excluye los símbolos del sleeve largo (SPY/GGAL/PAMP) del universo del corto
para evitar el conflicto "market mismatch" (mismo símbolo en dos monedas).

## Límites conocidos

- Requiere historia larga: con la DB de producción (forward-only, ~4 meses) no alcanza para
  120+60. Por eso existe el backfill (`market_backfill.db`, ~360 días desde 2024-12-30).
- Es paper sobre datos históricos: una ventana linda no prueba edge. El juez sigue siendo el
  gate OOS congelado con suficiente historia real.
