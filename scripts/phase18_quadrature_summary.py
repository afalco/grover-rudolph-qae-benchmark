#!/usr/bin/env python3
"""Summarize Phase 18 angle-structured quadrature-QAE hardware results."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN = (
    PROJECT_ROOT
    / "results/hardware/phase18_oscillatory_shifted_n4_midpoint_k01_ibm_kingston/20260812T143823Z"
)


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    keys = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize Phase 18 quadrature-QAE results.")
    parser.add_argument("--campaign-run", default=str(DEFAULT_RUN))
    parser.add_argument("--out-dir", default="results/phase18/quadrature_summary")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_dir = resolve_path(args.campaign_run)
    data_path = run_dir / "fetched_campaign_results.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    rows = data.get("rows", [])
    estimates = data.get("mlae_estimates", [])
    if not rows:
        raise SystemExit(f"No rows found in {data_path}")

    by_case: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_case.setdefault(str(row["case_id"]), []).append(row)

    estimate_by_case = {str(item["case_id"]): item for item in estimates}
    summary_rows: list[dict[str, Any]] = []
    circuit_rows: list[dict[str, Any]] = []
    for case_id, case_rows in sorted(by_case.items()):
        ordered = sorted(case_rows, key=lambda row: int(row["k"]))
        first = ordered[0]
        estimate = estimate_by_case.get(case_id, {})
        quadrature_value = float(first.get("quadrature_value"))
        true_integral = float(first.get("true_integral"))
        discretization_error = abs(quadrature_value - true_integral)
        a_hat = estimate.get("a_hat")
        mlae_estimation_error = abs(float(a_hat) - quadrature_value) if a_hat is not None else None
        total_error = abs(float(a_hat) - true_integral) if a_hat is not None else None
        summary_rows.append(
            {
                "case_id": case_id,
                "backend": first.get("backend"),
                "rule": first.get("rule"),
                "num_qubits": first.get("num_qubits"),
                "grid_points": first.get("grid_points"),
                "schedule": json.dumps([int(row["k"]) for row in ordered]),
                "shots_per_circuit": first.get("shots"),
                "total_shots": sum(int(row.get("shots", 0)) for row in ordered),
                "quadrature_value": quadrature_value,
                "true_integral": true_integral,
                "discretization_error": discretization_error,
                "mlae_estimate": a_hat,
                "mlae_estimation_error": mlae_estimation_error,
                "total_error": total_error,
                "max_depth": max(int(row["transpiled_metrics"]["depth"]) for row in ordered),
                "max_two_qubit_gates": max(
                    int(row["transpiled_metrics"]["two_qubit_gate_count"]) for row in ordered
                ),
                "max_two_qubit_depth": max(int(row["transpiled_two_qubit_depth"]) for row in ordered),
                "max_probability_abs_dev": max(float(row["p_abs_dev"]) for row in ordered),
                "campaign_run": str(run_dir),
            }
        )
        for row in ordered:
            circuit_rows.append(
                {
                    "case_id": case_id,
                    "backend": row.get("backend"),
                    "k": int(row["k"]),
                    "expected_p": float(row["expected_p_k"]),
                    "p_hat": float(row["p_hat"]),
                    "p_abs_dev": float(row["p_abs_dev"]),
                    "shots": int(row["shots"]),
                    "depth": int(row["transpiled_metrics"]["depth"]),
                    "two_qubit_gates": int(row["transpiled_metrics"]["two_qubit_gate_count"]),
                    "two_qubit_depth": int(row["transpiled_two_qubit_depth"]),
                    "job_id": row.get("job_id"),
                }
            )

    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_json = out_dir / "phase18_quadrature_summary.json"
    summary_csv = out_dir / "phase18_quadrature_summary.csv"
    circuits_csv = out_dir / "phase18_quadrature_circuits.csv"
    summary_json.write_text(
        json.dumps({"summary": summary_rows, "circuits": circuit_rows}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    write_csv(summary_rows, summary_csv)
    write_csv(circuit_rows, circuits_csv)

    print(f"Wrote JSON: {summary_json}")
    print(f"Wrote CSV:  {summary_csv}")
    print(f"Wrote CSV:  {circuits_csv}")
    print()
    print("Phase 18 quadrature summary:")
    for row in summary_rows:
        print(
            f"  {row['case_id']}: a_hat={row['mlae_estimate']:.8f} "
            f"true={row['true_integral']:.8f} total_error={row['total_error']:.6f} "
            f"max_depth={row['max_depth']} max_2q={row['max_two_qubit_gates']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
