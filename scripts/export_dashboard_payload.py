#!/usr/bin/env python3
"""Export the paper-live dashboard JSON payload for static hosting (F1-01).

**Qué hace (simple):** lee ``market.db`` y guarda una *foto* JSON de todo lo que verías
en el monitor web — equity, posiciones, riesgo, KPIs — sin levantar servidor.

Misma forma que ``GET /api/dashboard`` vía :class:`dashboard.service.DashboardService`.
Documentación completa: ``docs/dashboard.md``.

Contrato JSON: ``export_version`` ``"1"`` — mismas keys que ``GET /api/dashboard``.
Consumido por ``web/`` (F1-04) y commiteado en ``paper-live-data`` (F1-03 / ADR-065).

Ejemplos::

    python scripts/export_dashboard_payload.py
    python scripts/export_dashboard_payload.py --out data/dashboard_payload.json --pretty
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dashboard.service import DashboardConfig, DashboardService  # noqa: E402

EXPORT_VERSION = "1"
_REQUIRED_TOP_KEYS = (
    "meta",
    "data_freshness",
    "equity_curve",
    "positions",
    "recent_fills",
    "risk",
    "kpis",
    "alerts",
    "generated_at",
    "export_version",
)


def _drop_local_db_freshness(payload: dict[str, Any]) -> None:
    """Neutralize the local-checkout DB-freshness signal for published payloads.

    The ``stale_local_db`` alert (and ``data_freshness`` status) compares the
    working-tree ``market.db`` against ``origin/paper-live-data`` — a *local-dev*
    affordance telling YOU to sync your checkout. Baked into the artifact that
    Vercel serves it's nonsensical: CI exports the payload *before* committing the
    fresh ``market.db``, so it always fires as ``stale_remote`` with
    ``commits_behind: 0``. Strip it so the published dashboard isn't permanently
    flagged. The locally-served dashboard keeps the check (flag defaults off).
    """
    payload["alerts"] = [
        a for a in payload.get("alerts", []) if a.get("code") != "stale_local_db"
    ]
    fresh = payload.get("data_freshness") or {}
    payload["data_freshness"] = {
        "status": "ok",
        "message": "Snapshot publicado por CI; el chequeo de DB local no aplica.",
        "commits_behind": 0,
        "worktree_dirty": False,
        "remote_ref": fresh.get("remote_ref", "origin/paper-live-data"),
        "sync_hint": fresh.get("sync_hint", ""),
    }


def export_dashboard_payload(
    *,
    db_path: Path,
    policy_path: Path,
    calendar_path: Path,
    mode: str = "paper_live",
    include_db_freshness: bool = True,
) -> dict[str, Any]:
    """Build the dashboard payload dict (read-only over MarketDB)."""
    if not db_path.is_file():
        raise FileNotFoundError(f"Database not found: {db_path}")

    cfg = DashboardConfig(
        db_path=db_path,
        policy_path=policy_path,
        calendar_path=calendar_path,
        mode=mode,
    )
    payload = DashboardService(cfg).build_payload()
    if not include_db_freshness:
        _drop_local_db_freshness(payload)
    payload["export_version"] = EXPORT_VERSION
    payload["export_source"] = {
        "db_path": str(db_path),
        "policy_path": str(policy_path),
        "calendar_path": str(calendar_path),
        "mode": mode,
    }
    return payload


def write_dashboard_payload(
    payload: dict[str, Any],
    out_path: Path,
    *,
    pretty: bool = False,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2 if pretty else None, ensure_ascii=False)
    text += "\n"
    out_path.write_text(text, encoding="utf-8")


def validate_payload_shape(payload: dict[str, Any]) -> None:
    missing = [k for k in _REQUIRED_TOP_KEYS if k not in payload]
    if missing:
        raise ValueError(f"Payload missing required keys: {', '.join(missing)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export paper-live dashboard JSON (same contract as /api/dashboard).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=REPO_ROOT / "data" / "market.db",
        help="Path to market.db (default: data/market.db)",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=REPO_ROOT / "config" / "policy.v1.yaml",
    )
    parser.add_argument(
        "--calendar",
        type=Path,
        default=REPO_ROOT / "config" / "calendars" / "trading_days.v1.yaml",
    )
    parser.add_argument("--mode", default="paper_live")
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "data" / "dashboard_payload.json",
        help="Output JSON path (default: data/dashboard_payload.json for paper-live-data commits)",
    )
    parser.add_argument("--pretty", action="store_true", help="Indent JSON for human diff")
    parser.add_argument(
        "--no-db-freshness",
        action="store_true",
        help=(
            "Drop the local-checkout DB-freshness signal (stale_local_db alert). "
            "Use for the published/CI payload served by Vercel — the check only "
            "makes sense when running the dashboard against a local checkout."
        ),
    )
    args = parser.parse_args(argv)

    try:
        payload = export_dashboard_payload(
            db_path=args.db,
            policy_path=args.policy,
            calendar_path=args.calendar,
            mode=args.mode,
            include_db_freshness=not args.no_db_freshness,
        )
        validate_payload_shape(payload)
        write_dashboard_payload(payload, args.out, pretty=args.pretty)
    except FileNotFoundError as exc:
        print(f"export_dashboard_payload: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"export_dashboard_payload: failed: {exc}", file=sys.stderr)
        return 2

    n_curve = len(payload.get("equity_curve") or [])
    last_day = (payload.get("meta") or {}).get("last_trading_day")
    print(
        f"Wrote {args.out} — export_version={EXPORT_VERSION}, "
        f"snapshots={n_curve}, last_day={last_day or '—'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
