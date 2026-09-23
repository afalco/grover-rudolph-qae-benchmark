#!/usr/bin/env python3
"""Compare direct GR distribution estimates with amplified MLAE results.

This postprocesses existing hardware results only. It does not submit QPU jobs.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from grover_rudolph_qae_benchmark.gr_state_preparation import (  # noqa: E402
    bitstring_to_probability_index,
)


DEFAULT_DISTRIBUTION_RUNS = [
    PROJECT_ROOT
    / "results/hardware/phase3_gr_distribution_ibm_fez/20260805T103539Z/fetched_gr_distribution_results.json",
    PROJECT_ROOT
    / "results/hardware/phase3_gr_affine_n4_mitigation_ibm_fez/20260805T112409Z/fetched_gr_distribution_results.json",
]

DEFAULT_MLAE_RUNS = [
    PROJECT_ROOT
    / "results/hardware/phase4_gr_affine_n4_mlae_k01_ibm_fez/20260805T151942Z/fetched_gr_mlae_results.json",
    PROJECT_ROOT
    / "results/hardware/phase4_gr_affine_n4_k1_repeat_opt3_seed12345_ibm_fez/20260805T161506Z/fetched_gr_mlae_results.json",
]


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def load_rows(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("rows", [])


def upper_half_indices(grid_points: int) -> list[int]:
    return list(range(grid_points // 2, grid_points))


def marked_count_from_counts(counts: dict[str, int], grid_points: int) -> int:
    marked = set(upper_half_indices(grid_points))
    total = 0
    for bitstring, count in counts.items():
        if bitstring_to_probability_index(str(bitstring)) in marked:
            total += int(count)
    return total


def wilson_interval(successes: int, shots: int, *, z: float = 1.959963984540054) -> tuple[float, float]:
    if shots <= 0:
        return (float("nan"), float("nan"))
    phat = successes / shots
    denom = 1.0 + z * z / shots
    center = (phat + z * z / (2 * shots)) / denom
    half = z * math.sqrt((phat * (1 - phat) + z * z / (4 * shots)) / shots) / denom
    return max(0.0, center - half), min(1.0, center + half)


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    keys = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, sort_keys=True)
                    if isinstance(value, (dict, list))
                    else value
                    for key, value in row.items()
                }
            )


def distribution_rows(paths: list[Path], *, case_filter: str | None) -> list[dict[str, Any]]:
    rows_out = []
    for path in paths:
        for row in load_rows(path):
            if case_filter and row.get("case_id") != case_filter:
                continue
            grid_points = int(row["grid_points"])
            shots = int(row["shots"])
            successes = marked_count_from_counts(row["counts"], grid_points)
            p_hat = successes / shots if shots else float("nan")
            target = [float(value) for value in row["target_probabilities"]]
            exact = sum(target[index] for index in upper_half_indices(grid_points))
            ci_low, ci_high = wilson_interval(successes, shots)
            rows_out.append(
                {
                    "source": str(path),
                    "campaign_id": row.get("campaign_id"),
                    "case_id": row.get("case_id"),
                    "method": "distribution_direct",
                    "variant": "mitigated" if row.get("runtime_options") else "raw_distribution",
                    "backend": row.get("backend"),
                    "shots": shots,
                    "depth": row.get("transpiled_metrics", {}).get("depth"),
                    "twoq": row.get("transpiled_metrics", {}).get("two_qubit_gate_count"),
                    "estimate": p_hat,
                    "exact": exact,
                    "abs_error": abs(p_hat - exact),
                    "ci_low": ci_low,
                    "ci_high": ci_high,
                    "contains_exact": ci_low <= exact <= ci_high,
                    "distribution_tvd": row.get("tvd"),
                    "distribution_max_abs_deviation": row.get("max_abs_deviation"),
                    "marked_count": successes,
                }
            )
    return rows_out


def mlae_rows(paths: list[Path], *, case_filter: str | None) -> list[dict[str, Any]]:
    rows_out = []
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        for row in data.get("rows", []):
            if case_filter and row.get("case_id") != case_filter:
                continue
            shots = int(row["shots"])
            successes = int(row.get("marked_count", round(float(row["p_hat"]) * shots)))
            ci_low, ci_high = wilson_interval(successes, shots)
            rows_out.append(
                {
                    "source": str(path),
                    "campaign_id": row.get("campaign_id"),
                    "case_id": row.get("case_id"),
                    "method": "event_circuit_probability",
                    "variant": f"k={row.get('k')} opt={row.get('optimization_level')}",
                    "backend": row.get("backend"),
                    "shots": shots,
                    "depth": row.get("transpiled_metrics", {}).get("depth"),
                    "twoq": row.get("transpiled_metrics", {}).get("two_qubit_gate_count"),
                    "estimate": float(row["p_hat"]),
                    "exact": float(row["expected_p_k"]),
                    "abs_error": float(row["p_abs_dev"]),
                    "ci_low": ci_low,
                    "ci_high": ci_high,
                    "contains_exact": ci_low <= float(row["expected_p_k"]) <= ci_high,
                    "distribution_tvd": None,
                    "distribution_max_abs_deviation": None,
                    "marked_count": successes,
                }
            )
        for estimate in data.get("mlae_estimates", []):
            if case_filter and estimate.get("case_id") != case_filter:
                continue
            rows_out.append(
                {
                    "source": str(path),
                    "campaign_id": None,
                    "case_id": estimate.get("case_id"),
                    "method": "mlae_inverse",
                    "variant": "K=" + ",".join(str(k) for k in estimate.get("ks", [])),
                    "backend": None,
                    "shots": None,
                    "depth": None,
                    "twoq": None,
                    "estimate": float(estimate["a_hat"]),
                    "exact": float(estimate["a_exact"]),
                    "abs_error": float(estimate["abs_error"]),
                    "ci_low": None,
                    "ci_high": None,
                    "contains_exact": None,
                    "distribution_tvd": None,
                    "distribution_max_abs_deviation": None,
                    "marked_count": None,
                }
            )
    return rows_out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare direct distribution event estimates with amplified GR MLAE."
    )
    parser.add_argument(
        "--distribution-runs",
        nargs="*",
        default=[str(path) for path in DEFAULT_DISTRIBUTION_RUNS],
    )
    parser.add_argument("--mlae-runs", nargs="*", default=[str(path) for path in DEFAULT_MLAE_RUNS])
    parser.add_argument("--case-id", default="gr_affine_n4")
    parser.add_argument("--out-dir", default="results/phase4/direct_estimator")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    distribution_paths = [resolve_path(path) for path in args.distribution_runs]
    mlae_paths = [resolve_path(path) for path in args.mlae_runs]
    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = distribution_rows(distribution_paths, case_filter=args.case_id)
    rows.extend(mlae_rows(mlae_paths, case_filter=args.case_id))
    rows.sort(key=lambda row: (float(row["abs_error"]), str(row["method"]), str(row["variant"])))

    csv_path = out_dir / "gr_direct_estimator_comparison.csv"
    json_path = out_dir / "gr_direct_estimator_comparison.json"
    write_csv(rows, csv_path)
    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")

    print(f"Wrote JSON: {json_path}")
    print(f"Wrote CSV:  {csv_path}")
    print()
    print("Direct estimator comparison:")
    for row in rows:
        ci = ""
        if row["ci_low"] is not None:
            ci = f" ci=[{row['ci_low']:.6f},{row['ci_high']:.6f}]"
        cost = ""
        if row["depth"] is not None:
            cost = f" depth={row['depth']} 2q={row['twoq']}"
        print(
            f"  {row['method']:<25} {row['variant']:<18} "
            f"estimate={row['estimate']:.6f} exact={row['exact']:.6f} "
            f"err={row['abs_error']:.6f}{ci}{cost}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
