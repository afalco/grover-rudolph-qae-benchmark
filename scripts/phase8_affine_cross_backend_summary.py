#!/usr/bin/env python3
"""Summarize the gr_affine_n4 Fez-vs-Kingston cross-backend comparison.

This script consumes local Phase 8 comparison CSV files only. It does not
connect to IBM Quantum and does not submit jobs.
"""

from __future__ import annotations

import argparse
import csv
import json
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


def as_float(row: dict[str, str], key: str) -> float:
    return float(row[key])


def status_for(abs_error: float, contains_exact: bool) -> str:
    if abs_error <= 0.005 and contains_exact:
        return "paper_quality"
    if abs_error <= 0.010 and contains_exact:
        return "paper_quality"
    if abs_error <= 0.010:
        return "paper_quality_with_bias_caveat"
    if abs_error <= 0.020:
        return "backend_diagnostic"
    return "reject"


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize Fez-vs-Kingston affine comparison.")
    parser.add_argument(
        "--distribution-metrics",
        default="results/phase8/kingston_affine_comparison/phase8_kingston_affine_distribution_metrics.csv",
    )
    parser.add_argument(
        "--qoi-metrics",
        default="results/phase8/kingston_affine_comparison/phase8_kingston_affine_qoi_metrics.csv",
    )
    parser.add_argument("--out-dir", default="results/phase8/kingston_affine_comparison")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dist_rows = read_csv(resolve_path(args.distribution_metrics))
    qoi_rows = read_csv(resolve_path(args.qoi_metrics))
    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    distribution_summary = []
    for row in sorted(dist_rows, key=lambda item: item["backend"]):
        tvd = as_float(row, "tvd")
        max_dev = as_float(row, "max_abs_deviation")
        distribution_summary.append(
            {
                "backend": row["backend"],
                "shots": int(float(row["shots"])),
                "depth": int(float(row["depth"])),
                "twoq": int(float(row["twoq"])),
                "tvd": f"{tvd:.6f}",
                "hellinger": f"{as_float(row, 'hellinger'):.6f}",
                "classical_fidelity": f"{as_float(row, 'classical_fidelity'):.6f}",
                "max_abs_deviation": f"{max_dev:.6f}",
                "distribution_decision": "paper_quality_distribution"
                if tvd <= 0.030 and max_dev <= 0.010
                else "backend_diagnostic_distribution",
            }
        )

    qoi_summary = []
    for row in sorted(qoi_rows, key=lambda item: (item["observable"], item["backend"])):
        abs_error = as_float(row, "abs_error")
        contains = str(row["contains_exact"]).lower() == "true"
        qoi_summary.append(
            {
                "backend": row["backend"],
                "quantity_id": row["quantity"],
                "estimate": f"{as_float(row, 'estimate'):.6f}",
                "exact": f"{as_float(row, 'exact'):.6f}",
                "abs_error": f"{abs_error:.6f}",
                "contains_exact": contains,
                "decision": status_for(abs_error, contains),
            }
        )

    # Difference table by quantity: Kingston minus Fez absolute error.
    by_quantity: dict[str, dict[str, dict[str, Any]]] = {}
    for row in qoi_summary:
        by_quantity.setdefault(str(row["quantity_id"]), {})[str(row["backend"])] = row

    deltas = []
    for quantity, rows in sorted(by_quantity.items()):
        fez = rows.get("ibm_fez")
        kingston = rows.get("ibm_kingston")
        if not fez or not kingston:
            continue
        fez_error = float(fez["abs_error"])
        kingston_error = float(kingston["abs_error"])
        deltas.append(
            {
                "quantity_id": quantity,
                "fez_abs_error": f"{fez_error:.6f}",
                "kingston_abs_error": f"{kingston_error:.6f}",
                "kingston_minus_fez": f"{kingston_error - fez_error:.6f}",
                "better_backend": "ibm_kingston" if kingston_error < fez_error else "ibm_fez",
            }
        )

    dist_csv = out_dir / "phase8_affine_cross_backend_distribution_summary.csv"
    qoi_csv = out_dir / "phase8_affine_cross_backend_qoi_summary.csv"
    delta_csv = out_dir / "phase8_affine_cross_backend_qoi_delta.csv"
    json_path = out_dir / "phase8_affine_cross_backend_summary.json"
    md_path = out_dir / "phase8_affine_cross_backend_summary.md"

    write_csv(distribution_summary, dist_csv)
    write_csv(qoi_summary, qoi_csv)
    write_csv(deltas, delta_csv)
    json_path.write_text(
        json.dumps(
            {
                "distribution_summary": distribution_summary,
                "qoi_summary": qoi_summary,
                "qoi_delta": deltas,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    md_path.write_text(
        "\n".join(
            [
                "# Phase 8 gr_affine_n4 Cross-Backend Summary",
                "",
                "## Distribution",
                "",
                markdown_table(
                    distribution_summary,
                    ["backend", "tvd", "hellinger", "classical_fidelity", "max_abs_deviation", "distribution_decision"],
                ),
                "## Quantity-of-Interest Delta",
                "",
                markdown_table(deltas, ["quantity_id", "fez_abs_error", "kingston_abs_error", "kingston_minus_fez", "better_backend"]),
            ]
        ),
        encoding="utf-8",
    )

    print(f"Wrote CSV:  {dist_csv}")
    print(f"Wrote CSV:  {qoi_csv}")
    print(f"Wrote CSV:  {delta_csv}")
    print(f"Wrote JSON: {json_path}")
    print(f"Wrote MD:   {md_path}")
    print()
    print("Distribution summary:")
    for row in distribution_summary:
        print(
            f"  {row['backend']}: TVD={row['tvd']} H={row['hellinger']} "
            f"Fcl={row['classical_fidelity']} max={row['max_abs_deviation']} "
            f"{row['distribution_decision']}"
        )
    print()
    print("Quantity deltas:")
    for row in deltas:
        print(
            f"  {row['quantity_id']:<18} Fez={row['fez_abs_error']} "
            f"Kingston={row['kingston_abs_error']} delta={row['kingston_minus_fez']} "
            f"best={row['better_backend']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
