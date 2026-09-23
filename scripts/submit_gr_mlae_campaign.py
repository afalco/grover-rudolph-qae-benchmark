#!/usr/bin/env python3
"""Prepare or submit Grover-Rudolph event-amplification MLAE campaigns."""

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

from grover_rudolph_qae_benchmark.circuits import expected_amplified_probability
from grover_rudolph_qae_benchmark.gr_state_preparation import (
    build_gr_event_amplification_circuit,
    marked_indices,
    marked_probability,
    spec_from_config,
)
from grover_rudolph_qae_benchmark.ibm import DEFAULT_ACCOUNT_FILE, qiskit_runtime_service
from grover_rudolph_qae_benchmark.metrics import circuit_metrics, two_qubit_depth


DEFAULT_CAMPAIGN = PROJECT_ROOT / "experiments" / "phase4_gr_affine_n4_mlae_k01_ibm_fez.json"


def load_qiskit_tools():
    try:
        from qiskit import transpile
        from qiskit_ibm_runtime import SamplerV2 as Sampler
    except ImportError as exc:
        raise SystemExit("qiskit and qiskit-ibm-runtime are required.") from exc
    return transpile, Sampler


def backend_name(backend) -> str:
    name = getattr(backend, "name", None)
    if callable(name):
        return str(name())
    return str(name or backend)


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
        remaining = max(0.0, float(limit) - float(consumed))
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
    total = int(float(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if hours:
        parts.append(f"{hours} h")
    if minutes or hours:
        parts.append(f"{minutes} min")
    parts.append(f"{secs} s")
    return " ".join(parts)


def circuit_qasm(circuit) -> str | None:
    try:
        from qiskit import qasm2

        return qasm2.dumps(circuit)
    except Exception:
        try:
            return circuit.qasm()
        except Exception:
            return None


def apply_runtime_options(sampler, runtime_options: dict[str, Any]) -> None:
    if not runtime_options:
        return
    try:
        sampler.options.update(**runtime_options)
        return
    except Exception:
        pass
    for key, value in runtime_options.items():
        target = getattr(sampler.options, key, None)
        if isinstance(value, dict) and target is not None:
            for subkey, subvalue in value.items():
                try:
                    setattr(target, subkey, subvalue)
                except Exception:
                    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare or submit GR MLAE campaigns.")
    parser.add_argument("--campaign", default=str(DEFAULT_CAMPAIGN))
    parser.add_argument("--account-file", default=str(DEFAULT_ACCOUNT_FILE))
    parser.add_argument("--name", default=None)
    parser.add_argument("--instance", default=None)
    parser.add_argument("--backend", default=None)
    parser.add_argument("--shots", type=int, default=None)
    parser.add_argument("--optimization-level", type=int, default=None)
    parser.add_argument("--out-dir", default="results/hardware")
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--yes", action="store_true")
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
        else int(campaign.get("optimization_level", 2))
    )
    implementation = str(campaign.get("implementation", "ucry"))
    reflection_method = str(campaign.get("reflection_method", "mcx"))
    event = str(campaign.get("event", "upper_half"))
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
    campaign_out = out_dir / campaign["campaign_id"] / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    circuits_dir = campaign_out / "circuits"
    pending_dir = campaign_out / "pending"
    circuits_dir.mkdir(parents=True, exist_ok=True)
    pending_dir.mkdir(parents=True, exist_ok=True)

    print(f"Campaign:       {campaign['campaign_id']}")
    print(f"Backend:        {bname}")
    print(f"Backend status: {bstatus.get('status_msg')}")
    print(f"Pending jobs:   {bstatus.get('pending_jobs')}")
    print(f"Shots/job:      {shots}")
    print(f"Opt level:      {optimization_level}")
    print(f"Event:          {event}")
    print(f"Reflection:     {reflection_method}")
    print(f"Submit mode:    {args.submit}")
    if runtime_options:
        print(f"Runtime opts:   {json.dumps(runtime_options, sort_keys=True)}")
    if usage:
        print(f"Runtime used:   {format_duration(usage.get('usage_consumed_seconds'))}")
        print(f"Runtime left:   {format_duration(usage.get('estimated_remaining_seconds'))}")
    print()

    prepared: list[dict[str, Any]] = []
    for case in campaign.get("selected_cases", []):
        spec = spec_from_config(case)
        a_exact = marked_probability(spec, event)
        marked = marked_indices(spec, event)
        for k in case.get("ks", []):
            k = int(k)
            logical = build_gr_event_amplification_circuit(
                spec,
                k,
                event=event,
                measure=True,
                implementation=implementation,
                reflection_method=reflection_method,
            )
            started = time.perf_counter()
            kwargs = {"backend": backend, "optimization_level": optimization_level}
            if seed is not None:
                kwargs["seed_transpiler"] = int(seed)
            transpiled = transpile(logical, **kwargs)
            transpile_seconds = time.perf_counter() - started

            qasm_path = circuits_dir / f"{spec.case_id}_{event}_k{k}_transpiled.qasm"
            qasm_text = circuit_qasm(transpiled)
            if qasm_text is not None:
                qasm_path.write_text(qasm_text, encoding="utf-8")
            logical_metrics = circuit_metrics(logical)
            transpiled_metrics = circuit_metrics(transpiled)
            record = {
                "campaign_id": campaign["campaign_id"],
                "campaign_type": "gr_event_mlae",
                "case_id": spec.case_id,
                "label": spec.label,
                "purpose": case.get("purpose"),
                "backend": bname,
                "shots": shots,
                "optimization_level": optimization_level,
                "implementation": implementation,
                "reflection_method": reflection_method,
                "event": event,
                "k": k,
                "seed": seed,
                "num_qubits": spec.num_qubits,
                "grid_points": spec.grid_points,
                "target_probabilities": list(spec.probabilities),
                "marked_indices": marked,
                "a_exact": a_exact,
                "expected_p_k": expected_amplified_probability(a_exact, k),
                "runtime_options": runtime_options,
                "transpile_seconds": transpile_seconds,
                "logical_metrics": logical_metrics.to_dict(),
                "transpiled_metrics": transpiled_metrics.to_dict(),
                "transpiled_two_qubit_depth": two_qubit_depth(transpiled),
                "qasm_path": str(qasm_path) if qasm_text else None,
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
            f"a={record['a_exact']:.6f} expected_p={record['expected_p_k']:.6f} "
            f"depth={tm['depth']} twoq={tm['two_qubit_gate_count']} "
            f"2qdepth={record['transpiled_two_qubit_depth']}"
        )

    prepared_path = campaign_out / "prepared_campaign.json"
    prepared_path.write_text(json.dumps([item["record"] for item in prepared], indent=2, sort_keys=True), encoding="utf-8")
    print()
    print(f"Prepared metadata: {prepared_path}")
    if not args.submit:
        print()
        print("No jobs submitted. Re-run with --submit to spend QPU credits.")
        return 0

    if not args.yes:
        response = input("\nThis will submit jobs to IBM Quantum hardware and consume Runtime credits. Type SUBMIT to continue: ")
        if response.strip() != "SUBMIT":
            print("Submission cancelled.")
            return 1

    sampler = Sampler(backend)
    apply_runtime_options(sampler, runtime_options)
    submitted = []
    for item in prepared:
        record = dict(item["record"])
        print(f"Submitting {record['case_id']} k={record['k']} ...", end=" ", flush=True)
        job = sampler.run([item["circuit"]], shots=shots)
        record.update({"submitted": True, "job_id": job.job_id(), "submitted_utc": datetime.now(timezone.utc).isoformat()})
        pending_path = pending_dir / f"pending_{record['case_id']}_k{record['k']}_{record['job_id']}.json"
        pending_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        submitted.append(record)
        print(f"job_id={record['job_id']}")

    submitted_path = campaign_out / "submitted_campaign.json"
    submitted_path.write_text(json.dumps(submitted, indent=2, sort_keys=True), encoding="utf-8")
    print()
    print(f"Submitted metadata: {submitted_path}")
    print(f"Pending records:     {pending_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
