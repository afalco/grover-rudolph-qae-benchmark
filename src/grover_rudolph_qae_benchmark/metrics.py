"""Circuit and structural metrics for Phase 0."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import sqrt
from typing import Any

from .benchmark_specs import BenchmarkSpec


TWO_QUBIT_GATES = {
    "cx",
    "cz",
    "ecr",
    "swap",
    "iswap",
    "rxx",
    "ryy",
    "rzz",
    "rzx",
}


@dataclass(frozen=True)
class StructuralMetrics:
    multilinear_support: int
    maximum_degree: int
    minimum_m1_stratum: int
    canonical_length: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CircuitMetrics:
    depth: int
    size: int
    width: int
    num_qubits: int
    num_clbits: int
    one_qubit_gate_count: int
    two_qubit_gate_count: int
    multi_qubit_gate_count: int
    swap_count: int
    operation_counts: dict[str, int]

    def to_dict(self, prefix: str = "") -> dict[str, Any]:
        data = asdict(self)
        operation_counts = data.pop("operation_counts")
        out = {f"{prefix}{key}": value for key, value in data.items()}
        out[f"{prefix}operation_counts"] = operation_counts
        return out


def structural_metrics(spec: BenchmarkSpec, *, num_grid_qubits: int = 2) -> StructuralMetrics:
    nonzero = {
        subset: value
        for subset, value in spec.angle_coefficients.items()
        if abs(value) > 0.0
    }
    max_degree = max((len(subset) for subset in nonzero), default=0)
    min_m1 = min((2 ** (num_grid_qubits - len(subset)) for subset in nonzero), default=0)
    canonical_length = sum(
        abs(value) * sqrt(2 ** (num_grid_qubits - len(subset) - 1))
        for subset, value in nonzero.items()
    )
    return StructuralMetrics(
        multilinear_support=len(nonzero),
        maximum_degree=max_degree,
        minimum_m1_stratum=min_m1,
        canonical_length=canonical_length,
    )


def circuit_metrics(circuit) -> CircuitMetrics:
    counts = {str(key): int(value) for key, value in circuit.count_ops().items()}
    one_qubit = 0
    two_qubit = 0
    multi_qubit = 0

    for instruction in circuit.data:
        num_qubits = len(instruction.qubits)
        name = instruction.operation.name
        if num_qubits == 1 and name not in {"measure", "barrier"}:
            one_qubit += 1
        elif num_qubits == 2:
            two_qubit += 1
        elif num_qubits > 2:
            multi_qubit += 1

    return CircuitMetrics(
        depth=int(circuit.depth() or 0),
        size=int(circuit.size()),
        width=int(circuit.width()),
        num_qubits=int(circuit.num_qubits),
        num_clbits=int(circuit.num_clbits),
        one_qubit_gate_count=one_qubit,
        two_qubit_gate_count=two_qubit,
        multi_qubit_gate_count=multi_qubit,
        swap_count=counts.get("swap", 0),
        operation_counts=counts,
    )


def two_qubit_depth(circuit) -> int:
    """Return depth restricted to two-qubit operations when supported by Qiskit."""
    try:
        return int(circuit.depth(lambda instruction: len(instruction.qubits) == 2) or 0)
    except TypeError:
        return 0

