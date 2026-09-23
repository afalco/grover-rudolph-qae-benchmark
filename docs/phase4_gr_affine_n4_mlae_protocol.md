# Phase 4: Minimal MLAE for `gr_affine_n4`

## Objective

Run the first amplified Grover-Rudolph test only after the 16-point distribution
loader has been measured and improved by mitigation.

The marked event is:

```text
x >= 1/2
```

For the dyadic encoding this is the upper half of the 16-point grid.

## Configuration

```text
experiments/phase4_gr_affine_n4_mlae_k01_ibm_fez.json
```

Settings:

- backend: `ibm_fez`;
- shots: 2048 per circuit;
- implementation: `ucry`;
- schedule: `K={0,1}`;
- Runtime options: dynamical decoupling with `XY4`, gate twirling, and
  measurement twirling.

## Commands

Prepare without submitting:

```bash
python3 scripts/submit_gr_mlae_campaign.py \
  --campaign experiments/phase4_gr_affine_n4_mlae_k01_ibm_fez.json
```

Submit after reviewing the prepared circuits:

```bash
python3 scripts/submit_gr_mlae_campaign.py \
  --campaign experiments/phase4_gr_affine_n4_mlae_k01_ibm_fez.json \
  --submit
```

Fetch:

```bash
python3 scripts/fetch_gr_mlae_results.py \
  --campaign-run results/hardware/phase4_gr_affine_n4_mlae_k01_ibm_fez/<RUN_ID> \
  --wait
```

## Stopping Rule

Do not submit `k=2` until `k=0,1` are retrieved and the MLAE estimate is
reviewed. If `k=1` is unstable, run a dedicated layout/seed audit before adding
amplification depth.
