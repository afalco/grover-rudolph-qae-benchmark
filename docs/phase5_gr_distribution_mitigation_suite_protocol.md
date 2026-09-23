# Phase 5: Mitigated Grover-Rudolph Distribution Suite

## Objective

Test whether the direct-distribution methodology remains viable beyond the
mitigated `gr_affine_n4` reference run.

The previous amplified `gr_affine_n4 k=1` campaigns showed that the full Grover
iterate is currently too sensitive:

```text
k=1 opt=2: p_hat=0.173828, expected=0.052765, dev=0.121064
k=1 opt=3: p_hat=0.124023, expected=0.052765, dev=0.071259
```

By contrast, the mitigated direct-distribution estimator for the same affine
law gave the best current upper-half estimate:

```text
distribution direct, mitigated: estimate=0.671387, exact=0.680380, error=0.008993
```

This phase therefore measures distributions directly, with no Grover
amplification and no QAE estimator.

## Configuration

```text
experiments/phase5_gr_distribution_mitigation_suite_ibm_fez.json
```

Settings:

- backend: `ibm_fez`;
- shots: 4096 per circuit;
- optimization level: 2;
- implementation: `ucry`;
- Runtime options: dynamical decoupling with `XY4`, gate twirling, and
  measurement twirling.

Selected cases:

```text
gr_sin2_n3
gr_sin2_half_n3
gr_beta_bump_n4
```

The first two cases are mitigated repeats of stable 8-cell distributions. The
third case is the deferred localized 16-cell profile and is the stress test for
distribution loading.

## Commands

Prepare without submitting:

```bash
python3 scripts/submit_gr_distribution_campaign.py \
  --campaign experiments/phase5_gr_distribution_mitigation_suite_ibm_fez.json
```

Submit only after reviewing the prepared metadata:

```bash
python3 scripts/submit_gr_distribution_campaign.py \
  --campaign experiments/phase5_gr_distribution_mitigation_suite_ibm_fez.json \
  --submit
```

Fetch results:

```bash
python3 scripts/fetch_gr_distribution_results.py \
  --campaign-run results/hardware/phase5_gr_distribution_mitigation_suite_ibm_fez/<RUN_ID> \
  --wait
```

Analyze direct observables after fetching:

```bash
python3 scripts/analyze_gr_distribution_observables.py
```

## Decision Rules

- If `gr_beta_bump_n4` has TVD substantially above `0.05`, do not attempt QAE on
  localized 16-cell profiles before a layout/seed audit.
- If DD/twirling worsens the two n=3 profiles relative to their raw runs, use
  mitigation selectively rather than as a default setting.
- If all three distributions are close to their Phase 0 noisy predictions,
  proceed with direct-observable estimation as the main near-term hardware
  methodology.
