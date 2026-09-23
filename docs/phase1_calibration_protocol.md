# Phase 1 Calibration Campaign

## Backend

The first hardware calibration campaign will use `ibm_fez`.

Rationale:

- it had the lowest average two-qubit gate error and readout error among the
  initially visible Heron backends;
- Phase 0 showed that all `g0/g1/g2`, `k=0,1,2` circuits are below the current
  depth and two-qubit-gate thresholds;
- the first hardware campaign should use one backend only, so that differences
  between amplification levels are not confounded with backend calibration
  differences.

## Selected Jobs

The initial campaign should submit only six circuits:

| Case | Schedule | Purpose |
|---|---:|---|
| `g0` | `k=0,2` | amplitude calibration and degeneracy-avoiding MLAE |
| `g1` | `k=0,1` | affine encoding continuity benchmark |
| `g2` | `k=0,1` | quadratic encoding continuity benchmark |

Use `2048` shots per circuit and `optimization_level=0` to stay consistent with
the previous SISC experiments and Phase 0 transpilation.

## Deferred Jobs

Do not submit these in the first hardware batch:

| Case | Schedule | Reason |
|---|---:|---|
| `g0` | `k=1` | known degeneracy at \(a=1/4\), useful only if explicitly documenting the failure mode |
| `g1` | `k=2` | useful as a later depth-stress/repetition circuit |
| `g2` | `k=2` | most expensive calibration circuit; defer until `k=0,1` are analyzed |

## Before Submission

Before spending credits:

1. Re-run backend listing:

   ```bash
   python3 scripts/list_ibm_backends.py --min-qubits 100
   ```

2. Re-run Phase 0 for `ibm_fez` if calibration or status has changed:

   ```bash
   python3 scripts/phase0_audit.py --backend ibm_fez
   ```

3. Confirm Runtime remaining:

   ```bash
   python3 scripts/check_ibm_runtime_usage.py
   ```

4. Prepare the campaign without submitting:

   ```bash
   python3 scripts/submit_campaign.py \
     --campaign experiments/phase1_calibration_ibm_fez.json
   ```

   This builds and transpiles the six selected circuits and writes metadata under
   `results/hardware/`, but does not submit QPU jobs.

## Submission

Only submit after the preparation output has been reviewed:

```bash
python3 scripts/submit_campaign.py \
  --campaign experiments/phase1_calibration_ibm_fez.json \
  --submit
```

The script asks for an explicit `SUBMIT` confirmation before sending jobs. To skip
the interactive prompt in a controlled environment, add `--yes`.

## Fetching Results

To check job status and fetch any completed results from the latest submitted
`phase1_calibration_ibm_fez` run:

```bash
python3 scripts/fetch_campaign_results.py
```

To wait for unfinished jobs and poll every 30 seconds:

```bash
python3 scripts/fetch_campaign_results.py --wait
```

To fetch a specific campaign run:

```bash
python3 scripts/fetch_campaign_results.py \
  --campaign-run results/hardware/phase1_calibration_ibm_fez/<timestamp>
```

The script writes:

```text
fetched_campaign_results.json
fetched_campaign_results.csv
results/result_<case>_k<k>_<job_id>.json
```

It also computes a first MLAE estimate for every case whose selected schedule has
completed.

## Campaign Configuration

The machine-readable campaign file is:

```text
experiments/phase1_calibration_ibm_fez.json
```
