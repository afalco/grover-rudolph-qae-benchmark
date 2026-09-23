#!/usr/bin/env python3
"""Compatibility entry point for quantity-of-interest affine calibration."""

from __future__ import annotations

import runpy
from pathlib import Path

SCRIPT = Path(__file__).with_name("calibrate_gr_observable_affine.py")

if __name__ == "__main__":
    runpy.run_path(str(SCRIPT), run_name="__main__")
