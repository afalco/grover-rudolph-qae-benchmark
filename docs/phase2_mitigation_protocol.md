# Phase 2 Mitigation Protocol

## Purpose

The Phase 2 layout microcampaign showed that the backend noise model did not
predict the sensitive `g0, k=2` circuit reliably. The next step is therefore
explicit Runtime-level mitigation, not further random seed search.

This protocol tests a minimal mitigation microcampaign on the `g0` calibration
pair only.

## IBM Runtime Features

The campaign uses Qiskit Runtime Sampler options documented by IBM:

- dynamical decoupling;
- Pauli gate twirling;
- measurement twirling.

The options are stored in the campaign JSON and copied into submitted metadata.

## Campaign

```text
experiments/phase2_g0_mitigation_ibm_fez_opt2_dd_twirling.json
```

Selected jobs:

| Case | Schedule | Purpose |
|---|---:|---|
| `g0` | `k=0,2` | test mitigation on the sensitive calibration pair |

Backend: `ibm_fez`

Shots per job: `2048`

Optimization level: `2`

Transpiler seed: `12345`

Runtime options:

```json
{
  "dynamical_decoupling": {
    "enable": true,
    "sequence_type": "XY4",
    "scheduling_method": "alap"
  },
  "twirling": {
    "enable_gates": true,
    "enable_measure": true,
    "num_randomizations": 32,
    "shots_per_randomization": 64,
    "strategy": "active-accum"
  }
}
```

## Prepare Without Submission

```bash
python3 scripts/submit_campaign.py \
  --campaign experiments/phase2_g0_mitigation_ibm_fez_opt2_dd_twirling.json
```

## Submission

Submit only after checking the prepared circuit metrics and remaining Runtime
budget:

```bash
python3 scripts/submit_campaign.py \
  --campaign experiments/phase2_g0_mitigation_ibm_fez_opt2_dd_twirling.json \
  --submit
```

## Fetching

Use the timestamp printed by the submission command:

```bash
python3 scripts/fetch_campaign_results.py \
  --campaign-run results/hardware/phase2_g0_mitigation_ibm_fez_opt2_dd_twirling/<timestamp> \
  --wait
```

Then analyze:

```bash
python3 scripts/analyze_campaign_results.py \
  --campaign-run results/hardware/phase2_g0_mitigation_ibm_fez_opt2_dd_twirling/<timestamp>
```

## Decision Rule

Compare the mitigated `g0, k=2` result against:

| Source | `g0, k=2` abs. dev. |
|---|---:|
| Phase 1 accepted opt2 repeat | `0.047363` |
| Phase 2 layout seed repeat | `0.056641` |

If mitigation improves the deviation, use it as the preferred `g0` calibration
protocol. If not, stop spending QPU time on `g0` and keep `g1/g2` as the robust
positive Phase 1 evidence.

## Hardware Readout

The mitigation campaign was submitted as:

```text
results/hardware/phase2_g0_mitigation_ibm_fez_opt2_dd_twirling/20260805T094505Z
```

Submitted jobs:

| Case | k | Job ID |
|---|---:|---|
| `g0` | 0 | `d9pgca57skus73cku01g` |
| `g0` | 2 | `d9pgcae28h6s739r99fg` |

Hardware results:

| Case | k | Expected p | Hardware p_hat | Abs. dev. | z-score | Depth | 2q gates | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `g0` | 0 | 0.250000 | 0.245117 | 0.004883 | -0.51 | 5 | 0 | pass |
| `g0` | 2 | 0.250000 | 0.291016 | 0.041016 | 4.29 | 71 | 19 | watch |

MLAE estimate:

| Case | Schedule | Exact a | Hardware a_hat | Abs. error |
|---|---|---:|---:|---:|
| `g0` | `[0, 2]` | 0.250000 | 0.242160 | 0.007840 |

Comparison:

| Source | `g0, k=2` abs. dev. | MLAE abs. error |
|---|---:|---:|
| Phase 1 accepted opt2 repeat | 0.047363 | 0.008949 |
| Phase 2 layout seed repeat | 0.056641 | 0.010627 |
| Phase 2 DD + twirling | 0.041016 | 0.007840 |

Decision: mitigation gives the best `g0` result so far, but `g0, k=2` remains in
the watch region. It is worth keeping DD + twirling as the preferred `g0`
calibration protocol, but further QPU spending on this same two-circuit pair is
not justified unless a new mitigation method is being tested.
