#!/usr/bin/env python3
"""Export the *research simulation* as a dashboard payload (Opción B).

**Qué hace (simple):** corre la simulación walk-forward con aportes mensuales
(``run_wf_research_sim``) sobre toda la historia OHLCV y traduce su resultado al
MISMO contrato JSON que ``GET /api/dashboard`` / el payload paper-live. Se guarda
como ``dashboard_payload.sim.json`` — un segundo archivo, al lado del live, que la
UI sirve en una pestaña "Simulación".

Por qué un payload aparte (bajo acople): el dashboard live mide la corrida real
(8 días desde inception). La sim corre el bot sobre +1 año de OHLCV con aportes
DCA de 500k ARS/mes → curva larga y KPIs (Calmar) que SÍ significan algo. Reusa el
mismo componente visual; solo cambia la fuente de datos.

Métricas: la sim mide con **TWR** (time-weighted, excluye los aportes de la base)
y el criterio pre-registrado es **Calmar** (annualized_twr / |max_drawdown|).

Ejemplos::

    python scripts/export_sim_dashboard_payload.py
    python scripts/export_sim_dashboard_payload.py --contrib 500000 \\
        --out web/public/dashboard_payload.sim.json --pretty
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.storage import MarketDB  # noqa: E402
from reporting.twr_walk_forward import (  # noqa: E402
    annualized_sharpe,
    annualized_twr,
    cumulative_twr,
    max_drawdown,
    time_weighted_daily_returns,
)
from scripts.export_dashboard_payload import (  # noqa: E402
    EXPORT_VERSION,
    validate_payload_shape,
    write_dashboard_payload,
)
from scripts.run_paper_live import load_required_calendar_store  # noqa: E402
from scripts.run_wf_research_sim import (  # noqa: E402
    DEFAULT_CONTRIB,
    DEFAULT_DB,
    _prepare_sim_db,
    _trading_days,
    run_research_sim,
)

DEFAULT_START = date(2025, 1, 1)


def _parse_date(raw: str) -> date:
    return date.fromisoformat(raw)


def _equity_curve(series: list[Any]) -> list[dict[str, Any]]:
    # La serie de la sim solo expone equity total por día (DailyPoint: day, equity,
    # contribution). No hay desglose short/long/cash por día, así que esos quedan en 0
    # y la curva muestra el equity total — que es lo relevante para comparar.
    return [
        {
            "date": pt.day.isoformat(),
            "equity_total": pt.equity,
            "equity_short": 0.0,
            "equity_long": 0.0,
            "cash": 0.0,
            "mv_us": 0.0,
            "mv_ar": 0.0,
        }
        for pt in series
    ]


def build_sim_payload(
    series: list[Any],
    *,
    contribution: float,
    currency: str = "ARS",
) -> dict[str, Any]:
    """Translate a research-sim daily series into a dashboard-contract payload.

    Pure: takes the series (DailyPoint: day, equity, contribution) and emits the
    same JSON shape as the paper-live payload, mapping the TWR/Calmar metrics into
    the KPI block. Separated from the sim run so it can be tested without the heavy
    backtest.
    """
    if not series:
        raise ValueError("Research sim produced no daily points")

    returns = time_weighted_daily_returns(series)
    ann_twr = annualized_twr(returns)
    mdd = max_drawdown(returns)
    calmar = (ann_twr / abs(mdd)) if mdd < 0 else None
    sharpe = annualized_sharpe(returns)
    cum_twr = cumulative_twr(returns)

    total_contrib = sum(pt.contribution for pt in series)
    final_equity = series[-1].equity
    period_start = series[0].day.isoformat()
    period_end = series[-1].day.isoformat()

    detail = (
        f"Aporte mensual {contribution:,.0f} {currency} · "
        f"total aportado {total_contrib:,.0f} · "
        f"TWR acumulado {cum_twr * 100:+.2f}% · "
        f"Calmar {calmar:.2f}" if calmar is not None else
        f"Aporte mensual {contribution:,.0f} {currency} · "
        f"total aportado {total_contrib:,.0f} · "
        f"TWR acumulado {cum_twr * 100:+.2f}% · Calmar n/d"
    )

    payload: dict[str, Any] = {
        "meta": {
            "mode": "research_sim",
            "currency": currency,
            "starting_cash": contribution,
            "inception_date": period_start,
            "last_trading_day": period_end,
            "equity_total": final_equity,
            "num_open_positions": 0,
        },
        "data_freshness": {
            "status": "ok",
            "message": "Simulación de investigación; no aplica el chequeo de DB local.",
            "commits_behind": 0,
            "worktree_dirty": False,
            "remote_ref": "",
            "sync_hint": "",
        },
        "equity_curve": _equity_curve(series),
        "positions": [],
        "recent_fills": [],
        "risk": {
            "trading_allowed": True,
            "kill_switch": {"active": False, "activated_at": None, "monthly_dd": None},
            "thresholds": {
                "short_kill_switch_monthly_dd": 0.0,
                "max_daily_loss_short_pct": 0.0,
            },
            "factors": [
                {
                    "level": "info",
                    "code": "research_mode",
                    "message": (
                        "Modo investigación: aportes mensuales DCA, métricas TWR. "
                        "No es el gate de producción."
                    ),
                }
            ],
        },
        "kpis": {
            "status": "ok",
            "n_days": len(series),
            "sharpe_annualized": sharpe,
            "sharpe_na_reason": None,
            "sortino_annualized": None,
            "max_drawdown": mdd,
            "net_return_annualized": ann_twr,
            "calmar_total": calmar,
            "calmar_12m_long": None,
            "calmar_12m_na_reason": None,
            "hit_rate": None,
            "profit_factor": None,
            "ts_start": period_start,
            "ts_end": period_end,
        },
        "alerts": [
            {
                "severity": "info",
                "code": "research_sim",
                "title": "Simulación walk-forward (aportes mensuales)",
                "detail": detail,
            }
        ],
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "export_version": EXPORT_VERSION,
        "export_source": {
            "kind": "research_sim",
            "monthly_contribution": str(contribution),
            "period": f"{period_start}..{period_end}",
        },
    }
    return payload


def export_sim_dashboard_payload(
    *,
    db_path: Path,
    policy_path: Path,
    contribution: float,
    start: date,
    end: date,
    enable_long: bool = True,
    currency: str = "ARS",
) -> dict[str, Any]:
    """Run the research sim over the full history and build its dashboard payload."""
    if not db_path.is_file():
        raise FileNotFoundError(f"Database not found: {db_path}")

    policy_doc = yaml.safe_load(policy_path.open(encoding="utf-8"))
    sim_path = _prepare_sim_db(db_path)
    db = MarketDB(str(sim_path))
    calendar_store = load_required_calendar_store(policy_doc)

    days = _trading_days(db, start, end)
    if not days:
        raise ValueError(f"No trading days in range {start}..{end}")

    series = run_research_sim(
        db,
        days,
        policy_doc,
        contribution=contribution,
        calendar_store=calendar_store,
        enable_long=enable_long,
    )
    return build_sim_payload(series, contribution=contribution, currency=currency)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export the research simulation as a dashboard payload (Opción B).",
    )
    # Misma DB que la sim de investigación: market_backfill.db (historia completa).
    # NO data/market.db (paper-live), cuya data US stale envenena la valuación.
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--policy", type=Path, default=REPO_ROOT / "config" / "policy.v1.yaml")
    parser.add_argument(
        "--contrib",
        type=float,
        default=DEFAULT_CONTRIB,
        help="Aporte mensual en ARS (default: la mejor sim, 500.000)",
    )
    parser.add_argument("--start", type=_parse_date, default=DEFAULT_START)
    parser.add_argument("--end", type=_parse_date, default=date.today())
    parser.add_argument("--no-long", action="store_true", help="Solo sleeve corto")
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "web" / "public" / "dashboard_payload.sim.json",
        help="Output JSON path (default: web/public/dashboard_payload.sim.json)",
    )
    parser.add_argument("--pretty", action="store_true", help="Indent JSON for human diff")
    args = parser.parse_args(argv)

    try:
        payload = export_sim_dashboard_payload(
            db_path=args.db,
            policy_path=args.policy,
            contribution=args.contrib,
            start=args.start,
            end=args.end,
            enable_long=not args.no_long,
        )
        validate_payload_shape(payload)
        write_dashboard_payload(payload, args.out, pretty=args.pretty)
    except FileNotFoundError as exc:
        print(f"export_sim_dashboard_payload: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"export_sim_dashboard_payload: failed: {exc}", file=sys.stderr)
        return 2

    k = payload["kpis"]
    print(
        f"Wrote {args.out} — sim research, days={k['n_days']}, "
        f"calmar={k['calmar_total']}, last_day={payload['meta']['last_trading_day']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
