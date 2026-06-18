"""FastAPI server for the paper-live monitoring dashboard."""

from __future__ import annotations

import argparse
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from dashboard.service import DashboardConfig, DashboardService

_STATIC = Path(__file__).resolve().parent / "static"

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper-live monitoring dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=None, help="Path to market.db")
    parser.add_argument("--policy", type=Path, default=None)
    parser.add_argument("--calendar", type=Path, default=None)
    parser.add_argument("--mode", default="paper_live")
    args = parser.parse_args()

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
