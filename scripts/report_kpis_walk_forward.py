#!/usr/bin/env python3
"""Walk-forward OOS del informe KPI v3: tabla maestra por ventana + pass/fail (``kpi_oos_gate`` en policy).

Ejemplo::

    python scripts/report_kpis_walk_forward.py \\
        --equity equity.csv --trades fills.csv \\
        --policy config/policy.v1.yaml \\
        --out-json wf_kpi_oos.json
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

    args = parser.parse_args(argv)
    result = run_kpi_oos_walk_forward_from_paths(
        equity_path=args.equity,
        trades_path=args.trades,
        policy_path=args.policy,
        metadata_path=args.metadata,
        benchmark_returns_path=args.benchmark_returns,
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
