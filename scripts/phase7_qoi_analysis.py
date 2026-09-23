#!/usr/bin/env python3
"""Analyze Phase 7 direct-distribution quantity-of-interest validation runs.

This script uses local result files only. It does not connect to IBM Quantum and
does not submit jobs.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PHASE7_CAMPAIGNS = [
    "phase7_qoi_validation_n3_raw_ibm_fez",
    "phase7_qoi_validation_affine_mitigated_ibm_fez",
    "phase7_gr_beta_bump_n4_validation_opt3_seed45678_ibm_fez",
]


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def latest_result_for(campaign_id: str) -> Path | None:
    root = PROJECT_ROOT / "results" / "hardware" / campaign_id
    if not root.exists():
        return None
    candidates = sorted(
        root.glob("*/fetched_gr_distribution_results.json"),
        key=lambda path: path.parent.name,
        reverse=True,
    )
    return candidates[0] if candidates else None


def discover_results() -> list[Path]:
    paths: list[Path] = []
    for campaign_id in PHASE7_CAMPAIGNS:
        path = latest_result_for(campaign_id)
        if path is not None:
            paths.append(path)
    return paths


def run_command(args: list[str]) -> None:
    subprocess.run(args, cwd=PROJECT_ROOT, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build Phase 7 distribution and quantity-of-interest analysis tables."
    )
    parser.add_argument(
        "--distribution-runs",
        nargs="*",
        default=None,
        help="Fetched GR distribution result JSON files. Defaults to latest Phase 7 runs.",
    )
    parser.add_argument("--bootstrap-samples", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260808)
    parser.add_argument("--out-dir", default="results/phase7/qoi_analysis")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runs = [resolve_path(path) for path in args.distribution_runs] if args.distribution_runs else discover_results()
    if not runs:
        print("No fetched Phase 7 distribution results found.")
        print("Fetch at least one Phase 7 campaign before running this analysis.")
        return 1

    missing = [path for path in runs if not path.exists()]
    if missing:
        for path in missing:
            print(f"Missing result file: {path}")
        return 1

    out_dir = resolve_path(args.out_dir)
    qoi_metrics = out_dir / "phase7_qoi_metrics.csv"
    decision_dir = PROJECT_ROOT / "results" / "phase7" / "qoi_decision_table"

    run_command(
        [
            sys.executable,
            "scripts/phase6_distribution_analysis.py",
            "--distribution-runs",
            *[str(path) for path in runs],
            "--bootstrap-samples",
            str(args.bootstrap_samples),
            "--seed",
            str(args.seed),
            "--out-dir",
            str(out_dir),
            "--output-prefix",
            "phase7",
        ]
    )
    run_command(
        [
            sys.executable,
            "scripts/phase6_qoi_decision_table.py",
            "--qoi-metrics",
            str(qoi_metrics),
            "--out-dir",
            str(decision_dir),
            "--output-prefix",
            "phase7",
        ]
    )

    print()
    print("Phase 7 analysis complete.")
    print(f"Distribution/QoI metrics: {out_dir}")
    print(f"QoI decision table:       {decision_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
