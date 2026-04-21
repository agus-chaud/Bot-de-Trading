"""Walk-forward pre-gate for the short-term block (costs, turnover, monthly short DD).

Evalúa ventanas OOS consecutivas sobre una serie diaria ya normalizada
(`date -> {symbol -> bar}`), ejecuta el mismo pipeline que paper (`create_short_term_daily_backtester`)
y rechaza si algún umbral de política se viola.

Sin tuning de hiperparámetros en v1: cada ventana es evaluación out-of-sample sobre
datos fijos; el tramo previo aporta solo historial para features.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .cost_model import CostModel, MarketCostConfig
from .ledger import PortfolioLedger
from .paper_broker_sim import PaperBrokerSim
from .short_term_day_runner import create_short_term_daily_backtester


def _cost_model_from_policy(policy_doc: dict[str, Any]) -> CostModel:
    m = policy_doc["markets"]
    return CostModel(
        market_configs={
            "US": MarketCostConfig(
                commission_bps_per_side=float(m["US"]["commission_bps_per_side"]),
                slippage_bps=float(m["US"]["slippage_bps"]),
                min_spread_bps=0.5,
            ),
            "AR": MarketCostConfig(
                commission_bps_per_side=float(m["AR"]["commission_bps_per_side"]),
                slippage_bps=float(m["AR"]["slippage_bps"]),
                min_spread_bps=0.5,
            ),
        }
    )


def build_history_before_day(
    symbol: str,
    trading_day: date,
    sorted_dates: list[date],
    bars_by_date: dict[date, dict[str, dict[str, float]]],
    max_points: int,
) -> list[dict[str, float]]:
    """Historial estrictamente anterior a `trading_day` (últimos `max_points` días con bar)."""
    out: list[dict[str, float]] = []
    for d in sorted_dates:
        if d >= trading_day:
            break
        day_bar = bars_by_date.get(d, {}).get(symbol)
        if day_bar is None or "close" not in day_bar:
            continue
        vol = float(day_bar.get("volume", 0.0))
        out.append({"close": float(day_bar["close"]), "volume": vol})
    return out[-max_points:]


def _walk_forward_windows(
    sorted_dates: list[date],
    *,
    burn_in_trading_days: int,
    oos_trading_days: int,
    step_trading_days: int,
) -> list[list[date]]:
    """Ventanas OOS [start:end) sobre días de trading consecutivos en `sorted_dates`."""
    if burn_in_trading_days < 0 or oos_trading_days < 1 or step_trading_days < 1:
        raise ValueError("invalid walk_forward shape")
    windows: list[list[date]] = []
    i = burn_in_trading_days
    while i + oos_trading_days <= len(sorted_dates):
        windows.append(sorted_dates[i : i + oos_trading_days])
        i += step_trading_days
    return windows


def _simulate_oos_window(
    *,
    window_days: list[date],
    bars_by_date: dict[date, dict[str, dict[str, float]]],
    sorted_dates: list[date],
    policy_doc: dict[str, Any],
    repo_root: Path,
    starting_cash: float,
    history_cap: int,
) -> dict[str, Any]:
    ledger = PortfolioLedger(starting_cash=float(starting_cash))
    broker = PaperBrokerSim(ledger=ledger, cost_model=_cost_model_from_policy(policy_doc))
    backtester = create_short_term_daily_backtester(
        policy_doc=policy_doc,
        repo_root=repo_root,
        ledger=ledger,
        broker=broker,
        calendar_store=None,
        corporate_actions_store=None,
    )

    total_fees = 0.0
    sum_buy_notional = 0.0
    min_monthly_dd_short = 0.0
    equities: list[float] = []
    n_fills = 0

    for d in window_days:
        daily = bars_by_date.get(d)
        if not daily:
            continue
        hist: dict[str, list[dict[str, float]]] = {}
        for sym in daily:
            hist[sym] = build_history_before_day(sym, d, sorted_dates, bars_by_date, history_cap)

        events = backtester.run_day(
            trading_day=d,
            daily_bars=daily,
            pipeline_context={"history_by_symbol": hist},
        )
        fills = events[4].payload
        if isinstance(fills, list):
            for fill in fills:
                n_fills += 1
                total_fees += float(fill.get("fee", 0.0))
                if str(fill.get("side", "")).upper() == "BUY":
                    sum_buy_notional += float(fill["qty"]) * float(fill["price"])

        snap = events[-1].payload
        if isinstance(snap, dict):
            sb = snap.get("short_bucket") or {}
            dd = float(sb.get("monthly_drawdown", 0.0))
            min_monthly_dd_short = min(min_monthly_dd_short, dd)
            equities.append(float(snap.get("equity_total", starting_cash)))

    avg_equity = sum(equities) / max(len(equities), 1)
    n_days = len(window_days)
    turnover_ann = 0.0
    if avg_equity > 0 and n_days > 0:
        turnover_ann = (sum_buy_notional / avg_equity) * (252.0 / float(n_days))

    fee_ratio = total_fees / max(float(starting_cash), 1.0)

    return {
        "trading_days": tuple(str(x) for x in window_days),
        "total_fees": total_fees,
        "fee_ratio_of_initial": fee_ratio,
        "sum_buy_notional": sum_buy_notional,
        "min_short_monthly_drawdown": min_monthly_dd_short,
        "avg_equity": avg_equity,
        "n_days": n_days,
        "turnover_annualized_proxy": turnover_ann,
        "n_fills": n_fills,
    }


@dataclass
class PreGateWindowResult:
    """Resultado de una ventana OOS."""

    metrics: dict[str, Any]
    passed: bool
    violations: list[str] = field(default_factory=list)


@dataclass
class PreGateReport:
    """Agregado walk-forward + veredicto."""

    passed: bool
    windows: list[PreGateWindowResult]
    global_failures: list[str] = field(default_factory=list)


def run_short_term_pre_gate(
    *,
    policy_doc: dict[str, Any],
    repo_root: Path,
    bars_by_date: dict[date, dict[str, dict[str, float]]],
    starting_cash: float = 100_000.0,
    trading_days: list[date] | None = None,
) -> PreGateReport:
    """Ejecuta validación mínima pre-gate según `short_term_pre_gate` en policy (si falta, no-op pass)."""
    cfg = policy_doc.get("short_term_pre_gate")
    if not cfg or not bool(cfg.get("enabled", False)):
        return PreGateReport(passed=True, windows=[], global_failures=[])

    wf = cfg["walk_forward"]
    thr = cfg["thresholds"]
    oos_len = int(wf["oos_trading_days"])
    step = int(wf["step_trading_days"])
    min_windows = int(wf["min_oos_windows"])

    momentum = int(policy_doc["short_term_engine"]["momentum_lookback_days"])
    burn_in = momentum + 25

    sorted_dates = sorted(trading_days or list(bars_by_date.keys()))
    if not sorted_dates:
        return PreGateReport(
            passed=False,
            windows=[],
            global_failures=["empty_trading_calendar"],
        )

    windows = _walk_forward_windows(sorted_dates, burn_in_trading_days=burn_in, oos_trading_days=oos_len, step_trading_days=step)
    if len(windows) < min_windows:
        return PreGateReport(
            passed=False,
            windows=[],
            global_failures=[
                f"insufficient_oos_windows:need_{min_windows}_got_{len(windows)}_"
                f"(sorted_days={len(sorted_dates)},burn_in={burn_in},oos={oos_len},step={step})"
            ],
        )

    floor_raw = thr.get("monthly_short_drawdown_floor")
    kill_dd = float(policy_doc["short_kill_switch_monthly_dd"])
    monthly_floor = float(floor_raw) if floor_raw is not None else float(kill_dd)
    max_fee = float(thr["max_fee_pct_of_initial_per_window"])
    max_to = float(thr["max_turnover_annualized"])

    history_cap = max(momentum + 30, 60)

    results: list[PreGateWindowResult] = []
    for w in windows:
        metrics = _simulate_oos_window(
            window_days=w,
            bars_by_date=bars_by_date,
            sorted_dates=sorted_dates,
            policy_doc=policy_doc,
            repo_root=repo_root,
            starting_cash=starting_cash,
            history_cap=history_cap,
        )
        violations: list[str] = []
        if float(metrics["min_short_monthly_drawdown"]) < monthly_floor:
            violations.append(
                f"monthly_short_dd {metrics['min_short_monthly_drawdown']:.6f} < floor {monthly_floor:.6f}"
            )
        if float(metrics["fee_ratio_of_initial"]) > max_fee:
            violations.append(
                f"fee_ratio {metrics['fee_ratio_of_initial']:.6f} > max {max_fee:.6f}"
            )
        if float(metrics["turnover_annualized_proxy"]) > max_to:
            violations.append(
                f"turnover_ann {metrics['turnover_annualized_proxy']:.4f} > max {max_to:.4f}"
            )
        results.append(
            PreGateWindowResult(metrics=metrics, passed=len(violations) == 0, violations=violations)
        )

    passed = all(r.passed for r in results)
    return PreGateReport(passed=passed, windows=results, global_failures=[])
