"""Risk audit stage for the validation workflow.

Two types of checks:
  Tipo A — static YAML consistency checks (always run, no DB required).
           Failures block GO (passed=False).
  Tipo B — dynamic guardrail audit over the trading period.
           Always informational — never blocks GO.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data.storage import MarketDB

from validation.report import StageResult

logger = logging.getLogger(__name__)

_FLOAT_TOL = 1e-9


# ---------------------------------------------------------------------------
# Tipo A — static checks
# ---------------------------------------------------------------------------

def _check_schema(policy_doc: dict, schema_path: Path) -> tuple[bool, str]:
    """Validate policy_doc against the JSON Schema. Returns (ok, error_message)."""
    try:
        import json as _json

        from jsonschema import Draft202012Validator, ValidationError

        with schema_path.open(encoding="utf-8") as fh:
            schema = _json.load(fh)
        try:
            Draft202012Validator(schema).validate(policy_doc)
            return True, ""
        except ValidationError as exc:
            return False, f"jsonschema: {exc.message} (path: {list(exc.absolute_path)})"
    except ImportError:
        return False, "jsonschema package not installed"


def _run_static_checks(policy_doc: dict) -> tuple[list[str], list[str]]:
    """Run manual cross-field checks beyond JSON Schema.

    Returns (passed_names, failed_names).
    """
    passed: list[str] = []
    failed: list[str] = []

    # weights.short + weights.long == 1.0
    w = policy_doc.get("weights", {})
    w_sum = float(w.get("short", 0.0)) + float(w.get("long", 0.0))
    check_name = "weights_sum_to_one"
    if abs(w_sum - 1.0) < _FLOAT_TOL:
        passed.append(check_name)
    else:
        failed.append(check_name)

    # geo.AR + geo.US == 1.0
    g = policy_doc.get("geo", {})
    g_sum = float(g.get("AR", 0.0)) + float(g.get("US", 0.0))
    check_name = "geo_sum_to_one"
    if abs(g_sum - 1.0) < _FLOAT_TOL:
        passed.append(check_name)
    else:
        failed.append(check_name)

    # risk.short_kill_switch_monthly_dd < 0
    kill_dd = float(policy_doc.get("short_kill_switch_monthly_dd", 0.0))
    check_name = "kill_switch_monthly_dd_is_negative"
    if kill_dd < 0.0:
        passed.append(check_name)
    else:
        failed.append(check_name)

    # risk.max_daily_loss_short_pct < 0
    risk = policy_doc.get("risk", {})
    for field, label in (
        ("max_daily_loss_short_pct", "max_daily_loss_short_pct_is_negative"),
        ("max_daily_loss_long_pct", "max_daily_loss_long_pct_is_negative"),
        ("max_daily_loss_total_pct", "max_daily_loss_total_pct_is_negative"),
    ):
        val = float(risk.get(field, 0.0))
        if val < 0.0:
            passed.append(label)
        else:
            failed.append(label)

    return passed, failed


# ---------------------------------------------------------------------------
# Tipo B — dynamic guardrail audit
# ---------------------------------------------------------------------------

def _audit_guardrails(
    trading_days: list[date],
    policy_doc: dict,
    db: "MarketDB",
) -> dict[str, int]:
    """Run check_short_risk / check_long_risk over synthetic scoreboard rows for the period.

    Uses a neutral scoreboard (no losses, no drawdown) so the only guardrails
    that fire are the structural ones (data_quality, no_trade_window) when
    explicitly simulated.

    The real audit counts days where policy limits *would* block trading given
    the actual risk configuration — specifically:
      - halt_data_quality: activated when data_quality_ok=False
      - short_daily_loss_limit / long_daily_loss_limit: activated at threshold
      - no_trade_window: not applicable to daily backtests (no intraday clock)
      - notional_violations: orders rejected by max_notional_per_ticker_pct

    Since the stage runs without a live portfolio, we exercise each guardrail
    once per trading day using extreme scoreboard values that sit exactly at
    each threshold.  The count reflects how many days the threshold is tight
    enough to fire under those conditions — a structural sensitivity metric.
    """
    from core_sim.risk_guardrails import check_long_risk, check_short_risk

    risk_cfg = policy_doc.get("risk", {})
    kill_dd = float(policy_doc.get("short_kill_switch_monthly_dd", -0.08))
    max_daily_short = float(risk_cfg.get("max_daily_loss_short_pct", -0.02))
    max_daily_long = float(risk_cfg.get("max_daily_loss_long_pct", -0.015))
    max_notional = float(risk_cfg.get("max_notional_per_ticker_pct", 0.08))
    halt_on_dq = bool(risk_cfg.get("halt_on_data_quality", True))

    short_config = {
        "kill_dd": kill_dd,
        "max_daily_short": max_daily_short,
        "no_trade_first": int(risk_cfg.get("no_trade_first_minutes", 0)),
        "no_trade_last": int(risk_cfg.get("no_trade_last_minutes", 0)),
    }
    long_config = {"max_daily_long": max_daily_long}

    counts: dict[str, int] = {
        "guardrail_halt_data_quality": 0,
        "guardrail_daily_loss_short": 0,
        "guardrail_daily_loss_long": 0,
        "guardrail_no_trade_window": 0,
        "notional_violations": 0,
    }

    for _day in trading_days:
        # --- halt_data_quality ---
        if halt_on_dq:
            r = check_short_risk(
                sb={"monthly_drawdown": 0.0, "daily_return": 0.0},
                flags={"halt_on_data_quality": True, "data_quality_ok": False},
                config=short_config,
                now_minutes_from_open=None,
            )
            if r.reason == "halt_data_quality":
                counts["guardrail_halt_data_quality"] += 1

        # --- short daily loss limit: simulate being exactly at the limit ---
        r_short = check_short_risk(
            sb={"monthly_drawdown": 0.0, "daily_return": max_daily_short - 0.001},
            flags={"halt_on_data_quality": False, "data_quality_ok": True},
            config=short_config,
            now_minutes_from_open=None,
        )
        if r_short.reason == "short_daily_loss_limit":
            counts["guardrail_daily_loss_short"] += 1

        # --- long daily loss limit ---
        r_long = check_long_risk(
            sb={"long_daily_return": max_daily_long - 0.001},
            config=long_config,
        )
        if r_long.reason == "long_daily_loss_limit":
            counts["guardrail_daily_loss_long"] += 1

        # --- notional violations: a hypothetical order of max_notional + 1% ---
        # We simulate one "order" per day that exceeds the notional cap.
        # This probes whether the cap is tight (informational — always 1 per day
        # if max_notional < 1.0, which is always true in v1 policy).
        if max_notional < 1.0:
            counts["notional_violations"] += 1

    # no_trade_window is intraday — not applicable to daily cadence; stays 0
    return counts


# ---------------------------------------------------------------------------
# Main stage function
# ---------------------------------------------------------------------------

def run_risk_audit_stage(
    db: "MarketDB",
    trading_days: list[date],
    policy_doc: dict,
    repo_root: Path,
) -> StageResult:
    """Run the risk audit stage.

    Tipo A (static checks) can block GO.
    Tipo B (dynamic audit) is always informational.

    Args:
        db: MarketDB instance (used only for Tipo B).
        trading_days: Ordered list of trading days for the lookback period.
        policy_doc: Parsed policy.v1.yaml as a dict.
        repo_root: Absolute path to the repository root (used to locate schema file).

    Returns:
        StageResult with stage="risk_audit".
    """
    violations: list[str] = []

    # ------------------------------------------------------------------
    # Tipo A — JSON Schema validation
    # ------------------------------------------------------------------
    schema_path = repo_root / "config" / "policy.v1.schema.json"
    schema_ok, schema_error = _check_schema(policy_doc, schema_path)

    if not schema_ok:
        violations.append(f"schema_validation_failed: {schema_error}")
        logger.warning('{"event": "risk_audit_schema_fail", "error": %s}', json.dumps(schema_error))

    # ------------------------------------------------------------------
    # Tipo A — manual cross-field checks
    # ------------------------------------------------------------------
    static_passed, static_failed = _run_static_checks(policy_doc)

    for check in static_failed:
        violations.append(f"static_check_failed: {check}")
        logger.warning('{"event": "risk_audit_static_fail", "check": "%s"}', check)

    # ------------------------------------------------------------------
    # Tipo B — dynamic guardrail audit (always informational)
    # ------------------------------------------------------------------
    b_counts: dict[str, int] = {
        "guardrail_halt_data_quality": 0,
        "guardrail_daily_loss_short": 0,
        "guardrail_daily_loss_long": 0,
        "guardrail_no_trade_window": 0,
        "notional_violations": 0,
    }

    if trading_days:
        try:
            b_counts = _audit_guardrails(trading_days, policy_doc, db)
        except Exception as exc:  # pragma: no cover
            logger.warning('{"event": "risk_audit_tipo_b_error", "error": "%s"}', exc)

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------
    passed = schema_ok and len(static_failed) == 0

    metrics: dict = {
        # Tipo A
        "schema_valid": schema_ok,
        "static_checks_passed": static_passed,
        "static_checks_failed": static_failed,
        # Tipo B
        "guardrail_halt_data_quality": b_counts["guardrail_halt_data_quality"],
        "guardrail_daily_loss_short": b_counts["guardrail_daily_loss_short"],
        "guardrail_daily_loss_long": b_counts["guardrail_daily_loss_long"],
        "guardrail_no_trade_window": b_counts["guardrail_no_trade_window"],
        "notional_violations": b_counts["notional_violations"],
        "trading_days_audited": len(trading_days),
    }

    return StageResult(
        stage="risk_audit",
        passed=passed,
        metrics=metrics,
        violations=violations,
    )
