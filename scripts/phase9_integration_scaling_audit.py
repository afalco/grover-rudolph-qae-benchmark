#!/usr/bin/env python3
"""Phase 9 audit for scaling algebraic integration from GR probability laws."""

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

from grover_rudolph_qae_benchmark.gr_state_preparation import (  # noqa: E402
    build_gr_state_preparation_circuit,
    distribution_from_counts,
    midpoint_grid,
    spec_from_config,
    total_variation_distance,
)
from grover_rudolph_qae_benchmark.ibm import DEFAULT_ACCOUNT_FILE, qiskit_runtime_service  # noqa: E402
from grover_rudolph_qae_benchmark.metrics import circuit_metrics, two_qubit_depth  # noqa: E402


DEFAULT_CONFIG = PROJECT_ROOT / "experiments" / "phase9_integration_scaling_audit.json"


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
        state = Statevector.from_instruction(circuit)
    except Exception:
        return None
    probabilities = [0.0] * grid_points
    for bitstring, probability in state.probabilities_dict().items():
        probabilities[int(bitstring[::-1], 2)] += float(probability)
    return probabilities


def density_value(function: dict[str, Any], x: float) -> float:
    kind = str(function["kind"])
    if kind == "constant":
        return float(function.get("value", 1.0))
    if kind == "sin2_half":
        return math.sin(math.pi * x / 2.0) ** 2
    if kind == "sin2":
        return math.sin(math.pi * x) ** 2
    if kind == "affine":
        return float(function.get("offset", 0.0)) + float(function.get("slope", 1.0)) * x
    if kind == "beta_bump":
        alpha = float(function.get("alpha", 2.0))
        beta = float(function.get("beta", 5.0))
        scale = float(function.get("scale", 1.0))
        return scale * (x ** (alpha - 1.0)) * ((1.0 - x) ** (beta - 1.0))
    if kind == "gaussian":
        mean = float(function.get("mean", 0.5))
        sigma = float(function.get("sigma", 0.15))
        return math.exp(-0.5 * ((x - mean) / sigma) ** 2)
    raise ValueError(f"Unknown density kind {kind!r}")


def qoi_value(name: str, x: float) -> float:
    if name == "upper_half":
        return 1.0 if x >= 0.5 else 0.0
    if name == "mean_x":
        return x
    if name == "second_moment_x2":
        return x * x
    if name == "sin_pi_x":
        return math.sin(math.pi * x)
    if name == "call_x_minus_half":
        return max(x - 0.5, 0.0)
    raise ValueError(f"Unknown quantity of interest {name!r}")


def qoi_values(name: str, grid_points: int) -> list[float]:
    return [qoi_value(name, x) for x in midpoint_grid(grid_points)]


def expectation(distribution: list[float], values: list[float]) -> float:
    return sum(float(p) * float(v) for p, v in zip(distribution, values))


def continuous_reference(function: dict[str, Any], quantity: str, points: int) -> float:
    numerator = 0.0
    denominator = 0.0
    for index in range(points):
        x = (index + 0.5) / points
        weight = max(density_value(function, x), 0.0)
        numerator += qoi_value(quantity, x) * weight
        denominator += weight
    if denominator <= 0.0:
        return float("nan")
    return numerator / denominator


def flatten_row(row: dict[str, Any]) -> dict[str, Any]:
    flat = {}
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


def risk_status(depth: int | None, twoq: int | None, max_depth: int, max_twoq: int) -> str:
    if depth is None or twoq is None:
        return "not_transpiled"
    if depth > max_depth:
        return "reject_depth"
    if twoq > max_twoq:
        return "reject_two_qubit_gates"
    return "candidate"


def hardware_status(
    *,
    risk: str,
    noisy_tvd: float | None,
    max_noisy_qoi_error: float | None,
    max_tvd: float,
    max_qoi_error: float,
) -> str:
    if risk != "candidate":
        return risk
    if noisy_tvd is None or max_noisy_qoi_error is None:
        return "candidate_needs_noisy_model"
    if noisy_tvd > max_tvd:
        return "reject_noisy_tvd"
    if max_noisy_qoi_error > max_qoi_error:
        return "reject_noisy_qoi_error"
    return "hardware_candidate"


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Credit-free Phase 9 audit for scaling algebraic integration."
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--backend", default=None)
    parser.add_argument("--account-file", default=str(DEFAULT_ACCOUNT_FILE))
    parser.add_argument("--name", default=None)
    parser.add_argument("--instance", default=None)
    parser.add_argument("--shots", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--optimization-level", type=int, default=None)
    parser.add_argument("--implementation", choices=["ucry", "direct"], default=None)
    parser.add_argument("--continuous-grid-points", type=int, default=None)
    parser.add_argument("--skip-transpile", action="store_true")
    parser.add_argument("--skip-ideal-sim", action="store_true")
    parser.add_argument("--skip-noisy-sim", action="store_true")
    parser.add_argument("--max-depth", type=int, default=None)
    parser.add_argument("--max-two-qubit-gates", type=int, default=None)
    parser.add_argument("--max-noisy-tvd", type=float, default=None)
    parser.add_argument("--max-noisy-qoi-error", type=float, default=None)
    parser.add_argument("--out-dir", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = json.loads(resolve_path(args.config).read_text(encoding="utf-8"))
    shots = int(args.shots if args.shots is not None else config.get("shots", 4096))
    seed = int(args.seed if args.seed is not None else config.get("seed", 12345))
    optimization_level = int(
        args.optimization_level
        if args.optimization_level is not None
        else config.get("optimization_level", 2)
    )
    implementation = str(args.implementation or config.get("implementation", "ucry"))
    continuous_grid_points = int(
        args.continuous_grid_points
        if args.continuous_grid_points is not None
        else config.get("continuous_grid_points", 200000)
    )
    max_depth = int(args.max_depth if args.max_depth is not None else config.get("max_depth", 1200))
    max_twoq = int(
        args.max_two_qubit_gates
        if args.max_two_qubit_gates is not None
        else config.get("max_two_qubit_gates", 350)
    )
    max_noisy_tvd = float(
        args.max_noisy_tvd if args.max_noisy_tvd is not None else config.get("max_noisy_tvd", 0.05)
    )
    max_noisy_qoi_error = float(
        args.max_noisy_qoi_error
        if args.max_noisy_qoi_error is not None
        else config.get("max_noisy_qoi_error", 0.02)
    )
    quantities = [str(name) for name in config.get("quantities", [])]
    backend_arg = args.backend if args.backend is not None else config.get("backend")
    out_dir = resolve_path(args.out_dir or config.get("out_dir", "results/phase9/integration_scaling_audit"))
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

    case_rows: list[dict[str, Any]] = []
    qoi_rows: list[dict[str, Any]] = []
    for case in config.get("cases", []):
        spec = spec_from_config(case)
        function = dict(case["function"])

        started = time.perf_counter()
        logical = build_gr_state_preparation_circuit(spec, measure=True, implementation=implementation)
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

        exact_probs = exact_distribution(logical_unmeasured, spec.grid_points)
        exact_tvd = (
            total_variation_distance(exact_probs, spec.probabilities)
            if exact_probs is not None
            else None
        )

        ideal_distribution = None
        if not args.skip_ideal_sim:
            ideal_counts = simulate_counts(logical, shots=shots, seed=seed)
            if ideal_counts is not None:
                ideal_distribution = distribution_from_counts(ideal_counts, spec.grid_points)

        transpiled_metrics = None
        transpiled_twoq_depth = None
        noisy_distribution = None
        transpile_seconds = None
        if backend is not None and not args.skip_transpile:
            started = time.perf_counter()
            transpiled = transpile(
                logical,
                backend=backend,
                optimization_level=optimization_level,
                seed_transpiler=seed,
            )
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

        ideal_tvd = (
            total_variation_distance(ideal_distribution, spec.probabilities)
            if ideal_distribution is not None
            else None
        )
        noisy_tvd = (
            total_variation_distance(noisy_distribution, spec.probabilities)
            if noisy_distribution is not None
            else None
        )

        case_qoi_rows = []
        for quantity in quantities:
            values = qoi_values(quantity, spec.grid_points)
            continuous = continuous_reference(function, quantity, continuous_grid_points)
            discrete = expectation(list(spec.probabilities), values)
            exact_loaded = expectation(exact_probs, values) if exact_probs is not None else None
            ideal_sample = (
                expectation(ideal_distribution, values) if ideal_distribution is not None else None
            )
            noisy_sample = (
                expectation(noisy_distribution, values) if noisy_distribution is not None else None
            )
            row = {
                "case_id": spec.case_id,
                "quantity": quantity,
                "num_qubits": spec.num_qubits,
                "grid_points": spec.grid_points,
                "backend": backend_label,
                "shots": shots,
                "continuous_reference": continuous,
                "finite_law_value": discrete,
                "discretization_error": abs(discrete - continuous),
                "exact_loaded_value": exact_loaded,
                "exact_loaded_error": abs(exact_loaded - discrete) if exact_loaded is not None else None,
                "ideal_sample_value": ideal_sample,
                "ideal_sample_error": abs(ideal_sample - discrete) if ideal_sample is not None else None,
                "noisy_sample_value": noisy_sample,
                "noisy_sample_error": abs(noisy_sample - discrete) if noisy_sample is not None else None,
            }
            case_qoi_rows.append(row)
            qoi_rows.append(row)

        max_discretization_error = max(row["discretization_error"] for row in case_qoi_rows)
        noisy_errors = [
            row["noisy_sample_error"]
            for row in case_qoi_rows
            if row["noisy_sample_error"] is not None
        ]
        max_noisy_error = max(noisy_errors) if noisy_errors else None
        transpiled_depth = transpiled_metrics.depth if transpiled_metrics is not None else None
        transpiled_twoq = (
            transpiled_metrics.two_qubit_gate_count if transpiled_metrics is not None else None
        )
        risk = risk_status(transpiled_depth, transpiled_twoq, max_depth, max_twoq)
        status = hardware_status(
            risk=risk,
            noisy_tvd=noisy_tvd,
            max_noisy_qoi_error=max_noisy_error,
            max_tvd=max_noisy_tvd,
            max_qoi_error=max_noisy_qoi_error,
        )
        row = {
            "case_id": spec.case_id,
            "label": spec.label,
            "source": spec.source,
            "num_qubits": spec.num_qubits,
            "grid_points": spec.grid_points,
            "backend": backend_label,
            "shots": shots,
            "seed": seed,
            "optimization_level": optimization_level,
            "implementation": implementation,
            "continuous_grid_points": continuous_grid_points,
            "logical_qasm_path": str(logical_qasm_path) if logical_qasm_path else None,
            "build_seconds": build_seconds,
            "transpile_seconds": transpile_seconds,
            "exact_tvd": exact_tvd,
            "ideal_tvd": ideal_tvd,
            "noisy_tvd": noisy_tvd,
            "max_discretization_error": max_discretization_error,
            "max_noisy_qoi_error": max_noisy_error,
            "risk_status": risk,
            "hardware_status": status,
            "notes": spec.notes,
            **logical_metrics.to_dict(prefix="logical_"),
        }
        if transpiled_metrics is not None:
            row.update(transpiled_metrics.to_dict(prefix="transpiled_"))
            row["transpiled_two_qubit_depth"] = transpiled_twoq_depth
        case_rows.append(row)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    case_json = out_dir / f"phase9_integration_scaling_cases_{timestamp}.json"
    case_csv = out_dir / f"phase9_integration_scaling_cases_{timestamp}.csv"
    qoi_json = out_dir / f"phase9_integration_scaling_qoi_{timestamp}.json"
    qoi_csv = out_dir / f"phase9_integration_scaling_qoi_{timestamp}.csv"
    best_csv = out_dir / f"phase9_integration_scaling_best_{timestamp}.csv"

    case_json.write_text(json.dumps(case_rows, indent=2, sort_keys=True), encoding="utf-8")
    qoi_json.write_text(json.dumps(qoi_rows, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(case_rows, case_csv)
    write_csv(qoi_rows, qoi_csv)

    best_rows = sorted(
        case_rows,
        key=lambda row: (
            row["hardware_status"] != "hardware_candidate",
            row.get("transpiled_two_qubit_gate_count") is None,
            row.get("transpiled_two_qubit_gate_count") or 10**12,
            row.get("transpiled_depth") or 10**12,
            row.get("max_noisy_qoi_error") if row.get("max_noisy_qoi_error") is not None else 10**12,
        ),
    )
    write_csv(
        [
            {
                "case_id": row["case_id"],
                "num_qubits": row["num_qubits"],
                "grid_points": row["grid_points"],
                "backend": row["backend"],
                "hardware_status": row["hardware_status"],
                "logical_depth": row["logical_depth"],
                "transpiled_depth": row.get("transpiled_depth"),
                "transpiled_two_qubit_gate_count": row.get("transpiled_two_qubit_gate_count"),
                "noisy_tvd": row.get("noisy_tvd"),
                "max_discretization_error": row["max_discretization_error"],
                "max_noisy_qoi_error": row.get("max_noisy_qoi_error"),
            }
            for row in best_rows
        ],
        best_csv,
    )

    print(f"Wrote JSON: {case_json}")
    print(f"Wrote CSV:  {case_csv}")
    print(f"Wrote JSON: {qoi_json}")
    print(f"Wrote CSV:  {qoi_csv}")
    print(f"Wrote best: {best_csv}")
    print()
    print("Phase 9 integration-scaling summary:")
    for row in case_rows:
        print(
            f"  {row['case_id']}: n={row['num_qubits']} grid={row['grid_points']} "
            f"ldepth={row['logical_depth']} "
            f"tdepth={row.get('transpiled_depth', 'n/a')} "
            f"2q={row.get('transpiled_two_qubit_gate_count', 'n/a')} "
            f"disc={row['max_discretization_error']:.6g} "
            f"noisy_tvd={row.get('noisy_tvd')} "
            f"max_noisy_qoi={row.get('max_noisy_qoi_error')} "
            f"status={row['hardware_status']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
