#!/usr/bin/env python3
"""Compare Phase 17 local TVD diagnostics with measured QPU distributions.

Phase 17 separates deterministic Grover-Rudolph angle precision from finite-shot
sampling error.  This script checks whether measured backend TVD is compatible
with that local prediction or whether an additional backend contribution is
clearly dominant.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_PHASE17_DIR = PROJECT_ROOT / "results/phase17/gr_angle_precision_diagnostic"
DEFAULT_OUT_DIR = PROJECT_ROOT / "results/phase17/backend_check"
DEFAULT_QPU_RUNS = [
    PROJECT_ROOT
    / "results/hardware/phase7_qoi_validation_n3_raw_ibm_fez/20260808T164816Z/fetched_gr_distribution_results.json",
    PROJECT_ROOT
    / "results/hardware/phase9_affine_n5_distribution_ibm_kingston/20260808T195550Z/fetched_gr_distribution_results.json",
    PROJECT_ROOT
    / "results/hardware/phase9_beta_bump_n5_distribution_ibm_kingston/20260808T202355Z/fetched_gr_distribution_results.json",
    PROJECT_ROOT
    / "results/hardware/phase15_gaussian_sigma025_n6_distribution_ibm_kingston/20260809T115457Z/fetched_gr_distribution_results.json",
]

CASE_ALIASES = {
    "gr_gaussian_mid_sigma025_n6": "gr_gaussian_sigma025_n6",
}


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def latest_phase17_csv() -> Path:
    paths = sorted(DEFAULT_PHASE17_DIR.glob("phase17_gr_angle_precision_diagnostic_*.csv"))
    if not paths:
        raise SystemExit(f"No Phase 17 CSV files found in {DEFAULT_PHASE17_DIR}")
    return paths[-1]


def canonical_case_id(case_id: str) -> str:
    return CASE_ALIASES.get(case_id, case_id)


def load_phase17_rows(path: Path, bit_depth: int) -> dict[tuple[str, int], dict[str, Any]]:
    rows: dict[tuple[str, int], dict[str, Any]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if int(row["bit_depth"]) != bit_depth:
                continue
            key = (canonical_case_id(str(row["case_id"])), int(row["shots"]))
            rows[key] = row
    return rows


def load_qpu_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            print(f"Warning: missing QPU result file {path}", file=sys.stderr)
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for row in data.get("rows", []):
            record = dict(row)
            record["source_result_path"] = str(path)
            rows.append(record)
    return rows


def classification(qpu_tvd: float, local_mean: float, local_p95: float) -> str:
    if math.isnan(qpu_tvd) or math.isnan(local_mean) or math.isnan(local_p95):
        return "unmatched"
    if qpu_tvd <= local_p95:
        return "shot_compatible"
    if qpu_tvd <= 2.0 * local_p95:
        return "backend_visible"
    return "backend_dominated"


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    keys = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def write_summary(rows: list[dict[str, Any]], path: Path) -> None:
    lines = [
        "# Phase 17b Backend Check",
        "",
        "This table compares measured backend TVD with the local Phase 17",
        "finite-shot and angle-precision prediction.  The comparison is diagnostic:",
        "a backend TVD well above the local p95 indicates hardware, layout,",
        "transpilation, or mitigation effects beyond shot noise.",
        "",
        "| Case | Backend | shots | QPU TVD | local shot mean | local p95 | QPU/local | status |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['case_id']}` | `{row['backend']}` | {row['shots']} | "
            f"{row['qpu_tvd']:.6g} | {row['local_exact_shot_tvd_mean']:.6g} | "
            f"{row['local_exact_shot_tvd_p95']:.6g} | "
            f"{row['qpu_over_local_shot_mean']:.3g} | `{row['status']}` |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check QPU distribution TVD against Phase 17 local angle/shot predictions."
    )
    parser.add_argument("--phase17-csv", default=None)
    parser.add_argument("--qpu-runs", nargs="*", default=[str(path) for path in DEFAULT_QPU_RUNS])
    parser.add_argument("--bit-depth", type=int, default=16)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    phase17_csv = resolve_path(args.phase17_csv) if args.phase17_csv else latest_phase17_csv()
    qpu_paths = [resolve_path(path) for path in args.qpu_runs]
    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    local_rows = load_phase17_rows(phase17_csv, int(args.bit_depth))
    qpu_rows = load_qpu_rows(qpu_paths)

    rows: list[dict[str, Any]] = []
    for qpu in qpu_rows:
        case_id = canonical_case_id(str(qpu.get("case_id")))
        shots = int(qpu.get("shots", 0))
        local = local_rows.get((case_id, shots))
        if local is None:
            continue
        qpu_tvd = float(qpu.get("tvd", float("nan")))
        local_mean = float(local["exact_angle_shot_tvd_mean"])
        local_p95 = float(local["exact_angle_shot_tvd_p95"])
        qpu_over_local = qpu_tvd / local_mean if local_mean > 0.0 else float("inf")
        rows.append(
            {
                "case_id": case_id,
                "original_case_id": qpu.get("case_id"),
                "backend": qpu.get("backend"),
                "campaign_id": qpu.get("campaign_id"),
                "shots": shots,
                "bit_depth": int(args.bit_depth),
                "qpu_tvd": qpu_tvd,
                "qpu_max_abs_deviation": float(qpu.get("max_abs_deviation", float("nan"))),
                "local_angle_quantization_tvd": float(local["angle_quantization_tvd"]),
                "local_exact_shot_tvd_mean": local_mean,
                "local_exact_shot_tvd_p95": local_p95,
                "local_combined_tvd_mean": float(local["combined_tvd_mean"]),
                "local_combined_tvd_p95": float(local["combined_tvd_p95"]),
                "qpu_over_local_shot_mean": qpu_over_local,
                "qpu_minus_local_shot_mean": qpu_tvd - local_mean,
                "status": classification(qpu_tvd, local_mean, local_p95),
                "depth": qpu.get("transpiled_metrics", {}).get("depth"),
                "two_qubit_gate_count": qpu.get("transpiled_metrics", {}).get("two_qubit_gate_count"),
                "runtime_options_enabled": bool(qpu.get("runtime_options")),
                "source_result_path": qpu.get("source_result_path"),
            }
        )

    rows.sort(key=lambda row: (row["status"], -float(row["qpu_over_local_shot_mean"]), row["case_id"]))

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out_dir / f"phase17_backend_check_{timestamp}.json"
    csv_path = out_dir / f"phase17_backend_check_{timestamp}.csv"
    summary_path = out_dir / f"phase17_backend_check_{timestamp}.md"
    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(rows, csv_path)
    write_summary(rows, summary_path)

    print(f"Phase 17 local source: {phase17_csv}")
    print(f"Wrote JSON:            {json_path}")
    print(f"Wrote CSV:             {csv_path}")
    print(f"Wrote summary:         {summary_path}")
    print()
    print("Backend check:")
    for row in rows:
        print(
            f"  {row['case_id']:<26} {row['backend']:<13} shots={row['shots']:<5} "
            f"QPU={row['qpu_tvd']:.6g} local_p95={row['local_exact_shot_tvd_p95']:.6g} "
            f"ratio={row['qpu_over_local_shot_mean']:.2f} status={row['status']}"
        )
    if not rows:
        print("  No QPU rows matched the selected Phase 17 cases and shot counts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
