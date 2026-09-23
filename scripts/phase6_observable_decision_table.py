#!/usr/bin/env python3
"""Rank direct quantity-of-interest estimates from Phase 6 postprocessing.

The script consumes the Phase 6 quantity-of-interest CSV only. It does not
connect to IBM Quantum and does not submit jobs.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
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
    value = row.get(key, "")
    return float(value) if value else float("nan")


def as_int(row: dict[str, str], key: str) -> int:
    value = row.get(key, "")
    return int(float(value)) if value else 0


def as_bool(row: dict[str, str], key: str) -> bool:
    return str(row.get(key, "")).lower() == "true"


def status_for(abs_error: float, contains_exact: bool) -> str:
    if abs_error <= 0.005 and contains_exact:
        return "publishable"
    if abs_error <= 0.010 and contains_exact:
        return "usable_with_ci"
    if abs_error <= 0.010:
        return "usable_bias_reported"
    if abs_error <= 0.020:
        return "diagnostic"
    return "reject"


def rationale_for(status: str) -> str:
    if status == "publishable":
        return "Small absolute error and bootstrap interval contains the exact value."
    if status == "usable_with_ci":
        return "Moderate absolute error and bootstrap interval contains the exact value."
    if status == "usable_bias_reported":
        return "Moderate absolute error, but the bootstrap interval misses the exact value; report as biased."
    if status == "diagnostic":
        return "Quantity of interest is informative but too biased for a primary estimate."
    return "Quantity-of-interest error is too large for the current hardware data."


def status_rank(status: str) -> int:
    order = {
        "publishable": 0,
        "usable_with_ci": 1,
        "usable_bias_reported": 2,
        "diagnostic": 3,
        "reject": 4,
    }
    return order.get(status, 99)


def format_float(value: float, digits: int = 6) -> str:
    if math.isnan(value):
        return "nan"
    return f"{value:.{digits}f}"


def markdown_table(rows: list[dict[str, Any]]) -> str:
    headers = ["case", "quantity", "variant", "estimate", "exact", "abs err", "CI contains", "status"]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row['case_id']}`",
                    f"`{row['quantity_id']}`",
                    str(row["selected_variant"]),
                    format_float(float(row["estimate"]), 5),
                    format_float(float(row["exact"]), 5),
                    format_float(float(row["abs_error"]), 5),
                    str(row["contains_exact"]),
                    str(row["status"]),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Phase 6 quantity-of-interest decision table.")
    parser.add_argument(
        "--qoi-metrics",
        default=None,
        help="CSV produced by Phase 6 distribution analysis. Defaults to the legacy metrics path.",
    )
    parser.add_argument(
        "--observable-metrics",
        default="results/phase6/distribution_analysis/phase6_observable_metrics.csv",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--out-dir", default="results/phase6/qoi_decision_table")
    parser.add_argument("--output-prefix", default="phase6")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    metrics_path_text = args.qoi_metrics or args.observable_metrics
    qoi_path = resolve_path(metrics_path_text)
    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in read_csv(qoi_path):
        quantity_id = row.get("quantity") or row.get("observable")
        if not quantity_id:
            raise KeyError("Expected a 'quantity' column in the QoI metrics CSV.")
        grouped[(row["case_id"], quantity_id)].append(row)

    rows: list[dict[str, Any]] = []
    for (case_id, quantity_id), candidates in grouped.items():
        best = min(candidates, key=lambda row: (as_float(row, "abs_error"), row["variant"]))
        abs_error = as_float(best, "abs_error")
        contains_exact = as_bool(best, "contains_exact")
        status = status_for(abs_error, contains_exact)
        rows.append(
            {
                "case_id": case_id,
                "quantity_id": quantity_id,
                "selected_variant": best["variant"],
                "backend": best["backend"],
                "shots": as_int(best, "shots"),
                "depth": as_int(best, "depth"),
                "twoq": as_int(best, "twoq"),
                "estimate": as_float(best, "estimate"),
                "exact": as_float(best, "exact"),
                "abs_error": abs_error,
                "estimate_ci_low": as_float(best, "estimate_ci_low"),
                "estimate_ci_high": as_float(best, "estimate_ci_high"),
                "contains_exact": contains_exact,
                "status": status,
                "rationale": rationale_for(status),
            }
        )

    rows.sort(key=lambda row: (status_rank(str(row["status"])), row["case_id"], row["quantity_id"]))

    prefix = str(args.output_prefix)
    json_path = out_dir / f"{prefix}_qoi_decision_table.json"
    csv_path = out_dir / f"{prefix}_qoi_decision_table.csv"
    md_path = out_dir / f"{prefix}_qoi_decision_table.md"
    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(rows, csv_path)
    md_path.write_text(markdown_table(rows), encoding="utf-8")

    print(f"Wrote JSON: {json_path}")
    print(f"Wrote CSV:  {csv_path}")
    print(f"Wrote MD:   {md_path}")
    print()
    print("Selected quantity-of-interest estimates:")
    for row in rows:
        print(
            f"  {row['case_id']:<18} {row['quantity_id']:<18} "
            f"{row['selected_variant']:<24} err={row['abs_error']:.6f} "
            f"contains={row['contains_exact']} status={row['status']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
