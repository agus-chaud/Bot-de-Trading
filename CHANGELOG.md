# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Added
- **Paper-live workflow hardening** (`.github/workflows/paper_live_daily.yml`): added
  `Fetch latest OHLCV` step before pipeline execution (`fetch_daily.py --lookback 5`),
  switched `git add` to `git add -f` for gitignore robustness, and added `Notify on failure`
  step that creates a GitHub issue automatically when the daily run fails.
  ([`9546f2b`] `ci(paper-live): fetch OHLCV before daily run and alert failures`)

- **Git LFS for paper-live DB** (`.gitattributes`, branch `paper-live-data`): configured
  Git LFS tracking for `data/*.db` to prevent binary bloat from daily SQLite commits.
  `.gitignore` updated with `!data/market.db` negation on `paper-live-data` branch only.

### Fixed
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
