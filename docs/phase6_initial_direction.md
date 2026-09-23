# Phase 6: Initial Direction

## Objective

Use the hardware distributions already collected to build a stronger
direct-observable methodology before spending more IBM Quantum Runtime credits.

The immediate target is not another QPU campaign. Phase 5 showed that:

- direct distribution estimation is the most stable near-term workflow;
- amplified QAE is still too sensitive for larger Grover-Rudolph profiles;
- localized profiles such as `gr_beta_bump_n4` require observable-specific
  analysis rather than a single global TVD criterion.

## Inputs

Use existing result files:

```text
results/hardware/phase3_gr_distribution_ibm_fez/20260805T103539Z/fetched_gr_distribution_results.json
results/hardware/phase3_gr_affine_n4_mitigation_ibm_fez/20260805T112409Z/fetched_gr_distribution_results.json
results/hardware/phase5_gr_distribution_mitigation_suite_ibm_fez/20260805T172609Z/fetched_gr_distribution_results.json
results/hardware/phase5_gr_beta_bump_n4_repeat_opt3_seed45678_ibm_fez/20260805T173609Z/fetched_gr_distribution_results.json
```

## Planned Work

1. Extend distribution metrics:
   - TVD;
   - classical fidelity;
   - Hellinger distance;
   - L2/Frobenius-like distance;
   - maximum cell deviation.

2. Extend uncertainty quantification:
   - bootstrap intervals for TVD and observable errors;
   - repeated-run comparison where available;
   - case-by-case mitigation comparison.

3. Add observable-specific reporting:
   - `upper_half`;
   - `mean_x`;
   - `second_moment_x2`;
   - `sin_pi_x`;
   - `call_x_minus_half`;
   - any project-specific payoff or integrand added later.

4. Prepare a simulator-only purification diagnostic inspired by the mixed-state
   preparation reference:
   - load \(p\) with the Grover-Rudolph circuit;
   - copy the computational-basis index into an auxiliary register;
   - compare register marginals and mismatch rate.

## Decision Rule

No additional QPU work should be submitted until Phase 6 postprocessing has
identified a concrete, observable-specific reason for a new campaign.
