#!/usr/bin/env python3
"""Simulator-only purification diagnostic for Grover-Rudolph distributions.

The circuit prepares p on a main register, copies the computational-basis index
to an auxiliary register, and measures both. This is inspired by the
purification layer of mixed-state preparation algorithms.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from grover_rudolph_qae_benchmark.gr_state_preparation import (  # noqa: E402
    build_gr_state_preparation_circuit,
    spec_from_config,
    total_variation_distance,
)
from grover_rudolph_qae_benchmark.metrics import circuit_metrics, two_qubit_depth


DEFAULT_CONFIG = (
    PROJECT_ROOT / "experiments" / "phase5_gr_distribution_mitigation_suite_ibm_fez.json"
)


def load_qiskit():
    try:
        from qiskit import QuantumCircuit, transpile
        from qiskit_aer import AerSimulator
    except ImportError as exc:
        raise SystemExit(
            "qiskit and qiskit-aer are required.\n"
            "Install project dependencies with:\n\n"
            "  python3 -m pip install -e '.[simulation]'\n"
        ) from exc
    return QuantumCircuit, transpile, AerSimulator


def load_cases(path: Path) -> list[dict[str, Any]]:
    config = json.loads(path.read_text(encoding="utf-8"))
    cases = config.get("selected_cases") or config.get("cases") or []
    if not cases:
        raise SystemExit(f"No cases found in {path}.")
    return cases


def compact_bits(bitstring: str) -> str:
    return bitstring.replace(" ", "")[::-1]


def register_index(qubit_order_bits: str) -> int:
    return int(qubit_order_bits, 2) if qubit_order_bits else 0


def build_purification_circuit(spec, *, implementation: str):
    QuantumCircuit, _, _ = load_qiskit()
    n = spec.num_qubits
    qc = QuantumCircuit(2 * n, 2 * n, name=f"purify_{spec.case_id}")
    prep = build_gr_state_preparation_circuit(
        spec,
        measure=False,
        implementation=implementation,
    )
    qc.compose(prep, qubits=list(range(n)), inplace=True)
    for idx in range(n):
        qc.cx(idx, n + idx)
    qc.measure(list(range(2 * n)), list(range(2 * n)))
    return qc


def analyze_counts(counts: dict[str, int], n: int) -> dict[str, Any]:
    grid_points = 2**n
    main_counts = [0] * grid_points
    aux_counts = [0] * grid_points
    joint_counts = [[0] * grid_points for _ in range(grid_points)]
    mismatch = 0
    shots = sum(int(value) for value in counts.values())
    for bitstring, count in counts.items():
        bits = compact_bits(str(bitstring))
        main = register_index(bits[:n])
        aux = register_index(bits[n : 2 * n])
        count = int(count)
        main_counts[main] += count
        aux_counts[aux] += count
        joint_counts[main][aux] += count
        if main != aux:
            mismatch += count
    if shots <= 0:
        main_distribution = [float("nan")] * grid_points
        aux_distribution = [float("nan")] * grid_points
        mismatch_probability = float("nan")
    else:
        main_distribution = [count / shots for count in main_counts]
        aux_distribution = [count / shots for count in aux_counts]
        mismatch_probability = mismatch / shots
    return {
        "shots": shots,
        "main_counts": main_counts,
        "aux_counts": aux_counts,
        "joint_counts": joint_counts,
        "main_distribution": main_distribution,
        "aux_distribution": aux_distribution,
        "mismatch_count": mismatch,
        "mismatch_probability": mismatch_probability,
    }


def flatten_row(row: dict[str, Any]) -> dict[str, Any]:
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
            writer.writerow(flatten_row(row))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run simulator-only GR purification diagnostics."
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--case-id", nargs="*", default=None)
    parser.add_argument("--shots", type=int, default=4096)
    parser.add_argument("--seed", type=int, default=20260808)
    parser.add_argument("--implementation", choices=["ucry", "direct"], default="ucry")
    parser.add_argument("--out-dir", default="results/phase6/purification_diagnostic")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    _, transpile, AerSimulator = load_qiskit()
    simulator = AerSimulator(seed_simulator=args.seed)
    selected = set(args.case_id or [])

    rows: list[dict[str, Any]] = []
    for case in load_cases(config_path):
        spec = spec_from_config(case)
        if selected and spec.case_id not in selected:
            continue
        circuit = build_purification_circuit(spec, implementation=args.implementation)
        transpiled = transpile(circuit, simulator, seed_transpiler=args.seed)
        counts = simulator.run(
            transpiled,
            shots=args.shots,
            seed_simulator=args.seed,
        ).result().get_counts()
        analysis = analyze_counts(counts, spec.num_qubits)
        main_tvd = total_variation_distance(
            analysis["main_distribution"],
            spec.probabilities,
        )
        aux_tvd = total_variation_distance(
            analysis["aux_distribution"],
            spec.probabilities,
        )
        metrics = circuit_metrics(transpiled)
        rows.append(
            {
                "case_id": spec.case_id,
                "label": spec.label,
                "source": spec.source,
                "num_qubits": spec.num_qubits,
                "total_qubits": 2 * spec.num_qubits,
                "grid_points": spec.grid_points,
                "shots": args.shots,
                "seed": args.seed,
                "implementation": args.implementation,
                "main_tvd": main_tvd,
                "aux_tvd": aux_tvd,
                "main_aux_tvd": total_variation_distance(
                    analysis["main_distribution"],
                    analysis["aux_distribution"],
                ),
                "mismatch_probability": analysis["mismatch_probability"],
                "mismatch_count": analysis["mismatch_count"],
                "target_probabilities": list(spec.probabilities),
                "main_distribution": analysis["main_distribution"],
                "aux_distribution": analysis["aux_distribution"],
                "main_counts": analysis["main_counts"],
                "aux_counts": analysis["aux_counts"],
                "joint_counts": analysis["joint_counts"],
                "two_qubit_depth": two_qubit_depth(transpiled),
                **metrics.to_dict(prefix="transpiled_"),
            }
        )

    json_path = out_dir / "phase6_purification_diagnostic.json"
    csv_path = out_dir / "phase6_purification_diagnostic.csv"
    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(rows, csv_path)

    print(f"Wrote JSON: {json_path}")
    print(f"Wrote CSV:  {csv_path}")
    print()
    print("Purification diagnostic:")
    for row in rows:
        print(
            f"  {row['case_id']:<18} qubits={row['total_qubits']} "
            f"depth={row['transpiled_depth']} 2q={row['transpiled_two_qubit_gate_count']} "
            f"main_tvd={row['main_tvd']:.6f} aux_tvd={row['aux_tvd']:.6f} "
            f"mismatch={row['mismatch_probability']:.6f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
