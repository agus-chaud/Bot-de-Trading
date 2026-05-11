"""Walk-forward OOS: KPI v3 (report_kpis) por ventana + tabla maestra + gate pass/fail.

Reutiliza las mismas rejillas de ventanas que ``walk_forward_oos_windows`` (burn-in + OOS + step)
y ``build_kpi_v0_report_from_tables`` para cada tramo. Los umbrales viven en ``kpi_oos_gate``
del YAML de política (Fase 5 plan: gate pre-registrado).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from core_sim.short_term_pre_gate import walk_forward_oos_windows

from reporting.kpi_v0 import (
    KpiV0Report,
    build_kpi_v0_report_from_tables,
    load_benchmark_returns_csv,
    load_equity_csv,
    load_metadata,
    load_trades_csv,
    _parse_ts,
)


def _load_policy_doc(policy_path: str | Path) -> dict[str, Any]:
    p = Path(policy_path)
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("policy YAML root must be a mapping")
    return data


def _trading_days_sorted(rows: list[dict[str, str]]) -> list[date]:
    return sorted({_parse_ts(str(r["ts"])) for r in rows})


def _rows_in_trading_day_set(
    rows: list[dict[str, str]],
    day_set: set[date],
    *,
    ts_key: str = "ts",
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for r in rows:
        try:
            d = _parse_ts(str(r[ts_key]))
        except ValueError:
            continue
        if d in day_set:
            out.append(r)
    return out


def summary_row_for_master_table(window_index: int, rep: KpiV0Report) -> dict[str, Any]:
    """Columnas compactas por ventana (tabla maestra + JSON)."""
    st = rep.segment_total
    sh = rep.segment_short
    lg = rep.segment_long
    alpha: float | None = None
    alpha_na: str | None = None
    if rep.alpha_vs_benchmark:
        blk = rep.alpha_vs_benchmark.get("total") or {}
        alpha = blk.get("alpha_simple_return")
        alpha_na = blk.get("alpha_na_reason")

    return {
        "window_index": window_index,
        "ts_start": rep.ts_start,
        "ts_end": rep.ts_end,
        "n_trading_days": st.get("n_trading_days"),
        "net_return_annualized_total": st.get("net_return_annualized"),
        "net_return_annualized_total_na_reason": st.get("net_return_annualized_na_reason"),
        "max_drawdown_total": st.get("max_drawdown"),
        "sharpe_annualized_total": st.get("sharpe_annualized"),
        "sharpe_annualized_total_na_reason": st.get("sharpe_na_reason"),
        "sortino_annualized_total": st.get("sortino_annualized"),
        "sortino_annualized_total_na_reason": st.get("sortino_na_reason"),
        "max_drawdown_short": sh.get("max_drawdown"),
        "max_drawdown_long": lg.get("max_drawdown"),
        "mdd_12m_rolling_last_long": lg.get("mdd_12m_rolling_last"),
        "mdd_12m_rolling_na_reason_long": lg.get("mdd_12m_rolling_na_reason"),
        "calmar_12m_last_long": lg.get("calmar_12m_last"),
        "calmar_12m_na_reason_long": lg.get("calmar_12m_na_reason"),
        "turnover_long_monthly_last": lg.get("turnover_long_monthly_last"),
        "turnover_long_monthly_last_na_reason": lg.get("turnover_long_monthly_last_na_reason"),
        "alpha_simple_return_total": alpha,
        "alpha_simple_return_total_na_reason": alpha_na,
    }


def evaluate_kpi_oos_thresholds(rep: KpiV0Report, thr: dict[str, Any]) -> tuple[bool, list[str]]:
    """Pass/fail frente a umbrales del bloque ``kpi_oos_gate.thresholds`` (solo claves presentes y no null)."""
    violations: list[str] = []

    def check_min(label: str, actual: float | None, minimum: float | None) -> None:
        if minimum is None:
            return
        if actual is None:
            violations.append(f"{label}: metric_na (required min {minimum})")
            return
        if actual < float(minimum):
            violations.append(f"{label}: {actual} < min {minimum}")

    def check_max_drawdown(label: str, actual: float | None, floor: float | None) -> None:
        """Drawdowns son negativos; ``floor`` es el peor valor permitido (ej. -0.2). Pasa si ``actual >= floor``."""
        if floor is None:
            return
        if actual is None:
            violations.append(f"{label}: metric_na (required floor {floor})")
            return
        if actual < float(floor):
            violations.append(f"{label}: {actual} worse_than_floor {floor}")

    def check_max_nonneg(label: str, actual: float | None, maximum: float | None) -> None:
        if maximum is None:
            return
        if actual is None:
            violations.append(f"{label}: metric_na (required max {maximum})")
            return
        if actual > float(maximum):
            violations.append(f"{label}: {actual} > max {maximum}")

    st = rep.segment_total
    sh = rep.segment_short
    lg = rep.segment_long

    check_min("sharpe_annualized_total", st.get("sharpe_annualized"), thr.get("min_sharpe_annualized_total"))
    check_min("sortino_annualized_total", st.get("sortino_annualized"), thr.get("min_sortino_annualized_total"))
    check_max_drawdown("max_drawdown_total", st.get("max_drawdown"), thr.get("max_drawdown_total_floor"))
    check_max_drawdown("max_drawdown_short", sh.get("max_drawdown"), thr.get("max_drawdown_short_floor"))
    check_max_drawdown("max_drawdown_long", lg.get("max_drawdown"), thr.get("max_drawdown_long_floor"))

    check_min("calmar_12m_long", lg.get("calmar_12m_last"), thr.get("min_calmar_12m_long"))
    check_max_drawdown(
        "mdd_12m_rolling_long",
        lg.get("mdd_12m_rolling_last"),
        thr.get("max_mdd_12m_rolling_long_floor"),
    )
    check_max_nonneg(
        "turnover_long_monthly_last",
        lg.get("turnover_long_monthly_last"),
        thr.get("max_turnover_long_monthly_last"),
    )

    alpha: float | None = None
    if rep.alpha_vs_benchmark:
        alpha = (rep.alpha_vs_benchmark.get("total") or {}).get("alpha_simple_return")
    check_min("alpha_simple_return_total", alpha, thr.get("min_alpha_simple_return_total"))

    return (len(violations) == 0, violations)


def _aggregate_pass(window_passed: list[bool], agg: dict[str, Any]) -> tuple[bool, str]:
    rule = str(agg.get("rule", "all"))
    if rule == "all":
        return (all(window_passed) if window_passed else False, "all")
    if rule == "k_of_last_q":
        k = int(agg["k_pass"])
        q = int(agg["last_q_windows"])
        if not window_passed:
            return (False, rule)
        tail = window_passed[-min(q, len(window_passed)) :]
        ok = sum(1 for p in tail if p) >= k
        return (ok, f"k_of_last_q_{k}_of_{q}")
    raise ValueError(f"unknown kpi_oos_gate.aggregate.rule: {rule!r}")


@dataclass
class KpiOosWindowOutcome:
    window_index: int
    ts_start: str | None
    ts_end: str | None
    passed: bool
    violations: list[str] = field(default_factory=list)
    summary_row: dict[str, Any] = field(default_factory=dict)


@dataclass
class KpiOosWalkForwardResult:
    gate_enabled: bool
    gate_skipped_reason: str | None
    aggregate_passed: bool
    aggregate_rule_applied: str
    global_failures: list[str] = field(default_factory=list)
    windows: list[KpiOosWindowOutcome] = field(default_factory=list)
    master_table: list[dict[str, Any]] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "gate_enabled": self.gate_enabled,
            "gate_skipped_reason": self.gate_skipped_reason,
            "aggregate_passed": self.aggregate_passed,
            "aggregate_rule_applied": self.aggregate_rule_applied,
            "global_failures": list(self.global_failures),
            "windows": [
                {
                    "window_index": w.window_index,
                    "ts_start": w.ts_start,
                    "ts_end": w.ts_end,
                    "passed": w.passed,
                    "violations": list(w.violations),
                    "summary_row": dict(w.summary_row),
                }
                for w in self.windows
            ],
            "master_table": [dict(r) for r in self.master_table],
        }


def run_kpi_oos_walk_forward_from_paths(
    *,
    equity_path: str | Path,
    trades_path: str | Path | None,
    policy_path: str | Path,
    metadata_path: str | Path | None = None,
    benchmark_returns_path: str | Path | None = None,
    policy_doc_override: dict[str, Any] | None = None,
    walk_forward_override: dict[str, Any] | None = None,
) -> KpiOosWalkForwardResult:
    """Carga CSV/YAML, corta ventanas OOS, arma KPI v3 por ventana y aplica gate si está habilitado."""
    policy_doc = policy_doc_override or _load_policy_doc(policy_path)
    gate_cfg = policy_doc.get("kpi_oos_gate") or {}
    gate_enabled = bool(gate_cfg.get("enabled", False))
    wf: dict[str, Any] = dict(gate_cfg.get("walk_forward") or {})
    if walk_forward_override:
        wf.update({k: v for k, v in walk_forward_override.items() if v is not None})

    meta: dict[str, Any] = load_metadata(metadata_path) if metadata_path is not None else {}
    eq_rows, fieldnames = load_equity_csv(equity_path)
    tr_all: list[dict[str, str]] | None = load_trades_csv(trades_path) if trades_path is not None else None
    br_all: list[dict[str, str]] | None = (
        load_benchmark_returns_csv(benchmark_returns_path) if benchmark_returns_path is not None else None
    )

    trading_days = _trading_days_sorted(eq_rows)
    if not trading_days:
        return KpiOosWalkForwardResult(
            gate_enabled=gate_enabled,
            gate_skipped_reason=None,
            aggregate_passed=False,
            aggregate_rule_applied="none",
            global_failures=["empty_equity_trading_days"],
            windows=[],
            master_table=[],
        )

    burn_in = int(wf.get("burn_in_trading_days", 252))
    oos_len = int(wf.get("oos_trading_days", 60))
    step = int(wf.get("step_trading_days", 30))
    min_windows = int(wf.get("min_oos_windows", 1))

    windows = walk_forward_oos_windows(
        trading_days,
        burn_in_trading_days=burn_in,
        oos_trading_days=oos_len,
        step_trading_days=step,
    )

    if len(windows) < min_windows:
        return KpiOosWalkForwardResult(
            gate_enabled=gate_enabled,
            gate_skipped_reason=None,
            aggregate_passed=False,
            aggregate_rule_applied="none",
            global_failures=[
                f"insufficient_oos_windows:need_{min_windows}_got_{len(windows)}_"
                f"(days={len(trading_days)},burn_in={burn_in},oos={oos_len},step={step}). "
                f"Necesitás al menos burn_in+oos días de equity, o bajá burn_in/oos en "
                f"kpi_oos_gate.walk_forward (p. ej. --wf-burn-in en el script CLI)."
            ],
            windows=[],
            master_table=[],
        )

    thr = (gate_cfg.get("thresholds") or {}) if gate_enabled else {}
    agg_cfg = (gate_cfg.get("aggregate") or {"rule": "all"}) if gate_enabled else {"rule": "all"}

    outcomes: list[KpiOosWindowOutcome] = []
    master: list[dict[str, Any]] = []
    passes: list[bool] = []

    for i, win_dates in enumerate(windows):
        day_set = set(win_dates)
        sub_eq = _rows_in_trading_day_set(eq_rows, day_set)
        sub_tr = _rows_in_trading_day_set(tr_all or [], day_set) if tr_all is not None else None
        sub_br = _rows_in_trading_day_set(br_all or [], day_set) if br_all is not None else None

        rep = build_kpi_v0_report_from_tables(
            sub_eq,
            fieldnames,
            sub_tr,
            metadata=meta,
            policy_path=policy_path,
            benchmark_rows=sub_br,
        )
        row = summary_row_for_master_table(i, rep)
        if gate_enabled:
            passed, viol = evaluate_kpi_oos_thresholds(rep, thr)
        else:
            passed, viol = True, []
        row["gate_passed"] = passed
        row["gate_violations"] = viol
        master.append(row)
        outcomes.append(
            KpiOosWindowOutcome(
                window_index=i,
                ts_start=rep.ts_start,
                ts_end=rep.ts_end,
                passed=passed,
                violations=list(viol),
                summary_row=row,
            )
        )
        passes.append(passed)

    agg_ok, agg_label = _aggregate_pass(passes, agg_cfg)
    skip_reason = None
    if not gate_enabled:
        agg_ok = True
        skip_reason = "kpi_oos_gate.enabled_false"
        agg_label = "skipped_no_thresholds"

    return KpiOosWalkForwardResult(
        gate_enabled=gate_enabled,
        gate_skipped_reason=skip_reason,
        aggregate_passed=agg_ok,
        aggregate_rule_applied=agg_label,
        global_failures=[],
        windows=outcomes,
        master_table=master,
    )


def write_kpi_oos_walk_forward_json(result: KpiOosWalkForwardResult, path: str | Path) -> None:
    Path(path).write_text(json.dumps(result.to_json_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


__all__ = [
    "KpiOosWalkForwardResult",
    "KpiOosWindowOutcome",
    "evaluate_kpi_oos_thresholds",
    "run_kpi_oos_walk_forward_from_paths",
    "summary_row_for_master_table",
    "write_kpi_oos_walk_forward_json",
]
