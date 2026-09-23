#!/usr/bin/env python3
"""Classify functions by the angle-structure hierarchy G_n^(d)."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from grover_rudolph_qae_benchmark.angle_structure import (  # noqa: E402
    builtin_function_values,
    classify_values,
    qoi_function_values,
    subset_label,
    values_from_case_function,
)


DEFAULT_BUILTINS = ["g0", "g1", "g2"]
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
    return path


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


def compact_row(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": record["case_id"],
        "class": record["class"],
        "degree": record["degree"],
        "support": record["support"],
        "num_qubits": record["num_qubits"],
        "grid": record["grid"],
        "input_kind": record["input_kind"],
        "nonzero_coefficients": record["nonzero_coefficients"],
        "notes": record.get("notes", ""),
    }


def load_json_cases(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "cases" in data:
        return list(data["cases"])
    if "selected_cases" in data:
        return list(data["selected_cases"])
    raise ValueError(f"{path} does not contain 'cases' or 'selected_cases'.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify membership in the angle-structure class G_n^(d), where d is "
            "the multilinear degree of Theta_g(b)=2 asin(sqrt(g(x_i(b))))."
        )
    )
    parser.add_argument(
        "--builtins",
        nargs="*",
        default=None,
        help="Built-in QAE calibration functions to classify: g0 g1 g2.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Experiment JSON whose function cases should be classified.",
    )
    parser.add_argument(
        "--case-id",
        nargs="*",
        default=None,
        help="Optional subset of case ids from --config.",
    )
    parser.add_argument(
        "--quantities",
        nargs="*",
        default=None,
        help="Classify quantity-of-interest test functions on --num-qubits.",
    )
    parser.add_argument("--num-qubits", type=int, default=2)
    parser.add_argument("--grid", choices=["midpoint", "left"], default="midpoint")
    parser.add_argument(
        "--input-kind",
        choices=["qae_function", "normalized_probability", "max_normalized_profile"],
        default="qae_function",
        help=(
            "How to interpret JSON case functions. Use qae_function for paper "
            "classes, normalized_probability for the finite probability law, or "
            "max_normalized_profile for a profile rescaled to [0,1]."
        ),
    )
    parser.add_argument("--tolerance", type=float, default=1e-10)
    parser.add_argument(
        "--out-dir",
        default="results/angle_structure",
        help="Output directory for JSON/CSV reports.",
    )
    parser.add_argument("--output-prefix", default="angle_structure_classification")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    classifications = []

    builtins = DEFAULT_BUILTINS if args.builtins == [] else args.builtins
    if builtins:
        for case_id in builtins:
            values = builtin_function_values(case_id, args.num_qubits, grid=args.grid)
            classifications.append(
                classify_values(
                    case_id,
                    values,
                    num_qubits=args.num_qubits,
                    grid=args.grid,
                    input_kind="qae_function",
                    tolerance=args.tolerance,
                    notes="Built-in QAE calibration function.",
                )
            )

    if args.config:
        path = resolve_path(args.config)
        selected = set(args.case_id or [])
        for case in load_json_cases(path):
            case_id = str(case["case_id"])
            if selected and case_id not in selected:
                continue
            if "function" not in case:
                continue
            values = values_from_case_function(case, input_kind=args.input_kind)
            classifications.append(
                classify_values(
                    case_id,
                    values,
                    num_qubits=int(case["num_qubits"]),
                    grid=args.grid,
                    input_kind=args.input_kind,
                    tolerance=args.tolerance,
                    notes=str(case.get("notes", case.get("purpose", ""))),
                )
            )

    quantities = DEFAULT_QUANTITIES if args.quantities == [] else args.quantities
    if quantities:
        for name in quantities:
            values = qoi_function_values(name, args.num_qubits, grid=args.grid)
            classifications.append(
                classify_values(
                    name,
                    values,
                    num_qubits=args.num_qubits,
                    grid=args.grid,
                    input_kind="quantity_of_interest",
                    tolerance=args.tolerance,
                    notes="Classical test function used for direct integration postprocessing.",
                )
            )

    if not classifications:
        values = [builtin_function_values(case_id, args.num_qubits, grid=args.grid) for case_id in DEFAULT_BUILTINS]
        for case_id, case_values in zip(DEFAULT_BUILTINS, values):
            classifications.append(
                classify_values(
                    case_id,
                    case_values,
                    num_qubits=args.num_qubits,
                    grid=args.grid,
                    input_kind="qae_function",
                    tolerance=args.tolerance,
                    notes="Default built-in QAE calibration function.",
                )
            )

    records = [classification.to_dict() for classification in classifications]
    compact = [compact_row(record) for record in records]

    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{args.output_prefix}.json"
    csv_path = out_dir / f"{args.output_prefix}.csv"
    json_path.write_text(json.dumps(records, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(compact, csv_path)

    print(f"Wrote JSON: {json_path}")
    print(f"Wrote CSV:  {csv_path}")
    print()
    print("Angle-structure classification:")
    for record in compact:
        coeffs = ", ".join(
            f"{name}={value:.6g}" for name, value in record["nonzero_coefficients"].items()
        )
        print(
            f"  {record['case_id']:<24} {record['class']:<8} "
            f"degree={record['degree']} support={record['support']} "
            f"input={record['input_kind']} coeffs=[{coeffs}]"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
