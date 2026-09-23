#!/usr/bin/env python3
"""Phase 0 audit for larger Grover-Rudolph state-preparation circuits."""

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

from grover_rudolph_qae_benchmark.gr_state_preparation import (
    build_gr_state_preparation_circuit,
    distribution_from_counts,
    dyadic_stage_angles,
    spec_from_config,
    total_variation_distance,
)
from grover_rudolph_qae_benchmark.ibm import DEFAULT_ACCOUNT_FILE, qiskit_runtime_service
from grover_rudolph_qae_benchmark.metrics import circuit_metrics, two_qubit_depth


DEFAULT_CONFIG = PROJECT_ROOT / "experiments" / "phase0_gr_larger_grids_ibm_fez.json"


def load_qiskit():
    try:
        from qiskit import transpile
    except ImportError as exc:
        raise SystemExit(
            "qiskit is not installed.\n"
            "Install project dependencies with:\n\n"
            "  python3 -m pip install -e '.[simulation]'\n"
        ) from exc
    return transpile


def load_aer():
    try:
        from qiskit_aer import AerSimulator
    except ImportError:
        return None
    return AerSimulator


def backend_name(backend) -> str:
    name = getattr(backend, "name", None)
    if callable(name):
        return str(name())
    if name:
        return str(name)
    return str(backend)


def status_message(backend) -> str | None:
    try:
        status = backend.status()
    except Exception:
        return None
    return getattr(status, "status_msg", None)


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


def simulate_counts(circuit, *, shots: int, seed: int | None, noisy_backend=None):
    AerSimulator = load_aer()
    if AerSimulator is None:
        return None
    from qiskit import transpile

    if noisy_backend is None:
        simulator = AerSimulator(seed_simulator=seed)
    else:
        try:
            simulator = AerSimulator.from_backend(noisy_backend)
        except Exception:
            simulator = AerSimulator(seed_simulator=seed)
    simulation_circuit = transpile(circuit, simulator, seed_transpiler=seed)
    job = simulator.run(simulation_circuit, shots=shots, seed_simulator=seed)
    return job.result().get_counts()


def exact_distribution(circuit, grid_points: int) -> list[float] | None:
    try:
        from qiskit.quantum_info import Statevector
    except ImportError:
        return None
    try:
        probabilities = [0.0] * grid_points
        state = Statevector.from_instruction(circuit)
        for bitstring, probability in state.probabilities_dict().items():
            probabilities[int(bitstring[::-1], 2)] += float(probability)
        return probabilities
    except Exception:
        return None


def flatten_row(row: dict[str, Any]) -> dict[str, Any]:
    flat = {}
    for key, value in row.items():
        if isinstance(value, (dict, list, tuple)):
            flat[key] = json.dumps(value, sort_keys=True)
        else:
            flat[key] = value
    return flat


def risk_status(
    *,
    transpiled_depth: int | None,
    transpiled_two_qubit_gates: int | None,
    max_depth: int,
    max_two_qubit_gates: int,
) -> str:
    if transpiled_depth is None or transpiled_two_qubit_gates is None:
        return "not_transpiled"
    if transpiled_depth > max_depth:
        return "reject_depth"
    if transpiled_two_qubit_gates > max_two_qubit_gates:
        return "reject_two_qubit_gates"
    return "candidate"


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit larger Grover-Rudolph state-preparation circuits before QPU use."
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--backend", default=None)
    parser.add_argument("--account-file", default=str(DEFAULT_ACCOUNT_FILE))
    parser.add_argument("--name", default=None)
    parser.add_argument("--instance", default=None)
    parser.add_argument("--shots", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--optimization-level", type=int, default=None)
    parser.add_argument(
        "--implementation",
        choices=["ucry", "direct"],
        default=None,
        help="Grover-Rudolph circuit implementation. 'ucry' uses uniformly controlled Ry stages.",
    )
    parser.add_argument("--initial-layout", type=int, nargs="+", default=None)
    parser.add_argument("--skip-transpile", action="store_true")
    parser.add_argument("--skip-ideal-sim", action="store_true")
    parser.add_argument("--skip-noisy-sim", action="store_true")
    parser.add_argument("--max-depth", type=int, default=None)
    parser.add_argument("--max-two-qubit-gates", type=int, default=None)
    parser.add_argument("--out-dir", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    config = load_config(config_path)

    shots = int(args.shots if args.shots is not None else config.get("shots", 2048))
    seed = int(args.seed if args.seed is not None else config.get("seed", 12345))
    optimization_level = int(
        args.optimization_level
        if args.optimization_level is not None
        else config.get("optimization_level", 2)
    )
    implementation = str(args.implementation or config.get("implementation", "ucry"))
    max_depth = int(args.max_depth if args.max_depth is not None else config.get("max_depth", 5000))
    max_twoq = int(
        args.max_two_qubit_gates
        if args.max_two_qubit_gates is not None
        else config.get("max_two_qubit_gates", 10000)
    )
    backend_arg = args.backend if args.backend is not None else config.get("backend")
    out_dir = Path(args.out_dir or config.get("out_dir", "results/phase0_gr"))
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    circuits_dir = out_dir / "circuits"
    circuits_dir.mkdir(exist_ok=True)

    transpile = load_qiskit()
    backend = None
    backend_label = None
    if backend_arg and not args.skip_transpile:
        service = qiskit_runtime_service(
            account_file=args.account_file,
            name=args.name,
            instance=args.instance,
        )
        backend = service.backend(str(backend_arg))
        backend_label = backend_name(backend)

    rows: list[dict[str, Any]] = []
    for case in config.get("cases", []):
        spec = spec_from_config(case)
        angles = dyadic_stage_angles(spec)
        started = time.perf_counter()
        logical = build_gr_state_preparation_circuit(
            spec, measure=True, implementation=implementation
        )
        logical_unmeasured = build_gr_state_preparation_circuit(
            spec, measure=False, implementation=implementation
        )
        build_seconds = time.perf_counter() - started
        logical_metrics = circuit_metrics(logical)

        qasm_text = circuit_qasm(logical)
        logical_qasm_path = None
        if qasm_text is not None:
            logical_qasm_path = circuits_dir / f"{spec.case_id}_logical.qasm"
            logical_qasm_path.write_text(qasm_text, encoding="utf-8")

        ideal_counts = None
        exact_tvd = None
        exact_probs = exact_distribution(logical_unmeasured, spec.grid_points)
        if exact_probs is not None:
            exact_tvd = total_variation_distance(exact_probs, spec.probabilities)

        ideal_tvd = None
        if not args.skip_ideal_sim:
            ideal_counts = simulate_counts(logical, shots=shots, seed=seed)
            if ideal_counts is not None:
                ideal_distribution = distribution_from_counts(ideal_counts, spec.grid_points)
                ideal_tvd = total_variation_distance(
                    ideal_distribution, spec.probabilities
                )

        transpiled = None
        transpile_seconds = None
        transpiled_metrics = None
        transpiled_twoq_depth = None
        noisy_tvd = None

        if backend is not None and not args.skip_transpile:
            started = time.perf_counter()
            transpile_kwargs = {
                "backend": backend,
                "optimization_level": optimization_level,
                "seed_transpiler": seed,
            }
            if args.initial_layout is not None:
                transpile_kwargs["initial_layout"] = args.initial_layout
            transpiled = transpile(logical, **transpile_kwargs)
            transpile_seconds = time.perf_counter() - started
            transpiled_metrics = circuit_metrics(transpiled)
            transpiled_twoq_depth = two_qubit_depth(transpiled)

            qasm_text = circuit_qasm(transpiled)
            if qasm_text is not None:
                (circuits_dir / f"{spec.case_id}_transpiled.qasm").write_text(
                    qasm_text, encoding="utf-8"
                )

            if not args.skip_noisy_sim:
                noisy_counts = simulate_counts(transpiled, shots=shots, seed=seed, noisy_backend=backend)
                if noisy_counts is not None:
                    noisy_distribution = distribution_from_counts(noisy_counts, spec.grid_points)
                    noisy_tvd = total_variation_distance(
                        noisy_distribution, spec.probabilities
                    )

        transpiled_depth = (
            transpiled_metrics.depth if transpiled_metrics is not None else None
        )
        transpiled_twoq = (
            transpiled_metrics.two_qubit_gate_count
            if transpiled_metrics is not None
            else None
        )
        row = {
            "case_id": spec.case_id,
            "label": spec.label,
            "source": spec.source,
            "paper_basis": "dyadic_probability_tree_conditional_Ry",
            "num_qubits": spec.num_qubits,
            "grid_points": spec.grid_points,
            "shots": shots,
            "seed": seed,
            "backend": backend_label,
            "backend_status": status_message(backend) if backend is not None else None,
            "optimization_level": optimization_level,
            "implementation": implementation,
            "initial_layout": args.initial_layout,
            "conditional_rotation_count": len(angles),
            "nonzero_conditional_rotation_count": sum(
                1 for angle in angles if abs(angle.theta) >= 1e-15
            ),
            "max_abs_theta": max((abs(angle.theta) for angle in angles), default=0.0),
            "min_nonzero_mass": min(
                (angle.mass for angle in angles if angle.mass > 0.0), default=0.0
            ),
            "target_probabilities": list(spec.probabilities),
            "build_seconds": build_seconds,
            "transpile_seconds": transpile_seconds,
            "logical_qasm_path": str(logical_qasm_path) if logical_qasm_path else None,
            "exact_tvd": exact_tvd,
            "ideal_tvd": ideal_tvd,
            "noisy_tvd": noisy_tvd,
            "risk_status": risk_status(
                transpiled_depth=transpiled_depth,
                transpiled_two_qubit_gates=transpiled_twoq,
                max_depth=max_depth,
                max_two_qubit_gates=max_twoq,
            ),
            "notes": spec.notes,
            **logical_metrics.to_dict(prefix="logical_"),
        }
        if transpiled_metrics is not None:
            row.update(transpiled_metrics.to_dict(prefix="transpiled_"))
            row["transpiled_two_qubit_depth"] = transpiled_twoq_depth
        rows.append(row)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out_dir / f"phase0_gr_audit_{timestamp}.json"
    csv_path = out_dir / f"phase0_gr_audit_{timestamp}.csv"
    best_path = out_dir / f"phase0_gr_audit_best_{timestamp}.csv"

    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    if rows:
        all_keys = sorted({key for row in rows for key in row})
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=all_keys)
            writer.writeheader()
            for row in rows:
                writer.writerow(flatten_row(row))

        best_rows = sorted(
            rows,
            key=lambda row: (
                row["risk_status"] != "candidate",
                row.get("transpiled_two_qubit_gate_count") is None,
                row.get("transpiled_two_qubit_gate_count") or 10**12,
                row.get("transpiled_depth") or 10**12,
            ),
        )
        with best_path.open("w", newline="", encoding="utf-8") as handle:
            keys = [
                "case_id",
                "num_qubits",
                "grid_points",
                "risk_status",
                "logical_depth",
                "transpiled_depth",
                "transpiled_two_qubit_gate_count",
                "transpiled_two_qubit_depth",
                "exact_tvd",
                "ideal_tvd",
                "noisy_tvd",
            ]
            writer = csv.DictWriter(handle, fieldnames=keys)
            writer.writeheader()
            for row in best_rows:
                writer.writerow({key: row.get(key) for key in keys})

    print(f"Wrote JSON: {json_path}")
    print(f"Wrote CSV:  {csv_path}")
    print(f"Wrote best: {best_path}")
    print()
    print("Grover-Rudolph candidate summary:")
    for row in rows:
        print(
            f"  {row['case_id']}: n={row['num_qubits']} "
            f"grid={row['grid_points']} logical_depth={row['logical_depth']} "
            f"transpiled_depth={row.get('transpiled_depth', 'n/a')} "
            f"twoq={row.get('transpiled_two_qubit_gate_count', 'n/a')} "
            f"exact_tvd={row.get('exact_tvd')} "
            f"ideal_tvd={row.get('ideal_tvd')} noisy_tvd={row.get('noisy_tvd')} "
            f"status={row['risk_status']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
