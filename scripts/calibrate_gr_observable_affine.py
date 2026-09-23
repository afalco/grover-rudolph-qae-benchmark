#!/usr/bin/env python3
"""Leave-one-observable-out affine calibration for GR distribution estimates.

This is a diagnostic calibration study using known grid expectations for a
calibration distribution. It fits

    exact ~= alpha + beta * measured

on all but one observable and reports the held-out error.
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
    midpoint_grid,
)


DEFAULT_RUN = (
    PROJECT_ROOT
    / "results/hardware/phase3_gr_affine_n4_mitigation_ibm_fez"
    / "20260805T112409Z/fetched_gr_distribution_results.json"
)


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def load_case(path: Path, case_id: str) -> dict[str, Any]:
    for row in json.loads(path.read_text(encoding="utf-8")).get("rows", []):
        if row.get("case_id") == case_id:
            return row
    raise SystemExit(f"Case {case_id!r} not found in {path}")


def count_vector(counts: dict[str, int], grid_points: int) -> list[int]:
    out = [0] * grid_points
    for bitstring, count in counts.items():
        out[bitstring_to_probability_index(str(bitstring))] += int(count)
    return out


def observable_values(name: str, grid_points: int) -> list[float]:
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
    if name == "digital_upper_quarter":
        return [1.0 if index >= (3 * grid_points) // 4 else 0.0 for index in range(grid_points)]
    if name == "centered_abs":
        return [abs(x - 0.5) for x in xs]
    raise ValueError(f"Unknown observable {name!r}")


def estimate_from_counts(counts: list[int], values: list[float]) -> float:
    shots = sum(counts)
    return sum(count * value for count, value in zip(counts, values)) / shots


def expectation(probabilities: list[float], values: list[float]) -> float:
    return sum(probability * value for probability, value in zip(probabilities, values))


def fit_affine(xs: list[float], ys: list[float]) -> tuple[float, float]:
    if len(xs) != len(ys) or len(xs) < 2:
        raise ValueError("Need at least two paired samples.")
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x <= 1e-18:
        return mean_y, 0.0
    cov_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    beta = cov_xy / var_x
    alpha = mean_y - beta * mean_x
    return alpha, beta


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    keys = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Leave-one-observable-out affine calibration for GR observables."
    )
    parser.add_argument("--run", default=str(DEFAULT_RUN))
    parser.add_argument("--case-id", default="gr_affine_n4")
    parser.add_argument(
        "--observables",
        nargs="+",
        default=[
            "upper_half",
            "mean_x",
            "second_moment_x2",
            "sin_pi_x",
            "call_x_minus_half",
            "digital_upper_quarter",
            "centered_abs",
        ],
    )
    parser.add_argument("--out-dir", default="results/phase4/direct_estimator")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_path = resolve_path(args.run)
    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    case = load_case(run_path, args.case_id)
    grid_points = int(case["grid_points"])
    counts = count_vector(case["counts"], grid_points)
    target = [float(value) for value in case["target_probabilities"]]

    base = []
    for observable in args.observables:
        values = observable_values(observable, grid_points)
        measured = estimate_from_counts(counts, values)
        exact = expectation(target, values)
        base.append({"observable": observable, "measured": measured, "exact": exact})

    rows = []
    for held_out in base:
        train = [row for row in base if row["observable"] != held_out["observable"]]
        alpha, beta = fit_affine(
            [float(row["measured"]) for row in train],
            [float(row["exact"]) for row in train],
        )
        calibrated = alpha + beta * float(held_out["measured"])
        rows.append(
            {
                "source": str(run_path),
                "case_id": args.case_id,
                "held_out_observable": held_out["observable"],
                "measured": held_out["measured"],
                "exact": held_out["exact"],
                "raw_abs_error": abs(held_out["measured"] - held_out["exact"]),
                "calibrated": calibrated,
                "calibrated_abs_error": abs(calibrated - held_out["exact"]),
                "error_reduction": abs(held_out["measured"] - held_out["exact"])
                - abs(calibrated - held_out["exact"]),
                "alpha": alpha,
                "beta": beta,
                "num_training_observables": len(train),
            }
        )

    rows.sort(key=lambda row: row["held_out_observable"])
    csv_path = out_dir / "gr_observable_affine_calibration.csv"
    json_path = out_dir / "gr_observable_affine_calibration.json"
    write_csv(rows, csv_path)
    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")

    print(f"Wrote JSON: {json_path}")
    print(f"Wrote CSV:  {csv_path}")
    print()
    print("Leave-one-observable-out affine calibration:")
    for row in rows:
        print(
            f"  {row['held_out_observable']:<22} "
            f"raw_err={row['raw_abs_error']:.6f} "
            f"cal_err={row['calibrated_abs_error']:.6f} "
            f"delta={row['error_reduction']:+.6f} "
            f"alpha={row['alpha']:.4f} beta={row['beta']:.4f}"
        )
    mean_raw = sum(float(row["raw_abs_error"]) for row in rows) / len(rows)
    mean_cal = sum(float(row["calibrated_abs_error"]) for row in rows) / len(rows)
    print()
    print(f"Mean raw abs error:        {mean_raw:.6f}")
    print(f"Mean calibrated abs error: {mean_cal:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
