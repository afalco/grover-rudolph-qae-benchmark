#!/usr/bin/env python3
"""Build the Phase 6 decision table from local postprocessing outputs.

The script consumes Phase 6 CSV files only. It does not connect to IBM Quantum
and does not submit jobs.
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
        for row in rows:
            writer.writerow(row)


def as_float(row: dict[str, str], key: str) -> float:
    value = row.get(key, "")
    if value == "":
        return float("nan")
    return float(value)


def as_int(row: dict[str, str], key: str) -> int:
    value = row.get(key, "")
    if value == "":
        return 0
    return int(float(value))


def format_float(value: float, digits: int = 6) -> str:
    if math.isnan(value):
        return "nan"
    return f"{value:.{digits}f}"


def aggregate_observables(observable_rows: list[dict[str, str]]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in observable_rows:
        grouped[str(row["row_id"])].append(row)

    out: dict[str, dict[str, float]] = {}
    for row_id, rows in grouped.items():
        errors = [as_float(row, "abs_error") for row in rows]
        contains = [str(row.get("contains_exact", "")).lower() == "true" for row in rows]
        worst = max(errors) if errors else float("nan")
        mean = sum(errors) / len(errors) if errors else float("nan")
        coverage = sum(1 for value in contains if value) / len(contains) if contains else float("nan")
        out[row_id] = {
            "observable_count": float(len(rows)),
            "max_observable_error": worst,
            "mean_observable_error": mean,
            "ci_coverage_fraction": coverage,
        }
    return out


def decision_for(row: dict[str, Any]) -> tuple[str, str, str]:
    case_id = str(row["case_id"])
    variant = str(row["variant"])
    tvd = float(row["tvd"])
    hellinger = float(row["hellinger"])
    max_dev = float(row["max_abs_deviation"])
    max_obs = float(row["max_observable_error"])
    ci_cov = float(row["ci_coverage_fraction"])

    if "beta_bump" in case_id:
        return (
            "diagnostic_only",
            "do_not_run_amplified_qae",
            "Localized profile remains sensitive: TVD/Hellinger are higher and the opt/seed repeat is not a robust improvement.",
        )

    if case_id == "gr_affine_n4" and variant == "mitigated":
        return (
            "direct_distribution_and_selected_observables",
            "do_not_run_amplified_qae_yet",
            "Mitigation improves all global distances, but Phase 4 showed the first Grover iterate is still biased on hardware.",
        )

    if tvd <= 0.025 and max_dev <= 0.012 and max_obs <= 0.020:
        return (
            "direct_distribution_result",
            "defer_amplified_qae",
            "Distribution error is low; keep the result as direct probability loading until amplified circuits pass a separate audit.",
        )

    if tvd <= 0.035 and max_dev <= 0.016 and max_obs <= 0.020:
        return (
            "direct_distribution_result_with_caveat",
            "defer_amplified_qae",
            "Distribution metrics are acceptable, but at least one observable or confidence interval remains borderline.",
        )

    if hellinger <= 0.05 and max_dev <= 0.025 and ci_cov >= 0.2:
        return (
            "diagnostic_distribution",
            "do_not_run_amplified_qae_yet",
            "Useful for backend diagnostics, but not strong enough as a primary quantitative estimator.",
        )

    return (
        "hold",
        "do_not_run_amplified_qae",
        "Current errors are too large or too observable-dependent for escalation.",
    )


def markdown_table(rows: list[dict[str, Any]]) -> str:
    headers = [
        "case",
        "variant",
        "TVD",
        "H",
        "max cell",
        "max obs",
        "decision",
        "QAE",
    ]
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
                    str(row["variant"]),
                    format_float(float(row["tvd"]), 4),
                    format_float(float(row["hellinger"]), 4),
                    format_float(float(row["max_abs_deviation"]), 4),
                    format_float(float(row["max_observable_error"]), 4),
                    str(row["decision"]),
                    str(row["amplified_qae_decision"]),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Phase 6 decision table.")
    parser.add_argument(
        "--distribution-metrics",
        default="results/phase6/distribution_analysis/phase6_distribution_metrics.csv",
    )
    parser.add_argument(
        "--observable-metrics",
        default="results/phase6/distribution_analysis/phase6_observable_metrics.csv",
    )
    parser.add_argument("--out-dir", default="results/phase6/decision_table")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    distribution_path = resolve_path(args.distribution_metrics)
    observable_path = resolve_path(args.observable_metrics)
    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    observable_summary = aggregate_observables(read_csv(observable_path))
    rows: list[dict[str, Any]] = []

    for row in read_csv(distribution_path):
        row_id = str(row["row_id"])
        summary = observable_summary.get(row_id, {})
        record: dict[str, Any] = {
            "case_id": row["case_id"],
            "variant": row["variant"],
            "backend": row["backend"],
            "shots": as_int(row, "shots"),
            "depth": as_int(row, "depth"),
            "twoq": as_int(row, "twoq"),
            "tvd": as_float(row, "tvd"),
            "hellinger": as_float(row, "hellinger"),
            "classical_fidelity": as_float(row, "classical_fidelity"),
            "l2_distance": as_float(row, "l2_distance"),
            "max_abs_deviation": as_float(row, "max_abs_deviation"),
            "max_observable_error": summary.get("max_observable_error", float("nan")),
            "mean_observable_error": summary.get("mean_observable_error", float("nan")),
            "ci_coverage_fraction": summary.get("ci_coverage_fraction", float("nan")),
        }
        decision, qae_decision, rationale = decision_for(record)
        record["decision"] = decision
        record["amplified_qae_decision"] = qae_decision
        record["rationale"] = rationale
        rows.append(record)

    rows.sort(key=lambda item: (str(item["case_id"]), float(item["tvd"]), str(item["variant"])))

    json_path = out_dir / "phase6_decision_table.json"
    csv_path = out_dir / "phase6_decision_table.csv"
    md_path = out_dir / "phase6_decision_table.md"
    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(rows, csv_path)
    md_path.write_text(markdown_table(rows), encoding="utf-8")

    print(f"Wrote JSON: {json_path}")
    print(f"Wrote CSV:  {csv_path}")
    print(f"Wrote MD:   {md_path}")
    print()
    print("Phase 6 decisions:")
    for row in rows:
        print(
            f"  {row['case_id']:<18} {row['variant']:<24} "
            f"TVD={row['tvd']:.6f} max_obs={row['max_observable_error']:.6f} "
            f"decision={row['decision']} qae={row['amplified_qae_decision']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
