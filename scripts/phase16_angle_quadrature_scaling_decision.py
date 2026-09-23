#!/usr/bin/env python3
"""Phase 16 scaling decision table for angle-structured quadrature QAE.

This is a credit-free decision layer over the Phase 15 quadrature-QAE audit.
It promotes only rows whose circuit cost remains controlled and whose noisy
prediction is competitive with a matched Monte Carlo baseline. Randomized QMC
is reported as a mandatory strict baseline, but it is not used as the sole veto
because the current test set is one-dimensional and smooth.
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
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from phase15_quadrature_qae_audit import function_value, true_integral  # noqa: E402
from phase15c_quadrature_sampling_baselines import (  # noqa: E402
    function_values_array,
    qmc_estimates_vectorized,
    rmse,
)
from grover_rudolph_qae_benchmark.angle_structure import classify_values  # noqa: E402


DEFAULT_CONFIG = PROJECT_ROOT / "experiments/phase16_angle_quadrature_scaling_decision.json"


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def load_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def load_cases(path: Path) -> dict[str, dict[str, Any]]:
    config = json.loads(path.read_text(encoding="utf-8"))
    return {str(case["case_id"]): dict(case) for case in config["cases"]}


def as_float(value: Any, default: float = float("nan")) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def integrate_power(case: dict[str, Any], power: int, panels: int = 65536) -> float:
    if panels % 2:
        panels += 1
    h = 1.0 / panels

    def value(x: float) -> float:
        return function_value(case, x) ** power

    total = value(0.0) + value(1.0)
    odd = 0.0
    even = 0.0
    for idx in range(1, panels):
        y = value(idx * h)
        if idx % 2:
            odd += y
        else:
            even += y
    return h * (total + 4.0 * odd + 2.0 * even) / 3.0


def mc_rmse(case: dict[str, Any], samples: int) -> float:
    exact = true_integral(case)
    second = integrate_power(case, 2)
    variance = max(0.0, second - exact * exact)
    return math.sqrt(variance / samples) if samples > 0 else float("nan")


def qmc_rmse_for_case(
    case: dict[str, Any],
    *,
    samples: int,
    trials: int,
    rng: np.random.Generator,
) -> float:
    exact = true_integral(case)
    estimates = qmc_estimates_vectorized(case, trials=trials, samples=samples, rng=rng)
    return rmse([estimate - exact for estimate in estimates])


def quadrature_nodes(rule: str, num_qubits: int) -> list[float]:
    grid_points = 2**num_qubits
    if rule == "left":
        return [idx / grid_points for idx in range(grid_points)]
    if rule == "midpoint":
        return [(idx + 0.5) / grid_points for idx in range(grid_points)]
    if rule == "right":
        return [(idx + 1.0) / grid_points for idx in range(grid_points)]
    raise ValueError(f"Angle classification supports direct rules, not {rule!r}.")


def angle_classification(
    case_id: str,
    case: dict[str, Any],
    *,
    rule: str,
    num_qubits: int,
    tolerance: float,
) -> tuple[int, str, int]:
    values = [function_value(case, x) for x in quadrature_nodes(rule, num_qubits)]
    classification = classify_values(
        case_id,
        values,
        num_qubits=num_qubits,
        grid=rule,
        input_kind="qae_function",
        tolerance=tolerance,
    )
    return classification.degree, f"G_{num_qubits}^{classification.degree}", classification.support


def choose_decision(
    *,
    row: dict[str, Any],
    predicted_error: float,
    mc_error: float,
    config: dict[str, Any],
) -> str:
    depth = as_int(row.get("max_depth"))
    twoq = as_int(row.get("max_two_qubit_gates"))
    if depth > int(config["max_depth"]):
        return "reject_depth"
    if twoq > int(config["max_two_qubit_gates"]):
        return "reject_twoq"
    if predicted_error > float(config["max_noisy_total_error"]):
        return "defer_noisy_error"
    if predicted_error > float(config["max_qpu_over_mc_rmse"]) * mc_error:
        return "defer_mc_dominated"
    return "candidate_for_qpu_microcampaign"


def build_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    audit_rows = load_csv(resolve_path(config["phase15_audit_csv"]))
    cases = load_cases(resolve_path(config["phase15_config"]))
    selected_rules = set(str(rule) for rule in config.get("rules", []))
    budget_labels = [str(label) for label in config.get("budget_labels", ["shots"])]
    qmc_trials = int(config.get("qmc_trials", 1000))
    angle_tolerance = float(config.get("angle_tolerance", 1e-10))
    rng = np.random.default_rng(int(config.get("seed", 20260812)))
    qmc_cache: dict[tuple[str, int], float] = {}
    mc_cache: dict[tuple[str, int], float] = {}
    angle_cache: dict[tuple[str, str, int], tuple[int, str, int]] = {}
    rows: list[dict[str, Any]] = []

    for audit in audit_rows:
        if selected_rules and str(audit.get("rule")) not in selected_rules:
            continue
        case_id = str(audit["case_id"])
        case = cases[case_id]
        rule = str(audit.get("rule"))
        num_qubits = as_int(audit.get("num_qubits"))
        angle_key = (case_id, rule, num_qubits)
        if angle_key not in angle_cache:
            angle_cache[angle_key] = angle_classification(
                case_id,
                case,
                rule=rule,
                num_qubits=num_qubits,
                tolerance=angle_tolerance,
            )
        angle_degree, angle_class, angle_support = angle_cache[angle_key]
        predicted_error = as_float(audit.get("noisy_total_error"))
        if math.isnan(predicted_error):
            predicted_error = as_float(audit.get("total_error"))
        for budget_label in budget_labels:
            if budget_label == "shots":
                samples = as_int(audit.get("total_shots"))
            elif budget_label == "oracle_queries":
                samples = as_int(audit.get("oracle_queries"))
            else:
                raise ValueError(f"Unknown budget label {budget_label!r}")
            cache_key = (case_id, samples)
            if cache_key not in mc_cache:
                mc_cache[cache_key] = mc_rmse(case, samples)
            depth = as_int(audit.get("max_depth"))
            twoq = as_int(audit.get("max_two_qubit_gates"))
            cost_controlled = depth <= int(config["max_depth"]) and twoq <= int(config["max_two_qubit_gates"])
            if cost_controlled:
                if cache_key not in qmc_cache:
                    qmc_cache[cache_key] = qmc_rmse_for_case(
                        case,
                        samples=samples,
                        trials=qmc_trials,
                        rng=rng,
                    )
                qmc_error = qmc_cache[cache_key]
            else:
                qmc_error = float("nan")
            decision = choose_decision(
                row=audit,
                predicted_error=predicted_error,
                mc_error=mc_cache[cache_key],
                config=config,
            )
            rows.append(
                {
                    "experiment_id": config.get("experiment_id"),
                    "backend": audit.get("backend"),
                    "case_id": case_id,
                    "family": audit.get("family"),
                    "angle_class_hint": audit.get("angle_class_hint"),
                    "angle_degree": angle_degree,
                    "angle_class": angle_class,
                    "angle_support": angle_support,
                    "num_qubits": num_qubits,
                    "grid_points": as_int(audit.get("grid_points")),
                    "rule": audit.get("rule"),
                    "schedule": audit.get("schedule"),
                    "budget_label": budget_label,
                    "classical_samples": samples,
                    "qmc_trials": qmc_trials if cost_controlled else 0,
                    "true_integral": as_float(audit.get("true_integral")),
                    "quadrature_value": as_float(audit.get("quadrature_value")),
                    "discretization_error": as_float(audit.get("discretization_error")),
                    "ideal_mlae_total_error": as_float(audit.get("total_error")),
                    "noisy_mlae_total_error": as_float(audit.get("noisy_total_error")),
                    "predicted_qpu_total_error": predicted_error,
                    "mc_rmse": mc_cache[cache_key],
                    "qmc_rmse": qmc_error,
                    "qpu_over_mc_rmse": predicted_error / mc_cache[cache_key]
                    if mc_cache[cache_key] > 0
                    else float("inf"),
                    "qpu_over_qmc_rmse": predicted_error / qmc_error
                    if qmc_error > 0
                    else float("inf"),
                    "max_depth": depth,
                    "max_two_qubit_gates": twoq,
                    "max_two_qubit_depth": as_int(audit.get("max_two_qubit_depth")),
                    "total_circuits": as_int(audit.get("total_circuits")),
                    "total_shots": as_int(audit.get("total_shots")),
                    "oracle_queries": as_int(audit.get("oracle_queries")),
                    "optimization_level": as_int(audit.get("optimization_level")),
                    "transpiler_seed": as_int(audit.get("transpiler_seed")),
                    "phase15_status": audit.get("status"),
                    "decision": decision,
                }
            )
    rows.sort(
        key=lambda row: (
            row["budget_label"] != "shots",
            row["decision"] != "candidate_for_qpu_microcampaign",
            float(row["qpu_over_mc_rmse"]),
            int(row["max_two_qubit_gates"]),
            int(row["max_depth"]),
        )
    )
    return rows


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    keys = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def write_summary(rows: list[dict[str, Any]], path: Path) -> None:
    shot_rows = [row for row in rows if row["budget_label"] == "shots"]
    lines = [
        "# Phase 16 Angle-Structured Quadrature Scaling Decision",
        "",
        "Rows are promoted only when circuit cost is controlled, noisy simulation is",
        "below the configured error threshold, and the predicted QPU error is not",
        "larger than the matched MC RMSE at the same shot budget. QMC is reported as",
        "a mandatory strict baseline.",
        "",
        "| Decision | Case | Class | n | K | Depth | 2q | Pred. QPU | MC RMSE | QMC RMSE | QPU/MC |",
        "|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in shot_rows:
        lines.append(
            f"| `{row['decision']}` | `{row['case_id']}` | `{row['angle_class']}` | "
            f"{row['num_qubits']} | "
            f"`{row['schedule']}` | {row['max_depth']} | {row['max_two_qubit_gates']} | "
            f"{row['predicted_qpu_total_error']:.6f} | {row['mc_rmse']:.6f} | "
            f"{row['qmc_rmse']:.6f} | {row['qpu_over_mc_rmse']:.2f} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build Phase 16 quadrature-QAE scaling decision table."
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--out-dir", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = resolve_path(args.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    out_dir = resolve_path(args.out_dir or config.get("out_dir"))
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rows = build_rows(config)
    json_path = out_dir / f"phase16_angle_quadrature_scaling_decision_{timestamp}.json"
    csv_path = out_dir / f"phase16_angle_quadrature_scaling_decision_{timestamp}.csv"
    summary_path = out_dir / f"phase16_angle_quadrature_scaling_decision_{timestamp}.md"
    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(rows, csv_path)
    write_summary(rows, summary_path)

    print(f"Wrote JSON:    {json_path}")
    print(f"Wrote CSV:     {csv_path}")
    print(f"Wrote summary: {summary_path}")
    print()
    print("Phase 16 shot-budget candidates:")
    for row in rows:
        if row["budget_label"] != "shots":
            continue
        if row["decision"] != "candidate_for_qpu_microcampaign":
            continue
        print(
            f"  {row['case_id']:<20} n={row['num_qubits']} K={row['schedule']:<9} "
            f"class={row['angle_class']:<6} "
            f"depth={row['max_depth']:>3} 2q={row['max_two_qubit_gates']:>3} "
            f"pred={row['predicted_qpu_total_error']:.6f} "
            f"MC={row['mc_rmse']:.6f} QPU/MC={row['qpu_over_mc_rmse']:.2f}"
        )
    print()
    print("Top deferred rows by predicted QPU/MC ratio:")
    deferred = [
        row for row in rows
        if row["budget_label"] == "shots"
        and row["decision"] != "candidate_for_qpu_microcampaign"
    ]
    for row in sorted(deferred, key=lambda item: float(item["qpu_over_mc_rmse"]))[:8]:
        print(
            f"  {row['decision']:<20} {row['case_id']:<20} n={row['num_qubits']} "
            f"K={row['schedule']:<9} class={row['angle_class']:<6} "
            f"depth={row['max_depth']:>3} 2q={row['max_two_qubit_gates']:>3} "
            f"pred={row['predicted_qpu_total_error']:.6f} MC={row['mc_rmse']:.6f} "
            f"QPU/MC={row['qpu_over_mc_rmse']:.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
