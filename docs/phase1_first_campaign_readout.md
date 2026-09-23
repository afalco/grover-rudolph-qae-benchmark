# Phase 1 First Campaign Readout

Campaign run:

```text
results/hardware/phase1_calibration_ibm_fez/20260805T083930Z
```

Backend: `ibm_fez`

Shots per circuit: `2048`

## Summary

The first hardware campaign validates the basic workflow and gives usable
first estimates for `g1` and `g2`, but it also identifies one clear failure
case: `g0, k=2`.

| Case | k | Expected p | Hardware p_hat | Abs. dev. | z-score | Depth | 2q gates | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `g0` | 0 | 0.250000 | 0.256836 | 0.006836 | 0.71 | 6 | 0 | pass |
| `g0` | 2 | 0.250000 | 0.597656 | 0.347656 | 36.33 | 180 | 24 | fail |
| `g1` | 0 | 0.500000 | 0.543457 | 0.043457 | 3.93 | 60 | 7 | watch |
| `g1` | 1 | 0.500000 | 0.548828 | 0.048828 | 4.42 | 241 | 27 | watch |
| `g2` | 0 | 0.500000 | 0.475586 | 0.024414 | -2.21 | 116 | 16 | pass |
| `g2` | 1 | 0.500000 | 0.544434 | 0.044434 | 4.02 | 421 | 63 | watch |

The corresponding MLAE estimates are:

| Case | Schedule | Exact a | Hardware a_hat | Abs. error |
|---|---|---:|---:|---:|
| `g0` | `[0, 2]` | 0.250000 | 0.192990 | 0.057010 |
| `g1` | `[0, 1]` | 0.500000 | 0.489683 | 0.010317 |
| `g2` | `[0, 1]` | 0.500000 | 0.484213 | 0.015787 |

## Interpretation

The `g0, k=0` circuit is a clean amplitude calibration point. Its deviation is
consistent with shot noise and readout/systematic bias at this scale.

The `g0, k=2` result is not a statistical fluctuation. For 2048 shots and
expected probability 0.25, the observed deviation corresponds to about 36
binomial standard deviations. Since `g0` has the simplest state preparation,
this points to amplified coherent/noise sensitivity in the Grover iterate,
transpilation/layout effects, or a circuit-convention issue that only appears
after repeated amplification.

The `g1` and `g2` results are acceptable for a first calibration campaign. Their
MLAE estimates have absolute errors near `1e-2`, but several individual
probabilities are in the watch region, so they should not yet be used as final
benchmark evidence without mitigation and repetition.

## Immediate Decision

Do not submit the deferred `g1, k=2` or `g2, k=2` jobs yet.

Before spending more credits, run local diagnostics on the submitted circuits:

1. verify the amplified probability using the saved transpiled QASM circuits;
2. compare ideal simulation before and after transpilation;
3. test whether optimization level, layout, or basis decomposition changes the
   `g0, k=2` prediction;
4. only then consider a small repeat campaign for `g0, k=0,2` with an alternate
   compilation setting.

Run the diagnostic script in the same Python environment used for IBM
submission:

```bash
python3 scripts/diagnose_g0_k2.py
```

For a local-only diagnostic that does not contact IBM Runtime:

```bash
python3 scripts/diagnose_g0_k2.py --skip-backend
```

The expected diagnostic outcome is that the logical circuit and the saved QASM
predict `p=0.25` in ideal simulation. If the saved QASM also predicts a value
far from `0.25`, the issue is a circuit or transpilation convention mismatch. If
the saved QASM predicts `0.25`, the hardware deviation should be treated as a
backend/noise sensitivity issue and the repeat campaign must change compilation
or mitigation settings.

The local-only diagnostic was run with:

```bash
python3 scripts/diagnose_g0_k2.py --skip-backend
```

Observed diagnostic table:

| Label | Qubits | Depth | 2q gates | Exact p | Ideal-shot p |
|---|---:|---:|---:|---:|---:|
| `g0_k2_logical` | 3 | 18 | 0 | 0.250000 | 0.236816 |
| `g0_k2_saved_qasm` | 3 | 180 | 24 | 0.250000 | 0.236816 |
| `g0_k2_opt0` | 3 | 18 | 0 | 0.250000 | 0.236816 |
| `g0_k2_opt1` | 3 | 6 | 0 | 0.250000 | 0.236816 |
| `g0_k2_opt2` | 3 | 6 | 0 | 0.250000 | 0.236816 |
| `g0_k2_opt3` | 3 | 6 | 0 | 0.250000 | 0.236816 |

This rules out a logical circuit error and a saved-QASM convention error. The
large hardware deviation of `g0, k=2` must be treated as a physical
implementation issue: native-basis depth, physical layout, calibration drift, or
noise sensitivity of the Grover iterate.

The next non-credit diagnostic is backend-aware simulation:

```bash
python3 scripts/diagnose_g0_k2.py
```

This contacts IBM Runtime only to retrieve the backend and noise model; it does
not submit QPU jobs.

The backend-aware diagnostic was run and produced:

| Label | Depth | 2q gates | Exact p | Ideal-shot p | Noisy-shot p |
|---|---:|---:|---:|---:|---:|
| `g0_k2_logical` | 18 | 0 | 0.250000 | 0.236816 | n/a |
| `g0_k2_saved_qasm` | 180 | 24 | 0.250000 | 0.236816 | 0.284180 |
| `g0_k2_opt0` | 180 | 24 | 0.250000 | 0.236816 | 0.284180 |
| `g0_k2_opt1` | 85 | 21 | see note | 0.256348 | 0.313477 |
| `g0_k2_opt2` | 71 | 19 | 0.250000 | 0.236816 | 0.273926 |
| `g0_k2_opt3` | 71 | 19 | see note | 0.257812 | 0.284180 |

Note: the diagnostic script was updated after this run so that exact
probabilities for transpiled circuits follow the measured classical ancilla bit
`c[2]` after layout. The `ideal-shot p` and `noisy-shot p` columns already use
the measured bitstrings and are the relevant operational comparison.

The preferred repeat configuration is therefore `optimization_level=2`, not
`optimization_level=1`: it has lower depth, fewer two-qubit gates, and the best
backend-noise prediction among the tested variants.

Prepare the repeat without submission:

```bash
python3 scripts/submit_campaign.py \
  --campaign experiments/phase1_g0_repeat_ibm_fez_opt2.json
```

## Controlled g0 Repeat

The `optimization_level=2` repeat was submitted as:

```text
results/hardware/phase1_g0_repeat_ibm_fez_opt2/20260805T085729Z
```

Submitted jobs:

| Case | k | Job ID |
|---|---:|---|
| `g0` | 0 | `d9pfm0m28h6s739r8hk0` |
| `g0` | 2 | `d9pfm13bvhrs73a2ii3g` |

Hardware results:

| Case | k | Expected p | Hardware p_hat | Abs. dev. | z-score | Depth | 2q gates | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `g0` | 0 | 0.250000 | 0.246094 | 0.003906 | -0.41 | 5 | 0 | pass |
| `g0` | 2 | 0.250000 | 0.297363 | 0.047363 | 4.95 | 71 | 19 | watch |

MLAE estimate:

| Case | Schedule | Exact a | Hardware a_hat | Abs. error |
|---|---|---:|---:|---:|
| `g0` | `[0, 2]` | 0.250000 | 0.241051 | 0.008949 |

This resolves the original `g0, k=2` anomaly as a compilation-sensitive hardware
effect. The first campaign gave `p_hat=0.597656` and MLAE error `0.057010`; the
controlled `optimization_level=2` repeat gave `p_hat=0.297363` and MLAE error
`0.008949`. The repeat is still in the watch region, but it is now consistent
with a manageable residual hardware bias rather than a circuit-convention error.

Operational rule: use `optimization_level=2` or an explicitly justified layout
strategy for follow-up `g0` runs on `ibm_fez`; do not use the original
`optimization_level=0` circuit as benchmark evidence for `g0, k=2`.

## g1 opt2 Stress Result

The `g1` stress campaign was submitted as:

```text
results/hardware/phase1_g1_stress_ibm_fez_opt2/20260805T090601Z
```

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

This provides positive evidence that the methodology remains stable for the
affine benchmark at `k=2` after compilation tuning. The next candidate is a
carefully prepared `g2` opt2 campaign.

## g2 opt2 Stress Result

The `g2` stress campaign was submitted as:

```text
results/hardware/phase1_g2_stress_ibm_fez_opt2/20260805T091004Z
```

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

This completes the Phase 1 hardware evidence chain:

| Case | Accepted schedule | Optimization level | Hardware a_hat | Abs. error |
|---|---|---:|---:|---:|
| `g0` | `[0, 2]` | 2 | 0.241051 | 0.008949 |
| `g1` | `[0, 1, 2]` | 2 | 0.499093 | 0.000907 |
| `g2` | `[0, 1, 2]` | 2 | 0.499512 | 0.000488 |

Decision: pause QPU spending here and consolidate Phase 1. The project now has a
complete initial methodology demonstration: Phase 0 audit, hardware anomaly
detection, compilation-sensitive correction, and successful affine/quadratic
stress tests through `k=2`.

## Reproducible Artifacts

Fetched results:

```text
results/hardware/phase1_calibration_ibm_fez/20260805T083930Z/fetched_campaign_results.json
results/hardware/phase1_calibration_ibm_fez/20260805T083930Z/fetched_campaign_results.csv
```

Analysis table:

```text
results/hardware/phase1_calibration_ibm_fez/20260805T083930Z/analysis_summary.csv
```

Analysis script:

```bash
python3 scripts/analyze_campaign_results.py
```

Diagnostic script:

```bash
python3 scripts/diagnose_g0_k2.py
```

Phase 1 accepted-result summary:

```bash
python3 scripts/summarize_phase1_results.py
```

Summary artifacts:

```text
results/hardware/phase1_summary/phase1_accepted_circuits.csv
results/hardware/phase1_summary/phase1_mlae_estimates.csv
results/hardware/phase1_summary/phase1_summary.json
```
