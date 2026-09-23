#!/usr/bin/env python3
"""Phase 0 reproducible audit for g0, g1, and g2.

This script does not submit jobs to IBM Quantum hardware. It builds calibration
circuits, records logical and transpiled metrics, optionally runs ideal/noisy
simulation, and writes a candidate table before any QPU credits are spent.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from grover_rudolph_qae_benchmark.benchmark_specs import BENCHMARK_SPECS
from grover_rudolph_qae_benchmark.circuits import (
    build_amplification_circuit,
    expected_amplified_probability,
)
from grover_rudolph_qae_benchmark.ibm import DEFAULT_ACCOUNT_FILE, qiskit_runtime_service
from grover_rudolph_qae_benchmark.metrics import (
    circuit_metrics,
    structural_metrics,
    two_qubit_depth,
)


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


def status_message(backend) -> str | None:
    try:
        status = backend.status()
    except Exception:
        return None
    return getattr(status, "status_msg", None)


def backend_name(backend) -> str:
    name = getattr(backend, "name", None)
    if callable(name):
        return str(name())
    if name:
        return str(name)
    return str(backend)


def operation_count(metrics: dict[str, Any], gate: str) -> int:
    return int(metrics.get("operation_counts", {}).get(gate, 0))


def simulate_counts(circuit, *, shots: int, seed: int | None, noisy_backend=None):
    AerSimulator = load_aer()
    if AerSimulator is None:
        return None

    if noisy_backend is None:
        simulator = AerSimulator(seed_simulator=seed)
    else:
        try:
            simulator = AerSimulator.from_backend(noisy_backend)
        except Exception:
            simulator = AerSimulator(seed_simulator=seed)

    job = simulator.run(circuit, shots=shots, seed_simulator=seed)
    return job.result().get_counts()


def ancilla_probability_from_counts(counts: dict[str, int]) -> float:
    total = sum(counts.values())
    if total == 0:
        return float("nan")
    marked = sum(value for bitstring, value in counts.items() if bitstring[0] == "1")
    return marked / total


def circuit_qasm(circuit) -> str | None:
    """Return an OpenQASM 2 representation when the installed Qiskit supports it."""
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


def flatten_row(row: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Phase 0 audit without submitting QPU jobs."
    )
    parser.add_argument("--cases", nargs="+", default=["g0", "g1", "g2"])
    parser.add_argument("--ks", nargs="+", type=int, default=[0, 1, 2])
    parser.add_argument("--shots", type=int, default=2048)
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument("--backend", default=None, help="IBM backend name for transpilation/noise.")
    parser.add_argument("--account-file", default=str(DEFAULT_ACCOUNT_FILE))
    parser.add_argument("--name", default=None, help="Saved IBM account name.")
    parser.add_argument("--instance", default=None, help="IBM Quantum instance CRN/name.")
    parser.add_argument("--optimization-level", type=int, default=0)
    parser.add_argument("--initial-layout", type=int, nargs="+", default=None)
    parser.add_argument("--skip-ideal-sim", action="store_true")
    parser.add_argument("--skip-noisy-sim", action="store_true")
    parser.add_argument("--skip-transpile", action="store_true")
    parser.add_argument("--max-depth", type=int, default=5000)
    parser.add_argument("--max-two-qubit-gates", type=int, default=10000)
    parser.add_argument("--out-dir", default="results/phase0")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    transpile = load_qiskit()

    selected_specs = []
    for case_id in args.cases:
        if case_id not in BENCHMARK_SPECS:
            valid = ", ".join(sorted(BENCHMARK_SPECS))
            raise SystemExit(f"Unknown case {case_id!r}; valid cases: {valid}")
        selected_specs.append(BENCHMARK_SPECS[case_id])

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    backend = None
    backend_label = None
    if args.backend and not args.skip_transpile:
        service = qiskit_runtime_service(
            account_file=args.account_file,
            name=args.name,
            instance=args.instance,
        )
        backend = service.backend(args.backend)
        backend_label = backend_name(backend)

    rows: list[dict[str, Any]] = []
    circuits_dir = out_dir / "circuits"
    circuits_dir.mkdir(exist_ok=True)

    for spec in selected_specs:
        sm = structural_metrics(spec).to_dict()
        for k in args.ks:
            started = time.perf_counter()
            logical = build_amplification_circuit(spec, k, measure=True)
            build_seconds = time.perf_counter() - started
            logical_metrics = circuit_metrics(logical)

            qasm_path = circuits_dir / f"{spec.case_id}_k{k}_logical.qasm"
            qasm_text = circuit_qasm(logical)
            if qasm_text is None:
                qasm_path = None
            else:
                qasm_path.write_text(qasm_text, encoding="utf-8")

            ideal_counts = None
            ideal_p_hat = None
            if not args.skip_ideal_sim:
                ideal_counts = simulate_counts(logical, shots=args.shots, seed=args.seed)
                if ideal_counts is not None:
                    ideal_p_hat = ancilla_probability_from_counts(ideal_counts)

            transpiled = None
            transpile_seconds = None
            transpiled_metrics = None
            transpiled_two_qubit_depth = None
            noisy_counts = None
            noisy_p_hat = None

            if backend is not None and not args.skip_transpile:
                started = time.perf_counter()
                transpile_kwargs = {
                    "backend": backend,
                    "optimization_level": args.optimization_level,
                    "seed_transpiler": args.seed,
                }
                if args.initial_layout is not None:
                    transpile_kwargs["initial_layout"] = args.initial_layout
                transpiled = transpile(logical, **transpile_kwargs)
                transpile_seconds = time.perf_counter() - started
                transpiled_metrics = circuit_metrics(transpiled)
                transpiled_two_qubit_depth = two_qubit_depth(transpiled)

                transpiled_qasm_path = circuits_dir / f"{spec.case_id}_k{k}_transpiled.qasm"
                qasm_text = circuit_qasm(transpiled)
                if qasm_text is not None:
                    transpiled_qasm_path.write_text(qasm_text, encoding="utf-8")

                if not args.skip_noisy_sim:
                    noisy_counts = simulate_counts(
                        transpiled,
                        shots=args.shots,
                        seed=args.seed,
                        noisy_backend=backend,
                    )
                    if noisy_counts is not None:
                        noisy_p_hat = ancilla_probability_from_counts(noisy_counts)

            transpiled_depth = (
                transpiled_metrics.depth if transpiled_metrics is not None else None
            )
            transpiled_twoq = (
                transpiled_metrics.two_qubit_gate_count
                if transpiled_metrics is not None
                else None
            )

            expected_p = expected_amplified_probability(spec.a_exact, k)
            row = {
                "case_id": spec.case_id,
                "label": spec.label,
                "rule": spec.rule,
                "k": k,
                "shots": args.shots,
                "seed": args.seed,
                "a_exact": spec.a_exact,
                "expected_p_k": expected_p,
                "backend": backend_label,
                "backend_status": status_message(backend) if backend is not None else None,
                "optimization_level": args.optimization_level,
                "initial_layout": args.initial_layout,
                "build_seconds": build_seconds,
                "transpile_seconds": transpile_seconds,
                "ideal_p_hat": ideal_p_hat,
                "ideal_abs_dev": (
                    abs(ideal_p_hat - expected_p) if ideal_p_hat is not None else None
                ),
                "noisy_p_hat": noisy_p_hat,
                "noisy_abs_dev": (
                    abs(noisy_p_hat - expected_p) if noisy_p_hat is not None else None
                ),
                "risk_status": risk_status(
                    transpiled_depth=transpiled_depth,
                    transpiled_two_qubit_gates=transpiled_twoq,
                    max_depth=args.max_depth,
                    max_two_qubit_gates=args.max_two_qubit_gates,
                ),
                "notes": spec.notes,
                **sm,
                **logical_metrics.to_dict(prefix="logical_"),
            }
            if transpiled_metrics is not None:
                row.update(transpiled_metrics.to_dict(prefix="transpiled_"))
                row["transpiled_two_qubit_depth"] = transpiled_two_qubit_depth
                row["transpiled_cx_count"] = operation_count(
                    transpiled_metrics.to_dict(), "cx"
                )
                row["transpiled_ecr_count"] = operation_count(
                    transpiled_metrics.to_dict(), "ecr"
                )
                row["transpiled_cz_count"] = operation_count(
                    transpiled_metrics.to_dict(), "cz"
                )
            rows.append(row)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out_dir / f"phase0_candidate_table_{timestamp}.json"
    csv_path = out_dir / f"phase0_candidate_table_{timestamp}.csv"

    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    if rows:
        all_keys = sorted({key for row in rows for key in row})
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=all_keys)
            writer.writeheader()
            for row in rows:
                writer.writerow(flatten_row(row))

    print(f"Wrote JSON: {json_path}")
    print(f"Wrote CSV:  {csv_path}")
    print()
    print("Candidate summary:")
    for row in rows:
        print(
            f"  {row['case_id']} k={row['k']}: "
            f"logical_depth={row['logical_depth']} "
            f"transpiled_depth={row.get('transpiled_depth', 'n/a')} "
            f"twoq={row.get('transpiled_two_qubit_gate_count', 'n/a')} "
            f"status={row['risk_status']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
