"""KPI report — métricas alineadas con docs/kpi_report_spec.v1.md.

Retorno neto anualizado (sec. 5), max drawdown (sec. 7), Sharpe/Sortino (sec. 6),
hit rate / profit factor desde fills FIFO (sec. 8), costos por motor (sec. 10),
drift mandato 30/70 y 20/80 (sec. 11, serie + snapshot último día).
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import yaml


REQUIRED_EQUITY_COLS = ("ts", "equity_total", "equity_short", "equity_long")

@dataclass(frozen=True)
class MandateTargets:
    """Targets corto/largo y geo sobre equity_total (fracciones que suman 1 por eje)."""

    weight_short: float
    weight_long: float
    weight_ar: float
    weight_us: float


@dataclass(frozen=True)
class MandateDriftBands:
    """Media anchura ±X pp sobre drift (= desviación vs objetivo); solo comparación en informe."""

    short_pp: float | None = None
    long_pp: float | None = None
    ar_pp: float | None = None
    us_pp: float | None = None


def load_mandate_targets_from_policy_yaml(path: str | Path) -> MandateTargets:
    """Lee ``weights.*`` y ``geo.*`` desde YAML de política (p. ej. ``config/policy.v1.yaml``)."""
    data = load_metadata(path)
    w = data.get("weights") or {}
    g = data.get("geo") or {}
    return MandateTargets(
        weight_short=float(w["short"]),
        weight_long=float(w["long"]),
        weight_ar=float(g["AR"]),
        weight_us=float(g["US"]),
    )


def targets_from_metadata(meta: dict[str, Any]) -> MandateTargets | None:
    """Si ``meta`` incluye ``weights`` y ``geo`` anidados, devuelve targets; si no, None."""
    w, g = meta.get("weights"), meta.get("geo")
    if not isinstance(w, dict) or not isinstance(g, dict):
        return None
    try:
        return MandateTargets(
            weight_short=float(w["short"]),
            weight_long=float(w["long"]),
            weight_ar=float(g["AR"]),
            weight_us=float(g["US"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def bands_from_metadata(meta: dict[str, Any]) -> MandateDriftBands | None:
    """Opcional: ``mandate_drift_bands_pp: {short, long, AR, US}`` (medio ancho ± en pp)."""
    raw = meta.get("mandate_drift_bands_pp")
    if not isinstance(raw, dict):
        return None
    try:

        def f(k: str) -> float | None:
            v = raw.get(k)
            return None if v is None else float(v)

        return MandateDriftBands(
            short_pp=f("short"),
            long_pp=f("long"),
            ar_pp=f("AR"),
            us_pp=f("US"),
        )
    except (TypeError, ValueError):
        return None


def equity_csv_has_geo_columns(fieldnames: list[str]) -> bool:
    return "equity_ar" in fieldnames and "equity_us" in fieldnames


def mandate_drift_for_equity_rows(
    rows: list[dict[str, str]],
    *,
    fieldnames: list[str],
    targets: MandateTargets,
    bands: MandateDriftBands | None = None,
) -> dict[str, Any]:
    """sec. 11: serie diaria de drift vs targets + snapshot último ``ts``; geo NA si faltan columnas."""
    has_geo = equity_csv_has_geo_columns(fieldnames)
    geo_na = None if has_geo else "missing_equity_ar_equity_us_columns"

    series_out: list[dict[str, Any]] = []
    for r in rows:
        ts = (r.get("ts") or "").strip()
        try:
            et = _f(r, "equity_total")
            es = _f(r, "equity_short")
            el = _f(r, "equity_long")
        except ValueError:
            continue

        row_payload: dict[str, Any] = {"ts": ts}
        if et <= 0:
            row_payload["na_reason"] = "non_positive_equity_total"
            series_out.append(row_payload)
            continue

        ws = es / et
        wl = el / et
        row_payload["weight_short"] = ws
        row_payload["weight_long"] = wl
        row_payload["drift_short_pp"] = (ws - targets.weight_short) * 100.0
        row_payload["drift_long_pp"] = (wl - targets.weight_long) * 100.0

        if has_geo:
            ear = _f(r, "equity_ar")
            eus = _f(r, "equity_us")
            wa = ear / et
            wu = eus / et
            row_payload["weight_ar"] = wa
            row_payload["weight_us"] = wu
            row_payload["drift_ar_pp"] = (wa - targets.weight_ar) * 100.0
            row_payload["drift_us_pp"] = (wu - targets.weight_us) * 100.0
        else:
            row_payload["weight_ar"] = None
            row_payload["weight_us"] = None
            row_payload["drift_ar_pp"] = None
            row_payload["drift_us_pp"] = None
            row_payload["geo_na_reason"] = geo_na

        series_out.append(row_payload)

    snapshot: dict[str, Any]
    if not series_out:
        snapshot = {"na_reason": "empty_series"}
    else:
        last = series_out[-1]
        snap_ts = last.get("ts")
        snapshot = {"ts": snap_ts}
        if last.get("na_reason"):
            snapshot["na_reason"] = last["na_reason"]
        else:
            snapshot["weight_short"] = last["weight_short"]
            snapshot["weight_long"] = last["weight_long"]
            snapshot["drift_short_pp"] = last["drift_short_pp"]
            snapshot["drift_long_pp"] = last["drift_long_pp"]
            snapshot["weight_ar"] = last.get("weight_ar")
            snapshot["weight_us"] = last.get("weight_us")
            snapshot["drift_ar_pp"] = last.get("drift_ar_pp")
            snapshot["drift_us_pp"] = last.get("drift_us_pp")
            snapshot["geo_na_reason"] = last.get("geo_na_reason")

            if bands is not None:
                violations: list[str] = []

                def check(axis: str, drift: float | None, half_w: float | None) -> None:
                    if half_w is None or drift is None:
                        return
                    if abs(drift) > half_w:
                        violations.append(axis)

                check("short", snapshot["drift_short_pp"], bands.short_pp)
                check("long", snapshot["drift_long_pp"], bands.long_pp)
                check("AR", snapshot.get("drift_ar_pp"), bands.ar_pp)
                check("US", snapshot.get("drift_us_pp"), bands.us_pp)
                snapshot["bands_half_width_pp"] = {
                    "short": bands.short_pp,
                    "long": bands.long_pp,
                    "AR": bands.ar_pp,
                    "US": bands.us_pp,
                }
                snapshot["outside_band_axes"] = violations if violations else None

    out: dict[str, Any] = {
        "targets": {
            "weight_short": targets.weight_short,
            "weight_long": targets.weight_long,
            "weight_ar": targets.weight_ar,
            "weight_us": targets.weight_us,
        },
        "geo_series_na_reason": geo_na,
        "series": series_out,
        "snapshot_last_ts": snapshot,
    }
    return out


@dataclass
class KpiV0Report:
    """Serializable report payload (JSON + Markdown views)."""

    spec_id: str = "rpt_kpi.v1"
    report_version: str = "report_kpis_v3"
    run_id: str | None = None
    reporting_ccy: str = "USD"
    trading_days_per_year: int = 252
    ts_start: str | None = None
    ts_end: str | None = None
    segment_total: dict[str, Any] = field(default_factory=dict)
    segment_short: dict[str, Any] = field(default_factory=dict)
    segment_long: dict[str, Any] = field(default_factory=dict)
    costs_by_motor: dict[str, float] | None = None
    costs_na_reason: str | None = None
    mandate_drift: dict[str, Any] | None = None
    alpha_vs_benchmark: dict[str, Any] | None = None

    def to_json_dict(self) -> dict[str, Any]:
        root = {
            "spec_id": self.spec_id,
            "report_version": self.report_version,
            "run_id": self.run_id,
            "reporting_ccy": self.reporting_ccy,
            "trading_days_per_year": self.trading_days_per_year,
            "ts_start": self.ts_start,
            "ts_end": self.ts_end,
            "segment": {
                "total": self.segment_total,
                "short": self.segment_short,
                "long": self.segment_long,
            },
            "costs_by_motor": self.costs_by_motor,
            "costs_na_reason": self.costs_na_reason,
        }
        if self.mandate_drift is not None:
            root["mandate_drift"] = self.mandate_drift
        if self.alpha_vs_benchmark is not None:
            root["alpha_vs_benchmark"] = self.alpha_vs_benchmark
        return root


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


def load_benchmark_returns_csv(path: str | Path) -> list[dict[str, str]]:
    """CSV de benchmark con columnas mínimas: ``ts``, ``benchmark_return``."""
    p = Path(path)
    with p.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("benchmark returns CSV: missing header")
        required = {"ts", "benchmark_return"}
        if not required.issubset(set(reader.fieldnames)):
            raise ValueError("benchmark returns CSV must include ts and benchmark_return")
        rows = [dict(r) for r in reader]
    rows.sort(key=lambda r: _parse_ts(str(r["ts"])))
    return rows


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


def compute_rolling_mdd_calmar_12m(
    equity_series: list[float],
    *,
    trading_days_per_year: int = 252,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """sec. 7: MDD_12m y Calmar_12m rolling sobre una serie de equity."""
    window_returns = trading_days_per_year
    window_points = window_returns + 1
    out: list[dict[str, Any]] = []
    if len(equity_series) < window_points:
        return out, {
            "mdd_12m_rolling_last": None,
            "mdd_12m_rolling_na_reason": "insufficient_history",
            "calmar_12m_last": None,
            "calmar_12m_na_reason": "insufficient_history",
        }

    for end_idx in range(window_points - 1, len(equity_series)):
        start_idx = end_idx - (window_points - 1)
        win = equity_series[start_idx : end_idx + 1]
        mdd = compute_max_drawdown(win)
        e0 = win[0]
        e1 = win[-1]
        calmar: float | None = None
        calmar_na: str | None = None
        if e0 <= 0 or e1 <= 0:
            calmar_na = "non_positive_equity"
        elif abs(mdd) < 1e-8:
            calmar_na = "mdd_near_zero"
        else:
            ann = (e1 / e0) ** (trading_days_per_year / float(window_returns)) - 1.0
            calmar = ann / abs(mdd)

        out.append(
            {
                "end_index": end_idx,
                "mdd_12m": mdd,
                "calmar_12m": calmar,
                "calmar_12m_na_reason": calmar_na,
            }
        )

    last = out[-1]
    return out, {
        "mdd_12m_rolling_last": last["mdd_12m"],
        "mdd_12m_rolling_na_reason": None,
        "calmar_12m_last": last["calmar_12m"],
        "calmar_12m_na_reason": last["calmar_12m_na_reason"],
    }


def compute_daily_simple_returns(equity_series: list[float]) -> tuple[list[float] | None, str | None]:
    """§5: ``r_t = E_t / E_{t-1} - 1``."""
    if len(equity_series) < 2:
        return None, "insufficient_history"
    returns: list[float] = []
    for i in range(1, len(equity_series)):
        prev, cur = equity_series[i - 1], equity_series[i]
        if prev <= 0:
            return None, "non_positive_equity"
        returns.append(cur / prev - 1.0)
    return returns, None


def compute_sharpe_annualized(
    daily_returns: list[float],
    *,
    trading_days_per_year: int = 252,
) -> tuple[float | None, str | None]:
    """§6: ``sqrt(252) * mean(r) / std(r)`` (muestral); NA si ``std`` = 0 o hay menos de 2 retornos."""
    if len(daily_returns) < 2:
        return None, "insufficient_history"
    m = sum(daily_returns) / len(daily_returns)
    var = sum((r - m) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    s = math.sqrt(var)
    if s == 0.0:
        return None, "zero_std"
    sharpe = math.sqrt(float(trading_days_per_year)) * (m / s)
    return sharpe, None


def compute_sortino_annualized(
    daily_returns: list[float],
    *,
    trading_days_per_year: int = 252,
    mar: float = 0.0,
) -> tuple[float | None, str | None]:
    """§6: ``dd`` = desv. muestral sólo de ``r_t < MAR``; NA si vacío o ``dd`` = 0."""
    if len(daily_returns) < 2:
        return None, "insufficient_history"
    downside = [r for r in daily_returns if r < mar]
    if not downside:
        return None, "no_downside_returns"
    if len(downside) < 2:
        return None, "insufficient_downside_obs"
    mean_r = sum(daily_returns) / len(daily_returns)
    dm = sum(downside) / len(downside)
    var_d = sum((x - dm) ** 2 for x in downside) / (len(downside) - 1)
    dd = math.sqrt(var_d)
    if dd == 0.0:
        return None, "zero_downside_std"
    sortino = math.sqrt(float(trading_days_per_year)) * (mean_r / dd)
    return sortino, None


def _motor_fill_key(row: dict[str, str]) -> str:
    return (row.get("motor") or row.get("bucket") or "").strip().lower()


@dataclass
class _FifoLot:
    qty_open: float
    cost_basis_open: float
    initial_cost: float
    proceeds_closed: float = 0.0


def fifo_roundtrip_pnls_for_motor(sorted_rows: list[dict[str, str]], motor: str) -> list[float]:
    """§8: FIFO por (motor, símbolo); lista de PnL al cerrarse cada compra inicial (lote)."""
    eps = 1e-12
    key = motor.lower()
    queues: dict[str, list[_FifoLot]] = {}
    closed: list[float] = []

    for i, r in enumerate(sorted_rows):
        if _motor_fill_key(r) != key:
            continue
        symbol = str(r.get("symbol") or "").strip()
        if not symbol:
            raise ValueError(f"fills row {i}: missing symbol")
        side = str(r.get("side") or "").strip().upper()
        qty = float(r["qty"])  # validated by callers
        price = float(r["price"])
        if qty <= 0 or price <= 0:
            raise ValueError(f"fills row {i}: qty and price must be > 0")
        raw_fee = (r.get("fee") or "").strip()
        raw_fees = (r.get("fees") or "").strip()
        if raw_fee and raw_fees:
            raise ValueError(f"fills row {i}: use either fee or fees, not both")
        fee_base = float(raw_fee) if raw_fee else (float(raw_fees) if raw_fees else 0.0)
        fee_total_exec = fee_base + _f_opt(r, "slippage")

        sq = queues.setdefault(symbol, [])

        if side == "BUY":
            cost = qty * price + fee_total_exec
            sq.append(_FifoLot(qty_open=qty, cost_basis_open=cost, initial_cost=cost))
            continue
        if side != "SELL":
            raise ValueError(f"fills row {i}: side must be BUY or SELL")

        remaining = qty
        net_row = qty * price - fee_total_exec
        while remaining > eps:
            if not sq:
                raise ValueError(f"fills row {i}: SELL exceeds open qty for {symbol!r} ({key})")
            lot = sq[0]
            take = min(remaining, lot.qty_open)
            cost_piece = lot.cost_basis_open * (take / lot.qty_open)
            proceeds_piece = net_row * (take / qty)
            lot.proceeds_closed += proceeds_piece
            lot.cost_basis_open -= cost_piece
            lot.qty_open -= take
            remaining -= take
            if lot.qty_open <= eps:
                closed.append(lot.proceeds_closed - lot.initial_cost)
                sq.pop(0)

    return closed


def filter_rows_for_fifo_kpis(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Filas ejecutables FIFO: motor conocido + ``qty`` y ``price`` presentes."""
    out: list[dict[str, str]] = []
    for r in rows:
        if str(r.get("qty") or "").strip() == "" or str(r.get("price") or "").strip() == "":
            continue
        if _motor_fill_key(r) not in ("short", "long"):
            continue
        if str(r.get("ts") or "").strip() == "":
            continue
        out.append(r)
    return out


def sort_fills_by_ts(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Estable por ``ts`` (ISO fecha/datetime) y orden de llegada."""
    indexed = [(j, _parse_ts(r["ts"]), r) for j, r in enumerate(rows)]
    indexed.sort(key=lambda t: (t[1], t[0]))
    return [x[2] for x in indexed]


def fifo_kpis_from_trade_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    """Pipeline §8: ordena y calcula stats de round-trip (sin tocar CSV de costos globales)."""
    return roundtrip_trade_stats(sort_fills_by_ts(filter_rows_for_fifo_kpis(rows)))


def roundtrip_trade_stats(sorted_fills: list[dict[str, str]]) -> dict[str, Any]:
    """Hit rate / profit factor por motor (``short``, ``long``) y agregado (``total``)."""
    short_pnls = fifo_roundtrip_pnls_for_motor(sorted_fills, "short")
    long_pnls = fifo_roundtrip_pnls_for_motor(sorted_fills, "long")
    all_pnls = short_pnls + long_pnls
    return {
        "short": _hit_rate_profit_factor_block(short_pnls),
        "long": _hit_rate_profit_factor_block(long_pnls),
        "total": _hit_rate_profit_factor_block(all_pnls),
    }


def _hit_rate_profit_factor_block(roundtrip_pnls: list[float]) -> dict[str, Any]:
    if not roundtrip_pnls:
        return {
            "n_round_trips": 0,
            "hit_rate": None,
            "hit_rate_na_reason": "no_round_trips",
            "profit_factor": None,
            "profit_factor_na_reason": "no_round_trips",
        }
    n = len(roundtrip_pnls)
    n_hits = sum(1 for p in roundtrip_pnls if p > 0)
    gross_wins = sum(p for p in roundtrip_pnls if p > 0)
    gross_losses_abs = sum(-p for p in roundtrip_pnls if p < 0)

    block: dict[str, Any] = {
        "n_round_trips": n,
        "hit_rate": n_hits / n,
        "hit_rate_na_reason": None,
    }

    tol = 1e-18
    if gross_losses_abs > tol:
        block["profit_factor"] = gross_wins / gross_losses_abs
        block["profit_factor_na_reason"] = None
    elif gross_wins > tol:
        block["profit_factor"] = math.inf  # JSON: véase ``write_report_json`` (string ``inf``)
        block["profit_factor_na_reason"] = None
    else:
        block["profit_factor"] = None
        block["profit_factor_na_reason"] = "no_wins_or_losses"

    return block


def segment_risk_and_fills(
    *,
    equity_segment: list[float],
    fills_block: dict[str, Any],
    fills_field: Literal["short", "long", "total"],
    trading_days_per_year: int,
) -> dict[str, Any]:
    """Empaqueta Sharpe/Sortino (§6) del segment de equity + métricas de fills del bloque §8."""
    rets, rets_na = compute_daily_simple_returns(equity_segment)
    sharpe: float | None = None
    sharpe_na: str | None = rets_na
    sortino: float | None = None
    sortino_na: str | None = rets_na
    if rets is not None:
        sharpe, sharpe_na = compute_sharpe_annualized(rets, trading_days_per_year=trading_days_per_year)
        sortino, sortino_na = compute_sortino_annualized(rets, trading_days_per_year=trading_days_per_year)

    fb = fills_block[fills_field]
    return {
        "sharpe_annualized": sharpe,
        "sharpe_na_reason": sharpe_na,
        "sortino_annualized": sortino,
        "sortino_na_reason": sortino_na,
        "hit_rate": fb["hit_rate"],
        "hit_rate_na_reason": fb["hit_rate_na_reason"],
        "profit_factor": fb["profit_factor"],
        "profit_factor_na_reason": fb["profit_factor_na_reason"],
        "n_round_trips": fb["n_round_trips"],
    }


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


def compute_turnover_long_monthly(
    equity_rows: list[dict[str, str]],
    trade_rows: list[dict[str, str]],
) -> dict[str, Any]:
    """sec. 9.1: turnover mensual del largo = sum(|notional|)/(2*avg(equity_long))."""
    eq_by_month: dict[str, list[float]] = {}
    for r in equity_rows:
        ts = _parse_ts(str(r["ts"]))
        m = f"{ts.year:04d}-{ts.month:02d}"
        eq_by_month.setdefault(m, []).append(_f(r, "equity_long"))

    notional_by_month: dict[str, float] = {}
    for r in trade_rows:
        if _motor_fill_key(r) != "long":
            continue
        if str(r.get("qty") or "").strip() == "" or str(r.get("price") or "").strip() == "":
            continue
        ts = _parse_ts(str(r["ts"]))
        m = f"{ts.year:04d}-{ts.month:02d}"
        qty = abs(float(r["qty"]))
        price = abs(float(r["price"]))
        notional_by_month[m] = notional_by_month.get(m, 0.0) + qty * price

    months = sorted(set(eq_by_month) | set(notional_by_month))
    monthly: dict[str, dict[str, Any]] = {}
    for m in months:
        eq_list = eq_by_month.get(m, [])
        avg_eq = (sum(eq_list) / len(eq_list)) if eq_list else 0.0
        notional = notional_by_month.get(m, 0.0)
        if avg_eq == 0.0:
            monthly[m] = {
                "turnover_long_monthly": None,
                "turnover_long_monthly_na_reason": "zero_avg_equity_long",
                "sum_abs_notional_long": notional,
                "avg_equity_long": avg_eq,
            }
        else:
            monthly[m] = {
                "turnover_long_monthly": notional / (2.0 * avg_eq),
                "turnover_long_monthly_na_reason": None,
                "sum_abs_notional_long": notional,
                "avg_equity_long": avg_eq,
            }

    last_month = months[-1] if months else None
    return {
        "monthly": monthly,
        "last_month": last_month,
        "turnover_long_monthly_last": (
            None if last_month is None else monthly[last_month]["turnover_long_monthly"]
        ),
        "turnover_long_monthly_last_na_reason": (
            "no_months" if last_month is None else monthly[last_month]["turnover_long_monthly_na_reason"]
        ),
    }


def compute_alpha_vs_benchmark_aligned(
    rows: list[dict[str, str]],
    benchmark_rows: list[dict[str, str]],
    *,
    equity_key: str,
) -> dict[str, Any]:
    """sec. 12: alpha = retorno simple compuesto bot - benchmark (inner join por fecha)."""
    if len(rows) < 2:
        return {
            "alpha_simple_return": None,
            "alpha_na_reason": "insufficient_history",
            "n_obs": 0,
        }
    bench_by_day: dict[date, float] = {}
    for r in benchmark_rows:
        d = _parse_ts(str(r["ts"]))
        bench_by_day[d] = float(r["benchmark_return"])

    bot_by_day: dict[date, float] = {}
    for i in range(1, len(rows)):
        d = _parse_ts(str(rows[i]["ts"]))
        prev = _f(rows[i - 1], equity_key)
        cur = _f(rows[i], equity_key)
        if prev <= 0:
            continue
        bot_by_day[d] = cur / prev - 1.0

    common_days = sorted(set(bot_by_day) & set(bench_by_day))
    if not common_days:
        return {
            "alpha_simple_return": None,
            "alpha_na_reason": "no_inner_join_observations",
            "n_obs": 0,
        }

    bot_comp = 1.0
    bench_comp = 1.0
    for d in common_days:
        bot_comp *= 1.0 + bot_by_day[d]
        bench_comp *= 1.0 + bench_by_day[d]
    r_bot = bot_comp - 1.0
    r_bench = bench_comp - 1.0
    return {
        "alpha_simple_return": r_bot - r_bench,
        "alpha_na_reason": None,
        "n_obs": len(common_days),
        "bot_simple_return_aligned": r_bot,
        "benchmark_simple_return_aligned": r_bench,
    }


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
    policy_path: str | Path | None = None,
    benchmark_returns_path: str | Path | None = None,
) -> KpiV0Report:
    """Assemble report from CSV paths (§2.1 equity + §2.2 trades or split cost columns).

    ``policy_path`` suministra targets ``weights``/``geo`` para drift (spec sec. 11); si es ``None``,
    se usan claves anidadas en metadata o valores por defecto 30/70 y 20/80.
    """
    rows, fieldnames = load_equity_csv(equity_path)
    equities = [_f(r, "equity_total") for r in rows]
    eq_short = [_f(r, "equity_short") for r in rows]
    eq_long = [_f(r, "equity_long") for r in rows]

    meta: dict[str, Any] = {}
    if metadata_path is not None:
        meta = load_metadata(metadata_path)

    if policy_path is not None:
        mandate_targets = load_mandate_targets_from_policy_yaml(policy_path)
    else:
        mandate_targets = targets_from_metadata(meta) or MandateTargets(
            0.30,
            0.70,
            0.20,
            0.80,
        )
    mandate_bands = bands_from_metadata(meta)
    mandate_drift = mandate_drift_for_equity_rows(
        rows,
        fieldnames=fieldnames,
        targets=mandate_targets,
        bands=mandate_bands,
    )

    tdy = int(meta.get("trading_days_per_year", 252))

    ann, ann_na = compute_net_return_annualized(equities, trading_days_per_year=tdy)
    mdd = compute_max_drawdown(equities)

    costs: dict[str, float] | None = None
    costs_na: str | None = None
    tr_rows: list[dict[str, str]] = []
    if trades_path is not None:
        tr_rows = load_trades_csv(trades_path)
    br_rows: list[dict[str, str]] = []
    if benchmark_returns_path is not None:
        br_rows = load_benchmark_returns_csv(benchmark_returns_path)

    if equity_has_motor_cost_columns(fieldnames):
        costs = sum_costs_by_motor_from_equity(rows)
    elif trades_path is not None:
        costs = sum_costs_by_motor_from_trades(tr_rows) if tr_rows else {"short": 0.0, "long": 0.0}
    else:
        costs_na = "missing_trades_and_no_costs_day_short_long_in_equity_csv"

    rt_block = fifo_kpis_from_trade_rows(tr_rows)

    ts_start = rows[0]["ts"].strip() if rows else None
    ts_end = rows[-1]["ts"].strip() if rows else None

    risk_total = segment_risk_and_fills(
        equity_segment=equities,
        fills_block=rt_block,
        fills_field="total",
        trading_days_per_year=tdy,
    )
    segment = {
        "net_return_annualized": ann,
        "net_return_annualized_na_reason": ann_na,
        "max_drawdown": mdd,
        "n_trading_days": len(rows),
        "n_return_steps": max(0, len(rows) - 1),
        **risk_total,
    }

    segment_short_only = segment_risk_and_fills(
        equity_segment=eq_short,
        fills_block=rt_block,
        fills_field="short",
        trading_days_per_year=tdy,
    )
    segment_long_only = segment_risk_and_fills(
        equity_segment=eq_long,
        fills_block=rt_block,
        fills_field="long",
        trading_days_per_year=tdy,
    )
    rolling_series, rolling_last = compute_rolling_mdd_calmar_12m(eq_long, trading_days_per_year=tdy)
    segment_long_only.update(rolling_last)
    segment_long_only["mdd_12m_rolling_series"] = rolling_series

    to_long = compute_turnover_long_monthly(rows, tr_rows)
    segment_long_only["turnover_long_monthly"] = to_long["monthly"]
    segment_long_only["turnover_long_monthly_last"] = to_long["turnover_long_monthly_last"]
    segment_long_only["turnover_long_monthly_last_na_reason"] = to_long[
        "turnover_long_monthly_last_na_reason"
    ]
    segment_long_only["turnover_long_monthly_last_month"] = to_long["last_month"]

    alpha_block: dict[str, Any] | None = None
    if br_rows:
        alpha_block = {
            "total": compute_alpha_vs_benchmark_aligned(rows, br_rows, equity_key="equity_total"),
            "long": compute_alpha_vs_benchmark_aligned(rows, br_rows, equity_key="equity_long"),
        }

    rep = KpiV0Report(
        spec_id=str(meta.get("spec_id", "rpt_kpi.v1")),
        run_id=(str(meta["run_id"]) if meta.get("run_id") is not None else None),
        reporting_ccy=str(meta.get("reporting_ccy", "USD")),
        trading_days_per_year=tdy,
        ts_start=ts_start,
        ts_end=ts_end,
        segment_total=segment,
        segment_short=segment_short_only,
        segment_long=segment_long_only,
        costs_by_motor=costs,
        costs_na_reason=costs_na,
        mandate_drift=mandate_drift,
        alpha_vs_benchmark=alpha_block,
    )
    return rep


def _sanitize_json_values(obj: Any) -> Any:
    """Strict JSON-friendly: ``inf`` → ``\"inf\"`` (§8)."""
    if isinstance(obj, dict):
        return {k: _sanitize_json_values(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_json_values(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj):
            return None
        if math.isinf(obj):
            return "inf"
    return obj


def write_report_json(report: KpiV0Report, path: str | Path) -> None:
    p = Path(path)
    payload = _sanitize_json_values(report.to_json_dict())
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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

    def num_or_na(
        x: float | str | None,
        *,
        na_reason: str | None = None,
        nd: int = 4,
    ) -> str:
        if x is None:
            return f"NA ({na_reason})" if na_reason else "NA"
        if isinstance(x, str):
            return x
        if isinstance(x, float) and math.isinf(x) and x > 0:
            return "inf"
        return f"{x:.{nd}f}"

    sh = seg.get("sharpe_annualized")
    sh_na = seg.get("sharpe_na_reason")
    so = seg.get("sortino_annualized")
    so_na = seg.get("sortino_na_reason")
    hr = seg.get("hit_rate")
    hr_na = seg.get("hit_rate_na_reason")
    pf = seg.get("profit_factor")
    pf_na = seg.get("profit_factor_na_reason")

    lines = [
        f"# KPI report (`{report.report_version}`)",
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
        f"| Sharpe (anual) | {num_or_na(sh, na_reason=sh_na)} |",
        f"| Sortino (anual) | {num_or_na(so, na_reason=so_na)} |",
        f"| Hit rate (round-trips) | {num_or_na(hr, na_reason=hr_na)} |",
        f"| Profit factor | {num_or_na(pf, na_reason=pf_na)} |",
        f"| Round-trips (`n_round_trips`) | {seg.get('n_round_trips')} |",
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

    md = report.mandate_drift
    if md is not None:
        snap = md.get("snapshot_last_ts") or {}
        lines.extend(
            [
                "## Mandato: drift 30/70 y 20/80 (último día de la ventana)",
                "",
                f"- **snapshot `ts`**: {snap.get('ts', '—')}",
            ]
        )
        if snap.get("na_reason"):
            lines.append(f"- **NA**: {snap['na_reason']}")
        else:
            lines.extend(
                [
                    "| Eje | Drift (pp) |",
                    "|-----|------------|",
                    f"| Corto vs objetivo | {num_or_na(snap.get('drift_short_pp'), nd=4)} |",
                    f"| Largo vs objetivo | {num_or_na(snap.get('drift_long_pp'), nd=4)} |",
                ]
            )
            gna = snap.get("geo_na_reason")
            if gna:
                lines.append(f"| Geo AR/US | NA ({gna}) |")
            else:
                lines.extend(
                    [
                        f"| AR vs objetivo | {num_or_na(snap.get('drift_ar_pp'), nd=4)} |",
                        f"| US vs objetivo | {num_or_na(snap.get('drift_us_pp'), nd=4)} |",
                    ]
                )
            ob = snap.get("outside_band_axes")
            if ob:
                lines.append(f"- **Fuera de banda declarada (solo informe)**: {', '.join(ob)}")
            elif snap.get("bands_half_width_pp"):
                lines.append("- **Bandas (± medio ancho pp, metadata)**: declaradas; snapshot dentro de umbral en todos los ejes con banda.")
        tgt = md.get("targets") or {}
        lines.extend(
            [
                "",
                f"- **Objetivos usados**: corto {tgt.get('weight_short')}, largo {tgt.get('weight_long')}; "
                f"AR {tgt.get('weight_ar')}, US {tgt.get('weight_us')} (fracciones sobre `equity_total`).",
                "- **Serie diaria**: campo `mandate_drift.series` en el JSON de salida.",
                "",
            ]
        )

    seg_l = report.segment_long
    mdd12 = seg_l.get("mdd_12m_rolling_last")
    mdd12_na = seg_l.get("mdd_12m_rolling_na_reason")
    cal12 = seg_l.get("calmar_12m_last")
    cal12_na = seg_l.get("calmar_12m_na_reason")
    to_last = seg_l.get("turnover_long_monthly_last")
    to_last_na = seg_l.get("turnover_long_monthly_last_na_reason")
    to_last_month = seg_l.get("turnover_long_monthly_last_month")
    lines.extend(
        [
            "## Bloque largo (v3)",
            "",
            f"- **MDD_12m rolling (último)**: {pct(mdd12, mdd12_na)}",
            f"- **Calmar_12m (último)**: {num_or_na(cal12, na_reason=cal12_na)}",
            f"- **turnover_long_monthly (último mes `{to_last_month}`)**: {num_or_na(to_last, na_reason=to_last_na)}",
            "",
        ]
    )

    if report.alpha_vs_benchmark is not None:
        a_tot = report.alpha_vs_benchmark.get("total", {})
        a_long = report.alpha_vs_benchmark.get("long", {})
        lines.extend(
            [
                "## Alpha vs benchmark mixto (alineado)",
                "",
                "| Segmento | Alpha simple | Obs. inner join |",
                "|----------|--------------|-----------------|",
                f"| Total | {num_or_na(a_tot.get('alpha_simple_return'), na_reason=a_tot.get('alpha_na_reason'))} | {a_tot.get('n_obs')} |",
                f"| Largo | {num_or_na(a_long.get('alpha_simple_return'), na_reason=a_long.get('alpha_na_reason'))} | {a_long.get('n_obs')} |",
                "",
            ]
        )

    p.write_text("\n".join(lines), encoding="utf-8")
