# AGENTS — contexto para humanos y agentes (Bot de Trading, paper-first)

## Propósito del repo

Bot de trading/inversión en **Python**, perfil **moderado**, split **30/70** (corto/largo) y **20/80** (AR/US), **paper trading** con datos reales, riesgo **determinístico** antes que heurísticas opacas o LLM en la ejecución.

Problema que atacamos: el dueño del repo no quiere depender de trading manual diario (tiempo + sesgo emocional), pero sí construir un proceso con probabilidad de retorno sostenible. El enfoque es **proceso primero, dinero después**: metodología medible en paper, luego ramp-up controlado a capital real.

Plan maestro: `.cursor/plans/bot_trading_paper-first_155d6f04.plan.md`.

## Fuentes de verdad

| Qué | Dónde |
|-----|--------|
| Política humana (umbrales, matriz de violaciones, mapa de datos) | `POLICY.md` |
| Contrato parseable (YAML) | `config/policy.v1.yaml` |
| Validación estructural CI | `config/policy.v1.schema.json` + `tests/test_policy_schema.py` |
| Listas de símbolos | `config/symbols/whitelist_us.yaml`, `whitelist_ar.yaml` |

Ante conflicto numérico entre `POLICY.md` y YAML, **actualizar ambos en el mismo cambio** y anotar el motivo en el commit.

## Límites duros (todos los agentes)

1. **Sin secretos en el repo**: API keys, tokens, cookies → `.env` o gestor de secretos; nunca en YAML de ejemplo ni en tests commiteados.
2. **Paper-first por defecto**: no integrar ejecución live hasta fases y gates documentados en el plan.
3. **Riesgo en código, no en prompts**: guardarraíles, kill switch y límites por bucket no dependen de LLM.
4. **Cambios acotados**: no refactor masivo fuera del issue; seguir estilo y layout existente.

## Roles sugeridos (agent teams lite)

Usar roles para **acotar** qué toca cada subagente o PR. Solapamiento mínimo.

| Rol | Responsabilidad | Rutas típicas |
|-----|-----------------|---------------|
| **Spec / policy** | `POLICY.md`, `config/*.yaml`, `config/symbols/*`, schema JSON | `POLICY.md`, `config/` |
| **Core sim** | Paper broker, ledger, costos, event engine | `core_sim/paper_broker_sim.py`, `core_sim/ledger.py`, `core_sim/event_engine.py`, `core_sim/cost_model.py` |
| **Data** | Snapshot OHLCV + historial, whitelist, calendario en `MarketOpen`, corporate actions v1 | `core_sim/short_term_day_runner.py`, `core_sim/long_term_engine.py` (input contract), `core_sim/calendar_store.py` |
| **Engines** | Señales → intents; integración diaria corta; motor largo mensual por bandas; pre-gate walk-forward OOS | `core_sim/short_term_engine.py`, `core_sim/short_term_day_runner.py`, `core_sim/long_term_engine.py`, `core_sim/short_term_pre_gate.py`, `scripts/run_short_term_pre_gate.py` |
| **Risk** | Kill switch mensual corto, pérdida diaria bucket corto, ventanas no-trade, `halt_on_data_quality`, whitelist defensiva; allocator 30/70 + 20/80 en sizing | `core_sim/short_term_day_runner.py` (handlers `propose_orders` / `risk_check`), `config/policy.v1.yaml` → `risk`, `weights`, `geo` |
| **QA / CI** | Tests por **comportamiento** (ver *Smart testing*), schema policy, cobertura `core_sim` en CI | `tests/`, `.github/workflows/ci.yml` |

Un agente en rol **Spec** no debería implementar broker simulado; uno en rol **Core sim** no debería reescribir listas blancas sin coordinación con **Spec**.

## Smart testing (criterio de calidad)

Alineado a la skill **smart-testing**: probamos **comportamiento observable**, no detalles de implementación; prioridad **reglas de negocio / riesgo** → integración (`DailyEventBacktester` + pipeline corto) → helpers puros. Evitar sobre-mocking de módulos propios (`ledger`, `PaperBrokerSim`): preferir instancias reales en tests de integración. Los nombres de test describen el efecto (“should…”, “blocks…”, “stops…”). Cobertura es indicador **secundario** a la confianza en reglas; en CI se exige umbral mínimo sobre `core_sim` (ver workflow).

## Comandos útiles

```text
pip install -r requirements.txt
python -m pytest tests/ -v
python -m pytest tests/ -v --cov=core_sim --cov-report=term-missing
```

## Convenciones

- **Idioma**: documentación de producto/política en español; código y nombres de módulos en inglés salvo dominio AR/US ya acordado.
- **Versionado de config**: subir `schema_version` y archivo `policy.v{N}.yaml` al romper el contrato; mantener `policy.v1.schema.json` alineado a v1 o renombrar a `policy.v2.schema.json` con el nuevo major.

## Referencias

- Notas en `knowledge-base/` (contexto de producto, no normativa operativa).
- `README.md` (narrativa de negocio y mapa breve de componentes para retomada rápida).
