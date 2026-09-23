#!/usr/bin/env python3
"""Audit how the angle-structure degree behaves under grid refinement.

Credit-free structural audit for Workflow A. For each integrand the script
computes the multilinear degree d of the angle map

    Theta_g(b) = 2 asin(sqrt(g(x_{i(b)})))

on the dyadic grid with n qubits, for a range of n, and reports the induced
canonical gate count binom(n, <= d) against the generic cost 2^n.

Two questions are answered:

1.  Is membership in G_n^(d) stable under refinement?  The benchmark family
    g0, g1, g2 keeps its degree for every n, while the smooth stress cases
    (a straight line, a beta-like bump) climb to full degree d = n and cost
    the generic 2^n gates.  Smoothness is therefore not the operative
    property.

2.  How rigid is the low-degree class?  The --probe-stability cases show that
    rescaling g1 by 1/2, or perturbing it by 0.01, moves it from d = 1 to
    d = n.  Asking for d = 1 at every n forces g(x) = sin^2(alpha + beta x)
    exactly, because x is affine in the bits of the dyadic grid, so an affine
    angle table for all n means Theta_g is affine in x.

For a fixed n the class is much larger -- only the 2^n angle values have to
fit an affine form -- which is what makes the Sobolev decoupling lemma of
Chinesta, Falco and Falco-Pomares (arXiv:2604.24289) possible.

Usage:
    python3 scripts/audit_angle_degree_refinement.py
    python3 scripts/audit_angle_degree_refinement.py --n-min 2 --n-max 12
    python3 scripts/audit_angle_degree_refinement.py --probe-stability
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from grover_rudolph_qae_benchmark.angle_structure import (  # noqa: E402
    classify_callable,
)


# --- integrand registry ------------------------------------------------------

BENCHMARK_CASES: dict[str, tuple[Callable[[float], float], str]] = {
    "g0_constant_quarter": (
        lambda x: 0.25,
        "g0 = 1/4, the degree-0 calibration function.",
    ),
    "g1_sin2_half": (
        lambda x: math.sin(math.pi * x / 2.0) ** 2,
        "g1 = sin^2(pi x / 2), the affine-angle calibration function.",
    ),
    "g2_sin2": (
        lambda x: math.sin(math.pi * x) ** 2,
        "g2 = sin^2(pi x), the quadratic-angle calibration function.",
    ),
    "affine_shifted": (
        lambda x: 0.11 + 0.57 * x,
        "Straight line 0.11 + 0.57 x, the Phase 15b stress case.",
    ),
    "beta_bump": (
        lambda x: x * (1.0 - x) ** 4,
        "Smooth localized bump x (1-x)^4, the Phase 15b stress case.",
    ),
    "oscillatory_shifted": (
        lambda x: 0.55 + 0.25 * math.sin(2.0 * math.pi * x) + 0.10 * math.cos(4.0 * math.pi * x),
        "Shifted oscillatory profile of Phase 18.",
    ),
}

STABILITY_CASES: dict[str, tuple[Callable[[float], float], str]] = {
    "sin2_affine_a": (
        lambda x: math.sin(0.15 + 1.20 * x) ** 2,
        "sin^2 of an affine map: predicted to stay at d = 1 for every n.",
    ),
    "sin2_affine_b": (
        lambda x: math.sin(0.40 + 0.90 * x) ** 2,
        "sin^2 of a different affine map: same prediction.",
    ),
    "g1_scaled_half": (
        lambda x: 0.5 * math.sin(math.pi * x / 2.0) ** 2,
        "g1 rescaled by 1/2. Same shape, same smoothness.",
    ),
    "g1_offset_001": (
        lambda x: 0.98 * math.sin(math.pi * x / 2.0) ** 2 + 0.01,
        "g1 perturbed by 0.01. Graphically indistinguishable from g1.",
    ),
    "g2_scaled_half": (
        lambda x: 0.5 * math.sin(math.pi * x) ** 2,
        "g2 rescaled by 1/2, to check that d = 2 is equally fragile.",
    ),
}


def gate_count(num_qubits: int, degree: int) -> int:
    """Canonical encoding cost binom(n, <= d) of the monomial factorisation."""
    return sum(math.comb(num_qubits, k) for k in range(degree + 1))


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    keys = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def audit(
    cases: dict[str, tuple[Callable[[float], float], str]],
    *,
    n_min: int,
    n_max: int,
    grid: str,
    tolerance: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case_id, (fn, notes) in cases.items():
        for num_qubits in range(n_min, n_max + 1):
            classification = classify_callable(
                case_id,
                fn,
                num_qubits=num_qubits,
                grid=grid,
                tolerance=tolerance,
                notes=notes,
            )
            degree = int(classification.degree)
            gates = gate_count(num_qubits, degree)
            generic = 2**num_qubits
            rows.append(
                {
                    "case_id": case_id,
                    "num_qubits": num_qubits,
                    "grid_points": generic,
                    "degree": degree,
                    "class": f"G_{num_qubits}^({degree})",
                    "support": int(classification.support),
                    "gates_binom_le_d": gates,
                    "gates_generic_2n": generic,
                    "gate_fraction": gates / generic,
                    "full_degree": degree == num_qubits,
                    "notes": notes,
                }
            )
    return rows


def degree_table(rows: list[dict[str, Any]], n_min: int, n_max: int) -> str:
    cases = list(dict.fromkeys(row["case_id"] for row in rows))
    lookup = {(row["case_id"], row["num_qubits"]): row for row in rows}
    header = f"| {'case':<22} | " + " | ".join(f"n={n}" for n in range(n_min, n_max + 1))
    header += f" | gates at n={n_max} |"
    sep = "|" + "-" * 24 + "|" + "".join("-----|" for _ in range(n_min, n_max + 1)) + "----------------|"
    lines = [header, sep]
    for case_id in cases:
        cells = " | ".join(
            f"{lookup[(case_id, n)]['degree']:>3}" for n in range(n_min, n_max + 1)
        )
        last = lookup[(case_id, n_max)]
        lines.append(
            f"| {case_id:<22} | {cells} | "
            f"{last['gates_binom_le_d']:>5} / {last['gates_generic_2n']:<5} |"
        )
    return "\n".join(lines)


def verdicts(rows: list[dict[str, Any]]) -> list[str]:
    """Per-case verdict: is the degree constant under refinement, or does it grow?"""
    by_case: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_case.setdefault(row["case_id"], []).append(row)

    stable: list[str] = []
    growing: list[str] = []
    for case_id, case_rows in by_case.items():
        case_rows.sort(key=lambda r: r["num_qubits"])
        degrees = [r["degree"] for r in case_rows]
        last = case_rows[-1]
        if len(set(degrees)) == 1:
            gates = last["gates_binom_le_d"]
            plural = "gate" if gates == 1 else "gates"
            stable.append(
                f"{case_id} (d = {degrees[0]}, {gates} {plural} at n = {last['num_qubits']})"
            )
        else:
            growing.append(
                f"{case_id} (d: {degrees[0]} -> {degrees[-1]}, "
                f"{last['gate_fraction']:.0%} of the generic 2^n cost at n = {last['num_qubits']})"
            )

    lines = ["Degree constant under refinement:"]
    lines += [f"  {item}" for item in sorted(stable)] or ["  none"]
    lines += ["Degree grows with the grid:"]
    lines += [f"  {item}" for item in sorted(growing)] or ["  none"]
    return lines


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit the angle-structure degree of benchmark integrands under "
            "dyadic grid refinement, and the stability of the low-degree class."
        )
    )
    parser.add_argument("--n-min", type=int, default=2)
    parser.add_argument("--n-max", type=int, default=9)
    parser.add_argument("--grid", choices=["midpoint", "left"], default="midpoint")
    parser.add_argument("--tolerance", type=float, default=1e-10)
    parser.add_argument(
        "--probe-stability",
        action="store_true",
        help="Also audit the rescaled / perturbed / sin^2-of-affine probe family.",
    )
    parser.add_argument("--out-dir", default="results/angle_degree_refinement")
    parser.add_argument("--output-prefix", default="angle_degree_refinement")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.n_max < args.n_min:
        raise SystemExit("--n-max must be at least --n-min.")

    cases = dict(BENCHMARK_CASES)
    if args.probe_stability:
        cases.update(STABILITY_CASES)

    rows = audit(
        cases,
        n_min=args.n_min,
        n_max=args.n_max,
        grid=args.grid,
        tolerance=args.tolerance,
    )

    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{args.output_prefix}.json"
    csv_path = out_dir / f"{args.output_prefix}.csv"
    md_path = out_dir / f"{args.output_prefix}.md"

    table = degree_table(rows, args.n_min, args.n_max)
    summary = [
        "# Angle-structure degree under grid refinement",
        "",
        f"Grid: {args.grid}. Coefficient tolerance: {args.tolerance:g}.",
        "",
        "Multilinear degree d of Theta_g = 2 asin(sqrt(g)), and the canonical",
        "encoding cost binom(n, <= d) against the generic cost 2^n.",
        "",
        table,
        "",
        "A case reaching d = n needs the generic 2^n controlled rotations: the",
        "encoding offers no saving over an arbitrary state preparation.",
    ]

    json_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(rows, csv_path)
    md_path.write_text("\n".join(summary) + "\n", encoding="utf-8")

    print(f"Wrote JSON: {json_path}")
    print(f"Wrote CSV:  {csv_path}")
    print(f"Wrote MD:   {md_path}")
    print()
    print(table)
    print()
    for line in verdicts(rows):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
