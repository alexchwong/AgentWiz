"""The scriptSpell adapter and the typed-data bridge (0.2.2, 10.2-10.4).

One adapter serves Workshop Test, standalone block runs, workflow nodes and agent children: every `python` step
reaches scriptSpell through `run` here, on the run's own thread. AgentWiz adds no source, values or guest
exception text to records or messages; a step result carries scriptSpell's outcome, its safe issue codes and
lines, and the engine. scriptSpell is a language-level restriction, not an OS sandbox.

Knows nothing of HTTP. Uses only `scriptspell.__all__` and `__version__`.
"""
from __future__ import annotations

import threading
from decimal import Decimal
from typing import Any, Callable, Mapping

ENGINE_NAME = "scriptspell"
# Outcomes and what a step makes of them (10.2). Anything not listed is `unknown_outcome`.
SAFE_ISSUES = ("invalid_inputs", "invalid_result", "source_rejected")
FIXED = ("runtime_error", "limit_exceeded", "deadline")
UNUSABLE = ("dependency_unavailable", "platform_unavailable")
ISSUE_LIMIT = 20

_profile_lock = threading.Lock()
_profile: dict | None = None


def _installed() -> dict:
    """scriptSpell's profile, read once per process: the installed packages do not change while it runs."""
    global _profile
    with _profile_lock:
        if _profile is None:
            import scriptspell
            found = scriptspell.profile()
            _profile = {"profile": found["profile"], "codec": found["codec"], "version": scriptspell.__version__}
        return dict(_profile)


def engine() -> dict:
    """The `engine` object written into a python step when it is saved (10.1)."""
    found = _installed()
    return {"name": ENGINE_NAME, "profile": found["profile"], "codec": found["codec"]}


def engine_matches(value: Mapping) -> bool:
    try:
        return dict(value) == engine()
    except Exception:  # noqa: BLE001 - an unusable installation never matches
        return False


def version() -> str:
    return _installed()["version"]


def float_path(value: Any, path: str = "$") -> str | None:
    """The location of the first float at any depth, or None (10.3: floats are rejected)."""
    if type(value) is float:
        return path
    if isinstance(value, dict):
        for key, item in value.items():
            found = float_path(item, f"{path}.{key}")
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = float_path(item, f"{path}[{index}]")
            if found:
                return found
    return None


def _issues(result: Mapping) -> list[dict]:
    """scriptSpell's safe issue codes and lines only: never a symbol, value or message."""
    out = []
    for issue in (result.get("issues") or [])[:ISSUE_LIMIT]:
        if not isinstance(issue, Mapping):
            continue
        code = issue.get("code")
        line = issue.get("line")
        out.append({"code": code if isinstance(code, str) else "issue",
                    "line": line if type(line) is int else None})
    return out


class CleanupFailed(Exception):
    """scriptSpell could not confirm a worker's cleanup; the Python budget is closed for this process."""


def run(source: str, values: Mapping[str, Any], output_names: tuple[str, ...], *, token: Any,
        on_cleanup_failed: Callable[[], None]) -> dict:
    """Run one Python step to its end. Returns {"outcome": "ok"|"failed"|"stopped", "failure", "operation",
    "values"}; `values` only when ok. Waits for scriptSpell to return before the caller releases its slot."""
    import scriptspell
    cancel = threading.Event()
    token.on_cancel(cancel.set)            # set from any thread; scriptSpell polls it and kills the worker
    operation = {"engine": engine(), "scriptspell_version": scriptspell.__version__, "outcome": None,
                 "issues": [], "elapsed_s": None}
    try:
        result = scriptspell.run(source, dict(values), list(output_names), limits=scriptspell.Limits(),
                                 cancel=cancel)
    except RuntimeError as exc:
        if str(exc) == "cleanup_failed":
            on_cleanup_failed()
            operation["outcome"] = "cleanup_failed"
            return {"outcome": "failed", "failure": "cleanup_failed", "operation": operation}
        raise
    outcome = result.get("outcome") if isinstance(result, Mapping) else None
    operation["outcome"] = outcome if isinstance(outcome, str) else "unknown_outcome"
    elapsed = result.get("elapsed") if isinstance(result, Mapping) else None
    if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool):
        operation["elapsed_s"] = round(float(elapsed), 3)
    if outcome == "ok":
        produced = result.get("values")
        if not isinstance(produced, dict) or set(produced) != set(output_names):
            operation["outcome"] = "invalid_result"
            return {"outcome": "failed", "failure": "invalid_result", "operation": operation}
        found = float_path(produced)
        if found is not None:
            operation["float_path"] = "outputs" + found[1:]
            return {"outcome": "failed", "failure": "float_output", "operation": operation}
        return {"outcome": "ok", "failure": None, "operation": operation, "values": produced}
    if outcome == "cancelled":
        return {"outcome": "stopped", "failure": None, "operation": operation}
    if outcome in SAFE_ISSUES:
        operation["issues"] = _issues(result)
        return {"outcome": "failed", "failure": outcome, "operation": operation}
    if outcome in FIXED or outcome in UNUSABLE or outcome == "worker_crash":
        return {"outcome": "failed", "failure": outcome, "operation": operation}
    if outcome == "cleanup_failed":
        on_cleanup_failed()
        return {"outcome": "failed", "failure": "cleanup_failed", "operation": operation}
    operation["outcome"] = "unknown_outcome"
    return {"outcome": "failed", "failure": "unknown_outcome", "operation": operation}


def is_exact(value: Any) -> bool:
    """Whether `value` holds only types the bridge passes (10.3): str, bool, None, int, Decimal, lists, objects."""
    if value is None or type(value) in (str, bool, int, Decimal):
        return True
    if isinstance(value, list):
        return all(is_exact(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and is_exact(v) for k, v in value.items())
    return False
