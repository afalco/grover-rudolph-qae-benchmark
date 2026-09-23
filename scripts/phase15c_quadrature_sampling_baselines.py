#!/usr/bin/env python3
"""Phase 15c comparison of quadrature-QAE hardware estimates with MC/QMC.

The Phase 15b hardware runs estimate midpoint quadrature values of g under the
uniform grid. This script compares those estimates with classical Monte Carlo
and randomized quasi-Monte Carlo estimators for the continuous integral
int_0^1 g(x) dx, using matched sample budgets.
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

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from phase15_quadrature_qae_audit import function_value, true_integral  # noqa: E402


DEFAULT_CAMPAIGN_CONFIGS = [
    PROJECT_ROOT / "experiments/phase15b_quadrature_microcampaign_ibm_kingston.json",
    PROJECT_ROOT / "experiments/phase15b_quadrature_microcampaign_ibm_aachen.json",
]

DEFAULT_SUMMARIES = [
    PROJECT_ROOT
    / "results/hardware/phase15b_quadrature_microcampaign_ibm_kingston/20260809T173754Z/quadrature_qae_mlae_summary.csv",
    PROJECT_ROOT
    / "results/hardware/phase15b_quadrature_microcampaign_ibm_aachen/20260812T073051Z/quadrature_qae_mlae_summary.csv",
]


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def load_cases(config_paths: list[Path]) -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    for path in config_paths:
        config = json.loads(path.read_text(encoding="utf-8"))
        for case in config.get("selected_jobs", []):
            cases[str(case["case_id"])] = dict(case)
    return cases


def load_summary_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                row = dict(row)
                row["summary_source"] = str(path)
                rows.append(row)
    return rows


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


def summarize_abs_errors(abs_errors: list[float], errors: list[float]) -> dict[str, float]:
    return {
        "bias": mean(errors),
        "mae": mean(abs_errors),
        "rmse": rmse(errors),
        "std": stddev(errors),
        "abs_error_p50": percentile(abs_errors, 0.50),
        "abs_error_p90": percentile(abs_errors, 0.90),
        "abs_error_p95": percentile(abs_errors, 0.95),
        "abs_error_p975": percentile(abs_errors, 0.975),
    }


def van_der_corput(index: int, base: int = 2) -> float:
    value = 0.0
    denominator = 1.0
    while index > 0:
        index, remainder = divmod(index, base)
        denominator *= base
        value += remainder / denominator
    return value


def mc_estimate(case: dict[str, Any], samples: int, rng: random.Random) -> float:
    return mean([function_value(case, rng.random()) for _ in range(samples)])


def qmc_estimate(case: dict[str, Any], samples: int, shift: float) -> float:
    values = [
        function_value(case, (van_der_corput(index) + shift) % 1.0)
        for index in range(1, samples + 1)
    ]
    return mean(values)


def function_values_array(case: dict[str, Any], x: np.ndarray) -> np.ndarray:
    kind = str(case["kind"])
    if kind == "constant_quarter":
        values = np.full_like(x, 0.25, dtype=float)
    elif kind == "sin2_half":
        values = np.sin(math.pi * x / 2.0) ** 2
    elif kind == "sin2":
        values = np.sin(math.pi * x) ** 2
    elif kind == "affine":
        values = float(case.get("offset", 0.0)) + float(case.get("slope", 1.0)) * x
    elif kind == "beta_bump":
        alpha = float(case.get("alpha", 2.0))
        beta = float(case.get("beta", 5.0))
        values = float(case.get("scale", 1.0)) * (x ** (alpha - 1.0)) * ((1.0 - x) ** (beta - 1.0))
    elif kind == "oscillatory":
        values = (
            float(case.get("base", 0.5))
            + float(case.get("sin_amp", 0.25)) * np.sin(2.0 * math.pi * x)
            + float(case.get("cos_amp", 0.10)) * np.cos(4.0 * math.pi * x)
        )
    else:
        raise ValueError(f"Unknown function kind: {kind}")
    return np.clip(values.astype(float), 0.0, 1.0)


def van_der_corput_array(samples: int, base: int = 2) -> np.ndarray:
    indices = np.arange(1, samples + 1, dtype=np.int64)
    values = np.zeros(samples, dtype=float)
    denominator = 1.0
    active = indices.copy()
    while np.any(active > 0):
        remainders = active % base
        active = active // base
        denominator *= base
        values += remainders / denominator
    return values


def mc_estimates_vectorized(
    case: dict[str, Any],
    *,
    trials: int,
    samples: int,
    rng: np.random.Generator,
) -> list[float]:
    x = rng.random((trials, samples))
    return function_values_array(case, x).mean(axis=1).tolist()


def qmc_estimates_vectorized(
    case: dict[str, Any],
    *,
    trials: int,
    samples: int,
    rng: np.random.Generator,
) -> list[float]:
    base_points = van_der_corput_array(samples)
    shifts = rng.random((trials, 1))
    x = (base_points.reshape(1, samples) + shifts) % 1.0
    return function_values_array(case, x).mean(axis=1).tolist()


def backend_from_source(path_text: str) -> str:
    parts = Path(path_text).parts
    for part in parts:
        if part.startswith("phase15b_quadrature_microcampaign_"):
            return part.removeprefix("phase15b_quadrature_microcampaign_")
    return "unknown"


def make_rows(
    *,
    summary_rows: list[dict[str, Any]],
    cases: dict[str, dict[str, Any]],
    trials: int,
    seed: int,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)
    out_rows: list[dict[str, Any]] = []
    for source_row in summary_rows:
        case_id = str(source_row["case_id"])
        case = cases[case_id]
        exact = float(source_row.get("true_integral") or true_integral(case))
        qpu_estimate = float(source_row["mlae_a_hat"])
        qpu_total_error = abs(qpu_estimate - exact)
        total_shots = int(float(source_row["total_shots"]))
        oracle_queries = int(float(source_row["oracle_queries"]))
        budgets = {
            "shots": total_shots,
            "oracle_queries": oracle_queries,
        }

        for budget_label, samples in budgets.items():
            if trials > 0:
                mc_estimates = mc_estimates_vectorized(
                    case,
                    trials=trials,
                    samples=samples,
                    rng=np_rng,
                )
                qmc_estimates = qmc_estimates_vectorized(
                    case,
                    trials=trials,
                    samples=samples,
                    rng=np_rng,
                )
            else:
                mc_estimates = []
                qmc_estimates = []
            mc_errors = [estimate - exact for estimate in mc_estimates]
            qmc_errors = [estimate - exact for estimate in qmc_estimates]
            mc_abs_errors = [abs(error) for error in mc_errors]
            qmc_abs_errors = [abs(error) for error in qmc_errors]
            mc_summary = summarize_abs_errors(mc_abs_errors, mc_errors)
            qmc_summary = summarize_abs_errors(qmc_abs_errors, qmc_errors)
            out_rows.append(
                {
                    "backend": backend_from_source(source_row["summary_source"]),
                    "case_id": case_id,
                    "family": source_row.get("family", case.get("family", "")),
                    "group_id": source_row["group_id"],
                    "num_qubits": int(source_row["num_qubits"]),
                    "grid_points": 2 ** int(source_row["num_qubits"]),
                    "rule": source_row["rule"],
                    "ks": source_row["ks"],
                    "budget_label": budget_label,
                    "classical_samples": samples,
                    "trials": trials,
                    "true_integral": exact,
                    "quadrature_value": float(source_row["quadrature_value"]),
                    "discretization_error": float(source_row["discretization_error"]),
                    "qpu_mlae_estimate": qpu_estimate,
                    "qpu_mlae_estimation_error": float(source_row["mlae_estimation_error"]),
                    "qpu_total_error": qpu_total_error,
                    "qpu_total_shots": total_shots,
                    "qpu_oracle_queries": oracle_queries,
                    "mc_bias": mc_summary["bias"],
                    "mc_mae": mc_summary["mae"],
                    "mc_rmse": mc_summary["rmse"],
                    "mc_std": mc_summary["std"],
                    "mc_abs_error_p50": mc_summary["abs_error_p50"],
                    "mc_abs_error_p90": mc_summary["abs_error_p90"],
                    "mc_abs_error_p95": mc_summary["abs_error_p95"],
                    "mc_abs_error_p975": mc_summary["abs_error_p975"],
                    "mc_percentile_rank_of_qpu_error": percentile_rank(mc_abs_errors, qpu_total_error),
                    "qmc_bias": qmc_summary["bias"],
                    "qmc_mae": qmc_summary["mae"],
                    "qmc_rmse": qmc_summary["rmse"],
                    "qmc_std": qmc_summary["std"],
                    "qmc_abs_error_p50": qmc_summary["abs_error_p50"],
                    "qmc_abs_error_p90": qmc_summary["abs_error_p90"],
                    "qmc_abs_error_p95": qmc_summary["abs_error_p95"],
                    "qmc_abs_error_p975": qmc_summary["abs_error_p975"],
                    "qmc_percentile_rank_of_qpu_error": percentile_rank(qmc_abs_errors, qpu_total_error),
                    "qpu_error_over_mc_rmse": qpu_total_error / mc_summary["rmse"]
                    if mc_summary["rmse"] > 0
                    else float("inf"),
                    "qpu_error_over_qmc_rmse": qpu_total_error / qmc_summary["rmse"]
                    if qmc_summary["rmse"] > 0
                    else float("inf"),
                    "summary_source": source_row["summary_source"],
                }
            )
    out_rows.sort(key=lambda row: (row["backend"], row["case_id"], row["budget_label"]))
    return out_rows


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
        "# Phase 15c Quadrature Sampling Baselines",
        "",
        "The QPU column is the hardware MLAE estimate from Phase 15b. MC and",
        "randomized QMC estimate the continuous integral over [0,1]. Two budgets are",
        "reported: the same number of circuit shots and the same number of oracle",
        "queries used by the QPU schedule.",
        "",
        "| Backend | Case | Budget | QPU error | MC RMSE | QMC RMSE | QPU/MC | QPU/QMC |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['backend']}` | `{row['group_id']}` | {row['budget_label']}={row['classical_samples']} | "
            f"{row['qpu_total_error']:.6f} | {row['mc_rmse']:.6f} | {row['qmc_rmse']:.6f} | "
            f"{row['qpu_error_over_mc_rmse']:.2f} | {row['qpu_error_over_qmc_rmse']:.2f} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare Phase 15b quadrature-QAE results with MC/QMC baselines."
    )
    parser.add_argument(
        "--summaries",
        nargs="+",
        default=[str(path) for path in DEFAULT_SUMMARIES],
        help="Phase 15b quadrature_qae_mlae_summary.csv files.",
    )
    parser.add_argument(
        "--campaign-configs",
        nargs="+",
        default=[str(path) for path in DEFAULT_CAMPAIGN_CONFIGS],
        help="Campaign configs containing the function definitions.",
    )
    parser.add_argument("--trials", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260812)
    parser.add_argument("--out-dir", default="results/phase15c/quadrature_sampling_baselines")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = load_cases([resolve_path(path) for path in args.campaign_configs])
    summary_rows = load_summary_rows([resolve_path(path) for path in args.summaries])
    rows = make_rows(
        summary_rows=summary_rows,
        cases=cases,
        trials=int(args.trials),
        seed=int(args.seed),
    )

    json_path = out_dir / "phase15c_quadrature_sampling_baselines.json"
    csv_path = out_dir / "phase15c_quadrature_sampling_baselines.csv"
    summary_path = out_dir / "phase15c_quadrature_sampling_baselines.md"
    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(rows, csv_path)
    write_summary(rows, summary_path)

    print(f"Wrote JSON:    {json_path}")
    print(f"Wrote CSV:     {csv_path}")
    print(f"Wrote summary: {summary_path}")
    print()
    print("Phase 15c QPU vs MC/QMC total-error comparison:")
    for row in rows:
        if row["budget_label"] != "shots":
            continue
        print(
            f"  {row['backend']:<12} {row['group_id']:<28} "
            f"QPU={row['qpu_total_error']:.6f} "
            f"MC_RMSE={row['mc_rmse']:.6f} "
            f"QMC_RMSE={row['qmc_rmse']:.6f} "
            f"QPU/MC={row['qpu_error_over_mc_rmse']:.2f} "
            f"QPU/QMC={row['qpu_error_over_qmc_rmse']:.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
