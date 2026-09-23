# Phase 1 g2 Stress Protocol

## Purpose

This campaign tests the quadratic benchmark `g2` through the deferred `k=2`
amplification level after two compilation-tuning checks:

- `g0` was stabilized by repeating the calibration pair with
  `optimization_level=2`;
- `g1` passed the full `k=0,1,2` schedule with `optimization_level=2` and MLAE
  absolute error `0.000907`.

`g2` is structurally harder than `g1`: it has degree 2, larger canonical length,
and deeper compiled circuits. This campaign is therefore the next stress test,
not a routine continuation.

## Campaign

```text
experiments/phase1_g2_stress_ibm_fez_opt2.json
```

Selected jobs:

| Case | Schedule | Purpose |
|---|---:|---|
| `g2` | `k=0,1,2` | quadratic encoding stress test with consistent opt2 compilation |

Backend: `ibm_fez`

Shots per job: `2048`

Optimization level: `2`

## Prepare Without Submission

```bash
python3 scripts/submit_campaign.py \
  --campaign experiments/phase1_g2_stress_ibm_fez_opt2.json
```

Inspect the prepared depths and two-qubit counts before submission. The key
decision point is `g2, k=2`.

## Submission

Submit only after reviewing the prepared metadata:

```bash
python3 scripts/submit_campaign.py \
  --campaign experiments/phase1_g2_stress_ibm_fez_opt2.json \
  --submit
```

## Fetching

Use the timestamp printed by the submission command:

```bash
python3 scripts/fetch_campaign_results.py \
  --campaign-run results/hardware/phase1_g2_stress_ibm_fez_opt2/<timestamp> \
  --wait
```

Then analyze:

```bash
python3 scripts/analyze_campaign_results.py \
  --campaign-run results/hardware/phase1_g2_stress_ibm_fez_opt2/<timestamp>
```

## Decision Rule

If the `g2` opt2 schedule produces a usable MLAE estimate and `g2, k=2` is not a
failure outlier, this completes the first evidence chain:

```text
g0 calibration -> g1 affine stress -> g2 quadratic stress
```

If `g2, k=2` fails, pause QPU spending and move to layout selection, readout
mitigation, or zero-noise extrapolation experiments.

## Hardware Readout

The campaign was submitted as:

```text
results/hardware/phase1_g2_stress_ibm_fez_opt2/20260805T091004Z
```

Submitted jobs:

| Case | k | Job ID |
|---|---:|---|
| `g2` | 0 | `d9pfrsl7skus73ckte80` |
| `g2` | 1 | `d9pfrst7skus73ckte90` |
| `g2` | 2 | `d9pfrte28h6s739r8nj0` |

Hardware results:

| Case | k | Expected p | Hardware p_hat | Abs. dev. | z-score | Depth | 2q gates | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `g2` | 0 | 0.500000 | 0.506836 | 0.006836 | 0.62 | 43 | 9 | pass |
| `g2` | 1 | 0.500000 | 0.516113 | 0.016113 | 1.46 | 167 | 43 | pass |
| `g2` | 2 | 0.500000 | 0.504883 | 0.004883 | 0.44 | 291 | 79 | pass |

MLAE estimate:

| Case | Schedule | Exact a | Hardware a_hat | Abs. error |
|---|---|---:|---:|---:|
| `g2` | `[0, 1, 2]` | 0.500000 | 0.499512 | 0.000488 |

This completes the first evidence chain. The quadratic benchmark passes through
`k=2` under `optimization_level=2`, despite being the deepest selected circuit
in Phase 1.

Decision: pause further QPU spending and consolidate the result set into a
Phase 1 summary table, then design Phase 2 around mitigation/layout comparisons
or larger structured Grover-Rudolph instances.
