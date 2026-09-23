#!/usr/bin/env python3
"""Analyze existing Grover-Rudolph MLAE counts without new QPU use."""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from grover_rudolph_qae_benchmark.gr_state_preparation import (
    bitstring_to_probability_index,
    build_gr_event_amplification_circuit,
    spec_from_config,
    total_variation_distance,
)


DEFAULT_RUNS = [
    PROJECT_ROOT
    / "results/hardware/phase4_gr_affine_n4_mlae_k01_ibm_fez/20260805T151942Z/fetched_gr_mlae_results.json",
    PROJECT_ROOT
    / "results/hardware/phase4_gr_affine_n4_k1_repeat_opt3_seed12345_ibm_fez/20260805T161506Z/fetched_gr_mlae_results.json",
]


def load_rows(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("rows", [])


def observed_distribution(row: dict[str, Any]) -> list[float]:
    counts = row["counts"]
    grid_points = int(row["grid_points"])
    shots = sum(int(value) for value in counts.values())
    out = [0.0] * grid_points
    if shots == 0:
        return out
    for bitstring, count in counts.items():
        out[bitstring_to_probability_index(str(bitstring))] += int(count) / shots
    return out


def ideal_amplified_distribution(row: dict[str, Any]) -> list[float] | None:
    try:
        from qiskit.quantum_info import Statevector
    except ImportError:
        return None
    spec = spec_from_config(
        {
            "case_id": row["case_id"],
            "label": row.get("label", row["case_id"]),
            "num_qubits": int(row["num_qubits"]),
            "probabilities": row["target_probabilities"],
        }
    )
    circuit = build_gr_event_amplification_circuit(
        spec,
        int(row["k"]),
        event=str(row["event"]),
        measure=False,
        implementation=str(row.get("implementation", "ucry")),
        reflection_method=str(row.get("reflection_method", "mcx")),
    )
    distribution = [0.0] * spec.grid_points
    for bitstring, probability in Statevector.from_instruction(circuit).probabilities_dict().items():
        distribution[bitstring_to_probability_index(str(bitstring))] += float(probability)
    return distribution


def signed_contributions(row: dict[str, Any]) -> list[dict[str, Any]]:
    observed = observed_distribution(row)
    target = [float(value) for value in row["target_probabilities"]]
    ideal_amplified = ideal_amplified_distribution(row)
    marked = set(int(index) for index in row["marked_indices"])
    rows = []
    for index, (obs, tgt) in enumerate(zip(observed, target)):
        amp = ideal_amplified[index] if ideal_amplified is not None else None
        rows.append(
            {
                "case_id": row["case_id"],
                "k": row["k"],
                "optimization_level": row["optimization_level"],
                "index": index,
                "bitstring_big_endian": format(index, f"0{int(row['num_qubits'])}b"),
                "marked": index in marked,
                "observed": obs,
                "target_unamplified": tgt,
                "ideal_amplified": amp,
                "observed_minus_target_unamplified": obs - tgt,
                "observed_minus_ideal_amplified": obs - amp if amp is not None else None,
            }
        )
    return rows


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    keys = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def paired_half_rows(summary: dict[str, Any], observed: list[float], ideal: list[float]) -> list[dict[str, Any]]:
    half = len(observed) // 2
    rows = []
    for lower in range(half):
        upper = lower + half
        lower_delta = observed[lower] - ideal[lower]
        upper_delta = observed[upper] - ideal[upper]
        rows.append(
            {
                "source": summary["source"],
                "case_id": summary["case_id"],
                "k": summary["k"],
                "optimization_level": summary["optimization_level"],
                "lower_index": lower,
                "upper_index": upper,
                "lower_bitstring": format(lower, "04b"),
                "upper_bitstring": format(upper, "04b"),
                "lower_observed": observed[lower],
                "lower_ideal_amplified": ideal[lower],
                "lower_delta": lower_delta,
                "upper_observed": observed[upper],
                "upper_ideal_amplified": ideal[upper],
                "upper_delta": upper_delta,
                "pair_observed": observed[lower] + observed[upper],
                "pair_ideal_amplified": ideal[lower] + ideal[upper],
                "pair_delta": (observed[lower] + observed[upper]) - (ideal[lower] + ideal[upper]),
                "apparent_lower_to_upper_transfer": min(-lower_delta, upper_delta)
                if lower_delta < 0 and upper_delta > 0
                else 0.0,
            }
        )
    return rows


def fit_event_bit_flip(observed: list[float], ideal: list[float]) -> dict[str, float]:
    """Fit a two-parameter event-bit flip model between lower/upper halves.

    For each pair j and j+N/2:

      obs_lower = (1-alpha) ideal_lower + beta ideal_upper
      obs_upper = alpha ideal_lower + (1-beta) ideal_upper

    where alpha is lower->upper leakage and beta is upper->lower leakage.
    """
    half = len(observed) // 2
    # Least squares for y = A [alpha, beta].
    ata00 = ata01 = ata11 = 0.0
    aty0 = aty1 = 0.0
    for lower in range(half):
        upper = lower + half
        il = ideal[lower]
        iu = ideal[upper]
        # upper equation: obs_upper - ideal_upper = alpha*il - beta*iu
        y = observed[upper] - iu
        a0 = il
        a1 = -iu
        ata00 += a0 * a0
        ata01 += a0 * a1
        ata11 += a1 * a1
        aty0 += a0 * y
        aty1 += a1 * y
        # lower equation: obs_lower - ideal_lower = -alpha*il + beta*iu
        y = observed[lower] - il
        a0 = -il
        a1 = iu
        ata00 += a0 * a0
        ata01 += a0 * a1
        ata11 += a1 * a1
        aty0 += a0 * y
        aty1 += a1 * y

    det = ata00 * ata11 - ata01 * ata01
    if abs(det) < 1e-15:
        alpha = beta = 0.0
    else:
        alpha = (aty0 * ata11 - aty1 * ata01) / det
        beta = (ata00 * aty1 - ata01 * aty0) / det
    alpha = min(max(alpha, 0.0), 0.5)
    beta = min(max(beta, 0.0), 0.5)

    predicted = [0.0] * len(observed)
    corrected = [0.0] * len(observed)
    denom = (1 - alpha) * (1 - beta) - alpha * beta
    for lower in range(half):
        upper = lower + half
        il = ideal[lower]
        iu = ideal[upper]
        predicted[lower] = (1 - alpha) * il + beta * iu
        predicted[upper] = alpha * il + (1 - beta) * iu

        ol = observed[lower]
        ou = observed[upper]
        if abs(denom) < 1e-15:
            corrected[lower] = ol
            corrected[upper] = ou
        else:
            corrected[lower] = ((1 - beta) * ol - beta * ou) / denom
            corrected[upper] = (-(alpha) * ol + (1 - alpha) * ou) / denom

    corrected = [max(0.0, value) for value in corrected]
    norm = sum(corrected)
    if norm > 0:
        corrected = [value / norm for value in corrected]

    observed_marked = sum(observed[half:])
    ideal_marked = sum(ideal[half:])
    predicted_marked = sum(predicted[half:])
    corrected_marked = sum(corrected[half:])
    return {
        "event_flip_lower_to_upper": alpha,
        "event_flip_upper_to_lower": beta,
        "observed_marked": observed_marked,
        "ideal_marked": ideal_marked,
        "predicted_marked_from_flip_model": predicted_marked,
        "corrected_marked": corrected_marked,
        "observed_abs_dev": abs(observed_marked - ideal_marked),
        "corrected_abs_dev": abs(corrected_marked - ideal_marked),
        "model_tvd_to_observed": total_variation_distance(predicted, observed),
        "corrected_tvd_to_ideal": total_variation_distance(corrected, ideal),
    }


def quantile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def multinomial_sample(rng: random.Random, probabilities: list[float], shots: int) -> list[float]:
    cumulative = []
    total = 0.0
    for probability in probabilities:
        total += probability
        cumulative.append(total)
    counts = [0] * len(probabilities)
    for _ in range(shots):
        u = rng.random() * total
        for index, cutoff in enumerate(cumulative):
            if u <= cutoff:
                counts[index] += 1
                break
    return [count / shots for count in counts]


def bootstrap_event_bit_flip(
    observed: list[float],
    ideal: list[float],
    *,
    shots: int,
    repetitions: int,
    seed: int,
) -> dict[str, float]:
    rng = random.Random(seed)
    alpha_values = []
    beta_values = []
    corrected_values = []
    corrected_dev_values = []
    tvd_values = []
    for _ in range(repetitions):
        sample = multinomial_sample(rng, observed, shots)
        fit = fit_event_bit_flip(sample, ideal)
        alpha_values.append(fit["event_flip_lower_to_upper"])
        beta_values.append(fit["event_flip_upper_to_lower"])
        corrected_values.append(fit["corrected_marked"])
        corrected_dev_values.append(fit["corrected_abs_dev"])
        tvd_values.append(total_variation_distance(sample, ideal))
    return {
        "bootstrap_repetitions": repetitions,
        "bootstrap_seed": seed,
        "event_flip_lower_to_upper_ci_low": quantile(alpha_values, 0.025),
        "event_flip_lower_to_upper_ci_high": quantile(alpha_values, 0.975),
        "event_flip_upper_to_lower_ci_low": quantile(beta_values, 0.025),
        "event_flip_upper_to_lower_ci_high": quantile(beta_values, 0.975),
        "corrected_marked_ci_low": quantile(corrected_values, 0.025),
        "corrected_marked_ci_high": quantile(corrected_values, 0.975),
        "corrected_abs_dev_ci_low": quantile(corrected_dev_values, 0.025),
        "corrected_abs_dev_ci_high": quantile(corrected_dev_values, 0.975),
        "tvd_to_ideal_ci_low": quantile(tvd_values, 0.025),
        "tvd_to_ideal_ci_high": quantile(tvd_values, 0.975),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze GR MLAE count artifacts.")
    parser.add_argument("--runs", nargs="*", default=[str(path) for path in DEFAULT_RUNS])
    parser.add_argument("--out-dir", default="results/phase4/postprocessing")
    parser.add_argument("--bootstrap-repetitions", type=int, default=500)
    parser.add_argument("--bootstrap-seed", type=int, default=12345)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_paths = []
    for text in args.runs:
        path = Path(text)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        run_paths.append(path)

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    summary_rows = []
    cell_rows = []
    pair_rows = []
    flip_rows = []
    k1_distributions = []
    for path in run_paths:
        for row in load_rows(path):
            if row.get("p_hat") is None:
                continue
            observed = observed_distribution(row)
            target = [float(value) for value in row["target_probabilities"]]
            ideal_amplified = ideal_amplified_distribution(row)
            marked = set(int(index) for index in row["marked_indices"])
            marked_mass = sum(observed[index] for index in marked)
            unmarked_mass = 1.0 - marked_mass
            target_marked = sum(target[index] for index in marked)
            tvd_to_unamplified = total_variation_distance(observed, target)
            tvd_to_ideal_amplified = (
                total_variation_distance(observed, ideal_amplified)
                if ideal_amplified is not None
                else None
            )
            summary = {
                "source": str(path),
                "campaign_id": row["campaign_id"],
                "case_id": row["case_id"],
                "k": int(row["k"]),
                "optimization_level": int(row["optimization_level"]),
                "depth": int(row["transpiled_metrics"]["depth"]),
                "twoq": int(row["transpiled_metrics"]["two_qubit_gate_count"]),
                "shots": int(row["shots"]),
                "expected_p_k": float(row["expected_p_k"]),
                "p_hat": float(row["p_hat"]),
                "p_abs_dev": float(row["p_abs_dev"]),
                "unamplified_marked_mass": target_marked,
                "observed_marked_mass": marked_mass,
                "observed_unmarked_mass": unmarked_mass,
                "tvd_to_unamplified_distribution": tvd_to_unamplified,
                "tvd_to_ideal_amplified_distribution": tvd_to_ideal_amplified,
            }
            summary_rows.append(summary)
            cell_rows.extend(
                {**item, "source": str(path)} for item in signed_contributions(row)
            )
            if ideal_amplified is not None:
                pair_rows.extend(paired_half_rows(summary, observed, ideal_amplified))
                flip_rows.append(
                    {
                        **{
                            key: summary[key]
                            for key in (
                                "source",
                                "campaign_id",
                                "case_id",
                                "k",
                                "optimization_level",
                                "depth",
                                "twoq",
                            )
                        },
                        **fit_event_bit_flip(observed, ideal_amplified),
                        **bootstrap_event_bit_flip(
                            observed,
                            ideal_amplified,
                            shots=int(row["shots"]),
                            repetitions=int(args.bootstrap_repetitions),
                            seed=int(args.bootstrap_seed) + int(row["k"]) * 100 + int(row["optimization_level"]),
                        ),
                    }
                )
            if int(row["k"]) == 1:
                k1_distributions.append((summary, observed))

    comparisons = []
    for i in range(len(k1_distributions)):
        for j in range(i + 1, len(k1_distributions)):
            left, left_dist = k1_distributions[i]
            right, right_dist = k1_distributions[j]
            comparisons.append(
                {
                    "left_source": left["source"],
                    "right_source": right["source"],
                    "left_opt": left["optimization_level"],
                    "right_opt": right["optimization_level"],
                    "left_p_hat": left["p_hat"],
                    "right_p_hat": right["p_hat"],
                    "p_hat_delta": right["p_hat"] - left["p_hat"],
                    "distribution_tvd_between_runs": total_variation_distance(
                        left_dist, right_dist
                    ),
                }
            )

    summary_path = out_dir / "gr_mlae_counts_summary.csv"
    cells_path = out_dir / "gr_mlae_counts_cells.csv"
    pairs_path = out_dir / "gr_mlae_counts_pairs.csv"
    flips_path = out_dir / "gr_mlae_event_bit_flip.csv"
    comparisons_path = out_dir / "gr_mlae_counts_comparisons.csv"
    json_path = out_dir / "gr_mlae_counts_analysis.json"
    write_csv(summary_rows, summary_path)
    write_csv(cell_rows, cells_path)
    write_csv(pair_rows, pairs_path)
    write_csv(flip_rows, flips_path)
    write_csv(comparisons, comparisons_path)
    json_path.write_text(
        json.dumps(
            {
                "runs": [str(path) for path in run_paths],
                "summary": summary_rows,
                "comparisons": comparisons,
                "event_bit_flip": flip_rows,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    print(f"Wrote JSON: {json_path}")
    print(f"Wrote summary: {summary_path}")
    print(f"Wrote cells:   {cells_path}")
    print(f"Wrote pairs:   {pairs_path}")
    print(f"Wrote flips:   {flips_path}")
    print(f"Wrote compare: {comparisons_path}")
    print()
    print("Summary:")
    for row in summary_rows:
        print(
            f"  k={row['k']} opt={row['optimization_level']} "
            f"p_hat={row['p_hat']:.6f} expected={row['expected_p_k']:.6f} "
            f"dev={row['p_abs_dev']:.6f} "
            f"tvd_ideal_amp={row['tvd_to_ideal_amplified_distribution']:.6f}"
        )
    for row in comparisons:
        print(
            f"  k=1 opt {row['left_opt']} -> {row['right_opt']}: "
            f"delta_p={row['p_hat_delta']:.6f} "
            f"dist_tvd={row['distribution_tvd_between_runs']:.6f}"
        )
    if pair_rows:
        print()
        print("Largest apparent lower-to-upper transfers:")
        for row in sorted(
            pair_rows,
            key=lambda item: float(item["apparent_lower_to_upper_transfer"]),
            reverse=True,
        )[:6]:
            if float(row["apparent_lower_to_upper_transfer"]) <= 0:
                continue
            print(
                f"  k={row['k']} opt={row['optimization_level']} "
                f"{row['lower_bitstring']}->{row['upper_bitstring']} "
                f"transfer={row['apparent_lower_to_upper_transfer']:.6f} "
                f"lower_delta={row['lower_delta']:.6f} "
                f"upper_delta={row['upper_delta']:.6f}"
            )
    if flip_rows:
        print()
        print("Event-bit flip model:")
        for row in flip_rows:
            print(
                f"  k={row['k']} opt={row['optimization_level']} "
                f"L->U={row['event_flip_lower_to_upper']:.4f} "
                f"U->L={row['event_flip_upper_to_lower']:.4f} "
                f"p_obs={row['observed_marked']:.6f} "
                f"p_corr={row['corrected_marked']:.6f} "
                f"p_corr_ci=[{row['corrected_marked_ci_low']:.6f},{row['corrected_marked_ci_high']:.6f}] "
                f"ideal={row['ideal_marked']:.6f} "
                f"dev_corr={row['corrected_abs_dev']:.6f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
