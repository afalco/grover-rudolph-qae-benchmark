# Phase 3b: `gr_affine_n4` Distribution Microcampaign

## Objective

Repeat only the 16-point affine Grover-Rudolph distribution after the first
distribution campaign measured:

```text
gr_affine_n4: TVD = 0.047882
```

The Phase 0-GR noisy estimate was lower:

```text
gr_affine_n4: noisy TVD = 0.028098
```

This microcampaign tests whether the difference is reducible with light Runtime
mitigation and more shots, still without QAE amplification.

## Configuration

```text
experiments/phase3_gr_affine_n4_mitigation_ibm_fez.json
```

Settings:

- backend: `ibm_fez`;
- shots: 4096;
- optimization level: 2;
- implementation: `ucry`;
- Runtime options: dynamical decoupling with `XY4`, gate twirling, and
  measurement twirling.

## Commands

Prepare without submitting:

```bash
python3 scripts/submit_gr_distribution_campaign.py \
  --campaign experiments/phase3_gr_affine_n4_mitigation_ibm_fez.json
```

Submit after reviewing the prepared circuit:

```bash
python3 scripts/submit_gr_distribution_campaign.py \
  --campaign experiments/phase3_gr_affine_n4_mitigation_ibm_fez.json \
  --submit
```

Fetch:

```bash
python3 scripts/fetch_gr_distribution_results.py \
  --campaign-run results/hardware/phase3_gr_affine_n4_mitigation_ibm_fez/<RUN_ID> \
  --wait
```

## Decision

If the repeated TVD moves toward the Phase 0 noisy estimate, use the same
Runtime options for the first amplified 16-point test. If the TVD remains near
0.05 or worsens, perform a dedicated layout/seed audit for `gr_affine_n4` before
adding QAE.
