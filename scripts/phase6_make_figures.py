#!/usr/bin/env python3
"""Create Phase 6 figures from local decision tables."""

from __future__ import annotations

import argparse
import csv
import os
import textwrap
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def configure_matplotlib() -> None:
    cache_root = PROJECT_ROOT / "results/phase6/.cache"
    mpl_config = cache_root / "matplotlib"
    xdg_cache = cache_root / "xdg"
    mpl_config.mkdir(parents=True, exist_ok=True)
    xdg_cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(mpl_config))
    os.environ.setdefault("XDG_CACHE_HOME", str(xdg_cache))


configure_matplotlib()

import matplotlib.pyplot as plt  # noqa: E402


COLORS = {
    "raw": "#4C78A8",
    "mitigated": "#54A24B",
    "mitigated_opt3_seed45678": "#F58518",
    "publishable": "#2CA02C",
    "usable_with_ci": "#1F77B4",
    "usable_bias_reported": "#FF7F0E",
    "diagnostic": "#9467BD",
    "reject": "#D62728",
}


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def label_case_variant(row: dict[str, str]) -> str:
    case = str(row["case_id"]).replace("gr_", "").replace("_", " ")
    variant = str(row.get("variant") or row.get("selected_variant"))
    variant = variant.replace("mitigated_opt3_seed45678", "opt3 repeat")
    return "\n".join(textwrap.wrap(f"{case} / {variant}", width=18))


def save_all(fig: plt.Figure, out_dir: Path, stem: str) -> list[Path]:
    paths = []
    for ext in ("png", "pdf", "svg"):
        path = out_dir / f"{stem}.{ext}"
        fig.savefig(path, bbox_inches="tight", dpi=220)
        paths.append(path)
    plt.close(fig)
    return paths


def plot_distribution_metrics(rows: list[dict[str, str]], out_dir: Path) -> list[Path]:
    labels = [label_case_variant(row) for row in rows]
    tvd = [float(row["tvd"]) for row in rows]
    hellinger = [float(row["hellinger"]) for row in rows]
    colors = [COLORS.get(row["variant"], "#777777") for row in rows]
    x = list(range(len(rows)))
    width = 0.38

    fig, ax = plt.subplots(figsize=(11.5, 5.2))
    ax.bar([i - width / 2 for i in x], tvd, width, label="TVD", color=colors, alpha=0.95)
    ax.bar(
        [i + width / 2 for i in x],
        hellinger,
        width,
        label="Hellinger",
        color="#9A9A9A",
        alpha=0.75,
    )
    ax.axhline(0.025, color="#333333", linewidth=1.0, linestyle="--", label="TVD reference 0.025")
    ax.set_ylabel("Distance")
    ax.set_title("Phase 6 Distribution Distances")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=0, ha="center")
    ax.set_ylim(0.0, max(max(tvd), max(hellinger)) * 1.22)
    ax.grid(axis="y", alpha=0.25)
    ax.legend(frameon=False, ncols=3, loc="upper left")
    fig.tight_layout()
    return save_all(fig, out_dir, "phase6_distribution_distances")


def plot_functional_errors(rows: list[dict[str, str]], out_dir: Path) -> list[Path]:
    ordered = sorted(rows, key=lambda row: float(row["abs_error"]))
    labels = [
        "\n".join(
            textwrap.wrap(
                f"{row['case_id'].replace('gr_', '')} / {row['quantity_id']}".replace("_", " "),
                width=20,
            )
        )
        for row in ordered
    ]
    errors = [float(row["abs_error"]) for row in ordered]
    colors = [COLORS.get(row["status"], "#777777") for row in ordered]
    x = list(range(len(ordered)))

    fig, ax = plt.subplots(figsize=(13.0, 5.8))
    ax.bar(x, errors, color=colors, alpha=0.95)
    ax.axhline(0.005, color="#2CA02C", linewidth=1.0, linestyle="--", label="publishable threshold")
    ax.axhline(0.010, color="#1F77B4", linewidth=1.0, linestyle="--", label="usable threshold")
    ax.axhline(0.020, color="#9467BD", linewidth=1.0, linestyle="--", label="diagnostic threshold")
    ax.set_ylabel("Absolute error")
    ax.set_title("Phase 6 Direct Quantity-of-Interest Errors")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=65, ha="right")
    ax.set_ylim(0.0, max(errors) * 1.25)
    ax.grid(axis="y", alpha=0.25)
    ax.legend(frameon=False, ncols=3, loc="upper left")
    fig.tight_layout()
    return save_all(fig, out_dir, "phase6_functional_errors")


def plot_status_counts(rows: list[dict[str, str]], out_dir: Path) -> list[Path]:
    order = ["publishable", "usable_with_ci", "usable_bias_reported", "diagnostic", "reject"]
    counts = Counter(row["status"] for row in rows)
    values = [counts.get(status, 0) for status in order]

    fig, ax = plt.subplots(figsize=(8.2, 4.2))
    ax.bar(order, values, color=[COLORS[status] for status in order])
    ax.set_ylabel("Number of quantities")
    ax.set_title("Phase 6 Quantity-of-Interest Decision Counts")
    ax.set_ylim(0, max(values) + 2)
    ax.grid(axis="y", alpha=0.25)
    ax.set_xticks(list(range(len(order))))
    ax.set_xticklabels([status.replace("_", "\n") for status in order])
    for index, value in enumerate(values):
        ax.text(index, value + 0.12, str(value), ha="center", va="bottom")
    fig.tight_layout()
    return save_all(fig, out_dir, "phase6_functional_status_counts")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create Phase 6 report figures.")
    parser.add_argument(
        "--decision-table",
        default="results/phase6/decision_table/phase6_decision_table.csv",
    )
    parser.add_argument(
        "--qoi-table",
        default="results/phase6/qoi_decision_table/phase6_qoi_decision_table.csv",
    )
    parser.add_argument("--out-dir", default="results/phase6/figures")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    decision_rows = read_csv(resolve_path(args.decision_table))
    qoi_rows = read_csv(resolve_path(args.qoi_table))

    written = []
    written.extend(plot_distribution_metrics(decision_rows, out_dir))
    written.extend(plot_functional_errors(qoi_rows, out_dir))
    written.extend(plot_status_counts(qoi_rows, out_dir))

    print("Wrote figures:")
    for path in written:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
