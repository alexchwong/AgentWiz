#!/usr/bin/env python3
"""Checkout entry point for the packaged tutorial stand-in."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agentwiz.tutorial_support.mcp import *  # noqa: F403,F401

if __name__ == "__main__":
    main()  # noqa: F405
