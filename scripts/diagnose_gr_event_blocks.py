#!/usr/bin/env python3
"""Block-level diagnosis of Grover-Rudolph event amplification circuits.

The script reconstructs a k=1 Grover iterate as checkpoints

  A,
  O A,
  A^\dagger O A,
  S0 A^\dagger O A,
  A S0 A^\dagger O A,

and compares exact and backend-noise simulations for each prefix. It does not
submit QPU jobs.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from grover_rudolph_qae_benchmark.gr_state_preparation import (  # noqa: E402
    apply_event_phase_oracle,
    apply_zero_reflection,
    bitstring_to_probability_index,
    build_gr_state_preparation_circuit,
    distribution_from_counts,
    marked_indices,
    spec_from_config,
    total_variation_distance,
)
from grover_rudolph_qae_benchmark.ibm import (  # noqa: E402
    DEFAULT_ACCOUNT_FILE,
    qiskit_runtime_service,
)
from grover_rudolph_qae_benchmark.metrics import circuit_metrics, two_qubit_depth  # noqa: E402


DEFAULT_CONFIG = (
    PROJECT_ROOT
    / "experiments/phase4_gr_affine_n4_k1_repeat_opt3_seed12345_ibm_fez.json"
)


def load_qiskit():
    try:
        from qiskit import QuantumCircuit, transpile
        from qiskit.quantum_info import Statevector
    except ImportError as exc:
        raise SystemExit("qiskit is required.") from exc
    return QuantumCircuit, Statevector, transpile


def load_aer():
    try:
        from qiskit_aer import AerSimulator
    except ImportError as exc:
        raise SystemExit("qiskit-aer is required for noisy simulation.") from exc
    return AerSimulator


def backend_name(backend) -> str:
    name = getattr(backend, "name", None)
    if callable(name):
        return str(name())
    return str(name or backend)


def load_case_from_config(config: dict[str, Any]) -> tuple[dict[str, Any], int]:
    if "case" in config:
        case = dict(config["case"])
        return case, int(case.pop("k", 1))
    selected = list(config.get("selected_cases", []))
    if not selected:
        raise ValueError("Config needs either 'case' or 'selected_cases'.")
    case = dict(selected[0])
    ks = [int(value) for value in case.pop("ks", [1])]
    if 1 not in ks:
        raise ValueError("This block diagnosis is intended for k=1 circuits.")
    return case, 1


def add_measurements(circuit):
    measured = circuit.copy()
    creg = getattr(measured, "num_clbits", 0)
    if creg:
        measured.remove_final_measurements(inplace=True)
    measured.measure_all()
    return measured


def exact_distribution(circuit, grid_points: int, Statevector) -> list[float]:
    distribution = [0.0] * grid_points
    state = Statevector.from_instruction(circuit.remove_final_measurements(inplace=False))
    for bitstring, probability in state.probabilities_dict().items():
        distribution[bitstring_to_probability_index(str(bitstring))] += float(probability)
    return distribution


def marked_mass(distribution: list[float], marked: list[int]) -> float:
    marked_set = set(int(index) for index in marked)
    return sum(distribution[index] for index in marked_set)


def noisy_distribution(circuit, *, backend, shots: int, seed: int) -> list[float]:
    AerSimulator = load_aer()
    from qiskit import transpile

    simulator = AerSimulator.from_backend(backend)
    try:
        simulator.set_options(seed_simulator=seed)
    except Exception:
        pass
    measured = add_measurements(circuit)
    simulation_circuit = transpile(measured, simulator, seed_transpiler=seed)
    counts = dict(
        simulator.run(simulation_circuit, shots=shots, seed_simulator=seed).result().get_counts()
    )
    return distribution_from_counts(counts, 2 ** circuit.num_qubits)


def block_circuits(spec, *, event: str, implementation: str, reflection_method: str):
    QuantumCircuit, _, _ = load_qiskit()
    preparation = build_gr_state_preparation_circuit(
        spec, measure=False, implementation=implementation
    )
    inverse_preparation = preparation.inverse()

    qc = QuantumCircuit(spec.num_qubits, name=f"{spec.case_id}_block")
    blocks = []

    qc.compose(preparation, inplace=True)
    blocks.append(("A", "state_preparation", qc.copy()))

    apply_event_phase_oracle(qc, spec, event)
    blocks.append(("OA", "event_phase_oracle_after_A", qc.copy()))

    qc.compose(inverse_preparation, inplace=True)
    blocks.append(("AdgOA", "inverse_preparation_after_oracle", qc.copy()))

    apply_zero_reflection(qc, spec.num_qubits, method=reflection_method)
    blocks.append(("S0AdgOA", "zero_reflection_after_inverse", qc.copy()))

    qc.compose(preparation, inplace=True)
    blocks.append(("AS0AdgOA", "full_single_grover_iterate", qc.copy()))
    return blocks


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose GR k=1 amplification blocks.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--backend", default=None)
    parser.add_argument("--account-file", default=str(DEFAULT_ACCOUNT_FILE))
    parser.add_argument("--name", default=None)
    parser.add_argument("--instance", default=None)
    parser.add_argument("--shots", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--optimization-level", type=int, default=None)
    parser.add_argument("--skip-backend", action="store_true")
    parser.add_argument("--skip-noisy-sim", action="store_true")
    parser.add_argument("--out-dir", default="results/phase4/block_diagnostics")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    config = json.loads(config_path.read_text(encoding="utf-8"))

    _, Statevector, transpile = load_qiskit()
    case, k = load_case_from_config(config)
    if k != 1:
        raise SystemExit("Only k=1 is supported by this block diagnosis.")

    implementation = str(config.get("implementation", "ucry"))
    reflection_method = str(config.get("reflection_method", "mcx"))
    event = str(config.get("event", "upper_half"))
    shots = int(args.shots if args.shots is not None else config.get("shots", 2048))
    seed = int(args.seed if args.seed is not None else config.get("seed", 12345))
    optimization_level = int(
        args.optimization_level
        if args.optimization_level is not None
        else config.get("optimization_level", 3)
    )
    backend_id = args.backend or config.get("backend")

    spec = spec_from_config(case)
    marked = marked_indices(spec, event)
    blocks = block_circuits(
        spec,
        event=event,
        implementation=implementation,
        reflection_method=reflection_method,
    )
    full_ideal = exact_distribution(blocks[-1][2], spec.grid_points, Statevector)

    backend = None
    backend_label = None
    if backend_id and not args.skip_backend:
        service = qiskit_runtime_service(
            account_file=args.account_file,
            name=args.name,
            instance=args.instance,
        )
        backend = service.backend(str(backend_id))
        backend_label = backend_name(backend)

    rows: list[dict[str, Any]] = []
    for index, (block_id, label, circuit) in enumerate(blocks):
        measured = add_measurements(circuit)
        logical_metrics = circuit_metrics(measured)
        exact = exact_distribution(circuit, spec.grid_points, Statevector)
        noisy = None
        transpiled_metrics = None
        transpiled_2q_depth = None
        noisy_error = None
        if backend is not None:
            transpiled = transpile(
                measured,
                backend=backend,
                optimization_level=optimization_level,
                seed_transpiler=seed,
            )
            transpiled_metrics = circuit_metrics(transpiled)
            transpiled_2q_depth = two_qubit_depth(transpiled)
            if not args.skip_noisy_sim:
                try:
                    noisy = noisy_distribution(circuit, backend=backend, shots=shots, seed=seed)
                except Exception as exc:
                    noisy_error = str(exc)
        row = {
            "block_index": index,
            "block_id": block_id,
            "block_label": label,
            "case_id": spec.case_id,
            "event": event,
            "implementation": implementation,
            "reflection_method": reflection_method,
            "backend": backend_label,
            "optimization_level": optimization_level if backend is not None else None,
            "seed": seed,
            "shots": shots,
            "logical_depth": logical_metrics.depth,
            "logical_twoq": logical_metrics.two_qubit_gate_count,
            "logical_operation_counts": logical_metrics.operation_counts,
            "transpiled_depth": transpiled_metrics.depth if transpiled_metrics else None,
            "transpiled_twoq": transpiled_metrics.two_qubit_gate_count if transpiled_metrics else None,
            "transpiled_twoq_depth": transpiled_2q_depth,
            "exact_event_mass": marked_mass(exact, marked),
            "exact_tvd_to_full_k1_ideal": total_variation_distance(exact, full_ideal),
            "noisy_event_mass": marked_mass(noisy, marked) if noisy is not None else None,
            "noisy_abs_dev_to_exact_block": abs(marked_mass(noisy, marked) - marked_mass(exact, marked))
            if noisy is not None
            else None,
            "noisy_tvd_to_exact_block": total_variation_distance(noisy, exact)
            if noisy is not None
            else None,
            "noisy_error": noisy_error,
            "created_utc": datetime.now(timezone.utc).isoformat(),
        }
        rows.append(row)

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out_dir / f"diagnose_gr_event_blocks_{timestamp}.json"
    csv_path = out_dir / f"diagnose_gr_event_blocks_{timestamp}.csv"
    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(rows, csv_path)

    print(f"Wrote JSON: {json_path}")
    print(f"Wrote CSV:  {csv_path}")
    print()
    print("Block-level diagnosis:")
    for row in rows:
        print(
            f"  {row['block_id']:<8} exact={row['exact_event_mass']:.6f} "
            f"noisy={row['noisy_event_mass'] if row['noisy_event_mass'] is not None else 'n/a'} "
            f"depth={row['transpiled_depth'] if row['transpiled_depth'] is not None else 'n/a'} "
            f"2q={row['transpiled_twoq'] if row['transpiled_twoq'] is not None else 'n/a'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
