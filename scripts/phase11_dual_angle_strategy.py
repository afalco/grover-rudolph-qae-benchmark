#!/usr/bin/env python3
"""Phase 11 dual angle-structure strategy table.

The script combines two views of quantum numerical integration:

1. QAE view: classify the profile or test function as an amplitude function via
   the angle map Theta=2 asin(sqrt(.)).
2. Direct Grover-Rudolph view: prepare a finite probability law and estimate
   I_p(f) by postprocessing measured frequencies.

No IBM backend access is required.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from grover_rudolph_qae_benchmark.angle_structure import (  # noqa: E402
    classify_values,
    qoi_function_values,
    values_from_case_function,
)


DEFAULT_CONFIG = PROJECT_ROOT / "experiments/phase11_dual_angle_strategy.json"


def resolve_path(path_text: str) -> Path:
    if path_text.startswith("latest:"):
        pattern = path_text.split(":", 1)[1]
        pattern_path = Path(pattern)
        if not pattern_path.is_absolute():
            pattern = str(PROJECT_ROOT / pattern)
        matches = sorted(glob.glob(pattern))
        if not matches:
            return PROJECT_ROOT / pattern_path
        return Path(matches[-1])
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_cases(path: Path) -> list[dict[str, Any]]:
    data = load_json(path)
    if "cases" in data:
        return list(data["cases"])
    if "selected_cases" in data:
        return list(data["selected_cases"])
    raise ValueError(f"{path} does not contain 'cases' or 'selected_cases'.")


def read_csv_optional(path_text: str | None) -> list[dict[str, str]]:
    if not path_text:
        return []
    path = resolve_path(path_text)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


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
                    if isinstance(value, (dict, list, tuple))
                    else value
                    for key, value in row.items()
                }
            )


def float_or_none(value: Any) -> float | None:
    if value in (None, "", "nan", "None"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def by_case(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {str(row["case_id"]): row for row in rows if "case_id" in row}


def by_case_quantity(rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    out = {}
    for row in rows:
        case_id = row.get("case_id")
        quantity = row.get("quantity")
        if case_id and quantity:
            out[(str(case_id), str(quantity))] = row
    return out


def direct_status(
    *,
    phase9_case: dict[str, str] | None,
    phase10_dist: dict[str, str] | None,
    phase10_qoi: dict[str, str] | None,
    thresholds: dict[str, Any],
) -> str:
    if phase10_qoi:
        status = str(phase10_qoi.get("status", ""))
        if status == "mc_competitive":
            return "supported_by_phase10"
        if status == "near_mc_scale":
            return "near_mc_scale"
        if status == "hardware_bias_diagnostic":
            return "diagnostic_bias"
        if status == "mc_dominated":
            return "mc_dominated"

    if phase10_dist:
        dist_status = str(phase10_dist.get("status", ""))
        if dist_status == "hardware_bias_diagnostic":
            return "distribution_bias_diagnostic"

    if phase9_case:
        hardware_status = str(phase9_case.get("hardware_status", ""))
        noisy_tvd = float_or_none(phase9_case.get("noisy_tvd"))
        max_qoi = float_or_none(phase9_case.get("max_noisy_qoi_error"))
        if hardware_status == "hardware_candidate":
            if (
                noisy_tvd is not None
                and max_qoi is not None
                and noisy_tvd <= float(thresholds["max_direct_sampling_noisy_tvd"])
                and max_qoi <= float(thresholds["max_direct_sampling_noisy_qoi_error"])
            ):
                return "credit_free_direct_candidate"
            return "direct_candidate_needs_margin"
        if hardware_status:
            return hardware_status
    return "not_audited"


def qae_status(profile_degree: int, quantity_degree: int, thresholds: dict[str, Any]) -> str:
    low = int(thresholds["low_angle_degree"])
    moderate = int(thresholds["moderate_angle_degree"])
    if quantity_degree <= low and profile_degree <= moderate:
        return "amplified_qae_candidate"
    if quantity_degree <= low:
        return "qae_candidate_profile_cost_watch"
    if quantity_degree <= moderate:
        return "simulator_qae_candidate"
    return "defer_amplified_qae"


def recommendation(
    *,
    direct: str,
    qae: str,
    profile_degree: int,
    quantity_degree: int,
) -> str:
    if direct == "supported_by_phase10":
        return "paper_quality_direct_sampling"
    if direct == "mc_dominated":
        return "defer_qpu_repeat"
    if direct in {"diagnostic_bias", "distribution_bias_diagnostic"}:
        if qae in {"amplified_qae_candidate", "qae_candidate_profile_cost_watch"}:
            return "simulator_only_no_qpu_yet"
        return "direct_sampling_diagnostic"
    if direct == "near_mc_scale":
        return "repeat_or_cross_backend_direct_sampling"
    if qae == "amplified_qae_candidate" and direct in {
        "credit_free_direct_candidate",
        "not_audited",
    }:
        return "simulate_then_consider_amplified_qae"
    if qae == "qae_candidate_profile_cost_watch":
        return "simulate_qae_but_prioritize_direct_audit"
    if direct in {"credit_free_direct_candidate", "direct_candidate_needs_margin"}:
        return "direct_sampling_candidate"
    if quantity_degree >= 4 or profile_degree >= 4:
        return "local_audit_only_for_now"
    return "needs_local_audit"


def evidence_note(
    *,
    phase9_case: dict[str, str] | None,
    phase10_qoi: dict[str, str] | None,
    phase10_dist: dict[str, str] | None,
) -> str:
    parts = []
    if phase9_case:
        status = phase9_case.get("hardware_status")
        depth = phase9_case.get("transpiled_depth")
        twoq = phase9_case.get("transpiled_two_qubit_gate_count")
        tvd = phase9_case.get("noisy_tvd")
        if status:
            parts.append(f"Phase 9 {status}, depth={depth}, 2q={twoq}, noisy_TVD={tvd}")
    if phase10_dist:
        parts.append(
            "Phase 10 distribution "
            f"QPU_TVD={phase10_dist.get('qpu_tvd')}, MC_median={phase10_dist.get('mc_median_tvd')}"
        )
    if phase10_qoi:
        parts.append(
            "Phase 10 quantity "
            f"status={phase10_qoi.get('status')}, QPU/MC={phase10_qoi.get('qpu_abs_error_over_mc_rmse')}"
        )
    return "; ".join(parts)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Phase 11 dual angle strategy tables.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--tolerance", type=float, default=1e-10)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_json(resolve_path(args.config))
    thresholds = dict(config.get("thresholds", {}))
    out_dir = resolve_path(args.out_dir or str(config.get("out_dir", "results/phase11/dual_angle_strategy")))
    out_dir.mkdir(parents=True, exist_ok=True)

    source_config = resolve_path(str(config["source_config"]))
    cases_by_id = {str(case["case_id"]): case for case in load_cases(source_config)}
    selected_cases = [str(case_id) for case_id in config.get("cases", cases_by_id.keys())]
    quantities = [str(quantity) for quantity in config.get("quantities", [])]
    grid = str(config.get("grid", "midpoint"))
    profile_input_kind = str(config.get("profile_input_kind", "qae_function"))

    phase9_cases = by_case(read_csv_optional(config.get("phase9_best_table")))
    phase10_dist = by_case(read_csv_optional(config.get("phase10_distribution_decision_table")))
    phase10_qoi = by_case_quantity(read_csv_optional(config.get("phase10_qoi_decision_table")))

    profile_rows: list[dict[str, Any]] = []
    strategy_rows: list[dict[str, Any]] = []

    for case_id in selected_cases:
        case = cases_by_id[case_id]
        num_qubits = int(case["num_qubits"])
        profile_values = values_from_case_function(case, input_kind=profile_input_kind)
        profile_class = classify_values(
            case_id,
            profile_values,
            num_qubits=num_qubits,
            grid=grid,
            input_kind=profile_input_kind,
            tolerance=args.tolerance,
            notes=str(case.get("notes", "")),
        ).to_dict()
        phase9_case = phase9_cases.get(case_id)
        phase10_dist_row = phase10_dist.get(case_id)
        profile_rows.append(
            {
                "case_id": case_id,
                "num_qubits": num_qubits,
                "grid_points": 2**num_qubits,
                "profile_angle_class": profile_class["class"],
                "profile_angle_degree": profile_class["degree"],
                "profile_angle_support": profile_class["support"],
                "phase9_hardware_status": phase9_case.get("hardware_status") if phase9_case else "",
                "phase9_noisy_tvd": phase9_case.get("noisy_tvd") if phase9_case else "",
                "phase9_depth": phase9_case.get("transpiled_depth") if phase9_case else "",
                "phase9_twoq": phase9_case.get("transpiled_two_qubit_gate_count") if phase9_case else "",
                "phase10_distribution_status": phase10_dist_row.get("status") if phase10_dist_row else "",
                "notes": str(case.get("notes", "")),
            }
        )

        for quantity in quantities:
            quantity_values = qoi_function_values(quantity, num_qubits, grid=grid)
            quantity_class = classify_values(
                quantity,
                quantity_values,
                num_qubits=num_qubits,
                grid=grid,
                input_kind="quantity_of_interest",
                tolerance=args.tolerance,
                notes="Classical test function used for finite-law integration.",
            ).to_dict()
            qoi_row = phase10_qoi.get((case_id, quantity))
            direct = direct_status(
                phase9_case=phase9_case,
                phase10_dist=phase10_dist_row,
                phase10_qoi=qoi_row,
                thresholds=thresholds,
            )
            qae = qae_status(
                int(profile_class["degree"]),
                int(quantity_class["degree"]),
                thresholds,
            )
            strategy_rows.append(
                {
                    "case_id": case_id,
                    "quantity": quantity,
                    "num_qubits": num_qubits,
                    "grid_points": 2**num_qubits,
                    "profile_angle_class": profile_class["class"],
                    "profile_angle_degree": profile_class["degree"],
                    "quantity_angle_class": quantity_class["class"],
                    "quantity_angle_degree": quantity_class["degree"],
                    "direct_sampling_status": direct,
                    "amplified_qae_status": qae,
                    "recommendation": recommendation(
                        direct=direct,
                        qae=qae,
                        profile_degree=int(profile_class["degree"]),
                        quantity_degree=int(quantity_class["degree"]),
                    ),
                    "phase10_qpu_over_mc": qoi_row.get("qpu_abs_error_over_mc_rmse") if qoi_row else "",
                    "phase10_qoi_status": qoi_row.get("status") if qoi_row else "",
                    "evidence": evidence_note(
                        phase9_case=phase9_case,
                        phase10_qoi=qoi_row,
                        phase10_dist=phase10_dist_row,
                    ),
                }
            )

    profile_rows.sort(key=lambda row: (row["num_qubits"], row["case_id"]))
    strategy_rows.sort(
        key=lambda row: (
            row["case_id"],
            row["quantity_angle_degree"],
            row["quantity"],
        )
    )

    profile_json = out_dir / "phase11_profile_angle_strategy.json"
    profile_csv = out_dir / "phase11_profile_angle_strategy.csv"
    strategy_json = out_dir / "phase11_dual_angle_strategy.json"
    strategy_csv = out_dir / "phase11_dual_angle_strategy.csv"
    md_path = out_dir / "phase11_dual_angle_strategy.md"

    profile_json.write_text(json.dumps(profile_rows, indent=2, sort_keys=True), encoding="utf-8")
    strategy_json.write_text(json.dumps(strategy_rows, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(profile_rows, profile_csv)
    write_csv(strategy_rows, strategy_csv)

    lines = [
        "# Phase 11 Dual Angle Strategy",
        "",
        "## Profile Summary",
        "",
        "| Case | Grid | Profile class | Phase 9 status | Phase 10 status |",
        "|---|---:|---:|---|---|",
    ]
    for row in profile_rows:
        lines.append(
            f"| `{row['case_id']}` | {row['grid_points']} | "
            f"{row['profile_angle_class']} | {row['phase9_hardware_status']} | "
            f"{row['phase10_distribution_status']} |"
        )
    lines.extend(
        [
            "",
            "## Pair Strategy",
            "",
            "| Case | Quantity | Profile class | Quantity class | Direct | QAE | Recommendation |",
            "|---|---|---:|---:|---|---|---|",
        ]
    )
    for row in strategy_rows:
        lines.append(
            f"| `{row['case_id']}` | `{row['quantity']}` | "
            f"{row['profile_angle_class']} | {row['quantity_angle_class']} | "
            f"{row['direct_sampling_status']} | {row['amplified_qae_status']} | "
            f"{row['recommendation']} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote JSON: {profile_json}")
    print(f"Wrote CSV:  {profile_csv}")
    print(f"Wrote JSON: {strategy_json}")
    print(f"Wrote CSV:  {strategy_csv}")
    print(f"Wrote MD:   {md_path}")
    print()
    print("Phase 11 recommendations:")
    for row in strategy_rows:
        if row["recommendation"] in {
            "paper_quality_direct_sampling",
            "simulate_then_consider_amplified_qae",
            "direct_sampling_candidate",
        }:
            print(
                f"  {row['case_id']:<18} {row['quantity']:<18} "
                f"profile={row['profile_angle_class']:<5} "
                f"quantity={row['quantity_angle_class']:<5} "
                f"{row['recommendation']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
