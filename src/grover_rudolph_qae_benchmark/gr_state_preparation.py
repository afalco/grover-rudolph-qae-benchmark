"""Grover-Rudolph state-preparation circuits.

The construction follows the dyadic probability tree formulation used by
Falco, Falco-Pomares, and Matthies: a probability vector on a dyadic grid is
loaded through one uniformly controlled Ry stage per grid qubit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class GRDistributionSpec:
    """A normalized dyadic probability distribution."""

    case_id: str
    label: str
    num_qubits: int
    probabilities: tuple[float, ...]
    source: str
    notes: str = ""

    @property
    def grid_points(self) -> int:
        return 2**self.num_qubits


@dataclass(frozen=True)
class GRStageAngle:
    """One conditional rotation in the dyadic Grover-Rudolph tree."""

    level: int
    prefix: tuple[int, ...]
    mass: float
    left_mass: float
    right_mass: float
    theta: float


def midpoint_grid(grid_points: int) -> list[float]:
    return [(idx + 0.5) / grid_points for idx in range(grid_points)]


def normalize_weights(weights: Iterable[float]) -> tuple[float, ...]:
    values = [float(value) for value in weights]
    if not values:
        raise ValueError("At least one weight is required.")
    if any(value < 0 for value in values):
        raise ValueError("Grover-Rudolph weights must be non-negative.")
    total = sum(values)
    if total <= 0:
        raise ValueError("At least one Grover-Rudolph weight must be positive.")
    return tuple(value / total for value in values)


def function_weights(kind: str, grid_points: int, **params: Any) -> list[float]:
    """Evaluate a positive test density on the midpoint dyadic grid."""
    weights = []
    for x in midpoint_grid(grid_points):
        if kind == "constant":
            value = float(params.get("value", 1.0))
        elif kind == "sin2_half":
            value = math.sin(math.pi * x / 2) ** 2
        elif kind == "sin2":
            value = math.sin(math.pi * x) ** 2
        elif kind == "affine":
            value = float(params.get("offset", 0.0)) + float(params.get("slope", 1.0)) * x
        elif kind == "beta_bump":
            alpha = float(params.get("alpha", 2.0))
            beta = float(params.get("beta", 5.0))
            raw = (x ** (alpha - 1)) * ((1 - x) ** (beta - 1))
            value = float(params.get("scale", 1.0)) * raw
        elif kind == "gaussian":
            mean = float(params.get("mean", 0.5))
            sigma = float(params.get("sigma", 0.15))
            value = math.exp(-0.5 * ((x - mean) / sigma) ** 2)
        else:
            raise ValueError(f"Unknown Grover-Rudolph function kind: {kind}")
        weights.append(max(float(value), 0.0))
    return weights


def spec_from_config(case: dict[str, Any]) -> GRDistributionSpec:
    """Build a distribution spec from a JSON experiment entry."""
    case_id = str(case["case_id"])
    if "probabilities" in case:
        probabilities = normalize_weights(case["probabilities"])
        source = "probabilities"
    elif "weights" in case:
        probabilities = normalize_weights(case["weights"])
        source = "weights"
    elif "function" in case:
        function = dict(case["function"])
        kind = str(function.pop("kind"))
        num_qubits = int(case["num_qubits"])
        probabilities = normalize_weights(function_weights(kind, 2**num_qubits, **function))
        source = f"function:{kind}"
    else:
        raise ValueError(
            f"Case {case_id!r} needs probabilities, weights, or a function definition."
        )

    grid_points = len(probabilities)
    if grid_points & (grid_points - 1):
        raise ValueError(f"Case {case_id!r} must contain 2^n grid points.")
    num_qubits = int(round(math.log2(grid_points)))
    expected_num_qubits = case.get("num_qubits")
    if expected_num_qubits is not None and int(expected_num_qubits) != num_qubits:
        raise ValueError(
            f"Case {case_id!r} has {grid_points} values, not 2^{expected_num_qubits}."
        )

    return GRDistributionSpec(
        case_id=case_id,
        label=str(case.get("label", case_id)),
        num_qubits=num_qubits,
        probabilities=probabilities,
        source=source,
        notes=str(case.get("notes", "")),
    )


def _prefix_matches(index: int, num_qubits: int, prefix: tuple[int, ...]) -> bool:
    bits = format(index, f"0{num_qubits}b")
    return all(int(bits[pos]) == bit for pos, bit in enumerate(prefix))


def prefix_mass(probabilities: tuple[float, ...], num_qubits: int, prefix: tuple[int, ...]) -> float:
    return sum(
        probability
        for index, probability in enumerate(probabilities)
        if _prefix_matches(index, num_qubits, prefix)
    )


def dyadic_stage_angles(spec: GRDistributionSpec) -> list[GRStageAngle]:
    """Return the conditional Ry angles for the full dyadic tree."""
    angles: list[GRStageAngle] = []
    for level in range(spec.num_qubits):
        for prefix_index in range(2**level):
            if level == 0:
                prefix = ()
            else:
                prefix = tuple(int(bit) for bit in format(prefix_index, f"0{level}b"))
            left_prefix = prefix + (0,)
            right_prefix = prefix + (1,)
            left_mass = prefix_mass(spec.probabilities, spec.num_qubits, left_prefix)
            right_mass = prefix_mass(spec.probabilities, spec.num_qubits, right_prefix)
            mass = left_mass + right_mass
            if mass <= 0:
                theta = 0.0
            else:
                ratio = min(max(right_mass / mass, 0.0), 1.0)
                theta = 2.0 * math.asin(math.sqrt(ratio))
            angles.append(
                GRStageAngle(
                    level=level,
                    prefix=prefix,
                    mass=mass,
                    left_mass=left_mass,
                    right_mass=right_mass,
                    theta=theta,
                )
            )
    return angles


def apply_controlled_ry_for_prefix(qc, controls: list[int], target: int, prefix: tuple[int, ...], theta: float) -> None:
    """Apply Ry(theta) to target conditioned on controls matching prefix."""
    if abs(theta) < 1e-15:
        return
    if not controls:
        qc.ry(theta, target)
        return

    zero_controls = [qubit for qubit, bit in zip(controls, prefix) if bit == 0]
    for qubit in zero_controls:
        qc.x(qubit)

    if len(controls) == 1:
        qc.cry(theta, controls[0], target)
    else:
        from qiskit.circuit.library import RYGate

        gate = RYGate(theta).control(len(controls))
        qc.append(gate, [*controls, target])

    for qubit in reversed(zero_controls):
        qc.x(qubit)


def apply_uniformly_controlled_ry_stage(qc, target: int, theta_by_prefix: list[float]) -> None:
    """Apply one Grover-Rudolph stage as a uniformly controlled Ry gate."""
    if not theta_by_prefix:
        return
    if len(theta_by_prefix) == 1:
        if abs(theta_by_prefix[0]) >= 1e-15:
            qc.ry(theta_by_prefix[0], target)
        return

    from qiskit.circuit.library import UCRYGate

    controls = list(reversed(range(target)))
    qc.append(UCRYGate(theta_by_prefix), [target, *controls])


def build_gr_state_preparation_circuit(
    spec: GRDistributionSpec,
    *,
    measure: bool = True,
    implementation: str = "ucry",
):
    """Build the Grover-Rudolph state-preparation circuit for one distribution."""
    from qiskit import QuantumCircuit

    creg_size = spec.num_qubits if measure else 0
    qc = QuantumCircuit(spec.num_qubits, creg_size, name=f"gr_{spec.case_id}")
    angles = dyadic_stage_angles(spec)
    if implementation == "direct":
        for angle in angles:
            controls = list(range(angle.level))
            target = angle.level
            apply_controlled_ry_for_prefix(qc, controls, target, angle.prefix, angle.theta)
    elif implementation == "ucry":
        for level in range(spec.num_qubits):
            theta_by_prefix = [
                angle.theta for angle in angles if angle.level == level
            ]
            apply_uniformly_controlled_ry_stage(qc, level, theta_by_prefix)
    else:
        raise ValueError(
            f"Unknown Grover-Rudolph implementation {implementation!r}; "
            "valid values are 'ucry' and 'direct'."
        )
    if measure:
        qc.measure(list(range(spec.num_qubits)), list(range(spec.num_qubits)))
    return qc


def marked_indices(spec: GRDistributionSpec, event: str) -> list[int]:
    """Return probability-vector indices selected by an event predicate."""
    if event == "upper_half":
        return [index for index in range(spec.grid_points) if index >= spec.grid_points // 2]
    raise ValueError(f"Unknown marked event {event!r}; valid value: 'upper_half'.")


def marked_probability(spec: GRDistributionSpec, event: str) -> float:
    return sum(spec.probabilities[index] for index in marked_indices(spec, event))


def apply_event_phase_oracle(qc, spec: GRDistributionSpec, event: str) -> None:
    """Apply a phase flip to states selected by the event predicate."""
    if event == "upper_half":
        qc.z(0)
        return
    raise ValueError(f"Unknown marked event {event!r}; valid value: 'upper_half'.")


def apply_zero_reflection(qc, num_qubits: int, *, method: str = "mcx") -> None:
    """Reflection about |0...0> up to the global phase used in amplification."""
    if num_qubits < 1:
        raise ValueError("At least one qubit is required.")
    if method == "diagonal":
        from qiskit.circuit.library import Diagonal

        diagonal = [-1.0] + [1.0] * (2**num_qubits - 1)
        qc.append(Diagonal(diagonal), list(range(num_qubits)))
        return

    for qubit in range(num_qubits):
        qc.x(qubit)
    if num_qubits == 1:
        qc.z(0)
    else:
        target = num_qubits - 1
        controls = list(range(num_qubits - 1))
        if method == "mcx":
            qc.h(target)
            qc.mcx(controls, target)
            qc.h(target)
        elif method == "mcp":
            qc.mcp(math.pi, controls, target)
        else:
            raise ValueError(
                f"Unknown zero-reflection method {method!r}; "
                "valid values are 'mcx', 'mcp', and 'diagonal'."
            )
    for qubit in range(num_qubits):
        qc.x(qubit)


def build_gr_event_amplification_circuit(
    spec: GRDistributionSpec,
    k: int,
    *,
    event: str = "upper_half",
    measure: bool = True,
    implementation: str = "ucry",
    reflection_method: str = "mcx",
):
    """Build Q^k A|0> for a Grover-Rudolph distribution and marked event."""
    from qiskit import QuantumCircuit

    creg_size = spec.num_qubits if measure else 0
    qc = QuantumCircuit(spec.num_qubits, creg_size, name=f"grqae_{spec.case_id}_{event}_k{k}")
    preparation = build_gr_state_preparation_circuit(
        spec, measure=False, implementation=implementation
    )
    inverse_preparation = preparation.inverse()

    qc.compose(preparation, inplace=True)
    for _ in range(k):
        apply_event_phase_oracle(qc, spec, event)
        qc.compose(inverse_preparation, inplace=True)
        apply_zero_reflection(qc, spec.num_qubits, method=reflection_method)
        qc.compose(preparation, inplace=True)
    if measure:
        qc.measure(list(range(spec.num_qubits)), list(range(spec.num_qubits)))
    return qc


def bitstring_to_probability_index(bitstring: str) -> int:
    """Convert a Qiskit count key to the big-endian probability-vector index."""
    compact = bitstring.replace(" ", "")
    return int(compact[::-1], 2)


def distribution_from_counts(counts: dict[str, int], grid_points: int) -> list[float]:
    total = sum(counts.values())
    if total == 0:
        return [float("nan")] * grid_points
    distribution = [0.0] * grid_points
    for bitstring, count in counts.items():
        distribution[bitstring_to_probability_index(bitstring)] += count / total
    return distribution


def total_variation_distance(p: Iterable[float], q: Iterable[float]) -> float:
    return 0.5 * sum(abs(float(a) - float(b)) for a, b in zip(p, q))
