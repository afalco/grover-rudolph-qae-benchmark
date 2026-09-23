# Reference Note: Mixed-State Preparation

Reference:

```text
Lucas Friedrich, Douglas F. Pinto, Diego S. Starke, and Jonas Maziero,
"Preparing general mixed quantum states on quantum computers",
Quantum Information Processing 25, 283 (2026).
DOI: 10.1007/s11128-026-05299-7
```

Local file:

```text
../References/s11128-026-05299-7.pdf
```

## Core Idea

The paper proposes a modular purification-based algorithm for preparing an
arbitrary mixed state

\[
\rho=\sum_{j=0}^{d-1} r_j |r_j\rangle\langle r_j|,
\qquad d=2^n.
\]

The preparation uses `2n` qubits and three layers:

1. **Eigenvalue encoding**: prepare
   \[
   \sum_j \sqrt{r_j}|j\rangle
   \]
   on the first register.
2. **Entropy injection**: apply `n` CNOTs from the first register to an auxiliary
   register, producing
   \[
   \sum_j \sqrt{r_j}|j\rangle|j\rangle.
   \]
3. **Eigenvector preparation**: apply a unitary on the first register that maps
   \(|j\rangle\) to the eigenvector \(|r_j\rangle\).

Tracing out the auxiliary register gives the target mixed state.

## Relation to This Project

The first layer is directly aligned with our Grover-Rudolph loader:

\[
A_p|0^n\rangle=\sum_i \sqrt{p_i}|i\rangle.
\]

Therefore, our measured Grover-Rudolph distributions can be interpreted as the
eigenvalue-encoding layer of a mixed-state preparation protocol. If we append
the entropy-injection CNOT layer and trace out or ignore the auxiliary register,
the reduced state of the first register should have diagonal law \(p\).

This is conceptually useful for Phase 6 because it connects our algebraic
probability language with a concrete purification experiment:

\[
\omega_p(\Pi_i)=p_i
\quad\Longleftrightarrow\quad
\operatorname{Tr}_{\mathrm{aux}}(|\Psi_p\rangle\langle\Psi_p|)
\text{ has spectrum } p
\]

when no nontrivial eigenvector unitary is applied.

## Experimental Ideas for Phase 6

### 1. Add Distribution Metrics Beyond TVD

The paper evaluates mixed-state preparation with fidelity and Frobenius
distance. For our diagonal probability laws, the analogous classical metrics are
cheap to compute from existing counts:

- total variation distance;
- classical fidelity / Bhattacharyya overlap
  \[
  F_{\mathrm{cl}}(p,\hat p)=\left(\sum_i \sqrt{p_i\hat p_i}\right)^2;
  \]
- Hellinger distance;
- Euclidean or Frobenius-like distance
  \[
  \|p-\hat p\|_2.
  \]

This should be added to Phase 6 postprocessing. It may separate localized
profile failures more clearly than TVD alone.

### 2. Add Repetition Statistics

The paper reports repeated state preparations and summarizes averages and
standard deviations. Our hardware campaigns have mostly used one execution per
compiled circuit. Phase 6 should compute uncertainty by:

- bootstrap resampling existing counts;
- comparing repeated runs when they exist;
- reporting observable-specific confidence intervals, not only global
  distribution distances.

This is particularly relevant for `gr_beta_bump_n4`, where TVD and individual
observable errors moved in different directions.

### 3. Optional Purification Diagnostic, Simulator First

A minimal experiment inspired by the paper is:

1. prepare a Grover-Rudolph distribution \(p\) on an `n`-qubit register;
2. add `n` auxiliary qubits;
3. copy the computational-basis index using `n` CNOTs;
4. measure both registers.

For \(n=3\), this uses 6 qubits plus 3 extra CNOTs after state preparation. For
\(n=4\), it uses 8 qubits plus 4 extra CNOTs. The experiment does not require
the expensive arbitrary eigenvector unitary.

The useful diagnostics would be:

- marginal distribution on the first register;
- marginal distribution on the auxiliary register;
- mismatch probability between the two registers;
- mutual information or classical correlation between registers.

This should be audited locally before any QPU submission. It is not necessary
for the current direct-observable workflow, but it could become a strong
methodological bridge between Grover-Rudolph loading and algebraic probability.

### 4. Do Not Implement General Mixed-State Preparation Yet

The full algorithm requires arbitrary eigenvector preparation. The paper itself
identifies this as the dominant cost for general states. This is not a good
near-term QPU target for our IBM credit budget. It should remain a conceptual
reference unless we restrict to a very structured eigenbasis, for example:

- computational basis only;
- Bell basis for two-qubit demonstrations;
- a shallow, problem-specific eigenvector transform.

## Recommendation

For Phase 6, incorporate the reference through postprocessing and diagnostics,
not through a new hardware-heavy mixed-state campaign.

Concrete next actions:

1. add classical fidelity, Hellinger distance, and L2/Frobenius-like distance to
   the distribution analysis scripts;
2. add bootstrap confidence intervals for distribution metrics and observables;
3. design a simulator-only purification diagnostic for `gr_sin2_n3` and
   `gr_sin2_half_n3`;
4. keep amplified QAE and full mixed-state preparation deferred until the direct
   distribution diagnostics are stable.
