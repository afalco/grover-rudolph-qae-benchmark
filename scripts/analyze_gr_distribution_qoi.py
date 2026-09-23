#!/usr/bin/env python3
"""Compatibility entry point for quantity-of-interest analysis."""

from __future__ import annotations

import runpy
from pathlib import Path

SCRIPT = Path(__file__).with_name("analyze_gr_distribution_observables.py")

if __name__ == "__main__":
    runpy.run_path(str(SCRIPT), run_name="__main__")
