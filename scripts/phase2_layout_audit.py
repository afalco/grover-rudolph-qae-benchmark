#!/usr/bin/env python3
"""Credit-free Phase 2 audit over optimization levels and transpiler seeds."""

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

from grover_rudolph_qae_benchmark.benchmark_specs import get_benchmark_spec
from grover_rudolph_qae_benchmark.circuits import (
    build_amplification_circuit,
    expected_amplified_probability,
)
from grover_rudolph_qae_benchmark.ibm import DEFAULT_ACCOUNT_FILE, qiskit_runtime_service
from grover_rudolph_qae_benchmark.metrics import circuit_metrics, structural_metrics, two_qubit_depth


DEFAULT_CONFIG = PROJECT_ROOT / "experiments" / "phase2_layout_audit_ibm_fez.json"


def load_qiskit_tools():
    try:
        from qiskit import transpile
    except ImportError as exc:
        raise SystemExit(
            "qiskit is not installed. Run this script in the Qiskit environment."
        ) from exc
    return transpile


def load_aer():
    try:
        from qiskit_aer import AerSimulator
    except ImportError as exc:
        raise SystemExit(
            "qiskit-aer is not installed. Install the simulation dependencies first."
        ) from exc
    return AerSimulator


def backend_name(backend) -> str:
    name = getattr(backend, "name", None)
    if callable(name):
        return str(name())
    return str(name or backend)


def backend_status(backend) -> dict[str, Any]:
    try:
        status = backend.status()
    except Exception as exc:
        return {"status_msg": f"unavailable: {exc}"}
    return {
        "operational": getattr(status, "operational", None),
        "pending_jobs": getattr(status, "pending_jobs", None),
        "status_msg": getattr(status, "status_msg", None),
    }


def ancilla_probability_from_counts(counts: dict[str, int]) -> float:
    total = sum(int(value) for value in counts.values())
    if total == 0:
        return float("nan")
    marked = 0
    for bitstring, value in counts.items():
        clean = str(bitstring).replace(" ", "")
        if clean and clean[0] == "1":
            marked += int(value)
    return marked / total


def simulate_counts(circuit, *, shots: int, seed: int, noisy_backend=None) -> dict[str, int]:
    AerSimulator = load_aer()
    if noisy_backend is None:
        simulator = AerSimulator(seed_simulator=seed)
    else:
        simulator = AerSimulator.from_backend(noisy_backend)
        try:
            simulator.set_options(seed_simulator=seed)
        except Exception:
            pass
    job = simulator.run(circuit, shots=shots, seed_simulator=seed)
    return dict(job.result().get_counts())


def flatten_row(row: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, (dict, list, tuple)):
            flat[key] = json.dumps(value, sort_keys=True)
        else:
            flat[key] = value
    return flat


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    keys = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow(flatten_row(row))


def selected_work_items(config: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for entry in config.get("cases", []):
        for k in entry.get("ks", []):
            items.append({"case_id": entry["case_id"], "k": int(k)})
    return items


def rank_key(row: dict[str, Any]) -> tuple[float, int, int]:
    noisy_abs_dev = row.get("noisy_abs_dev")
    if noisy_abs_dev is None or isinstance(noisy_abs_dev, str) or math.isnan(float(noisy_abs_dev)):
        primary = float("inf")
    else:
        primary = float(noisy_abs_dev)
    return (
        primary,
        int(row.get("two_qubit_gate_count") or 10**9),
        int(row.get("transpiled_depth") or 10**9),
    )


def best_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: list[dict[str, Any]] = []
    keys = sorted({(row["case_id"], int(row["k"])) for row in rows})
    for case_id, k in keys:
        subset = [row for row in rows if row["case_id"] == case_id and int(row["k"]) == k]
        if subset:
            best.append(sorted(subset, key=rank_key)[0])
    return best


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 2 layout/compilation audit.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--account-file", default=str(DEFAULT_ACCOUNT_FILE))
    parser.add_argument("--name", default=None, help="Saved IBM account name.")
    parser.add_argument("--instance", default=None, help="IBM Quantum instance CRN/name.")
    parser.add_argument("--backend", default=None, help="Override backend from config.")
    parser.add_argument("--out-dir", default="results/phase2/layout_audit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    config = json.loads(config_path.read_text(encoding="utf-8"))

    transpile = load_qiskit_tools()
    service = qiskit_runtime_service(
        account_file=args.account_file,
        name=args.name,
        instance=args.instance,
    )
    backend_id = args.backend or config["backend"]
    backend = service.backend(backend_id)
    bname = backend_name(backend)
    bstatus = backend_status(backend)

    shots = int(config.get("shots", 4096))
    simulation_seed = int(config.get("simulation_seed", 12345))
    optimization_levels = [int(level) for level in config.get("optimization_levels", [2])]
    transpiler_seeds = [int(seed) for seed in config.get("transpiler_seeds", [12345])]

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    rows: list[dict[str, Any]] = []
    print(f"Audit:        {config.get('audit_id')}")
    print(f"Backend:      {bname}")
    print(f"Status:       {bstatus.get('status_msg')}")
    print(f"Pending jobs: {bstatus.get('pending_jobs')}")
    print(f"Shots/sim:    {shots}")
    print()

    for item in selected_work_items(config):
        spec = get_benchmark_spec(item["case_id"])
        k = int(item["k"])
        logical = build_amplification_circuit(spec, k, measure=True)
        expected_p = expected_amplified_probability(spec.a_exact, k)
        sm = structural_metrics(spec).to_dict()

        for opt_level in optimization_levels:
            for transpiler_seed in transpiler_seeds:
                started = time.perf_counter()
                transpiled = transpile(
                    logical,
                    backend=backend,
                    optimization_level=opt_level,
                    seed_transpiler=transpiler_seed,
                )
                transpile_seconds = time.perf_counter() - started
                metrics = circuit_metrics(transpiled)

                ideal_p = None
                noisy_p = None
                ideal_error = None
                noisy_error = None
                try:
                    ideal_counts = simulate_counts(
                        transpiled,
                        shots=shots,
                        seed=simulation_seed,
                    )
                    ideal_p = ancilla_probability_from_counts(ideal_counts)
                except Exception as exc:
                    ideal_error = str(exc)
                try:
                    noisy_counts = simulate_counts(
                        transpiled,
                        shots=shots,
                        seed=simulation_seed,
                        noisy_backend=backend,
                    )
                    noisy_p = ancilla_probability_from_counts(noisy_counts)
                except Exception as exc:
                    noisy_error = str(exc)

                row = {
                    "audit_id": config.get("audit_id"),
                    "backend": bname,
                    "backend_status": bstatus,
                    "case_id": spec.case_id,
                    "k": k,
                    "optimization_level": opt_level,
                    "transpiler_seed": transpiler_seed,
                    "simulation_seed": simulation_seed,
                    "shots": shots,
                    "a_exact": spec.a_exact,
                    "expected_p": expected_p,
                    "ideal_p": ideal_p,
                    "ideal_abs_dev": abs(ideal_p - expected_p) if ideal_p is not None else None,
                    "noisy_p": noisy_p,
                    "noisy_abs_dev": abs(noisy_p - expected_p) if noisy_p is not None else None,
                    "ideal_error": ideal_error,
                    "noisy_error": noisy_error,
                    "transpile_seconds": transpile_seconds,
                    "transpiled_depth": metrics.depth,
                    "two_qubit_gate_count": metrics.two_qubit_gate_count,
                    "two_qubit_depth": two_qubit_depth(transpiled),
                    "operation_counts": metrics.operation_counts,
                    "structural_metrics": sm,
                    "created_utc": datetime.now(timezone.utc).isoformat(),
                }
                rows.append(row)
                noisy_text = "n/a" if noisy_p is None else f"{noisy_p:.6f}"
                print(
                    f"{spec.case_id} k={k} opt={opt_level} seed={transpiler_seed}: "
                    f"depth={metrics.depth} 2q={metrics.two_qubit_gate_count} noisy={noisy_text}"
                )

    best = best_rows(rows)
    json_path = out_dir / f"phase2_layout_audit_{timestamp}.json"
    csv_path = out_dir / f"phase2_layout_audit_{timestamp}.csv"
    best_csv_path = out_dir / f"phase2_layout_audit_best_{timestamp}.csv"

    json_path.write_text(
        json.dumps(
            {
                "config": config,
                "config_path": str(config_path),
                "backend": bname,
                "backend_status": bstatus,
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "rows": rows,
                "best_rows": best,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    write_csv(rows, csv_path)
    write_csv(best, best_csv_path)

    print()
    print(f"Wrote JSON: {json_path}")
    print(f"Wrote CSV:  {csv_path}")
    print(f"Wrote best: {best_csv_path}")
    print()
    print("Best candidates:")
    for row in best:
        noisy_text = "n/a" if row.get("noisy_p") is None else f"{row['noisy_p']:.6f}"
        dev_text = "n/a" if row.get("noisy_abs_dev") is None else f"{row['noisy_abs_dev']:.6f}"
        print(
            f"  {row['case_id']} k={row['k']}: opt={row['optimization_level']} "
            f"seed={row['transpiler_seed']} depth={row['transpiled_depth']} "
            f"2q={row['two_qubit_gate_count']} noisy={noisy_text} dev={dev_text}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
