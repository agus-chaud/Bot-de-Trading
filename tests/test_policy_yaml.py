"""Tests de parsing y validación mínima del contrato policy.v1.yaml (Fase 1)."""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_FILE = REPO_ROOT / "config" / "policy.v1.yaml"


def _load_policy():
    with POLICY_FILE.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_policy_yaml_loads():
    cfg = _load_policy()
    assert cfg["schema_version"] == 1
    assert cfg["profile"] == "moderate"


def test_weights_and_geo_sum_to_one():
    cfg = _load_policy()
    w = cfg["weights"]
    assert abs(w["short"] + w["long"] - 1.0) < 1e-6
    g = cfg["geo"]
    assert abs(g["AR"] + g["US"] - 1.0) < 1e-6


def test_short_kill_switch_is_negative_fraction():
    cfg = _load_policy()
    ks = cfg["short_kill_switch_monthly_dd"]
    assert ks <= 0
    assert ks == pytest.approx(-0.08)


def test_short_term_pre_gate_block_present():
    cfg = _load_policy()
    pg = cfg.get("short_term_pre_gate")
    assert isinstance(pg, dict)
    assert "walk_forward" in pg and "thresholds" in pg
    assert int(pg["walk_forward"]["min_oos_windows"]) >= 1


def test_execution_mode_allowed():
    cfg = _load_policy()
    assert cfg["execution_mode"] in ("semi_auto", "auto")


def test_whitelist_files_exist_relative_to_repo():
    cfg = _load_policy()
    us_path = REPO_ROOT / cfg["symbols"]["whitelist_us_file"]
    ar_path = REPO_ROOT / cfg["symbols"]["whitelist_ar_file"]
    assert us_path.is_file()
    assert ar_path.is_file()
    with us_path.open(encoding="utf-8") as f:
        us = yaml.safe_load(f)
    assert set(us["etfs"]) >= {"SPY", "QQQ", "IWM"}
    assert isinstance(us["stocks"], list) and len(us["stocks"]) >= 1


def test_calendar_and_corporate_actions_files_exist_relative_to_repo():
    cfg = _load_policy()
    calendar_path = REPO_ROOT / cfg["calendar"]["source_of_truth"]
    actions_path = REPO_ROOT / cfg["corporate_actions"]["us_file"]

    assert calendar_path.is_file()
    assert actions_path.is_file()

    with calendar_path.open(encoding="utf-8") as f:
        calendar_doc = yaml.safe_load(f)
    assert isinstance(calendar_doc["us"]["sessions"], list) and len(calendar_doc["us"]["sessions"]) >= 1
    assert isinstance(calendar_doc["ar"]["business_days"], list) and len(calendar_doc["ar"]["business_days"]) >= 1

    with actions_path.open(encoding="utf-8") as f:
        actions_doc = yaml.safe_load(f)
    assert set(actions_doc["supported_types"]) == {"split", "dividend"}
