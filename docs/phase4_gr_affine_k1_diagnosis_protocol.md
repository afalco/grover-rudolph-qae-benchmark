# Phase 4b: `gr_affine_n4 k=1` Diagnosis

## Objective

Diagnose the first amplified Grover-Rudolph circuit before any repeat is
submitted. The Phase 4 hardware readout was:

```text
gr_affine_n4 k=1: observed p = 0.173828, expected p = 0.052765
```

The deviation is too large to submit `k=2`.

## Command

```bash
python3 scripts/diagnose_gr_affine_k1.py \
  --config experiments/phase4_gr_affine_n4_k1_diagnosis_ibm_fez.json
```

The script does not submit QPU jobs. It performs:

- exact statevector validation of the logical circuit;
- exact statevector validation after transpilation;
- noisy simulation using an Aer model extracted from `ibm_fez`;
- a sweep over optimization levels and transpiler seeds;
- ranking by noisy deviation, two-qubit count, and depth.

## Decision Rule

Repeat `k=1` only if the diagnosis identifies a candidate with lower depth or
two-qubit count and a noisy prediction close to the expected probability. If the
noise model remains far below the observed hardware deviation, treat the issue
as amplified hardware sensitivity not captured by the backend model and do not
submit `k=2`.
