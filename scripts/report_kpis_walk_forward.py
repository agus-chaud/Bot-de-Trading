#!/usr/bin/env python3
"""Walk-forward OOS del informe KPI v3: tabla maestra por ventana + pass/fail (``kpi_oos_gate`` en policy).

Ejemplo (policy por defecto; ver ``kpi_oos_gate.walk_forward`` en YAML)::

    python scripts/report_kpis_walk_forward.py \\
        --equity equity.csv --trades fills.csv \\
        --policy config/policy.v1.yaml \\
        --out-json wf_kpi_oos.json

Demo con pocos días (p. ej. fixture de 60 sesiones): sobrescribí la rejilla con ``--wf-*``.
Una ventana que cubra toda la serie evita cortar fills BUY/SELL en mitades distintas::

    python scripts/report_kpis_walk_forward.py \\
        --equity tests/fixtures/kpi_golden/equity_60d.csv \\
        --trades tests/fixtures/kpi_golden/trades_60d.csv \\
        --metadata tests/fixtures/kpi_golden/metadata.yaml \\
        --benchmark-returns tests/fixtures/kpi_golden/benchmark_returns_60d.csv \\
        --wf-burn-in 0 --wf-oos 60 --wf-step 60 \\
        --out-json wf.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from reporting.kpi_walk_forward import (  # noqa: E402
    run_kpi_oos_walk_forward_from_paths,
    write_kpi_oos_walk_forward_json,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Walk-forward OOS: KPI v3 por tramo + gate (policy kpi_oos_gate).",
    )
    parser.add_argument("--equity", type=Path, required=True)
    parser.add_argument("--trades", type=Path, default=None)
    parser.add_argument(
        "--policy",
        type=Path,
        default=REPO_ROOT / "config" / "policy.v1.yaml",
    )
    parser.add_argument("--metadata", type=Path, default=None)
    parser.add_argument("--benchmark-returns", type=Path, default=None)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument(
        "--wf-burn-in",
        type=int,
        default=None,
        metavar="N",
        help="Override kpi_oos_gate.walk_forward.burn_in_trading_days (útil si el CSV tiene menos de burn_in+oos días).",
    )
    parser.add_argument(
        "--wf-oos",
        type=int,
        default=None,
        metavar="N",
        help="Override kpi_oos_gate.walk_forward.oos_trading_days.",
    )
    parser.add_argument(
        "--wf-step",
        type=int,
        default=None,
        metavar="N",
        help="Override kpi_oos_gate.walk_forward.step_trading_days.",
    )
    parser.add_argument(
        "--wf-min-windows",
        type=int,
        default=None,
        metavar="N",
        help="Override kpi_oos_gate.walk_forward.min_oos_windows.",
    )

    args = parser.parse_args(argv)
    wf_override: dict[str, object] = {}
    if args.wf_burn_in is not None:
        wf_override["burn_in_trading_days"] = args.wf_burn_in
    if args.wf_oos is not None:
        wf_override["oos_trading_days"] = args.wf_oos
    if args.wf_step is not None:
        wf_override["step_trading_days"] = args.wf_step
    if args.wf_min_windows is not None:
        wf_override["min_oos_windows"] = args.wf_min_windows

    result = run_kpi_oos_walk_forward_from_paths(
        equity_path=args.equity,
        trades_path=args.trades,
        policy_path=args.policy,
        metadata_path=args.metadata,
        benchmark_returns_path=args.benchmark_returns,
        walk_forward_override=wf_override if wf_override else None,
    )
    write_kpi_oos_walk_forward_json(result, args.out_json)

    print("aggregate_passed:", result.aggregate_passed)
    print("gate_enabled:", result.gate_enabled)
    print("windows:", len(result.windows))
    if result.global_failures:
        print("global_failures:", "; ".join(result.global_failures))
    return 0 if (not result.global_failures and result.aggregate_passed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
