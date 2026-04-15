# AGENTS — contexto para humanos y agentes (Bot de Trading, paper-first)

## Propósito del repo

Bot de trading/inversión en **Python**, perfil **moderado**, split **30/70** (corto/largo) y **20/80** (AR/US), **paper trading** con datos reales, riesgo **determinístico** antes que heurísticas opacas o LLM en la ejecución.

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
| **Core sim** | Paper broker, ledger, costos, eventos | (Fase 2) `src/...` según se cree el árbol |
| **Data** | Esquema OHLCV, conectores, calendario | (Fase 2+) capa datos |
| **Engines** | `short_term_engine`, `long_term_engine` solo señales → órdenes intent | motores aislados del broker |
| **Risk** | `risk_guardrails`, kill switch -8% mensual corto, integración con allocator | núcleo riesgo |
| **QA / CI** | Tests, validación schema, pipelines | `tests/`, `.github/workflows/` |

Un agente en rol **Spec** no debería implementar broker simulado; uno en rol **Core sim** no debería reescribir listas blancas sin coordinación con **Spec**.

## Comandos útiles

```text
pip install -r requirements.txt
python -m pytest tests/ -v
```

## Convenciones

- **Idioma**: documentación de producto/política en español; código y nombres de módulos en inglés salvo dominio AR/US ya acordado.
- **Versionado de config**: subir `schema_version` y archivo `policy.v{N}.yaml` al romper el contrato; mantener `policy.v1.schema.json` alineado a v1 o renombrar a `policy.v2.schema.json` con el nuevo major.

## Referencias

- Notas en `knowledge-base/` (contexto de producto, no normativa operativa).
