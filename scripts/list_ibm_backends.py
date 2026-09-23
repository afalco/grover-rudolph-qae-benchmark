#!/usr/bin/env python3
"""List IBM Quantum backends visible to the configured account."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from grover_rudolph_qae_benchmark.ibm import DEFAULT_ACCOUNT_FILE, qiskit_runtime_service


def backend_name(backend) -> str:
    name = getattr(backend, "name", None)
    if callable(name):
        return str(name())
    if name:
        return str(name)
    return str(backend)


def backend_status(backend) -> dict[str, Any]:
    try:
        status = backend.status()
    except Exception as exc:
        return {"available": None, "pending_jobs": None, "status_msg": f"unavailable: {exc}"}
    return {
        "available": getattr(status, "operational", None),
        "pending_jobs": getattr(status, "pending_jobs", None),
        "status_msg": getattr(status, "status_msg", None),
    }


def backend_configuration(backend) -> dict[str, Any]:
    try:
        config = backend.configuration()
    except Exception:
        config = None

    if config is None:
        return {}

    basis_gates = getattr(config, "basis_gates", None)
    simulator = getattr(config, "simulator", None)
    num_qubits = getattr(config, "num_qubits", None)
    max_shots = getattr(config, "max_shots", None)
    coupling_map = getattr(config, "coupling_map", None)

    return {
        "num_qubits": num_qubits,
        "basis_gates": basis_gates,
        "simulator": simulator,
        "max_shots": max_shots,
        "num_couplings": len(coupling_map) if coupling_map is not None else None,
    }


def backend_properties_summary(backend) -> dict[str, Any]:
    try:
        props = backend.properties()
    except Exception:
        return {}
    if props is None:
        return {}

    two_qubit_errors: list[float] = []
    readout_errors: list[float] = []

    try:
        for gate in props.gates:
            if len(getattr(gate, "qubits", [])) == 2:
                for parameter in gate.parameters:
                    if parameter.name == "gate_error":
                        two_qubit_errors.append(float(parameter.value))
    except Exception:
        pass

    try:
        for qubit in props.qubits:
            for parameter in qubit:
                if parameter.name == "readout_error":
                    readout_errors.append(float(parameter.value))
    except Exception:
        pass

    def mean(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    return {
        "avg_two_qubit_gate_error": mean(two_qubit_errors),
        "avg_readout_error": mean(readout_errors),
    }


def backend_record(backend) -> dict[str, Any]:
    record: dict[str, Any] = {"name": backend_name(backend)}
    record.update(backend_configuration(backend))
    record.update(backend_status(backend))
    record.update(backend_properties_summary(backend))
    return record


def sort_key(record: dict[str, Any]) -> tuple:
    available = record.get("available")
    simulator = record.get("simulator")
    pending_jobs = record.get("pending_jobs")
    num_qubits = record.get("num_qubits")
    avg_error = record.get("avg_two_qubit_gate_error")
    return (
        0 if available else 1,
        1 if simulator else 0,
        pending_jobs if pending_jobs is not None else 10**9,
        -(num_qubits or 0),
        avg_error if avg_error is not None else 1.0,
        record.get("name", ""),
    )


def format_float(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.4g}"
    except (TypeError, ValueError):
        return str(value)


def print_table(records: list[dict[str, Any]]) -> None:
    headers = [
        "name",
        "qubits",
        "available",
        "sim",
        "queue",
        "2q_error",
        "readout",
        "basis",
        "status",
    ]
    print(
        f"{headers[0]:28} {headers[1]:>6} {headers[2]:>9} {headers[3]:>5} "
        f"{headers[4]:>6} {headers[5]:>9} {headers[6]:>9} {headers[7]:24} {headers[8]}"
    )
    print("-" * 116)
    for record in records:
        basis_gates = record.get("basis_gates") or []
        basis = ",".join(str(gate) for gate in basis_gates[:5])
        if len(basis_gates) > 5:
            basis += ",..."
        print(
            f"{record.get('name', '-')[:28]:28} "
            f"{str(record.get('num_qubits', '-')):>6} "
            f"{str(record.get('available', '-')):>9} "
            f"{str(record.get('simulator', '-')):>5} "
            f"{str(record.get('pending_jobs', '-')):>6} "
            f"{format_float(record.get('avg_two_qubit_gate_error')):>9} "
            f"{format_float(record.get('avg_readout_error')):>9} "
            f"{basis[:24]:24} "
            f"{record.get('status_msg', '-')}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List IBM Quantum backends visible to the configured account."
    )
    parser.add_argument("--account-file", default=str(DEFAULT_ACCOUNT_FILE))
    parser.add_argument("--name", default=None, help="Saved IBM account name.")
    parser.add_argument("--instance", default=None, help="IBM Quantum instance CRN/name.")
    parser.add_argument("--min-qubits", type=int, default=0)
    parser.add_argument("--include-simulators", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print raw JSON records.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    service = qiskit_runtime_service(
        account_file=args.account_file,
        name=args.name,
        instance=args.instance,
    )

    records = [backend_record(backend) for backend in service.backends()]
    if not args.include_simulators:
        records = [record for record in records if not record.get("simulator")]
    if args.min_qubits:
        records = [
            record
            for record in records
            if (record.get("num_qubits") or 0) >= args.min_qubits
        ]
    records.sort(key=sort_key)

    if args.json:
        print(json.dumps(records, indent=2, sort_keys=True))
    else:
        print_table(records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

