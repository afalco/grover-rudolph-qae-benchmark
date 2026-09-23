#!/usr/bin/env python3
"""Phase 17 local Grover-Rudolph angle-precision versus shot-noise diagnostic.

The AIMS Grover-Rudolph stability result separates deterministic rotation-angle
perturbations from stochastic sampling error in total variation distance (TVD).
This script mirrors that separation for the probability-law preparation cases
used in the project:

1. exact target law p;
2. law p_b obtained by quantizing every physical Ry angle to a b-bit grid;
3. empirical law sampled from p;
4. empirical law sampled from p_b.

No QPU credits are used.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from grover_rudolph_qae_benchmark.gr_state_preparation import (  # noqa: E402
    GRStageAngle,
    dyadic_stage_angles,
    spec_from_config,
    total_variation_distance,
)


DEFAULT_CONFIG = PROJECT_ROOT / "experiments/phase17_gr_angle_precision_diagnostic.json"


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def quantize_physical_ry_angle(theta: float, bit_depth: int) -> float:
    """Quantize a physical Ry angle in [0, pi] using the AIMS b-bit convention."""
    if bit_depth <= 1:
        raise ValueError("bit_depth must be greater than 1.")
    mesh = math.pi / (2 ** (bit_depth - 1))
    return min(max(round(theta / mesh) * mesh, 0.0), math.pi)


def stage_angle_lookup(angles: list[GRStageAngle]) -> dict[tuple[int, tuple[int, ...]], float]:
    return {(angle.level, angle.prefix): float(angle.theta) for angle in angles}


def distribution_from_angles(
    *,
    num_qubits: int,
    angles: dict[tuple[int, tuple[int, ...]], float],
) -> list[float]:
    probabilities = []
    for index in range(2**num_qubits):
        bits = tuple(int(bit) for bit in format(index, f"0{num_qubits}b"))
        probability = 1.0
        for level, bit in enumerate(bits):
            theta = angles[(level, bits[:level])]
            right_probability = math.sin(theta / 2.0) ** 2
            probability *= right_probability if bit else 1.0 - right_probability
        probabilities.append(probability)
    total = sum(probabilities)
    if total > 0.0:
        probabilities = [value / total for value in probabilities]
    return probabilities


def quantized_distribution(angles: list[GRStageAngle], *, num_qubits: int, bit_depth: int) -> list[float]:
    quantized = {
        (angle.level, angle.prefix): quantize_physical_ry_angle(angle.theta, bit_depth)
        for angle in angles
    }
    return distribution_from_angles(num_qubits=num_qubits, angles=quantized)


def sample_tvd_values(
    distribution: list[float],
    target: list[float],
    *,
    shots: int,
    trials: int,
    rng: np.random.Generator,
) -> list[float]:
    samples = rng.multinomial(shots, np.asarray(distribution, dtype=float), size=trials)
    empirical = samples / float(shots)
    target_array = np.asarray(target, dtype=float).reshape(1, len(target))
    tvd = 0.5 * np.abs(empirical - target_array).sum(axis=1)
    return tvd.astype(float).tolist()


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def stddev(values: list[float]) -> float:
    if len(values) < 2:
        return float("nan")
    average = mean(values)
    return math.sqrt(sum((value - average) ** 2 for value in values) / (len(values) - 1))


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


def summarize_samples(values: list[float]) -> dict[str, float]:
    return {
        "mean": mean(values),
        "std": stddev(values),
        "p50": percentile(values, 0.50),
        "p90": percentile(values, 0.90),
        "p95": percentile(values, 0.95),
        "p975": percentile(values, 0.975),
    }


def angle_bound(num_qubits: int, bit_depth: int) -> float:
    # AIMS convention: physical Ry mesh pi/2^(b-1), so Grover-Rudolph half-angle
    # perturbation is at most pi/2^(b+1), giving TVD <= n*pi/2^(b+1).
    return min(1.0, num_qubits * math.pi / (2 ** (bit_depth + 1)))


def shot_bound(num_qubits: int, shots: int, delta: float) -> float:
    return min(1.0, math.sqrt((2**num_qubits) * math.log(2.0 / delta) / (2.0 * shots)))


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    keys = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def write_summary(rows: list[dict[str, Any]], path: Path) -> None:
    lines = [
        "# Phase 17 Grover-Rudolph Angle Precision Diagnostic",
        "",
        "This credit-free diagnostic separates deterministic physical-angle",
        "quantization from stochastic finite-shot error in total variation distance.",
        "",
        "| Case | n | b | shots | angle TVD | exact-shot TVD | combined TVD | AIMS bound |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    compact = [
        row for row in rows
        if int(row["bit_depth"]) in {8, 16, 32}
        and int(row["shots"]) in {4096, 8192}
    ]
    for row in compact:
        lines.append(
            f"| `{row['case_id']}` | {row['num_qubits']} | {row['bit_depth']} | "
            f"{row['shots']} | {row['angle_quantization_tvd']:.6g} | "
            f"{row['exact_angle_shot_tvd_mean']:.6g} | "
            f"{row['combined_tvd_mean']:.6g} | {row['combined_aims_bound']:.6g} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Separate Grover-Rudolph angle quantization TVD from shot-noise TVD."
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--cases", nargs="+", default=None)
    parser.add_argument("--bit-depths", nargs="+", type=int, default=None)
    parser.add_argument("--shots", nargs="+", type=int, default=None)
    parser.add_argument("--trials", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = resolve_path(args.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    out_dir = resolve_path(args.out_dir or config.get("out_dir"))
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    selected_cases = set(args.cases or [])
    bit_depths = [int(value) for value in (args.bit_depths or config.get("bit_depths", [8, 16, 32]))]
    shot_values = [int(value) for value in (args.shots or config.get("shots", [4096]))]
    trials = int(args.trials if args.trials is not None else config.get("trials", 1000))
    seed = int(args.seed if args.seed is not None else config.get("seed", 20260812))
    delta = float(config.get("confidence_delta", 0.05))
    rng = np.random.default_rng(seed)

    rows: list[dict[str, Any]] = []
    for case in config.get("cases", []):
        case_id = str(case["case_id"])
        if selected_cases and case_id not in selected_cases:
            continue
        spec = spec_from_config(case)
        target = list(spec.probabilities)
        exact_angles = dyadic_stage_angles(spec)
        exact_from_angles = distribution_from_angles(
            num_qubits=spec.num_qubits,
            angles=stage_angle_lookup(exact_angles),
        )
        reconstruction_tvd = total_variation_distance(target, exact_from_angles)
        for bit_depth in bit_depths:
            p_quantized = quantized_distribution(
                exact_angles,
                num_qubits=spec.num_qubits,
                bit_depth=bit_depth,
            )
            quantization_tvd = total_variation_distance(target, p_quantized)
            deterministic_bound = angle_bound(spec.num_qubits, bit_depth)
            for shots in shot_values:
                exact_shot_samples = sample_tvd_values(
                    target,
                    target,
                    shots=shots,
                    trials=trials,
                    rng=rng,
                )
                combined_samples = sample_tvd_values(
                    p_quantized,
                    target,
                    shots=shots,
                    trials=trials,
                    rng=rng,
                )
                exact_summary = summarize_samples(exact_shot_samples)
                combined_summary = summarize_samples(combined_samples)
                stochastic_bound = shot_bound(spec.num_qubits, shots, delta)
                rows.append(
                    {
                        "experiment_id": config.get("experiment_id"),
                        "case_id": case_id,
                        "label": spec.label,
                        "num_qubits": spec.num_qubits,
                        "grid_points": spec.grid_points,
                        "source": spec.source,
                        "bit_depth": bit_depth,
                        "shots": shots,
                        "trials": trials,
                        "confidence_delta": delta,
                        "angle_count": len(exact_angles),
                        "nonzero_angle_count": sum(1 for angle in exact_angles if abs(angle.theta) > 1e-15),
                        "reconstruction_tvd": reconstruction_tvd,
                        "angle_quantization_tvd": quantization_tvd,
                        "angle_bound": deterministic_bound,
                        "shot_bound": stochastic_bound,
                        "combined_aims_bound": min(1.0, deterministic_bound + stochastic_bound),
                        "exact_angle_shot_tvd_mean": exact_summary["mean"],
                        "exact_angle_shot_tvd_std": exact_summary["std"],
                        "exact_angle_shot_tvd_p50": exact_summary["p50"],
                        "exact_angle_shot_tvd_p95": exact_summary["p95"],
                        "combined_tvd_mean": combined_summary["mean"],
                        "combined_tvd_std": combined_summary["std"],
                        "combined_tvd_p50": combined_summary["p50"],
                        "combined_tvd_p95": combined_summary["p95"],
                        "angle_tvd_over_exact_shot_mean": quantization_tvd / exact_summary["mean"]
                        if exact_summary["mean"] > 0
                        else float("inf"),
                        "combined_over_exact_shot_mean": combined_summary["mean"] / exact_summary["mean"]
                        if exact_summary["mean"] > 0
                        else float("inf"),
                        "notes": spec.notes,
                    }
                )

    rows.sort(key=lambda row: (row["case_id"], int(row["bit_depth"]), int(row["shots"])))
    json_path = out_dir / f"phase17_gr_angle_precision_diagnostic_{timestamp}.json"
    csv_path = out_dir / f"phase17_gr_angle_precision_diagnostic_{timestamp}.csv"
    summary_path = out_dir / f"phase17_gr_angle_precision_diagnostic_{timestamp}.md"
    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(rows, csv_path)
    write_summary(rows, summary_path)

    print(f"Wrote JSON:    {json_path}")
    print(f"Wrote CSV:     {csv_path}")
    print(f"Wrote summary: {summary_path}")
    print()
    print("Phase 17 angle precision versus shot noise:")
    for row in rows:
        if int(row["bit_depth"]) == 8 and int(row["shots"]) == 4096:
            print(
                f"  {row['case_id']:<26} n={row['num_qubits']} "
                f"angle_TVD={row['angle_quantization_tvd']:.6g} "
                f"shot_TVD={row['exact_angle_shot_tvd_mean']:.6g} "
                f"combined={row['combined_tvd_mean']:.6g} "
                f"angle/shot={row['angle_tvd_over_exact_shot_mean']:.2f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
