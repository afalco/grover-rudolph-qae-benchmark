#!/usr/bin/env python3
"""Diagnose the amplified gr_affine_n4 k=1 circuit before any repeat."""

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
from grover_rudolph_qae_benchmark.gr_state_preparation import (
    bitstring_to_probability_index,
    build_gr_event_amplification_circuit,
    marked_indices,
    marked_probability,
    spec_from_config,
)
from grover_rudolph_qae_benchmark.ibm import DEFAULT_ACCOUNT_FILE, qiskit_runtime_service
from grover_rudolph_qae_benchmark.metrics import circuit_metrics, two_qubit_depth


DEFAULT_CONFIG = PROJECT_ROOT / "experiments" / "phase4_gr_affine_n4_k1_diagnosis_ibm_fez.json"


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
        raise SystemExit("qiskit-aer is required.") from exc
    return AerSimulator


def backend_name(backend) -> str:
    name = getattr(backend, "name", None)
    if callable(name):
        return str(name())
    return str(name or backend)


def marked_probability_from_counts(counts: dict[str, int], marked: list[int]) -> float:
    total = sum(int(value) for value in counts.values())
    if total == 0:
        return float("nan")
    marked_set = set(int(index) for index in marked)
    successes = 0
    for bitstring, value in counts.items():
        if bitstring_to_probability_index(str(bitstring)) in marked_set:
            successes += int(value)
    return successes / total


def exact_marked_probability(circuit, marked: list[int], Statevector) -> float:
    marked_set = set(int(index) for index in marked)
    total = 0.0
    state = Statevector.from_instruction(circuit.remove_final_measurements(inplace=False))
    for bitstring, probability in state.probabilities_dict().items():
        if bitstring_to_probability_index(str(bitstring)) in marked_set:
            total += float(probability)
    return total


def simulate_noisy(circuit, *, backend, shots: int, seed: int, marked: list[int]) -> float:
    AerSimulator = load_aer()
    simulator = AerSimulator.from_backend(backend)
    try:
        simulator.set_options(seed_simulator=seed)
    except Exception:
        pass
    from qiskit import transpile

    sim_circuit = transpile(circuit, simulator, seed_transpiler=seed)
    counts = dict(simulator.run(sim_circuit, shots=shots, seed_simulator=seed).result().get_counts())
    return marked_probability_from_counts(counts, marked)


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


def rank_key(row: dict[str, Any]) -> tuple[float, int, int]:
    noisy_dev = row.get("noisy_abs_dev")
    primary = float(noisy_dev) if noisy_dev is not None else float("inf")
    return (
        primary,
        int(row.get("two_qubit_gate_count") or 10**9),
        int(row.get("transpiled_depth") or 10**9),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose gr_affine_n4 k=1.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--account-file", default=str(DEFAULT_ACCOUNT_FILE))
    parser.add_argument("--name", default=None)
    parser.add_argument("--instance", default=None)
    parser.add_argument("--backend", default=None)
    parser.add_argument("--out-dir", default="results/phase4/diagnostics")
    parser.add_argument("--skip-noisy-sim", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    config = json.loads(config_path.read_text(encoding="utf-8"))

    transpile, Statevector = load_qiskit_tools()
    service = qiskit_runtime_service(
        account_file=args.account_file,
        name=args.name,
        instance=args.instance,
    )
    backend_id = args.backend or config["backend"]
    backend = service.backend(backend_id)
    bname = backend_name(backend)

    case = dict(config["case"])
    k = int(case.pop("k"))
    implementation = str(config.get("implementation", "ucry"))
    reflection_method = str(config.get("reflection_method", "mcx"))
    event = str(config.get("event", "upper_half"))
    shots = int(config.get("shots", 4096))
    simulation_seed = int(config.get("simulation_seed", 12345))
    opt_levels = [int(value) for value in config.get("optimization_levels", [2])]
    seeds = [int(value) for value in config.get("transpiler_seeds", [12345])]

    spec = spec_from_config(case)
    marked = marked_indices(spec, event)
    a_exact = marked_probability(spec, event)
    expected_p = expected_amplified_probability(a_exact, k)
    logical = build_gr_event_amplification_circuit(
        spec,
        k,
        event=event,
        measure=True,
        implementation=implementation,
        reflection_method=reflection_method,
    )
    logical_exact_p = exact_marked_probability(logical, marked, Statevector)

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    print(f"Diagnosis:    {config.get('audit_id')}")
    print(f"Backend:      {bname}")
    print(f"Case:         {spec.case_id} k={k} event={event}")
    print(f"a_exact:      {a_exact:.8f}")
    print(f"expected p:   {expected_p:.8f}")
    print(f"logical exact:{logical_exact_p:.8f}")
    print()

    rows: list[dict[str, Any]] = []
    for opt_level in opt_levels:
        for seed in seeds:
            started = time.perf_counter()
            transpiled = transpile(
                logical,
                backend=backend,
                optimization_level=opt_level,
                seed_transpiler=seed,
            )
            transpile_seconds = time.perf_counter() - started
            metrics = circuit_metrics(transpiled)
            exact_p = None
            exact_error = None
            noisy_p = None
            noisy_error = None
            exact_p = logical_exact_p
            if not args.skip_noisy_sim:
                try:
                    noisy_p = simulate_noisy(
                        transpiled,
                        backend=backend,
                        shots=shots,
                        seed=simulation_seed,
                        marked=marked,
                    )
                except Exception as exc:
                    noisy_error = str(exc)
            row = {
                "audit_id": config.get("audit_id"),
                "backend": bname,
                "case_id": spec.case_id,
                "event": event,
                "implementation": implementation,
                "reflection_method": reflection_method,
                "k": k,
                "a_exact": a_exact,
                "expected_p": expected_p,
                "logical_exact_p": logical_exact_p,
                "optimization_level": opt_level,
                "transpiler_seed": seed,
                "simulation_seed": simulation_seed,
                "shots": shots,
                "transpiled_depth": metrics.depth,
                "two_qubit_gate_count": metrics.two_qubit_gate_count,
                "two_qubit_depth": two_qubit_depth(transpiled),
                "operation_counts": metrics.operation_counts,
                "exact_p": exact_p,
                "exact_abs_dev": abs(exact_p - expected_p) if exact_p is not None else None,
                "exact_error": exact_error,
                "noisy_p": noisy_p,
                "noisy_abs_dev": abs(noisy_p - expected_p) if noisy_p is not None else None,
                "noisy_error": noisy_error,
                "transpile_seconds": transpile_seconds,
                "created_utc": datetime.now(timezone.utc).isoformat(),
            }
            rows.append(row)
            noisy_text = "n/a" if noisy_p is None else f"{noisy_p:.6f}"
            dev_text = "n/a" if row["noisy_abs_dev"] is None else f"{row['noisy_abs_dev']:.6f}"
            exact_text = "n/a" if exact_p is None else f"{exact_p:.6f}"
            print(
                f"opt={opt_level} seed={seed}: depth={metrics.depth} "
                f"2q={metrics.two_qubit_gate_count} exact={exact_text} "
                f"noisy={noisy_text} dev={dev_text}"
            )

    best = sorted(rows, key=rank_key)[: min(10, len(rows))]
    json_path = out_dir / f"diagnose_gr_affine_k1_{timestamp}.json"
    csv_path = out_dir / f"diagnose_gr_affine_k1_{timestamp}.csv"
    best_path = out_dir / f"diagnose_gr_affine_k1_best_{timestamp}.csv"
    json_path.write_text(
        json.dumps(
            {
                "config": config,
                "config_path": str(config_path),
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
    write_csv(best, best_path)
    print()
    print(f"Wrote JSON: {json_path}")
    print(f"Wrote CSV:  {csv_path}")
    print(f"Wrote best: {best_path}")
    print()
    print("Best candidates:")
    for row in best:
        print(
            f"  opt={row['optimization_level']} seed={row['transpiler_seed']}: "
            f"depth={row['transpiled_depth']} 2q={row['two_qubit_gate_count']} "
            f"noisy={row.get('noisy_p')} dev={row.get('noisy_abs_dev')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
