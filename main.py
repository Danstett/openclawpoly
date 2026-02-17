#!/usr/bin/env python3
"""Entrypoint for Railpack/Railway auto-detection."""

import os
import sys

# Enable loop mode by default when deployed (Railway sets RAILWAY_ENVIRONMENT)
if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("LOOP"):
    os.environ.setdefault("LOOP", "1")
    os.environ.setdefault("LOOP_INTERVAL", "15")  # 15s for better coverage in final minute

# Run the trading script
if __name__ == "__main__":
    import runpy
    runpy.run_module("fastloop_trader", run_name="__main__")
