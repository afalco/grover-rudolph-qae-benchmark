#!/usr/bin/env python3
"""Fetch and analyze Grover-Rudolph distribution-measurement results."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from grover_rudolph_qae_benchmark.gr_state_preparation import (
    distribution_from_counts,
    total_variation_distance,
)
from grover_rudolph_qae_benchmark.ibm import DEFAULT_ACCOUNT_FILE, qiskit_runtime_service


DEFAULT_RUN_DIR = PROJECT_ROOT / "results" / "hardware" / "phase3_gr_distribution_ibm_fez"


def latest_submitted_campaign() -> Path:
    candidates = sorted(DEFAULT_RUN_DIR.glob("*/submitted_campaign.json"))
    if not candidates:
        raise SystemExit(
            "No submitted Grover-Rudolph distribution campaign found. "
            "Pass --campaign-run path/to/submitted_campaign.json."
        )
    return candidates[-1]


def resolve_campaign_path(path_text: str | None) -> Path:
    if path_text is None:
        return latest_submitted_campaign()
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if path.is_dir():
        candidate = path / "submitted_campaign.json"
        if not candidate.exists():
            raise SystemExit(f"No submitted_campaign.json found in {path}")
        return candidate
    return path


def job_status_text(job) -> str:
    try:
        status = job.status()
    except Exception as exc:
        return f"STATUS_ERROR:{exc}"
    name = getattr(status, "name", None)
    return str(name or status)


def is_done_status(status: str) -> bool:
    upper = status.upper()
    return any(token in upper for token in ("DONE", "COMPLETED"))


def is_failed_status(status: str) -> bool:
    upper = status.upper()
    return any(token in upper for token in ("ERROR", "FAILED", "CANCELLED"))


def get_counts_from_result(result) -> dict[str, int]:
    pub_result = result[0]
    data = getattr(pub_result, "data", None)
    if data is None:
        raise ValueError("Result has no data field.")

    for name in ("c", "meas", "measurements"):
        bit_array = getattr(data, name, None)
        if bit_array is not None and hasattr(bit_array, "get_counts"):
            return dict(bit_array.get_counts())

    try:
        for _, bit_array in data.items():
            if hasattr(bit_array, "get_counts"):
                return dict(bit_array.get_counts())
    except Exception:
        pass

    try:
        for name in data.keys():
            bit_array = getattr(data, name)
            if hasattr(bit_array, "get_counts"):
                return dict(bit_array.get_counts())
    except Exception:
        pass

    raise ValueError("Could not locate a BitArray with get_counts() in the result.")


def l1_distance(p: list[float], q: list[float]) -> float:
    return sum(abs(float(a) - float(b)) for a, b in zip(p, q))


def max_abs_deviation(p: list[float], q: list[float]) -> float:
    return max((abs(float(a) - float(b)) for a, b in zip(p, q)), default=float("nan"))


def chi2_statistic(counts_distribution: list[float], target: list[float], shots: int) -> float:
    total = 0.0
    for observed_probability, expected_probability in zip(counts_distribution, target):
        expected_count = shots * expected_probability
        if expected_count > 0:
            observed_count = shots * observed_probability
            total += (observed_count - expected_count) ** 2 / expected_count
    return total


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
                    if isinstance(value, (dict, list))
                    else value
                    for key, value in row.items()
                }
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch IBM Quantum Grover-Rudolph distribution results."
    )
    parser.add_argument(
        "--campaign-run",
        default=None,
        help="Path to submitted_campaign.json or its containing run directory.",
    )
    parser.add_argument("--account-file", default=str(DEFAULT_ACCOUNT_FILE))
    parser.add_argument("--name", default=None, help="Saved IBM account name.")
    parser.add_argument("--instance", default=None, help="IBM Quantum instance CRN/name.")
    parser.add_argument("--wait", action="store_true", help="Wait for unfinished jobs.")
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    submitted_path = resolve_campaign_path(args.campaign_run)
    run_dir = submitted_path.parent
    results_dir = run_dir / "results"
    results_dir.mkdir(exist_ok=True)

    records = json.loads(submitted_path.read_text(encoding="utf-8"))
    service = qiskit_runtime_service(
        account_file=args.account_file,
        name=args.name,
        instance=args.instance,
    )

    rows: list[dict[str, Any]] = []
    deadline = time.time() + args.timeout_seconds

    print(f"Campaign run: {run_dir}")
    print(f"Jobs:         {len(records)}")
    print()

    for record in records:
        job_id = record["job_id"]
        label = record["case_id"]
        job = service.job(job_id)
        status = job_status_text(job)

        while args.wait and not is_done_status(status) and not is_failed_status(status):
            if time.time() >= deadline:
                print(f"{label}: timeout while waiting; status={status}")
                break
            print(f"{label}: status={status}; waiting {args.poll_seconds}s")
            time.sleep(args.poll_seconds)
            status = job_status_text(job)

        row = {
            **record,
            "fetch_status": status,
            "fetched_utc": datetime.now(timezone.utc).isoformat(),
        }

        if is_done_status(status):
            try:
                result = job.result()
                counts = dict(sorted(get_counts_from_result(result).items()))
                shots = sum(counts.values())
                target = [float(value) for value in record["target_probabilities"]]
                observed = distribution_from_counts(counts, int(record["grid_points"]))
                tvd = total_variation_distance(observed, target)
                row.update(
                    {
                        "shots": shots,
                        "counts": counts,
                        "observed_distribution": observed,
                        "target_probabilities": target,
                        "tvd": tvd,
                        "l1_distance": l1_distance(observed, target),
                        "max_abs_deviation": max_abs_deviation(observed, target),
                        "chi2": chi2_statistic(observed, target, shots),
                        "chi2_dof": max(1, int(record["grid_points"]) - 1),
                    }
                )
                result_path = results_dir / f"result_{record['case_id']}_{job_id}.json"
                result_path.write_text(json.dumps(row, indent=2, sort_keys=True), encoding="utf-8")
                row["result_path"] = str(result_path)
                print(
                    f"{label}: DONE tvd={tvd:.6f} "
                    f"max_dev={row['max_abs_deviation']:.6f} shots={shots}"
                )
            except Exception as exc:
                row["result_error"] = str(exc)
                print(f"{label}: result extraction failed: {exc}")
        else:
            print(f"{label}: status={status}")

        rows.append(row)

    completed = [row for row in rows if row.get("tvd") is not None]
    summary = {
        "campaign_run": str(run_dir),
        "submitted_campaign": str(submitted_path),
        "fetched_utc": datetime.now(timezone.utc).isoformat(),
        "rows": rows,
        "mean_tvd": sum(float(row["tvd"]) for row in completed) / len(completed)
        if completed
        else None,
        "max_tvd": max((float(row["tvd"]) for row in completed), default=None),
    }
    summary_json = run_dir / "fetched_gr_distribution_results.json"
    summary_csv = run_dir / "fetched_gr_distribution_results.csv"
    summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(rows, summary_csv)

    print()
    print(f"Wrote JSON: {summary_json}")
    print(f"Wrote CSV:  {summary_csv}")
    if completed:
        print()
        print("Distribution summary:")
        for row in sorted(completed, key=lambda item: float(item["tvd"])):
            print(
                f"  {row['case_id']}: tvd={row['tvd']:.6f} "
                f"max_dev={row['max_abs_deviation']:.6f} "
                f"depth={row['transpiled_metrics']['depth']} "
                f"twoq={row['transpiled_metrics']['two_qubit_gate_count']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
