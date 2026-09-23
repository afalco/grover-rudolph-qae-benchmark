#!/usr/bin/env python3
"""Build a Phase 3/6/7 paper-quality versus backend-diagnostic summary.

This script consumes local CSV files only. It does not connect to IBM Quantum
and does not submit jobs.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    keys = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def as_float(row: dict[str, str] | None, key: str) -> float:
    if row is None:
        return float("nan")
    value = row.get(key, "")
    return float(value) if value != "" else float("nan")


def as_bool(row: dict[str, str] | None, key: str) -> bool:
    if row is None:
        return False
    return str(row.get(key, "")).lower() == "true"


def format_float(value: float, digits: int = 6) -> str:
    if value != value:
        return ""
    return f"{value:.{digits}f}"


def qoi_key(row: dict[str, str]) -> tuple[str, str]:
    return row["case_id"], row["quantity_id"]


def qoi_classification(phase6: dict[str, str] | None, phase7: dict[str, str] | None) -> tuple[str, str]:
    if phase7 is not None:
        status = phase7["status"]
        if status in {"publishable", "usable_with_ci"}:
            return (
                "paper_quality",
                "Validated in Phase 7 with confidence interval coverage.",
            )
        if status == "usable_bias_reported":
            return (
                "paper_quality_with_bias_caveat",
                "Phase 7 absolute error is below 0.01, but interval coverage misses the exact value.",
            )
        return (
            "backend_diagnostic",
            "Phase 7 repeat is diagnostic or rejected for this functional.",
        )

    if phase6 is not None and phase6["status"] in {"publishable", "usable_with_ci"}:
        return (
            "pending_replication",
            "Strong Phase 6 result, but no Phase 7 repeat was run.",
        )
    if phase6 is not None and phase6["status"] == "usable_bias_reported":
        return (
            "backend_diagnostic",
            "Phase 6 result is biased and has no Phase 7 validation.",
        )
    return (
        "backend_diagnostic",
        "No paper-quality validation available.",
    )


def distribution_classification(row: dict[str, str]) -> tuple[str, str]:
    case_id = row["case_id"]
    variant = row["variant"]
    tvd = as_float(row, "tvd")
    max_dev = as_float(row, "max_abs_deviation")

    if tvd <= 0.030 and max_dev <= 0.010:
        return (
            "paper_quality_distribution",
            "Global distance and maximum cell deviation pass the Phase 7 publication threshold.",
        )
    if tvd <= 0.040 and max_dev <= 0.025:
        return (
            "backend_diagnostic_distribution",
            "Distribution is useful for backend characterization, but too uneven for a global claim.",
        )
    if "phase6" in row.get("source", "") and case_id == "gr_beta_bump_n4":
        return (
            "backend_diagnostic_distribution",
            "Localized profile remains sensitive and was not stabilized in Phase 7.",
        )
    return (
        "backend_diagnostic_distribution",
        f"Latest {case_id} {variant} distribution does not pass global publication thresholds.",
    )


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize Phase 3/6/7 paper-quality and backend-diagnostic evidence."
    )
    parser.add_argument(
        "--phase6-qoi",
        default="results/phase6/qoi_decision_table/phase6_qoi_decision_table.csv",
    )
    parser.add_argument(
        "--phase7-qoi",
        default="results/phase7/qoi_decision_table/phase7_qoi_decision_table.csv",
    )
    parser.add_argument(
        "--phase7-distribution",
        default="results/phase7/qoi_analysis/phase7_distribution_metrics.csv",
    )
    parser.add_argument("--out-dir", default="results/phase7/paper_quality_summary")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    phase6_qoi = {qoi_key(row): row for row in read_csv(resolve_path(args.phase6_qoi))}
    phase7_qoi = {qoi_key(row): row for row in read_csv(resolve_path(args.phase7_qoi))}
    phase7_distribution = read_csv(resolve_path(args.phase7_distribution))

    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    qoi_rows: list[dict[str, Any]] = []
    for key in sorted(set(phase6_qoi) | set(phase7_qoi)):
        p6 = phase6_qoi.get(key)
        p7 = phase7_qoi.get(key)
        classification, rationale = qoi_classification(p6, p7)
        p6_error = as_float(p6, "abs_error")
        p7_error = as_float(p7, "abs_error")
        record = {
            "case_id": key[0],
            "quantity_id": key[1],
            "phase6_status": p6["status"] if p6 else "",
            "phase6_error": format_float(p6_error),
            "phase6_contains_exact": str(as_bool(p6, "contains_exact")) if p6 else "",
            "phase7_status": p7["status"] if p7 else "",
            "phase7_error": format_float(p7_error),
            "phase7_contains_exact": str(as_bool(p7, "contains_exact")) if p7 else "",
            "error_change_phase7_minus_phase6": format_float(p7_error - p6_error)
            if p6 and p7
            else "",
            "classification": classification,
            "rationale": rationale,
        }
        qoi_rows.append(record)

    qoi_rank = {
        "paper_quality": 0,
        "paper_quality_with_bias_caveat": 1,
        "pending_replication": 2,
        "backend_diagnostic": 3,
    }
    qoi_rows.sort(key=lambda row: (qoi_rank.get(str(row["classification"]), 9), row["case_id"], row["quantity_id"]))

    distribution_rows: list[dict[str, Any]] = []
    for row in sorted(phase7_distribution, key=lambda item: (item["case_id"], item["variant"])):
        classification, rationale = distribution_classification(row)
        distribution_rows.append(
            {
                "case_id": row["case_id"],
                "variant": row["variant"],
                "shots": row["shots"],
                "depth": row["depth"],
                "twoq": row["twoq"],
                "tvd": format_float(as_float(row, "tvd")),
                "hellinger": format_float(as_float(row, "hellinger")),
                "classical_fidelity": format_float(as_float(row, "classical_fidelity")),
                "max_abs_deviation": format_float(as_float(row, "max_abs_deviation")),
                "classification": classification,
                "rationale": rationale,
            }
        )

    qoi_json = out_dir / "phase7_qoi_paper_quality_summary.json"
    qoi_csv = out_dir / "phase7_qoi_paper_quality_summary.csv"
    dist_json = out_dir / "phase7_distribution_paper_quality_summary.json"
    dist_csv = out_dir / "phase7_distribution_paper_quality_summary.csv"
    md_path = out_dir / "phase7_paper_quality_summary.md"

    qoi_json.write_text(json.dumps(qoi_rows, indent=2, sort_keys=True), encoding="utf-8")
    dist_json.write_text(json.dumps(distribution_rows, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(qoi_rows, qoi_csv)
    write_csv(distribution_rows, dist_csv)

    qoi_counts = Counter(row["classification"] for row in qoi_rows)
    dist_counts = Counter(row["classification"] for row in distribution_rows)
    md_parts = [
        "# Phase 3/6/7 Paper-Quality Summary",
        "",
        "## Distribution-Level Evidence",
        "",
        markdown_table(
            distribution_rows,
            [
                "case_id",
                "variant",
                "tvd",
                "max_abs_deviation",
                "classification",
            ],
        ),
        "## Quantity-of-Interest Evidence",
        "",
        markdown_table(
            qoi_rows,
            [
                "case_id",
                "quantity_id",
                "phase6_error",
                "phase7_error",
                "error_change_phase7_minus_phase6",
                "classification",
            ],
        ),
    ]
    md_path.write_text("\n".join(md_parts), encoding="utf-8")

    print(f"Wrote JSON: {qoi_json}")
    print(f"Wrote CSV:  {qoi_csv}")
    print(f"Wrote JSON: {dist_json}")
    print(f"Wrote CSV:  {dist_csv}")
    print(f"Wrote MD:   {md_path}")
    print()
    print("Distribution classifications:")
    for key, count in sorted(dist_counts.items()):
        print(f"  {key}: {count}")
    print()
    print("Quantity-of-interest classifications:")
    for key, count in sorted(qoi_counts.items()):
        print(f"  {key}: {count}")
    print()
    print("Paper-quality quantities:")
    for row in qoi_rows:
        if row["classification"] == "paper_quality":
            print(
                f"  {row['case_id']} {row['quantity_id']}: "
                f"phase7_err={row['phase7_error']} status={row['phase7_status']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
