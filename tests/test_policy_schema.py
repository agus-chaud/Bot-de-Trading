"""Valida config/policy.v1.yaml contra config/policy.v1.schema.json (CI)."""

from pathlib import Path
import copy
import json
import yaml
import pytest
from jsonschema import Draft202012Validator, ValidationError

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


class TestValidationWfSchema:
    """T0.3 — Tests de validación del bloque validation_wf."""

    def test_lookback_trading_days_90_passes(self, policy_doc, schema):
        """lookback_trading_days: 90 debe pasar la validación."""
        doc = copy.deepcopy(policy_doc)
        doc["validation_wf"] = {"lookback_trading_days": 90}
        Draft202012Validator(schema).validate(doc)

    def test_lookback_trading_days_below_minimum_fails(self, policy_doc, schema):
        """lookback_trading_days: 5 debe fallar (minimum es 20)."""
        doc = copy.deepcopy(policy_doc)
        doc["validation_wf"] = {"lookback_trading_days": 5}
        with pytest.raises(ValidationError):
            Draft202012Validator(schema).validate(doc)

    def test_missing_validation_wf_fails(self, policy_doc, schema):
        """Omitir validation_wf debe fallar porque es requerido."""
        doc = copy.deepcopy(policy_doc)
        doc.pop("validation_wf", None)
        with pytest.raises(ValidationError):
            Draft202012Validator(schema).validate(doc)
