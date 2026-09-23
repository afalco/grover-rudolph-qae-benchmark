# Phase 2 Layout and Compilation Audit

## Purpose

Phase 1 established the first accepted hardware evidence chain:

```text
g0 calibration -> g1 affine stress -> g2 quadratic stress
```

The next phase should not start with additional QPU spending. The remaining
technical question is whether the residual `g0, k=2` bias and the deeper `g2`
circuits can be improved by layout and compilation choices.

This protocol defines a credit-free audit over optimization levels and
transpiler seeds on `ibm_fez`.

## Configuration

```text
experiments/phase2_layout_audit_ibm_fez.json
```

The audit covers the accepted Phase 1 schedules:

| Case | Schedule |
|---|---:|
| `g0` | `k=0,2` |
| `g1` | `k=0,1,2` |
| `g2` | `k=0,1,2` |

For each circuit, it evaluates several optimization levels and transpiler seeds.
It records depth, two-qubit gate count, two-qubit depth, ideal-shot probability,
backend-noise-shot probability, and deviation from the ideal model.

## Run

```bash
python3 scripts/phase2_layout_audit.py \
  --config experiments/phase2_layout_audit_ibm_fez.json
```

This contacts IBM Runtime only to retrieve the backend. It does not submit QPU
jobs.

## Outputs

The audit writes timestamped artifacts under:

```text
results/phase2/layout_audit/
```

Main files:

```text
phase2_layout_audit_<timestamp>.json
phase2_layout_audit_<timestamp>.csv
phase2_layout_audit_best_<timestamp>.csv
```

## Decision Rule

If the audit finds candidates that improve the predicted noisy deviation without
increasing depth or two-qubit count, Phase 2 hardware should be a microcampaign
that repeats only the sensitive circuits under the selected compilation
settings.

If no clear improvement is found, Phase 2 should move to mitigation experiments
instead of spending QPU time on more random compilations.

## Audit Readout

The first audit was written to:

```text
results/phase2/layout_audit/phase2_layout_audit_20260805T092909Z.json
results/phase2/layout_audit/phase2_layout_audit_20260805T092909Z.csv
results/phase2/layout_audit/phase2_layout_audit_best_20260805T092909Z.csv
```

Best candidates:

| Case | k | Opt. | Seed | Depth | 2q gates | Noisy p | Abs. dev. |
|---|---:|---:|---:|---:|---:|---:|---:|
| `g0` | 0 | 1 | 12345 | 5 | 0 | 0.243652 | 0.006348 |
| `g0` | 2 | 2 | 67890 | 76 | 19 | 0.258545 | 0.008545 |
| `g1` | 0 | 3 | 12345 | 18 | 4 | 0.503906 | 0.003906 |
| `g1` | 1 | 3 | 23456 | 79 | 20 | 0.501953 | 0.001953 |
| `g1` | 2 | 1 | 23456 | 187 | 44 | 0.500000 | 0.000000 |
| `g2` | 0 | 2 | 12345 | 43 | 9 | 0.500977 | 0.000977 |
| `g2` | 1 | 2 | 12345 | 167 | 43 | 0.496826 | 0.003174 |
| `g2` | 2 | 3 | 12345 | 291 | 79 | 0.503662 | 0.003662 |

The actionable candidate is `g0, k=2`: the accepted Phase 1 hardware run had
absolute deviation `0.047363`, while the audit predicts `0.008545` with the same
two-qubit count and only a small depth increase. The `g1` and `g2` Phase 1
hardware results are already strong, so they should not be repeated merely
because the audit found small simulated improvements.

Prepare the Phase 2 microcampaign without submission:

```bash
python3 scripts/submit_campaign.py \
  --campaign experiments/phase2_g0_layout_repeat_ibm_fez_opt2_seed67890.json
```

## Microcampaign Readout

The Phase 2 `g0` layout microcampaign was submitted as:

```text
results/hardware/phase2_g0_layout_repeat_ibm_fez_opt2_seed67890/20260805T093851Z
```

Submitted jobs:

| Case | k | Job ID |
|---|---:|---|
| `g0` | 0 | `d9pg9ct7skus73ckts60` |
| `g0` | 2 | `d9pg9d628h6s739r95r0` |

Hardware results:

| Case | k | Expected p | Hardware p_hat | Abs. dev. | z-score | Depth | 2q gates | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `g0` | 0 | 0.250000 | 0.245605 | 0.004395 | -0.46 | 5 | 0 | pass |
| `g0` | 2 | 0.250000 | 0.306641 | 0.056641 | 5.92 | 76 | 19 | watch |

MLAE estimate:

| Case | Schedule | Exact a | Hardware a_hat | Abs. error |
|---|---|---:|---:|---:|
| `g0` | `[0, 2]` | 0.250000 | 0.239373 | 0.010627 |

The audit-selected seed did not improve the hardware result. The predicted noisy
deviation for `g0, k=2` was `0.008545`, but the measured deviation was
`0.056641`, slightly worse than the accepted Phase 1 repeat deviation
`0.047363`.

Decision: stop random layout/seed microcampaigns for `g0`. The residual
`g0, k=2` bias is not reliably predicted by the backend noise model used in this
audit. The next meaningful Phase 2 direction is mitigation, such as readout
mitigation, dynamical decoupling, twirling, or zero-noise extrapolation, rather
than further seed search.
