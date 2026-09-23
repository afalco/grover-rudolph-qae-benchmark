# Phase 3: Grover-Rudolph Distribution Measurement Campaign

## Objective

Measure the output distributions of the first larger Grover-Rudolph
state-preparation circuits on `ibm_fez`, before adding any QAE amplification.

This campaign answers a narrower question than QAE:

```text
Does the hardware reproduce the prepared dyadic probability law with acceptable
total variation distance?
```

## Campaign

Configuration:

```text
experiments/phase3_gr_distribution_ibm_fez.json
```

Selected circuits:

| case | qubits | grid | shots |
|---|---:|---:|---:|
| `gr_sin2_n3` | 3 | 8 | 2048 |
| `gr_sin2_half_n3` | 3 | 8 | 2048 |
| `gr_affine_n4` | 4 | 16 | 2048 |

Prepared `ibm_fez` metrics:

| case | transpiled depth | two-qubit gates |
|---|---:|---:|
| `gr_sin2_n3` | 31 | 7 |
| `gr_sin2_half_n3` | 36 | 7 |
| `gr_affine_n4` | 76 | 16 |

## Commands

Prepare without submitting:

```bash
python3 scripts/submit_gr_distribution_campaign.py \
  --campaign experiments/phase3_gr_distribution_ibm_fez.json
```

Submit only after reviewing the prepared metadata:

```bash
python3 scripts/submit_gr_distribution_campaign.py \
  --campaign experiments/phase3_gr_distribution_ibm_fez.json \
  --submit
```

Fetch results:

```bash
python3 scripts/fetch_gr_distribution_results.py --wait
```

Or fetch a specific run:

```bash
python3 scripts/fetch_gr_distribution_results.py \
  --campaign-run results/hardware/phase3_gr_distribution_ibm_fez/<RUN_ID> \
  --wait
```

## Metrics

The result fetcher stores:

- raw bitstring counts;
- observed distribution in probability-vector order;
- target Grover-Rudolph probabilities;
- total variation distance;
- L1 distance;
- maximum per-cell absolute deviation;
- chi-square statistic;
- circuit depth and two-qubit metrics from submission metadata.

## Decision Rule

Proceed to amplified QAE circuits only if the measured total variation distance
is consistent with the Phase 0-GR noisy simulation and sampling error.

If the `n=3` circuits are stable but `gr_affine_n4` degrades, the next step is a
layout/seed audit for `n=4`, not QAE amplification.
