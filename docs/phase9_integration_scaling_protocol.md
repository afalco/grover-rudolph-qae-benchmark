# Phase 9 Integration-Scaling Protocol

Phase 9 studies how algebraic integration scales when Grover-Rudolph prepares a
finite probability law on increasingly fine dyadic grids.

For a profile `F` on the midpoint grid

```text
x_j = (j + 1/2) / 2^n, j = 0, ..., 2^n - 1,
```

the finite probability law is

```text
p_j = F(x_j) / sum_l F(x_l).
```

The target quantities are classical functionals of this law:

```text
I_p(f) = sum_j f(x_j) p_j.
```

The audit separates three errors:

```text
discretization_error = | I_p(f) - integral f(x)F(x)dx / integral F(x)dx |
exact_loaded_error   = | I_loaded(f) - I_p(f) |
ideal_sample_error   = | I_ideal_shots(f) - I_p(f) |
noisy_sample_error   = | I_backend_noise(f) - I_p(f) |
```

The first error is classical and depends on the grid. The second checks the
logical Grover-Rudolph construction. The third is finite-shot sampling error.
The fourth is the backend-noise prediction and is the main credit-free filter
before submitting a QPU campaign.

Run locally without backend access:

```bash
python3 scripts/phase9_integration_scaling_audit.py \
  --config experiments/phase9_integration_scaling_audit.json \
  --skip-transpile
```

Run with a backend-aware noisy model:

```bash
python3 scripts/phase9_integration_scaling_audit.py \
  --config experiments/phase9_integration_scaling_audit.json \
  --backend ibm_kingston
```

The backend-aware run writes a candidate table ordered by circuit cost and
predicted quantity-of-interest error. A case is a hardware candidate only if it
passes the depth, two-qubit-gate, noisy-TVD, and maximum quantity-error
thresholds in the configuration file.
