"""Circuit construction for the Phase 0 calibration benchmarks."""

from __future__ import annotations

from .benchmark_specs import BenchmarkSpec


def apply_zero_reflection(qc) -> None:
    """Reflection about |000> for the three-qubit calibration circuits."""
    qc.x(0)
    qc.x(1)
    qc.x(2)
    qc.h(2)
    qc.ccx(0, 1, 2)
    qc.h(2)
    qc.x(0)
    qc.x(1)
    qc.x(2)


def build_amplification_circuit(spec: BenchmarkSpec, k: int, *, measure: bool = True):
    """Build Q^k A|000> for one of the calibration benchmark instances."""
    from qiskit import QuantumCircuit

    creg_size = 3 if measure else 0
    qc = QuantumCircuit(3, creg_size, name=f"{spec.case_id}_{spec.rule}_k{k}")
    spec.apply_state_preparation(qc)
    for _ in range(k):
        qc.z(2)
        spec.apply_inverse_state_preparation(qc)
        apply_zero_reflection(qc)
        spec.apply_state_preparation(qc)
    if measure:
        qc.measure([0, 1, 2], [0, 1, 2])
    return qc


def expected_amplified_probability(a: float, k: int) -> float:
    """The ideal MLAE model p_k(a)."""
    from math import asin, sin, sqrt

    clipped = min(max(a, 1e-12), 1 - 1e-12)
    return sin((2 * k + 1) * asin(sqrt(clipped))) ** 2
