#!/usr/bin/env python3
"""Estimate the unsubmitted Phase 18 k=2 circuit without spending QPU credits."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
for root in (SRC_ROOT, SCRIPTS_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from grover_rudolph_qae_benchmark.circuits import expected_amplified_probability  # noqa: E402
from grover_rudolph_qae_benchmark.ibm import DEFAULT_ACCOUNT_FILE, qiskit_runtime_service  # noqa: E402
from submit_quadrature_qae_campaign import (  # noqa: E402
    build_qae_circuit,
    quadrature_value,
    true_integral,
)

DEFAULT_CAMPAIGN = (
    PROJECT_ROOT / "experiments/phase18_oscillatory_shifted_n4_midpoint_k012_ibm_kingston.json"
)
DEFAULT_HW_RUN = (
    PROJECT_ROOT
    / "results/hardware/phase18_oscillatory_shifted_n4_midpoint_k01_ibm_kingston/20260812T143823Z"
)


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def load_qiskit_tools():
    try:
        from qiskit import transpile
        from qiskit_aer import AerSimulator
    except ImportError as exc:
        raise SystemExit("qiskit and qiskit-aer are required for this projection.") from exc
    return transpile, AerSimulator


def marked_probability_counts(counts: dict[str, int]) -> float:
    total = sum(int(value) for value in counts.values())
    if total <= 0:
        return float("nan")
    return sum(int(value) for bitstring, value in counts.items() if str(bitstring).replace(" ", "")[0] == "1") / total


def simulate_probability(circuit, *, backend, shots: int, seed: int) -> float | None:
    transpile, AerSimulator = load_qiskit_tools()
    simulator = AerSimulator.from_backend(backend, seed_simulator=seed)
    result = simulator.run(circuit, shots=shots, seed_simulator=seed).result()
    counts = result.get_counts()
    return marked_probability_counts(counts)


def linear_projection(x0: float, y0: float, x1: float, y1: float, x: float) -> float:
    if abs(x1 - x0) < 1e-12:
        return y1
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def mle_from_probabilities(observations: list[tuple[int, float, int]]) -> float:
    eps = 1e-15

    def nll(a: float) -> float:
        total = 0.0
        for k, p_hat, shots in observations:
            p = min(max(expected_amplified_probability(a, k), eps), 1.0 - eps)
            successes = int(round(float(p_hat) * int(shots)))
            total -= successes * math.log(p) + (int(shots) - successes) * math.log(1.0 - p)
        return total

    lo = 1e-8
    hi = 1.0 - 1e-8
    grid_size = 20001
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


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    keys = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Estimate unsubmitted Phase 18 k=2 behavior.")
    parser.add_argument("--campaign", default=str(DEFAULT_CAMPAIGN))
    parser.add_argument("--hardware-run", default=str(DEFAULT_HW_RUN))
    parser.add_argument("--backend", default=None)
    parser.add_argument("--account-file", default=str(DEFAULT_ACCOUNT_FILE))
    parser.add_argument("--name", default=None)
    parser.add_argument("--instance", default=None)
    parser.add_argument("--shots", type=int, default=8192)
    parser.add_argument("--seed", type=int, default=20260812)
    parser.add_argument("--skip-noisy-sim", action="store_true")
    parser.add_argument("--out-dir", default="results/phase18/k2_projection")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    campaign_path = resolve_path(args.campaign)
    campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
    job_case = campaign["selected_jobs"][0]
    backend_name = args.backend or campaign["backend"]
    optimization_level = int(campaign.get("optimization_level", 2))
    transpiler_seed = int(campaign.get("seed", 12345))
    q_value = quadrature_value(job_case)
    exact_integral = true_integral(job_case)

    hardware_path = resolve_path(args.hardware_run) / "fetched_campaign_results.json"
    hardware_data = json.loads(hardware_path.read_text(encoding="utf-8"))
    hardware_rows = sorted(hardware_data["rows"], key=lambda row: int(row["k"]))
    hardware_by_k = {int(row["k"]): row for row in hardware_rows}

    backend = None
    if not args.skip_noisy_sim:
        service = qiskit_runtime_service(
            account_file=args.account_file,
            name=args.name,
            instance=args.instance,
        )
        backend = service.backend(backend_name)

    transpile, _ = load_qiskit_tools()
    rows: list[dict[str, Any]] = []
    for k in [0, 1, 2]:
        circuit = build_qae_circuit(job_case, k, measure=True)
        transpiled = transpile(
            circuit,
            backend=backend if backend is not None else None,
            optimization_level=optimization_level,
            seed_transpiler=transpiler_seed,
        )
        metrics = {
            "depth": int(transpiled.depth()),
            "two_qubit_gates": sum(
                1 for instruction in transpiled.data if len(getattr(instruction.operation, "name", "")) >= 0 and len(instruction.qubits) == 2
            ),
        }
        expected = expected_amplified_probability(q_value, k)
        noisy_p = None
        if backend is not None:
            noisy_p = simulate_probability(transpiled, backend=backend, shots=args.shots, seed=args.seed + k)
        hw = hardware_by_k.get(k)
        rows.append(
            {
                "case_id": job_case["case_id"],
                "backend": backend_name,
                "k": k,
                "quadrature_value": q_value,
                "true_integral": exact_integral,
                "expected_p": expected,
                "noisy_sim_p": noisy_p,
                "noisy_sim_dev": abs(noisy_p - expected) if noisy_p is not None else None,
                "hardware_p": None if hw is None else float(hw["p_hat"]),
                "hardware_dev_signed": None if hw is None else float(hw["p_hat"]) - expected,
                "hardware_dev_abs": None if hw is None else abs(float(hw["p_hat"]) - expected),
                "depth": metrics["depth"],
                "two_qubit_gates": metrics["two_qubit_gates"],
                "submitted_to_qpu": hw is not None,
            }
        )

    k0 = next(row for row in rows if row["k"] == 0)
    k1 = next(row for row in rows if row["k"] == 1)
    k2 = next(row for row in rows if row["k"] == 2)
    depth_delta = linear_projection(
        float(k0["depth"]),
        float(k0["hardware_dev_signed"]),
        float(k1["depth"]),
        float(k1["hardware_dev_signed"]),
        float(k2["depth"]),
    )
    twoq_delta = linear_projection(
        float(k0["two_qubit_gates"]),
        float(k0["hardware_dev_signed"]),
        float(k1["two_qubit_gates"]),
        float(k1["hardware_dev_signed"]),
        float(k2["two_qubit_gates"]),
    )
    projected_p_depth = min(max(float(k2["expected_p"]) + depth_delta, 0.0), 1.0)
    projected_p_twoq = min(max(float(k2["expected_p"]) + twoq_delta, 0.0), 1.0)

    projection = {
        "case_id": job_case["case_id"],
        "backend": backend_name,
        "unsubmitted_k": 2,
        "expected_p_k2": k2["expected_p"],
        "noisy_sim_p_k2": k2["noisy_sim_p"],
        "projected_hardware_p_k2_by_depth": projected_p_depth,
        "projected_hardware_p_k2_by_twoq": projected_p_twoq,
        "projected_hardware_p_k2_midpoint": (projected_p_depth + projected_p_twoq) / 2.0,
        "projected_abs_dev_midpoint": abs(((projected_p_depth + projected_p_twoq) / 2.0) - float(k2["expected_p"])),
        "k2_depth": k2["depth"],
        "k2_two_qubit_gates": k2["two_qubit_gates"],
        "warning": "Projection is empirical from k=0,1 only; do not treat as a substitute for QPU data.",
    }

    observed_k01 = [
        (0, float(k0["hardware_p"]), int(hardware_by_k[0]["shots"])),
        (1, float(k1["hardware_p"]), int(hardware_by_k[1]["shots"])),
    ]
    shots = int(hardware_by_k[0]["shots"])
    scenarios = {
        "k01_only": observed_k01,
        "k01_plus_ideal_k2": observed_k01 + [(2, float(k2["expected_p"]), shots)],
        "k01_plus_noisy_sim_k2": observed_k01
        + [(2, float(k2["noisy_sim_p"]), shots)]
        if k2["noisy_sim_p"] is not None
        else observed_k01,
        "k01_plus_empirical_projected_mid_k2": observed_k01
        + [(2, projection["projected_hardware_p_k2_midpoint"], shots)],
    }
    scenario_rows = []
    for name, observations in scenarios.items():
        a_hat = mle_from_probabilities(observations)
        scenario_rows.append(
            {
                "scenario": name,
                "a_hat": a_hat,
                "abs_error_to_quadrature_value": abs(a_hat - q_value),
                "implied_p2": expected_amplified_probability(a_hat, 2),
            }
        )

    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "phase18_k2_projection.json"
    csv_path = out_dir / "phase18_k2_projection_circuits.csv"
    json_path.write_text(
        json.dumps(
            {"circuits": rows, "projection": projection, "mlae_scenarios": scenario_rows},
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    write_csv(rows, csv_path)

    print(f"Wrote JSON: {json_path}")
    print(f"Wrote CSV:  {csv_path}")
    print()
    print("Phase 18 k=2 projection:")
    print(
        f"  expected_p={projection['expected_p_k2']:.6f} "
        f"noisy_sim={projection['noisy_sim_p_k2'] if projection['noisy_sim_p_k2'] is not None else 'n/a'}"
    )
    print(
        f"  projected_hardware_p=[{projection['projected_hardware_p_k2_by_depth']:.6f}, "
        f"{projection['projected_hardware_p_k2_by_twoq']:.6f}] "
        f"mid={projection['projected_hardware_p_k2_midpoint']:.6f}"
    )
    print(
        f"  k2 cost: depth={projection['k2_depth']} "
        f"twoq={projection['k2_two_qubit_gates']}"
    )
    print("  MLAE scenarios:")
    for row in scenario_rows:
        print(
            f"    {row['scenario']}: a_hat={row['a_hat']:.8f} "
            f"err={row['abs_error_to_quadrature_value']:.6f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
