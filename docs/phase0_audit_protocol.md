# Phase 0 Audit Protocol

## Purpose

Phase 0 is a no-QPU audit. Its purpose is to build the calibration circuits,
simulate them locally when possible, transpile them against a selected backend,
and produce a candidate table before spending IBM Quantum Runtime credits.

The initial calibration cases are:

- `g0`: constant amplitude \(a=1/4\), degree 0;
- `g1`: affine encoding for \(\sin^2(\pi x/2)\), degree 1;
- `g2`: quadratic encoding for \(\sin^2(\pi x)\), degree 2.

These cases are for calibration and continuity with the previous SISC paper. They
are not the final utility-scale benchmark.

## Install

From the repository root:

```bash
python3 -m pip install -e '.[simulation]'
```

If simulation support is not needed:

```bash
python3 -m pip install -e .
```

## Local Audit

Build circuits and run ideal simulation if `qiskit-aer` is installed:

```bash
python3 scripts/phase0_audit.py --skip-transpile
```

This writes:

```text
results/phase0/phase0_candidate_table_<timestamp>.json
results/phase0/phase0_candidate_table_<timestamp>.csv
results/phase0/circuits/
```

## Backend Transpilation Audit

Transpile against a selected IBM backend and run noisy simulation if
`qiskit-aer` is installed:

```bash
python3 scripts/phase0_audit.py --backend <backend_name>
```

Example:

```bash
python3 scripts/phase0_audit.py --backend ibm_kingston
```

The backend name should be replaced by an available backend in the active IBM
Quantum instance.

## Thresholds

By default, a circuit is marked as a candidate if:

- transpiled depth is at most `5000`;
- transpiled two-qubit gate count is at most `10000`.

Override these thresholds with:

```bash
python3 scripts/phase0_audit.py \
  --backend <backend_name> \
  --max-depth 3000 \
  --max-two-qubit-gates 6000
```

## Recorded Metrics

For each `(case_id, k)` pair, the audit records:

- logical circuit depth, width, size, and operation counts;
- transpiled circuit depth, width, size, operation counts, and two-qubit depth;
- construction and transpilation time;
- expected ideal amplified probability \(p_k(a)\);
- ideal and noisy simulated ancilla probabilities when available;
- structural metrics:
  - multilinear support;
  - maximum degree;
  - minimum embedding stratum \(m_1\);
  - weighted canonical length \(L_{\mathrm{can}}\).

## Important Constraint

This script does not submit jobs to IBM Quantum hardware. It only uses local
simulation and backend metadata/transpilation.

