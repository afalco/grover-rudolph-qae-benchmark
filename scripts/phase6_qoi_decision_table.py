#!/usr/bin/env python3
"""Compatibility entry point for Phase 6 quantity-of-interest decisions."""

from __future__ import annotations

import runpy
from pathlib import Path

SCRIPT = Path(__file__).with_name("phase6_observable_decision_table.py")

if __name__ == "__main__":
    runpy.run_path(str(SCRIPT), run_name="__main__")
