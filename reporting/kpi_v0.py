"""KPI report v0 — smoke metrics aligned with docs/kpi_report_spec.v1.md.

Retorno neto anualizado (§5), max drawdown (§7), costos por motor (§10).
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml


REQUIRED_EQUITY_COLS = ("ts", "equity_total", "equity_short", "equity_long")


@dataclass
class KpiV0Report:
    """Serializable report payload (JSON + Markdown views)."""

    spec_id: str = "rpt_kpi.v1"
    report_version: str = "report_kpis_v0"
    run_id: str | None = None
    reporting_ccy: str = "USD"
    trading_days_per_year: int = 252
    ts_start: str | None = None
    ts_end: str | None = None
    segment_total: dict[str, Any] = field(default_factory=dict)
    costs_by_motor: dict[str, float] | None = None
    costs_na_reason: str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "report_version": self.report_version,
            "run_id": self.run_id,
            "reporting_ccy": self.reporting_ccy,
            "trading_days_per_year": self.trading_days_per_year,
            "ts_start": self.ts_start,
            "ts_end": self.ts_end,
            "segment": {"total": self.segment_total},
            "costs_by_motor": self.costs_by_motor,
            "costs_na_reason": self.costs_na_reason,
        }


def _parse_ts(raw: str) -> date:
    s = (raw or "").strip()
    if not s:
        raise ValueError("empty ts")
    if "T" in s or s.endswith("Z"):
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    return date.fromisoformat(s[:10])


def _f(row: dict[str, str], key: str) -> float:
    v = row.get(key)
    if v is None or str(v).strip() == "":
        raise ValueError(f"missing or empty numeric column {key!r}")
    return float(v)


def _f_opt(row: dict[str, str], key: str, default: float = 0.0) -> float:
    v = row.get(key)
    if v is None or str(v).strip() == "":
        return default
    return float(v)


def load_equity_csv(path: str | Path) -> tuple[list[dict[str, str]], list[str]]:
    """Read equity CSV; return rows (as string dicts) sorted by `ts` and header fieldnames."""
    p = Path(path)
    with p.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("equity CSV: missing header")
        fieldnames = list(reader.fieldnames)
        missing = [c for c in REQUIRED_EQUITY_COLS if c not in reader.fieldnames]
        if missing:
            raise ValueError(f"equity CSV missing columns: {missing}")
        rows = [dict(r) for r in reader]

    def sort_key(r: dict[str, str]) -> date:
        return _parse_ts(r["ts"])

    rows.sort(key=sort_key)
    return rows, fieldnames


def load_trades_csv(path: str | Path) -> list[dict[str, str]]:
    """Read trades / fills CSV (string dicts per row)."""
    p = Path(path)
    with p.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("trades CSV: missing header")
        return [dict(r) for r in reader]


def compute_net_return_annualized(
    equity_total_series: list[float],
    *,
    trading_days_per_year: int = 252,
) -> tuple[float | None, str | None]:
    """``(E_T/E_0)^(252/N) - 1`` with *N* = number of consecutive return steps (§5)."""
    if len(equity_total_series) < 2:
        return None, "insufficient_history"
    e0 = equity_total_series[0]
    e_t = equity_total_series[-1]
    if e0 <= 0 or e_t <= 0:
        return None, "non_positive_equity"
    n = len(equity_total_series) - 1
    factor = e_t / e0
    out = factor ** (trading_days_per_year / float(n)) - 1.0
    return out, None


def compute_max_drawdown(equity_series: list[float]) -> float:
    """``min_t (E_t / peak_t - 1)`` — negative fraction (§7)."""
    if not equity_series:
        return 0.0
    peak = equity_series[0]
    worst = 0.0
    for e in equity_series:
        peak = max(peak, e)
        if peak > 0:
            worst = min(worst, e / peak - 1.0)
    return worst


def equity_has_motor_cost_columns(fieldnames: list[str]) -> bool:
    return "costs_day_short" in fieldnames and "costs_day_long" in fieldnames


def sum_costs_by_motor_from_equity(rows: list[dict[str, str]]) -> dict[str, float]:
    short_total = 0.0
    long_total = 0.0
    for r in rows:
        short_total += _f_opt(r, "costs_day_short")
        long_total += _f_opt(r, "costs_day_long")
    return {"short": short_total, "long": long_total}


def _row_execution_cost(r: dict[str, str], row_index: int) -> float:
    """One of ``fee`` or ``fees`` (not both) plus optional ``slippage``."""
    raw_fee = (r.get("fee") or "").strip()
    raw_fees = (r.get("fees") or "").strip()
    if raw_fee and raw_fees:
        raise ValueError(f"trades row {row_index}: use either fee or fees, not both")
    base = float(raw_fee) if raw_fee else (float(raw_fees) if raw_fees else 0.0)
    return base + _f_opt(r, "slippage")


def sum_costs_by_motor_from_trades(rows: list[dict[str, str]]) -> dict[str, float]:
    """Sum execution cost per ``motor`` or ``bucket`` (ledger ``short``/``long``)."""
    totals: dict[str, float] = {"short": 0.0, "long": 0.0}
    for i, r in enumerate(rows):
        motor_raw = (r.get("motor") or r.get("bucket") or "").strip().lower()
        if motor_raw not in ("short", "long"):
            raise ValueError(f"trades row {i}: need motor or bucket in {{short,long}}, got {motor_raw!r}")
        totals[motor_raw] = totals[motor_raw] + _row_execution_cost(r, i)
    return totals


def load_metadata(path: str | Path) -> dict[str, Any]:
    """Load YAML or JSON run metadata."""
    p = Path(path)
    suf = p.suffix.lower()
    txt = p.read_text(encoding="utf-8")
    if suf in (".yaml", ".yml"):
        data = yaml.safe_load(txt)
    elif suf == ".json":
        data = json.loads(txt)
    else:
        raise ValueError(f"unsupported metadata suffix {suf!r} (use .yaml, .yml, or .json)")
    if not isinstance(data, dict):
        raise ValueError("metadata root must be a mapping")
    return data


def build_kpi_v0_report(
    equity_path: str | Path,
    trades_path: str | Path | None,
    *,
    metadata_path: str | Path | None = None,
) -> KpiV0Report:
    """Assemble report from CSV paths (§2.1 equity + §2.2 trades or split cost columns)."""
    rows, fieldnames = load_equity_csv(equity_path)
    equities = [_f(r, "equity_total") for r in rows]

    meta: dict[str, Any] = {}
    if metadata_path is not None:
        meta = load_metadata(metadata_path)

    tdy = int(meta.get("trading_days_per_year", 252))

    ann, ann_na = compute_net_return_annualized(equities, trading_days_per_year=tdy)
    mdd = compute_max_drawdown(equities)

    costs: dict[str, float] | None = None
    costs_na: str | None = None
    if equity_has_motor_cost_columns(fieldnames):
        costs = sum_costs_by_motor_from_equity(rows)
    elif trades_path is not None:
        tr = load_trades_csv(trades_path)
        costs = sum_costs_by_motor_from_trades(tr) if tr else {"short": 0.0, "long": 0.0}
    else:
        costs_na = "missing_trades_and_no_costs_day_short_long_in_equity_csv"

    ts_start = rows[0]["ts"].strip() if rows else None
    ts_end = rows[-1]["ts"].strip() if rows else None

    segment = {
        "net_return_annualized": ann,
        "net_return_annualized_na_reason": ann_na,
        "max_drawdown": mdd,
        "n_trading_days": len(rows),
        "n_return_steps": max(0, len(rows) - 1),
    }

    rep = KpiV0Report(
        spec_id=str(meta.get("spec_id", "rpt_kpi.v1")),
        run_id=(str(meta["run_id"]) if meta.get("run_id") is not None else None),
        reporting_ccy=str(meta.get("reporting_ccy", "USD")),
        trading_days_per_year=tdy,
        ts_start=ts_start,
        ts_end=ts_end,
        segment_total=segment,
        costs_by_motor=costs,
        costs_na_reason=costs_na,
    )
    return rep


def write_report_json(report: KpiV0Report, path: str | Path) -> None:
    p = Path(path)
    p.write_text(json.dumps(report.to_json_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_report_markdown(report: KpiV0Report, path: str | Path) -> None:
    p = Path(path)
    seg = report.segment_total
    ann = seg.get("net_return_annualized")
    ann_na = seg.get("net_return_annualized_na_reason")
    mdd = seg.get("max_drawdown")

    def pct(x: float | None, na_reason: str | None = None) -> str:
        if x is None:
            return f"NA ({na_reason})" if na_reason else "NA"
        return f"{100.0 * x:.4f}%"

    lines = [
        "# KPI report v0 (`report_kpis_v0`)",
        "",
        f"- **spec_id**: {report.spec_id}",
        f"- **run_id**: {report.run_id or '—'}",
        f"- **window**: {report.ts_start} → {report.ts_end}",
        f"- **trading_days_per_year**: {report.trading_days_per_year}",
        "",
        "## Segmento total",
        "",
        "| Métrica | Valor |",
        "|--------|-------|",
        f"| Retorno neto anualizado | {pct(ann, ann_na)} |",
        f"| Max drawdown | {pct(mdd)} |",
        f"| Sesiones (`n_trading_days`) | {seg.get('n_trading_days')} |",
        "",
        "## Costos por motor",
        "",
    ]
    if report.costs_by_motor is not None:
        c = report.costs_by_motor
        lines.append(f"- **short**: {c.get('short', 0.0):.6f} {report.reporting_ccy}")
        lines.append(f"- **long**: {c.get('long', 0.0):.6f} {report.reporting_ccy}")
    else:
        lines.append(f"*NA — {report.costs_na_reason}*")
    lines.append("")
    p.write_text("\n".join(lines), encoding="utf-8")
