"""FastAPI server for the paper-live monitoring dashboard."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from dashboard.db_freshness import check_db_freshness, sync_db_from_remote
from dashboard.service import DashboardConfig, DashboardService

_STATIC = Path(__file__).resolve().parent / "static"
_REPO_ROOT = Path(__file__).resolve().parents[1]

app = FastAPI(title="Bot de Trading — Monitor", version="0.1.0")
_service: DashboardService | None = None


@app.on_event("startup")
def _startup() -> None:
    if _service is None:
        configure()


def _get_service() -> DashboardService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Dashboard not configured")
    return _service


@app.get("/api/dashboard")
def api_dashboard() -> dict:
    try:
        return _get_service().build_payload()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/health")
def api_health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


app.mount("/static", StaticFiles(directory=_STATIC), name="static")


def configure(
    *,
    db_path: Path | None = None,
    policy_path: Path | None = None,
    calendar_path: Path | None = None,
    mode: str = "paper_live",
) -> None:
    global _service
    cfg = DashboardConfig()
    if db_path is not None:
        cfg = DashboardConfig(
            db_path=db_path,
            policy_path=policy_path or cfg.policy_path,
            calendar_path=calendar_path or cfg.calendar_path,
            mode=mode,
        )
    _service = DashboardService(cfg)


def _warn_if_stale(db_path: Path, *, fetch: bool) -> None:
    report = check_db_freshness(db_path, fetch=fetch)
    if report.status == "ok":
        print(f"[dashboard] DB OK — alineada con {report.remote_ref}")
        return
    print(f"[dashboard] AVISO: {report.message}", file=sys.stderr)
    print(f"[dashboard] Sync: {report.sync_hint}", file=sys.stderr)
    print("[dashboard] O: python scripts/run_dashboard.py --sync-db", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper-live monitoring dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=None, help="Path to market.db")
    parser.add_argument("--policy", type=Path, default=None)
    parser.add_argument("--calendar", type=Path, default=None)
    parser.add_argument("--mode", default="paper_live")
    parser.add_argument(
        "--fetch-remote",
        action="store_true",
        help="git fetch origin paper-live-data antes de chequear frescura",
    )
    parser.add_argument(
        "--sync-db",
        action="store_true",
        help="Traer data/market.db desde origin/paper-live-data (Git LFS) y salir si falla",
    )
    parser.add_argument(
        "--skip-freshness-check",
        action="store_true",
        help="No avisar en consola si la DB local está desactualizada",
    )
    args = parser.parse_args()

    db_path = args.db or (_REPO_ROOT / "data" / "market.db")

    if args.sync_db:
        try:
            sync_db_from_remote(_REPO_ROOT, db_rel=str(db_path.relative_to(_REPO_ROOT)).replace("\\", "/"))
            print("[dashboard] market.db sincronizada desde origin/paper-live-data")
        except Exception as exc:
            print(f"[dashboard] sync falló: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc

    if not args.skip_freshness_check:
        _warn_if_stale(db_path, fetch=args.fetch_remote or args.sync_db)

    configure(
        db_path=args.db,
        policy_path=args.policy,
        calendar_path=args.calendar,
        mode=args.mode,
    )

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
