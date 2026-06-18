# Monitor paper-live y export JSON (dashboard)

Documentación operativa del tablero de monitoreo **solo lectura** y del export estático
para la demo web (MVP interfaz, Fase 1).

---

## En simple: qué es y para qué sirve

El bot opera en **papel** cada día y guarda todo en `data/market.db`: equity, posiciones,
operaciones, kill switch, etc.

El **dashboard** es la ventana para **ver** eso sin tocar el motor:

| Pieza | Qué hace | Analogía |
|-------|----------|----------|
| **Monitor local** (`run_dashboard.py`) | Servidor FastAPI en tu PC que lee la DB y muestra gráficos | Cámara en vivo conectada a la DB |
| **Export JSON** (`export_dashboard_payload.py`) | Saca una **foto** de los mismos datos en un archivo | Foto diaria del estado del bot |
| **Demo web** (`web/`, Next.js) | Misma UI que el monitor, pero lee el JSON estático (Vercel) | Escaparate para la cátedra sin Python |

**Importante:** ninguna de las dos cosas **opera** ni **mueve plata**. Solo leen lo que ya
corrió `run_paper_live.py`.

---

## De dónde salen los datos

```
run_paper_live.py  →  escribe en market.db (snapshots, fills, meta)
                              ↓
              DashboardService.build_payload()  ← una sola fuente de verdad
                    ↓              ↓                    ↓
         run_dashboard.py   export_dashboard_payload.py   (mismo contrato)
         GET /api/dashboard  data/dashboard_payload.json
              ↓                        ↓
         FastAPI :8765          web/public/ (Next.js, F1-04)
```

Tanto la API `GET /api/dashboard`, el export y la demo web consumen el **mismo contrato**
(`export_version: "1"`) producido por **`dashboard/service.py`**.
Si el monitor local, el JSON y `web/` muestran lo mismo, el cableado está bien.

---

## Monitor local (desarrollo / operador)

```text
python scripts/run_dashboard.py --fetch-remote --sync-db
```

Abre **http://127.0.0.1:8765** con:

- Curva de equity
- Posiciones abiertas (replay de fills + MTM)
- Últimas operaciones
- Estado de riesgo (kill switch, drawdown)
- KPIs (Sharpe, Calmar, max DD)
- Alertas (días sin corrida, DB desactualizada, errores de fetch)

### DB desactualizada

El monitor lee la copia **local** de `market.db`. La versión “oficial” del día a día vive en
la rama **`paper-live-data`** (Git LFS), actualizada por GitHub Actions.

Si ves datos viejos:

1. Cerrá el dashboard (en Windows el archivo puede quedar bloqueado).
2. Sincronizá: `python scripts/run_dashboard.py --sync-db`
3. O manual: `git fetch origin` + `git checkout origin/paper-live-data -- data/market.db` + `git lfs checkout data/market.db`

`dashboard/db_freshness.py` compara tu copia local con `origin/paper-live-data` y muestra
un banner si está stale.

---

## Export JSON (F1-01 — base para Vercel)

```text
python scripts/export_dashboard_payload.py
python scripts/export_dashboard_payload.py --pretty
```

Por defecto escribe **`data/dashboard_payload.json`** (gitignored en `main`; el CI commitea con `-f` en `paper-live-data`, ver F1-03).

### Qué contiene el archivo

| Bloque | Contenido |
|--------|-----------|
| `meta` | Modo, moneda, capital inicial, último día con snapshot, equity actual |
| `equity_curve` | Serie diaria de equity total / corto / largo |
| `positions` | Posiciones abiertas al último día |
| `recent_fills` | Últimas ~25 operaciones |
| `risk` | Kill switch, umbrales, si “se puede operar” hoy |
| `kpis` | Sharpe, Calmar, max drawdown (si hay historia suficiente) |
| `alerts` | Avisos automáticos (huecos, fetch, kill switch) |
| `data_freshness` | Si la DB local está alineada con remoto |
| `generated_at` | Timestamp UTC del export |
| `export_version` | Versión del formato (hoy: `"1"`) |
| `export_source` | Paths de DB/policy usados (auditoría local) |

### Flags útiles

| Flag | Default | Uso |
|------|---------|-----|
| `--db` | `data/market.db` | Otra base SQLite |
| `--policy` | `config/policy.v1.yaml` | Policy para umbrales de riesgo/KPI |
| `--calendar` | `config/calendars/trading_days.v1.yaml` | Alertas de días hábiles |
| `--mode` | `paper_live` | Modo en snapshots |
| `--out` | `data/dashboard_payload.json` | Ruta de salida |
| `--pretty` | off | JSON indentado (diff humano) |

### Códigos de salida

| Código | Significado |
|--------|-------------|
| `0` | OK, JSON escrito |
| `1` | No existe la DB (`--db`) |
| `2` | Otro error (payload inválido, permisos, etc.) |

### Artifact en GitHub Actions (F1-02)

Tras cada corrida exitosa de **Paper Live Daily**, el workflow:

1. Ejecuta `export_dashboard_payload.py` sobre la `market.db` recién actualizada (`--out data/dashboard_payload.json`).
2. Sube **`data/dashboard_payload.json`** como artifact **`dashboard-payload`** (retención 90 días).
3. Commitea el mismo archivo en **`paper-live-data`** junto con la DB (**F1-03** / **ADR-065**).

**Descargarlo:** repo en GitHub → **Actions** → corrida de *Paper Live Daily* → sección **Artifacts** → `dashboard-payload`.

Ese archivo es el que consumirá la web en Vercel (F1-04): no hace falta clonar el repo ni correr Python para ver el estado del paper-live del día.

---

## Arquitectura Vercel (F1-03)

**Decisión cerrada** — ver **ADR-065**. Resumen operativo:

### Opciones evaluadas

| | Opción A — JSON estático | Opción B — SQLite lite |
|--|--------------------------|-------------------------|
| **Qué** | `dashboard_payload.json` pre-agregado por `DashboardService` | `market.db` (o réplica) en Blob/Turso + consultas en runtime |
| **Tamaño hoy** | ~5 KB | ~1,4 MB+ (crece con OHLCV/fills) |
| **Sync** | Commit en `paper-live-data` + rebuild Vercel | Job aparte, LFS, credenciales Blob/DB |
| **Runtime Vercel** | Archivo estático en `public/` | WASM sql.js, API route, o Turso |
| **Veredicto** | **Elegida para MVP** | Descartada (sobredimensionada para demo solo lectura) |

### Cable artifact → Vercel (modelo adoptado)

```
paper_live_daily.yml
  ├─ run_paper_live.py        → market.db
  ├─ export_dashboard_payload → dashboard_payload.json
  ├─ upload-artifact          → backup Actions (90 d)
  └─ git commit -f            → data/dashboard_payload.json en paper-live-data
                                        ↓
                              push dispara Vercel (rama paper-live-data)
                                        ↓
                              web/ build copia JSON → public/dashboard_payload.json
                                        ↓
                              Next.js lee /dashboard_payload.json (F1-04)
```

| Pregunta | Respuesta |
|----------|-----------|
| ¿Fuente primaria en prod? | `data/dashboard_payload.json` en rama **`paper-live-data`** |
| ¿Cuándo se actualiza la web? | Tras cada paper-live exitoso → push → **rebuild automático** en Vercel |
| ¿Para qué sirve el artifact Actions? | Backup, auditoría y descarga manual (no runtime) |
| ¿Auth? | **F1-06**: middleware en la app; el JSON viaja en el mismo deploy, no URL raw pública |
| ¿Repo privado? | Compatible — no depende de `raw.githubusercontent.com` |

---

## Demo web Next.js (F1-04)

App read-only en **`web/`** — misma UX que `dashboard/static/` (monitor local), pero alimentada por
JSON estático en lugar de SQLite en runtime.

### Comandos

```text
cd web
npm install
npm run dev          # http://localhost:3000
npm run build        # verifica producción (prebuild copia el JSON)
```

Desde la raíz del repo (sin entrar a `web/`):

```text
python -m pytest tests/test_web_dashboard.py tests/test_dashboard_export.py -v
```

### Cómo obtiene los datos

| Fase | Mecanismo | Archivo |
|------|-----------|---------|
| **Build** | `scripts/copy-payload.mjs` (hook `predev` / `prebuild`) | `public/dashboard_payload.json` |
| **SSR inicial** | `lib/dashboard-server.ts` lee del disco | mismo path o `fixtures/` |
| **Refresh manual** | Botón «Actualizar» → `fetch('/dashboard_payload.json')` | `lib/dashboard-client.ts` |

Orden de búsqueda al copiar payload:

1. `../data/dashboard_payload.json` (export real o copia desde `paper-live-data`)
2. `web/fixtures/dashboard_payload.json` (fixture commiteado para dev sin DB)

En producción (Vercel), el build corre desde la raíz del monorepo con `data/dashboard_payload.json`
presente en la rama **`paper-live-data`** tras el paper-live diario.

### Pantallas y bloques (paridad con monitor local)

- KPIs: equity total, Sharpe, Calmar, max drawdown
- Curva de equity (total / corto / largo) — Chart.js
- Posiciones abiertas al último día
- Últimas operaciones (`recent_fills`, ~25)
- Panel de riesgo (kill switch, factores, umbrales)
- Alertas operativas (huecos, fetch, idle con posiciones, etc.)
- Fondo animado (port de `dashboard/static/neural-bg.js`)

### Estructura `web/`

| Ruta | Rol |
|------|-----|
| `app/page.tsx` | Server Component — payload inicial en build |
| `app/layout.tsx` | Layout + fuente Roboto Mono |
| `app/globals.css` | Estilos (port de `dashboard/static/styles.css`) |
| `components/DashboardView.tsx` | UI principal (client) |
| `components/EquityChart.tsx` | Gráfico Chart.js |
| `components/NeuralBackground.tsx` | Canvas de fondo |
| `lib/types.ts` | Tipos TypeScript del contrato JSON |
| `lib/dashboard-server.ts` | Lectura con `fs` (solo servidor) |
| `lib/dashboard-client.ts` | `fetch` + validación de shape (cliente) |
| `lib/format.ts` | Formateo ARS / porcentajes |
| `fixtures/dashboard_payload.json` | Fixture para dev y fallback de build |
| `scripts/copy-payload.mjs` | Copia JSON → `public/` |

Detalle operativo de la app: **`web/README.md`**.

### Relación monitor local ↔ demo web

| | Monitor local (`:8765`) | Demo web (`web/`) |
|--|-------------------------|-------------------|
| Fuente de datos | `market.db` en vivo | `dashboard_payload.json` |
| Actualización | Polling cada 60 s a `/api/dashboard` | Datos del último deploy; refresh re-lee el JSON estático |
| Uso | Operador / desarrollo | Cátedra / demo pública (con auth en F1-06) |
| Código UI | `dashboard/static/*.js` | Port a React en `web/components/` |

No duplicar lógica de agregación en el front: si falta un bloque en la UI, primero agregarlo a
`DashboardService` y al export; después reflejarlo en `web/`.

### Deploy (F1-05 — hecho)

Proyecto Vercel **`bot-de-trading`** conectado a `agus-chaud/Bot-de-Trading` (GitHub).

| Setting | Valor |
|---------|--------|
| Production Branch | **`paper-live-data`** |
| Root Directory | `web` |
| Framework | Next.js |
| Build Command | `npm run build` (incluye `prebuild` → `copy-payload.mjs`) |
| Install Command | `npm install` |
| Ignored Build Step | `web/vercel.json` → `ignoreCommand`: **siempre build** en `paper-live-data` |

**URLs (jun 2026):**

| Entorno | URL |
|---------|-----|
| **Production** | https://web-pearl-theta-64.vercel.app |
| **Preview** (por deploy/PR) | `https://bot-de-trading-<hash>-aguschaud-4044s-projects.vercel.app` |

**Env en Vercel** (`web/.env.example`):

| Variable | Production | Preview | Development |
|----------|------------|---------|-------------|
| `NEXT_PUBLIC_APP_URL` | URL production arriba | misma base | `http://localhost:3000` |

Auth demo: **F1-06** (`DASHBOARD_DEMO_PASSWORD` + middleware).

**Deploy manual** (desde la raíz del repo — no desde `web/`, porque `rootDirectory` ya apunta ahí):

```text
npx vercel deploy          # preview
npx vercel deploy --prod   # production
```

Cada push exitoso de paper-live en `paper-live-data` dispara **rebuild de production** por tres vías (redundantes a propósito):

1. **Git push** → Vercel (rama prod = `paper-live-data`)
2. **Commit en `web/public/dashboard_payload.json`** → cambio bajo `web/` (Vercel no salta el build por root directory)
3. **Deploy hook** → `paper_live_daily.yml` hace `POST` a `secrets.VERCEL_DEPLOY_HOOK` tras push con cambios

Ver **ADR-065**.

### Ramas `main` vs `paper-live-data` (operación CI + Vercel)

GitHub Actions usa **dos ramas** con roles distintos:

| Qué | Rama | Por qué |
|-----|------|---------|
| **Receta del workflow** (`paper_live_daily.yml`) | **`main`** (default) | GitHub lee schedule/dispatch desde la rama default |
| **Ejecución** (checkout, DB, push diario) | **`paper-live-data`** | Datos operativos + JSON del día |
| **Production Vercel** | **`paper-live-data`** | Cada push diario dispara rebuild con JSON nuevo |

**Flujo típico tras cambiar el pipeline (ej. F1-05):**

1. PR con cambios de workflow / `web/` → **merge a `main`**
2. En local: `git checkout paper-live-data && git merge main` (alinear código en la rama operativa)
3. Próxima corrida paper-live (10:00 UTC Lun–Vie) usa la receta nueva de `main`

**Secretos GitHub** (Settings → Actions): `IOL_USER`, `IOL_PASS`, `VERCEL_DEPLOY_HOOK` (deploy hook del proyecto `bot-de-trading`).

Conflictos de merge en `data/market.db` (LFS): `git checkout --ours|--theirs` en el puntero; no editar `<<<<<<<` a mano.

### Implicaciones futuras

- Si el JSON crece mucho (p. ej. historial completo de fills), subir `export_version` y/o paginar bloques — no migrar a SQLite sin necesidad demostrada.
- **F1-06**: password / env en middleware Next.js antes de exponer URL a la cátedra.

## Tests

```text
# Export Python + contrato del workflow CI
python -m pytest tests/test_dashboard_export.py -v

# Scaffold web/ + fixture JSON
python -m pytest tests/test_web_dashboard.py -v

# Ambos
python -m pytest tests/test_dashboard_export.py tests/test_web_dashboard.py -v

# Build de producción Next.js
cd web && npm run build
```

---

## Roadmap MVP interfaz (contexto)

| Paso | Estado | Descripción |
|------|--------|-------------|
| **F1-01** | Hecho | Export JSON desde `DashboardService` |
| **F1-02** | Hecho | CI sube `dashboard-payload` como artifact tras cada paper-live |
| **F1-03** | Hecho | Arquitectura Vercel: JSON estático + commit en `paper-live-data` + rebuild (**ADR-065**) |
| **F1-04** | Hecho | App Next.js en `web/` — equity, posiciones, KPIs, alertas (fixture local) |
| **F1-05** | Hecho | Deploy Vercel (`bot-de-trading`, root `web/`, preview + production) |
| **F1-06** | Pendiente | Auth demo (password / env) |

Detalle del plan: canvas `mvp-interfaz-plan` y `docs/mvp_gate.md` (gate de capital, aparte de la UI).

---

## Archivos del módulo

| Ruta | Rol |
|------|-----|
| `dashboard/service.py` | Agrega datos de `MarketDB` + KPIs |
| `dashboard/server.py` | FastAPI + estáticos |
| `dashboard/db_freshness.py` | Chequeo LFS / remoto |
| `dashboard/static/` | HTML, CSS, JS del monitor |
| `scripts/run_dashboard.py` | Arranque del servidor |
| `scripts/export_dashboard_payload.py` | Export JSON estático |
| `web/` | App Next.js read-only para Vercel (**F1-04**) — ver `web/README.md` |
| `tests/test_dashboard_export.py` | Comportamiento del export + workflow CI |
| `tests/test_web_dashboard.py` | Fixture, scaffold y scripts de build web |

---

## Ver también

- `web/README.md` — desarrollo y build de la demo Next.js
- `AGENTS.md` — comandos rápidos
- `docs/mvp_gate.md` — gate para autorizar 10% de capital (no es el dashboard)
- `decisiones-tecnicas.md` — ADR-040, ADR-050 (paper-live), ADR-063 (MVP), **ADR-065** (Vercel JSON)
