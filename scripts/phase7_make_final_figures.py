#!/usr/bin/env python3
"""Create final Phase 7 figures for the project memory.

This script consumes local summary CSV files only. It does not connect to IBM
Quantum and does not submit jobs.
"""

from __future__ import annotations

import argparse
import csv
import os
import textwrap
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def configure_matplotlib() -> None:
    cache_root = PROJECT_ROOT / "results/phase7/.cache"
    mpl_config = cache_root / "matplotlib"
    xdg_cache = cache_root / "xdg"
    mpl_config.mkdir(parents=True, exist_ok=True)
    xdg_cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(mpl_config))
    os.environ.setdefault("XDG_CACHE_HOME", str(xdg_cache))


configure_matplotlib()

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402


COLORS = {
    "paper_quality_distribution": "#2CA02C",
    "backend_diagnostic_distribution": "#9467BD",
    "paper_quality": "#2CA02C",
    "paper_quality_with_bias_caveat": "#FF7F0E",
    "backend_diagnostic": "#9467BD",
}


def resolve_path(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def save_all(fig: plt.Figure, out_dir: Path, stem: str) -> list[Path]:
    paths = []
    for ext in ("png", "pdf", "svg"):
        path = out_dir / f"{stem}.{ext}"
        fig.savefig(path, bbox_inches="tight", dpi=220)
        paths.append(path)
    plt.close(fig)
    return paths


def wrap_label(text: str, width: int = 22) -> str:
    return "\n".join(textwrap.wrap(text.replace("_", " "), width=width))


def plot_distribution_summary(rows: list[dict[str, str]], out_dir: Path) -> list[Path]:
    ordered = sorted(rows, key=lambda row: (row["classification"], row["case_id"]))
    labels = [wrap_label(f"{row['case_id']} / {row['variant']}", 22) for row in ordered]
    tvd = [float(row["tvd"]) for row in ordered]
    max_dev = [float(row["max_abs_deviation"]) for row in ordered]
    colors = [COLORS.get(row["classification"], "#777777") for row in ordered]
    x = list(range(len(ordered)))
    width = 0.38

    fig, ax = plt.subplots(figsize=(10.8, 5.1))
    ax.bar([i - width / 2 for i in x], tvd, width, color=colors, label="TVD", alpha=0.95)
    ax.bar(
        [i + width / 2 for i in x],
        max_dev,
        width,
        color="#9A9A9A",
        label="Max cell deviation",
        alpha=0.78,
    )
    ax.axhline(0.030, color="#333333", linewidth=1.0, linestyle="--", label="TVD paper threshold")
    ax.axhline(0.040, color="#666666", linewidth=1.0, linestyle=":", label="TVD diagnostic threshold")
    ax.set_ylabel("Distance")
    ax.set_title("Phase 7 Final Distribution Classification")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=0, ha="center")
    ax.set_ylim(0.0, max(max(tvd), max(max_dev)) * 1.26)
    ax.grid(axis="y", alpha=0.25)
    legend_items = [
        Patch(facecolor=COLORS["paper_quality_distribution"], label="paper-quality distribution"),
        Patch(facecolor=COLORS["backend_diagnostic_distribution"], label="backend diagnostic"),
        Patch(facecolor="#9A9A9A", label="max cell deviation"),
    ]
    ax.legend(handles=legend_items, frameon=False, ncols=3, loc="upper left")
    fig.tight_layout()
    return save_all(fig, out_dir, "phase7_distribution_classification")


def plot_qoi_classification(rows: list[dict[str, str]], out_dir: Path) -> list[Path]:
    ordered = sorted(
        rows,
        key=lambda row: (
            {
                "paper_quality": 0,
                "paper_quality_with_bias_caveat": 1,
                "backend_diagnostic": 2,
            }.get(row["classification"], 9),
            float(row["phase7_error"] or "99"),
        ),
    )
    labels = [wrap_label(f"{row['case_id']} / {row['quantity_id']}", 24) for row in ordered]
    errors = [float(row["phase7_error"]) for row in ordered if row["phase7_error"]]
    colors = [COLORS.get(row["classification"], "#777777") for row in ordered]

    fig, ax = plt.subplots(figsize=(13.4, 6.3))
    x = list(range(len(ordered)))
    ax.bar(x, [float(row["phase7_error"] or 0.0) for row in ordered], color=colors, alpha=0.95)
    ax.axhline(0.005, color="#2CA02C", linewidth=1.0, linestyle="--", label="paper-quality threshold")
    ax.axhline(0.010, color="#1F77B4", linewidth=1.0, linestyle="--", label="bias-caveat threshold")
    ax.axhline(0.020, color="#9467BD", linewidth=1.0, linestyle="--", label="diagnostic threshold")
    ax.set_ylabel(r"Absolute error $|I_{\hat p}(f)-I_p(f)|$")
    ax.set_title("Phase 7 Final Quantity-of-Interest Classification")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=65, ha="right")
    ax.set_ylim(0.0, max(errors) * 1.25 if errors else 1.0)
    ax.grid(axis="y", alpha=0.25)
    legend_items = [
        Patch(facecolor=COLORS["paper_quality"], label="paper-quality"),
        Patch(facecolor=COLORS["paper_quality_with_bias_caveat"], label="bias caveat"),
        Patch(facecolor=COLORS["backend_diagnostic"], label="backend diagnostic"),
    ]
    ax.legend(handles=legend_items, frameon=False, ncols=3, loc="upper left")
    fig.tight_layout()
    return save_all(fig, out_dir, "phase7_qoi_classification")


def plot_qoi_error_change(rows: list[dict[str, str]], out_dir: Path) -> list[Path]:
    comparable = [row for row in rows if row["error_change_phase7_minus_phase6"]]
    ordered = sorted(comparable, key=lambda row: float(row["error_change_phase7_minus_phase6"]))
    labels = [wrap_label(f"{row['case_id']} / {row['quantity_id']}", 24) for row in ordered]
    deltas = [float(row["error_change_phase7_minus_phase6"]) for row in ordered]
    colors = ["#2CA02C" if value <= 0 else "#D62728" for value in deltas]
    x = list(range(len(ordered)))

    fig, ax = plt.subplots(figsize=(13.4, 5.7))
    ax.bar(x, deltas, color=colors, alpha=0.92)
    ax.axhline(0.0, color="#333333", linewidth=1.0)
    ax.set_ylabel("Phase 7 error minus Phase 6 error")
    ax.set_title("Run-to-Run Change in Quantity-of-Interest Error")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=65, ha="right")
    span = max(abs(min(deltas)), abs(max(deltas))) if deltas else 1.0
    ax.set_ylim(-1.20 * span, 1.20 * span)
    ax.grid(axis="y", alpha=0.25)
    ax.legend(
        handles=[
            Patch(facecolor="#2CA02C", label="improved or unchanged"),
            Patch(facecolor="#D62728", label="degraded"),
        ],
        frameon=False,
        ncols=2,
        loc="upper left",
    )
    fig.tight_layout()
    return save_all(fig, out_dir, "phase7_qoi_error_change")


def plot_classification_counts(rows: list[dict[str, str]], out_dir: Path) -> list[Path]:
    order = ["paper_quality", "paper_quality_with_bias_caveat", "backend_diagnostic"]
    counts = Counter(row["classification"] for row in rows)
    values = [counts.get(item, 0) for item in order]

    fig, ax = plt.subplots(figsize=(8.3, 4.3))
    ax.bar(order, values, color=[COLORS[item] for item in order])
    ax.set_ylabel("Number of quantities")
    ax.set_title("Phase 7 Final Quantity Classification Counts")
    ax.set_ylim(0, max(values) + 2)
    ax.grid(axis="y", alpha=0.25)
    ax.set_xticks(list(range(len(order))))
    ax.set_xticklabels(["paper\nquality", "bias\ncaveat", "backend\ndiagnostic"])
    for index, value in enumerate(values):
        ax.text(index, value + 0.12, str(value), ha="center", va="bottom")
    fig.tight_layout()
    return save_all(fig, out_dir, "phase7_qoi_classification_counts")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create final Phase 7 report figures.")
    parser.add_argument(
        "--distribution-summary",
        default="results/phase7/paper_quality_summary/phase7_distribution_paper_quality_summary.csv",
    )
    parser.add_argument(
        "--qoi-summary",
        default="results/phase7/paper_quality_summary/phase7_qoi_paper_quality_summary.csv",
    )
    parser.add_argument("--out-dir", default="results/phase7/figures")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    distribution_rows = read_csv(resolve_path(args.distribution_summary))
    qoi_rows = read_csv(resolve_path(args.qoi_summary))

    written: list[Path] = []
    written.extend(plot_distribution_summary(distribution_rows, out_dir))
    written.extend(plot_qoi_classification(qoi_rows, out_dir))
    written.extend(plot_qoi_error_change(qoi_rows, out_dir))
    written.extend(plot_classification_counts(qoi_rows, out_dir))

    print("Wrote figures:")
    for path in written:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
