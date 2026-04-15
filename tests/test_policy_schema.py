"""Valida config/policy.v1.yaml contra config/policy.v1.schema.json (CI)."""

from pathlib import Path

import json
import yaml
import pytest
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_YAML = REPO_ROOT / "config" / "policy.v1.yaml"
POLICY_SCHEMA = REPO_ROOT / "config" / "policy.v1.schema.json"


@pytest.fixture(scope="module")
def policy_doc():
    with POLICY_YAML.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def schema():
    with POLICY_SCHEMA.open(encoding="utf-8") as f:
        return json.load(f)


def test_policy_conforms_to_json_schema(policy_doc, schema):
    Draft202012Validator(schema).validate(policy_doc)


def test_weights_sum_to_one(policy_doc):
    w = policy_doc["weights"]
    assert abs(w["short"] + w["long"] - 1.0) < 1e-6


def test_geo_sum_to_one(policy_doc):
    g = policy_doc["geo"]
    assert abs(g["AR"] + g["US"] - 1.0) < 1e-6
