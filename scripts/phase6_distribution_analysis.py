#!/usr/bin/env python3
"""Phase 6 postprocessing for measured Grover-Rudolph distributions.

This script uses existing hardware count files only. It does not connect to IBM
Quantum and does not submit jobs.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
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
    total_variation_distance,
)


DEFAULT_DISTRIBUTION_RUNS = [
    PROJECT_ROOT
    / "results/hardware/phase3_gr_distribution_ibm_fez/20260805T103539Z/fetched_gr_distribution_results.json",
    PROJECT_ROOT
    / "results/hardware/phase3_gr_affine_n4_mitigation_ibm_fez/20260805T112409Z/fetched_gr_distribution_results.json",
    PROJECT_ROOT
    / "results/hardware/phase5_gr_distribution_mitigation_suite_ibm_fez/20260805T172609Z/fetched_gr_distribution_results.json",
    PROJECT_ROOT
    / "results/hardware/phase5_gr_beta_bump_n4_repeat_opt3_seed45678_ibm_fez/20260805T173609Z/fetched_gr_distribution_results.json",
]

DEFAULT_OBSERVABLES = [
    "upper_half",
    "mean_x",
    "second_moment_x2",
    "sin_pi_x",
    "call_x_minus_half",
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


def distribution_from_count_vector(counts: list[int]) -> list[float]:
    shots = sum(counts)
    if shots <= 0:
        return [float("nan")] * len(counts)
    return [count / shots for count in counts]


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
    raise ValueError(f"Unknown quantity of interest {name!r}")


def expectation(distribution: list[float], values: list[float]) -> float:
    return sum(float(p) * float(v) for p, v in zip(distribution, values))


def max_abs_deviation(observed: list[float], target: list[float]) -> float:
    return max(abs(float(a) - float(b)) for a, b in zip(observed, target))


def l2_distance(observed: list[float], target: list[float]) -> float:
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(observed, target)))


def classical_fidelity(observed: list[float], target: list[float]) -> float:
    overlap = sum(math.sqrt(max(0.0, float(a)) * max(0.0, float(b))) for a, b in zip(observed, target))
    return overlap * overlap


def hellinger_distance(observed: list[float], target: list[float]) -> float:
    sq = sum(
        (math.sqrt(max(0.0, float(a))) - math.sqrt(max(0.0, float(b)))) ** 2
        for a, b in zip(observed, target)
    )
    return math.sqrt(max(0.0, sq) / 2.0)


def distribution_metrics(observed: list[float], target: list[float]) -> dict[str, float]:
    return {
        "tvd": total_variation_distance(observed, target),
        "classical_fidelity": classical_fidelity(observed, target),
        "hellinger": hellinger_distance(observed, target),
        "l2_distance": l2_distance(observed, target),
        "max_abs_deviation": max_abs_deviation(observed, target),
    }


def bootstrap_counts(counts: list[int], rng: random.Random) -> list[int]:
    shots = sum(counts)
    if shots <= 0:
        return [0] * len(counts)
    population = list(range(len(counts)))
    weights = counts
    sample = [0] * len(counts)
    for index in rng.choices(population, weights=weights, k=shots):
        sample[index] += 1
    return sample


def percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lower = int(math.floor(pos))
    upper = int(math.ceil(pos))
    if lower == upper:
        return ordered[lower]
    weight = pos - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def interval(values: list[float], alpha: float = 0.05) -> tuple[float, float]:
    return percentile(values, alpha / 2.0), percentile(values, 1.0 - alpha / 2.0)


def variant_label(row: dict[str, Any], source_path: Path) -> str:
    campaign = str(row.get("campaign_id", ""))
    if "repeat_opt3_seed45678" in campaign:
        return "mitigated_opt3_seed45678"
    if row.get("runtime_options"):
        return "mitigated"
    return "raw"


def row_id(row: dict[str, Any], source_path: Path) -> str:
    return f"{row.get('case_id')}::{row.get('campaign_id')}::{source_path.parent.name}"


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
        description="Distribution and quantity-of-interest postprocessing."
    )
    parser.add_argument(
        "--distribution-runs",
        nargs="*",
        default=[str(path) for path in DEFAULT_DISTRIBUTION_RUNS],
    )
    parser.add_argument("--observables", nargs="+", default=DEFAULT_OBSERVABLES, help=argparse.SUPPRESS)
    parser.add_argument(
        "--quantities",
        nargs="+",
        default=None,
        help="Quantity-of-interest functionals to evaluate.",
    )
    parser.add_argument("--bootstrap-samples", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260808)
    parser.add_argument("--out-dir", default="results/phase6/distribution_analysis")
    parser.add_argument("--output-prefix", default="phase6")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    quantities = args.quantities or args.observables
    paths = [resolve_path(path) for path in args.distribution_runs]
    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    distribution_rows: list[dict[str, Any]] = []
    observable_rows: list[dict[str, Any]] = []

    for path in paths:
        for source_row in load_rows(path):
            case_id = str(source_row.get("case_id"))
            grid_points = int(source_row["grid_points"])
            shots = int(source_row["shots"])
            counts = count_vector(source_row["counts"], grid_points)
            observed = distribution_from_count_vector(counts)
            target = [float(value) for value in source_row["target_probabilities"]]

            metrics = distribution_metrics(observed, target)
            metric_samples = {name: [] for name in metrics}
            observable_samples = {name: [] for name in quantities}
            for _ in range(max(0, int(args.bootstrap_samples))):
                sample_counts = bootstrap_counts(counts, rng)
                sample_distribution = distribution_from_count_vector(sample_counts)
                sample_metrics = distribution_metrics(sample_distribution, target)
                for name, value in sample_metrics.items():
                    metric_samples[name].append(value)
                for quantity in quantities:
                    values = observable_values(quantity, grid_points)
                    observable_samples[quantity].append(expectation(sample_distribution, values))

            dist_record: dict[str, Any] = {
                "row_id": row_id(source_row, path),
                "source": str(path),
                "case_id": case_id,
                "campaign_id": source_row.get("campaign_id"),
                "variant": variant_label(source_row, path),
                "backend": source_row.get("backend"),
                "grid_points": grid_points,
                "shots": shots,
                "depth": source_row.get("transpiled_metrics", {}).get("depth"),
                "twoq": source_row.get("transpiled_metrics", {}).get("two_qubit_gate_count"),
                "bootstrap_samples": int(args.bootstrap_samples),
            }
            for name, value in metrics.items():
                lo, hi = interval(metric_samples[name]) if metric_samples[name] else (float("nan"), float("nan"))
                dist_record[name] = value
                dist_record[f"{name}_ci_low"] = lo
                dist_record[f"{name}_ci_high"] = hi
            distribution_rows.append(dist_record)

            for observable in quantities:
                values = observable_values(observable, grid_points)
                estimate = expectation(observed, values)
                exact = expectation(target, values)
                lo, hi = (
                    interval(observable_samples[observable])
                    if observable_samples[observable]
                    else (float("nan"), float("nan"))
                )
                observable_rows.append(
                    {
                        "row_id": row_id(source_row, path),
                        "source": str(path),
                        "case_id": case_id,
                        "campaign_id": source_row.get("campaign_id"),
                        "variant": variant_label(source_row, path),
                        "quantity": observable,
                        "observable": observable,
                        "backend": source_row.get("backend"),
                        "grid_points": grid_points,
                        "shots": shots,
                        "depth": source_row.get("transpiled_metrics", {}).get("depth"),
                        "twoq": source_row.get("transpiled_metrics", {}).get("two_qubit_gate_count"),
                        "estimate": estimate,
                        "exact": exact,
                        "abs_error": abs(estimate - exact),
                        "estimate_ci_low": lo,
                        "estimate_ci_high": hi,
                        "contains_exact": lo <= exact <= hi,
                        "bootstrap_samples": int(args.bootstrap_samples),
                    }
                )

    distribution_rows.sort(key=lambda row: (row["case_id"], row["variant"], row["campaign_id"]))
    observable_rows.sort(
        key=lambda row: (row["case_id"], row["variant"], row["observable"], row["campaign_id"])
    )

    prefix = str(args.output_prefix)
    dist_json = out_dir / f"{prefix}_distribution_metrics.json"
    dist_csv = out_dir / f"{prefix}_distribution_metrics.csv"
    obs_json = out_dir / f"{prefix}_qoi_metrics.json"
    obs_csv = out_dir / f"{prefix}_qoi_metrics.csv"
    dist_json.write_text(json.dumps(distribution_rows, indent=2, sort_keys=True), encoding="utf-8")
    obs_json.write_text(json.dumps(observable_rows, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(distribution_rows, dist_csv)
    write_csv(observable_rows, obs_csv)
    if prefix == "phase6":
        # Backward-compatible filenames for older scripts and existing notebooks.
        (out_dir / "phase6_observable_metrics.json").write_text(
            json.dumps(observable_rows, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        write_csv(observable_rows, out_dir / "phase6_observable_metrics.csv")

    print(f"Wrote JSON: {dist_json}")
    print(f"Wrote CSV:  {dist_csv}")
    print(f"Wrote JSON: {obs_json}")
    print(f"Wrote CSV:  {obs_csv}")
    print()
    print("Distribution metrics:")
    for row in distribution_rows:
        print(
            f"  {row['case_id']:<18} {row['variant']:<24} "
            f"TVD={row['tvd']:.6f} "
            f"H={row['hellinger']:.6f} "
            f"Fcl={row['classical_fidelity']:.6f} "
            f"L2={row['l2_distance']:.6f} "
            f"max={row['max_abs_deviation']:.6f}"
        )
    print()
    print("Largest quantity-of-interest errors:")
    for row in sorted(observable_rows, key=lambda item: item["abs_error"], reverse=True)[:12]:
        print(
            f"  {row['case_id']:<18} {row['variant']:<24} {row['quantity']:<18} "
            f"err={row['abs_error']:.6f} "
            f"ci=[{row['estimate_ci_low']:.6f},{row['estimate_ci_high']:.6f}] "
            f"contains={row['contains_exact']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
