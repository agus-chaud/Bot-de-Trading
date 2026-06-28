# Dataclasses para el reporte del validation-wf

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class StageResult:
    stage: str              # "data_quality" | "short_pre_gate" | "long_engine" | "risk_audit" | "kill_switch_history"
    passed: bool
    metrics: dict           # métricas específicas por etapa
    violations: list[str]   # descripciones de qué falló
    skipped: bool = False   # True si la etapa fue skipped (ej: disabled en policy)


@dataclass(frozen=True)
class ValidationReport:
    go: bool                        # True = GO, False = NO-GO
    period_start: date
    period_end: date
    lookback_trading_days: int
    stages: list[StageResult]
    generated_at: str               # ISO timestamp
    policy_version: int             # schema_version del policy.v1.yaml
