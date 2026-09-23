#!/usr/bin/env python3
"""Create Phase 10 sampling-baseline figures."""

from __future__ import annotations

import argparse
import csv
import os
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def configure_matplotlib() -> None:
    cache_root = PROJECT_ROOT / "results/phase10/.cache"
    mpl_config = cache_root / "matplotlib"
    xdg_cache = cache_root / "xdg"
    mpl_config.mkdir(parents=True, exist_ok=True)
    xdg_cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(mpl_config))
    os.environ.setdefault("XDG_CACHE_HOME", str(xdg_cache))


configure_matplotlib()

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402


STATUS_COLORS = {
    "mc_competitive": "#2CA02C",
    "near_mc_scale": "#1F77B4",
    "hardware_bias_diagnostic": "#FF7F0E",
    "mc_dominated": "#D62728",
    "typical_sampling_error": "#2CA02C",
    "upper_sampling_tail": "#1F77B4",
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


def short_case(case_id: str) -> str:
    return case_id.replace("gr_", "").replace("_", " ")


def plot_distribution(rows: list[dict[str, str]], out_dir: Path) -> list[Path]:
    labels = ["\n".join(textwrap.wrap(short_case(row["case_id"]), width=14)) for row in rows]
    qpu = [float(row["qpu_tvd"]) for row in rows]
    mc = [float(row["mc_median_tvd"]) for row in rows]
    qmc = [float(row["qmc_median_tvd"]) for row in rows]
    x = list(range(len(rows)))
    width = 0.24

    fig, ax = plt.subplots(figsize=(8.8, 4.8))
    ax.bar([i - width for i in x], qpu, width, label="QPU", color="#4C78A8")
    ax.bar(x, mc, width, label="MC median", color="#F58518")
    ax.bar([i + width for i in x], qmc, width, label="QMC median", color="#54A24B")
    ax.set_ylabel("TVD")
    ax.set_title("Phase 10 Distribution Error vs Classical Sampling")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylim(0.0, max(qpu + mc + qmc) * 1.25)
    ax.grid(axis="y", alpha=0.25)
    ax.legend(frameon=False, ncols=3, loc="upper left")
    fig.tight_layout()
    return save_all(fig, out_dir, "phase10_distribution_sampling_baselines")


def plot_qoi(rows: list[dict[str, str]], out_dir: Path) -> list[Path]:
    ordered = sorted(rows, key=lambda row: (row["case_id"], float(row["qpu_abs_error_over_mc_rmse"])))
    labels = [
        "\n".join(
            textwrap.wrap(
                f"{short_case(row['case_id'])} / {row['quantity']}".replace("_", " "),
                width=18,
            )
        )
        for row in ordered
    ]
    qpu_errors = [float(row["qpu_abs_error"]) for row in ordered]
    mc_rmse = [float(row["mc_rmse"]) for row in ordered]
    colors = [STATUS_COLORS.get(row["status"], "#777777") for row in ordered]
    x = list(range(len(ordered)))
    width = 0.38

    fig, ax = plt.subplots(figsize=(13.2, 5.8))
    ax.bar(
        [i - width / 2 for i in x],
        qpu_errors,
        width,
        label="QPU absolute error",
        color=colors,
    )
    ax.bar(
        [i + width / 2 for i in x],
        mc_rmse,
        width,
        label="MC RMSE",
        color="#9A9A9A",
        alpha=0.8,
    )
    ax.set_ylabel("Error")
    ax.set_title("Phase 10 Quantity-of-Interest Error vs MC Sampling")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=62, ha="right")
    ax.set_ylim(0.0, max(qpu_errors + mc_rmse) * 1.22)
    ax.grid(axis="y", alpha=0.25)
    legend_items = [
        Patch(facecolor=STATUS_COLORS["mc_competitive"], label="MC-competitive"),
        Patch(facecolor=STATUS_COLORS["hardware_bias_diagnostic"], label="diagnostic"),
        Patch(facecolor=STATUS_COLORS["mc_dominated"], label="MC-dominated"),
        Patch(facecolor="#9A9A9A", label="MC RMSE"),
    ]
    ax.legend(handles=legend_items, frameon=False, ncols=4, loc="upper left")
    fig.tight_layout()
    return save_all(fig, out_dir, "phase10_qoi_sampling_baselines")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create Phase 10 figures.")
    parser.add_argument(
        "--distribution-decision-table",
        default="results/phase10/decision_table/phase10_distribution_decision_table.csv",
    )
    parser.add_argument(
        "--qoi-decision-table",
        default="results/phase10/decision_table/phase10_qoi_decision_table.csv",
    )
    parser.add_argument("--out-dir", default="results/phase10/figures")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = resolve_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    distribution_rows = read_csv(resolve_path(args.distribution_decision_table))
    qoi_rows = read_csv(resolve_path(args.qoi_decision_table))

    written = []
    written.extend(plot_distribution(distribution_rows, out_dir))
    written.extend(plot_qoi(qoi_rows, out_dir))

    print("Wrote figures:")
    for path in written:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
