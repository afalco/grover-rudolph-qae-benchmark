#!/usr/bin/env python3
"""Audit GR distribution layouts/seeds with observable-level noisy simulation."""

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

from grover_rudolph_qae_benchmark.gr_state_preparation import (
    build_gr_state_preparation_circuit,
    distribution_from_counts,
    midpoint_grid,
    spec_from_config,
    total_variation_distance,
)
from grover_rudolph_qae_benchmark.ibm import DEFAULT_ACCOUNT_FILE, qiskit_runtime_service
from grover_rudolph_qae_benchmark.metrics import circuit_metrics, two_qubit_depth


DEFAULT_CONFIG = (
    PROJECT_ROOT / "experiments" / "phase5_gr_beta_bump_n4_layout_audit_ibm_fez.json"
)


def load_qiskit():
    try:
        from qiskit import transpile
        from qiskit_aer import AerSimulator
    except ImportError as exc:
        raise SystemExit(
            "qiskit and qiskit-aer are required.\n"
            "Install project dependencies with:\n\n"
            "  python3 -m pip install -e '.[simulation]'\n"
        ) from exc
    return transpile, AerSimulator


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


def observable_values(name: str, grid_points: int) -> list[float]:
    xs = midpoint_grid(grid_points)
    if name == "upper_half":
        return [1.0 if index >= grid_points // 2 else 0.0 for index in range(grid_points)]
    if name == "mean_x":
        return xs
    if name == "second_moment_x2":
        return [x * x for x in xs]
    if name == "sin_pi_x":
        return [math.sin(math.pi * x) for x in xs]
    if name == "call_x_minus_half":
        return [max(x - 0.5, 0.0) for x in xs]
    raise ValueError(f"Unknown observable {name!r}")


def expectation(distribution: list[float], values: list[float]) -> float:
    return sum(float(p) * float(v) for p, v in zip(distribution, values))


def max_abs_deviation(observed: list[float], target: list[float]) -> float:
    return max(abs(a - b) for a, b in zip(observed, target))


def risk_status(depth: int, twoq: int, max_depth: int, max_twoq: int) -> str:
    if depth > max_depth:
        return "reject_depth"
    if twoq > max_twoq:
        return "reject_two_qubit_gates"
    return "candidate"


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Credit-free noisy/layout audit for GR distribution circuits."
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--account-file", default=str(DEFAULT_ACCOUNT_FILE))
    parser.add_argument("--name", default=None)
    parser.add_argument("--instance", default=None)
    parser.add_argument("--backend", default=None)
    parser.add_argument("--shots", type=int, default=None)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--skip-noisy-sim", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    config = json.loads(config_path.read_text(encoding="utf-8"))

    backend_id = args.backend or config["backend"]
    shots = int(args.shots if args.shots is not None else config.get("shots", 4096))
    implementation = str(config.get("implementation", "ucry"))
    optimization_levels = [int(value) for value in config.get("optimization_levels", [2])]
    seeds = [int(value) for value in config.get("seeds", [12345])]
    observables = [str(value) for value in config.get("observables", [])]
    max_depth = int(config.get("max_depth", 1200))
    max_twoq = int(config.get("max_two_qubit_gates", 350))

    out_dir = Path(args.out_dir or config.get("out_dir", "results/phase5/layout_audit"))
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    transpile, AerSimulator = load_qiskit()
    service = qiskit_runtime_service(
        account_file=args.account_file,
        name=args.name,
        instance=args.instance,
    )
    backend = service.backend(str(backend_id))
    backend_label = backend_name(backend)
    simulator = None if args.skip_noisy_sim else AerSimulator.from_backend(backend)

    spec = spec_from_config(config["case"])
    logical = build_gr_state_preparation_circuit(
        spec, measure=True, implementation=implementation
    )
    target = list(spec.probabilities)
    exact_observables = {
        name: expectation(target, observable_values(name, spec.grid_points))
        for name in observables
    }

    rows: list[dict[str, Any]] = []
    for optimization_level in optimization_levels:
        for seed in seeds:
            started = time.perf_counter()
            transpiled = transpile(
                logical,
                backend=backend,
                optimization_level=optimization_level,
                seed_transpiler=seed,
            )
            transpile_seconds = time.perf_counter() - started
            metrics = circuit_metrics(transpiled)
            twoq_depth = two_qubit_depth(transpiled)

            noisy_distribution = None
            noisy_tvd = None
            noisy_max_dev = None
            noisy_observable_errors = {}
            noisy_observable_estimates = {}
            if simulator is not None:
                sim_circuit = transpile(
                    transpiled,
                    simulator,
                    seed_transpiler=seed,
                )
                counts = simulator.run(
                    sim_circuit,
                    shots=shots,
                    seed_simulator=seed,
                ).result().get_counts()
                noisy_distribution = distribution_from_counts(counts, spec.grid_points)
                noisy_tvd = total_variation_distance(noisy_distribution, target)
                noisy_max_dev = max_abs_deviation(noisy_distribution, target)
                for name in observables:
                    estimate = expectation(
                        noisy_distribution,
                        observable_values(name, spec.grid_points),
                    )
                    noisy_observable_estimates[name] = estimate
                    noisy_observable_errors[name] = abs(estimate - exact_observables[name])

            row = {
                "case_id": spec.case_id,
                "label": spec.label,
                "backend": backend_label,
                "backend_status": status_message(backend),
                "shots": shots,
                "implementation": implementation,
                "optimization_level": optimization_level,
                "seed": seed,
                "num_qubits": spec.num_qubits,
                "grid_points": spec.grid_points,
                "source": spec.source,
                "target_probabilities": target,
                "transpile_seconds": transpile_seconds,
                "transpiled_two_qubit_depth": twoq_depth,
                "noisy_distribution": noisy_distribution,
                "noisy_tvd": noisy_tvd,
                "noisy_max_abs_deviation": noisy_max_dev,
                "exact_observables": exact_observables,
                "noisy_observable_estimates": noisy_observable_estimates,
                "noisy_observable_errors": noisy_observable_errors,
                "risk_status": risk_status(
                    metrics.depth,
                    metrics.two_qubit_gate_count,
                    max_depth,
                    max_twoq,
                ),
                "audited_utc": datetime.now(timezone.utc).isoformat(),
                **metrics.to_dict(prefix="transpiled_"),
            }
            if noisy_observable_errors:
                row["max_observable_error"] = max(noisy_observable_errors.values())
                row["mean_observable_error"] = sum(noisy_observable_errors.values()) / len(
                    noisy_observable_errors
                )
            rows.append(row)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out_dir / f"gr_distribution_variant_audit_{timestamp}.json"
    csv_path = out_dir / f"gr_distribution_variant_audit_{timestamp}.csv"
    best_path = out_dir / f"gr_distribution_variant_audit_best_{timestamp}.csv"
    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(rows, csv_path)

    best_rows = sorted(
        rows,
        key=lambda row: (
            row["risk_status"] != "candidate",
            row.get("noisy_tvd") if row.get("noisy_tvd") is not None else 10**9,
            row.get("max_observable_error", 10**9),
            row["transpiled_two_qubit_gate_count"],
            row["transpiled_depth"],
        ),
    )
    best_keys = [
        "case_id",
        "backend",
        "optimization_level",
        "seed",
        "risk_status",
        "transpiled_depth",
        "transpiled_two_qubit_gate_count",
        "transpiled_two_qubit_depth",
        "noisy_tvd",
        "noisy_max_abs_deviation",
        "mean_observable_error",
        "max_observable_error",
    ]
    with best_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=best_keys)
        writer.writeheader()
        for row in best_rows:
            writer.writerow({key: row.get(key) for key in best_keys})

    print(f"Variant audit: {spec.case_id}")
    print(f"Backend:       {backend_label}")
    print(f"Shots:         {shots}")
    print(f"Wrote JSON:    {json_path}")
    print(f"Wrote CSV:     {csv_path}")
    print(f"Wrote best:    {best_path}")
    print()
    print("Best candidates:")
    for row in best_rows[:10]:
        print(
            f"  opt={row['optimization_level']} seed={row['seed']}: "
            f"depth={row['transpiled_depth']} "
            f"2q={row['transpiled_two_qubit_gate_count']} "
            f"noisy_tvd={row.get('noisy_tvd')} "
            f"max_dev={row.get('noisy_max_abs_deviation')} "
            f"mean_obs_err={row.get('mean_observable_error')} "
            f"max_obs_err={row.get('max_observable_error')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
