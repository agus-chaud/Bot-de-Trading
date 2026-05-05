#!/usr/bin/env python3
"""Informe KPI: lee CSV de equity (+ trades o columnas de costo) -> JSON + Markdown.

Alineado con ``docs/kpi_report_spec.v1.md``: retorno anualizado, max DD, costos,
Sharpe/Sortino, hit rate / profit factor, drift mandato 30/70 y 20/80 (serie + snapshot).

Ejemplo::

    python scripts/report_kpis.py --equity equity.csv --trades fills.csv --out-json kpi.json --out-md kpi.md

Si el CSV de equity incluye ``costs_day_short`` y ``costs_day_long``, ``--trades`` es opcional.
Targets de drift por defecto desde ``config/policy.v1.yaml`` (flag ``--policy``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from reporting.kpi_v0 import (  # noqa: E402
    build_kpi_v0_report,
    write_report_json,
    write_report_markdown,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KPI report (equity + fills -> JSON/Markdown).")
    parser.add_argument(
        "--equity",
        required=True,
        type=Path,
        help="CSV serie diaria (ts, equity_total, equity_short, equity_long, ...).",
    )
    parser.add_argument(
        "--trades",
        type=Path,
        default=None,
        help="CSV fills: motor|bucket, fee|fees, slippage opcional. "
        "Opcional si equity tiene costs_day_short y costs_day_long.",
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=None,
        help="YAML/JSON opcional: run_id, spec_id, trading_days_per_year, weights/geo, "
        "mandate_drift_bands_pp.",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=REPO_ROOT / "config" / "policy.v1.yaml",
        help="YAML de política para targets weights/geo del drift (sec. 11). "
        "Use --no-policy para tomar solo metadata o defaults 30/70, 20/80.",
    )
    parser.add_argument(
        "--no-policy",
        action="store_true",
        help="No leer archivo de política; usar weights/geo del metadata o valores por defecto.",
    )
    parser.add_argument("--out-json", type=Path, required=True, help="Salida JSON.")
    parser.add_argument("--out-md", type=Path, required=True, help="Salida Markdown.")

    args = parser.parse_args(argv)

    policy_arg: Path | None = None if args.no_policy else args.policy

    report = build_kpi_v0_report(
        args.equity,
        args.trades,
        metadata_path=args.metadata,
        policy_path=policy_arg,
    )

    if report.costs_na_reason:
        print(f"warning: {report.costs_na_reason}", file=sys.stderr)

    write_report_json(report, args.out_json)
    write_report_markdown(report, args.out_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
