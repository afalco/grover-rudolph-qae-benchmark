"""Angle-structure classification for QAE amplitude functions."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable

from .gr_state_preparation import function_weights, midpoint_grid, normalize_weights


@dataclass(frozen=True)
class AngleStructureClassification:
    case_id: str
    num_qubits: int
    grid: str
    input_kind: str
    degree: int
    support: int
    coefficient_tolerance: float
    values: tuple[float, ...]
    angles: tuple[float, ...]
    angle_coefficients: dict[tuple[int, ...], float]
    nonzero_coefficients: dict[tuple[int, ...], float]
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["angle_coefficients"] = {
            subset_label(key): value for key, value in self.angle_coefficients.items()
        }
        data["nonzero_coefficients"] = {
            subset_label(key): value for key, value in self.nonzero_coefficients.items()
        }
        data["class"] = f"G_{self.num_qubits}^{self.degree}"
        return data


def subset_label(subset: tuple[int, ...]) -> str:
    if not subset:
        return "1"
    return "*".join(f"b{index}" for index in subset)


def grid_points(num_qubits: int, *, grid: str = "midpoint") -> list[float]:
    if grid == "midpoint":
        return midpoint_grid(2**num_qubits)
    if grid == "left":
        return [index / (2**num_qubits) for index in range(2**num_qubits)]
    raise ValueError("valid grids are 'midpoint' and 'left'")


def bit_tuple(index: int, num_qubits: int) -> tuple[int, ...]:
    return tuple(int(bit) for bit in format(index, f"0{num_qubits}b"))


def zeta_values_by_mask(values: Iterable[float], num_qubits: int) -> list[float]:
    ordered = list(values)
    if len(ordered) != 2**num_qubits:
        raise ValueError(f"Expected {2**num_qubits} values, got {len(ordered)}.")
    out = [0.0] * (2**num_qubits)
    for index, value in enumerate(ordered):
        bits = bit_tuple(index, num_qubits)
        mask = 0
        for pos, bit in enumerate(bits):
            if bit:
                mask |= 1 << pos
        out[mask] = float(value)
    return out


def multilinear_coefficients(values: Iterable[float], num_qubits: int) -> dict[tuple[int, ...], float]:
    """Return coefficients in the basis prod_{i in S} b_i on {0,1}^n."""
    coeffs = zeta_values_by_mask(values, num_qubits)
    for bit in range(num_qubits):
        step = 1 << bit
        for mask in range(2**num_qubits):
            if mask & step:
                coeffs[mask] -= coeffs[mask ^ step]
    out: dict[tuple[int, ...], float] = {}
    for mask, value in enumerate(coeffs):
        subset = tuple(index for index in range(num_qubits) if mask & (1 << index))
        out[subset] = value
    return out


def angle_map_from_values(values: Iterable[float]) -> tuple[float, ...]:
    angles = []
    for value in values:
        clipped = min(max(float(value), 0.0), 1.0)
        angles.append(2.0 * math.asin(math.sqrt(clipped)))
    return tuple(angles)


def classify_values(
    case_id: str,
    values: Iterable[float],
    *,
    num_qubits: int,
    grid: str = "midpoint",
    input_kind: str = "qae_function",
    tolerance: float = 1e-10,
    notes: str = "",
) -> AngleStructureClassification:
    value_tuple = tuple(float(value) for value in values)
    if len(value_tuple) != 2**num_qubits:
        raise ValueError(f"{case_id}: expected {2**num_qubits} values, got {len(value_tuple)}.")
    if any(value < -tolerance or value > 1.0 + tolerance for value in value_tuple):
        raise ValueError(
            f"{case_id}: angle-structure classification requires values in [0,1]."
        )
    angle_tuple = angle_map_from_values(value_tuple)
    coeffs = multilinear_coefficients(angle_tuple, num_qubits)
    nonzero = {
        subset: value for subset, value in coeffs.items() if abs(value) > tolerance
    }
    degree = max((len(subset) for subset in nonzero), default=0)
    return AngleStructureClassification(
        case_id=case_id,
        num_qubits=num_qubits,
        grid=grid,
        input_kind=input_kind,
        degree=degree,
        support=len(nonzero),
        coefficient_tolerance=tolerance,
        values=value_tuple,
        angles=angle_tuple,
        angle_coefficients=coeffs,
        nonzero_coefficients=nonzero,
        notes=notes,
    )


def builtin_function_values(case_id: str, num_qubits: int, *, grid: str = "midpoint") -> tuple[float, ...]:
    xs = grid_points(num_qubits, grid=grid)
    if case_id == "g0":
        return tuple(0.25 for _ in xs)
    if case_id == "g1":
        return tuple(math.sin(math.pi * x / 2.0) ** 2 for x in xs)
    if case_id == "g2":
        return tuple(math.sin(math.pi * x) ** 2 for x in xs)
    raise ValueError(f"Unknown builtin angle-structure case {case_id!r}.")


def qoi_function_values(name: str, num_qubits: int, *, grid: str = "midpoint") -> tuple[float, ...]:
    xs = grid_points(num_qubits, grid=grid)
    if name == "upper_half":
        return tuple(1.0 if x >= 0.5 else 0.0 for x in xs)
    if name == "mean_x":
        return tuple(xs)
    if name == "second_moment_x2":
        return tuple(x * x for x in xs)
    if name == "sin_pi_x":
        return tuple(math.sin(math.pi * x) for x in xs)
    if name == "call_x_minus_half":
        return tuple(max(x - 0.5, 0.0) for x in xs)
    raise ValueError(f"Unknown quantity of interest {name!r}.")


def values_from_case_function(
    case: dict[str, Any],
    *,
    input_kind: str,
) -> tuple[float, ...]:
    num_qubits = int(case["num_qubits"])
    function = dict(case["function"])
    kind = str(function.pop("kind"))
    raw_values = tuple(function_weights(kind, 2**num_qubits, **function))
    if input_kind == "qae_function":
        return raw_values
    if input_kind == "normalized_probability":
        return normalize_weights(raw_values)
    if input_kind == "max_normalized_profile":
        scale = max(raw_values)
        if scale <= 0.0:
            raise ValueError(f"{case['case_id']}: cannot normalize zero profile.")
        return tuple(value / scale for value in raw_values)
    raise ValueError(
        "valid input kinds are 'qae_function', 'normalized_probability', "
        "and 'max_normalized_profile'"
    )


def classify_callable(
    case_id: str,
    fn: Callable[[float], float],
    *,
    num_qubits: int,
    grid: str = "midpoint",
    input_kind: str = "qae_function",
    tolerance: float = 1e-10,
    notes: str = "",
) -> AngleStructureClassification:
    values = tuple(fn(x) for x in grid_points(num_qubits, grid=grid))
    return classify_values(
        case_id,
        values,
        num_qubits=num_qubits,
        grid=grid,
        input_kind=input_kind,
        tolerance=tolerance,
        notes=notes,
    )
