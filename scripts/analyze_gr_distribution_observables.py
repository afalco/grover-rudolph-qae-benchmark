#!/usr/bin/env python3
"""Estimate quantities of interest from measured Grover-Rudolph distributions.

This uses existing distribution-measurement hardware results only. It does not
submit QPU jobs.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from grover_rudolph_qae_benchmark.gr_state_preparation import (  # noqa: E402
    bitstring_to_probability_index,
    midpoint_grid,
)


DEFAULT_DISTRIBUTION_RUNS = [
    PROJECT_ROOT
    / "results/hardware/phase3_gr_distribution_ibm_fez/20260805T103539Z/fetched_gr_distribution_results.json",
    PROJECT_ROOT
    / "results/hardware/phase3_gr_affine_n4_mitigation_ibm_fez/20260805T112409Z/fetched_gr_distribution_results.json",
]


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def load_rows(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("rows", [])


def count_vector(counts: dict[str, int], grid_points: int) -> list[int]:
    out = [0] * grid_points
    for bitstring, count in counts.items():
        out[bitstring_to_probability_index(str(bitstring))] += int(count)
    return out


def quantity_values(name: str, grid_points: int) -> list[float]:
    xs = midpoint_grid(grid_points)
    if name == "upper_half":
        return [1.0 if index >= grid_points // 2 else 0.0 for index in range(grid_points)]
    if name == "mean_x":
        return xs
    if name == "second_moment_x2":
        return [x * x for x in xs]
    if name == "sin_pi_x":
        return [math.sin(math.pi * x) for x in xs]
    if name == "call_x_minus_half":
        return [max(x - 0.5, 0.0) for x in xs]
    raise ValueError(f"Unknown quantity of interest {name!r}")


def estimate_from_counts(counts: list[int], values: list[float]) -> float:
    shots = sum(counts)
    if shots <= 0:
        return float("nan")
    return sum(count * value for count, value in zip(counts, values)) / shots


def exact_from_distribution(probabilities: list[float], values: list[float]) -> float:
    return sum(probability * value for probability, value in zip(probabilities, values))


def standard_error_from_counts(counts: list[int], values: list[float]) -> float:
    shots = sum(counts)
    if shots <= 1:
        return float("nan")
    mean = estimate_from_counts(counts, values)
    second = sum(count * value * value for count, value in zip(counts, values)) / shots
    variance = max(0.0, second - mean * mean)
    return math.sqrt(variance / shots)


def normal_interval(estimate: float, standard_error: float, *, z: float = 1.959963984540054) -> tuple[float, float]:
    if math.isnan(standard_error):
        return (float("nan"), float("nan"))
    return estimate - z * standard_error, estimate + z * standard_error


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Estimate quantities of interest from measured GR distributions."
    )
    parser.add_argument(
        "--distribution-runs",
        nargs="*",
        default=[str(path) for path in DEFAULT_DISTRIBUTION_RUNS],
    )
    parser.add_argument(
        "--quantities",
        nargs="+",
        default=[
            "upper_half",
            "mean_x",
            "second_moment_x2",
            "sin_pi_x",
            "call_x_minus_half",
        ],
    )
    parser.add_argument(
        "--observables",
        nargs="+",
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--case-id", default=None)
    parser.add_argument("--out-dir", default="results/phase4/direct_estimator")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    quantities = args.observables if args.observables is not None else args.quantities
    paths = [resolve_path(path) for path in args.distribution_runs]
    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows_out: list[dict[str, Any]] = []
    for path in paths:
        for row in load_rows(path):
            if args.case_id and row.get("case_id") != args.case_id:
                continue
            grid_points = int(row["grid_points"])
            shots = int(row["shots"])
            counts = count_vector(row["counts"], grid_points)
            target = [float(value) for value in row["target_probabilities"]]
            variant = "mitigated" if row.get("runtime_options") else "raw_distribution"
            for quantity in quantities:
                values = quantity_values(quantity, grid_points)
                estimate = estimate_from_counts(counts, values)
                exact = exact_from_distribution(target, values)
                standard_error = standard_error_from_counts(counts, values)
                ci_low, ci_high = normal_interval(estimate, standard_error)
                rows_out.append(
                    {
                        "source": str(path),
                        "campaign_id": row.get("campaign_id"),
                        "case_id": row.get("case_id"),
                        "label": row.get("label"),
                        "variant": variant,
                        "quantity": quantity,
                        "backend": row.get("backend"),
                        "grid_points": grid_points,
                        "shots": shots,
                        "depth": row.get("transpiled_metrics", {}).get("depth"),
                        "twoq": row.get("transpiled_metrics", {}).get("two_qubit_gate_count"),
                        "distribution_tvd": row.get("tvd"),
                        "distribution_max_abs_deviation": row.get("max_abs_deviation"),
                        "estimate": estimate,
                        "exact": exact,
                        "abs_error": abs(estimate - exact),
                        "standard_error": standard_error,
                        "ci_low": ci_low,
                        "ci_high": ci_high,
                        "contains_exact": ci_low <= exact <= ci_high,
                    }
                )

    rows_out.sort(
        key=lambda row: (
            str(row["case_id"]),
            str(row["variant"]),
            str(row["quantity"]),
        )
    )
    csv_path = out_dir / "gr_distribution_qoi.csv"
    json_path = out_dir / "gr_distribution_qoi.json"
    write_csv(rows_out, csv_path)
    json_path.write_text(json.dumps(rows_out, indent=2, sort_keys=True), encoding="utf-8")

    print(f"Wrote JSON: {json_path}")
    print(f"Wrote CSV:  {csv_path}")
    print()
    print("Quantity-of-interest estimates from measured GR distributions:")
    for row in rows_out:
        print(
            f"  {row['case_id']:<18} {row['variant']:<16} {row['quantity']:<18} "
            f"estimate={row['estimate']:.6f} exact={row['exact']:.6f} "
            f"err={row['abs_error']:.6f} "
            f"ci=[{row['ci_low']:.6f},{row['ci_high']:.6f}] "
            f"contains={row['contains_exact']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
