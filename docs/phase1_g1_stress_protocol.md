# Phase 1 g1 Stress Protocol

## Purpose

This campaign tests the deferred `g1, k=2` amplification level after the `g0`
anomaly was reduced by changing from `optimization_level=0` to
`optimization_level=2`.

The campaign runs the full `g1` schedule `k=0,1,2` under the same compilation
setting. This avoids combining the original `optimization_level=0` data with a
new deeper circuit compiled differently.

## Campaign

```text
experiments/phase1_g1_stress_ibm_fez_opt2.json
```

Selected jobs:

| Case | Schedule | Purpose |
|---|---:|---|
| `g1` | `k=0,1,2` | affine encoding stress test with consistent opt2 compilation |

Backend: `ibm_fez`

Shots per job: `2048`

Optimization level: `2`

## Prepare Without Submission

```bash
python3 scripts/submit_campaign.py \
  --campaign experiments/phase1_g1_stress_ibm_fez_opt2.json
```

Inspect the prepared depths and two-qubit counts before submission. The campaign
should only proceed if `g1, k=2` is still a moderate-depth stress test relative
to the deferred `g2, k=2` circuit.

## Submission

Submit only after reviewing the prepared metadata:

```bash
python3 scripts/submit_campaign.py \
  --campaign experiments/phase1_g1_stress_ibm_fez_opt2.json \
  --submit
```

## Fetching

Use the timestamp printed by the submission command:

```bash
python3 scripts/fetch_campaign_results.py \
  --campaign-run results/hardware/phase1_g1_stress_ibm_fez_opt2/<timestamp> \
  --wait
```

Then analyze:

```bash
python3 scripts/analyze_campaign_results.py \
  --campaign-run results/hardware/phase1_g1_stress_ibm_fez_opt2/<timestamp>
```

## Decision Rule

If the `g1` opt2 schedule produces an MLAE absolute error near `1e-2`, it can be
used as positive evidence that the benchmark methodology survives one additional
amplification level after compilation tuning.

If `g1, k=2` has a large deviation, pause before submitting `g2, k=2` and move
to layout selection or mitigation experiments.

## Hardware Readout

The campaign was submitted as:

```text
results/hardware/phase1_g1_stress_ibm_fez_opt2/20260805T090601Z
```

Submitted jobs:

| Case | k | Job ID |
|---|---:|---|
| `g1` | 0 | `d9pfq0m28h6s739r8lk0` |
| `g1` | 1 | `d9pfq0t7skus73cktcag` |
| `g1` | 2 | `d9pfq13bvhrs73a2ilvg` |

Hardware results:

| Case | k | Expected p | Hardware p_hat | Abs. dev. | z-score | Depth | 2q gates | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `g1` | 0 | 0.500000 | 0.504395 | 0.004395 | 0.40 | 18 | 4 | pass |
| `g1` | 1 | 0.500000 | 0.530762 | 0.030762 | 2.78 | 78 | 18 | pass |
| `g1` | 2 | 0.500000 | 0.511230 | 0.011230 | 1.02 | 152 | 36 | pass |

MLAE estimate:

| Case | Schedule | Exact a | Hardware a_hat | Abs. error |
|---|---|---:|---:|---:|
| `g1` | `[0, 1, 2]` | 0.500000 | 0.499093 | 0.000907 |

This is the cleanest hardware result so far. All three `g1` circuits pass the
binomial-deviation screen, and the three-point MLAE estimate improves the
previous two-point `optimization_level=0` estimate by more than an order of
magnitude.

Decision: the methodology is robust for the affine benchmark through `k=2` when
compiled with `optimization_level=2`. The next hardware candidate is the
deferred `g2, k=2`, but it should be prepared under `optimization_level=2` and
reviewed before submission because the `g2` schedule is structurally deeper.
