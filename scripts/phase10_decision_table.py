#!/usr/bin/env python3
"""Create Phase 10 decision tables from sampling-baseline outputs."""

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
    keys = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def qoi_status(ratio: float) -> str:
    if ratio <= 1.25:
        return "mc_competitive"
    if ratio <= 3.0:
        return "near_mc_scale"
    if ratio <= 6.0:
        return "hardware_bias_diagnostic"
    return "mc_dominated"


def distribution_status(percentile_rank: float) -> str:
    if percentile_rank <= 0.75:
        return "typical_sampling_error"
    if percentile_rank <= 0.95:
        return "upper_sampling_tail"
    return "hardware_bias_diagnostic"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create Phase 10 decision tables.")
    parser.add_argument(
        "--distribution-table",
        default="results/phase10/sampling_baselines/phase10_sampling_distribution_metrics.csv",
    )
    parser.add_argument(
        "--qoi-table",
        default="results/phase10/sampling_baselines/phase10_sampling_qoi_metrics.csv",
    )
    parser.add_argument("--out-dir", default="results/phase10/decision_table")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    distribution_rows = []
    for row in read_csv(resolve_path(args.distribution_table)):
        if row["metric"] != "tvd":
            continue
        mc_rank = float(row["mc_percentile_rank_of_qpu"])
        distribution_rows.append(
            {
                "case_id": row["case_id"],
                "metric": row["metric"],
                "qpu_tvd": float(row["qpu_value"]),
                "mc_median_tvd": float(row["mc_median"]),
                "qmc_median_tvd": float(row["qmc_median"]),
                "qpu_percentile_rank_vs_mc": mc_rank,
                "qpu_percentile_rank_vs_qmc": float(row["qmc_percentile_rank_of_qpu"]),
                "status": distribution_status(mc_rank),
            }
        )

    qoi_rows = []
    for row in read_csv(resolve_path(args.qoi_table)):
        ratio = float(row["qpu_abs_error_over_mc_rmse"])
        qoi_rows.append(
            {
                "case_id": row["case_id"],
                "quantity": row["quantity"],
                "qpu_abs_error": float(row["qpu_abs_error"]),
                "mc_rmse": float(row["mc_rmse"]),
                "qmc_rmse": float(row["qmc_rmse"]),
                "qpu_abs_error_over_mc_rmse": ratio,
                "qpu_abs_error_over_qmc_rmse": float(row["qpu_abs_error_over_qmc_rmse"]),
                "status": qoi_status(ratio),
            }
        )

    distribution_rows.sort(key=lambda row: row["case_id"])
    qoi_rows.sort(key=lambda row: (row["case_id"], row["qpu_abs_error_over_mc_rmse"]))

    dist_json = out_dir / "phase10_distribution_decision_table.json"
    dist_csv = out_dir / "phase10_distribution_decision_table.csv"
    qoi_json = out_dir / "phase10_qoi_decision_table.json"
    qoi_csv = out_dir / "phase10_qoi_decision_table.csv"
    md_path = out_dir / "phase10_decision_table.md"

    dist_json.write_text(json.dumps(distribution_rows, indent=2, sort_keys=True), encoding="utf-8")
    qoi_json.write_text(json.dumps(qoi_rows, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(distribution_rows, dist_csv)
    write_csv(qoi_rows, qoi_csv)

    lines = [
        "# Phase 10 Decision Table",
        "",
        "## Distribution Level",
        "",
        "| Case | QPU TVD | MC median TVD | QMC median TVD | Status |",
        "|---|---:|---:|---:|---|",
    ]
    for row in distribution_rows:
        lines.append(
            f"| `{row['case_id']}` | {row['qpu_tvd']:.6f} | "
            f"{row['mc_median_tvd']:.6f} | {row['qmc_median_tvd']:.6f} | "
            f"{row['status']} |"
        )
    lines.extend(
        [
            "",
            "## Quantity Level",
            "",
            "| Case | Quantity | QPU error | MC RMSE | QPU/MC | Status |",
            "|---|---|---:|---:|---:|---|",
        ]
    )
    for row in qoi_rows:
        lines.append(
            f"| `{row['case_id']}` | `{row['quantity']}` | "
            f"{row['qpu_abs_error']:.6f} | {row['mc_rmse']:.6f} | "
            f"{row['qpu_abs_error_over_mc_rmse']:.2f} | {row['status']} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote JSON: {dist_json}")
    print(f"Wrote CSV:  {dist_csv}")
    print(f"Wrote JSON: {qoi_json}")
    print(f"Wrote CSV:  {qoi_csv}")
    print(f"Wrote MD:   {md_path}")
    print()
    print("Phase 10 quantity decisions:")
    for row in qoi_rows:
        print(
            f"  {row['case_id']:<18} {row['quantity']:<18} "
            f"QPU/MC={row['qpu_abs_error_over_mc_rmse']:.2f} {row['status']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
