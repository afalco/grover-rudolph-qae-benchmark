#!/usr/bin/env python3
"""Phase 14 audit for direct Grover-Rudolph integration scaling.

This script is credit-free. It builds direct distribution-sampling circuits for
n=4, n=5, and n=6 cases, transpiles them to a local IBM-like CZ basis, and
compares finite-law integration errors with MC/QMC sampling baselines. With
``--backend`` it also performs backend-aware transpilation and noisy simulation,
but it never submits QPU jobs.
"""

from __future__ import annotations

import argparse
import bisect
import csv
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

from grover_rudolph_qae_benchmark.angle_structure import (  # noqa: E402
    classify_values,
    qoi_function_values,
    values_from_case_function,
)
from grover_rudolph_qae_benchmark.gr_state_preparation import (  # noqa: E402
    build_gr_state_preparation_circuit,
    distribution_from_counts as distribution_from_qiskit_counts,
    midpoint_grid,
    spec_from_config,
    total_variation_distance,
)
from grover_rudolph_qae_benchmark.ibm import DEFAULT_ACCOUNT_FILE, qiskit_runtime_service  # noqa: E402
from grover_rudolph_qae_benchmark.metrics import circuit_metrics, two_qubit_depth  # noqa: E402


DEFAULT_CONFIG = PROJECT_ROOT / "experiments" / "phase14_direct_scaling_audit.json"


def load_qiskit():
    try:
        from qiskit import transpile
    except ImportError as exc:
        raise SystemExit("qiskit is not installed in the active Python environment.") from exc
    return transpile


def load_aer_simulator():
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


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    keys = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, sort_keys=True)
                    if isinstance(value, (dict, list, tuple))
                    else value
                    for key, value in row.items()
                }
            )


def density_value(function: dict[str, Any], x: float) -> float:
    kind = str(function["kind"])
    if kind == "sin2":
        return math.sin(math.pi * x) ** 2
    if kind == "sin2_half":
        return math.sin(math.pi * x / 2.0) ** 2
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
    if kind == "constant":
        return float(function.get("value", 1.0))
    raise ValueError(f"Unknown density kind {kind!r}")


def quantity_values(name: str, grid_points: int) -> list[float]:
    xs = midpoint_grid(grid_points)
    if name == "upper_half":
        return [1.0 if index >= grid_points // 2 else 0.0 for index in range(grid_points)]
    if name == "mean_x":
        return list(xs)
    if name == "second_moment_x2":
        return [x * x for x in xs]
    if name == "sin_pi_x":
        return [math.sin(math.pi * x) for x in xs]
    if name == "call_x_minus_half":
        return [max(x - 0.5, 0.0) for x in xs]
    raise ValueError(f"Unknown quantity of interest {name!r}")


def expectation(probabilities: list[float] | tuple[float, ...], values: list[float]) -> float:
    return sum(float(p) * float(v) for p, v in zip(probabilities, values))


def continuous_reference(function: dict[str, Any], quantity: str, points: int) -> float:
    numerator = 0.0
    denominator = 0.0
    for index in range(points):
        x = (index + 0.5) / points
        weight = max(density_value(function, x), 0.0)
        numerator += qoi_value(quantity, x) * weight
        denominator += weight
    return numerator / denominator if denominator > 0.0 else float("nan")


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


def cumulative_distribution(probabilities: tuple[float, ...]) -> list[float]:
    cumulative = []
    running = 0.0
    for probability in probabilities:
        running += float(probability)
        cumulative.append(running)
    if cumulative:
        cumulative[-1] = 1.0
    return cumulative


def sample_index(cumulative: list[float], u: float) -> int:
    return min(bisect.bisect_right(cumulative, u), len(cumulative) - 1)


def distribution_from_counts(counts: list[int]) -> list[float]:
    total = sum(counts)
    return [count / total for count in counts] if total else [float("nan")] * len(counts)


def mc_counts(cumulative: list[float], shots: int, rng: random.Random) -> list[int]:
    counts = [0] * len(cumulative)
    for _ in range(shots):
        counts[sample_index(cumulative, rng.random())] += 1
    return counts


def van_der_corput(index: int, base: int = 2) -> float:
    value = 0.0
    denominator = 1.0
    while index > 0:
        index, remainder = divmod(index, base)
        denominator *= base
        value += remainder / denominator
    return value


def qmc_counts(cumulative: list[float], shots: int, shift: float) -> list[int]:
    counts = [0] * len(cumulative)
    for sample in range(1, shots + 1):
        u = (van_der_corput(sample) + shift) % 1.0
        counts[sample_index(cumulative, u)] += 1
    return counts


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def rmse(errors: list[float]) -> float:
    return math.sqrt(mean([error * error for error in errors])) if errors else float("nan")


def percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - pos) + ordered[hi] * (pos - lo)


def exact_distribution_from_statevector(circuit, grid_points: int) -> list[float] | None:
    try:
        from qiskit.quantum_info import Statevector
    except ImportError:
        return None
    state = Statevector.from_instruction(circuit)
    probabilities = [0.0] * grid_points
    for bitstring, probability in state.probabilities_dict().items():
        probabilities[int(bitstring[::-1], 2)] += float(probability)
    return probabilities


def best_transpilation(
    logical,
    *,
    basis_gates: list[str],
    optimization_levels: list[int],
    seeds: list[int],
    backend=None,
):
    transpile = load_qiskit()
    best = None
    rows = []
    for opt in optimization_levels:
        for seed in seeds:
            started = time.perf_counter()
            if backend is None:
                circuit = transpile(
                    logical,
                    basis_gates=basis_gates,
                    optimization_level=int(opt),
                    seed_transpiler=int(seed),
                )
            else:
                circuit = transpile(
                    logical,
                    backend=backend,
                    optimization_level=int(opt),
                    seed_transpiler=int(seed),
                )
            elapsed = time.perf_counter() - started
            metrics = circuit_metrics(circuit)
            row = {
                "optimization_level": int(opt),
                "seed": int(seed),
                "transpile_seconds": elapsed,
                "two_qubit_depth": two_qubit_depth(circuit),
                "circuit": circuit,
                **metrics.to_dict(prefix="transpiled_"),
            }
            rows.append(row)
            key = (
                metrics.two_qubit_gate_count,
                metrics.depth,
                row["two_qubit_depth"],
                int(opt),
                int(seed),
            )
            if best is None or key < best[0]:
                best = (key, row)
    return best[1], rows


def simulate_noisy_distribution(circuit, *, backend, shots: int, seed: int, grid_points: int):
    AerSimulator = load_aer_simulator()
    if AerSimulator is None:
        return None
    simulator = AerSimulator.from_backend(backend)
    job = simulator.run(circuit, shots=shots, seed_simulator=seed)
    counts = job.result().get_counts()
    return distribution_from_qiskit_counts(counts, grid_points)


def decision_status(
    *,
    depth: int,
    twoq: int,
    max_depth: int,
    max_twoq: int,
    max_discretization_error: float,
    discretization_limit: float,
) -> str:
    if depth > max_depth:
        return "hold_depth_before_backend"
    if twoq > max_twoq:
        return "hold_two_qubit_cost_before_backend"
    if max_discretization_error > discretization_limit:
        return "diagnostic_discretization_watch"
    return "candidate_for_backend_audit"


def backend_decision_status(
    *,
    local_status: str,
    noisy_tvd: float | None,
    max_noisy_qoi_error: float | None,
    max_noisy_tvd: float,
    max_noisy_qoi_error_threshold: float,
) -> str:
    if local_status != "candidate_for_backend_audit":
        return local_status
    if noisy_tvd is None or max_noisy_qoi_error is None:
        return "candidate_needs_noisy_model"
    if noisy_tvd > max_noisy_tvd:
        return "hold_noisy_tvd_before_qpu"
    if max_noisy_qoi_error > max_noisy_qoi_error_threshold:
        return "hold_noisy_qoi_error_before_qpu"
    return "candidate_for_qpu_distribution_campaign"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Credit-free Phase 14 local audit for direct GR integration scaling."
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--backend", default=None)
    parser.add_argument("--account-file", default=str(DEFAULT_ACCOUNT_FILE))
    parser.add_argument("--name", default=None)
    parser.add_argument("--instance", default=None)
    parser.add_argument("--case-id", nargs="*", default=None)
    parser.add_argument("--shots", type=int, default=None)
    parser.add_argument("--mc-trials", type=int, default=None)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--skip-transpile", action="store_true")
    parser.add_argument("--skip-noisy-sim", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = json.loads(resolve_path(args.config).read_text(encoding="utf-8"))
    shots = int(args.shots if args.shots is not None else config.get("shots", 8192))
    mc_trials = int(args.mc_trials if args.mc_trials is not None else config.get("mc_trials", 400))
    seed = int(config.get("seed", 20260809))
    basis_gates = [str(gate) for gate in config.get("basis_gates", ["cz", "id", "rz", "sx", "x"])]
    optimization_levels = [int(value) for value in config.get("optimization_levels", [1, 2, 3])]
    seeds = [int(value) for value in config.get("seeds", [seed])]
    implementation = str(config.get("implementation", "ucry"))
    continuous_grid_points = int(config.get("continuous_grid_points", 200000))
    max_depth = int(config.get("max_depth", 900))
    max_twoq = int(config.get("max_two_qubit_gates", 260))
    discretization_limit = float(config.get("max_discretization_error", 0.015))
    max_noisy_tvd = float(config.get("max_noisy_tvd", 0.06))
    max_noisy_qoi_error = float(config.get("max_noisy_qoi_error", 0.025))
    quantities = [str(name) for name in config.get("quantities", [])]
    out_dir = resolve_path(args.out_dir or config.get("out_dir", "results/phase14/direct_scaling_audit"))
    out_dir.mkdir(parents=True, exist_ok=True)
    selected_case_ids = set(args.case_id or [])

    backend = None
    backend_label = None
    if args.backend:
        service = qiskit_runtime_service(
            account_file=args.account_file,
            name=args.name,
            instance=args.instance,
        )
        backend = service.backend(str(args.backend))
        backend_label = backend_name(backend)

    rng = random.Random(seed)
    case_rows: list[dict[str, Any]] = []
    qoi_rows: list[dict[str, Any]] = []
    transpile_rows: list[dict[str, Any]] = []

    for case in config.get("cases", []):
        if selected_case_ids and str(case["case_id"]) not in selected_case_ids:
            continue
        spec = spec_from_config(case)
        function = dict(case["function"])
        logical = build_gr_state_preparation_circuit(spec, measure=True, implementation=implementation)
        logical_unmeasured = build_gr_state_preparation_circuit(spec, measure=False, implementation=implementation)
        logical_metrics = circuit_metrics(logical)
        exact_loaded = exact_distribution_from_statevector(logical_unmeasured, spec.grid_points)
        exact_tvd = (
            total_variation_distance(exact_loaded, spec.probabilities)
            if exact_loaded is not None
            else None
        )

        if args.skip_transpile:
            best = {
                "optimization_level": None,
                "seed": None,
                "two_qubit_depth": None,
                "transpiled_depth": None,
                "transpiled_two_qubit_gate_count": None,
            }
        else:
            best, rows = best_transpilation(
                logical,
                basis_gates=basis_gates,
                optimization_levels=optimization_levels,
                seeds=seeds,
                backend=backend,
            )
            for row in rows:
                public_row = dict(row)
                public_row.pop("circuit", None)
                transpile_rows.append({"case_id": spec.case_id, "backend": backend_label, **public_row})

        noisy_distribution = None
        if backend is not None and not args.skip_noisy_sim and not args.skip_transpile:
            noisy_distribution = simulate_noisy_distribution(
                best["circuit"],
                backend=backend,
                shots=shots,
                seed=seed,
                grid_points=spec.grid_points,
            )
        noisy_tvd = (
            total_variation_distance(noisy_distribution, spec.probabilities)
            if noisy_distribution is not None
            else None
        )

        profile_class = classify_values(
            spec.case_id,
            values_from_case_function(case, input_kind="max_normalized_profile"),
            num_qubits=spec.num_qubits,
            input_kind="max_normalized_profile",
            notes="Positive profile rescaled to [0,1] for angle-structure diagnostics.",
        )

        cumulative = cumulative_distribution(spec.probabilities)
        case_qoi_rows = []
        for quantity in quantities:
            values = quantity_values(quantity, spec.grid_points)
            qoi_class = classify_values(
                quantity,
                qoi_function_values(quantity, spec.num_qubits),
                num_qubits=spec.num_qubits,
                input_kind="quantity_of_interest",
            )
            finite = expectation(spec.probabilities, values)
            continuous = continuous_reference(function, quantity, continuous_grid_points)
            mc_errors = []
            qmc_errors = []
            mc_tvd = []
            qmc_tvd = []
            for _ in range(mc_trials):
                mc_dist = distribution_from_counts(mc_counts(cumulative, shots, rng))
                qmc_dist = distribution_from_counts(qmc_counts(cumulative, shots, rng.random()))
                mc_errors.append(expectation(mc_dist, values) - finite)
                qmc_errors.append(expectation(qmc_dist, values) - finite)
                mc_tvd.append(total_variation_distance(mc_dist, spec.probabilities))
                qmc_tvd.append(total_variation_distance(qmc_dist, spec.probabilities))
            row = {
                "case_id": spec.case_id,
                "quantity": quantity,
                "backend": backend_label,
                "num_qubits": spec.num_qubits,
                "grid_points": spec.grid_points,
                "shots": shots,
                "finite_law_value": finite,
                "continuous_reference": continuous,
                "discretization_error": abs(finite - continuous),
                "noisy_sample_value": expectation(noisy_distribution, values)
                if noisy_distribution is not None
                else None,
                "noisy_sample_error": abs(expectation(noisy_distribution, values) - finite)
                if noisy_distribution is not None
                else None,
                "quantity_angle_class": f"G_{spec.num_qubits}^{qoi_class.degree}",
                "quantity_angle_degree": qoi_class.degree,
                "mc_rmse": rmse(mc_errors),
                "mc_mae": mean([abs(error) for error in mc_errors]),
                "mc_abs_error_p95": percentile([abs(error) for error in mc_errors], 0.95),
                "qmc_rmse": rmse(qmc_errors),
                "qmc_mae": mean([abs(error) for error in qmc_errors]),
                "qmc_abs_error_p95": percentile([abs(error) for error in qmc_errors], 0.95),
                "mc_tvd_median": percentile(mc_tvd, 0.5),
                "qmc_tvd_median": percentile(qmc_tvd, 0.5),
            }
            qoi_rows.append(row)
            case_qoi_rows.append(row)

        max_disc = max(row["discretization_error"] for row in case_qoi_rows)
        noisy_qoi_errors = [
            row["noisy_sample_error"] for row in case_qoi_rows if row["noisy_sample_error"] is not None
        ]
        max_noisy_error = max(noisy_qoi_errors) if noisy_qoi_errors else None
        min_mc_rmse = min(row["mc_rmse"] for row in case_qoi_rows)
        min_qmc_rmse = min(row["qmc_rmse"] for row in case_qoi_rows)
        depth = best.get("transpiled_depth")
        twoq = best.get("transpiled_two_qubit_gate_count")
        status = (
            "not_transpiled"
            if args.skip_transpile
            else decision_status(
                depth=int(depth),
                twoq=int(twoq),
                max_depth=max_depth,
                max_twoq=max_twoq,
                max_discretization_error=max_disc,
                discretization_limit=discretization_limit,
            )
        )
        backend_status = (
            backend_decision_status(
                local_status=status,
                noisy_tvd=noisy_tvd,
                max_noisy_qoi_error=max_noisy_error,
                max_noisy_tvd=max_noisy_tvd,
                max_noisy_qoi_error_threshold=max_noisy_qoi_error,
            )
            if backend is not None
            else status
        )
        public_best = dict(best)
        public_best.pop("circuit", None)
        case_rows.append(
            {
                "case_id": spec.case_id,
                "label": spec.label,
                "backend": backend_label,
                "num_qubits": spec.num_qubits,
                "grid_points": spec.grid_points,
                "shots": shots,
                "implementation": implementation,
                "basis_gates": basis_gates,
                "profile_angle_class": f"G_{spec.num_qubits}^{profile_class.degree}",
                "profile_angle_degree": profile_class.degree,
                "profile_angle_support": profile_class.support,
                "exact_loaded_tvd": exact_tvd,
                "noisy_tvd": noisy_tvd,
                "max_discretization_error": max_disc,
                "max_noisy_qoi_error": max_noisy_error,
                "min_mc_rmse_across_quantities": min_mc_rmse,
                "min_qmc_rmse_across_quantities": min_qmc_rmse,
                "decision_status": status,
                "backend_decision_status": backend_status,
                "notes": spec.notes,
                **logical_metrics.to_dict(prefix="logical_"),
                **public_best,
            }
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    case_json = out_dir / f"phase14_direct_scaling_cases_{timestamp}.json"
    case_csv = out_dir / f"phase14_direct_scaling_cases_{timestamp}.csv"
    qoi_json = out_dir / f"phase14_direct_scaling_qoi_{timestamp}.json"
    qoi_csv = out_dir / f"phase14_direct_scaling_qoi_{timestamp}.csv"
    transpile_json = out_dir / f"phase14_direct_scaling_transpilation_{timestamp}.json"
    transpile_csv = out_dir / f"phase14_direct_scaling_transpilation_{timestamp}.csv"
    best_csv = out_dir / f"phase14_direct_scaling_best_{timestamp}.csv"

    case_json.write_text(json.dumps(case_rows, indent=2, sort_keys=True), encoding="utf-8")
    qoi_json.write_text(json.dumps(qoi_rows, indent=2, sort_keys=True), encoding="utf-8")
    transpile_json.write_text(json.dumps(transpile_rows, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(case_rows, case_csv)
    write_csv(qoi_rows, qoi_csv)
    write_csv(transpile_rows, transpile_csv)
    write_csv(
        sorted(
            case_rows,
            key=lambda row: (
                row["backend_decision_status"]
                not in {"candidate_for_qpu_distribution_campaign", "candidate_for_backend_audit"},
                row.get("transpiled_two_qubit_gate_count") or 10**12,
                row.get("transpiled_depth") or 10**12,
            ),
        ),
        best_csv,
    )

    print(f"Wrote JSON: {case_json}")
    print(f"Wrote CSV:  {case_csv}")
    print(f"Wrote JSON: {qoi_json}")
    print(f"Wrote CSV:  {qoi_csv}")
    print(f"Wrote JSON: {transpile_json}")
    print(f"Wrote CSV:  {transpile_csv}")
    print(f"Wrote best: {best_csv}")
    print()
    mode = f"backend-aware audit on {backend_label}" if backend_label else "local audit"
    print(f"Phase 14 direct-scaling {mode}:")
    for row in case_rows:
        print(
            f"  {row['case_id']:<16} n={row['num_qubits']} grid={row['grid_points']:<3} "
            f"class={row['profile_angle_class']:<6} depth={row.get('transpiled_depth')} "
            f"2q={row.get('transpiled_two_qubit_gate_count')} "
            f"disc={row['max_discretization_error']:.6g} "
            f"noisy_tvd={row.get('noisy_tvd')} "
            f"noisy_qoi={row.get('max_noisy_qoi_error')} "
            f"MCmin={row['min_mc_rmse_across_quantities']:.6g} "
            f"QMCmin={row['min_qmc_rmse_across_quantities']:.6g} "
            f"status={row['backend_decision_status']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
