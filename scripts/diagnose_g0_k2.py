#!/usr/bin/env python3
"""Diagnose the anomalous g0, k=2 hardware result without submitting QPU jobs."""

from __future__ import annotations

import argparse
import csv
import json
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
from grover_rudolph_qae_benchmark.metrics import circuit_metrics, two_qubit_depth


DEFAULT_RUN_DIR = PROJECT_ROOT / "results" / "hardware" / "phase1_calibration_ibm_fez"


def load_qiskit():
    try:
        from qiskit import transpile
        from qiskit.quantum_info import Statevector
    except ImportError as exc:
        raise SystemExit(
            "qiskit is not installed.\n"
            "Run this script in the same Python environment used for IBM submission."
        ) from exc
    return transpile, Statevector


def load_aer():
    try:
        from qiskit_aer import AerSimulator
    except ImportError as exc:
        raise SystemExit(
            "qiskit-aer is not installed.\n"
            "Install the simulation dependencies or run in the project Python environment."
        ) from exc
    return AerSimulator


def latest_run_dir() -> Path:
    candidates = sorted(DEFAULT_RUN_DIR.glob("*/fetched_campaign_results.json"))
    if not candidates:
        candidates = sorted(DEFAULT_RUN_DIR.glob("*/submitted_campaign.json"))
    if not candidates:
        raise SystemExit("No campaign run found. Pass --campaign-run explicitly.")
    return candidates[-1].parent


def resolve_run_dir(path_text: str | None) -> Path:
    if path_text is None:
        return latest_run_dir()
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if path.is_file():
        return path.parent
    return path


def backend_name(backend) -> str:
    name = getattr(backend, "name", None)
    if callable(name):
        return str(name())
    return str(name or backend)


def circuit_qasm(circuit) -> str | None:
    if hasattr(circuit, "qasm"):
        try:
            return circuit.qasm()
        except Exception:
            pass
    try:
        from qiskit import qasm2

        return qasm2.dumps(circuit)
    except Exception:
        return None


def load_qasm_circuit(path: Path):
    try:
        from qiskit import qasm2

        text = path.read_text(encoding="utf-8")
        if "sx " in text and "gate sx " not in text:
            text = text.replace(
                'include "qelib1.inc";',
                'include "qelib1.inc";\n'
                "gate sx a { u3(pi/2,-pi/2,pi/2) a; }",
                1,
            )
        return qasm2.loads(text)
    except Exception as exc:
        raise RuntimeError(f"Could not load QASM from {path}: {exc}") from exc


def remove_idle_qubits(circuit):
    """Return a copy without idle quantum wires, preserving classical bits."""
    try:
        from qiskit.converters import circuit_to_dag, dag_to_circuit

        dag = circuit_to_dag(circuit)
        idle_qubits = [wire for wire in dag.idle_wires() if wire in circuit.qubits]
        if not idle_qubits:
            return circuit
        dag.remove_qubits(*idle_qubits)
        return dag_to_circuit(dag)
    except Exception:
        return circuit


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


def exact_ancilla_probability(circuit, Statevector) -> float:
    ancilla_qubit_index = 2
    for instruction in circuit.data:
        if instruction.operation.name != "measure" or not instruction.clbits:
            continue
        clbit_index = circuit.find_bit(instruction.clbits[0]).index
        if clbit_index == 2:
            ancilla_qubit_index = circuit.find_bit(instruction.qubits[0]).index
            break

    no_measure = circuit.remove_final_measurements(inplace=False)
    state = Statevector.from_instruction(no_measure)
    probabilities = state.probabilities_dict()
    bit_position = circuit.num_qubits - 1 - ancilla_qubit_index
    marked = 0.0
    for bitstring, probability in probabilities.items():
        clean = str(bitstring).replace(" ", "")
        if len(clean) > bit_position and clean[bit_position] == "1":
            marked += float(probability)
    return marked


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


def summarize_circuit(
    *,
    label: str,
    circuit,
    expected_p: float,
    shots: int,
    seed: int,
    Statevector,
    noisy_backend=None,
    exact: bool = True,
) -> dict[str, Any]:
    metrics = circuit_metrics(circuit)
    row: dict[str, Any] = {
        "label": label,
        "num_qubits": metrics.num_qubits,
        "depth": metrics.depth,
        "two_qubit_gate_count": metrics.two_qubit_gate_count,
        "two_qubit_depth": two_qubit_depth(circuit),
        "operation_counts": metrics.operation_counts,
        "expected_p": expected_p,
    }

    if exact:
        try:
            exact_p = exact_ancilla_probability(circuit, Statevector)
            row["exact_p"] = exact_p
            row["exact_abs_dev"] = abs(exact_p - expected_p)
        except Exception as exc:
            row["exact_error"] = str(exc)

    try:
        counts = simulate_counts(circuit, shots=shots, seed=seed)
        ideal_p = ancilla_probability_from_counts(counts)
        row.update(
            {
                "ideal_shot_p": ideal_p,
                "ideal_shot_abs_dev": abs(ideal_p - expected_p),
                "ideal_counts": dict(sorted(counts.items())),
            }
        )
    except Exception as exc:
        row["ideal_sim_error"] = str(exc)

    if noisy_backend is not None:
        try:
            counts = simulate_counts(circuit, shots=shots, seed=seed, noisy_backend=noisy_backend)
            noisy_p = ancilla_probability_from_counts(counts)
            row.update(
                {
                    "noisy_shot_p": noisy_p,
                    "noisy_shot_abs_dev": abs(noisy_p - expected_p),
                    "noisy_counts": dict(sorted(counts.items())),
                }
            )
        except Exception as exc:
            row["noisy_sim_error"] = str(exc)

    return row


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    scalar_keys = [
        "label",
        "num_qubits",
        "depth",
        "two_qubit_gate_count",
        "two_qubit_depth",
        "expected_p",
        "exact_p",
        "exact_abs_dev",
        "ideal_shot_p",
        "ideal_shot_abs_dev",
        "noisy_shot_p",
        "noisy_shot_abs_dev",
        "exact_error",
        "ideal_sim_error",
        "noisy_sim_error",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=scalar_keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in scalar_keys})


def print_table(rows: list[dict[str, Any]]) -> None:
    print(
        f"{'label':<28} {'qubits':>6} {'depth':>6} {'2q':>5} "
        f"{'exact':>10} {'ideal':>10} {'noisy':>10}"
    )
    print("-" * 85)
    for row in rows:
        exact = row.get("exact_p")
        ideal = row.get("ideal_shot_p")
        noisy = row.get("noisy_shot_p")
        print(
            f"{row['label']:<28} {int(row['num_qubits']):>6} "
            f"{int(row['depth']):>6} {int(row['two_qubit_gate_count']):>5} "
            f"{exact if exact is not None else float('nan'):>10.6f} "
            f"{ideal if ideal is not None else float('nan'):>10.6f} "
            f"{noisy if noisy is not None else float('nan'):>10.6f}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose g0 k=2 locally.")
    parser.add_argument("--campaign-run", default=None)
    parser.add_argument("--case-id", default="g0")
    parser.add_argument("--k", type=int, default=2)
    parser.add_argument("--backend", default="ibm_fez")
    parser.add_argument("--account-file", default=str(DEFAULT_ACCOUNT_FILE))
    parser.add_argument("--name", default=None)
    parser.add_argument("--instance", default=None)
    parser.add_argument("--shots", type=int, default=2048)
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument("--optimization-levels", type=int, nargs="+", default=[0, 1, 2, 3])
    parser.add_argument("--skip-backend", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    transpile, Statevector = load_qiskit()

    run_dir = resolve_run_dir(args.campaign_run)
    out_dir = run_dir / "diagnostics"
    out_dir.mkdir(exist_ok=True)

    spec = get_benchmark_spec(args.case_id)
    expected_p = expected_amplified_probability(spec.a_exact, args.k)
    rows: list[dict[str, Any]] = []

    backend = None
    if not args.skip_backend and args.backend:
        service = qiskit_runtime_service(
            account_file=args.account_file,
            name=args.name,
            instance=args.instance,
        )
        backend = service.backend(args.backend)

    logical = build_amplification_circuit(spec, args.k, measure=True)
    rows.append(
        summarize_circuit(
            label=f"{args.case_id}_k{args.k}_logical",
            circuit=logical,
            expected_p=expected_p,
            shots=args.shots,
            seed=args.seed,
            Statevector=Statevector,
            noisy_backend=None,
        )
    )

    qasm_path = run_dir / "circuits" / f"{args.case_id}_k{args.k}_transpiled.qasm"
    if qasm_path.exists():
        saved_qasm = remove_idle_qubits(load_qasm_circuit(qasm_path))
        rows.append(
            summarize_circuit(
                label=f"{args.case_id}_k{args.k}_saved_qasm",
                circuit=saved_qasm,
                expected_p=expected_p,
                shots=args.shots,
                seed=args.seed,
                Statevector=Statevector,
                noisy_backend=backend,
            )
        )

    for opt_level in args.optimization_levels:
        started = time.perf_counter()
        kwargs = {
            "optimization_level": opt_level,
            "seed_transpiler": args.seed,
        }
        if backend is not None:
            kwargs["backend"] = backend
        transpiled = transpile(logical, **kwargs)
        transpile_seconds = time.perf_counter() - started
        reduced = remove_idle_qubits(transpiled)

        qasm_text = circuit_qasm(transpiled)
        if qasm_text is not None:
            qasm_out = out_dir / f"{args.case_id}_k{args.k}_opt{opt_level}_transpiled.qasm"
            qasm_out.write_text(qasm_text, encoding="utf-8")

        row = summarize_circuit(
            label=f"{args.case_id}_k{args.k}_opt{opt_level}",
            circuit=reduced,
            expected_p=expected_p,
            shots=args.shots,
            seed=args.seed,
            Statevector=Statevector,
            noisy_backend=backend,
        )
        row["transpile_seconds"] = transpile_seconds
        row["backend"] = backend_name(backend) if backend is not None else None
        rows.append(row)

    payload = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "campaign_run": str(run_dir),
        "case_id": args.case_id,
        "k": args.k,
        "a_exact": spec.a_exact,
        "expected_p": expected_p,
        "backend": backend_name(backend) if backend is not None else None,
        "shots": args.shots,
        "seed": args.seed,
        "rows": rows,
    }

    json_path = out_dir / f"diagnose_{args.case_id}_k{args.k}.json"
    csv_path = out_dir / f"diagnose_{args.case_id}_k{args.k}.csv"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(rows, csv_path)

    print(f"Campaign run: {run_dir}")
    print(f"Expected p:   {expected_p:.6f}")
    if backend is not None:
        print(f"Backend:      {backend_name(backend)}")
    print()
    print_table(rows)
    print()
    print(f"Wrote JSON: {json_path}")
    print(f"Wrote CSV:  {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
