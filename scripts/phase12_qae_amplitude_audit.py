#!/usr/bin/env python3
"""Credit-free audit of QAE amplitude circuits for finite-law integration.

For a Grover-Rudolph probability law p_F and a test function f in [0,1], the
state-preparation block prepares

    sum_i sqrt(p_i) |i> (
        sqrt(1-f_i) |0> + sqrt(f_i) |1>
    )

so that the marked objective-qubit probability is I_p(f).
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
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from grover_rudolph_qae_benchmark.angle_structure import (  # noqa: E402
    classify_values,
    qoi_function_values,
    values_from_case_function,
)
from grover_rudolph_qae_benchmark.circuits import expected_amplified_probability  # noqa: E402
from grover_rudolph_qae_benchmark.gr_state_preparation import (  # noqa: E402
    apply_uniformly_controlled_ry_stage,
    apply_zero_reflection,
    build_gr_state_preparation_circuit,
    spec_from_config,
)
from grover_rudolph_qae_benchmark.metrics import circuit_metrics, two_qubit_depth  # noqa: E402


DEFAULT_CONFIG = PROJECT_ROOT / "experiments/phase12_qae_amplitude_audit.json"


def load_qiskit_tools():
    try:
        from qiskit import QuantumCircuit, transpile
        from qiskit.quantum_info import Statevector
    except ImportError as exc:
        raise SystemExit("qiskit is required.") from exc
    return QuantumCircuit, transpile, Statevector


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def load_cases(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases") or data.get("selected_cases")
    if not cases:
        raise ValueError(f"{path} does not contain cases.")
    return {str(case["case_id"]): dict(case) for case in cases}


def expectation(probabilities: list[float] | tuple[float, ...], values: list[float] | tuple[float, ...]) -> float:
    return sum(float(p) * float(v) for p, v in zip(probabilities, values))


def build_integration_state_preparation(spec, quantity: str, *, implementation: str):
    QuantumCircuit, _, _ = load_qiskit_tools()
    total_qubits = spec.num_qubits + 1
    objective = spec.num_qubits
    qc = QuantumCircuit(total_qubits, name=f"int_{spec.case_id}_{quantity}")
    gr = build_gr_state_preparation_circuit(spec, measure=False, implementation=implementation)
    qc.compose(gr, qubits=list(range(spec.num_qubits)), inplace=True)
    values = qoi_function_values(quantity, spec.num_qubits)
    angles = [2.0 * math.asin(math.sqrt(min(max(float(value), 0.0), 1.0))) for value in values]
    apply_uniformly_controlled_ry_stage(qc, objective, angles)
    return qc


def build_qae_circuit(spec, quantity: str, k: int, *, implementation: str, reflection_method: str, measure: bool):
    QuantumCircuit, _, _ = load_qiskit_tools()
    total_qubits = spec.num_qubits + 1
    objective = spec.num_qubits
    creg_size = total_qubits if measure else 0
    qc = QuantumCircuit(total_qubits, creg_size, name=f"qae_{spec.case_id}_{quantity}_k{k}")
    preparation = build_integration_state_preparation(
        spec,
        quantity,
        implementation=implementation,
    )
    inverse_preparation = preparation.inverse()
    qc.compose(preparation, inplace=True)
    for _ in range(k):
        qc.z(objective)
        qc.compose(inverse_preparation, inplace=True)
        apply_zero_reflection(qc, total_qubits, method=reflection_method)
        qc.compose(preparation, inplace=True)
    if measure:
        qc.measure(list(range(total_qubits)), list(range(total_qubits)))
    return qc


def marked_probability_statevector(circuit, objective_qubit: int, Statevector) -> float:
    bare = circuit.remove_final_measurements(inplace=False)
    probabilities = Statevector.from_instruction(bare).probabilities()
    return sum(
        float(probability)
        for basis_index, probability in enumerate(probabilities)
        if (basis_index >> objective_qubit) & 1
    )


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


def status(depth: int, twoq: int, *, max_depth: int, max_twoq: int) -> str:
    if depth > max_depth:
        return "reject_depth"
    if twoq > max_twoq:
        return "reject_twoq"
    return "simulator_candidate"


def pair_recommendation(*, max_k_viable: int | None, p_range: float) -> str:
    if p_range < 1e-8:
        return "degenerate_not_qae_informative"
    if max_k_viable is None:
        return "do_not_amplify"
    if max_k_viable >= 2:
        return "local_qae_simulation_candidate"
    return "k1_only_depth_watch"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit finite-law QAE amplitude circuits.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--out-dir", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = resolve_path(args.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    source_cases = load_cases(resolve_path(str(config["source_config"])))
    implementation = str(config.get("implementation", "ucry"))
    reflection_method = str(config.get("reflection_method", "mcx"))
    basis_gates = list(config.get("basis_gates", ["cz", "id", "rz", "sx", "x"]))
    opt_levels = [int(value) for value in config.get("optimization_levels", [2])]
    seeds = [int(value) for value in config.get("transpiler_seeds", [12345])]
    ks = [int(value) for value in config.get("ks", [0, 1, 2])]
    max_depth = int(config.get("max_depth", 600))
    max_twoq = int(config.get("max_two_qubit_gates", 120))

    out_dir = resolve_path(args.out_dir or str(config.get("out_dir", "results/phase12/qae_amplitude_audit")))
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    _, transpile, Statevector = load_qiskit_tools()
    rows: list[dict[str, Any]] = []
    pair_rows: list[dict[str, Any]] = []

    for pair in config.get("selected_pairs", []):
        case_id = str(pair["case_id"])
        quantity = str(pair["quantity"])
        spec = spec_from_config(source_cases[case_id])
        qvalues = qoi_function_values(quantity, spec.num_qubits)
        a_exact = expectation(spec.probabilities, qvalues)
        profile_values = values_from_case_function(source_cases[case_id], input_kind="qae_function")
        profile_class = classify_values(
            case_id,
            profile_values,
            num_qubits=spec.num_qubits,
            input_kind="qae_function",
        ).to_dict()
        quantity_class = classify_values(
            quantity,
            qvalues,
            num_qubits=spec.num_qubits,
            input_kind="quantity_of_interest",
        ).to_dict()

        best_for_pair: list[dict[str, Any]] = []
        for k in ks:
            logical = build_qae_circuit(
                spec,
                quantity,
                k,
                implementation=implementation,
                reflection_method=reflection_method,
                measure=True,
            )
            exact_p = marked_probability_statevector(logical, spec.num_qubits, Statevector)
            expected_p = expected_amplified_probability(a_exact, k)
            logical_metrics = circuit_metrics(logical)
            for opt_level in opt_levels:
                for seed in seeds:
                    started = time.perf_counter()
                    transpiled = transpile(
                        logical,
                        basis_gates=basis_gates,
                        optimization_level=opt_level,
                        seed_transpiler=seed,
                    )
                    transpile_seconds = time.perf_counter() - started
                    metrics = circuit_metrics(transpiled)
                    row = {
                        "audit_id": config.get("audit_id"),
                        "case_id": case_id,
                        "quantity": quantity,
                        "purpose": pair.get("purpose", ""),
                        "num_distribution_qubits": spec.num_qubits,
                        "total_qubits": spec.num_qubits + 1,
                        "grid_points": spec.grid_points,
                        "profile_angle_class": profile_class["class"],
                        "profile_angle_degree": profile_class["degree"],
                        "quantity_angle_class": quantity_class["class"],
                        "quantity_angle_degree": quantity_class["degree"],
                        "a_exact": a_exact,
                        "k": k,
                        "expected_p_k": expected_p,
                        "statevector_p_k": exact_p,
                        "statevector_abs_dev": abs(exact_p - expected_p),
                        "logical_depth": logical_metrics.depth,
                        "logical_twoq": logical_metrics.two_qubit_gate_count,
                        "optimization_level": opt_level,
                        "transpiler_seed": seed,
                        "basis_gates": basis_gates,
                        "transpiled_depth": metrics.depth,
                        "two_qubit_gate_count": metrics.two_qubit_gate_count,
                        "two_qubit_depth": two_qubit_depth(transpiled),
                        "operation_counts": metrics.operation_counts,
                        "query_count": 2 * k + 1,
                        "status": status(
                            metrics.depth,
                            metrics.two_qubit_gate_count,
                            max_depth=max_depth,
                            max_twoq=max_twoq,
                        ),
                        "transpile_seconds": transpile_seconds,
                        "created_utc": datetime.now(timezone.utc).isoformat(),
                    }
                    rows.append(row)
                    best_for_pair.append(row)

        viable = [row for row in best_for_pair if row["status"] == "simulator_candidate"]
        best_k_max = {}
        for k in ks:
            candidates = [row for row in viable if int(row["k"]) == k]
            if candidates:
                best_k_max[k] = min(
                    candidates,
                    key=lambda row: (row["two_qubit_gate_count"], row["transpiled_depth"]),
                )
        max_k_viable = max(best_k_max) if best_k_max else None
        best_rows = list(best_k_max.values())
        p_values = [expected_amplified_probability(a_exact, k) for k in ks]
        p_range = max(p_values) - min(p_values)
        pair_rows.append(
            {
                "case_id": case_id,
                "quantity": quantity,
                "grid_points": spec.grid_points,
                "total_qubits": spec.num_qubits + 1,
                "profile_angle_class": profile_class["class"],
                "quantity_angle_class": quantity_class["class"],
                "a_exact": a_exact,
                "max_k_viable": max_k_viable,
                "max_k_depth": best_k_max[max_k_viable]["transpiled_depth"] if max_k_viable is not None else "",
                "max_k_twoq": best_k_max[max_k_viable]["two_qubit_gate_count"] if max_k_viable is not None else "",
                "amplified_probability_range": p_range,
                "recommended_schedule": [int(row["k"]) for row in best_rows],
                "recommendation": pair_recommendation(
                    max_k_viable=max_k_viable,
                    p_range=p_range,
                ),
                "purpose": pair.get("purpose", ""),
            }
        )

    rows.sort(
        key=lambda row: (
            row["case_id"],
            row["quantity"],
            row["k"],
            row["status"],
            row["two_qubit_gate_count"],
            row["transpiled_depth"],
        )
    )
    pair_rows.sort(key=lambda row: (row["recommendation"], row["case_id"], row["quantity"]))

    json_path = out_dir / f"phase12_qae_amplitude_audit_{timestamp}.json"
    csv_path = out_dir / f"phase12_qae_amplitude_audit_{timestamp}.csv"
    best_json = out_dir / f"phase12_qae_amplitude_audit_best_{timestamp}.json"
    best_csv = out_dir / f"phase12_qae_amplitude_audit_best_{timestamp}.csv"
    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    best_json.write_text(json.dumps(pair_rows, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(rows, csv_path)
    write_csv(pair_rows, best_csv)

    print(f"Wrote JSON: {json_path}")
    print(f"Wrote CSV:  {csv_path}")
    print(f"Wrote best JSON: {best_json}")
    print(f"Wrote best CSV:  {best_csv}")
    print()
    print("Phase 12 QAE amplitude audit:")
    for row in pair_rows:
        print(
            f"  {row['case_id']:<16} {row['quantity']:<12} "
            f"a={row['a_exact']:.6f} "
            f"profile={row['profile_angle_class']:<5} "
            f"quantity={row['quantity_angle_class']:<5} "
            f"max_k={row['max_k_viable']} "
            f"depth={row['max_k_depth']} 2q={row['max_k_twoq']} "
            f"{row['recommendation']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
