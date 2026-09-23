#!/usr/bin/env python3
"""Create a compact summary of the accepted Phase 1 hardware campaigns."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]

ACCEPTED_RUNS = [
    PROJECT_ROOT
    / "results/hardware/phase1_g0_repeat_ibm_fez_opt2/20260805T085729Z/fetched_campaign_results.json",
    PROJECT_ROOT
    / "results/hardware/phase1_g1_stress_ibm_fez_opt2/20260805T090601Z/fetched_campaign_results.json",
    PROJECT_ROOT
    / "results/hardware/phase1_g2_stress_ibm_fez_opt2/20260805T091004Z/fetched_campaign_results.json",
]


def load_rows(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows", [])
    estimates = payload.get("mlae_estimates", [])
    return rows, estimates


def scalar_row(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("transpiled_metrics", {})
    return {
        "campaign_id": row.get("campaign_id"),
        "case_id": row.get("case_id"),
        "k": row.get("k"),
        "backend": row.get("backend"),
        "optimization_level": row.get("optimization_level"),
        "shots": row.get("shots"),
        "expected_p": row.get("expected_p_k"),
        "p_hat": row.get("p_hat"),
        "p_abs_dev": row.get("p_abs_dev"),
        "transpiled_depth": metrics.get("depth"),
        "two_qubit_gate_count": metrics.get("two_qubit_gate_count"),
        "two_qubit_depth": row.get("transpiled_two_qubit_depth"),
        "job_id": row.get("job_id"),
    }


def estimate_row(estimate: dict[str, Any], campaign_id: str) -> dict[str, Any]:
    return {
        "campaign_id": campaign_id,
        "case_id": estimate.get("case_id"),
        "ks": ",".join(str(k) for k in estimate.get("ks", [])),
        "a_exact": estimate.get("a_exact"),
        "a_hat": estimate.get("a_hat"),
        "abs_error": estimate.get("abs_error"),
    }


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    keys = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    out_dir = PROJECT_ROOT / "results" / "hardware" / "phase1_summary"
    out_dir.mkdir(parents=True, exist_ok=True)

    circuit_rows: list[dict[str, Any]] = []
    estimate_rows: list[dict[str, Any]] = []
    for run_path in ACCEPTED_RUNS:
        rows, estimates = load_rows(run_path)
        circuit_rows.extend(scalar_row(row) for row in rows if row.get("p_hat") is not None)
        campaign_id = rows[0].get("campaign_id") if rows else run_path.parent.parent.name
        estimate_rows.extend(estimate_row(estimate, campaign_id) for estimate in estimates)

    circuit_rows.sort(key=lambda row: (str(row["case_id"]), int(row["k"])))
    estimate_rows.sort(key=lambda row: str(row["case_id"]))

    circuit_csv = out_dir / "phase1_accepted_circuits.csv"
    estimate_csv = out_dir / "phase1_mlae_estimates.csv"
    summary_json = out_dir / "phase1_summary.json"

    write_csv(circuit_rows, circuit_csv)
    write_csv(estimate_rows, estimate_csv)
    summary_json.write_text(
        json.dumps(
            {
                "accepted_runs": [str(path) for path in ACCEPTED_RUNS],
                "circuits": circuit_rows,
                "mlae_estimates": estimate_rows,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    print(f"Wrote CSV:  {circuit_csv}")
    print(f"Wrote CSV:  {estimate_csv}")
    print(f"Wrote JSON: {summary_json}")
    print()
    print("MLAE estimates:")
    for row in estimate_rows:
        print(
            f"  {row['case_id']} K=[{row['ks']}]: "
            f"a_hat={float(row['a_hat']):.8f} error={float(row['abs_error']):.6f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
