#!/usr/bin/env python3
"""Backend-aware noisy audit for the first non-degenerate finite-law QAE candidate.

This script does not submit QPU jobs. With backend access it transpiles against
the selected IBM backend and uses AerSimulator.from_backend for noisy counts.
With --skip-backend it performs a local basis-gate transpilation only.
"""

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
SCRIPT_ROOT = PROJECT_ROOT / "scripts"
SRC_ROOT = PROJECT_ROOT / "src"
for path in (SRC_ROOT, SCRIPT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from grover_rudolph_qae_benchmark.angle_structure import qoi_function_values  # noqa: E402
from grover_rudolph_qae_benchmark.circuits import expected_amplified_probability  # noqa: E402
from grover_rudolph_qae_benchmark.gr_state_preparation import bitstring_to_probability_index, spec_from_config  # noqa: E402
from grover_rudolph_qae_benchmark.ibm import DEFAULT_ACCOUNT_FILE, qiskit_runtime_service  # noqa: E402
from grover_rudolph_qae_benchmark.metrics import circuit_metrics, two_qubit_depth  # noqa: E402
from phase12_qae_amplitude_audit import (  # noqa: E402
    build_qae_circuit,
    expectation,
    load_cases,
    marked_probability_statevector,
)


DEFAULT_CONFIG = PROJECT_ROOT / "experiments/phase13_qae_noisy_audit_sin2_n3_sinpix.json"


def load_qiskit_tools():
    try:
        from qiskit import transpile
        from qiskit.quantum_info import Statevector
    except ImportError as exc:
        raise SystemExit("qiskit is required.") from exc
    return transpile, Statevector


def load_aer():
    try:
        from qiskit_aer import AerSimulator
    except ImportError as exc:
        raise SystemExit("qiskit-aer is required for noisy simulation.") from exc
    return AerSimulator


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def backend_name(backend) -> str:
    name = getattr(backend, "name", None)
    if callable(name):
        return str(name())
    return str(name or backend)


def marked_probability_from_counts(counts: dict[str, int], objective_qubit: int) -> float:
    total = sum(int(value) for value in counts.values())
    if total <= 0:
        return float("nan")
    marked = 0
    for bitstring, count in counts.items():
        index = bitstring_to_probability_index(str(bitstring))
        if (index >> objective_qubit) & 1:
            marked += int(count)
    return marked / total


def simulate_noisy_probability(circuit, *, simulator, shots: int, seed: int, objective_qubit: int) -> float:
    from qiskit import transpile

    sim_circuit = transpile(circuit, simulator, seed_transpiler=seed)
    counts = dict(simulator.run(sim_circuit, shots=shots, seed_simulator=seed).result().get_counts())
    return marked_probability_from_counts(counts, objective_qubit)


def mle_estimate(observations: list[dict[str, Any]], grid_size: int) -> dict[str, Any] | None:
    if not observations:
        return None
    best_a = 0.0
    best_ll = -float("inf")
    eps = 1e-15
    for idx in range(grid_size):
        a = idx / (grid_size - 1)
        ll = 0.0
        for obs in observations:
            k = int(obs["k"])
            shots = int(obs["shots"])
            p_hat = float(obs["p_hat"])
            successes = round(p_hat * shots)
            p = min(max(expected_amplified_probability(a, k), eps), 1.0 - eps)
            ll += successes * math.log(p) + (shots - successes) * math.log(1.0 - p)
        if ll > best_ll:
            best_ll = ll
            best_a = a
    return {"a_hat": best_a, "log_likelihood": best_ll}


def flatten(row: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key, value in row.items():
        if isinstance(value, (dict, list, tuple)):
            out[key] = json.dumps(value, sort_keys=True)
        else:
            out[key] = value
    return out


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    keys = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow(flatten(row))


def risk_status(depth: int, twoq: int, *, max_depth: int, max_twoq: int) -> str:
    if depth > max_depth:
        return "reject_depth"
    if twoq > max_twoq:
        return "reject_twoq"
    return "candidate"


def campaign_status(
    *,
    rows: list[dict[str, Any]],
    expected_ks: list[int],
    mlae_error: float | None,
    max_single_dev: float | None,
    max_mlae_error: float,
    max_probability_dev: float,
) -> str:
    if len(rows) < len(expected_ks):
        return "reject_incomplete_viable_schedule"
    if any(row["risk_status"] != "candidate" for row in rows):
        return "reject_cost"
    if mlae_error is None or max_single_dev is None:
        return "candidate_needs_noisy_model"
    if max_single_dev > max_probability_dev:
        return "reject_noisy_probability_bias"
    if mlae_error > max_mlae_error:
        return "reject_noisy_mlae_error"
    return "hardware_microcampaign_candidate"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Noisy audit for finite-law QAE candidate.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--backend", default=None)
    parser.add_argument("--account-file", default=str(DEFAULT_ACCOUNT_FILE))
    parser.add_argument("--name", default=None)
    parser.add_argument("--instance", default=None)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--skip-backend", action="store_true")
    parser.add_argument("--skip-noisy-sim", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = json.loads(resolve_path(args.config).read_text(encoding="utf-8"))
    source_cases = load_cases(resolve_path(str(config["source_config"])))
    case_id = str(config["case_id"])
    quantity = str(config["quantity"])
    implementation = str(config.get("implementation", "ucry"))
    reflection_method = str(config.get("reflection_method", "mcx"))
    shots = int(config.get("shots", 4096))
    simulation_seed = int(config.get("simulation_seed", 20260809))
    opt_levels = [int(value) for value in config.get("optimization_levels", [2])]
    seeds = [int(value) for value in config.get("transpiler_seeds", [12345])]
    ks = [int(value) for value in config.get("ks", [0, 1, 2])]
    basis_gates = list(config.get("basis_gates", ["cz", "id", "rz", "sx", "x"]))
    max_depth = int(config.get("max_depth", 500))
    max_twoq = int(config.get("max_two_qubit_gates", 100))
    max_mlae_error = float(config.get("max_noisy_mlae_error", 0.03))
    max_probability_dev = float(config.get("max_noisy_single_probability_deviation", 0.08))
    mle_grid_size = int(config.get("mle_grid_size", 10001))

    out_dir = resolve_path(args.out_dir or str(config.get("out_dir", "results/phase13/qae_noisy_audit")))
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    transpile, Statevector = load_qiskit_tools()
    spec = spec_from_config(source_cases[case_id])
    qvalues = qoi_function_values(quantity, spec.num_qubits)
    a_exact = expectation(spec.probabilities, qvalues)

    backend = None
    simulator = None
    backend_label = "basis_gates_only"
    if not args.skip_backend:
        backend_id = args.backend or str(config["backend"])
        service = qiskit_runtime_service(
            account_file=args.account_file,
            name=args.name,
            instance=args.instance,
        )
        backend = service.backend(backend_id)
        backend_label = backend_name(backend)
        if not args.skip_noisy_sim:
            AerSimulator = load_aer()
            simulator = AerSimulator.from_backend(backend)

    rows: list[dict[str, Any]] = []
    best_by_k: dict[int, dict[str, Any]] = {}
    for k in ks:
        logical = build_qae_circuit(
            spec,
            quantity,
            k,
            implementation=implementation,
            reflection_method=reflection_method,
            measure=True,
        )
        objective_qubit = spec.num_qubits
        statevector_p = marked_probability_statevector(logical, objective_qubit, Statevector)
        expected_p = expected_amplified_probability(a_exact, k)
        logical_metrics = circuit_metrics(logical)
        for opt_level in opt_levels:
            for seed in seeds:
                started = time.perf_counter()
                if backend is not None:
                    transpiled = transpile(
                        logical,
                        backend=backend,
                        optimization_level=opt_level,
                        seed_transpiler=seed,
                    )
                else:
                    transpiled = transpile(
                        logical,
                        basis_gates=basis_gates,
                        optimization_level=opt_level,
                        seed_transpiler=seed,
                    )
                transpile_seconds = time.perf_counter() - started
                metrics = circuit_metrics(transpiled)
                noisy_p = None
                noisy_error = None
                if simulator is not None:
                    try:
                        noisy_p = simulate_noisy_probability(
                            transpiled,
                            simulator=simulator,
                            shots=shots,
                            seed=simulation_seed,
                            objective_qubit=objective_qubit,
                        )
                    except Exception as exc:
                        noisy_error = str(exc)
                row = {
                    "audit_id": config["audit_id"],
                    "backend": backend_label,
                    "case_id": case_id,
                    "quantity": quantity,
                    "a_exact": a_exact,
                    "k": k,
                    "shots": shots,
                    "expected_p_k": expected_p,
                    "statevector_p_k": statevector_p,
                    "statevector_abs_dev": abs(statevector_p - expected_p),
                    "noisy_p_k": noisy_p,
                    "noisy_abs_dev": abs(noisy_p - expected_p) if noisy_p is not None else None,
                    "noisy_error": noisy_error,
                    "logical_depth": logical_metrics.depth,
                    "logical_twoq": logical_metrics.two_qubit_gate_count,
                    "optimization_level": opt_level,
                    "transpiler_seed": seed,
                    "transpiled_depth": metrics.depth,
                    "two_qubit_gate_count": metrics.two_qubit_gate_count,
                    "two_qubit_depth": two_qubit_depth(transpiled),
                    "operation_counts": metrics.operation_counts,
                    "risk_status": risk_status(
                        metrics.depth,
                        metrics.two_qubit_gate_count,
                        max_depth=max_depth,
                        max_twoq=max_twoq,
                    ),
                    "transpile_seconds": transpile_seconds,
                    "created_utc": datetime.now(timezone.utc).isoformat(),
                }
                rows.append(row)
        candidates = [row for row in rows if int(row["k"]) == k and row["risk_status"] == "candidate"]
        if candidates:
            best_by_k[k] = min(
                candidates,
                key=lambda row: (
                    float(row["noisy_abs_dev"]) if row["noisy_abs_dev"] is not None else float("inf"),
                    int(row["two_qubit_gate_count"]),
                    int(row["transpiled_depth"]),
                ),
            )

    best_rows = [best_by_k[k] for k in ks if k in best_by_k]
    noisy_observations = [
        {"k": row["k"], "shots": row["shots"], "p_hat": row["noisy_p_k"]}
        for row in best_rows
        if row["noisy_p_k"] is not None
    ]
    estimate = mle_estimate(noisy_observations, mle_grid_size) if len(noisy_observations) == len(ks) else None
    mlae_error = abs(float(estimate["a_hat"]) - a_exact) if estimate else None
    single_devs = [float(row["noisy_abs_dev"]) for row in best_rows if row["noisy_abs_dev"] is not None]
    max_single_dev = max(single_devs) if single_devs else None
    summary = {
        "audit_id": config["audit_id"],
        "backend": backend_label,
        "case_id": case_id,
        "quantity": quantity,
        "a_exact": a_exact,
        "ks": ks,
        "shots": shots,
        "best_rows": best_rows,
        "mle_grid_size": mle_grid_size,
        "noisy_mlae_estimate": estimate,
        "noisy_mlae_abs_error": mlae_error,
        "max_noisy_single_probability_deviation": max_single_dev,
        "status": campaign_status(
            rows=best_rows,
            expected_ks=ks,
            mlae_error=mlae_error,
            max_single_dev=max_single_dev,
            max_mlae_error=max_mlae_error,
            max_probability_dev=max_probability_dev,
        ),
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }

    json_path = out_dir / f"phase13_qae_noisy_audit_{timestamp}.json"
    csv_path = out_dir / f"phase13_qae_noisy_audit_{timestamp}.csv"
    best_json = out_dir / f"phase13_qae_noisy_audit_best_{timestamp}.json"
    best_csv = out_dir / f"phase13_qae_noisy_audit_best_{timestamp}.csv"
    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    best_json.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(rows, csv_path)
    write_csv(best_rows, best_csv)

    print(f"Wrote JSON: {json_path}")
    print(f"Wrote CSV:  {csv_path}")
    print(f"Wrote best JSON: {best_json}")
    print(f"Wrote best CSV:  {best_csv}")
    print()
    print(f"Phase 13 noisy QAE audit: {case_id} / {quantity}")
    print(f"Backend: {backend_label}")
    print(f"a_exact: {a_exact:.8f}")
    for row in best_rows:
        noisy_text = "n/a" if row["noisy_p_k"] is None else f"{row['noisy_p_k']:.6f}"
        dev_text = "n/a" if row["noisy_abs_dev"] is None else f"{row['noisy_abs_dev']:.6f}"
        print(
            f"  k={row['k']}: opt={row['optimization_level']} seed={row['transpiler_seed']} "
            f"depth={row['transpiled_depth']} 2q={row['two_qubit_gate_count']} "
            f"expected={row['expected_p_k']:.6f} noisy={noisy_text} dev={dev_text}"
        )
    if estimate:
        print(f"MLAE noisy estimate: a_hat={estimate['a_hat']:.8f} error={mlae_error:.6f}")
    print(f"Status: {summary['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
