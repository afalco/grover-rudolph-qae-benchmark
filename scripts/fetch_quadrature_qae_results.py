#!/usr/bin/env python3
"""Fetch and analyze quadrature-rule QAE/MLAE hardware campaigns."""

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
from grover_rudolph_qae_benchmark.ibm import DEFAULT_ACCOUNT_FILE, qiskit_runtime_service


DEFAULT_RUN_DIR = PROJECT_ROOT / "results" / "hardware" / "phase15b_quadrature_microcampaign_ibm_kingston"


def latest_submitted_campaign() -> Path:
    candidates = sorted(DEFAULT_RUN_DIR.glob("*/submitted_campaign.json"))
    if not candidates:
        raise SystemExit("No submitted quadrature campaign found.")
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
    raise ValueError("Could not locate BitArray counts in result.")


def objective_success_count(counts: dict[str, int]) -> int:
    total = 0
    for bitstring, value in counts.items():
        clean = str(bitstring).replace(" ", "")
        if clean and clean[0] == "1":
            total += int(value)
    return total


def mle_estimate(observations: list[dict[str, Any]], grid_size: int = 20001) -> float:
    eps = 1e-15

    def nll(a: float) -> float:
        total = 0.0
        for obs in observations:
            p = min(max(expected_amplified_probability(a, int(obs["k"])), eps), 1.0 - eps)
            successes = int(obs["m_objective"])
            shots = int(obs["shots"])
            total -= successes * math.log(p) + (shots - successes) * math.log(1.0 - p)
        return total

    lo = 1e-8
    hi = 1.0 - 1e-8
    step = (hi - lo) / (grid_size - 1)
    best_a = lo
    best_value = float("inf")
    for idx in range(grid_size):
        a = lo + idx * step
        value = nll(a)
        if value < best_value:
            best_a = a
            best_value = value
    left = max(lo, best_a - 5 * step)
    right = min(hi, best_a + 5 * step)
    for _ in range(80):
        m1 = left + (right - left) / 3.0
        m2 = right - (right - left) / 3.0
        if nll(m1) < nll(m2):
            right = m2
        else:
            left = m1
    return (left + right) / 2.0


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
    parser = argparse.ArgumentParser(description="Fetch quadrature QAE campaign results.")
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
        label = f"{record['group_id']} k={record['k']}"
        job = service.job(record["job_id"])
        status = job_status_text(job)
        while args.wait and not is_done_status(status) and not is_failed_status(status):
            if time.time() >= deadline:
                print(f"{label}: timeout while waiting; status={status}")
                break
            print(f"{label}: status={status}; waiting {args.poll_seconds}s")
            time.sleep(args.poll_seconds)
            status = job_status_text(job)

        row = {**record, "fetch_status": status, "fetched_utc": datetime.now(timezone.utc).isoformat()}
        if is_done_status(status):
            result = job.result()
            counts = get_counts_from_result(result)
            shots = sum(counts.values())
            m_objective = objective_success_count(counts)
            p_hat = m_objective / shots if shots else None
            expected = float(record["expected_p_k"])
            row.update(
                {
                    "shots": shots,
                    "m_objective": m_objective,
                    "p_hat": p_hat,
                    "p_abs_dev": abs(p_hat - expected) if p_hat is not None else None,
                    "counts": dict(sorted(counts.items())),
                }
            )
            print(
                f"{label}: DONE p_hat={p_hat:.6f} expected={expected:.6f} "
                f"dev={abs(p_hat - expected):.6f}"
            )
        else:
            print(f"{label}: status={status}")
        rows.append(row)

    summary_rows: list[dict[str, Any]] = []
    for group_id in sorted({row["group_id"] for row in rows}):
        group = [row for row in rows if row["group_id"] == group_id and "m_objective" in row]
        expected_count = len({int(row["k"]) for row in rows if row["group_id"] == group_id})
        if len(group) != expected_count:
            continue
        a_hat = mle_estimate(group)
        q_value = float(group[0]["quadrature_value"])
        true_value = float(group[0]["true_integral"])
        summary_rows.append(
            {
                "group_id": group_id,
                "case_id": group[0]["case_id"],
                "family": group[0].get("family"),
                "num_qubits": group[0]["num_qubits"],
                "rule": group[0]["rule"],
                "ks": [int(row["k"]) for row in sorted(group, key=lambda item: int(item["k"]))],
                "quadrature_value": q_value,
                "true_integral": true_value,
                "discretization_error": abs(q_value - true_value),
                "mlae_a_hat": a_hat,
                "mlae_estimation_error": abs(a_hat - q_value),
                "total_error": abs(a_hat - true_value),
                "total_shots": sum(int(row["shots"]) for row in group),
                "oracle_queries": sum(int(row["shots"]) * (2 * int(row["k"]) + 1) for row in group),
            }
        )

    json_path = run_dir / "fetched_quadrature_qae_results.json"
    csv_path = run_dir / "fetched_quadrature_qae_results.csv"
    summary_json_path = run_dir / "quadrature_qae_mlae_summary.json"
    summary_csv_path = run_dir / "quadrature_qae_mlae_summary.csv"
    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(rows, csv_path)
    summary_json_path.write_text(json.dumps(summary_rows, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(summary_rows, summary_csv_path)
    print()
    print(f"Wrote JSON:    {json_path}")
    print(f"Wrote CSV:     {csv_path}")
    print(f"Wrote summary: {summary_csv_path}")
    print()
    print("MLAE quadrature estimates:")
    for row in summary_rows:
        print(
            f"  {row['group_id']}: a_hat={row['mlae_a_hat']:.8f} "
            f"q={row['quadrature_value']:.8f} true={row['true_integral']:.8f} "
            f"est_err={row['mlae_estimation_error']:.6f} total_err={row['total_error']:.6f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
