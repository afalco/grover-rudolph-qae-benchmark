#!/usr/bin/env python3
"""Summarize Phase 2 audit and hardware microcampaign outcomes."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]

PHASE1_G0 = (
    PROJECT_ROOT
    / "results/hardware/phase1_g0_repeat_ibm_fez_opt2/20260805T085729Z/fetched_campaign_results.json"
)
PHASE2_G0 = (
    PROJECT_ROOT
    / "results/hardware/phase2_g0_layout_repeat_ibm_fez_opt2_seed67890/20260805T093851Z/fetched_campaign_results.json"
)
PHASE2_G0_MITIGATED = (
    PROJECT_ROOT
    / "results/hardware/phase2_g0_mitigation_ibm_fez_opt2_dd_twirling/20260805T094505Z/fetched_campaign_results.json"
)
AUDIT_BEST = (
    PROJECT_ROOT
    / "results/phase2/layout_audit/phase2_layout_audit_best_20260805T092909Z.csv"
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_row(payload: dict[str, Any], case_id: str, k: int) -> dict[str, Any]:
    for row in payload.get("rows", []):
        if row.get("case_id") == case_id and int(row.get("k")) == k:
            return row
    raise KeyError(f"No row for {case_id} k={k} in payload")


def find_estimate(payload: dict[str, Any], case_id: str) -> dict[str, Any]:
    for row in payload.get("mlae_estimates", []):
        if row.get("case_id") == case_id:
            return row
    raise KeyError(f"No estimate for {case_id} in payload")


def load_audit_best(path: Path, case_id: str, k: int) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("case_id") == case_id and int(row.get("k", -1)) == k:
                return row
    raise KeyError(f"No audit row for {case_id} k={k}")


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    keys = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    out_dir = PROJECT_ROOT / "results" / "phase2" / "summary"
    out_dir.mkdir(parents=True, exist_ok=True)

    phase1 = load_json(PHASE1_G0)
    phase2 = load_json(PHASE2_G0)
    phase2_mitigated = load_json(PHASE2_G0_MITIGATED)
    phase1_k2 = find_row(phase1, "g0", 2)
    phase2_k2 = find_row(phase2, "g0", 2)
    phase2_mitigated_k2 = find_row(phase2_mitigated, "g0", 2)
    audit_k2 = load_audit_best(AUDIT_BEST, "g0", 2)
    phase1_est = find_estimate(phase1, "g0")
    phase2_est = find_estimate(phase2, "g0")
    phase2_mitigated_est = find_estimate(phase2_mitigated, "g0")

    rows = [
        {
            "source": "phase1_hardware_accepted",
            "case_id": "g0",
            "k": 2,
            "optimization_level": phase1_k2.get("optimization_level"),
            "transpiler_seed": phase1_k2.get("seed"),
            "depth": phase1_k2.get("transpiled_metrics", {}).get("depth"),
            "two_qubit_gate_count": phase1_k2.get("transpiled_metrics", {}).get(
                "two_qubit_gate_count"
            ),
            "expected_p": phase1_k2.get("expected_p_k"),
            "p_hat_or_noisy_p": phase1_k2.get("p_hat"),
            "abs_dev": phase1_k2.get("p_abs_dev"),
            "a_hat": phase1_est.get("a_hat"),
            "a_abs_error": phase1_est.get("abs_error"),
        },
        {
            "source": "phase2_audit_prediction",
            "case_id": "g0",
            "k": 2,
            "optimization_level": audit_k2.get("optimization_level"),
            "transpiler_seed": audit_k2.get("transpiler_seed"),
            "depth": audit_k2.get("transpiled_depth"),
            "two_qubit_gate_count": audit_k2.get("two_qubit_gate_count"),
            "expected_p": audit_k2.get("expected_p"),
            "p_hat_or_noisy_p": audit_k2.get("noisy_p"),
            "abs_dev": audit_k2.get("noisy_abs_dev"),
            "a_hat": "",
            "a_abs_error": "",
        },
        {
            "source": "phase2_hardware_seed67890",
            "case_id": "g0",
            "k": 2,
            "optimization_level": phase2_k2.get("optimization_level"),
            "transpiler_seed": phase2_k2.get("seed"),
            "depth": phase2_k2.get("transpiled_metrics", {}).get("depth"),
            "two_qubit_gate_count": phase2_k2.get("transpiled_metrics", {}).get(
                "two_qubit_gate_count"
            ),
            "expected_p": phase2_k2.get("expected_p_k"),
            "p_hat_or_noisy_p": phase2_k2.get("p_hat"),
            "abs_dev": phase2_k2.get("p_abs_dev"),
            "a_hat": phase2_est.get("a_hat"),
            "a_abs_error": phase2_est.get("abs_error"),
        },
        {
            "source": "phase2_hardware_dd_twirling",
            "case_id": "g0",
            "k": 2,
            "optimization_level": phase2_mitigated_k2.get("optimization_level"),
            "transpiler_seed": phase2_mitigated_k2.get("seed"),
            "depth": phase2_mitigated_k2.get("transpiled_metrics", {}).get("depth"),
            "two_qubit_gate_count": phase2_mitigated_k2.get("transpiled_metrics", {}).get(
                "two_qubit_gate_count"
            ),
            "expected_p": phase2_mitigated_k2.get("expected_p_k"),
            "p_hat_or_noisy_p": phase2_mitigated_k2.get("p_hat"),
            "abs_dev": phase2_mitigated_k2.get("p_abs_dev"),
            "a_hat": phase2_mitigated_est.get("a_hat"),
            "a_abs_error": phase2_mitigated_est.get("abs_error"),
        },
    ]

    csv_path = out_dir / "phase2_g0_layout_summary.csv"
    json_path = out_dir / "phase2_g0_layout_summary.json"
    write_csv(rows, csv_path)
    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")

    print(f"Wrote CSV:  {csv_path}")
    print(f"Wrote JSON: {json_path}")
    print()
    for row in rows:
        print(
            f"{row['source']}: p={float(row['p_hat_or_noisy_p']):.6f} "
            f"dev={float(row['abs_dev']):.6f} depth={row['depth']} "
            f"2q={row['two_qubit_gate_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
