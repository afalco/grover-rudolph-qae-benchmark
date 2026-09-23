# Phase 1b Simulator Comparison Protocol

## Purpose

Phase 1b directly addresses the IBM recommendation to compare QAE variants.
This phase is simulator-first and does not submit QPU jobs.

The comparison covers:

- MLAE with fixed schedules;
- IQAE-style adaptive likelihood estimation;
- canonical phase-estimation QAE on the ideal grid;
- Monte Carlo;
- quasi-Monte Carlo.

## Scope

The first implementation uses the three Phase 1 calibration cases:

| Case | Exact amplitude |
|---|---:|
| `g0` | 0.25 |
| `g1` | 0.50 |
| `g2` | 0.50 |

The quantum estimators use the ideal binomial model

```text
p_k(a) = sin^2((2k+1) asin(sqrt(a))).
```

This is intentionally a statistical simulator rather than a circuit-level Aer
workflow. It isolates estimator behavior, query cost, shot allocation, and
degeneracy before any hardware decision.

## Run

```bash
python3 scripts/phase1b_simulator_comparison.py \
  --config experiments/phase1b_simulator_comparison.json
```

## Outputs

The script writes:

```text
results/phase1b/phase1b_simulator_comparison_<timestamp>.json
results/phase1b/phase1b_simulator_comparison_<timestamp>.csv
results/phase1b/phase1b_simulator_summary_<timestamp>.csv
```

## Metrics

For each method and case, the summary reports:

- mean absolute error;
- root mean squared error;
- empirical 95% interval from repetitions;
- total shots;
- oracle query count proxy;
- maximum amplification level;
- degeneracy flags where relevant.

## Decision Rule

MLAE remains the main hardware workflow unless IQAE-style adaptation clearly
reduces error at comparable query and shot cost. Canonical phase-estimation QAE
is treated as simulator-only unless the implied controlled-depth requirements
are small enough to pass Phase 0 auditing.

## First Simulator Readout

The first Phase 1b run was written to:

```text
results/phase1b/phase1b_simulator_comparison_20260805T100038Z.json
results/phase1b/phase1b_simulator_comparison_20260805T100038Z.csv
results/phase1b/phase1b_simulator_summary_20260805T100038Z.csv
```

Quantum estimator summary:

| Case | Method | Variant | MAE | RMSE | Mean queries | Mean shots |
|---|---|---|---:|---:|---:|---:|
| `g0` | IQAE proxy | steps=3 | 0.000764 | 0.000950 | 30597 | 6144 |
| `g0` | MLAE | K=0,1,2 | 0.001238 | 0.001556 | 18432 | 6144 |
| `g0` | MLAE | K=0,1 | 0.001440 | 0.001794 | 8192 | 4096 |
| `g0` | MLAE | K=0,2 | 0.001523 | 0.001846 | 12288 | 4096 |
| `g1` | IQAE proxy | steps=3 | 0.000634 | 0.000802 | 38912 | 6144 |
| `g1` | MLAE | K=0,1,2 | 0.001511 | 0.001931 | 18432 | 6144 |
| `g2` | IQAE proxy | steps=3 | 0.000748 | 0.000935 | 38912 | 6144 |
| `g2` | MLAE | K=0,1,2 | 0.001459 | 0.001815 | 18432 | 6144 |

Interpretation:

- IQAE-style adaptation reduces statistical error in this ideal model, but uses
  roughly twice the oracle-query budget of the fixed `K=0,1,2` MLAE schedule.
- MLAE `K=0,1,2` remains the best hardware-realistic default.
- Canonical QAE is degenerate for `g1` and `g2` because `a=0.5` lies exactly on
  the phase grid for the tested evaluation registers.
- MC/QMC are also degenerate for these small four-point calibration functions:
  QMC samples full periods exactly, and `g0` is constant.

Decision: Phase 1b should next add non-special amplitudes or larger structured
Grover--Rudolph instances before drawing strong conclusions about canonical QAE,
MC, or QMC. For the current Phase 1 calibration family, MLAE remains the main
hardware workflow.

## Non-Degenerate Extensions

Two follow-up configurations remove the artificial degeneracies of the first
calibration family.

Synthetic non-special amplitudes:

```bash
python3 scripts/phase1b_simulator_comparison.py \
  --config experiments/phase1b_nondegenerate_amplitudes.json
```

Larger Grover--Rudolph-style midpoint grids:

```bash
python3 scripts/phase1b_simulator_comparison.py \
  --config experiments/phase1b_gr_larger_grids.json
```

These runs should be used to compare canonical QAE, MC, and QMC more fairly.

## Non-Degenerate Readout

Synthetic amplitudes were run with:

```text
results/phase1b/phase1b_simulator_summary_20260805T100851Z.csv
```

Larger midpoint-grid functions were run with:

```text
results/phase1b/phase1b_simulator_summary_20260805T100933Z.csv
```

Representative quantum-estimator results:

| Case | Method | Variant | MAE | Queries |
|---|---|---|---:|---:|
| `a17` | IQAE proxy | steps=3 | 0.000553 | 38912 |
| `a17` | MLAE | K=0,1,2 | 0.001067 | 18432 |
| `a33` | IQAE proxy | steps=3 | 0.000692 | 38912 |
| `a33` | MLAE | K=0,1,2 | 0.001357 | 18432 |
| `a61` | IQAE proxy | steps=3 | 0.000703 | 36823 |
| `a61` | MLAE | K=0,1,2 | 0.001446 | 18432 |
| `gr_affine_n4` | IQAE proxy | steps=3 | 0.000867 | 33710 |
| `gr_affine_n4` | MLAE | K=0,1,2 | 0.001443 | 18432 |
| `gr_beta_bump_n4` | IQAE proxy | steps=3 | 0.000512 | 38359 |
| `gr_beta_bump_n4` | MLAE | K=0,1,2 | 0.001035 | 18432 |

The non-degenerate extensions confirm the first Phase 1b conclusion: IQAE-style
adaptation improves statistical accuracy in the ideal model, but MLAE gives a
better hardware-facing tradeoff because it avoids the much larger query budget
and adaptive execution overhead.
