#!/usr/bin/env python3
"""Compare Phase 0 candidate tables across backends and runs."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


DEFAULT_RESULTS_DIR = Path("results/phase0")


def parse_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped in {"", "None", "null"}:
        return None
    if stripped in {"True", "False"}:
        return stripped == "True"
    try:
        if "." in stripped or "e" in stripped.lower():
            return float(stripped)
        return int(stripped)
    except ValueError:
        return value


def load_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError(f"Expected a list in {path}")
        return [dict(row, source_file=str(path)) for row in data]

    if path.suffix == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            return [
                {key: parse_value(value) for key, value in row.items()} | {"source_file": str(path)}
                for row in reader
            ]

    raise ValueError(f"Unsupported table format: {path}")


def discover_tables(results_dir: Path, *, latest: bool) -> list[Path]:
    paths = sorted(results_dir.glob("phase0_candidate_table_*.json"))
    if latest and paths:
        return [paths[-1]]
    return paths


def status_rank(status: Any) -> int:
    order = {
        "candidate": 0,
        "not_transpiled": 1,
        "reject_depth": 2,
        "reject_two_qubit_gates": 3,
    }
    return order.get(str(status), 99)


def numeric(row: dict[str, Any], key: str, default: float = float("inf")) -> float:
    value = row.get(key)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def comparison_key(row: dict[str, Any]) -> tuple:
    return (
        str(row.get("case_id", "")),
        int(numeric(row, "k", 0)),
        status_rank(row.get("risk_status")),
        numeric(row, "transpiled_depth"),
        numeric(row, "transpiled_two_qubit_gate_count"),
        numeric(row, "noisy_abs_dev"),
        str(row.get("backend", "")),
    )


def best_by_case_k(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("case_id")), int(numeric(row, "k", 0)))
        if key not in best or comparison_key(row) < comparison_key(best[key]):
            best[key] = row
    return sorted(best.values(), key=lambda row: (str(row.get("case_id")), int(numeric(row, "k", 0))))


def format_float(value: Any, digits: int = 4) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.{digits}g}"
    except (TypeError, ValueError):
        return str(value)


def format_int(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return str(value)


def print_rows(rows: list[dict[str, Any]], *, best_only: bool) -> None:
    if best_only:
        rows = best_by_case_k(rows)
    else:
        rows = sorted(rows, key=comparison_key)

    print(
        f"{'case':4} {'k':>2} {'backend':18} {'status':22} "
        f"{'ldepth':>7} {'tdepth':>7} {'2q':>5} {'2qdepth':>7} "
        f"{'ideal_dev':>10} {'noisy_dev':>10} {'L_can':>8}"
    )
    print("-" * 112)
    for row in rows:
        print(
            f"{str(row.get('case_id', '-')):4} "
            f"{str(row.get('k', '-')):>2} "
            f"{str(row.get('backend', '-'))[:18]:18} "
            f"{str(row.get('risk_status', '-')):22} "
            f"{format_int(row.get('logical_depth')):>7} "
            f"{format_int(row.get('transpiled_depth')):>7} "
            f"{format_int(row.get('transpiled_two_qubit_gate_count')):>5} "
            f"{format_int(row.get('transpiled_two_qubit_depth')):>7} "
            f"{format_float(row.get('ideal_abs_dev')):>10} "
            f"{format_float(row.get('noisy_abs_dev')):>10} "
            f"{format_float(row.get('canonical_length')):>8}"
        )


def write_csv(rows: list[dict[str, Any]], path: Path, *, best_only: bool) -> None:
    output_rows = best_by_case_k(rows) if best_only else sorted(rows, key=comparison_key)
    if not output_rows:
        return
    keys = sorted({key for row in output_rows for key in row})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in output_rows:
            writer.writerow(row)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare Phase 0 candidate tables.")
    parser.add_argument("tables", nargs="*", help="Phase 0 JSON/CSV tables to compare.")
    parser.add_argument(
        "--results-dir",
        default=str(DEFAULT_RESULTS_DIR),
        help="Directory used when no tables are passed.",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Only use the latest table found in --results-dir.",
    )
    parser.add_argument(
        "--best-only",
        action="store_true",
        help="Print only the best row for each (case_id, k).",
    )
    parser.add_argument(
        "--out-csv",
        default=None,
        help="Optional path to write the merged comparison as CSV.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.tables:
        paths = [Path(path) for path in args.tables]
    else:
        paths = discover_tables(Path(args.results_dir), latest=args.latest)

    if not paths:
        raise SystemExit("No Phase 0 tables found.")

    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.extend(load_rows(path))

    print(f"Loaded {len(rows)} rows from {len(paths)} table(s).")
    print()
    print_rows(rows, best_only=args.best_only)

    if args.out_csv:
        out_path = Path(args.out_csv)
        write_csv(rows, out_path, best_only=args.best_only)
        print()
        print(f"Wrote CSV: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
