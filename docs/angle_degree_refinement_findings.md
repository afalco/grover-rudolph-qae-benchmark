# Angle-Structure Degree Under Grid Refinement

Credit-free structural audit for Workflow A. No QPU time was spent.

Script:

```text
scripts/audit_angle_degree_refinement.py --probe-stability
```

Results:

```text
results/angle_degree_refinement/angle_degree_refinement.{json,csv,md}
```

Grid: midpoint. Coefficient tolerance: `1e-10`. Degrees computed with the
existing `src/grover_rudolph_qae_benchmark/angle_structure.py` classifier, so
this audit uses exactly the same Moebius/Walsh--Hadamard machinery as
`scripts/classify_angle_structure.py` and Phase 16.

## Motivation

Phase 16 states, without numbers, that "g2 remains in G^(2)_n as n grows,
whereas the shifted affine and smooth bump stress cases move to higher or full
angle degree on refined grids", and concludes that "regularity alone is not the
selection criterion". This audit quantifies that sentence and turns it into a
reproducible table, because the consequence is stronger than the sentence
suggests.

## Result 1: the benchmark family is stable, the smooth stress cases are not

Multilinear degree `d` of `Theta_g = 2 asin(sqrt(g))`, and the canonical
encoding cost `binom(n, <= d)` against the generic cost `2^n`.

| case                   | n=2 | n=3 | n=4 | n=5 | n=6 | n=7 | n=8 | n=9 | gates at n=9 |
|------------------------|-----|-----|-----|-----|-----|-----|-----|-----|--------------|
| `g0_constant_quarter`  |   0 |   0 |   0 |   0 |   0 |   0 |   0 |   0 |    1 / 512   |
| `g1_sin2_half`         |   1 |   1 |   1 |   1 |   1 |   1 |   1 |   1 |   10 / 512   |
| `g2_sin2`              |   2 |   2 |   2 |   2 |   2 |   2 |   2 |   2 |   46 / 512   |
| `affine_shifted`       |   2 |   3 |   4 |   5 |   6 |   7 |   8 |   9 |  512 / 512   |
| `beta_bump`            |   2 |   3 |   4 |   5 |   6 |   7 |   8 |   9 |  512 / 512   |
| `oscillatory_shifted`  |   1 |   3 |   3 |   5 |   5 |   7 |   7 |   9 |  512 / 512   |

The straight line `0.11 + 0.57 x` is the smoothest non-constant integrand in
the benchmark and is the *most expensive* to encode: full degree `d = n`, the
generic `2^n` gates, no saving whatsoever over an arbitrary state preparation.
The oscillatory `g2 = sin^2(pi x)` costs 46 gates out of 512 at `n = 9`.

Smoothness is therefore not merely "not the criterion"; on this family it runs
in the opposite direction.

## Result 2: the low-degree class is not robust

| probe                  | n=2 | n=3 | n=4 | n=5 | n=6 | n=7 | n=8 | n=9 | gates at n=9 |
|------------------------|-----|-----|-----|-----|-----|-----|-----|-----|--------------|
| `g1_sin2_half`         |   1 |   1 |   1 |   1 |   1 |   1 |   1 |   1 |   10 / 512   |
| `sin2_affine_a`        |   1 |   1 |   1 |   1 |   1 |   1 |   1 |   1 |   10 / 512   |
| `sin2_affine_b`        |   1 |   1 |   1 |   1 |   1 |   1 |   1 |   1 |   10 / 512   |
| `g1_scaled_half`       |   2 |   3 |   4 |   5 |   6 |   7 |   8 |   9 |  512 / 512   |
| `g1_offset_001`        |   1 |   3 |   3 |   5 |   5 |   7 |   7 |   9 |  512 / 512   |
| `g2_scaled_half`       |   2 |   2 |   4 |   4 |   6 |   6 |   8 |   8 |  511 / 512   |

`g1_scaled_half` is `g1 / 2` and `g1_offset_001` is `0.98 g1 + 0.01`. Both are
graphically indistinguishable from `g1`, share its smoothness, monotonicity and
range, and both move from 10 gates to the generic 512. The same happens to
`g2` under rescaling. Membership in a low-degree class survives neither
rescaling nor an arbitrarily small perturbation.

## Why: the exact characterisation of the stable affine class

On the uniform dyadic grid, `x` is affine in the bits. Requiring `Theta_g` to
be affine in the bits *for every n* is therefore equivalent to requiring
`Theta_g` to be affine in `x`, that is

```text
2 asin(sqrt(g(x))) = alpha + beta x   <=>   g(x) = sin^2(alpha/2 + beta x/2).
```

So the set of integrands that stay in `G_n^(1)` under unbounded refinement is
exactly the two-parameter family `sin^2` of an affine map, and nothing else.
The `sin2_affine_a` and `sin2_affine_b` rows confirm this numerically for
arbitrary parameters; `g1` is the member with `alpha = 0`, `beta = pi`.

For a *fixed* `n` the class is far larger: only the `2^n` angle values have to
fit an affine form, which leaves the interpolation between grid points
completely free. That is precisely the freedom used by the Sobolev decoupling
lemma of Chinesta, Falco and Falco-Pomares (arXiv:2604.24289), which builds
integrands of arbitrarily low Sobolev regularity inside `G_n^(1)`.

The two statements are consistent and, taken together, sharper than either one
alone: the angle degree is not an analytic invariant in any direction. It is an
arithmetic alignment condition between the integrand and the dyadic structure
of the grid.

## Consequences for the project

1. **Reporting.** Claims of the form "low-degree integrands are cheap to
   encode" should always be qualified by the grid. `g in G_n^(d)` is a property
   of the pair (integrand, discretisation), not of the integrand.

2. **Why the campaign behaved as it did.** The two Phase 15b stress cases that
   failed to beat Monte Carlo, `affine_shifted` and the smooth bump, are
   exactly the two that reach full degree under refinement. The two that beat
   it, `g1` and `g2`, are the two whose degree is stable. This was not luck,
   and it is a stronger statement than the Phase 16 rule, which only screens at
   the `n` actually submitted.

3. **Open Problem (iii) is the urgent one.** Approximation inside `G_n^(d)` is
   not a refinement of the theory; given this fragility it is what makes the
   theory usable at all. The natural object is the truncation of the
   multilinear expansion of `Theta_g` to degree `d`, with the induced
   integration error bounded by the tail `sum_{|S| > d} |Theta_S|`. Without it,
   the usable class is a measure-zero family.

4. **Same phenomenon on the classical side.** See
   `docs/qmc_baseline_note.md`: the quasi-Monte Carlo baseline is exact for
   `g2` for the same kind of reason, commensurability between the Fourier
   content of the integrand and the dyadic point set, not smoothness.

## Suggested use in the paper

The Result 1 table is a candidate figure for the SISC revision. It converts the
Sobolev decoupling lemma from a technical curiosity into the central structural
statement: the encoding degree is independent of regularity in *both*
directions, and the low-degree strata are rigid.
