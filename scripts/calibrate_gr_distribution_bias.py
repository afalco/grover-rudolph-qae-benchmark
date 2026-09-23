#!/usr/bin/env python3
"""Classical bias calibration for measured Grover-Rudolph distributions.

This is a diagnostic postprocessing tool. It compares the observed distribution
with the known target distribution and evaluates shrinkage corrections

    p_corrected = (1 - lambda) p_observed + lambda p_target

for observable estimates. This intentionally reports the shrinkage parameter,
because using the target distribution itself is not a blind production
correction; it is a controlled way to quantify residual distribution bias.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from grover_rudolph_qae_benchmark.gr_state_preparation import midpoint_grid  # noqa: E402


DEFAULT_RUN = (
    PROJECT_ROOT
    / "results/hardware/phase3_gr_affine_n4_mitigation_ibm_fez"
    / "20260805T112409Z/fetched_gr_distribution_results.json"
)


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def load_rows(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8")).get("rows", [])


def observable_values(name: str, grid_points: int) -> list[float]:
    xs = midpoint_grid(grid_points)
    if name == "upper_half":
        return [1.0 if index >= grid_points // 2 else 0.0 for index in range(grid_points)]
    if name == "mean_x":
        return xs
    if name == "second_moment_x2":
        return [x * x for x in xs]
    if name == "sin_pi_x":
        return [math.sin(math.pi * x) for x in xs]
    if name == "call_x_minus_half":
        return [max(x - 0.5, 0.0) for x in xs]
    raise ValueError(f"Unknown observable {name!r}")


def expectation(probabilities: list[float], values: list[float]) -> float:
    return sum(probability * value for probability, value in zip(probabilities, values))


def tvd(p: list[float], q: list[float]) -> float:
    return 0.5 * sum(abs(a - b) for a, b in zip(p, q))


def l2_distance(p: list[float], q: list[float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(p, q)))


def corrected_distribution(observed: list[float], target: list[float], shrinkage: float) -> list[float]:
    corrected = [
        (1.0 - shrinkage) * observed_value + shrinkage * target_value
        for observed_value, target_value in zip(observed, target)
    ]
    total = sum(corrected)
    if total > 0:
        corrected = [value / total for value in corrected]
    return corrected


def signed_cell_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    observed = [float(value) for value in row["observed_distribution"]]
    target = [float(value) for value in row["target_probabilities"]]
    grid_points = int(row["grid_points"])
    xs = midpoint_grid(grid_points)
    out = []
    for index, (obs, tgt) in enumerate(zip(observed, target)):
        out.append(
            {
                "case_id": row["case_id"],
                "index": index,
                "x_midpoint": xs[index],
                "observed": obs,
                "target": tgt,
                "signed_bias": obs - tgt,
                "abs_bias": abs(obs - tgt),
                "relative_bias": (obs - tgt) / tgt if tgt else None,
            }
        )
    return out


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    keys = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, sort_keys=True)
                    if isinstance(value, (dict, list))
                    else value
                    for key, value in row.items()
                }
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate shrinkage calibration of GR distribution bias."
    )
    parser.add_argument("--run", default=str(DEFAULT_RUN))
    parser.add_argument("--case-id", default="gr_affine_n4")
    parser.add_argument(
        "--observables",
        nargs="+",
        default=[
            "upper_half",
            "mean_x",
            "second_moment_x2",
            "sin_pi_x",
            "call_x_minus_half",
        ],
    )
    parser.add_argument(
        "--shrinkage-grid",
        type=float,
        nargs="+",
        default=[0.0, 0.25, 0.5, 0.75, 1.0],
    )
    parser.add_argument("--out-dir", default="results/phase4/direct_estimator")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_path = resolve_path(args.run)
    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    selected = None
    for row in load_rows(run_path):
        if row.get("case_id") == args.case_id:
            selected = row
            break
    if selected is None:
        raise SystemExit(f"Case {args.case_id!r} not found in {run_path}")

    observed = [float(value) for value in selected["observed_distribution"]]
    target = [float(value) for value in selected["target_probabilities"]]
    grid_points = int(selected["grid_points"])

    rows_out = []
    for shrinkage in args.shrinkage_grid:
        corrected = corrected_distribution(observed, target, float(shrinkage))
        for observable in args.observables:
            values = observable_values(observable, grid_points)
            estimate = expectation(corrected, values)
            exact = expectation(target, values)
            raw = expectation(observed, values)
            rows_out.append(
                {
                    "source": str(run_path),
                    "campaign_id": selected.get("campaign_id"),
                    "case_id": selected.get("case_id"),
                    "observable": observable,
                    "shrinkage_to_target": float(shrinkage),
                    "estimate": estimate,
                    "exact": exact,
                    "raw_estimate": raw,
                    "raw_abs_error": abs(raw - exact),
                    "abs_error": abs(estimate - exact),
                    "error_reduction": abs(raw - exact) - abs(estimate - exact),
                    "corrected_tvd_to_target": tvd(corrected, target),
                    "corrected_l2_to_target": l2_distance(corrected, target),
                    "raw_tvd_to_target": tvd(observed, target),
                    "raw_l2_to_target": l2_distance(observed, target),
                    "depth": selected.get("transpiled_metrics", {}).get("depth"),
                    "twoq": selected.get("transpiled_metrics", {}).get("two_qubit_gate_count"),
                    "shots": int(selected["shots"]),
                }
            )

    cell_rows = signed_cell_rows(selected)
    rows_out.sort(
        key=lambda row: (
            str(row["observable"]),
            float(row["shrinkage_to_target"]),
        )
    )
    cell_rows.sort(key=lambda row: float(row["abs_bias"]), reverse=True)

    csv_path = out_dir / "gr_distribution_bias_calibration.csv"
    json_path = out_dir / "gr_distribution_bias_calibration.json"
    cells_path = out_dir / "gr_distribution_cell_bias.csv"
    write_csv(rows_out, csv_path)
    write_csv(cell_rows, cells_path)
    json_path.write_text(
        json.dumps(
            {
                "source": str(run_path),
                "case_id": args.case_id,
                "diagnostic_note": (
                    "Shrinkage-to-target uses the known target distribution and is a "
                    "diagnostic bias quantification, not a blind production correction."
                ),
                "observable_rows": rows_out,
                "cell_bias_rows": cell_rows,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    print(f"Wrote JSON: {json_path}")
    print(f"Wrote CSV:  {csv_path}")
    print(f"Wrote cells:{cells_path}")
    print()
    print("Largest cell biases:")
    for row in cell_rows[:6]:
        print(
            f"  i={row['index']:>2} x={row['x_midpoint']:.4f} "
            f"obs={row['observed']:.6f} target={row['target']:.6f} "
            f"bias={row['signed_bias']:+.6f}"
        )
    print()
    print("Shrinkage calibration summary:")
    for row in rows_out:
        if row["shrinkage_to_target"] in (0.0, 0.5, 1.0):
            print(
                f"  {row['observable']:<18} lambda={row['shrinkage_to_target']:.2f} "
                f"estimate={row['estimate']:.6f} exact={row['exact']:.6f} "
                f"err={row['abs_error']:.6f} tvd={row['corrected_tvd_to_target']:.6f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
