"""IOL API call budgeting: per-job ceiling, monthly counters in MarketDB, structured hooks for ar_connector.

Call kinds (metering): token (password grant POST /token), refresh (refresh grant),
history (GET historia), universe_volume (GET during liquidity ranking).
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_IOL_CTX: ContextVar["IolMeterSession | None"] = ContextVar("iol_api_meter_session", default=None)

IOL_KIND_TOKEN = "token"
IOL_KIND_REFRESH = "refresh"
IOL_KIND_HISTORY = "history"
IOL_KIND_UNIVERSE_VOLUME = "universe_volume"


@dataclass
class ApiBudgetEval:
    month_key: str
    monthly_limit: int
    soft_threshold: int
    counts: dict[str, int]
    monthly_total: int
    monthly_hard_exceeded: bool
    monthly_soft_exceeded: bool
    force_monthly_cadence: bool


def month_key_for_date(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def load_policy_api_budget(policy_doc: dict[str, Any]) -> dict[str, Any]:
    return policy_doc["symbols"]["universe_selection"]["api_budget"]


def evaluate_api_budget(
    *,
    usage_row: dict[str, int],
    api_budget_cfg: dict[str, Any],
    month_key: str,
) -> ApiBudgetEval:
    monthly_limit = int(api_budget_cfg["monthly_limit"])
    soft_pct = float(api_budget_cfg["soft_limit_pct"])
    soft_threshold = int(monthly_limit * soft_pct)
    total = (
        int(usage_row["token_count"])
        + int(usage_row["refresh_count"])
        + int(usage_row["history_count"])
        + int(usage_row["universe_volume_count"])
    )
    hard = total >= monthly_limit
    soft = total >= soft_threshold
    return ApiBudgetEval(
        month_key=month_key,
        monthly_limit=monthly_limit,
        soft_threshold=soft_threshold,
        counts=dict(usage_row),
        monthly_total=total,
        monthly_hard_exceeded=hard,
        monthly_soft_exceeded=soft,
        force_monthly_cadence=soft and not hard,
    )


def same_iso_week(a: date, b: date) -> bool:
    return a.isocalendar()[:2] == b.isocalendar()[:2]


def should_refresh_dynamic_universe(
    today: date,
    db: Any,
    *,
    frequency: str,
    budget_eval: ApiBudgetEval,
) -> tuple[bool, str]:
    """Whether this run should execute IOL liquidity ranking (vs reuse last snapshot)."""
    if budget_eval.monthly_hard_exceeded:
        return False, "monthly_hard_cap"

    last = db.get_latest_universe_selection_date()

    if budget_eval.force_monthly_cadence:
        if last is not None and last.year == today.year and last.month == today.month:
            return False, "soft_monthly_degraded_cadence"

    if frequency == "daily":
        return True, "ok"

    if frequency == "monthly":
        if last is not None and last.year == today.year and last.month == today.month:
            return False, "policy_monthly_cadence"
        return True, "ok"

    if frequency == "weekly":
        if last is not None and same_iso_week(last, today):
            return False, "policy_weekly_cadence"
        return True, "ok"

    return True, "ok"


@dataclass
class IolMeterSession:
    """Active fetch job: per-job slots and after-success monthly persistence."""

    db: Any
    month_key: str
    max_calls_per_job: int
    job_used: int = 0
    run_by_kind: dict[str, int] | None = None

    def __post_init__(self) -> None:
        if self.run_by_kind is None:
            self.run_by_kind = {
                IOL_KIND_TOKEN: 0,
                IOL_KIND_REFRESH: 0,
                IOL_KIND_HISTORY: 0,
                IOL_KIND_UNIVERSE_VOLUME: 0,
            }

    def try_consume_job_slot(self) -> bool:
        if self.max_calls_per_job <= 0:
            return True
        if self.job_used >= self.max_calls_per_job:
            return False
        self.job_used += 1
        return True

    def record_success(self, kind: str) -> None:
        if kind not in (IOL_KIND_TOKEN, IOL_KIND_REFRESH, IOL_KIND_HISTORY, IOL_KIND_UNIVERSE_VOLUME):
            raise ValueError(f"unknown IOL meter kind: {kind}")
        assert self.run_by_kind is not None
        self.run_by_kind[kind] = self.run_by_kind.get(kind, 0) + 1
        self.db.increment_iol_api_usage(
            self.month_key,
            token=1 if kind == IOL_KIND_TOKEN else 0,
            refresh=1 if kind == IOL_KIND_REFRESH else 0,
            history=1 if kind == IOL_KIND_HISTORY else 0,
            universe_volume=1 if kind == IOL_KIND_UNIVERSE_VOLUME else 0,
        )


def current_meter_session() -> IolMeterSession | None:
    return _IOL_CTX.get()


def try_consume_iol_job_slot() -> bool:
    s = current_meter_session()
    if s is None:
        return True
    return s.try_consume_job_slot()


def record_iol_call(kind: str) -> None:
    s = current_meter_session()
    if s is None:
        return
    s.record_success(kind)


@contextmanager
def iol_meter_session(
    db: Any,
    month_key: str,
    max_calls_per_job: int,
) -> Iterator[IolMeterSession]:
    session = IolMeterSession(db=db, month_key=month_key, max_calls_per_job=max_calls_per_job)
    token = _IOL_CTX.set(session)
    try:
        yield session
    finally:
        _IOL_CTX.reset(token)
        logger.info(
            '{"event": "iol_api_job_summary", "month_key": "%s", "job_slots_used": %d, "by_kind": %s}',
            month_key,
            session.job_used,
            json.dumps(session.run_by_kind or {}),
        )


def json_sanitize(d: dict[str, int]) -> str:
    import json

    return json.dumps(d)


class IolJobBudgetExhausted(Exception):
    """Raised when max_calls_per_job would be exceeded before an IOL HTTP call."""


def budget_exhausted_log(reason: str) -> None:
    logger.warning('{"event": "iol_api_budget_exhausted", "reason": "%s"}', reason)
