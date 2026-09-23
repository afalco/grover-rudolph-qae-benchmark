# Project Scope

## Primary Objective

Build a reproducible benchmark suite for Grover-Rudolph state preparation and
amplitude-estimation-based numerical integration on IBM Quantum hardware.

## Benchmark Axes

1. Algorithmic estimator:
   - MLAE
   - IQAE
   - standard QAE
   - Monte Carlo
   - quasi-Monte Carlo

2. Circuit and compilation cost:
   - logical depth
   - transpiled depth
   - two-qubit depth
   - two-qubit gate count
   - SWAP count
   - physical layout
   - transpilation runtime

3. Structural encoding cost:
   - multilinear support
   - maximum degree
   - minimum embedding stratum \(m_1\)
   - weighted canonical length \(L_{\mathrm{can}}\)

4. Hardware performance:
   - backend family
   - calibration metadata
   - shots
   - runtime consumed
   - estimator error
   - mitigation/postselection overhead

## First Milestone

Reproduce the existing \(g_0\), \(g_1\), and \(g_2\) circuits in a unified Qiskit
workflow and validate them in ideal and noisy simulation before any QPU run.

