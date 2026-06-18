#!/usr/bin/env python3
"""Simulador walk-forward de INVESTIGACIÓN con aportes mensuales y métricas TWR.

MODO INVESTIGACIÓN — NO es el gate de producción ni el paper-live productivo:
  - Corre sobre una copia aislada de la DB (por defecto la backfilleada).
  - Modela aportes periódicos (DCA): cada primer día hábil de mes entra capital nuevo,
    que los motores despliegan según el split 30/70.
  - Mide con TWR (time-weighted), que excluye los aportes de la base — ver
    `reporting/twr_walk_forward.py`. Reporta walk-forward OOS configurable (120/60/30
    por defecto) y, como secundario, la TIR (MWR) que es tu experiencia real en pesos.

El walk-forward acá es exploratorio: sus parámetros son libres. Esto NO reemplaza el
gate KPI OOS congelado (POLICY.md §13). Cambiar el gate de producción exige pre-registro
+ ADR; este script es para entender la estrategia, no para autorizar capital real.

Uso::

  python scripts/run_wf_research_sim.py
  python scripts/run_wf_research_sim.py --contrib 500000 --burn-in 120 --oos 60 --step 30
  python scripts/run_wf_research_sim.py --db data/market_backfill.db --start 2025-01-01
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core_sim.long_term_monthly_runner import create_long_term_monthly_backtester  # noqa: E402
from core_sim.paper_broker_sim import PaperBrokerSim  # noqa: E402
from core_sim.short_hedge_engine import short_hedge_config_from_policy_dict  # noqa: E402
from core_sim.short_hedge_runner import (  # noqa: E402
    load_hedge_whitelist,
    run_hedge_sleeve_day,
)
from core_sim.short_term_day_runner import (  # noqa: E402
    create_short_term_daily_backtester,
    load_merged_whitelist,
)
from data.storage import MarketDB  # noqa: E402
from reporting.twr_walk_forward import (  # noqa: E402
    DailyPoint,
    annualized_sharpe,
    annualized_twr,
    cumulative_twr,
    evaluate_walk_forward,
    max_drawdown,
    money_weighted_return,
    time_weighted_daily_returns,
)
from scripts.run_paper_live import (  # noqa: E402
    PAPER_LIVE_MODE,
    _VENUE_MAP,
    _build_history_from_db,
    _build_long_pipeline_context,
    _cost_model_from_policy,
    _overlay_ar_long_sleeve_bars_from_db,
    _resilient_snapshot,
    load_required_calendar_store,
)

logger = logging.getLogger("wf_research_sim")

DEFAULT_DB = REPO_ROOT / "data" / "market_backfill.db"
DEFAULT_CONTRIB = 500_000.0
SIM_DIR = REPO_ROOT / "data" / "_sim"


def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise SystemExit(f"Fecha inválida {raw!r} (esperado YYYY-MM-DD)") from exc


def _prepare_sim_db(source_db: Path) -> Path:
    """Copia la DB fuente a un archivo aislado y limpia el estado de paper trading."""
    SIM_DIR.mkdir(parents=True, exist_ok=True)
    sim_path = SIM_DIR / "wf_research.db"
    if sim_path.exists():
        sim_path.unlink()
    shutil.copy2(source_db, sim_path)
    db = MarketDB(str(sim_path))
    with db._conn:
        db._conn.execute("DELETE FROM paper_snapshots")
        db._conn.execute("DELETE FROM paper_fills")
        db._conn.execute("DELETE FROM portfolio_meta")
    return sim_path


def _trading_days(db: MarketDB, start: date, end: date) -> list[date]:
    """Días con al menos una barra en el rango (cualquier venue)."""
    cur = db._conn.execute(
        "SELECT DISTINCT ts FROM ohlcv WHERE ts BETWEEN ? AND ? ORDER BY ts",
        (start.isoformat(), end.isoformat()),
    )
    return [date.fromisoformat(r[0]) for r in cur.fetchall()]


def run_research_sim(
    db: MarketDB,
    days: list[date],
    policy_doc: dict[str, Any],
    *,
    contribution: float,
    calendar_store: Any,
    enable_long: bool,
) -> list[DailyPoint]:
    """Corre el pipeline día a día con aportes mensuales. Devuelve la serie diaria.

    Modelo de aportes: el primer día hábil de cada mes el capital acumulado crece en
    ``contribution``. Como el ledger se reconstruye por día con
    ``starting_cash = aportes acumulados``, la plata nueva aparece como caja que los
    motores despliegan según policy (30/70). El aporte se registra en el DailyPoint para
    que el TWR lo excluya de la base (no es ganancia).
    """
    mode = PAPER_LIVE_MODE
    momentum = int(policy_doc["short_term_engine"]["momentum_lookback_days"])
    history_cap = max(momentum + 30, 60)
    merged_whitelist = load_merged_whitelist(REPO_ROOT, policy_doc)

    # El sleeve largo "es dueño" de sus símbolos (core + satélite). El corto NO debe
    # operarlos: SPY es CEDEAR (AR/pesos) en el largo y acción US (USD) en el universo
    # corto — si ambos lo compran, el ledger rechaza mezclar dos monedas bajo un símbolo
    # ("market mismatch"). Excluimos los símbolos del largo del universo del corto.
    lt = policy_doc.get("long_term_engine", {})
    long_symbols = {
        str(item["symbol"]).strip().upper()
        for item in (lt.get("core_lines", []) + lt.get("satellite_lines", []))
    }

    # Sleeve corto como cobertura (plan_hedge_short Fase 4): si está activo, el corto se
    # maneja como hedge_static y REEMPLAZA al momentum táctico (el split 20/10 con ambos
    # activos no es enforceable con un solo short_cash; v1 corre el hedge sobre el 30%).
    sh_raw = policy_doc.get("short_hedge") or {}
    hedge_enabled = bool(sh_raw.get("enabled", False))
    hedge_cfg = short_hedge_config_from_policy_dict(sh_raw) if hedge_enabled else None
    hedge_whitelist = load_hedge_whitelist(REPO_ROOT, policy_doc) if hedge_enabled else frozenset()
    weights_short = float(policy_doc["weights"]["short"])

    cumulative_contrib = 0.0
    seen_months: set[tuple[int, int]] = set()
    series: list[DailyPoint] = []

    for day in days:
        contribution_today = 0.0
        month_key = (day.year, day.month)
        if month_key not in seen_months:
            seen_months.add(month_key)
            cumulative_contrib += contribution
            contribution_today = contribution

        ledger = db.replay_ledger_from_fills(mode, starting_cash=cumulative_contrib)
        cost_model = _cost_model_from_policy(policy_doc)
        broker = PaperBrokerSim(ledger=ledger, cost_model=cost_model)

        daily_bars: dict[str, dict[str, float]] = {}
        for sym, market in merged_whitelist.items():
            venue = _VENUE_MAP.get(market, market)
            rows = db.get_ohlcv(sym, day, day, venue)
            if rows:
                bar = rows[0]
                daily_bars[sym] = {
                    "open": bar.open, "high": bar.high, "low": bar.low,
                    "close": bar.close, "volume": bar.volume,
                }
        if not daily_bars:
            continue

        if hedge_enabled:
            # Cobertura: el sleeve corto se rebalancea hacia la canasta (o cash si des-riesga).
            all_fills: list[dict] = run_hedge_sleeve_day(
                db=db, day=day, ledger=ledger, broker=broker,
                hedge_cfg=hedge_cfg, hedge_whitelist=hedge_whitelist,
                weights_short=weights_short, resilient_snapshot=_resilient_snapshot,
            )
        else:
            short_backtester = create_short_term_daily_backtester(
                policy_doc=policy_doc, repo_root=REPO_ROOT, ledger=ledger,
                broker=broker, calendar_store=calendar_store,
                corporate_actions_store=None, db=db,
            )
            # Universo del corto = todo MENOS los símbolos del largo (evita el conflicto SPY).
            short_bars = {s: b for s, b in daily_bars.items() if s not in long_symbols}
            history_by_symbol: dict[str, list[dict[str, float]]] = {}
            for sym, market in merged_whitelist.items():
                if sym not in short_bars:
                    continue
                venue = _VENUE_MAP.get(market, market)
                history_by_symbol[sym] = _build_history_from_db(
                    db, sym, day, venue, lookback_days=history_cap
                )
            short_events = short_backtester.run_day(
                trading_day=day,
                daily_bars=short_bars,
                pipeline_context={"history_by_symbol": history_by_symbol},
            )
            short_fills = short_events[4].payload
            all_fills = list(short_fills) if isinstance(short_fills, list) else []

        if enable_long:
            bars_long = dict(daily_bars)
            _overlay_ar_long_sleeve_bars_from_db(db, day, policy_doc, bars_long)
            snap_for_long = _resilient_snapshot(db, day, ledger)
            long_ctx = _build_long_pipeline_context(ledger, snap_for_long, calendar_store)
            long_backtester = create_long_term_monthly_backtester(
                policy_doc=policy_doc, repo_root=REPO_ROOT, ledger=ledger,
                broker=broker, calendar_store=calendar_store, db=db,
            )
            long_events = long_backtester.run_day(
                trading_day=day, daily_bars=bars_long, pipeline_context=long_ctx
            )
            long_fills = long_events[4].payload
            if isinstance(long_fills, list) and long_fills:
                all_fills.extend(long_fills)

        if all_fills:
            run_id = f"wf_{day.isoformat()}_{uuid.uuid4().hex[:8]}"
            db.persist_fills(run_id, mode, day, all_fills)

        snap = _resilient_snapshot(db, day, ledger)
        equity = float(snap.get("equity_total", 0.0)) if isinstance(snap, dict) else 0.0
        series.append(DailyPoint(day=day, equity=equity, contribution=contribution_today))

    return series


def main() -> int:
    p = argparse.ArgumentParser(description="Simulador walk-forward de investigación (TWR + aportes)")
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--policy", type=Path, default=REPO_ROOT / "config" / "policy.v1.yaml")
    p.add_argument("--start", type=_parse_date, default=date(2025, 1, 1))
    p.add_argument("--end", type=_parse_date, default=date.today())
    p.add_argument("--contrib", type=float, default=DEFAULT_CONTRIB, help="Aporte mensual (ARS)")
    p.add_argument("--burn-in", type=int, default=120)
    p.add_argument("--oos", type=int, default=60)
    p.add_argument("--step", type=int, default=30)
    p.add_argument("--no-long", action="store_true", help="Solo sleeve corto")
    p.add_argument("--out-json", type=Path, default=SIM_DIR / "wf_research_report.json")
    args = p.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    if not args.db.exists():
        print(json.dumps({"error": "db_not_found", "db": str(args.db)}))
        return 2

    policy_doc = yaml.safe_load(args.policy.open(encoding="utf-8"))
    sim_path = _prepare_sim_db(args.db)
    db = MarketDB(str(sim_path))
    calendar_store = load_required_calendar_store(policy_doc)

    days = _trading_days(db, args.start, args.end)
    if not days:
        print(json.dumps({"error": "no_trading_days", "start": args.start.isoformat()}))
        return 1

    series = run_research_sim(
        db, days, policy_doc,
        contribution=args.contrib, calendar_store=calendar_store,
        enable_long=not args.no_long,
    )

    returns = time_weighted_daily_returns(series)
    report = evaluate_walk_forward(series, burn_in=args.burn_in, oos=args.oos, step=args.step)

    total_contrib = sum(pt.contribution for pt in series)
    final_equity = series[-1].equity if series else 0.0
    cashflows = [(pt.day, -pt.contribution) for pt in series if pt.contribution > 0]
    mwr = money_weighted_return(cashflows, final_equity, series[-1].day) if series else None

    # Calmar agregado (criterio pre-registrado, Fase 3): annualized_twr / |max_drawdown|
    # sobre la serie diaria continua. max_drawdown se mide sobre el índice TWR (excluye
    # los aportes). Mismo cálculo para las 3 carteras → comparación A/B/C reproducible.
    ann_twr = annualized_twr(returns)
    mdd = max_drawdown(returns)
    calmar = (ann_twr / abs(mdd)) if mdd < 0 else None
    sharpe = annualized_sharpe(returns)

    summary = {
        "mode": "research",
        "window_config": {"burn_in": args.burn_in, "oos": args.oos, "step": args.step},
        "monthly_contribution": args.contrib,
        "period": {"start": series[0].day.isoformat(), "end": series[-1].day.isoformat()} if series else {},
        "days_simulated": len(series),
        "total_contributed": total_contrib,
        "final_equity": final_equity,
        "twr_cumulative_pct": cumulative_twr(returns) * 100.0,
        "annualized_twr_pct": ann_twr * 100.0,
        "max_drawdown_pct": mdd * 100.0,
        "calmar": calmar,
        "sharpe_annualized": sharpe,
        "mwr_annualized_pct": (mwr * 100.0) if mwr is not None else None,
        "walk_forward": report,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    _print_report(summary)
    return 0


def _print_report(s: dict[str, Any]) -> None:
    wf = s["walk_forward"]
    line = "=" * 72
    print(f"\n{line}")
    print("  SIMULADOR WALK-FORWARD - MODO INVESTIGACION (no es el gate de produccion)")
    print(line)
    per = s.get("period", {})
    print(f"  Periodo          : {per.get('start','?')} -> {per.get('end','?')}  ({s['days_simulated']} dias)")
    print(f"  Aporte mensual   : {s['monthly_contribution']:,.0f} ARS")
    print(f"  Total aportado   : {s['total_contributed']:,.0f} ARS")
    print(f"  Equity final     : {s['final_equity']:,.0f} ARS")
    print(f"  TWR acumulado    : {s['twr_cumulative_pct']:+.2f}%  (rendimiento de la estrategia, sin aportes)")
    cal = s.get("calmar")
    print(f"  TWR anualizado   : {s.get('annualized_twr_pct', 0.0):+.2f}%   MaxDD: {s.get('max_drawdown_pct', 0.0):.2f}%")
    print(f"  Calmar (criterio): {cal:.3f}" if cal is not None else "  Calmar (criterio): n/d (sin drawdown)")
    shp = s.get("sharpe_annualized")
    print(f"  Sharpe anualizado: {shp:.3f}" if shp is not None else "  Sharpe anualizado: n/d")
    mwr = s.get("mwr_annualized_pct")
    print(f"  TIR (MWR) anual  : {mwr:+.2f}%" if mwr is not None else "  TIR (MWR) anual  : n/d")
    print(f"\n  Walk-forward {wf['config']['burn_in']}+{wf['config']['oos']} paso {wf['config']['step']}: {wf['num_windows']} ventanas OOS")
    if wf["insufficient_data"]:
        print("  [!] Datos insuficientes para formar una ventana (necesita burn_in+oos dias).")
    else:
        print(f"  {'#':>2} {'OOS desde':12} {'OOS hasta':12} {'TWR':>8} {'Sharpe':>8} {'MaxDD':>8} {'pasa':>5}")
        for w in wf["windows"]:
            sh = f"{w['sharpe_annualized']:.2f}" if w["sharpe_annualized"] is not None else "n/d"
            print(f"  {w['index']:>2} {w['oos_start']:12} {w['oos_end']:12} "
                  f"{w['twr_cumulative']*100:>7.1f}% {sh:>8} {w['max_drawdown']*100:>7.1f}% {('SI' if w['passed'] else 'no'):>5}")
        print(f"\n  Agregado (todas las ventanas pasan): {'SI' if wf['aggregate_passed'] else 'NO'}")
    print(line)
    print("  Recordatorio: modo investigacion. El gate real (252+60, congelado) decide capital real.\n")


if __name__ == "__main__":
    raise SystemExit(main())
