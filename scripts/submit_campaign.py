#!/usr/bin/env python3
"""Prepare or submit a hardware campaign from a JSON configuration file.

Default mode is safe: circuits are built and transpiled, but no QPU jobs are
submitted. Add --submit to spend IBM Quantum Runtime credits.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from grover_rudolph_qae_benchmark.benchmark_specs import get_benchmark_spec
from grover_rudolph_qae_benchmark.circuits import (
    build_amplification_circuit,
    expected_amplified_probability,
)
from grover_rudolph_qae_benchmark.ibm import DEFAULT_ACCOUNT_FILE, qiskit_runtime_service
from grover_rudolph_qae_benchmark.metrics import circuit_metrics, structural_metrics, two_qubit_depth


DEFAULT_CAMPAIGN = PROJECT_ROOT / "experiments" / "phase1_calibration_ibm_fez.json"


def load_qiskit_tools():
    try:
        from qiskit import transpile
        from qiskit_ibm_runtime import SamplerV2 as Sampler
    except ImportError as exc:
        raise SystemExit(
            "qiskit and qiskit-ibm-runtime are required.\n"
            "Install them with:\n\n"
            "  python3 -m pip install -e '.[simulation]'\n"
        ) from exc
    return transpile, Sampler


def backend_name(backend) -> str:
    name = getattr(backend, "name", None)
    if callable(name):
        return str(name())
    if name:
        return str(name)
    return str(backend)


def backend_status(backend) -> dict[str, Any]:
    try:
        status = backend.status()
    except Exception as exc:
        return {"status_msg": f"unavailable: {exc}"}
    return {
        "operational": getattr(status, "operational", None),
        "pending_jobs": getattr(status, "pending_jobs", None),
        "status_msg": getattr(status, "status_msg", None),
    }


def usage_summary(service) -> dict[str, Any] | None:
    try:
        usage = service.usage()
    except Exception:
        return None
    consumed = usage.get("usage_consumed_seconds")
    limit = usage.get("usage_limit_seconds") or usage.get("usage_allocation_seconds")
    remaining = None
    if consumed is not None and limit is not None:
        try:
            remaining = max(0.0, float(limit) - float(consumed))
        except (TypeError, ValueError):
            remaining = None
    return {
        "usage_consumed_seconds": consumed,
        "usage_limit_seconds": usage.get("usage_limit_seconds"),
        "usage_allocation_seconds": usage.get("usage_allocation_seconds"),
        "estimated_remaining_seconds": remaining,
        "usage_limit_reached": usage.get("usage_limit_reached"),
        "usage_period": usage.get("usage_period"),
    }


def format_duration(seconds: Any) -> str:
    if seconds is None:
        return "not reported"
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return str(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} h")
    if minutes or hours:
        parts.append(f"{minutes} min")
    parts.append(f"{secs} s")
    return " ".join(parts)


def circuit_qasm(circuit) -> str | None:
    if hasattr(circuit, "qasm"):
        try:
            return circuit.qasm()
        except Exception:
            pass
    try:
        from qiskit import qasm2

        return qasm2.dumps(circuit)
    except Exception:
        return None


def selected_work_items(campaign: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for entry in campaign.get("selected_jobs", []):
        case_id = entry["case_id"]
        for k in entry.get("ks", []):
            items.append(
                {
                    "case_id": case_id,
                    "k": int(k),
                    "purpose": entry.get("purpose"),
                }
            )
    return items


def apply_runtime_options(sampler, runtime_options: dict[str, Any]) -> None:
    if not runtime_options:
        return
    try:
        sampler.options.update(**runtime_options)
        return
    except Exception:
        pass

    for key, value in runtime_options.items():
        if isinstance(value, dict):
            target = getattr(sampler.options, key, None)
            if target is not None:
                try:
                    target.update(**value)
                    continue
                except Exception:
                    for subkey, subvalue in value.items():
                        try:
                            setattr(target, subkey, subvalue)
                        except Exception:
                            pass
        else:
            try:
                setattr(sampler.options, key, value)
            except Exception:
                pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare or submit an IBM hardware campaign.")
    parser.add_argument("--campaign", default=str(DEFAULT_CAMPAIGN))
    parser.add_argument("--account-file", default=str(DEFAULT_ACCOUNT_FILE))
    parser.add_argument("--name", default=None, help="Saved IBM account name.")
    parser.add_argument("--instance", default=None, help="IBM Quantum instance CRN/name.")
    parser.add_argument("--backend", default=None, help="Override backend from campaign JSON.")
    parser.add_argument("--shots", type=int, default=None, help="Override shots from campaign JSON.")
    parser.add_argument("--optimization-level", type=int, default=None)
    parser.add_argument("--initial-layout", type=int, nargs="+", default=None)
    parser.add_argument("--out-dir", default="results/hardware")
    parser.add_argument("--submit", action="store_true", help="Actually submit jobs to IBM QPU.")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation required with --submit.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    campaign_path = Path(args.campaign)
    if not campaign_path.is_absolute():
        campaign_path = PROJECT_ROOT / campaign_path
    campaign = json.loads(campaign_path.read_text(encoding="utf-8"))

    backend_id = args.backend or campaign["backend"]
    shots = args.shots or int(campaign.get("shots", 2048))
    optimization_level = (
        args.optimization_level
        if args.optimization_level is not None
        else int(campaign.get("optimization_level", 0))
    )
    seed = campaign.get("seed")
    runtime_options = campaign.get("runtime_options", {})

    transpile, Sampler = load_qiskit_tools()
    service = qiskit_runtime_service(
        account_file=args.account_file,
        name=args.name,
        instance=args.instance,
    )
    backend = service.backend(backend_id)
    bname = backend_name(backend)
    bstatus = backend_status(backend)
    usage = usage_summary(service)

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir
    campaign_out = out_dir / campaign["campaign_id"] / datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    circuits_dir = campaign_out / "circuits"
    metadata_dir = campaign_out / "pending"
    circuits_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    work_items = selected_work_items(campaign)
    if not work_items:
        raise SystemExit("Campaign has no selected_jobs.")

    print(f"Campaign:       {campaign['campaign_id']}")
    print(f"Backend:        {bname}")
    print(f"Backend status: {bstatus.get('status_msg')}")
    print(f"Pending jobs:   {bstatus.get('pending_jobs')}")
    print(f"Shots/job:      {shots}")
    print(f"Opt level:      {optimization_level}")
    print(f"Submit mode:    {args.submit}")
    if runtime_options:
        print(f"Runtime opts:   {json.dumps(runtime_options, sort_keys=True)}")
    if usage:
        print(f"Runtime used:   {format_duration(usage.get('usage_consumed_seconds'))}")
        print(f"Runtime left:   {format_duration(usage.get('estimated_remaining_seconds'))}")
    print()

    prepared: list[dict[str, Any]] = []
    for item in work_items:
        spec = get_benchmark_spec(item["case_id"])
        k = item["k"]
        logical = build_amplification_circuit(spec, k, measure=True)
        started = time.perf_counter()
        transpile_kwargs = {
            "backend": backend,
            "optimization_level": optimization_level,
        }
        if seed is not None:
            transpile_kwargs["seed_transpiler"] = int(seed)
        if args.initial_layout is not None:
            transpile_kwargs["initial_layout"] = args.initial_layout
        transpiled = transpile(logical, **transpile_kwargs)
        transpile_seconds = time.perf_counter() - started

        logical_metrics = circuit_metrics(logical)
        transpiled_metrics = circuit_metrics(transpiled)
        sm = structural_metrics(spec)
        qasm_text = circuit_qasm(transpiled)
        qasm_path = circuits_dir / f"{spec.case_id}_k{k}_transpiled.qasm"
        if qasm_text is not None:
            qasm_path.write_text(qasm_text, encoding="utf-8")

        record = {
            "campaign_id": campaign["campaign_id"],
            "case_id": spec.case_id,
            "label": spec.label,
            "k": k,
            "purpose": item.get("purpose"),
            "backend": bname,
            "shots": shots,
            "optimization_level": optimization_level,
            "seed": seed,
            "runtime_options": runtime_options,
            "initial_layout": args.initial_layout,
            "a_exact": spec.a_exact,
            "expected_p_k": expected_amplified_probability(spec.a_exact, k),
            "transpile_seconds": transpile_seconds,
            "logical_metrics": logical_metrics.to_dict(),
            "transpiled_metrics": transpiled_metrics.to_dict(),
            "transpiled_two_qubit_depth": two_qubit_depth(transpiled),
            "structural_metrics": sm.to_dict(),
            "qasm_path": str(qasm_path) if qasm_text is not None else None,
            "backend_status": bstatus,
            "usage_before_submission": usage,
            "prepared_utc": datetime.now(timezone.utc).isoformat(),
            "submitted": False,
        }
        prepared.append({"record": record, "circuit": transpiled})

    print("Prepared circuits:")
    for item in prepared:
        record = item["record"]
        tm = record["transpiled_metrics"]
        print(
            f"  {record['case_id']} k={record['k']}: "
            f"depth={tm['depth']} twoq={tm['two_qubit_gate_count']} "
            f"2qdepth={record['transpiled_two_qubit_depth']} "
            f"expected_p={record['expected_p_k']:.6f}"
        )

    summary_path = campaign_out / "prepared_campaign.json"
    summary_path.write_text(
        json.dumps([item["record"] for item in prepared], indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print()
    print(f"Prepared metadata: {summary_path}")

    if not args.submit:
        print()
        print("No jobs submitted. Re-run with --submit to spend QPU credits.")
        return 0

    if not args.yes:
        response = input(
            "\nThis will submit jobs to IBM Quantum hardware and consume Runtime credits. "
            "Type SUBMIT to continue: "
        )
        if response.strip() != "SUBMIT":
            print("Submission cancelled.")
            return 1

    sampler = Sampler(backend)
    apply_runtime_options(sampler, runtime_options)
    submitted_records = []
    for item in prepared:
        record = item["record"]
        circuit = item["circuit"]
        print(f"Submitting {record['case_id']} k={record['k']} ...", end=" ", flush=True)
        job = sampler.run([circuit], shots=shots)
        job_id = job.job_id()
        record = dict(record)
        record.update(
            {
                "submitted": True,
                "job_id": job_id,
                "submitted_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
        pending_path = metadata_dir / f"pending_{record['case_id']}_k{record['k']}_{job_id}.json"
        pending_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        submitted_records.append(record)
        print(f"job_id={job_id}")

    submitted_path = campaign_out / "submitted_campaign.json"
    submitted_path.write_text(json.dumps(submitted_records, indent=2, sort_keys=True), encoding="utf-8")
    print()
    print(f"Submitted metadata: {submitted_path}")
    print(f"Pending records:     {metadata_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
