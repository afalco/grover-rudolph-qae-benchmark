#!/usr/bin/env python3
"""Prepare or submit quadrature-rule QAE/MLAE hardware campaigns."""

from __future__ import annotations

import argparse
import json
import math
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
    apply_uniformly_controlled_ry_stage,
    apply_zero_reflection,
)
from grover_rudolph_qae_benchmark.ibm import DEFAULT_ACCOUNT_FILE, qiskit_runtime_service
from grover_rudolph_qae_benchmark.metrics import circuit_metrics, two_qubit_depth


DEFAULT_CAMPAIGN = PROJECT_ROOT / "experiments" / "phase15b_quadrature_microcampaign_ibm_kingston.json"


def load_qiskit_tools():
    try:
        from qiskit import QuantumCircuit, transpile
        from qiskit_ibm_runtime import SamplerV2 as Sampler
    except ImportError as exc:
        raise SystemExit("qiskit and qiskit-ibm-runtime are required.") from exc
    return QuantumCircuit, transpile, Sampler


def resolve_path(path_text: str | Path) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def function_value(case: dict[str, Any], x: float) -> float:
    kind = str(case["kind"])
    if kind == "constant_quarter":
        value = 0.25
    elif kind == "sin2_half":
        value = math.sin(math.pi * x / 2.0) ** 2
    elif kind == "sin2":
        value = math.sin(math.pi * x) ** 2
    elif kind == "affine":
        value = float(case.get("offset", 0.0)) + float(case.get("slope", 1.0)) * x
    elif kind == "beta_bump":
        alpha = float(case.get("alpha", 2.0))
        beta = float(case.get("beta", 5.0))
        value = float(case.get("scale", 1.0)) * (x ** (alpha - 1.0)) * ((1.0 - x) ** (beta - 1.0))
    elif kind == "oscillatory":
        value = (
            float(case.get("base", 0.5))
            + float(case.get("sin_amp", 0.25)) * math.sin(2.0 * math.pi * x)
            + float(case.get("cos_amp", 0.10)) * math.cos(4.0 * math.pi * x)
        )
    else:
        raise ValueError(f"Unknown function kind: {kind}")
    return min(max(float(value), 0.0), 1.0)


def quadrature_nodes(rule: str, num_qubits: int) -> list[float]:
    grid_points = 2**num_qubits
    if rule == "left":
        return [idx / grid_points for idx in range(grid_points)]
    if rule == "midpoint":
        return [(idx + 0.5) / grid_points for idx in range(grid_points)]
    if rule == "right":
        return [(idx + 1.0) / grid_points for idx in range(grid_points)]
    raise ValueError(f"Direct hardware jobs support left/midpoint/right, not {rule!r}.")


def quadrature_values(case: dict[str, Any]) -> list[float]:
    return [function_value(case, x) for x in quadrature_nodes(str(case["rule"]), int(case["num_qubits"]))]


def quadrature_value(case: dict[str, Any]) -> float:
    values = quadrature_values(case)
    return sum(values) / len(values)


def true_integral(case: dict[str, Any], panels: int = 65536) -> float:
    if panels % 2:
        panels += 1
    h = 1.0 / panels
    total = function_value(case, 0.0) + function_value(case, 1.0)
    odd = 0.0
    even = 0.0
    for idx in range(1, panels):
        value = function_value(case, idx * h)
        if idx % 2:
            odd += value
        else:
            even += value
    return h * (total + 4.0 * odd + 2.0 * even) / 3.0


def build_preparation(case: dict[str, Any]):
    QuantumCircuit, _, _ = load_qiskit_tools()
    values = quadrature_values(case)
    num_qubits = int(case["num_qubits"])
    objective = num_qubits
    qc = QuantumCircuit(num_qubits + 1, name=f"quad_A_{case['case_id']}_n{num_qubits}_{case['rule']}")
    for qubit in range(num_qubits):
        qc.h(qubit)
    angles = [
        2.0 * math.asin(math.sqrt(min(max(float(value), 0.0), 1.0)))
        for value in values
    ]
    apply_uniformly_controlled_ry_stage(qc, objective, angles)
    return qc


def build_qae_circuit(case: dict[str, Any], k: int, *, measure: bool):
    QuantumCircuit, _, _ = load_qiskit_tools()
    num_qubits = int(case["num_qubits"])
    total_qubits = num_qubits + 1
    objective = num_qubits
    qc = QuantumCircuit(
        total_qubits,
        total_qubits if measure else 0,
        name=f"quad_{case['case_id']}_n{num_qubits}_{case['rule']}_k{k}",
    )
    preparation = build_preparation(case)
    inverse_preparation = preparation.inverse()
    qc.compose(preparation, inplace=True)
    for _ in range(k):
        qc.z(objective)
        qc.compose(inverse_preparation, inplace=True)
        apply_zero_reflection(qc, total_qubits, method="mcx")
        qc.compose(preparation, inplace=True)
    if measure:
        qc.measure(list(range(total_qubits)), list(range(total_qubits)))
    return qc


def backend_name(backend) -> str:
    name = getattr(backend, "name", None)
    return str(name() if callable(name) else name)


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
    parser = argparse.ArgumentParser(description="Prepare or submit quadrature QAE campaigns.")
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
    campaign_path = resolve_path(args.campaign)
    campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
    backend_id = args.backend or campaign["backend"]
    shots = args.shots or int(campaign.get("shots", 2048))
    optimization_level = (
        args.optimization_level
        if args.optimization_level is not None
        else int(campaign.get("optimization_level", 2))
    )
    seed = campaign.get("seed")
    runtime_options = campaign.get("runtime_options", {})

    _, transpile, Sampler = load_qiskit_tools()
    service = qiskit_runtime_service(
        account_file=args.account_file,
        name=args.name,
        instance=args.instance,
    )
    backend = service.backend(backend_id)
    bname = backend_name(backend)
    bstatus = backend_status(backend)
    usage = usage_summary(service)

    out_dir = resolve_path(args.out_dir)
    campaign_out = out_dir / campaign["campaign_id"] / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    circuits_dir = campaign_out / "circuits"
    pending_dir = campaign_out / "pending"
    circuits_dir.mkdir(parents=True, exist_ok=True)
    pending_dir.mkdir(parents=True, exist_ok=True)

    selected_jobs = campaign.get("selected_jobs", [])
    if not selected_jobs:
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

    prepared = []
    for case in selected_jobs:
        q_value = quadrature_value(case)
        exact = true_integral(case)
        for k in [int(value) for value in case.get("ks", [])]:
            logical = build_qae_circuit(case, k, measure=True)
            started = time.perf_counter()
            kwargs = {"backend": backend, "optimization_level": optimization_level}
            if seed is not None:
                kwargs["seed_transpiler"] = int(seed)
            transpiled = transpile(logical, **kwargs)
            transpile_seconds = time.perf_counter() - started
            logical_metrics = circuit_metrics(logical)
            transpiled_metrics = circuit_metrics(transpiled)
            qasm_path = circuits_dir / f"{case['case_id']}_n{case['num_qubits']}_{case['rule']}_k{k}.qasm"
            qasm_text = circuit_qasm(transpiled)
            if qasm_text is not None:
                qasm_path.write_text(qasm_text, encoding="utf-8")
            group_id = f"{case['case_id']}_n{case['num_qubits']}_{case['rule']}"
            record = {
                "campaign_id": campaign["campaign_id"],
                "campaign_type": "quadrature_qae_mlae",
                "group_id": group_id,
                "case_id": case["case_id"],
                "family": case.get("family"),
                "function_kind": case["kind"],
                "num_qubits": int(case["num_qubits"]),
                "grid_points": 2 ** int(case["num_qubits"]),
                "rule": case["rule"],
                "k": k,
                "ks": [int(value) for value in case.get("ks", [])],
                "purpose": case.get("purpose"),
                "backend": bname,
                "shots": shots,
                "optimization_level": optimization_level,
                "seed": seed,
                "runtime_options": runtime_options,
                "quadrature_value": q_value,
                "true_integral": exact,
                "discretization_error": abs(q_value - exact),
                "expected_p_k": expected_amplified_probability(q_value, k),
                "logical_metrics": logical_metrics.to_dict(),
                "transpiled_metrics": transpiled_metrics.to_dict(),
                "transpiled_two_qubit_depth": two_qubit_depth(transpiled),
                "transpile_seconds": transpile_seconds,
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
            f"  {record['group_id']} k={record['k']}: "
            f"q={record['quadrature_value']:.6f} true={record['true_integral']:.6f} "
            f"depth={tm['depth']} twoq={tm['two_qubit_gate_count']} "
            f"2qdepth={record['transpiled_two_qubit_depth']} "
            f"expected_p={record['expected_p_k']:.6f}"
        )

    prepared_path = campaign_out / "prepared_campaign.json"
    prepared_path.write_text(
        json.dumps([item["record"] for item in prepared], indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print()
    print(f"Prepared metadata: {prepared_path}")

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
        record = dict(item["record"])
        print(f"Submitting {record['group_id']} k={record['k']} ...", end=" ", flush=True)
        try:
            job = sampler.run([item["circuit"]], shots=shots)
        except Exception as exc:
            record.update(
                {
                    "submission_failed": True,
                    "submission_error": str(exc),
                    "submission_failed_utc": datetime.now(timezone.utc).isoformat(),
                }
            )
            failed_path = campaign_out / "submission_failed_campaign.json"
            failed_path.write_text(
                json.dumps(
                    {"failed_record": record, "submitted_records": submitted_records},
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            print("FAILED")
            print(f"Submission failed before a job id was recorded: {exc}")
            print(f"Failure metadata: {failed_path}")
            return 2
        job_id = job.job_id()
        record.update(
            {
                "submitted": True,
                "job_id": job_id,
                "submitted_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
        pending_path = pending_dir / f"pending_{record['group_id']}_k{record['k']}_{job_id}.json"
        pending_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        submitted_records.append(record)
        print(f"job_id={job_id}")

    submitted_path = campaign_out / "submitted_campaign.json"
    submitted_path.write_text(json.dumps(submitted_records, indent=2, sort_keys=True), encoding="utf-8")
    print()
    print(f"Submitted metadata: {submitted_path}")
    print(f"Pending records:     {pending_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
