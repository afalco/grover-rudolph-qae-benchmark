#!/usr/bin/env python3
"""Credit-free audit of Grover-Rudolph amplification variants."""

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

from grover_rudolph_qae_benchmark.circuits import expected_amplified_probability  # noqa: E402
from grover_rudolph_qae_benchmark.gr_state_preparation import (  # noqa: E402
    bitstring_to_probability_index,
    build_gr_event_amplification_circuit,
    marked_indices,
    marked_probability,
    spec_from_config,
)
from grover_rudolph_qae_benchmark.ibm import (  # noqa: E402
    DEFAULT_ACCOUNT_FILE,
    qiskit_runtime_service,
)
from grover_rudolph_qae_benchmark.metrics import circuit_metrics, two_qubit_depth  # noqa: E402


DEFAULT_CONFIG = (
    PROJECT_ROOT
    / "experiments/phase4_gr_affine_n4_k1_repeat_opt3_seed12345_ibm_fez.json"
)


def load_qiskit():
    try:
        from qiskit import transpile
        from qiskit.quantum_info import Statevector
    except ImportError as exc:
        raise SystemExit("qiskit is required.") from exc
    return transpile, Statevector


def load_aer():
    try:
        from qiskit_aer import AerSimulator
    except ImportError as exc:
        raise SystemExit("qiskit-aer is required for noisy simulation.") from exc
    return AerSimulator


def backend_name(backend) -> str:
    name = getattr(backend, "name", None)
    if callable(name):
        return str(name())
    return str(name or backend)


def load_case_from_config(config: dict[str, Any]) -> tuple[dict[str, Any], int]:
    if "case" in config:
        case = dict(config["case"])
        return case, int(case.pop("k", 1))
    selected = list(config.get("selected_cases", []))
    if not selected:
        raise ValueError("Config needs either 'case' or 'selected_cases'.")
    case = dict(selected[0])
    ks = [int(value) for value in case.pop("ks", [1])]
    if 1 not in ks:
        raise ValueError("The default amplification-variant audit expects k=1.")
    return case, 1


def marked_probability_from_counts(counts: dict[str, int], marked: list[int]) -> float:
    total = sum(int(value) for value in counts.values())
    if total == 0:
        return float("nan")
    marked_set = set(int(index) for index in marked)
    successes = 0
    for bitstring, value in counts.items():
        if bitstring_to_probability_index(str(bitstring)) in marked_set:
            successes += int(value)
    return successes / total


def exact_marked_probability(circuit, marked: list[int], Statevector) -> float:
    marked_set = set(int(index) for index in marked)
    state = Statevector.from_instruction(circuit.remove_final_measurements(inplace=False))
    total = 0.0
    for bitstring, probability in state.probabilities_dict().items():
        if bitstring_to_probability_index(str(bitstring)) in marked_set:
            total += float(probability)
    return total


def simulate_noisy(circuit, *, simulator, shots: int, seed: int, marked: list[int]) -> float:
    try:
        simulator.set_options(seed_simulator=seed)
    except Exception:
        pass
    counts = dict(simulator.run(circuit, shots=shots, seed_simulator=seed).result().get_counts())
    return marked_probability_from_counts(counts, marked)


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


def rank_key(row: dict[str, Any]) -> tuple[float, int, int, str, str]:
    noisy_abs_dev = row.get("noisy_abs_dev")
    primary = float(noisy_abs_dev) if noisy_abs_dev is not None else float("inf")
    return (
        primary,
        int(row.get("two_qubit_gate_count") or 10**9),
        int(row.get("transpiled_depth") or 10**9),
        str(row.get("implementation")),
        str(row.get("reflection_method")),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit GR amplification variants before further QPU use."
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--backend", default=None)
    parser.add_argument("--account-file", default=str(DEFAULT_ACCOUNT_FILE))
    parser.add_argument("--name", default=None)
    parser.add_argument("--instance", default=None)
    parser.add_argument("--shots", type=int, default=None)
    parser.add_argument("--simulation-seed", type=int, default=12345)
    parser.add_argument("--optimization-levels", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument(
        "--transpiler-seeds",
        type=int,
        nargs="+",
        default=[12345, 23456, 34567, 45678, 56789, 67890],
    )
    parser.add_argument("--implementations", nargs="+", default=["ucry", "direct"])
    parser.add_argument("--reflection-methods", nargs="+", default=["mcx", "mcp", "diagonal"])
    parser.add_argument("--skip-backend", action="store_true")
    parser.add_argument("--skip-noisy-sim", action="store_true")
    parser.add_argument("--out-dir", default="results/phase4/variant_audit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    config = json.loads(config_path.read_text(encoding="utf-8"))

    transpile, Statevector = load_qiskit()
    case, k = load_case_from_config(config)
    if k != 1:
        raise SystemExit("Only k=1 is supported by this variant audit.")

    event = str(config.get("event", "upper_half"))
    shots = int(args.shots if args.shots is not None else config.get("shots", 2048))
    backend_id = args.backend or config.get("backend")

    spec = spec_from_config(case)
    marked = marked_indices(spec, event)
    a_exact = marked_probability(spec, event)
    expected_p = expected_amplified_probability(a_exact, k)

    backend = None
    backend_label = None
    simulator = None
    if backend_id and not args.skip_backend:
        service = qiskit_runtime_service(
            account_file=args.account_file,
            name=args.name,
            instance=args.instance,
        )
        backend = service.backend(str(backend_id))
        backend_label = backend_name(backend)
        if not args.skip_noisy_sim:
            AerSimulator = load_aer()
            simulator = AerSimulator.from_backend(backend)

    rows: list[dict[str, Any]] = []
    print(f"Variant audit: {spec.case_id} k={k} event={event}")
    print(f"Expected p:    {expected_p:.8f}")
    print(f"Backend:       {backend_label or 'not used'}")
    print()

    for implementation in args.implementations:
        for reflection_method in args.reflection_methods:
            try:
                logical = build_gr_event_amplification_circuit(
                    spec,
                    k,
                    event=event,
                    measure=True,
                    implementation=implementation,
                    reflection_method=reflection_method,
                )
                logical_exact = exact_marked_probability(logical, marked, Statevector)
            except Exception as exc:
                rows.append(
                    {
                        "case_id": spec.case_id,
                        "event": event,
                        "k": k,
                        "implementation": implementation,
                        "reflection_method": reflection_method,
                        "expected_p": expected_p,
                        "error": str(exc),
                        "created_utc": datetime.now(timezone.utc).isoformat(),
                    }
                )
                continue

            logical_metrics = circuit_metrics(logical)
            for opt_level in args.optimization_levels:
                for seed in args.transpiler_seeds:
                    started = time.perf_counter()
                    transpiled = None
                    transpiled_metrics = None
                    transpiled_2q_depth = None
                    transpile_error = None
                    exact_p = None
                    exact_error = None
                    noisy_p = None
                    noisy_error = None
                    if backend is not None:
                        try:
                            transpiled = transpile(
                                logical,
                                backend=backend,
                                optimization_level=int(opt_level),
                                seed_transpiler=int(seed),
                            )
                            transpiled_metrics = circuit_metrics(transpiled)
                            transpiled_2q_depth = two_qubit_depth(transpiled)
                        except Exception as exc:
                            transpile_error = str(exc)
                    transpile_seconds = time.perf_counter() - started

                    exact_p = logical_exact

                    if simulator is not None and transpiled is not None:
                        try:
                            noisy_p = simulate_noisy(
                                transpiled,
                                simulator=simulator,
                                shots=shots,
                                seed=int(args.simulation_seed),
                                marked=marked,
                            )
                        except Exception as exc:
                            noisy_error = str(exc)

                    row = {
                        "case_id": spec.case_id,
                        "event": event,
                        "k": k,
                        "backend": backend_label,
                        "implementation": implementation,
                        "reflection_method": reflection_method,
                        "optimization_level": int(opt_level),
                        "transpiler_seed": int(seed),
                        "simulation_seed": int(args.simulation_seed),
                        "shots": shots,
                        "a_exact": a_exact,
                        "expected_p": expected_p,
                        "logical_exact_p": logical_exact,
                        "logical_depth": logical_metrics.depth,
                        "logical_two_qubit_gate_count": logical_metrics.two_qubit_gate_count,
                        "logical_operation_counts": logical_metrics.operation_counts,
                        "transpiled_depth": transpiled_metrics.depth if transpiled_metrics else None,
                        "two_qubit_gate_count": transpiled_metrics.two_qubit_gate_count
                        if transpiled_metrics
                        else None,
                        "two_qubit_depth": transpiled_2q_depth,
                        "transpiled_operation_counts": transpiled_metrics.operation_counts
                        if transpiled_metrics
                        else None,
                        "exact_p": exact_p,
                        "exact_abs_dev": abs(exact_p - expected_p) if exact_p is not None else None,
                        "noisy_p": noisy_p,
                        "noisy_abs_dev": abs(noisy_p - expected_p) if noisy_p is not None else None,
                        "transpile_seconds": transpile_seconds,
                        "transpile_error": transpile_error,
                        "exact_error": exact_error,
                        "noisy_error": noisy_error,
                        "created_utc": datetime.now(timezone.utc).isoformat(),
                    }
                    rows.append(row)

    ranked = sorted(rows, key=rank_key)
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out_dir / f"gr_amplification_variant_audit_{timestamp}.json"
    csv_path = out_dir / f"gr_amplification_variant_audit_{timestamp}.csv"
    best_path = out_dir / f"gr_amplification_variant_audit_best_{timestamp}.csv"
    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(rows, csv_path)
    write_csv(ranked[: min(20, len(ranked))], best_path)

    print(f"Wrote JSON: {json_path}")
    print(f"Wrote CSV:  {csv_path}")
    print(f"Wrote best: {best_path}")
    print()
    print("Best variants:")
    for row in ranked[:10]:
        noisy = row.get("noisy_p")
        dev = row.get("noisy_abs_dev")
        print(
            f"  impl={row.get('implementation'):<6} refl={row.get('reflection_method'):<8} "
            f"opt={row.get('optimization_level')} seed={row.get('transpiler_seed')}: "
            f"depth={row.get('transpiled_depth')} 2q={row.get('two_qubit_gate_count')} "
            f"noisy={'n/a' if noisy is None else f'{noisy:.6f}'} "
            f"dev={'n/a' if dev is None else f'{dev:.6f}'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
