"""Aggregate paper-live data for the monitoring dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from core_sim.calendar_store import TradingCalendarStore
from data.storage import MarketDB
from dashboard.db_freshness import check_db_freshness
from dashboard.risk_matrix import build_risk_matrix
from dashboard.trade_thesis import build_position_theses
from reporting.kpi_v0 import build_kpi_v0_report_from_tables

_VENUE_MAP: dict[str, str] = {"US": "XNYS", "AR": "XBUE", "XNYS": "XNYS", "XBUE": "XBUE"}

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DB = _REPO_ROOT / "data" / "market.db"
_DEFAULT_POLICY = _REPO_ROOT / "config" / "policy.v1.yaml"
_DEFAULT_CALENDAR = _REPO_ROOT / "config" / "calendars" / "trading_days.v1.yaml"


@dataclass(frozen=True)
class DashboardConfig:
    db_path: Path = _DEFAULT_DB
    policy_path: Path = _DEFAULT_POLICY
    calendar_path: Path = _DEFAULT_CALENDAR
    mode: str = "paper_live"


class DashboardService:
    """Read-only aggregator over MarketDB + KPI pipeline."""

    def __init__(self, config: DashboardConfig | None = None) -> None:
        self.config = config or DashboardConfig()
        self._policy: dict[str, Any] | None = None
        self._calendar: TradingCalendarStore | None = None

    def _db(self) -> MarketDB:
        return MarketDB(str(self.config.db_path))

    def _load_policy(self) -> dict[str, Any]:
        if self._policy is None:
            with self.config.policy_path.open(encoding="utf-8") as handle:
                self._policy = yaml.safe_load(handle) or {}
        return self._policy

    def _load_calendar(self) -> TradingCalendarStore | None:
        if self._calendar is not None:
            return self._calendar
        if self.config.calendar_path.is_file():
            self._calendar = TradingCalendarStore.from_yaml(self.config.calendar_path)
            return self._calendar
        if self.config.db_path.is_file():
            try:
                self._calendar = TradingCalendarStore.from_db(str(self.config.db_path))
                return self._calendar
            except Exception:
                return None
        return None

    def build_payload(self) -> dict[str, Any]:
        db = self._db()
        mode = self.config.mode
        policy = self._load_policy()
        meta = db.get_portfolio_meta(mode)
        snapshots = db.get_paper_snapshots(mode)
        last_day = db.get_last_snapshot_day(mode)
        ks = db.get_kill_switch_state("short")

        alerts = self._alerts(db, snapshots, last_day, ks, policy)
        freshness = self._data_freshness_block()
        if freshness["status"] != "ok":
            alerts.insert(
                0,
                {
                    "severity": "critical",
                    "code": "stale_local_db",
                    "title": "DB local desactualizada",
                    "detail": (
                        f"{freshness['message']} "
                        f"Sincronizá con: {freshness['sync_hint']} "
                        "o arrancá con: python scripts/run_dashboard.py --sync-db"
                    ),
                },
            )

        positions = self._positions(db, meta, last_day)
        context = self._position_market_context(db, positions, last_day, policy)
        latest = snapshots[-1] if snapshots else None
        max_lag = max((c["lag_days"] for c in context.values()), default=0)
        ks_floor = float(policy.get("short_kill_switch_monthly_dd", -0.08))

        return {
            "meta": self._meta_block(meta, last_day, snapshots),
            "data_freshness": freshness,
            "equity_curve": self._equity_curve(snapshots),
            "positions": positions,
            "recent_fills": self._recent_fills(db, limit=25),
            "risk": self._risk_block(snapshots, ks, policy),
            "risk_matrix": build_risk_matrix(
                latest_snapshot=latest,
                positions=positions,
                max_data_lag_days=max_lag,
                fetch_issue_count=len(db.get_recent_fetch_errors()),
                ks_active=bool(ks.active),
                ks_floor=ks_floor,
            ),
            "position_theses": build_position_theses(positions, context),
            "kpis": self._kpis(snapshots, db),
            "alerts": alerts,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

    def _position_market_context(
        self,
        db: MarketDB,
        positions: list[dict[str, Any]],
        last_day: date | None,
        policy: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """Per-symbol closes (for technicals) + lag de datos (para staleness)."""
        ctx: dict[str, dict[str, Any]] = {}
        if last_day is None:
            return ctx
        lookback = int(
            (policy.get("short_term_engine") or {}).get("momentum_lookback_days", 20)
        )
        start = last_day - timedelta(days=lookback * 2 + 15)
        for pos in positions:
            symbol = str(pos["symbol"])
            venue = _VENUE_MAP.get(str(pos.get("market")).upper(), "XNYS")
            rows = db.get_ohlcv(symbol, start, last_day, venue)
            closes = [float(r.close) for r in rows][-lookback:]
            last_ts = rows[-1].ts if rows else None
            lag_days = (last_day - last_ts).days if last_ts else 999
            ctx[symbol] = {"closes": closes, "lag_days": lag_days}
        return ctx

    def _data_freshness_block(self) -> dict[str, Any]:
        report = check_db_freshness(self.config.db_path, fetch=False)
        return {
            "status": report.status,
            "message": report.message,
            "commits_behind": report.commits_behind,
            "worktree_dirty": report.worktree_dirty,
            "remote_ref": report.remote_ref,
            "sync_hint": report.sync_hint,
        }

    def _meta_block(
        self,
        meta: Any,
        last_day: date | None,
        snapshots: list[dict[str, Any]],
    ) -> dict[str, Any]:
        latest = snapshots[-1] if snapshots else None
        return {
            "mode": self.config.mode,
            "currency": meta.currency if meta else "ARS",
            "starting_cash": meta.starting_cash if meta else None,
            "inception_date": meta.inception_date.isoformat() if meta else None,
            "last_trading_day": last_day.isoformat() if last_day else None,
            "equity_total": latest["equity_total"] if latest else None,
            "num_open_positions": latest["num_open_positions"] if latest else 0,
        }

    def _equity_curve(self, snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "date": row["trading_day"],
                "equity_total": row["equity_total"],
                "equity_short": row["equity_short"],
                "equity_long": row["equity_long"],
                "cash": row["cash"],
                "mv_us": row["mv_us"],
                "mv_ar": row["mv_ar"],
            }
            for row in snapshots
        ]

    def _recent_fills(self, db: MarketDB, *, limit: int) -> list[dict[str, Any]]:
        rows = db.get_paper_fills(self.config.mode)
        tail = rows[-limit:] if len(rows) > limit else rows
        tail = list(reversed(tail))
        return [
            {
                "trading_day": r["trading_day"],
                "ts_fill": r["ts_fill"],
                "symbol": r["symbol"],
                "side": r["side"],
                "qty": r["qty"],
                "price": r["price"],
                "bucket": r["bucket"],
                "engine": r["engine"],
                "reason": r["reason"],
                "fee": r["fee"],
                "cost_total": r["cost_total"],
            }
            for r in tail
        ]

    def _positions(
        self,
        db: MarketDB,
        meta: Any,
        last_day: date | None,
    ) -> list[dict[str, Any]]:
        if last_day is None or meta is None:
            return []
        ledger = db.replay_ledger_from_fills(
            mode=self.config.mode,
            starting_cash=meta.starting_cash,
        )
        if not ledger.positions:
            return []

        daily_bars: dict[str, dict[str, float]] = {}
        for symbol, pos in ledger.positions.items():
            venue = _VENUE_MAP.get(pos.market.upper(), "XNYS")
            rows = db.get_ohlcv(symbol, last_day, last_day, venue)
            if rows:
                bar = rows[0]
                daily_bars[symbol] = {
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                }

        snap = ledger.mark_to_market(trading_day=last_day, daily_bars=daily_bars)
        positions_raw = snap.get("positions") or {}
        out: list[dict[str, Any]] = []
        for symbol, p in positions_raw.items():
            out.append(
                {
                    "symbol": symbol,
                    "qty": p["qty"],
                    "avg_cost": p["avg_cost"],
                    "market": p["market"],
                    "bucket": p["bucket"],
                    "market_value": p["market_value"],
                    "unrealized_pnl": p["unrealized_pnl"],
                    "stale": bool(p.get("stale")),
                }
            )
        out.sort(key=lambda x: abs(float(x["market_value"])), reverse=True)
        return out

    def _risk_block(
        self,
        snapshots: list[dict[str, Any]],
        ks: Any,
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        latest = snapshots[-1] if snapshots else None
        risk_cfg = policy.get("risk") or {}
        ks_floor = float(policy.get("short_kill_switch_monthly_dd", -0.08))
        daily_short_floor = float(risk_cfg.get("max_daily_loss_short_pct", -0.02))

        factors: list[dict[str, str]] = []
        trading_allowed = True

        if ks.active:
            trading_allowed = False
            factors.append(
                {
                    "level": "critical",
                    "code": "kill_switch",
                    "message": (
                        f"Kill switch activo desde {ks.activated_at} "
                        f"(DD mensual corto {ks.monthly_dd:.2%})"
                        if ks.activated_at and ks.monthly_dd is not None
                        else "Kill switch activo — motor corto congelado"
                    ),
                }
            )

        if latest:
            if int(latest.get("kill_switch_active") or 0):
                trading_allowed = False
                if not any(f["code"] == "kill_switch" for f in factors):
                    factors.append(
                        {
                            "level": "critical",
                            "code": "kill_switch_snapshot",
                            "message": "Snapshot EOD marca kill_switch_active=1",
                        }
                    )

            dd = latest.get("short_monthly_drawdown")
            if dd is not None and float(dd) <= ks_floor:
                trading_allowed = False
                factors.append(
                    {
                        "level": "critical",
                        "code": "monthly_dd_floor",
                        "message": (
                            f"Drawdown mensual corto {float(dd):.2%} "
                            f"≤ umbral {ks_floor:.2%}"
                        ),
                    }
                )
            elif dd is not None and float(dd) <= ks_floor * 0.75:
                factors.append(
                    {
                        "level": "warning",
                        "code": "monthly_dd_warning",
                        "message": (
                            f"Drawdown mensual corto {float(dd):.2%} "
                            f"cerca del kill switch ({ks_floor:.2%})"
                        ),
                    }
                )

            daily_ret = latest.get("short_daily_return")
            if daily_ret is not None and float(daily_ret) <= daily_short_floor:
                factors.append(
                    {
                        "level": "warning",
                        "code": "daily_loss_short",
                        "message": (
                            f"Pérdida diaria bucket corto {float(daily_ret):.2%} "
                            f"≤ límite {daily_short_floor:.2%}"
                        ),
                    }
                )

            if int(latest.get("num_fills_today") or 0) == 0:
                factors.append(
                    {
                        "level": "info",
                        "code": "no_fills_today",
                        "message": (
                            f"Sin fills el {latest['trading_day']} "
                            "(guardrails, sin señal, o semi_auto pendiente)"
                        ),
                    }
                )

        if not factors:
            factors.append(
                {
                    "level": "ok",
                    "code": "nominal",
                    "message": "Sin bloqueos de riesgo detectados en el último snapshot",
                }
            )

        return {
            "trading_allowed": trading_allowed,
            "kill_switch": {
                "active": ks.active,
                "activated_at": ks.activated_at.isoformat() if ks.activated_at else None,
                "monthly_dd": ks.monthly_dd,
            },
            "thresholds": {
                "short_kill_switch_monthly_dd": ks_floor,
                "max_daily_loss_short_pct": daily_short_floor,
            },
            "latest_snapshot": (
                {
                    "trading_day": latest["trading_day"],
                    "short_monthly_drawdown": latest.get("short_monthly_drawdown"),
                    "short_daily_return": latest.get("short_daily_return"),
                    "num_fills_today": latest.get("num_fills_today"),
                    "kill_switch_active": bool(latest.get("kill_switch_active")),
                }
                if latest
                else None
            ),
            "factors": factors,
        }

    def _kpis(self, snapshots: list[dict[str, Any]], db: MarketDB) -> dict[str, Any]:
        if len(snapshots) < 2:
            return {
                "status": "insufficient_history",
                "n_days": len(snapshots),
                "sharpe_annualized": None,
                "calmar": None,
                "max_drawdown": None,
                "net_return_annualized": None,
            }

        equity_rows: list[dict[str, str]] = []
        for row in snapshots:
            equity_rows.append(
                {
                    "ts": str(row["trading_day"]),
                    "equity_total": str(row["equity_total"]),
                    "equity_short": str(row["equity_short"]),
                    "equity_long": str(row["equity_long"]),
                    "cash": str(row["cash"]),
                    "costs_day": str(row["costs_day"]),
                }
            )
        fieldnames = list(equity_rows[0].keys())

        trade_rows: list[dict[str, str]] = []
        for fill in db.get_paper_fills(self.config.mode):
            trade_rows.append(
                {
                    "ts": str(fill["trading_day"]),
                    "symbol": str(fill["symbol"]),
                    "side": str(fill["side"]),
                    "qty": str(fill["qty"]),
                    "price": str(fill["price"]),
                    "motor": str(fill["bucket"]),
                    "fee": str(fill["fee"]),
                    "slippage": str(fill["slippage"]),
                }
            )

        report = build_kpi_v0_report_from_tables(
            equity_rows,
            fieldnames,
            trade_rows if trade_rows else None,
            policy_path=self.config.policy_path,
        )
        total = report.segment_total
        long_seg = report.segment_long

        ann = total.get("net_return_annualized")
        mdd = total.get("max_drawdown")
        calmar: float | None = None
        if ann is not None and mdd is not None and float(mdd) < 0:
            calmar = float(ann) / abs(float(mdd))

        return {
            "status": "ok",
            "n_days": total.get("n_trading_days"),
            "sharpe_annualized": total.get("sharpe_annualized"),
            "sharpe_na_reason": total.get("sharpe_na_reason"),
            "sortino_annualized": total.get("sortino_annualized"),
            "max_drawdown": mdd,
            "net_return_annualized": ann,
            "calmar_total": calmar,
            "calmar_12m_long": long_seg.get("calmar_12m_last"),
            "calmar_12m_na_reason": long_seg.get("calmar_12m_na_reason"),
            "hit_rate": total.get("hit_rate"),
            "profit_factor": total.get("profit_factor"),
            "ts_start": report.ts_start,
            "ts_end": report.ts_end,
        }

    def _alerts(
        self,
        db: MarketDB,
        snapshots: list[dict[str, Any]],
        last_day: date | None,
        ks: Any,
        policy: dict[str, Any],
    ) -> list[dict[str, str]]:
        alerts: list[dict[str, str]] = []
        today = date.today()

        if not snapshots:
            alerts.append(
                {
                    "severity": "critical",
                    "code": "no_snapshots",
                    "title": "Sin snapshots paper-live",
                    "detail": "No hay curva de equity en la base. ¿Corrió run_paper_live?",
                }
            )
            return alerts

        cal = self._load_calendar()
        if cal and last_day:
            expected = self._expected_trading_days_since(last_day, today, cal)
            if expected:
                alerts.append(
                    {
                        "severity": "warning",
                        "code": "missing_run_days",
                        "title": "Días de mercado sin corrida",
                        "detail": (
                            f"Último snapshot {last_day.isoformat()}; "
                            f"sin procesar: {', '.join(d.isoformat() for d in expected[:5])}"
                            + (f" (+{len(expected) - 5} más)" if len(expected) > 5 else "")
                        ),
                    }
                )

        latest = snapshots[-1]
        if int(latest.get("kill_switch_active") or 0) or ks.active:
            alerts.append(
                {
                    "severity": "critical",
                    "code": "kill_switch",
                    "title": "Kill switch activo",
                    "detail": "El bucket corto está congelado por drawdown mensual.",
                }
            )

        ks_floor = float(policy.get("short_kill_switch_monthly_dd", -0.08))
        dd = latest.get("short_monthly_drawdown")
        if dd is not None and float(dd) <= ks_floor * 0.85 and not ks.active:
            alerts.append(
                {
                    "severity": "warning",
                    "code": "drawdown_near_ks",
                    "title": "Drawdown cerca del kill switch",
                    "detail": f"DD mensual corto {float(dd):.2%} (umbral {ks_floor:.2%}).",
                }
            )

        if int(latest.get("num_fills_today") or 0) == 0 and int(latest.get("num_open_positions") or 0) > 0:
            alerts.append(
                {
                    "severity": "info",
                    "code": "idle_with_positions",
                    "title": "Día sin operaciones con posiciones abiertas",
                    "detail": (
                        f"{latest['trading_day']}: 0 fills pero "
                        f"{latest['num_open_positions']} posiciones abiertas."
                    ),
                }
            )

        fetch_issues = self._recent_fetch_issues(db)
        if fetch_issues:
            alerts.append(
                {
                    "severity": "warning",
                    "code": "fetch_errors",
                    "title": "Errores recientes de ingesta OHLCV",
                    "detail": fetch_issues,
                }
            )

        gap_days = self._equity_gap_days(snapshots)
        for gap in gap_days:
            alerts.append(
                {
                    "severity": "warning",
                    "code": "equity_gap",
                    "title": "Hueco en curva de equity",
                    "detail": f"Sin snapshot entre {gap[0]} y {gap[1]}.",
                }
            )

        if not alerts:
            alerts.append(
                {
                    "severity": "ok",
                    "code": "healthy",
                    "title": "Operación nominal",
                    "detail": "No se detectaron anomalías en los chequeos automáticos.",
                }
            )
        return alerts

    def _expected_trading_days_since(
        self,
        after: date,
        until: date,
        cal: TradingCalendarStore,
    ) -> list[date]:
        """Trading days in (after, until] that union US sessions and AR business days."""
        out: list[date] = []
        d = after + timedelta(days=1)
        while d <= until:
            if cal.is_us_session(d) or cal.is_ar_business_day(d):
                out.append(d)
            d += timedelta(days=1)
        return out

    def _equity_gap_days(self, snapshots: list[dict[str, Any]]) -> list[tuple[str, str]]:
        gaps: list[tuple[str, str]] = []
        cal = self._load_calendar()
        for prev, curr in zip(snapshots, snapshots[1:]):
            d0 = date.fromisoformat(prev["trading_day"])
            d1 = date.fromisoformat(curr["trading_day"])
            if (d1 - d0).days <= 1:
                continue
            if cal:
                missing = [
                    d
                    for d in self._expected_trading_days_since(d0, d1 - timedelta(days=1), cal)
                ]
                if missing:
                    gaps.append((prev["trading_day"], curr["trading_day"]))
            else:
                gaps.append((prev["trading_day"], curr["trading_day"]))
        return gaps

    def _recent_fetch_issues(self, db: MarketDB, limit: int = 8) -> str | None:
        rows = db.get_recent_fetch_errors(limit=limit)
        if not rows:
            return None
        parts = [
            f"{r['symbol']}@{r['venue']} ({r['status']}: {r['skip_reason'] or '—'})"
            for r in rows[:4]
        ]
        return "; ".join(parts)
