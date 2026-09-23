"""IBM Quantum account helpers."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_ACCOUNT_FILE = Path.home() / ".qiskit" / "qiskit-ibm.json"


def load_env_file(path: Path = DEFAULT_ENV_FILE) -> None:
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


def qiskit_runtime_service(
    *,
    account_file: str | Path = DEFAULT_ACCOUNT_FILE,
    name: str | None = None,
    instance: str | None = None,
):
    """Create a QiskitRuntimeService from saved account credentials."""
    load_env_file()
    try:
        from qiskit_ibm_runtime import QiskitRuntimeService
    except ImportError as exc:
        raise SystemExit(
            "qiskit-ibm-runtime is not installed.\n"
            "Install it with:\n\n"
            "  python3 -m pip install qiskit-ibm-runtime\n"
        ) from exc

    account_path = Path(account_file).expanduser()
    selected_instance = instance or os.environ.get("IBM_QUANTUM_INSTANCE")
    try:
        return QiskitRuntimeService(
            filename=str(account_path),
            name=name,
            instance=selected_instance,
        )
    except TypeError:
        return QiskitRuntimeService(
            channel="ibm_quantum_platform",
            filename=str(account_path),
            name=name,
            instance=selected_instance,
        )

