# Phase 5b: `gr_beta_bump_n4` Focused Repeat

## Objective

Repeat only the localized 16-cell distribution `gr_beta_bump_n4` using the best
credit-free layout/noise audit candidate.

The first hardware run measured:

```text
optimization_level=2, seed=12345
depth=68, two-qubit gates=16
hardware TVD=0.045590
```

The exact-profile noisy audit predicted this candidate well:

```text
predicted noisy TVD=0.043878
```

The best audited candidate is:

```text
optimization_level=3, seed=45678
depth=70, two-qubit gates=16
predicted noisy TVD=0.021744
predicted max deviation=0.006505
```

This repeat tests whether the localized distribution can be stabilized by
layout/seed choice before any amplified QAE attempt.

## Configuration

```text
experiments/phase5_gr_beta_bump_n4_repeat_opt3_seed45678_ibm_fez.json
```

Settings:

- backend: `ibm_fez`;
- shots: 4096;
- optimization level: 3;
- seed: 45678;
- implementation: `ucry`;
- Runtime options: dynamical decoupling with `XY4`, gate twirling, and
  measurement twirling.

## Commands

Prepare without submitting:

```bash
python3 scripts/submit_gr_distribution_campaign.py \
  --campaign experiments/phase5_gr_beta_bump_n4_repeat_opt3_seed45678_ibm_fez.json
```

Submit only after reviewing the prepared circuit:

```bash
python3 scripts/submit_gr_distribution_campaign.py \
  --campaign experiments/phase5_gr_beta_bump_n4_repeat_opt3_seed45678_ibm_fez.json \
  --submit
```

Fetch:

```bash
python3 scripts/fetch_gr_distribution_results.py \
  --campaign-run results/hardware/phase5_gr_beta_bump_n4_repeat_opt3_seed45678_ibm_fez/<RUN_ID> \
  --wait
```

## Decision

If TVD remains near `0.045`, localized 16-cell profiles should remain
direct-distribution-only on the current backend. If TVD moves near `0.02`, the
next step should still be direct observable estimation; amplified QAE remains
deferred until the distribution-level evidence is stable.
