#!/usr/bin/env python3
"""Measure each utility's worst case on bounded input (starter blocks addendum, 2.4). Prints seconds per case;
the target is under 1 second. Not a test: tests/test_utilities.py checks a generous bound."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from agentwiz import agent_tools  # noqa: E402
from test_utilities import _adversarial  # noqa: E402


def main() -> None:
    for name, arguments in _adversarial().items():
        started = time.monotonic()
        try:
            agent_tools.run_utility(name, arguments)
            outcome = "ok"
        except agent_tools.ToolError as exc:
            outcome = exc.code
        print(f"{name:22s} {time.monotonic() - started:6.3f} s  {outcome}")


if __name__ == "__main__":
    main()
