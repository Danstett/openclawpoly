#!/usr/bin/env python3
"""Entrypoint for Railpack/Railway auto-detection."""

import runpy

if __name__ == "__main__":
    runpy.run_module("fastloop_trader", run_name="__main__")
