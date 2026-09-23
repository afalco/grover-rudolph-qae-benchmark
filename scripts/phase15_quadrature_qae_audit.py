#!/usr/bin/env python3
"""Phase 15 quadrature-rule QAE audit.

This script treats g as an integrand under a uniform quadrature rule. It is
therefore different from the Grover-Rudolph finite-law workflow, where a profile
defines a probability law p and one computes I_p(f).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
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
    apply_uniformly_controlled_ry_stage,
    apply_zero_reflection,
)
from grover_rudolph_qae_benchmark.ibm import DEFAULT_ACCOUNT_FILE, qiskit_runtime_service
from grover_rudolph_qae_benchmark.metrics import circuit_metrics, two_qubit_depth


DEFAULT_CONFIG = PROJECT_ROOT / "experiments" / "phase15_quadrature_qae_audit.json"


def load_qiskit_tools():
    try:
        from qiskit import QuantumCircuit, transpile
        from qiskit.quantum_info import Statevector
    except ImportError as exc:
        raise SystemExit("qiskit is required for this audit.") from exc
    return QuantumCircuit, transpile, Statevector


def load_aer():
    try:
        from qiskit_aer import AerSimulator
    except ImportError:
        return None
    return AerSimulator


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def function_value(case: dict[str, Any], x: float) -> float:
    kind = str(case["kind"])
    if kind == "constant_quarter":
        value = 0.25
    elif kind == "sin2_half":
        value = math.sin(math.pi * x / 2.0) ** 2
    elif kind == "sin2":
        value = math.sin(math.pi * x) ** 2
    elif kind == "affine":
        value = float(case.get("offset", 0.0)) + float(case.get("slope", 1.0)) * x
    elif kind == "beta_bump":
        alpha = float(case.get("alpha", 2.0))
        beta = float(case.get("beta", 5.0))
        value = float(case.get("scale", 1.0)) * (x ** (alpha - 1.0)) * ((1.0 - x) ** (beta - 1.0))
    elif kind == "oscillatory":
        value = (
            float(case.get("base", 0.5))
            + float(case.get("sin_amp", 0.25)) * math.sin(2.0 * math.pi * x)
            + float(case.get("cos_amp", 0.10)) * math.cos(4.0 * math.pi * x)
        )
    else:
        raise ValueError(f"Unknown function kind: {kind}")
    return min(max(float(value), 0.0), 1.0)


def quadrature_nodes(rule: str, num_qubits: int) -> list[float]:
    grid_points = 2**num_qubits
    if rule == "left":
        return [idx / grid_points for idx in range(grid_points)]
    if rule == "midpoint":
        return [(idx + 0.5) / grid_points for idx in range(grid_points)]
    if rule == "right":
        return [(idx + 1.0) / grid_points for idx in range(grid_points)]
    raise ValueError(f"Rule {rule!r} is not a direct circuit rule.")


def direct_rule_values(case: dict[str, Any], rule: str, num_qubits: int) -> list[float]:
    return [function_value(case, x) for x in quadrature_nodes(rule, num_qubits)]


def quadrature_value(case: dict[str, Any], rule: str, num_qubits: int) -> float:
    if rule == "simpson":
        left = quadrature_value(case, "left", num_qubits)
        midpoint = quadrature_value(case, "midpoint", num_qubits)
        right = quadrature_value(case, "right", num_qubits)
        return (left + 4.0 * midpoint + right) / 6.0
    values = direct_rule_values(case, rule, num_qubits)
    return sum(values) / len(values)


def true_integral(case: dict[str, Any], panels: int = 65536) -> float:
    if panels % 2:
        panels += 1
    h = 1.0 / panels
    total = function_value(case, 0.0) + function_value(case, 1.0)
    odd = 0.0
    even = 0.0
    for idx in range(1, panels):
        value = function_value(case, idx * h)
        if idx % 2:
            odd += value
        else:
            even += value
    return h * (total + 4.0 * odd + 2.0 * even) / 3.0


def build_preparation(values: list[float]):
    QuantumCircuit, _, _ = load_qiskit_tools()
    grid_points = len(values)
    num_qubits = int(round(math.log2(grid_points)))
    objective = num_qubits
    qc = QuantumCircuit(num_qubits + 1, name=f"quad_A_n{num_qubits}")
    for qubit in range(num_qubits):
        qc.h(qubit)
    angles = [
        2.0 * math.asin(math.sqrt(min(max(float(value), 0.0), 1.0)))
        for value in values
    ]
    apply_uniformly_controlled_ry_stage(qc, objective, angles)
    return qc


def build_qae_circuit(values: list[float], k: int, *, measure: bool):
    QuantumCircuit, _, _ = load_qiskit_tools()
    num_qubits = int(round(math.log2(len(values))))
    total_qubits = num_qubits + 1
    objective = num_qubits
    qc = QuantumCircuit(total_qubits, total_qubits if measure else 0, name=f"quad_qae_n{num_qubits}_k{k}")
    preparation = build_preparation(values)
    inverse_preparation = preparation.inverse()
    qc.compose(preparation, inplace=True)
    for _ in range(k):
        qc.z(objective)
        qc.compose(inverse_preparation, inplace=True)
        apply_zero_reflection(qc, total_qubits, method="mcx")
        qc.compose(preparation, inplace=True)
    if measure:
        qc.measure(list(range(total_qubits)), list(range(total_qubits)))
    return qc


def marked_probability_statevector(circuit, objective: int, Statevector) -> float:
    bare = circuit.remove_final_measurements(inplace=False)
    probabilities = Statevector.from_instruction(bare).probabilities()
    return sum(
        float(prob)
        for index, prob in enumerate(probabilities)
        if (index >> objective) & 1
    )


def marked_probability_counts(counts: dict[str, int]) -> float:
    total = sum(counts.values())
    if total == 0:
        return float("nan")
    return sum(value for bitstring, value in counts.items() if bitstring[0] == "1") / total


def binomial_sample(rng: random.Random, shots: int, p: float) -> int:
    return sum(1 for _ in range(shots) if rng.random() < p)


def mle_estimate(observations: list[dict[str, int]], grid_size: int) -> float:
    eps = 1e-15

    def nll(a: float) -> float:
        total = 0.0
        for obs in observations:
            p = min(max(expected_amplified_probability(a, int(obs["k"])), eps), 1.0 - eps)
            successes = int(obs["successes"])
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

    left = max(lo, best_a - 5.0 * step)
    right = min(hi, best_a + 5.0 * step)
    for _ in range(80):
        m1 = left + (right - left) / 3.0
        m2 = right - (right - left) / 3.0
        if nll(m1) < nll(m2):
            right = m2
        else:
            left = m1
    return (left + right) / 2.0


def flatten(row: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key, value in row.items():
        if isinstance(value, (list, tuple, dict)):
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


def load_backend(args: argparse.Namespace):
    if not args.backend:
        return None
    service = qiskit_runtime_service(
        account_file=args.account_file,
        name=args.name,
        instance=args.instance,
    )
    return service.backend(args.backend)


def backend_name(backend) -> str | None:
    if backend is None:
        return None
    name = getattr(backend, "name", None)
    return str(name() if callable(name) else name)


def make_simulator(backend, seed: int):
    AerSimulator = load_aer()
    if AerSimulator is None:
        return None
    if backend is not None:
        try:
            return AerSimulator.from_backend(backend, seed_simulator=seed)
        except Exception:
            pass
    return AerSimulator(seed_simulator=seed)


def direct_rule_metrics(
    *,
    case: dict[str, Any],
    rule: str,
    num_qubits: int,
    schedule: list[int],
    shots: int,
    seed: int,
    mle_grid_size: int,
    transpile,
    Statevector,
    backend,
    simulator,
    opt_level: int,
    transpiler_seed: int,
    basis_gates: list[str],
) -> dict[str, Any]:
    stable_key = f"{case['case_id']}|{rule}|{num_qubits}|{','.join(map(str, schedule))}"
    stable_offset = int(hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:8], 16)
    rng = random.Random(seed + stable_offset)
    values = direct_rule_values(case, rule, num_qubits)
    q_value = sum(values) / len(values)
    observations: list[dict[str, int]] = []
    max_depth = 0
    max_twoq = 0
    max_twoq_depth = 0
    total_depth = 0
    total_twoq = 0
    noisy_observations: list[dict[str, int]] = []
    statevector_max_dev = 0.0
    started = time.perf_counter()
    for k in schedule:
        circuit = build_qae_circuit(values, k, measure=True)
        exact_p = expected_amplified_probability(q_value, k)
        sv_p = marked_probability_statevector(circuit, num_qubits, Statevector)
        statevector_max_dev = max(statevector_max_dev, abs(sv_p - exact_p))
        successes = binomial_sample(rng, shots, exact_p)
        observations.append({"k": k, "shots": shots, "successes": successes})
        if backend is not None:
            transpiled = transpile(
                circuit,
                backend=backend,
                optimization_level=opt_level,
                seed_transpiler=transpiler_seed,
            )
        else:
            transpiled = transpile(
                circuit,
                basis_gates=basis_gates,
                optimization_level=opt_level,
                seed_transpiler=transpiler_seed,
            )
        metrics = circuit_metrics(transpiled)
        tq_depth = two_qubit_depth(transpiled)
        max_depth = max(max_depth, metrics.depth)
        max_twoq = max(max_twoq, metrics.two_qubit_gate_count)
        max_twoq_depth = max(max_twoq_depth, tq_depth)
        total_depth += metrics.depth
        total_twoq += metrics.two_qubit_gate_count
        if simulator is not None:
            try:
                counts = simulator.run(transpiled, shots=shots, seed_simulator=seed + k).result().get_counts()
                noisy_p = marked_probability_counts(counts)
                noisy_observations.append(
                    {"k": k, "shots": shots, "successes": int(round(noisy_p * shots))}
                )
            except Exception:
                pass
    runtime_seconds = time.perf_counter() - started
    a_hat = mle_estimate(observations, grid_size=mle_grid_size)
    noisy_a_hat = (
        mle_estimate(noisy_observations, grid_size=mle_grid_size)
        if len(noisy_observations) == len(schedule)
        else None
    )
    return {
        "quadrature_value": q_value,
        "mlae_a_hat": a_hat,
        "mlae_estimation_error": abs(a_hat - q_value),
        "noisy_mlae_a_hat": noisy_a_hat,
        "noisy_mlae_estimation_error": None if noisy_a_hat is None else abs(noisy_a_hat - q_value),
        "max_depth": max_depth,
        "max_two_qubit_gates": max_twoq,
        "max_two_qubit_depth": max_twoq_depth,
        "sum_depth_over_schedule": total_depth,
        "sum_two_qubit_gates_over_schedule": total_twoq,
        "statevector_max_probability_deviation": statevector_max_dev,
        "local_runtime_seconds": runtime_seconds,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 15 quadrature-rule QAE audit.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--backend", default=None)
    parser.add_argument("--account-file", default=str(DEFAULT_ACCOUNT_FILE))
    parser.add_argument("--name", default=None)
    parser.add_argument("--instance", default=None)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--skip-noisy-sim", action="store_true")
    parser.add_argument("--cases", nargs="+", default=None)
    parser.add_argument("--rules", nargs="+", default=None)
    parser.add_argument("--num-qubits", nargs="+", type=int, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = resolve_path(args.config)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    out_dir = resolve_path(args.out_dir or config.get("out_dir", "results/phase15/quadrature_qae_audit"))
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    _, transpile, Statevector = load_qiskit_tools()
    backend = load_backend(args)
    simulator = None if args.skip_noisy_sim else make_simulator(backend, int(config.get("seed", 12345)))
    backend_label = backend_name(backend) or "local_basis"

    selected_case_ids = set(args.cases or [str(case["case_id"]) for case in config["cases"]])
    cases = [case for case in config["cases"] if str(case["case_id"]) in selected_case_ids]
    rules = args.rules or list(config.get("rules", ["left", "midpoint", "right", "simpson"]))
    num_qubits_values = args.num_qubits or [int(value) for value in config.get("num_qubits", [2, 3, 4, 5, 6])]
    schedules = [[int(k) for k in schedule] for schedule in config.get("mlae_schedules", [[0, 1], [0, 1, 2]])]
    shots = int(config.get("shots_per_circuit", 2048))
    seed = int(config.get("seed", 12345))
    mle_grid_size = int(config.get("mle_grid_size", 5001))
    opt_level = int(config.get("optimization_levels", [2])[0])
    transpiler_seed = int(config.get("transpiler_seeds", [12345])[0])
    basis_gates = [str(gate) for gate in config.get("basis_gates", ["cz", "id", "rz", "sx", "x"])]
    max_depth_threshold = int(config.get("max_depth", 900))
    max_twoq_threshold = int(config.get("max_two_qubit_gates", 250))
    max_total_error = float(config.get("max_total_error_for_qpu", 0.03))
    max_estimation_error = float(config.get("max_estimation_error_for_qpu", 0.02))

    rows: list[dict[str, Any]] = []
    for case in cases:
        exact = true_integral(case)
        for num_qubits in num_qubits_values:
            direct_cache: dict[tuple[str, tuple[int, ...]], dict[str, Any]] = {}
            for rule in rules:
                for schedule in schedules:
                    schedule_key = tuple(schedule)
                    if rule == "simpson":
                        components = []
                        for component_rule in ("left", "midpoint", "right"):
                            key = (component_rule, schedule_key)
                            if key not in direct_cache:
                                direct_cache[key] = direct_rule_metrics(
                                    case=case,
                                    rule=component_rule,
                                    num_qubits=num_qubits,
                                    schedule=schedule,
                                    shots=shots,
                                    seed=seed,
                                    mle_grid_size=mle_grid_size,
                                    transpile=transpile,
                                    Statevector=Statevector,
                                    backend=backend,
                                    simulator=simulator,
                                    opt_level=opt_level,
                                    transpiler_seed=transpiler_seed,
                                    basis_gates=basis_gates,
                                )
                            components.append(direct_cache[key])
                        q_value = (components[0]["quadrature_value"] + 4.0 * components[1]["quadrature_value"] + components[2]["quadrature_value"]) / 6.0
                        a_hat = (components[0]["mlae_a_hat"] + 4.0 * components[1]["mlae_a_hat"] + components[2]["mlae_a_hat"]) / 6.0
                        noisy_values = [component["noisy_mlae_a_hat"] for component in components]
                        noisy_a_hat = (
                            (float(noisy_values[0]) + 4.0 * float(noisy_values[1]) + float(noisy_values[2])) / 6.0
                            if all(value is not None for value in noisy_values)
                            else None
                        )
                        metrics = {
                            "quadrature_value": q_value,
                            "mlae_a_hat": a_hat,
                            "mlae_estimation_error": abs(a_hat - q_value),
                            "noisy_mlae_a_hat": noisy_a_hat,
                            "noisy_mlae_estimation_error": None if noisy_a_hat is None else abs(noisy_a_hat - q_value),
                            "max_depth": max(component["max_depth"] for component in components),
                            "max_two_qubit_gates": max(component["max_two_qubit_gates"] for component in components),
                            "max_two_qubit_depth": max(component["max_two_qubit_depth"] for component in components),
                            "sum_depth_over_schedule": sum(component["sum_depth_over_schedule"] for component in components),
                            "sum_two_qubit_gates_over_schedule": sum(component["sum_two_qubit_gates_over_schedule"] for component in components),
                            "statevector_max_probability_deviation": max(component["statevector_max_probability_deviation"] for component in components),
                            "local_runtime_seconds": sum(component["local_runtime_seconds"] for component in components),
                        }
                        component_rules = ["left", "midpoint", "right"]
                    else:
                        key = (rule, schedule_key)
                        if key not in direct_cache:
                            direct_cache[key] = direct_rule_metrics(
                                case=case,
                                rule=rule,
                                num_qubits=num_qubits,
                                schedule=schedule,
                                shots=shots,
                                seed=seed,
                                mle_grid_size=mle_grid_size,
                                transpile=transpile,
                                Statevector=Statevector,
                                backend=backend,
                                simulator=simulator,
                                opt_level=opt_level,
                                transpiler_seed=transpiler_seed,
                                basis_gates=basis_gates,
                            )
                        metrics = direct_cache[key]
                        component_rules = [rule]

                    discretization_error = abs(metrics["quadrature_value"] - exact)
                    total_error = abs(metrics["mlae_a_hat"] - exact)
                    noisy_total_error = (
                        None
                        if metrics["noisy_mlae_a_hat"] is None
                        else abs(float(metrics["noisy_mlae_a_hat"]) - exact)
                    )
                    status = "candidate_for_noisy_audit"
                    if metrics["max_depth"] > max_depth_threshold:
                        status = "reject_depth"
                    elif metrics["max_two_qubit_gates"] > max_twoq_threshold:
                        status = "reject_twoq"
                    elif total_error > max_total_error:
                        status = "hold_total_error"
                    elif metrics["mlae_estimation_error"] > max_estimation_error:
                        status = "hold_estimation_error"
                    elif noisy_total_error is not None and noisy_total_error > max_total_error:
                        status = "hold_noisy_total_error"
                    elif backend is not None:
                        status = "candidate_for_qpu_if_queue_allows"

                    rows.append(
                        {
                            "experiment_id": config.get("experiment_id"),
                            "backend": backend_label,
                            "case_id": case["case_id"],
                            "family": case.get("family", ""),
                            "function_kind": case["kind"],
                            "angle_class_hint": case.get("angle_class_hint", ""),
                            "num_qubits": num_qubits,
                            "grid_points": 2**num_qubits,
                            "rule": rule,
                            "component_rules": component_rules,
                            "schedule": schedule,
                            "shots_per_circuit": shots,
                            "total_circuits": len(schedule) * len(component_rules),
                            "total_shots": shots * len(schedule) * len(component_rules),
                            "oracle_queries": shots * sum(2 * k + 1 for k in schedule) * len(component_rules),
                            "true_integral": exact,
                            "quadrature_value": metrics["quadrature_value"],
                            "discretization_error": discretization_error,
                            "mlae_a_hat": metrics["mlae_a_hat"],
                            "mlae_estimation_error": metrics["mlae_estimation_error"],
                            "total_error": total_error,
                            "noisy_mlae_a_hat": metrics["noisy_mlae_a_hat"],
                            "noisy_mlae_estimation_error": metrics["noisy_mlae_estimation_error"],
                            "noisy_total_error": noisy_total_error,
                            "max_depth": metrics["max_depth"],
                            "max_two_qubit_gates": metrics["max_two_qubit_gates"],
                            "max_two_qubit_depth": metrics["max_two_qubit_depth"],
                            "sum_depth_over_schedule": metrics["sum_depth_over_schedule"],
                            "sum_two_qubit_gates_over_schedule": metrics["sum_two_qubit_gates_over_schedule"],
                            "statevector_max_probability_deviation": metrics["statevector_max_probability_deviation"],
                            "local_runtime_seconds": metrics["local_runtime_seconds"],
                            "optimization_level": opt_level,
                            "transpiler_seed": transpiler_seed,
                            "status": status,
                        }
                    )

    rows.sort(
        key=lambda row: (
            str(row["family"]) != "calibration",
            str(row["case_id"]),
            int(row["num_qubits"]),
            str(row["rule"]),
            str(row["schedule"]),
        )
    )
    json_path = out_dir / f"phase15_quadrature_qae_audit_{timestamp}.json"
    csv_path = out_dir / f"phase15_quadrature_qae_audit_{timestamp}.csv"
    best_path = out_dir / f"phase15_quadrature_qae_audit_best_{timestamp}.csv"
    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(rows, csv_path)

    best_rows = []
    groups = sorted({(row["case_id"], row["num_qubits"]) for row in rows})
    for case_id, num_qubits in groups:
        subset = [row for row in rows if row["case_id"] == case_id and row["num_qubits"] == num_qubits]
        best_rows.append(
            sorted(
                subset,
                key=lambda row: (
                    str(row["status"]).startswith("reject"),
                    float(row["total_error"]),
                    int(row["max_two_qubit_gates"]),
                    int(row["max_depth"]),
                ),
            )[0]
        )
    write_csv(best_rows, best_path)

    print(f"Wrote JSON: {json_path}")
    print(f"Wrote CSV:  {csv_path}")
    print(f"Wrote best: {best_path}")
    print("\nBest quadrature candidates by case and n:")
    for row in best_rows:
        print(
            f"  {row['case_id']:20s} n={row['num_qubits']} rule={row['rule']:8s} "
            f"K={row['schedule']} q={float(row['quadrature_value']):.8f} "
            f"true={float(row['true_integral']):.8f} total_err={float(row['total_error']):.6g} "
            f"depth={row['max_depth']} 2q={row['max_two_qubit_gates']} status={row['status']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
