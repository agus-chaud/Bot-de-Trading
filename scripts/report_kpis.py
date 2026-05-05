#!/usr/bin/env python3
"""Informe KPI v0: lee CSV de equity (+ trades o columnas de costo) → JSON + Markdown.

Smoke test alineado con ``docs/kpi_report_spec.v1.md``: retorno neto anualizado (total),
max drawdown (total), costos por motor.

Ejemplo::

    python scripts/report_kpis.py --equity equity.csv --trades fills.csv --out-json kpi.json --out-md kpi.md

Si el CSV de equity incluye ``costs_day_short`` y ``costs_day_long``, ``--trades`` es opcional.
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
    parser = argparse.ArgumentParser(description="KPI report v0 (smoke).")
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
        help="YAML/JSON opcional: run_id, spec_id, trading_days_per_year, reporting_ccy.",
    )
    parser.add_argument("--out-json", type=Path, required=True, help="Salida JSON.")
    parser.add_argument("--out-md", type=Path, required=True, help="Salida Markdown.")

    args = parser.parse_args(argv)

    report = build_kpi_v0_report(
        args.equity,
        args.trades,
        metadata_path=args.metadata,
    )

    if report.costs_na_reason:
        print(f"warning: {report.costs_na_reason}", file=sys.stderr)

    write_report_json(report, args.out_json)
    write_report_markdown(report, args.out_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
