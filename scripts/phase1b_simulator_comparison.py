#!/usr/bin/env python3
"""Phase 1b statistical simulator: MLAE vs IQAE proxy vs QAE vs MC/QMC."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from grover_rudolph_qae_benchmark.benchmark_specs import BENCHMARK_SPECS
from grover_rudolph_qae_benchmark.circuits import expected_amplified_probability


DEFAULT_CONFIG = PROJECT_ROOT / "experiments" / "phase1b_simulator_comparison.json"


GRID_VALUES = {
    "g0": [0.25, 0.25, 0.25, 0.25],
    "g1": [
        math.sin(math.pi * (1 / 8) / 2) ** 2,
        math.sin(math.pi * (3 / 8) / 2) ** 2,
        math.sin(math.pi * (5 / 8) / 2) ** 2,
        math.sin(math.pi * (7 / 8) / 2) ** 2,
    ],
    "g2": [
        math.sin(math.pi * (1 / 8)) ** 2,
        math.sin(math.pi * (3 / 8)) ** 2,
        math.sin(math.pi * (5 / 8)) ** 2,
        math.sin(math.pi * (7 / 8)) ** 2,
    ],
}


def function_values(kind: str, grid_points: int, **params: Any) -> list[float]:
    values = []
    for idx in range(grid_points):
        x = (idx + 0.5) / grid_points
        if kind == "constant":
            value = float(params["value"])
        elif kind == "sin2_half":
            value = math.sin(math.pi * x / 2) ** 2
        elif kind == "sin2":
            value = math.sin(math.pi * x) ** 2
        elif kind == "affine":
            value = float(params.get("offset", 0.0)) + float(params.get("slope", 1.0)) * x
        elif kind == "beta_bump":
            alpha = float(params.get("alpha", 2.0))
            beta = float(params.get("beta", 5.0))
            raw = (x ** (alpha - 1)) * ((1 - x) ** (beta - 1))
            scale = float(params.get("scale", 1.0))
            value = scale * raw
        else:
            raise ValueError(f"Unknown function kind: {kind}")
        values.append(min(max(value, 0.0), 1.0))
    return values


def resolve_cases(config: dict[str, Any]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for case in config.get("custom_cases", []):
        case_id = str(case["case_id"])
        if "grid_values" in case:
            values = [float(value) for value in case["grid_values"]]
            a_exact = float(case.get("a_exact", sum(values) / len(values)))
        elif "function" in case:
            function = dict(case["function"])
            kind = function.pop("kind")
            grid_points = int(case.get("grid_points", 4))
            values = function_values(kind, grid_points, **function)
            a_exact = float(case.get("a_exact", sum(values) / len(values)))
        else:
            a_exact = float(case["a_exact"])
            values = None
        cases.append(
            {
                "case_id": case_id,
                "a_exact": a_exact,
                "grid_values": values,
                "notes": case.get("notes", ""),
            }
        )

    for case_id in config.get("cases", ["g0", "g1", "g2"]):
        if case_id in BENCHMARK_SPECS:
            cases.append(
                {
                    "case_id": case_id,
                    "a_exact": float(BENCHMARK_SPECS[case_id].a_exact),
                    "grid_values": GRID_VALUES.get(case_id),
                    "notes": BENCHMARK_SPECS[case_id].notes,
                }
            )
        elif case_id in GRID_VALUES:
            values = GRID_VALUES[case_id]
            cases.append(
                {
                    "case_id": case_id,
                    "a_exact": sum(values) / len(values),
                    "grid_values": values,
                    "notes": "",
                }
            )
        else:
            raise ValueError(
                f"Unknown case {case_id!r}. Use BENCHMARK_SPECS or custom_cases."
            )
    return cases


def mle_estimate(observations: list[dict[str, int]], grid_size: int = 5001) -> float:
    eps = 1e-15

    def nll(a: float) -> float:
        total = 0.0
        for obs in observations:
            k = int(obs["k"])
            m = int(obs["successes"])
            shots = int(obs["shots"])
            p = min(max(expected_amplified_probability(a, k), eps), 1 - eps)
            total -= m * math.log(p) + (shots - m) * math.log(1 - p)
        return total

    lo = 1e-8
    hi = 1 - 1e-8
    step = (hi - lo) / (grid_size - 1)
    best_a = lo
    best_value = float("inf")
    for idx in range(grid_size):
        a = lo + idx * step
        value = nll(a)
        if value < best_value:
            best_a = a
            best_value = value

    left = max(lo, best_a - 5 * step)
    right = min(hi, best_a + 5 * step)
    for _ in range(80):
        m1 = left + (right - left) / 3
        m2 = right - (right - left) / 3
        if nll(m1) < nll(m2):
            right = m2
        else:
            left = m1
    return (left + right) / 2


def binomial_sample(rng: random.Random, shots: int, p: float) -> int:
    return sum(1 for _ in range(shots) if rng.random() < p)


def run_mlae(
    rng: random.Random,
    *,
    a_exact: float,
    schedule: list[int],
    shots_per_circuit: int,
    mle_grid_size: int,
) -> dict[str, Any]:
    observations = []
    for k in schedule:
        p = expected_amplified_probability(a_exact, k)
        observations.append(
            {
                "k": int(k),
                "shots": shots_per_circuit,
                "successes": binomial_sample(rng, shots_per_circuit, p),
            }
        )
    a_hat = mle_estimate(observations, grid_size=mle_grid_size)
    return {
        "a_hat": a_hat,
        "total_shots": shots_per_circuit * len(schedule),
        "oracle_queries": shots_per_circuit * sum(2 * k + 1 for k in schedule),
        "max_k": max(schedule),
        "schedule": schedule,
    }


def posterior_interval(
    observations: list[dict[str, int]],
    *,
    grid_size: int = 401,
    mass: float = 0.95,
) -> tuple[float, float, float]:
    eps = 1e-15
    values: list[tuple[float, float]] = []
    max_loglike = -float("inf")
    for idx in range(grid_size):
        a = (idx + 0.5) / grid_size
        loglike = 0.0
        for obs in observations:
            p = min(max(expected_amplified_probability(a, int(obs["k"])), eps), 1 - eps)
            m = int(obs["successes"])
            shots = int(obs["shots"])
            loglike += m * math.log(p) + (shots - m) * math.log(1 - p)
        values.append((a, loglike))
        max_loglike = max(max_loglike, loglike)

    weights = [(a, math.exp(loglike - max_loglike)) for a, loglike in values]
    norm = sum(weight for _, weight in weights)
    weights = [(a, weight / norm) for a, weight in weights]
    mean = sum(a * weight for a, weight in weights)

    sorted_weights = sorted(weights)
    tail = (1 - mass) / 2
    cdf = 0.0
    lo = sorted_weights[0][0]
    hi = sorted_weights[-1][0]
    for a, weight in sorted_weights:
        cdf += weight
        if cdf >= tail:
            lo = a
            break
    cdf = 0.0
    for a, weight in sorted_weights:
        cdf += weight
        if cdf >= 1 - tail:
            hi = a
            break
    return mean, lo, hi


def choose_iqae_k(
    observations: list[dict[str, int]],
    *,
    max_k: int,
    posterior_grid_size: int,
) -> int:
    if not observations:
        return 0
    mean, lo, hi = posterior_interval(
        observations, mass=0.80, grid_size=posterior_grid_size
    )
    candidates = list(range(max_k + 1))

    def score(k: int) -> tuple[float, int]:
        plo = expected_amplified_probability(lo, k)
        phi = expected_amplified_probability(hi, k)
        pmid = expected_amplified_probability(mean, k)
        contrast = max(abs(plo - phi), abs(pmid - plo), abs(pmid - phi))
        return (-contrast, 2 * k + 1)

    return sorted(candidates, key=score)[0]


def run_iqae_proxy(
    rng: random.Random,
    *,
    a_exact: float,
    max_k: int,
    steps: int,
    shots_per_circuit: int,
    mle_grid_size: int,
    posterior_grid_size: int,
) -> dict[str, Any]:
    observations: list[dict[str, int]] = []
    schedule: list[int] = []
    for _ in range(steps):
        k = choose_iqae_k(
            observations, max_k=max_k, posterior_grid_size=posterior_grid_size
        )
        schedule.append(k)
        p = expected_amplified_probability(a_exact, k)
        observations.append(
            {
                "k": int(k),
                "shots": shots_per_circuit,
                "successes": binomial_sample(rng, shots_per_circuit, p),
            }
        )
    a_hat = mle_estimate(observations, grid_size=mle_grid_size)
    return {
        "a_hat": a_hat,
        "total_shots": shots_per_circuit * len(schedule),
        "oracle_queries": shots_per_circuit * sum(2 * k + 1 for k in schedule),
        "max_k": max(schedule),
        "schedule": schedule,
    }


def canonical_qae_distribution(a_exact: float, m_eval: int) -> list[float]:
    theta = math.asin(math.sqrt(min(max(a_exact, 0.0), 1.0))) / math.pi
    phases = [theta, 1 - theta]
    m_states = 2**m_eval
    probs = [0.0 for _ in range(m_states)]
    for phase in phases:
        for y in range(m_states):
            delta = phase - y / m_states
            if abs(math.sin(math.pi * delta)) < 1e-15:
                prob = 1.0
            else:
                prob = (
                    math.sin(math.pi * m_states * delta)
                    / (m_states * math.sin(math.pi * delta))
                ) ** 2
            probs[y] += 0.5 * prob
    norm = sum(probs)
    return [p / norm for p in probs]


def sample_from_distribution(rng: random.Random, probs: list[float]) -> int:
    threshold = rng.random()
    cdf = 0.0
    for idx, prob in enumerate(probs):
        cdf += prob
        if threshold <= cdf:
            return idx
    return len(probs) - 1


def run_canonical_qae(
    rng: random.Random,
    *,
    a_exact: float,
    m_eval: int,
) -> dict[str, Any]:
    probs = canonical_qae_distribution(a_exact, m_eval)
    y = sample_from_distribution(rng, probs)
    m_states = 2**m_eval
    a_hat = math.sin(math.pi * y / m_states) ** 2
    return {
        "a_hat": a_hat,
        "total_shots": 1,
        "oracle_queries": m_states - 1,
        "max_k": 2 ** (m_eval - 1),
        "schedule": [2**j for j in range(m_eval)],
    }


def radical_inverse(index: int, base: int = 2) -> float:
    result = 0.0
    factor = 1.0 / base
    while index > 0:
        result += factor * (index % base)
        index //= base
        factor /= base
    return result


def grid_value(grid_values: list[float] | None, a_exact: float, x: float) -> float:
    if grid_values is None:
        return 1.0 if x < a_exact else 0.0
    idx = min(len(grid_values) - 1, max(0, int(x * len(grid_values))))
    return grid_values[idx]


def run_mc(
    rng: random.Random,
    *,
    grid_values: list[float] | None,
    a_exact: float,
    sample_size: int,
) -> dict[str, Any]:
    values = [grid_value(grid_values, a_exact, rng.random()) for _ in range(sample_size)]
    return {
        "a_hat": sum(values) / sample_size,
        "total_shots": sample_size,
        "oracle_queries": 0,
        "max_k": 0,
        "schedule": [],
    }


def run_qmc(
    *,
    grid_values: list[float] | None,
    a_exact: float,
    sample_size: int,
) -> dict[str, Any]:
    values = [
        grid_value(grid_values, a_exact, radical_inverse(i + 1))
        for i in range(sample_size)
    ]
    return {
        "a_hat": sum(values) / sample_size,
        "total_shots": sample_size,
        "oracle_queries": 0,
        "max_k": 0,
        "schedule": [],
    }


def percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - pos) + ordered[hi] * (pos - lo)


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = []
    keys = sorted({(row["case_id"], row["method"], row["variant"]) for row in rows})
    for case_id, method, variant in keys:
        subset = [
            row
            for row in rows
            if row["case_id"] == case_id and row["method"] == method and row["variant"] == variant
        ]
        abs_errors = [float(row["abs_error"]) for row in subset]
        sq_errors = [float(row["abs_error"]) ** 2 for row in subset]
        summary.append(
            {
                "case_id": case_id,
                "method": method,
                "variant": variant,
                "repetitions": len(subset),
                "mean_abs_error": statistics.mean(abs_errors),
                "rmse": math.sqrt(statistics.mean(sq_errors)),
                "q025_abs_error": percentile(abs_errors, 0.025),
                "q975_abs_error": percentile(abs_errors, 0.975),
                "mean_total_shots": statistics.mean(float(row["total_shots"]) for row in subset),
                "mean_oracle_queries": statistics.mean(
                    float(row["oracle_queries"]) for row in subset
                ),
                "max_k": max(int(row["max_k"]) for row in subset),
            }
        )
    return summary


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    keys = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 1b simulator comparison.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--out-dir", default="results/phase1b")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    config = json.loads(config_path.read_text(encoding="utf-8"))
    rng = random.Random(int(config.get("seed", 12345)))

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    rows: list[dict[str, Any]] = []
    repetitions = int(config.get("repetitions", 200))
    shots_per_circuit = int(config.get("shots_per_circuit", 2048))
    mle_grid_size = int(config.get("mle_grid_size", 5001))
    posterior_grid_size = int(config.get("posterior_grid_size", 401))

    cases = resolve_cases(config)
    for case in cases:
        case_id = case["case_id"]
        a_exact = float(case["a_exact"])
        grid_values = case.get("grid_values")
        for rep in range(repetitions):
            for schedule in config.get("mlae_schedules", [[0, 1, 2]]):
                result = run_mlae(
                    rng,
                    a_exact=a_exact,
                    schedule=[int(k) for k in schedule],
                    shots_per_circuit=shots_per_circuit,
                    mle_grid_size=mle_grid_size,
                )
                rows.append(
                    {
                        "case_id": case_id,
                        "method": "MLAE",
                        "variant": "K=" + ",".join(str(k) for k in schedule),
                        "repetition": rep,
                        "a_exact": a_exact,
                        "a_hat": result["a_hat"],
                        "abs_error": abs(result["a_hat"] - a_exact),
                        "total_shots": result["total_shots"],
                        "oracle_queries": result["oracle_queries"],
                        "max_k": result["max_k"],
                        "schedule": json.dumps(result["schedule"]),
                    }
                )

            result = run_iqae_proxy(
                rng,
                a_exact=a_exact,
                max_k=int(config.get("iqae_max_k", 4)),
                steps=int(config.get("iqae_steps", 3)),
                shots_per_circuit=shots_per_circuit,
                mle_grid_size=mle_grid_size,
                posterior_grid_size=posterior_grid_size,
            )
            rows.append(
                {
                    "case_id": case_id,
                    "method": "IQAE_proxy",
                    "variant": f"steps={config.get('iqae_steps', 3)}",
                    "repetition": rep,
                    "a_exact": a_exact,
                    "a_hat": result["a_hat"],
                    "abs_error": abs(result["a_hat"] - a_exact),
                    "total_shots": result["total_shots"],
                    "oracle_queries": result["oracle_queries"],
                    "max_k": result["max_k"],
                    "schedule": json.dumps(result["schedule"]),
                }
            )

            for m_eval in config.get("canonical_qae_evaluation_qubits", [2, 3, 4]):
                result = run_canonical_qae(rng, a_exact=a_exact, m_eval=int(m_eval))
                rows.append(
                    {
                        "case_id": case_id,
                        "method": "canonical_QAE",
                        "variant": f"m={m_eval}",
                        "repetition": rep,
                        "a_exact": a_exact,
                        "a_hat": result["a_hat"],
                        "abs_error": abs(result["a_hat"] - a_exact),
                        "total_shots": result["total_shots"],
                        "oracle_queries": result["oracle_queries"],
                        "max_k": result["max_k"],
                        "schedule": json.dumps(result["schedule"]),
                    }
                )

            for sample_size in config.get("classical_sample_sizes", [64, 256, 1024]):
                for method, result in (
                    (
                        "MC",
                        run_mc(
                            rng,
                            grid_values=grid_values,
                            a_exact=a_exact,
                            sample_size=int(sample_size),
                        ),
                    ),
                    (
                        "QMC",
                        run_qmc(
                            grid_values=grid_values,
                            a_exact=a_exact,
                            sample_size=int(sample_size),
                        ),
                    ),
                ):
                    rows.append(
                        {
                            "case_id": case_id,
                            "method": method,
                            "variant": f"N={sample_size}",
                            "repetition": rep,
                            "a_exact": a_exact,
                            "a_hat": result["a_hat"],
                            "abs_error": abs(result["a_hat"] - a_exact),
                            "total_shots": result["total_shots"],
                            "oracle_queries": result["oracle_queries"],
                            "max_k": result["max_k"],
                            "schedule": json.dumps(result["schedule"]),
                        }
                    )

    summary_rows = summarize(rows)
    json_path = out_dir / f"phase1b_simulator_comparison_{timestamp}.json"
    csv_path = out_dir / f"phase1b_simulator_comparison_{timestamp}.csv"
    summary_path = out_dir / f"phase1b_simulator_summary_{timestamp}.csv"

    json_path.write_text(
        json.dumps({"config": config, "rows": rows, "summary": summary_rows}, indent=2),
        encoding="utf-8",
    )
    write_csv(rows, csv_path)
    write_csv(summary_rows, summary_path)

    print(f"Wrote JSON:    {json_path}")
    print(f"Wrote CSV:     {csv_path}")
    print(f"Wrote summary: {summary_path}")
    print()
    print("Quantum-estimator comparison by case:")
    for case in cases:
        case_id = case["case_id"]
        subset = [
            row
            for row in summary_rows
            if row["case_id"] == case_id
            and row["method"] in {"MLAE", "IQAE_proxy", "canonical_QAE"}
        ]
        best = sorted(
            subset,
            key=lambda row: (
                row["mean_abs_error"],
                row["mean_oracle_queries"],
                row["mean_total_shots"],
            ),
        )
        for row in best:
            print(
                f"  {case_id} {row['method']} {row['variant']}: "
                f"MAE={row['mean_abs_error']:.6g} RMSE={row['rmse']:.6g} "
                f"queries={row['mean_oracle_queries']:.1f} shots={row['mean_total_shots']:.1f}"
            )
        print(
            f"  Note: inspect MC/QMC and canonical_QAE for {case_id}; exactness can "
            "occur for special amplitudes or grids."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
