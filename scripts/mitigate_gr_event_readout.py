#!/usr/bin/env python3
"""Readout/leakage mitigation for a Grover-Rudolph event bit.

This script uses already fetched hardware counts. It does not submit QPU jobs.
When backend access is available, it reads the current backend calibration to
obtain the assignment probabilities of the physical event qubit.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from grover_rudolph_qae_benchmark.gr_state_preparation import (  # noqa: E402
    bitstring_to_probability_index,
    build_gr_event_amplification_circuit,
    marked_indices,
    spec_from_config,
    total_variation_distance,
)
from grover_rudolph_qae_benchmark.ibm import (  # noqa: E402
    DEFAULT_ACCOUNT_FILE,
    qiskit_runtime_service,
)


DEFAULT_RUN = (
    PROJECT_ROOT
    / "results/hardware/phase4_gr_affine_n4_k1_repeat_opt3_seed12345_ibm_fez"
    / "20260805T161506Z/fetched_gr_mlae_results.json"
)


def load_rows(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("rows", [])


def parse_measurement_map(qasm_path: Path) -> dict[int, int]:
    """Return classical-bit -> physical-qubit map from OpenQASM measurements."""
    mapping: dict[int, int] = {}
    pattern = re.compile(r"^\s*measure\s+q\[(\d+)\]\s*->\s*c\[(\d+)\]\s*;")
    for line in qasm_path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            physical = int(match.group(1))
            classical = int(match.group(2))
            mapping[classical] = physical
    return mapping


def event_classical_bit(row: dict[str, Any]) -> int:
    """Return the classical bit that defines the event.

    For the currently supported ``upper_half`` event, the marked set is the
    upper half of the big-endian probability-vector indices. Because Qiskit
    count keys are converted with ``bitstring[::-1]``, that event is classical
    bit 0 when the logical circuit measures q[i] -> c[i].
    """
    if row.get("event") != "upper_half":
        raise ValueError(f"Unsupported event: {row.get('event')!r}")
    return 0


def bit_from_count_key(bitstring: str, classical_bit: int, num_clbits: int) -> int:
    compact = bitstring.replace(" ", "")
    if len(compact) != num_clbits:
        raise ValueError(
            f"Count key {bitstring!r} has length {len(compact)}, expected {num_clbits}."
        )
    return int(compact[num_clbits - 1 - classical_bit])


def observed_distribution(row: dict[str, Any]) -> list[float]:
    counts = row["counts"]
    grid_points = int(row["grid_points"])
    shots = sum(int(value) for value in counts.values())
    out = [0.0] * grid_points
    if shots == 0:
        return out
    for bitstring, count in counts.items():
        out[bitstring_to_probability_index(str(bitstring))] += int(count) / shots
    return out


def ideal_amplified_distribution(row: dict[str, Any]) -> list[float]:
    try:
        from qiskit.quantum_info import Statevector
    except ImportError as exc:
        raise SystemExit("qiskit is required for the ideal amplified distribution.") from exc

    spec = spec_from_config(
        {
            "case_id": row["case_id"],
            "label": row.get("label", row["case_id"]),
            "num_qubits": int(row["num_qubits"]),
            "probabilities": row["target_probabilities"],
        }
    )
    circuit = build_gr_event_amplification_circuit(
        spec,
        int(row["k"]),
        event=str(row["event"]),
        measure=False,
        implementation=str(row.get("implementation", "ucry")),
        reflection_method=str(row.get("reflection_method", "mcx")),
    )
    distribution = [0.0] * spec.grid_points
    for bitstring, probability in Statevector.from_instruction(circuit).probabilities_dict().items():
        distribution[bitstring_to_probability_index(str(bitstring))] += float(probability)
    return distribution


def marked_probability_from_distribution(distribution: list[float], row: dict[str, Any]) -> float:
    spec = spec_from_config(
        {
            "case_id": row["case_id"],
            "label": row.get("label", row["case_id"]),
            "num_qubits": int(row["num_qubits"]),
            "probabilities": row["target_probabilities"],
        }
    )
    return sum(distribution[index] for index in marked_indices(spec, str(row["event"])))


def event_mass_from_counts(row: dict[str, Any], classical_bit: int) -> float:
    counts = row["counts"]
    num_clbits = int(row["num_qubits"])
    shots = sum(int(value) for value in counts.values())
    if shots == 0:
        return float("nan")
    marked = sum(
        int(count)
        for bitstring, count in counts.items()
        if bit_from_count_key(str(bitstring), classical_bit, num_clbits) == 1
    )
    return marked / shots


def backend_readout_parameters(backend, physical_qubit: int) -> dict[str, Any]:
    props = backend.properties()
    if props is None:
        return {"source": "backend_properties_unavailable"}

    parameters: dict[str, float] = {}
    try:
        for parameter in props.qubits[physical_qubit]:
            parameters[str(parameter.name)] = float(parameter.value)
    except Exception:
        return {"source": "backend_properties_unavailable"}

    p01 = parameters.get("prob_meas1_prep0")
    p10 = parameters.get("prob_meas0_prep1")
    readout_error = parameters.get("readout_error")
    source = "backend_assignment_probabilities"
    if p01 is None or p10 is None:
        if readout_error is None:
            return {
                "source": "backend_properties_missing_assignment_probabilities",
                "physical_qubit": physical_qubit,
                "available_parameters": parameters,
            }
        p01 = p10 = readout_error
        source = "backend_symmetric_readout_error"
    return {
        "source": source,
        "physical_qubit": physical_qubit,
        "p_meas1_given_prep0": float(p01),
        "p_meas0_given_prep1": float(p10),
        "readout_error": readout_error,
        "available_parameters": parameters,
    }


def corrected_event_mass(observed: float, p01: float, p10: float) -> float:
    denominator = 1.0 - p01 - p10
    if abs(denominator) < 1e-15:
        return observed
    return min(max((observed - p01) / denominator, 0.0), 1.0)


def correct_distribution_event_bit(
    observed: list[float],
    *,
    p01: float,
    p10: float,
) -> list[float]:
    half = len(observed) // 2
    denominator = 1.0 - p01 - p10
    if abs(denominator) < 1e-15:
        return list(observed)

    corrected = [0.0] * len(observed)
    for lower in range(half):
        upper = lower + half
        measured_lower = observed[lower]
        measured_upper = observed[upper]
        true_lower = ((1.0 - p10) * measured_lower - p10 * measured_upper) / denominator
        true_upper = (-p01 * measured_lower + (1.0 - p01) * measured_upper) / denominator
        corrected[lower] = max(0.0, true_lower)
        corrected[upper] = max(0.0, true_upper)

    norm = sum(corrected)
    if norm > 0:
        corrected = [value / norm for value in corrected]
    return corrected


def fit_empirical_event_flip(observed: list[float], ideal: list[float]) -> dict[str, float]:
    half = len(observed) // 2
    ata00 = ata01 = ata11 = 0.0
    aty0 = aty1 = 0.0
    for lower in range(half):
        upper = lower + half
        il = ideal[lower]
        iu = ideal[upper]

        y = observed[upper] - iu
        a0 = il
        a1 = -iu
        ata00 += a0 * a0
        ata01 += a0 * a1
        ata11 += a1 * a1
        aty0 += a0 * y
        aty1 += a1 * y

        y = observed[lower] - il
        a0 = -il
        a1 = iu
        ata00 += a0 * a0
        ata01 += a0 * a1
        ata11 += a1 * a1
        aty0 += a0 * y
        aty1 += a1 * y

    det = ata00 * ata11 - ata01 * ata01
    if abs(det) < 1e-15:
        alpha = beta = 0.0
    else:
        alpha = (aty0 * ata11 - aty1 * ata01) / det
        beta = (ata00 * aty1 - ata01 * aty0) / det
    return {
        "empirical_lower_to_upper": min(max(alpha, 0.0), 0.5),
        "empirical_upper_to_lower": min(max(beta, 0.0), 0.5),
    }


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    keys = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mitigate GR event-bit readout using existing hardware counts."
    )
    parser.add_argument("--runs", nargs="*", default=[str(DEFAULT_RUN)])
    parser.add_argument("--backend", default=None)
    parser.add_argument("--account-file", default=str(DEFAULT_ACCOUNT_FILE))
    parser.add_argument("--name", default=None)
    parser.add_argument("--instance", default=None)
    parser.add_argument("--skip-backend", action="store_true")
    parser.add_argument("--p01", type=float, default=None, help="Manual P(meas 1 | prep 0).")
    parser.add_argument("--p10", type=float, default=None, help="Manual P(meas 0 | prep 1).")
    parser.add_argument(
        "--readout-error",
        type=float,
        default=None,
        help="Manual symmetric readout error used when p01/p10 are not given.",
    )
    parser.add_argument("--out-dir", default="results/phase4/readout_mitigation")
    return parser.parse_args()


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def main() -> int:
    args = parse_args()
    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    backend_cache: dict[str, Any] = {}
    rows_out: list[dict[str, Any]] = []

    for run_text in args.runs:
        run_path = resolve_path(run_text)
        for row in load_rows(run_path):
            qasm_path = Path(row["qasm_path"])
            measurement_map = parse_measurement_map(qasm_path)
            c_event = event_classical_bit(row)
            physical_event_qubit = measurement_map.get(c_event)
            if physical_event_qubit is None:
                raise RuntimeError(f"Could not find c[{c_event}] in {qasm_path}")

            backend_id = args.backend or str(row["backend"])
            readout = {
                "source": "manual_unavailable",
                "physical_qubit": physical_event_qubit,
            }
            if args.p01 is not None and args.p10 is not None:
                readout.update(
                    {
                        "source": "manual_assignment_probabilities",
                        "p_meas1_given_prep0": float(args.p01),
                        "p_meas0_given_prep1": float(args.p10),
                    }
                )
            elif args.readout_error is not None:
                readout.update(
                    {
                        "source": "manual_symmetric_readout_error",
                        "p_meas1_given_prep0": float(args.readout_error),
                        "p_meas0_given_prep1": float(args.readout_error),
                        "readout_error": float(args.readout_error),
                    }
                )
            elif not args.skip_backend:
                if backend_id not in backend_cache:
                    service = qiskit_runtime_service(
                        account_file=args.account_file,
                        name=args.name,
                        instance=args.instance,
                    )
                    backend_cache[backend_id] = service.backend(backend_id)
                readout = backend_readout_parameters(
                    backend_cache[backend_id], physical_event_qubit
                )

            p01 = readout.get("p_meas1_given_prep0")
            p10 = readout.get("p_meas0_given_prep1")
            observed = observed_distribution(row)
            ideal = ideal_amplified_distribution(row)
            empirical = fit_empirical_event_flip(observed, ideal)
            raw_event = event_mass_from_counts(row, c_event)
            corrected_scalar = None
            corrected_distribution = None
            corrected_distribution_event = None
            corrected_tvd = None
            readout_explained_fraction = None
            if p01 is not None and p10 is not None:
                corrected_scalar = corrected_event_mass(raw_event, float(p01), float(p10))
                corrected_distribution = correct_distribution_event_bit(
                    observed,
                    p01=float(p01),
                    p10=float(p10),
                )
                corrected_distribution_event = marked_probability_from_distribution(
                    corrected_distribution, row
                )
                corrected_tvd = total_variation_distance(corrected_distribution, ideal)
                if empirical["empirical_lower_to_upper"] > 0:
                    readout_explained_fraction = min(
                        float(p01) / empirical["empirical_lower_to_upper"], 1.0
                    )

            ideal_event = marked_probability_from_distribution(ideal, row)
            rows_out.append(
                {
                    "source": str(run_path),
                    "campaign_id": row["campaign_id"],
                    "case_id": row["case_id"],
                    "event": row["event"],
                    "k": int(row["k"]),
                    "optimization_level": int(row["optimization_level"]),
                    "backend": backend_id,
                    "qasm_path": str(qasm_path),
                    "event_classical_bit": c_event,
                    "event_count_key_position_from_left": int(row["num_qubits"]) - 1 - c_event,
                    "event_physical_qubit": physical_event_qubit,
                    "measurement_map_c_to_physical_q": json.dumps(
                        measurement_map, sort_keys=True
                    ),
                    "readout_source": readout.get("source"),
                    "p_meas1_given_prep0": p01,
                    "p_meas0_given_prep1": p10,
                    "backend_readout_error": readout.get("readout_error"),
                    "raw_event_probability": raw_event,
                    "ideal_event_probability": ideal_event,
                    "raw_abs_dev": abs(raw_event - ideal_event),
                    "readout_corrected_scalar_probability": corrected_scalar,
                    "readout_corrected_distribution_probability": corrected_distribution_event,
                    "readout_corrected_abs_dev": abs(corrected_scalar - ideal_event)
                    if corrected_scalar is not None
                    else None,
                    "readout_corrected_distribution_tvd_to_ideal": corrected_tvd,
                    "empirical_lower_to_upper": empirical["empirical_lower_to_upper"],
                    "empirical_upper_to_lower": empirical["empirical_upper_to_lower"],
                    "readout_explained_fraction_of_lower_to_upper": readout_explained_fraction,
                    "shots": int(row["shots"]),
                    "depth": int(row["transpiled_metrics"]["depth"]),
                    "twoq": int(row["transpiled_metrics"]["two_qubit_gate_count"]),
                }
            )

    csv_path = out_dir / "gr_event_readout_mitigation.csv"
    json_path = out_dir / "gr_event_readout_mitigation.json"
    write_csv(rows_out, csv_path)
    json_path.write_text(json.dumps(rows_out, indent=2, sort_keys=True), encoding="utf-8")

    print(f"Wrote JSON: {json_path}")
    print(f"Wrote CSV:  {csv_path}")
    print()
    print("Event-bit readout mitigation:")
    for row in rows_out:
        print(
            f"  {row['case_id']} k={row['k']} opt={row['optimization_level']}: "
            f"c[{row['event_classical_bit']}] -> q[{row['event_physical_qubit']}], "
            f"raw={row['raw_event_probability']:.6f}, "
            f"ideal={row['ideal_event_probability']:.6f}, "
            f"empirical L->U={row['empirical_lower_to_upper']:.4f}"
        )
        if row["readout_corrected_scalar_probability"] is None:
            print(f"    no readout calibration applied ({row['readout_source']})")
        else:
            explained = (
                f"{row['readout_explained_fraction_of_lower_to_upper']:.2%}"
                if row["readout_explained_fraction_of_lower_to_upper"] is not None
                else "n/a"
            )
            print(
                f"    readout {row['readout_source']}: "
                f"p01={row['p_meas1_given_prep0']:.5f}, "
                f"p10={row['p_meas0_given_prep1']:.5f}, "
                f"corrected={row['readout_corrected_scalar_probability']:.6f}, "
                f"corrected_dev={row['readout_corrected_abs_dev']:.6f}, "
                f"explained={explained}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
