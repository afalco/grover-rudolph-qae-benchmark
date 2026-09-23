#!/usr/bin/env python3
"""Analyze fetched hardware campaign results."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_DIR = PROJECT_ROOT / "results" / "hardware" / "phase1_calibration_ibm_fez"


def latest_fetched_campaign() -> Path:
    candidates = sorted(DEFAULT_RUN_DIR.glob("*/fetched_campaign_results.json"))
    if not candidates:
        raise SystemExit(
            "No fetched campaign found. Pass --campaign-run path/to/fetched_campaign_results.json."
        )
    return candidates[-1]


def resolve_campaign_path(path_text: str | None) -> Path:
    if path_text is None:
        return latest_fetched_campaign()
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if path.is_dir():
        candidate = path / "fetched_campaign_results.json"
        if not candidate.exists():
            raise SystemExit(f"No fetched_campaign_results.json found in {path}")
        return candidate
    return path


def binomial_sigma(p: float, shots: int) -> float:
    if shots <= 0:
        return float("nan")
    return math.sqrt(max(p * (1.0 - p), 0.0) / shots)


def row_status(abs_z: float | None, p_abs_dev: float | None) -> str:
    if abs_z is None or p_abs_dev is None:
        return "missing"
    if abs_z >= 8.0 or p_abs_dev >= 0.10:
        return "fail"
    if abs_z >= 4.0 or p_abs_dev >= 0.04:
        return "watch"
    return "pass"


def summarize_row(row: dict[str, Any]) -> dict[str, Any]:
    shots = int(row.get("shots") or 0)
    expected = float(row["expected_p_k"])
    p_hat = row.get("p_hat")
    if p_hat is None:
        sigma = None
        z_score = None
        abs_z = None
        p_abs_dev = None
    else:
        p_hat = float(p_hat)
        sigma = binomial_sigma(expected, shots)
        p_abs_dev = abs(p_hat - expected)
        z_score = (p_hat - expected) / sigma if sigma > 0 else None
        abs_z = abs(z_score) if z_score is not None else None

    metrics = row.get("transpiled_metrics", {})
    return {
        "case_id": row.get("case_id"),
        "k": row.get("k"),
        "backend": row.get("backend"),
        "job_id": row.get("job_id"),
        "shots": shots,
        "expected_p": expected,
        "p_hat": p_hat,
        "p_abs_dev": p_abs_dev,
        "binomial_sigma": sigma,
        "z_score": z_score,
        "abs_z_score": abs_z,
        "status": row_status(abs_z, p_abs_dev),
        "transpiled_depth": metrics.get("depth"),
        "two_qubit_gate_count": metrics.get("two_qubit_gate_count"),
        "two_qubit_depth": row.get("transpiled_two_qubit_depth"),
    }


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    keys = [
        "case_id",
        "k",
        "backend",
        "job_id",
        "shots",
        "expected_p",
        "p_hat",
        "p_abs_dev",
        "binomial_sigma",
        "z_score",
        "abs_z_score",
        "status",
        "transpiled_depth",
        "two_qubit_gate_count",
        "two_qubit_depth",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def print_table(rows: list[dict[str, Any]]) -> None:
    print(
        f"{'case':<5} {'k':>2} {'p_hat':>9} {'expected':>9} {'dev':>9} "
        f"{'z':>8} {'depth':>6} {'2q':>5} {'status':>7}"
    )
    print("-" * 76)
    for row in rows:
        print(
            f"{row['case_id']:<5} {int(row['k']):>2} "
            f"{row['p_hat']:>9.6f} {row['expected_p']:>9.6f} "
            f"{row['p_abs_dev']:>9.6f} {row['z_score']:>8.2f} "
            f"{int(row['transpiled_depth']):>6} "
            f"{int(row['two_qubit_gate_count']):>5} "
            f"{row['status']:>7}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze fetched IBM campaign results.")
    parser.add_argument(
        "--campaign-run",
        default=None,
        help="Path to fetched_campaign_results.json or its containing run directory.",
    )
    parser.add_argument(
        "--out-csv",
        default=None,
        help="Optional CSV path. Defaults to <run>/analysis_summary.csv.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    fetched_path = resolve_campaign_path(args.campaign_run)
    run_dir = fetched_path.parent
    payload = json.loads(fetched_path.read_text(encoding="utf-8"))

    rows = [summarize_row(row) for row in payload.get("rows", []) if row.get("p_hat") is not None]
    rows.sort(key=lambda row: (str(row["case_id"]), int(row["k"])))

    out_csv = Path(args.out_csv) if args.out_csv else run_dir / "analysis_summary.csv"
    if not out_csv.is_absolute():
        out_csv = PROJECT_ROOT / out_csv
    write_csv(rows, out_csv)

    print(f"Campaign run: {run_dir}")
    print_table(rows)
    print()
    print(f"Wrote CSV: {out_csv}")

    failing = [row for row in rows if row["status"] == "fail"]
    watched = [row for row in rows if row["status"] == "watch"]
    if failing:
        print()
        print("Failing circuits:")
        for row in failing:
            print(
                f"  {row['case_id']} k={row['k']}: dev={row['p_abs_dev']:.6f}, "
                f"z={row['z_score']:.2f}, depth={row['transpiled_depth']}, "
                f"2q={row['two_qubit_gate_count']}"
            )
    if watched:
        print()
        print("Watch circuits:")
        for row in watched:
            print(
                f"  {row['case_id']} k={row['k']}: dev={row['p_abs_dev']:.6f}, "
                f"z={row['z_score']:.2f}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
