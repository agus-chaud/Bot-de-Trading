# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Fixed
- **ADR-051** — Valuación resiliente a huecos de datos: `PortfolioLedger.mark_to_market` ya no crashea con `ValueError: missing close price` cuando una posición abierta no tiene barra ese día. Ahora arrastra el último close conocido (carry-forward), o `avg_cost` si nunca se vio precio, y marca la valuación como `stale`. El snapshot expone `stale_marks` y un flag `stale` por posición. Esto destraba `run_validation_wf`, que abortaba la corrida completa ante un solo hueco (ej. `TXAR`). (`core_sim/ledger.py`, `tests/test_ledger.py`).

### Added
- **ADR-050** — Runbook operativo paper-live: secretos GitHub obligatorios para CI, recuperación F3 en tandas, resolución de conflictos LFS en `data/market.db`, feriados sin barras, incidente IOL histórico 401 (`decisiones-tecnicas.md`, `docs/project-overview.md`, `POLICY.md` §15, `README.md`, `AGENTS.md`).

- **Fetch audit trail (`fetch_log`)** — Fase 2 auditoría IOL (**ADR-049**):
  - Persistencia por símbolo/rango en tabla `fetch_log` vía `MarketDB.log_fetch` y taxonomía en `data/fetch_trace.py` (`status`, `skip_reason`, `source` / `effective_source`).
  - `data/fetcher.py` registra cada símbolo US/AR tras `fetch_and_store`; AR usa `fetch_ar_ohlcv_with_trace` con calendario XBUE para detectar huecos.
  - `extra` JSON auditables: `rows_by_source`, `partial_fallback`, `provider`, `iol_only`, `attempts`, `start_date`, `end_date`, `rows`.
  - **Fallback parcial**: si IOL cubre solo parte del calendario AR, Byma rellena fechas faltantes (IOL gana en empate); `source=mixed` cuando aplica.
  - `scripts/fetch_daily.py`: env opcional `FETCH_IOL_ONLY=1|true|yes` → sin fallback Byma en ingesta diaria.
  - Tests: `tests/test_fetch_trace.py`, `TestPartialSourceAttribution` en `test_data_ar_connector.py`, `TestFetchLogPersistence` en `test_data_fetcher.py`.

- **Gate KPI OOS activado** (`config/policy.v1.yaml`, `config/policy.v1.schema.json`):
  `kpi_oos_gate.enabled` cambia de `false` a `true`; 7 umbrales bloqueantes rellenados
  (Sharpe ≥ 0.30, Sortino ≥ 0.40, DD total ≥ -18%, DD corto ≥ -10%, DD largo ≥ -25%,
  turnover largo ≤ 8%, alpha ≥ -2%); 2 informativas (Calmar, MDD 12m) en `null`.
  Nuevo campo `ramp_stage: paper` con enum validado en schema.
  Anexo fechado `gate.v1` (2026-05-11) en `POLICY.md` §13.
  Protocolo ramp-up (paper → 10% → 25% → 50% → 100%) en `POLICY.md` §14.
  Decisión registrada en `decisiones-tecnicas.md` (**ADR-041**).

- **Paper-live workflow hardening** (`.github/workflows/paper_live_daily.yml`): added
  `Fetch latest OHLCV` step before pipeline execution (`fetch_daily.py --lookback 5`),
  switched `git add` to `git add -f` for gitignore robustness, and added `Notify on failure`
  step that creates a GitHub issue automatically when the daily run fails.
  ([`9546f2b`] `ci(paper-live): fetch OHLCV before daily run and alert failures`)

- **Git LFS for paper-live DB** (`.gitattributes`, branch `paper-live-data`): configured
  Git LFS tracking for `data/*.db` to prevent binary bloat from daily SQLite commits.
  `.gitignore` updated with `!data/market.db` negation on `paper-live-data` branch only.

### Fixed
- **`run_paper_live.py`**: días del gap sin barras OHLCV (p. ej. feriado AR) se registran como warning y se omiten en lugar de abortar todo el catch-up (**ADR-050**).
- **Paper-live CI (may–jun 2026)**: documentada cadena de fallo (secretos solo locales → gap → F3) y verificación exitosa post-configuración (`workflow_dispatch` 2026-06-02).

- **Short bucket monthly drawdown** (`core_sim/ledger.py`): switched DD calculation
  from MV-only to bucket equity (`short_cash + MV_short`). Closing a profitable
  position no longer produces phantom -100% drawdowns. Peak and drawdown now
  track the full bucket allocation, not just open-position market value.
  ([`e724378`] `fix(ledger): switch short bucket DD to bucket-equity basis`)

- **US market venue consistency** (`data/connectors/us_connector.py`, `data/fetcher.py`):
  venue constant changed from `"US"` to `"XNYS"` (MIC code) across the US connector
  and fetcher log labels. Eliminates silent mismatch between connector output and
  SQLite storage layer.
  ([`ef3bd8b`] `fix(connectors): use XNYS venue consistently for US market`)

### Added
- **Venue migration script** (`scripts/migrate_venue_us_to_xnys.py`): idempotent
  CLI script to backfill existing `ohlcv` rows from `venue='US'` to `venue='XNYS'`.
  Handles PK conflicts, prints rows affected, exit codes 0/1/2.
  ([`b333381`] `feat(scripts): add idempotent migration for US to XNYS venue`)

### Post-merge action required
Run venue migration against local DB before starting paper-live:
```
python scripts/migrate_venue_us_to_xnys.py --db data/market.db
```

Then re-run validation-wf to establish new baseline with honest DD metrics:
```
python -m scripts.run_validation_wf --db data/market.db
```
Archive output as `validation_reports/baseline_post_dd_fix_YYYY-MM-DD.json`.
