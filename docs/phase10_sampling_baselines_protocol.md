# Phase 10 Sampling Baselines Protocol

Phase 10 is credit-free. It compares the already measured QPU distributions
with classical sampling baselines under the same finite probability law.

For a Grover-Rudolph law \(p=(p_j)_{j=0}^{N-1}\) on the midpoint grid
\(x_j=(j+1/2)/N\), each test function \(f\) defines

\[
I_p(f)=\sum_{j=0}^{N-1} f(x_j)p_j .
\]

The hardware estimator uses the empirical distribution \(\hat p_{\rm QPU}\)
obtained from measured counts:

\[
I_{\hat p_{\rm QPU}}(f)=\sum_j f(x_j)\hat p_{{\rm QPU},j}.
\]

The MC baseline draws the same number of samples \(M\) from the exact finite
law \(p\), builds the empirical distribution \(\hat p_{\rm MC}\), and evaluates
\(I_{\hat p_{\rm MC}}(f)\). The QMC baseline uses randomized shifted
van-der-Corput points in one dimension and the inverse CDF of the same finite
law. This is a strong finite-law baseline: it assumes the exact discrete CDF is
available classically. It should therefore be interpreted as a reference for
sampling quality, not as a full accounting of the cost of constructing the law.

Run the default Phase 10 comparison:

```bash
python3 scripts/phase10_sampling_baselines.py --trials 1000
```

The default inputs are the two Phase 9 32-cell hardware runs:

```text
results/hardware/phase9_affine_n5_distribution_ibm_kingston/20260808T195550Z/fetched_gr_distribution_results.json
results/hardware/phase9_beta_bump_n5_distribution_ibm_kingston/20260808T202355Z/fetched_gr_distribution_results.json
```

Outputs are written under:

```text
results/phase10/sampling_baselines/
```

The main files are:

```text
phase10_sampling_distribution_metrics.{json,csv}
phase10_sampling_qoi_metrics.{json,csv}
phase10_sampling_summary.md
```

The distribution table reports QPU TVD, Hellinger distance, classical fidelity,
L2 distance, and maximum cell deviation against MC/QMC sampling distributions
at the same number of samples. The quantity table reports QPU absolute error,
MC/QMC MAE and RMSE, and percentile ranks showing where the QPU error lies
inside the classical sampling distribution.
