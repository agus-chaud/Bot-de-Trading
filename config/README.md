# Configuración versionada (`policy.v{N}.yaml`)

## Convención de archivos

- Archivo activo de ejemplo: `policy.v1.yaml` (`schema_version: 1`).
- Al romper compatibilidad de campos (renombres, tipos distintos, claves obligatorias nuevas), incrementar `schema_version` y crear `policy.v2.yaml`, manteniendo migración documentada.

## JSON Schema (CI)

- Archivo: `policy.v1.schema.json` (Draft 2020-12).
- En pipeline: `python -m pytest tests/test_policy_schema.py` valida tipos, enums, claves obligatorias y sumas `weights` / `geo`.

## Validaciones mínimas (parser / CI)

| Regla | Descripción |
|-------|-------------|
| Schema | `Draft202012Validator` sobre el YAML cargado. |
| Suma `weights` | `weights.short + weights.long == 1.0` (tolerancia numérica 1e-6). |
| Suma `geo` | `geo.AR + geo.US == 1.0`. |
| Kill switch | `short_kill_switch_monthly_dd <= 0` (típicamente negativo, p.ej. -0.08). |
| Whitelists | Si `execution_mode` o riesgo exigen símbolos, las rutas `whitelist_*_file` deben existir o usar `inline_*` en entornos de prueba. |
| Coherencia mercados | Claves bajo `markets` deben alinearse con regiones usadas en datos (US/AR). |
| Calendario | `calendar.source_of_truth` debe apuntar a un archivo único para sesiones US y días hábiles AR. |
| Corporate actions | `corporate_actions.us_file` debe existir y declarar `split` y `dividend` como tipos soportados en v1. |

## `execution_mode`

- **`semi_auto`**: las órdenes que pasan `risk_guardrails` se persisten como `pending_orders` hasta confirmación explícita (CLI/archivo/Telegram en fases posteriores). No se llama a fill en el simulador hasta aprobar.
- **`auto`**: si pasa riesgo, la orden sigue hacia `paper_broker_sim` sin gate humano.

El comportamiento exacto de la cola se implementa en Fase 2+; aquí queda el **contrato** de configuración.

## Relación con `POLICY.md`

Los números operativos y la matriz de violaciones viven en `POLICY.md`. El YAML es la **fuente parseable** para código y tests; ante conflicto, actualizar ambos en el mismo cambio.

## Calendario y corporate actions (v1)

- Calendario único: `config/calendars/trading_days.v1.yaml` (sesiones US + días hábiles AR).
- Corporate actions US: `config/corporate_actions/us_actions.v1.yaml` con soporte mínimo para:
  - `dividend` (campo `cash_amount`)
  - `split` (campo `split_ratio`)
