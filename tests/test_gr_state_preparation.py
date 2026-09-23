from __future__ import annotations

import math

from grover_rudolph_qae_benchmark.gr_state_preparation import (
    GRDistributionSpec,
    dyadic_stage_angles,
    normalize_weights,
    spec_from_config,
)


def test_normalize_weights() -> None:
    probabilities = normalize_weights([1.0, 2.0, 1.0])
    assert probabilities == (0.25, 0.5, 0.25)


def test_dyadic_stage_angles_reconstruct_node_masses() -> None:
    spec = GRDistributionSpec(
        case_id="toy",
        label="toy",
        num_qubits=2,
        probabilities=(0.1, 0.2, 0.3, 0.4),
        source="test",
    )
    angles = dyadic_stage_angles(spec)

    assert len(angles) == 3
    for angle in angles:
        assert math.isclose(angle.left_mass + angle.right_mass, angle.mass)

    root = angles[0]
    assert root.prefix == ()
    assert math.isclose(root.left_mass, 0.3)
    assert math.isclose(root.right_mass, 0.7)
    assert math.isclose(math.sin(root.theta / 2) ** 2, 0.7)


def test_spec_from_function_config_normalizes_to_power_of_two_grid() -> None:
    spec = spec_from_config(
        {
            "case_id": "affine_n3",
            "num_qubits": 3,
            "function": {"kind": "affine", "offset": 0.2, "slope": 0.4},
        }
    )
    assert spec.num_qubits == 3
    assert len(spec.probabilities) == 8
    assert math.isclose(sum(spec.probabilities), 1.0)
