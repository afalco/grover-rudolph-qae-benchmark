#!/usr/bin/env python3
"""Phase 10 comparison of QPU sampling with MC/QMC finite-law baselines.

This script is credit-free. It uses fetched Grover-Rudolph distribution counts
and compares the resulting empirical integration estimates with classical Monte
Carlo and randomized quasi-Monte Carlo estimators that sample from the same
finite probability law.
"""

from __future__ import annotations

import argparse
import bisect
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
    / "results/hardware/phase9_affine_n5_distribution_ibm_kingston/20260808T195550Z/fetched_gr_distribution_results.json",
    PROJECT_ROOT
    / "results/hardware/phase9_beta_bump_n5_distribution_ibm_kingston/20260808T202355Z/fetched_gr_distribution_results.json",
]

DEFAULT_QUANTITIES = [
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
    if path.is_dir():
        candidate = path / "fetched_gr_distribution_results.json"
        if candidate.exists():
            return candidate
    return path


def load_rows(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    return list(data.get("rows", []))


def count_vector(counts: dict[str, int], grid_points: int) -> list[int]:
    out = [0] * grid_points
    for bitstring, count in counts.items():
        out[bitstring_to_probability_index(str(bitstring))] += int(count)
    return out


def distribution_from_counts_vector(counts: list[int]) -> list[float]:
    shots = sum(counts)
    if shots <= 0:
        return [float("nan")] * len(counts)
    return [count / shots for count in counts]


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


def expectation(distribution: list[float], values: list[float]) -> float:
    return sum(float(p) * float(value) for p, value in zip(distribution, values))


def l2_distance(observed: list[float], target: list[float]) -> float:
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(observed, target)))


def max_abs_deviation(observed: list[float], target: list[float]) -> float:
    return max(abs(float(a) - float(b)) for a, b in zip(observed, target))


def hellinger_distance(observed: list[float], target: list[float]) -> float:
    squared = sum(
        (math.sqrt(max(0.0, float(a))) - math.sqrt(max(0.0, float(b)))) ** 2
        for a, b in zip(observed, target)
    )
    return math.sqrt(max(0.0, squared) / 2.0)


def classical_fidelity(observed: list[float], target: list[float]) -> float:
    overlap = sum(
        math.sqrt(max(0.0, float(a)) * max(0.0, float(b)))
        for a, b in zip(observed, target)
    )
    return overlap * overlap


def distribution_metrics(observed: list[float], target: list[float]) -> dict[str, float]:
    return {
        "tvd": total_variation_distance(observed, target),
        "hellinger": hellinger_distance(observed, target),
        "classical_fidelity": classical_fidelity(observed, target),
        "l2_distance": l2_distance(observed, target),
        "max_abs_deviation": max_abs_deviation(observed, target),
    }


def cumulative_distribution(probabilities: list[float]) -> list[float]:
    cumulative: list[float] = []
    running = 0.0
    for probability in probabilities:
        running += float(probability)
        cumulative.append(running)
    if cumulative:
        cumulative[-1] = 1.0
    return cumulative


def sample_index(cumulative: list[float], u: float) -> int:
    return min(bisect.bisect_right(cumulative, u), len(cumulative) - 1)


def mc_counts(cumulative: list[float], shots: int, rng: random.Random) -> list[int]:
    counts = [0] * len(cumulative)
    for _ in range(shots):
        counts[sample_index(cumulative, rng.random())] += 1
    return counts


def van_der_corput(index: int, base: int = 2) -> float:
    value = 0.0
    denominator = 1.0
    while index > 0:
        index, remainder = divmod(index, base)
        denominator *= base
        value += remainder / denominator
    return value


def qmc_counts(cumulative: list[float], shots: int, shift: float) -> list[int]:
    counts = [0] * len(cumulative)
    for sample in range(1, shots + 1):
        u = (van_der_corput(sample) + shift) % 1.0
        counts[sample_index(cumulative, u)] += 1
    return counts


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


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


def stddev(values: list[float]) -> float:
    if len(values) < 2:
        return float("nan")
    average = mean(values)
    return math.sqrt(sum((value - average) ** 2 for value in values) / (len(values) - 1))


def rmse(errors: list[float]) -> float:
    return math.sqrt(mean([error * error for error in errors])) if errors else float("nan")


def percentile_rank(samples: list[float], value: float) -> float:
    if not samples:
        return float("nan")
    return sum(1 for sample in samples if sample <= value) / len(samples)


def summarize_samples(samples: list[float]) -> dict[str, float]:
    return {
        "mean": mean(samples),
        "std": stddev(samples),
        "p025": percentile(samples, 0.025),
        "p25": percentile(samples, 0.25),
        "median": percentile(samples, 0.5),
        "p75": percentile(samples, 0.75),
        "p975": percentile(samples, 0.975),
    }


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


def qpu_row_id(source_row: dict[str, Any], path: Path) -> str:
    return f"{source_row.get('case_id')}::{source_row.get('campaign_id')}::{path.parent.name}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare QPU Grover-Rudolph sampling with MC/QMC baselines."
    )
    parser.add_argument(
        "--distribution-runs",
        nargs="*",
        default=[str(path) for path in DEFAULT_DISTRIBUTION_RUNS],
        help="Fetched distribution JSON files or campaign-run directories.",
    )
    parser.add_argument(
        "--quantities",
        nargs="+",
        default=DEFAULT_QUANTITIES,
        help="Finite-law quantities of interest to evaluate.",
    )
    parser.add_argument("--trials", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260809)
    parser.add_argument("--out-dir", default="results/phase10/sampling_baselines")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = [resolve_path(path) for path in args.distribution_runs]
    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    distribution_rows: list[dict[str, Any]] = []
    qoi_rows: list[dict[str, Any]] = []

    for path in paths:
        for source_row in load_rows(path):
            case_id = str(source_row["case_id"])
            grid_points = int(source_row["grid_points"])
            shots = int(source_row["shots"])
            target = [float(value) for value in source_row["target_probabilities"]]
            cumulative = cumulative_distribution(target)
            qpu_counts = count_vector(source_row["counts"], grid_points)
            qpu_distribution = distribution_from_counts_vector(qpu_counts)
            qpu_dist_metrics = distribution_metrics(qpu_distribution, target)

            mc_dist_samples = {key: [] for key in qpu_dist_metrics}
            qmc_dist_samples = {key: [] for key in qpu_dist_metrics}
            mc_qoi_samples = {quantity: [] for quantity in args.quantities}
            qmc_qoi_samples = {quantity: [] for quantity in args.quantities}

            quantity_value_map = {
                quantity: quantity_values(quantity, grid_points)
                for quantity in args.quantities
            }

            for _ in range(max(0, int(args.trials))):
                mc_distribution = distribution_from_counts_vector(mc_counts(cumulative, shots, rng))
                qmc_distribution = distribution_from_counts_vector(
                    qmc_counts(cumulative, shots, rng.random())
                )

                for name, value in distribution_metrics(mc_distribution, target).items():
                    mc_dist_samples[name].append(value)
                for name, value in distribution_metrics(qmc_distribution, target).items():
                    qmc_dist_samples[name].append(value)

                for quantity, values in quantity_value_map.items():
                    mc_qoi_samples[quantity].append(expectation(mc_distribution, values))
                    qmc_qoi_samples[quantity].append(expectation(qmc_distribution, values))

            base_record = {
                "row_id": qpu_row_id(source_row, path),
                "source": str(path),
                "case_id": case_id,
                "campaign_id": source_row.get("campaign_id"),
                "backend": source_row.get("backend"),
                "shots": shots,
                "grid_points": grid_points,
                "depth": source_row.get("transpiled_metrics", {}).get("depth"),
                "twoq": source_row.get("transpiled_metrics", {}).get("two_qubit_gate_count"),
                "optimization_level": source_row.get("optimization_level"),
                "seed": source_row.get("seed"),
                "trials": int(args.trials),
            }

            for metric_name, qpu_value in qpu_dist_metrics.items():
                mc_summary = summarize_samples(mc_dist_samples[metric_name])
                qmc_summary = summarize_samples(qmc_dist_samples[metric_name])
                distribution_rows.append(
                    {
                        **base_record,
                        "metric": metric_name,
                        "qpu_value": qpu_value,
                        "mc_mean": mc_summary["mean"],
                        "mc_median": mc_summary["median"],
                        "mc_p025": mc_summary["p025"],
                        "mc_p975": mc_summary["p975"],
                        "mc_percentile_rank_of_qpu": percentile_rank(
                            mc_dist_samples[metric_name], qpu_value
                        ),
                        "qmc_mean": qmc_summary["mean"],
                        "qmc_median": qmc_summary["median"],
                        "qmc_p025": qmc_summary["p025"],
                        "qmc_p975": qmc_summary["p975"],
                        "qmc_percentile_rank_of_qpu": percentile_rank(
                            qmc_dist_samples[metric_name], qpu_value
                        ),
                    }
                )

            for quantity, values in quantity_value_map.items():
                exact = expectation(target, values)
                qpu_estimate = expectation(qpu_distribution, values)
                mc_errors = [sample - exact for sample in mc_qoi_samples[quantity]]
                qmc_errors = [sample - exact for sample in qmc_qoi_samples[quantity]]
                mc_abs_errors = [abs(error) for error in mc_errors]
                qmc_abs_errors = [abs(error) for error in qmc_errors]
                qpu_abs_error = abs(qpu_estimate - exact)

                qoi_rows.append(
                    {
                        **base_record,
                        "quantity": quantity,
                        "exact": exact,
                        "qpu_estimate": qpu_estimate,
                        "qpu_error": qpu_estimate - exact,
                        "qpu_abs_error": qpu_abs_error,
                        "mc_mean_estimate": mean(mc_qoi_samples[quantity]),
                        "mc_bias": mean(mc_errors),
                        "mc_mae": mean(mc_abs_errors),
                        "mc_rmse": rmse(mc_errors),
                        "mc_std": stddev(mc_qoi_samples[quantity]),
                        "mc_abs_error_p50": percentile(mc_abs_errors, 0.5),
                        "mc_abs_error_p95": percentile(mc_abs_errors, 0.95),
                        "mc_percentile_rank_of_qpu_abs_error": percentile_rank(
                            mc_abs_errors, qpu_abs_error
                        ),
                        "qmc_mean_estimate": mean(qmc_qoi_samples[quantity]),
                        "qmc_bias": mean(qmc_errors),
                        "qmc_mae": mean(qmc_abs_errors),
                        "qmc_rmse": rmse(qmc_errors),
                        "qmc_std": stddev(qmc_qoi_samples[quantity]),
                        "qmc_abs_error_p50": percentile(qmc_abs_errors, 0.5),
                        "qmc_abs_error_p95": percentile(qmc_abs_errors, 0.95),
                        "qmc_percentile_rank_of_qpu_abs_error": percentile_rank(
                            qmc_abs_errors, qpu_abs_error
                        ),
                        "qpu_abs_error_over_mc_rmse": qpu_abs_error / rmse(mc_errors)
                        if rmse(mc_errors) > 0
                        else float("inf"),
                        "qpu_abs_error_over_qmc_rmse": qpu_abs_error / rmse(qmc_errors)
                        if rmse(qmc_errors) > 0
                        else float("inf"),
                    }
                )

    distribution_rows.sort(key=lambda row: (row["case_id"], row["metric"]))
    qoi_rows.sort(key=lambda row: (row["case_id"], row["quantity"]))

    dist_json = out_dir / "phase10_sampling_distribution_metrics.json"
    dist_csv = out_dir / "phase10_sampling_distribution_metrics.csv"
    qoi_json = out_dir / "phase10_sampling_qoi_metrics.json"
    qoi_csv = out_dir / "phase10_sampling_qoi_metrics.csv"
    dist_json.write_text(json.dumps(distribution_rows, indent=2, sort_keys=True), encoding="utf-8")
    qoi_json.write_text(json.dumps(qoi_rows, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(distribution_rows, dist_csv)
    write_csv(qoi_rows, qoi_csv)

    summary_md = out_dir / "phase10_sampling_summary.md"
    lines = [
        "# Phase 10 Sampling Baselines",
        "",
        "All MC and QMC estimates use the same finite probability law and the same",
        "number of samples as the corresponding QPU run.",
        "",
        "## Distribution Metrics",
        "",
        "| Case | Metric | QPU | MC median | QMC median | QPU percentile vs MC | QPU percentile vs QMC |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in distribution_rows:
        if row["metric"] != "tvd":
            continue
        lines.append(
            f"| `{row['case_id']}` | {row['metric']} | {row['qpu_value']:.6f} | "
            f"{row['mc_median']:.6f} | {row['qmc_median']:.6f} | "
            f"{row['mc_percentile_rank_of_qpu']:.3f} | "
            f"{row['qmc_percentile_rank_of_qpu']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## Quantity-of-Interest Errors",
            "",
            "| Case | Quantity | QPU error | MC RMSE | QMC RMSE | QPU/MC | QPU/QMC |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in sorted(qoi_rows, key=lambda item: item["qpu_abs_error"], reverse=True):
        lines.append(
            f"| `{row['case_id']}` | `{row['quantity']}` | {row['qpu_abs_error']:.6f} | "
            f"{row['mc_rmse']:.6f} | {row['qmc_rmse']:.6f} | "
            f"{row['qpu_abs_error_over_mc_rmse']:.2f} | "
            f"{row['qpu_abs_error_over_qmc_rmse']:.2f} |"
        )
    summary_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote JSON: {dist_json}")
    print(f"Wrote CSV:  {dist_csv}")
    print(f"Wrote JSON: {qoi_json}")
    print(f"Wrote CSV:  {qoi_csv}")
    print(f"Wrote summary: {summary_md}")
    print()
    print("Distribution TVD comparison:")
    for row in distribution_rows:
        if row["metric"] == "tvd":
            print(
                f"  {row['case_id']:<18} "
                f"QPU={row['qpu_value']:.6f} "
                f"MC_med={row['mc_median']:.6f} "
                f"QMC_med={row['qmc_median']:.6f} "
                f"rank_MC={row['mc_percentile_rank_of_qpu']:.3f} "
                f"rank_QMC={row['qmc_percentile_rank_of_qpu']:.3f}"
            )
    print()
    print("Largest QPU quantity-of-interest errors:")
    for row in sorted(qoi_rows, key=lambda item: item["qpu_abs_error"], reverse=True)[:10]:
        print(
            f"  {row['case_id']:<18} {row['quantity']:<18} "
            f"QPU_err={row['qpu_abs_error']:.6f} "
            f"MC_RMSE={row['mc_rmse']:.6f} "
            f"QMC_RMSE={row['qmc_rmse']:.6f} "
            f"QPU/MC={row['qpu_abs_error_over_mc_rmse']:.2f} "
            f"QPU/QMC={row['qpu_abs_error_over_qmc_rmse']:.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
