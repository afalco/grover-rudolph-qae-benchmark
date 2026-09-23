"""Definitions of the initial g0, g1, g2 benchmark instances."""

from __future__ import annotations

from dataclasses import dataclass
from math import pi
from typing import Callable


AngleCoefficients = dict[tuple[int, ...], float]


@dataclass(frozen=True)
class BenchmarkSpec:
    """Small calibration benchmark from the SISC QAE experiments."""

    case_id: str
    label: str
    rule: str
    a_exact: float
    degree: int
    angle_coefficients: AngleCoefficients
    notes: str
    apply_state_preparation: Callable
    apply_inverse_state_preparation: Callable


def _apply_cry(qc, control: int, target: int, theta: float) -> None:
    """Apply CRY(theta) using the decomposition used in the legacy scripts."""
    qc.ry(theta / 2, target)
    qc.cx(control, target)
    qc.ry(-theta / 2, target)
    qc.cx(control, target)


def apply_g0_state_preparation(qc) -> None:
    """State preparation for g0(x)=1/4 on the n=2 midpoint grid."""
    qc.h(0)
    qc.h(1)
    qc.ry(pi / 3, 2)


def apply_g0_inverse_state_preparation(qc) -> None:
    qc.ry(-pi / 3, 2)
    qc.h(0)
    qc.h(1)


def apply_g1_state_preparation(qc) -> None:
    """State preparation for g1(x)=sin^2(pi*x/2) on the n=2 midpoint grid."""
    qc.h(0)
    qc.h(1)
    qc.ry(pi / 8, 2)
    _apply_cry(qc, 0, 2, pi / 2)
    _apply_cry(qc, 1, 2, pi / 4)


def apply_g1_inverse_state_preparation(qc) -> None:
    qc.cx(1, 2)
    qc.ry(pi / 8, 2)
    qc.cx(1, 2)
    qc.ry(-pi / 8, 2)
    qc.cx(0, 2)
    qc.ry(pi / 4, 2)
    qc.cx(0, 2)
    qc.ry(-pi / 4, 2)
    qc.ry(-pi / 8, 2)
    qc.h(0)
    qc.h(1)


def apply_g2_state_preparation(qc) -> None:
    """State preparation for g2(x)=sin^2(pi*x) on the n=2 midpoint grid."""
    qc.h(0)
    qc.h(1)
    qc.ry(pi / 4, 2)
    _apply_cry(qc, 0, 2, pi / 2)
    _apply_cry(qc, 1, 2, pi / 2)
    qc.ccx(0, 1, 2)


def apply_g2_inverse_state_preparation(qc) -> None:
    qc.ccx(0, 1, 2)
    qc.cx(1, 2)
    qc.ry(pi / 4, 2)
    qc.cx(1, 2)
    qc.ry(-pi / 4, 2)
    qc.cx(0, 2)
    qc.ry(pi / 4, 2)
    qc.cx(0, 2)
    qc.ry(-pi / 4, 2)
    qc.ry(-pi / 4, 2)
    qc.h(0)
    qc.h(1)


BENCHMARK_SPECS: dict[str, BenchmarkSpec] = {
    "g0": BenchmarkSpec(
        case_id="g0",
        label="g0_constant_quarter",
        rule="midpoint",
        a_exact=0.25,
        degree=0,
        angle_coefficients={(): pi / 3},
        notes="Calibration case; k=1 is degenerate because p_1(1/4)=1.",
        apply_state_preparation=apply_g0_state_preparation,
        apply_inverse_state_preparation=apply_g0_inverse_state_preparation,
    ),
    "g1": BenchmarkSpec(
        case_id="g1",
        label="sin2_pi_x_over_2",
        rule="midpoint",
        a_exact=0.5,
        degree=1,
        angle_coefficients={(): pi / 8, (0,): pi / 2, (1,): pi / 4},
        notes="Affine encoding; p_k(1/2)=1/2 for all k.",
        apply_state_preparation=apply_g1_state_preparation,
        apply_inverse_state_preparation=apply_g1_inverse_state_preparation,
    ),
    "g2": BenchmarkSpec(
        case_id="g2",
        label="sin2_pi_x",
        rule="midpoint",
        a_exact=0.5,
        degree=2,
        angle_coefficients={(): pi / 4, (0,): pi / 2, (1,): pi / 2, (0, 1): -pi},
        notes="Quadratic encoding; CCRY(-pi) implemented as CCX.",
        apply_state_preparation=apply_g2_state_preparation,
        apply_inverse_state_preparation=apply_g2_inverse_state_preparation,
    ),
}


def get_benchmark_spec(case_id: str) -> BenchmarkSpec:
    try:
        return BENCHMARK_SPECS[case_id]
    except KeyError as exc:
        valid = ", ".join(sorted(BENCHMARK_SPECS))
        raise ValueError(f"Unknown benchmark {case_id!r}; valid cases: {valid}") from exc

