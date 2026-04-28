#!/usr/bin/env python3
"""Ejecuta el workflow de validación pre-live (exit 0 = GO, 1 = NO-GO)."""

from __future__ import annotations

import argparse
import dataclasses
import io
import json
import sys
from datetime import date, datetime
from pathlib import Path

# Forzar UTF-8 en stdout para soportar emojis en Windows (cp1252 no los tiene)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.storage import MarketDB                   # noqa: E402
from validation.report import ValidationReport      # noqa: E402
from validation.runner import run_validation_wf     # noqa: E402

POLICY_YAML = REPO_ROOT / "config" / "policy.v1.yaml"
REPORTS_DIR = REPO_ROOT / "validation_reports"

_STAGE_LABELS = {
    "data_quality": "Data Quality",
    "short_pre_gate": "Short Pre-Gate",
    "long_engine": "Long Engine",
    "risk_audit": "Risk Audit",
    "kill_switch_history": "Kill Switch History",
}


def _print_summary(report: ValidationReport) -> None:
    print()
    print("=" * 54)
    print("  VALIDATION WORKFLOW — PRE-LIVE REPORT")
    print("=" * 54)
    for stage in report.stages:
        label = _STAGE_LABELS.get(stage.stage, stage.stage)
        if stage.skipped:
            icon = "⏭ "
            status = "SKIPPED"
        elif stage.passed:
            icon = "✅"
            status = "PASS"
        else:
            icon = "❌"
            status = "FAIL"
        print(f"  {icon}  {label:<28} {status}")
        for v in stage.violations:
            print(f"       • {v}")
    print("-" * 54)
    verdict = "✅  GO" if report.go else "❌  NO-GO"
    print(f"  VEREDICTO FINAL: {verdict}")
    print(f"  Generado: {report.generated_at}")
    print("=" * 54)
    print()


def _report_to_dict(report: ValidationReport) -> dict:
    """Serializa ValidationReport a dict JSON-compatible."""
    d = dataclasses.asdict(report)
    # Convertir date → string ISO
    for key in ("period_start", "period_end"):
        if isinstance(d[key], date):
            d[key] = d[key].isoformat()
    return d


def _save_report(report: ValidationReport) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    out_path = REPORTS_DIR / f"validation_{ts}.json"
    payload = _report_to_dict(report)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Workflow de validación pre-live del bot de trading."
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Path a la MarketDB SQLite. Por defecto: data/market.db relativo al repo.",
    )
    parser.add_argument(
        "--cash",
        type=float,
        default=100_000.0,
        help="Capital inicial de referencia para las simulaciones (default: 100000).",
    )
    parser.add_argument(
        "--date",
        dest="reference_date",
        default=None,
        help="Fecha de referencia ISO YYYY-MM-DD (default: hoy).",
    )
    args = parser.parse_args()

    # Cargar policy
    with POLICY_YAML.open(encoding="utf-8") as f:
        policy_doc = yaml.safe_load(f)

    # Resolver path de la DB
    db_path = args.db or str(REPO_ROOT / "data" / "market.db")
    db = MarketDB(db_path)

    # Fecha de referencia
    reference_date: date | None = None
    if args.reference_date:
        reference_date = date.fromisoformat(args.reference_date)

    # Ejecutar workflow
    report = run_validation_wf(
        policy_doc=policy_doc,
        db=db,
        starting_cash=args.cash,
        reference_date=reference_date,
    )

    # Mostrar resumen en consola
    _print_summary(report)

    # Guardar JSON
    out_path = _save_report(report)
    print(f"  Reporte guardado en: {out_path}")
    print()

    sys.exit(0 if report.go else 1)


if __name__ == "__main__":
    main()
