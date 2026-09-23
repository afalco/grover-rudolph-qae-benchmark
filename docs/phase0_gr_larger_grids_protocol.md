# Phase 0-GR: Larger Grover-Rudolph State-Preparation Audit

## Objective

Build and audit real Grover-Rudolph state-preparation circuits for larger
dyadic grids before deciding whether any instance is viable on `ibm_fez`.

This phase is credit-free: it does not submit QPU jobs.

## Construction

The circuit generator follows the dyadic probability-tree construction of
Falco, Falco-Pomares, and Matthies. Given a normalized probability vector

```math
p_0,\ldots,p_{2^n-1},
```

the generator computes conditional masses on the dyadic tree. At level `j`, for
each binary prefix `b`, it applies one `Ry(theta_b)` rotation to the next qubit,
conditioned on the prefix. The angle is chosen from

```math
\sin^2(\theta_b/2)
  =
  \frac{P(b1)}{P(b0)+P(b1)}.
```

Thus the target state is

```math
\sum_{k=0}^{2^n-1}\sqrt{p_k}\,|k\rangle .
```

The implemented circuit uses Qiskit's uniformly controlled `Ry` gate
implementation (`UCRYGate`) for each Grover-Rudolph level. This matches the
ancilla-free Gray-code/Walsh-Hadamard ladder viewpoint in the Falco,
Falco-Pomares, and Matthies construction and avoids compiling each prefix as an
independent multi-controlled rotation.

## Initial Instances

The first audit configuration is:

```text
experiments/phase0_gr_larger_grids_ibm_fez.json
```

It includes:

- `gr_sin2_half_n3`: 8 midpoint cells;
- `gr_sin2_n3`: 8 midpoint cells;
- `gr_affine_n4`: 16 midpoint cells;
- `gr_beta_bump_n4`: 16 midpoint cells.

The `n=3` cases test the transition beyond the original three-qubit calibration
circuits. The `n=4` cases are the first nontrivial larger dyadic grids with
non-uniform conditional rotations.

## Commands

Logical construction and ideal simulation only:

```bash
python3 scripts/phase0_gr_audit.py \
  --config experiments/phase0_gr_larger_grids_ibm_fez.json \
  --skip-transpile
```

Backend-aware audit on `ibm_fez`:

```bash
python3 scripts/phase0_gr_audit.py \
  --config experiments/phase0_gr_larger_grids_ibm_fez.json \
  --backend ibm_fez
```

The outputs are written to:

```text
results/phase0_gr/
```

## First `ibm_fez` Readout

The first backend-aware audit with the `ucry` implementation was:

```text
results/phase0_gr/phase0_gr_audit_20260805T102606Z.csv
```

Summary:

| case | qubits | grid | transpiled depth | two-qubit gates | noisy TVD | status |
|---|---:|---:|---:|---:|---:|---|
| `gr_sin2_half_n3` | 3 | 8 | 36 | 7 | 0.0241 | candidate |
| `gr_sin2_n3` | 3 | 8 | 31 | 7 | 0.0200 | candidate |
| `gr_affine_n4` | 4 | 16 | 76 | 16 | 0.0281 | candidate |
| `gr_beta_bump_n4` | 4 | 16 | 70 | 16 | 0.0320 | candidate |

The exact statevector total variation distance is at numerical roundoff level
for all four cases, confirming that the dyadic probability-tree implementation
prepares the intended law.

## Metrics

For each distribution, the audit stores:

- logical circuit and QASM;
- transpiled circuit and QASM when a backend is selected;
- grid size and number of qubits;
- number of conditional rotations;
- logical and transpiled depth;
- two-qubit gate count and two-qubit depth;
- ideal total variation distance to the target distribution;
- noisy total variation distance using an Aer model extracted from the backend;
- candidate/rejection status from depth and two-qubit thresholds.

## Decision Rule

A circuit is a candidate only if:

- the transpiled depth is below the configured threshold;
- the transpiled two-qubit gate count is below the configured threshold;
- the noisy total variation distance is small enough to justify a hardware
  measurement campaign.

The next hardware step should be a state-preparation measurement campaign, not
yet QAE amplification. QAE should only be added after the state loader itself has
acceptable cost and output-distribution stability.

Based on this audit, the best first hardware measurement campaign should use
the two `n=3` circuits and one `n=4` circuit, preferably `gr_affine_n4`. The
`gr_beta_bump_n4` circuit is also viable by depth and two-qubit count, but its
more localized distribution makes the measured total variation distance more
sensitive to sampling and readout noise.
