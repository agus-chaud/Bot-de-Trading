"""CLI script: fetch OHLCV data for configured symbols and store in SQLite.

Usage:
    python scripts/fetch_daily.py [--lookback N] [--db PATH] [--symbols-us S1 S2] [--symbols-ar S1 S2]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Allow imports from the project root regardless of CWD.
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml  # type: ignore[import]

from data.fetcher import fetch_and_store
from data.iol_api_meter import (
    ApiBudgetEval,
    evaluate_api_budget,
    iol_meter_session,
    month_key_for_date,
    should_refresh_dynamic_universe,
)
from data.storage import MarketDB
from data.universe_selector import (
    dynamic_tops_from_db,
    merge_fetch_universe,
    open_ar_position_symbols_from_db,
    persist_universe_snapshots,
    select_dynamic_universe,
    static_ar_symbols_from_policy,
)

_DEFAULT_SYMBOLS_US = ["SPY", "QQQ", "IWM"]
_DEFAULT_SYMBOLS_AR = ["GGAL", "YPFD", "BMA", "PAMP", "TXAR"]

_POLICY_PATH = Path(__file__).parent.parent / "config" / "policy.v1.yaml"
_REPO_ROOT = Path(__file__).parent.parent

logger = logging.getLogger(__name__)


def _load_policy() -> dict:
    with open(_POLICY_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_symbols_from_policy(policy: dict) -> tuple[list[str], list[str]]:
    """Read whitelists referenced in policy.v1.yaml; fall back to hardcoded defaults."""
    try:
        base = _REPO_ROOT

        us_file = policy.get("symbols", {}).get("whitelist_us_file")
        ar_file = policy.get("symbols", {}).get("whitelist_ar_file")

        us_symbols: list[str] = []
        if us_file:
            with open(base / us_file) as f:
                data = yaml.safe_load(f)
            for key in ("etfs", "stocks", "adrs"):
                us_symbols.extend(data.get(key, []))

        ar_symbols: list[str] = []
        if ar_file:
            with open(base / ar_file) as f:
                data = yaml.safe_load(f)
            for key in ("stocks",):
                ar_symbols.extend(data.get(key, []))

        return us_symbols or _DEFAULT_SYMBOLS_US, ar_symbols or _DEFAULT_SYMBOLS_AR

    except Exception:
        return _DEFAULT_SYMBOLS_US, _DEFAULT_SYMBOLS_AR


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch daily OHLCV bars and store in SQLite.")
    parser.add_argument("--lookback", type=int, default=5, help="Days back from today (default: 5)")
    parser.add_argument("--db", default="data/market.db", help="Path to SQLite DB (default: data/market.db)")
    parser.add_argument("--symbols-us", nargs="*", dest="symbols_us", help="US symbols to fetch")
    parser.add_argument("--symbols-ar", nargs="*", dest="symbols_ar", help="AR symbols to fetch")
    return parser.parse_args()


def _budget_eval_placeholder(month_key: str) -> ApiBudgetEval:
    z = {"token_count": 0, "refresh_count": 0, "history_count": 0, "universe_volume_count": 0}
    return ApiBudgetEval(
        month_key=month_key,
        monthly_limit=10**9,
        soft_threshold=10**9,
        counts=z,
        monthly_total=0,
        monthly_hard_exceeded=False,
        monthly_soft_exceeded=False,
        force_monthly_cadence=False,
    )


def _resolve_symbols_ar_for_run(
    *,
    policy: dict,
    db: MarketDB,
    today: date,
    symbols_ar_override: list[str] | None,
    budget_eval: ApiBudgetEval,
    universe_report: dict[str, object],
) -> None:
    """Mutates *universe_report* and sets symbols_ar on it under key symbols_ar_effective."""
    if symbols_ar_override is not None:
        universe_report["symbols_ar_effective"] = list(symbols_ar_override)
        return

    holdings = open_ar_position_symbols_from_db(db)
    ucfg = policy.get("symbols", {}).get("universe_selection") or {}
    universe_report["universe_selection_enabled"] = bool(ucfg.get("enabled", False))

    if not ucfg.get("enabled", False):
        base_ar = static_ar_symbols_from_policy(_REPO_ROOT, policy)
        if not base_ar:
            _, legacy_ar = _load_symbols_from_policy(policy)
            base_ar = legacy_ar
        symbols_ar = merge_fetch_universe(base_ar, [], holdings)
        universe_report.update(
            {
                "holdings_overlay_ar": holdings,
                "symbols_ar_effective": symbols_ar,
                "dynamic_refresh_decision": "disabled_static_whitelist",
            }
        )
        return

    freq = str(ucfg.get("rebalance_frequency", "weekly"))
    should_refresh, refresh_reason = should_refresh_dynamic_universe(
        today,
        db,
        frequency=freq,
        budget_eval=budget_eval,
    )
    universe_report["dynamic_refresh_decision"] = refresh_reason

    if budget_eval.monthly_soft_exceeded and not budget_eval.monthly_hard_exceeded:
        logger.warning(
            '{"event": "iol_api_monthly_soft", "month_key": "%s", "monthly_total": %d, "soft_threshold": %d}',
            budget_eval.month_key,
            budget_eval.monthly_total,
            budget_eval.soft_threshold,
        )

    if not should_refresh:
        merval, cedear, sel_date = dynamic_tops_from_db(db)
        if merval or cedear:
            symbols_ar = merge_fetch_universe(merval, cedear, holdings)
            universe_report.update(
                {
                    "selection_reused_from": sel_date.isoformat() if sel_date else None,
                    "merval_top": merval,
                    "cedear_top": cedear,
                    "holdings_overlay_ar": holdings,
                    "symbols_ar_effective": symbols_ar,
                }
            )
        else:
            base_ar = static_ar_symbols_from_policy(_REPO_ROOT, policy)
            if not base_ar:
                _, legacy_ar = _load_symbols_from_policy(policy)
                base_ar = legacy_ar
            symbols_ar = merge_fetch_universe(base_ar, [], holdings)
            universe_report.update(
                {
                    "holdings_overlay_ar": holdings,
                    "symbols_ar_effective": symbols_ar,
                    "fallback": "no_snapshot_used_static",
                }
            )
        return

    dyn = select_dynamic_universe(
        policy,
        _REPO_ROOT,
        selection_date=today,
        as_of_date=today,
    )
    if dyn.budget_job_aborted:
        universe_report["dynamic_selection"] = "aborted_job_budget"
        universe_report["skipped_in_ranking"] = list(dyn.skipped)
        merval, cedear, sel_date = dynamic_tops_from_db(db)
        if merval or cedear:
            symbols_ar = merge_fetch_universe(merval, cedear, holdings)
            universe_report.update(
                {
                    "selection_reused_from": sel_date.isoformat() if sel_date else None,
                    "merval_top": merval,
                    "cedear_top": cedear,
                    "holdings_overlay_ar": holdings,
                    "symbols_ar_effective": symbols_ar,
                }
            )
        else:
            base_ar = static_ar_symbols_from_policy(_REPO_ROOT, policy)
            if not base_ar:
                _, legacy_ar = _load_symbols_from_policy(policy)
                base_ar = legacy_ar
            symbols_ar = merge_fetch_universe(base_ar, [], holdings)
            universe_report.update(
                {
                    "holdings_overlay_ar": holdings,
                    "symbols_ar_effective": symbols_ar,
                    "fallback": "after_abort_static",
                }
            )
        return

    persist_universe_snapshots(db, today, dyn.snapshot_rows)
    symbols_ar = merge_fetch_universe(dyn.merval_symbols, dyn.cedear_symbols, holdings)
    universe_report.update(
        {
            "selection_date": today.isoformat(),
            "snapshot_rows_persisted": len(dyn.snapshot_rows),
            "merval_top": list(dyn.merval_symbols),
            "cedear_top": list(dyn.cedear_symbols),
            "holdings_overlay_ar": holdings,
            "symbols_ar_effective": symbols_ar,
            "skipped_in_ranking": list(dyn.skipped),
            "budget_job_aborted": dyn.budget_job_aborted,
        }
    )


def main() -> None:
    args = _parse_args()

    today = date.today()
    start_date = today - timedelta(days=args.lookback)
    end_date = today

    policy = _load_policy()

    if args.symbols_us is not None:
        symbols_us = args.symbols_us
    else:
        symbols_us, _ = _load_symbols_from_policy(policy)

    db_path = args.db
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    db = MarketDB(db_path)

    month_key = month_key_for_date(today)
    usel = policy.get("symbols", {}).get("universe_selection") or {}
    api_budget_cfg = usel.get("api_budget")
    usage_before = db.get_iol_api_usage_month(month_key)
    if api_budget_cfg:
        budget_eval = evaluate_api_budget(
            usage_row=usage_before,
            api_budget_cfg=api_budget_cfg,
            month_key=month_key,
        )
        max_job = int(api_budget_cfg["max_calls_per_job"])
    else:
        budget_eval = _budget_eval_placeholder(month_key)
        max_job = 0

    universe_report: dict[str, object] = {
        "universe_selection_enabled": False,
        "api_budget": {
            "month_key": budget_eval.month_key,
            "monthly_total_before_run": budget_eval.monthly_total,
            "monthly_limit": budget_eval.monthly_limit,
            "soft_threshold": budget_eval.soft_threshold,
            "monthly_hard_exceeded": budget_eval.monthly_hard_exceeded,
            "monthly_soft_exceeded": budget_eval.monthly_soft_exceeded,
            "force_monthly_cadence": budget_eval.force_monthly_cadence,
        },
    }

    job_snapshot: dict[str, object] = {}

    with iol_meter_session(db, month_key, max_job) as meter:
        _resolve_symbols_ar_for_run(
            policy=policy,
            db=db,
            today=today,
            symbols_ar_override=args.symbols_ar,
            budget_eval=budget_eval,
            universe_report=universe_report,
        )
        symbols_ar = list(universe_report.get("symbols_ar_effective") or [])
        iol_only = os.environ.get("FETCH_IOL_ONLY", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        report = fetch_and_store(
            symbols_us=symbols_us,
            symbols_ar=symbols_ar,
            start_date=start_date,
            end_date=end_date,
            db=db,
            iol_only=iol_only,
        )
        universe_report["fetch_iol_only"] = iol_only
        job_snapshot = {"job_slots_used": meter.job_used, "by_kind": dict(meter.run_by_kind or {})}

    usage_after = db.get_iol_api_usage_month(month_key)
    universe_report["iol_api"] = {
        "counts_before_run": usage_before,
        "counts_after_run": usage_after,
        "job": job_snapshot,
    }

    output = {
        "universe": universe_report,
        "fetched_us": report.fetched_us,
        "fetched_ar": report.fetched_ar,
        "skipped_us": report.skipped_us,
        "skipped_ar": report.skipped_ar,
        "rows_stored": report.rows_stored,
        "errors": report.errors,
    }
    print(json.dumps(output, indent=2))

    total_fetched = len(report.fetched_us) + len(report.fetched_ar)
    sys.exit(0 if total_fetched > 0 else 1)


if __name__ == "__main__":
    main()
