# Paper-Live Dashboard (Next.js)

App **solo lectura** para la demo web en Vercel (**F1-04**). Consume el mismo JSON que
`export_dashboard_payload.py` / `GET /api/dashboard`, según **ADR-065** (arquitectura JSON
estático, sin SQLite en serverless).

Documentación operativa completa: **`docs/dashboard.md`**.

---

## Inicio rápido

```bash
cd web
npm install
npm run dev
```

Abre **http://localhost:3000**.

`predev` y `prebuild` ejecutan `scripts/copy-payload.mjs`, que copia el payload a
`public/dashboard_payload.json` desde (en orden):

1. `../data/dashboard_payload.json` — export real (`python scripts/export_dashboard_payload.py`)
2. `fixtures/dashboard_payload.json` — fixture commiteado (dev sin DB)

## Build de producción

```bash
npm run build
npm start
```

`npm run build` debe pasar en CI y en Vercel antes del deploy (**F1-05**).

## Arquitectura

```
dashboard/service.py  →  export_dashboard_payload.py  →  data/dashboard_payload.json
                                                              ↓
                                                    copy-payload.mjs (prebuild)
                                                              ↓
                                                    public/dashboard_payload.json
                                                              ↓
                              app/page.tsx (SSR) + DashboardView (client refresh)
```

| Módulo | Entorno | Responsabilidad |
|--------|---------|-----------------|
| `lib/dashboard-server.ts` | Servidor (build/SSR) | `fs.readFileSync` — **no** importar en client |
| `lib/dashboard-client.ts` | Cliente | `fetch('/dashboard_payload.json')` + validación de keys |
| `lib/types.ts` | Ambos | Contrato TypeScript (`export_version: "1"`) |
| `lib/format.ts` | Cliente | `fmtMoney`, `fmtPct`, locale `es-AR` |

La UI es un port de `dashboard/static/` (HTML/CSS/JS del monitor FastAPI). Cambios visuales
deberían mantenerse alineados entre ambos hasta que la demo web reemplace al monitor estático
como referencia principal.

## Pantallas

Equivalente al monitor en `:8765`:

- KPIs (equity, Sharpe, Calmar, max DD)
- Curva de equity (Chart.js)
- Posiciones, últimas operaciones, riesgo, alertas
- Fondo neural (`NeuralBackground`)

## Tests (desde la raíz del repo)

```bash
python -m pytest tests/test_web_dashboard.py -v
```

Verifica fixture, `package.json` scripts y presencia de archivos clave.

## Deploy Vercel (F1-05)

| Setting | Valor |
|---------|--------|
| Proyecto | `bot-de-trading` |
| Production Branch | `paper-live-data` |
| Root Directory | `web` |
| Production URL | https://web-pearl-theta-64.vercel.app |

`web/vercel.json` fija framework Next.js, comandos de build e `ignoreCommand` (siempre build en
`paper-live-data`). El JSON del día llega commiteado por `paper_live_daily.yml` en
`data/dashboard_payload.json` y copia a `web/public/`; `prebuild` también lee `../data/` si existe.
Tras push, el workflow dispara el deploy hook `VERCEL_DEPLOY_HOOK` (secret en GitHub).

**CLI** (desde la **raíz** del repo):

```bash
npx vercel deploy          # preview
npx vercel deploy --prod   # production
```

Variables: ver `web/.env.example`. Auth cátedra en **F1-06**.

**Sincronización de ramas:** el workflow se lee desde `main`; los datos diarios van a `paper-live-data`. Tras mergear cambios de CI a `main`, ejecutar `git checkout paper-live-data && git merge main` en local (ver `docs/dashboard.md` § Ramas).

## Ver también

- `docs/dashboard.md` — F1-01 a F1-06, pipeline CI, monitor local
- `decisiones-tecnicas.md` — **ADR-065**
- `tests/test_dashboard_export.py` — contrato Python del export
