"""Terminal reporting of unexpected failures (0.1.4, Section 5.1).

`report_internal` used to live in `ui/server.py`, which made plain modules import the HTTP module inside
functions (a dependency from `runs`, `model_step`, `history` and `mcp_ops` on `ui`). It lives here now; `ui/server.py`
re-exports it, and every plain module imports it from this file.
"""
from __future__ import annotations

import sys
import traceback


class ShuttingDown(Exception):
    """Shutdown has begun: the thing asked for is not admitted any more (0.1.4, 10.10). The HTTP layer answers
    503 `busy` with this text; the subclasses name the gate that refused."""

    def __init__(self) -> None:
        super().__init__("AgentWiz is shutting down")


def report_internal(route: str, exc: BaseException) -> None:
    """Terminal report for an unexpected failure: class and stack locations only, never messages."""
    lines = [f"[AgentWiz] internal error in {route}: {type(exc).__name__}"]
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if current is not exc:
            lines.append(f"  caused by {type(current).__name__}")
        for frame in traceback.extract_tb(current.__traceback__):
            lines.append(f"    {frame.filename}, line {frame.lineno}, in {frame.name}")
        current = current.__cause__ or current.__context__
    print("\n".join(lines), file=sys.stderr, flush=True)
