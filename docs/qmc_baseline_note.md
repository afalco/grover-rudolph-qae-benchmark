# Note on the Quasi-Monte Carlo Baseline

Code review of the QMC baselines used in Phases 1b, 10, 15c and 16.
No QPU time involved.

## What the baseline is

A **base-2 van der Corput (radical-inverse) sequence**, randomised by a single
uniform shift modulo 1 (a Cranley--Patterson rotation). There is **no
scrambling**, and no external QMC library: `scipy.stats.qmc`, Sobol' and Halton
do not appear anywhere in the repository.

| Script | Construction | Randomisation |
|---|---|---|
| `scripts/phase1b_simulator_comparison.py` (`radical_inverse`) | vdC base 2, indices `1..N` | **none**, deterministic |
| `scripts/phase10_sampling_baselines.py` (`van_der_corput`, `qmc_counts`) | vdC base 2, mapped through the exact inverse CDF of the finite law | random shift mod 1, one per trial |
| `scripts/phase15c_quadrature_sampling_baselines.py` (`van_der_corput_array`, `qmc_estimates_vectorized`) | vdC base 2, vectorised | same; 1000 trials, seed `20260812` |
| `scripts/phase16_angle_quadrature_scaling_decision.py` | imports the Phase 15c helpers | same |

**Housekeeping.** There are three independent copies of the same
radical-inverse routine, and the Phase 1b copy is not randomised while the
others are. They should be consolidated into `src/` before the report becomes
an article.

## Why the QMC column is so small

Reproduced with the repository's own code: `g1` gives `6.96e-05` against the
`7.0e-05` reported in Phase 15c, and the affine case `4.05e-05` against
`4.0e-05`. The construction is confirmed.

For `N` a power of two, the base-2 van der Corput point set is essentially a
**shifted regular lattice**. The error of a randomly shifted lattice rule is
then governed by the Fourier coefficients of the **periodic extension** of the
integrand, not by its smoothness on `[0,1]`:

- `g2 = sin^2(pi x) = 1/2 - cos(2 pi x)/2` has period 1, so the lattice
  annihilates every mode except multiples of `N` and integrates it **exactly**.
  The `<1e-06` entries in the Phase 15c table are zero *by symmetry*.
- the bump `x (1-x)^4` vanishes at both endpoints, so its periodic extension is
  continuous and the error decays as `O(N^-2)`, giving the `~1e-08` figures.
- `g1` and the affine case have a jump at the wrap-around, error `O(N^-1)`,
  hence their `1e-05`.

## Consequence

The `QPU/QMC` ratios in the Phase 15c table (1033 for `g2`, 104196 for the
bump) measure an alignment artefact between the test functions and the dyadic
structure of the point set. They are not a performance comparison and should
not be reported as one.

A defensible QMC comparison in the article needs either integrands whose
periodic extension is not commensurate with the lattice, or a scrambled digital
net (Owen scrambling), where the randomisation breaks the alignment and the
reported RMSE is an honest error estimate.

This is the classical mirror image of the structural effect documented in
`docs/angle_degree_refinement_findings.md`: on both sides of the benchmark,
what decides the outcome is arithmetic alignment with the discretisation, not
regularity.
