#!/usr/bin/env python3
"""Show IBM Quantum Runtime usage for the configured account.

The script reads the IBM Quantum account configuration from
~/.qiskit/qiskit-ibm.json by default. It never prints the API token.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


DEFAULT_ACCOUNT_FILE = Path.home() / ".qiskit" / "qiskit-ibm.json"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE pairs from a simple .env file without overwriting env vars."""
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def format_duration(seconds: Any) -> str:
    """Return a compact human-readable duration."""
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


def load_runtime_service():
    try:
        from qiskit_ibm_runtime import QiskitRuntimeService
    except ImportError as exc:
        raise SystemExit(
            "qiskit-ibm-runtime is not installed.\n"
            "Install it with:\n\n"
            "  python3 -m pip install qiskit-ibm-runtime\n"
        ) from exc
    return QiskitRuntimeService


def usage_remaining_seconds(usage: dict[str, Any]) -> float | None:
    consumed = usage.get("usage_consumed_seconds")
    limit = usage.get("usage_limit_seconds")
    allocation = usage.get("usage_allocation_seconds")

    ceiling = limit if limit is not None else allocation
    if consumed is None or ceiling is None:
        return None

    try:
        return max(0.0, float(ceiling) - float(consumed))
    except (TypeError, ValueError):
        return None


def print_usage(usage: dict[str, Any], *, heading: str | None = None) -> None:
    if heading:
        print(heading)
        print("-" * len(heading))

    instance_id = usage.get("instance_id", "not reported")
    plan_id = usage.get("plan_id", "not reported")
    consumed = usage.get("usage_consumed_seconds")
    limit = usage.get("usage_limit_seconds")
    allocation = usage.get("usage_allocation_seconds")
    remaining = usage_remaining_seconds(usage)

    print(f"Instance:              {instance_id}")
    print(f"Plan:                  {plan_id}")
    print(f"Consumed:              {format_duration(consumed)}")
    print(f"Usage limit:           {format_duration(limit)}")
    print(f"Usage allocation:      {format_duration(allocation)}")
    print(f"Estimated remaining:   {format_duration(remaining)}")

    if usage.get("usage_limit_reached") is not None:
        print(f"Usage limit reached:   {usage['usage_limit_reached']}")
    if usage.get("time_available_at"):
        print(f"Time available at:     {usage['time_available_at']}")

    period = usage.get("usage_period") or {}
    if period:
        print(f"Usage period start:    {period.get('start_time', 'not reported')}")
        print(f"Usage period end:      {period.get('end_time', 'not reported')}")


def main() -> int:
    load_env_file(DEFAULT_ENV_FILE)

    parser = argparse.ArgumentParser(
        description="Show IBM Quantum Runtime usage for the configured account."
    )
    parser.add_argument(
        "--account-file",
        default=str(DEFAULT_ACCOUNT_FILE),
        help="Path to qiskit-ibm.json (default: ~/.qiskit/qiskit-ibm.json).",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Saved account name to use, if several accounts are stored.",
    )
    parser.add_argument(
        "--instance",
        default=os.environ.get("IBM_QUANTUM_INSTANCE"),
        help=(
            "Specific IBM Quantum instance CRN or service name. "
            "Defaults to IBM_QUANTUM_INSTANCE from .env or the environment."
        ),
    )
    parser.add_argument(
        "--all-instances",
        action="store_true",
        help="Query usage for every instance visible to the account.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print raw usage JSON instead of a formatted summary.",
    )
    args = parser.parse_args()

    account_file = Path(args.account_file).expanduser()
    if not account_file.exists():
        raise SystemExit(f"Account file not found: {account_file}")

    QiskitRuntimeService = load_runtime_service()

    try:
        service = QiskitRuntimeService(
            filename=str(account_file),
            name=args.name,
            instance=args.instance,
        )
    except TypeError:
        service = QiskitRuntimeService(
            channel="ibm_quantum_platform",
            filename=str(account_file),
            name=args.name,
            instance=args.instance,
        )

    if args.all_instances:
        instances = service.instances()
        results: list[dict[str, Any]] = []
        for item in instances:
            instance = item.get("crn") or item.get("name")
            if not instance:
                continue
            instance_service = QiskitRuntimeService(
                filename=str(account_file),
                name=args.name,
                instance=instance,
            )
            usage = instance_service.usage()
            usage["_instance_record"] = {
                key: value for key, value in item.items() if key != "token"
            }
            results.append(usage)

        if args.json:
            print(json.dumps(results, indent=2, sort_keys=True))
        else:
            for idx, usage in enumerate(results):
                if idx:
                    print()
                print_usage(usage, heading=f"Instance {idx + 1}")
        return 0

    usage = service.usage()
    if args.json:
        print(json.dumps(usage, indent=2, sort_keys=True))
    else:
        active_instance = None
        try:
            active_instance = service.active_instance()
        except Exception:
            active_instance = None
        if active_instance:
            print(f"Active instance CRN:   {active_instance}")
        print_usage(usage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
