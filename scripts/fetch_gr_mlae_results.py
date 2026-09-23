#!/usr/bin/env python3
"""Fetch and analyze Grover-Rudolph event-amplification MLAE results."""

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

from grover_rudolph_qae_benchmark.circuits import expected_amplified_probability
from grover_rudolph_qae_benchmark.gr_state_preparation import bitstring_to_probability_index
from grover_rudolph_qae_benchmark.ibm import DEFAULT_ACCOUNT_FILE, qiskit_runtime_service


DEFAULT_RUN_DIR = PROJECT_ROOT / "results" / "hardware" / "phase4_gr_affine_n4_mlae_k01_ibm_fez"


def latest_submitted_campaign() -> Path:
    candidates = sorted(DEFAULT_RUN_DIR.glob("*/submitted_campaign.json"))
    if not candidates:
        raise SystemExit("No submitted GR MLAE campaign found. Pass --campaign-run.")
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
    return str(getattr(status, "name", None) or status)


def is_done_status(status: str) -> bool:
    return any(token in status.upper() for token in ("DONE", "COMPLETED"))


def is_failed_status(status: str) -> bool:
    return any(token in status.upper() for token in ("ERROR", "FAILED", "CANCELLED"))


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


def marked_success_count(counts: dict[str, int], marked_indices: list[int]) -> int:
    marked = set(int(index) for index in marked_indices)
    total = 0
    for bitstring, count in counts.items():
        if bitstring_to_probability_index(str(bitstring)) in marked:
            total += int(count)
    return total


def mle_estimate(observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not observations:
        return None

    def neg_log_likelihood(a: float) -> float:
        eps = 1e-15
        total = 0.0
        for obs in observations:
            k = int(obs["k"])
            m = int(obs["marked_count"])
            shots = int(obs["shots"])
            p = min(max(expected_amplified_probability(a, k), eps), 1 - eps)
            total -= m * math.log(p) + (shots - m) * math.log(1 - p)
        return total

    grid_size = 20001
    lo = 1e-8
    hi = 1 - 1e-8
    step = (hi - lo) / (grid_size - 1)
    best_a = lo
    best_value = float("inf")
    for idx in range(grid_size):
        a = lo + idx * step
        value = neg_log_likelihood(a)
        if value < best_value:
            best_a = a
            best_value = value

    left = max(lo, best_a - 5 * step)
    right = min(hi, best_a + 5 * step)
    for _ in range(80):
        m1 = left + (right - left) / 3
        m2 = right - (right - left) / 3
        if neg_log_likelihood(m1) < neg_log_likelihood(m2):
            right = m2
        else:
            left = m1
    a_hat = (left + right) / 2
    a_exact = float(observations[0]["a_exact"])
    return {
        "case_id": observations[0]["case_id"],
        "event": observations[0]["event"],
        "ks": [int(obs["k"]) for obs in observations],
        "a_hat": a_hat,
        "a_exact": a_exact,
        "abs_error": abs(a_hat - a_exact),
        "neg_log_likelihood": neg_log_likelihood(a_hat),
    }


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
    parser = argparse.ArgumentParser(description="Fetch GR MLAE results.")
    parser.add_argument("--campaign-run", default=None)
    parser.add_argument("--account-file", default=str(DEFAULT_ACCOUNT_FILE))
    parser.add_argument("--name", default=None)
    parser.add_argument("--instance", default=None)
    parser.add_argument("--wait", action="store_true")
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
    deadline = time.time() + args.timeout_seconds
    rows: list[dict[str, Any]] = []

    print(f"Campaign run: {run_dir}")
    print(f"Jobs:         {len(records)}")
    print()
    for record in records:
        job = service.job(record["job_id"])
        status = job_status_text(job)
        label = f"{record['case_id']} k={record['k']}"
        while args.wait and not is_done_status(status) and not is_failed_status(status):
            if time.time() >= deadline:
                print(f"{label}: timeout while waiting; status={status}")
                break
            print(f"{label}: status={status}; waiting {args.poll_seconds}s")
            time.sleep(args.poll_seconds)
            status = job_status_text(job)

        row = {**record, "fetch_status": status, "fetched_utc": datetime.now(timezone.utc).isoformat()}
        if is_done_status(status):
            try:
                counts = dict(sorted(get_counts_from_result(job.result()).items()))
                shots = sum(counts.values())
                marked_count = marked_success_count(counts, record["marked_indices"])
                p_hat = marked_count / shots if shots else None
                expected_p = float(record["expected_p_k"])
                row.update(
                    {
                        "shots": shots,
                        "counts": counts,
                        "marked_count": marked_count,
                        "p_hat": p_hat,
                        "p_abs_dev": abs(p_hat - expected_p) if p_hat is not None else None,
                    }
                )
                result_path = results_dir / f"result_{record['case_id']}_k{record['k']}_{record['job_id']}.json"
                result_path.write_text(json.dumps(row, indent=2, sort_keys=True), encoding="utf-8")
                row["result_path"] = str(result_path)
                print(
                    f"{label}: DONE p_hat={p_hat:.6f} "
                    f"expected={expected_p:.6f} dev={row['p_abs_dev']:.6f}"
                )
            except Exception as exc:
                row["result_error"] = str(exc)
                print(f"{label}: result extraction failed: {exc}")
        else:
            print(f"{label}: status={status}")
        rows.append(row)

    completed = [row for row in rows if row.get("p_hat") is not None]
    estimates = []
    for case_id in sorted({row["case_id"] for row in completed}):
        case_rows = sorted([row for row in completed if row["case_id"] == case_id], key=lambda row: int(row["k"]))
        estimate = mle_estimate(case_rows)
        if estimate:
            estimates.append(estimate)

    summary = {
        "campaign_run": str(run_dir),
        "submitted_campaign": str(submitted_path),
        "fetched_utc": datetime.now(timezone.utc).isoformat(),
        "rows": rows,
        "mlae_estimates": estimates,
    }
    summary_json = run_dir / "fetched_gr_mlae_results.json"
    summary_csv = run_dir / "fetched_gr_mlae_results.csv"
    summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(rows, summary_csv)
    print()
    print(f"Wrote JSON: {summary_json}")
    print(f"Wrote CSV:  {summary_csv}")
    if estimates:
        print()
        print("MLAE estimates:")
        for estimate in estimates:
            print(
                f"  {estimate['case_id']} {estimate['event']} K={estimate['ks']}: "
                f"a_hat={estimate['a_hat']:.8f} error={estimate['abs_error']:.6f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
