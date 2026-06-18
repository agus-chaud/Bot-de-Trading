"""Smoke tests for web/ Next.js dashboard (F1-04)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = REPO_ROOT / "web"
FIXTURE = WEB_ROOT / "fixtures" / "dashboard_payload.json"
REQUIRED_KEYS = (
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


def test_web_fixture_should_match_export_contract():
    assert FIXTURE.is_file(), "web/fixtures/dashboard_payload.json missing"
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED_KEYS if k not in payload]
    assert not missing, f"fixture missing keys: {missing}"
    assert payload["export_version"] == "1"
    assert len(payload["equity_curve"]) >= 1


def test_web_package_should_define_build_scripts():
    pkg = json.loads((WEB_ROOT / "package.json").read_text(encoding="utf-8"))
    scripts = pkg.get("scripts", {})
    assert "build" in scripts
    assert "prebuild" in scripts
    assert "copy-payload" in scripts["prebuild"] or "copy-payload.mjs" in scripts["prebuild"]


@pytest.mark.parametrize(
    "rel_path",
    [
        "app/page.tsx",
        "components/DashboardView.tsx",
        "lib/dashboard-client.ts",
        "scripts/copy-payload.mjs",
        "vercel.json",
    ],
)
def test_web_should_have_f1_04_scaffold(rel_path: str):
    assert (WEB_ROOT / rel_path).is_file(), f"missing web/{rel_path}"


def test_web_vercel_json_should_target_nextjs():
    cfg = json.loads((WEB_ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert cfg.get("framework") == "nextjs"
    assert "npm run build" in cfg.get("buildCommand", "")
    assert "paper-live-data" in cfg.get("ignoreCommand", "")
