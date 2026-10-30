"""Task settings: the type, its rules, and `task_settings.json` (techspec 0.1.2, Sections 7.1, 7.5).

Task settings belong to a request, not to a model profile: reasoning level, temperature, max tokens and
eight advanced sampling settings. Their names are modelSpell's `complete()` argument names everywhere
(page, file, request and state), so no mapping table exists.

`task_settings.json` changes only through this module, called from `state.py` under the state lock
with the task-settings revision. It is read with the strict file reader (`jsonio.decode_file`), not
the exact-number reader, because modelSpell takes Python numbers. Unknown top-level fields and unknown
`advanced` keys are kept when the file is rewritten, and are neither shown nor sent.

Messages name fields and rules, never values. Standard library only; knows nothing about HTTP.
"""
from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .workspace import InvalidDocument, Problem, check_format_version, read_document, write_atomic

REASONING_LEVELS = ("", "off", "low", "medium", "high")      # "" means the backend's default
ADVANCED = ("top_p", "top_k", "min_p", "repetition_penalty",
            "presence_penalty", "frequency_penalty", "seed", "stop")
SAFE_INTEGER = 2**53 - 1
FORMAT_VERSION = 1
FILE = "task_settings.json"

# The page's labels, used in AgentWiz's own refusal messages.
LABELS = {"reasoning": "reasoning", "temperature": "temperature", "max_tokens": "max tokens",
          "top_p": "top-p", "top_k": "top-k", "min_p": "min-p", "repetition_penalty": "repetition penalty",
          "presence_penalty": "presence penalty", "frequency_penalty": "frequency penalty", "seed": "seed",
          "stop": "stop sequences"}

_FLOATS = ("top_p", "min_p", "repetition_penalty", "presence_penalty", "frequency_penalty")


class TaskSettingError(ValueError):
    """A refused task setting: AgentWiz's own text, or a modelSpell pre-send message."""


@dataclass(frozen=True)
class TaskSettings:
    reasoning: str = ""
    temperature: int | float | None = None
    max_tokens: int | None = None
    advanced: Mapping[str, Any] = field(default_factory=dict)   # known keys only; set values only

    def set_names(self) -> frozenset[str]:
        """modelSpell argument names of the settings that are set."""
        names = set(self.advanced)
        if self.reasoning:
            names.add("reasoning")
        if self.temperature is not None:
            names.add("temperature")
        if self.max_tokens is not None:
            names.add("max_tokens")
        return frozenset(names)

    def values(self) -> dict:
        """The set values by modelSpell argument name (reasoning included when set)."""
        out: dict[str, Any] = {}
        if self.reasoning:
            out["reasoning"] = self.reasoning
        if self.temperature is not None:
            out["temperature"] = self.temperature
        if self.max_tokens is not None:
            out["max_tokens"] = self.max_tokens
        out.update({k: (list(v) if k == "stop" else v) for k, v in self.advanced.items()})
        return out

    def request_shape(self) -> dict:
        """The request's shape with blank settings absent (the `result` event's `task`)."""
        out: dict[str, Any] = {}
        if self.reasoning:
            out["reasoning"] = self.reasoning
        if self.temperature is not None:
            out["temperature"] = self.temperature
        if self.max_tokens is not None:
            out["max_tokens"] = self.max_tokens
        if self.advanced:
            out["advanced"] = {k: (list(v) if k == "stop" else v) for k, v in self.advanced.items()}
        return out

    def saved_shape(self) -> dict:
        """All four fields, with nulls: the shape of the file and of `/api/state`'s `task.saved`."""
        return {"reasoning": self.reasoning, "temperature": self.temperature, "max_tokens": self.max_tokens,
                "advanced": {k: (list(v) if k == "stop" else v) for k, v in self.advanced.items()}}


# --- field rules ---------------------------------------------------------------------------------
def _is_number(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return True
    return isinstance(v, float) and math.isfinite(v)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _check_value(name: str, value: Any, where: str) -> Any:
    """The value to keep (None for not set); raises TaskSettingError naming the field and rule."""
    if value is None:
        return None
    label = f"{where}{name}"
    if name == "reasoning":
        if value not in REASONING_LEVELS:
            raise TaskSettingError(f"{label} must be one of: (blank), off, low, medium, high")
        return value or None
    if name == "temperature":
        if not _is_number(value) or value < 0:
            raise TaskSettingError(f"{label} must be a finite number of at least 0")
        return value
    if name == "max_tokens":
        if not _is_int(value) or value < 1:
            raise TaskSettingError(f"{label} must be an integer of at least 1")
        return value
    if name in _FLOATS:
        if not _is_number(value):
            raise TaskSettingError(f"{label} must be a finite number")
        return value
    if name == "top_k":
        if not _is_int(value):
            raise TaskSettingError(f"{label} must be an integer")
        return value
    if name == "seed":
        if not _is_int(value) or abs(value) > SAFE_INTEGER:
            raise TaskSettingError(f"{label} must be an integer between -(2^53 - 1) and 2^53 - 1")
        return value
    if name == "stop":
        if not isinstance(value, list) or any(not isinstance(s, str) or s == "" for s in value):
            raise TaskSettingError(f"{label} must be a list of non-empty strings")
        return tuple(value) if value else None
    raise TaskSettingError(f"{label} is not a task setting")  # pragma: no cover - callers check names


def _parse(value: object, *, unknown_advanced: str, where: str = "") -> tuple[TaskSettings, dict]:
    """(settings, unknown advanced entries). unknown_advanced is "refuse" or "keep"."""
    if value is None:
        return TaskSettings(), {}
    if not isinstance(value, Mapping):
        raise TaskSettingError(f"{where.rstrip('.') or 'task'} must be an object")
    reasoning = _check_value("reasoning", value.get("reasoning"), where) or ""
    temperature = _check_value("temperature", value.get("temperature"), where)
    max_tokens = _check_value("max_tokens", value.get("max_tokens"), where)
    adv_in = value.get("advanced")
    if adv_in is None:
        adv_in = {}
    if not isinstance(adv_in, Mapping):
        raise TaskSettingError(f"{where}advanced must be an object")
    advanced: dict[str, Any] = {}
    unknown: dict[str, Any] = {}
    for key, item in adv_in.items():
        if key not in ADVANCED:
            if unknown_advanced == "refuse":
                raise TaskSettingError(f"{where}advanced has a key that is not a task setting")
            unknown[key] = item
            continue
        kept = _check_value(key, item, f"{where}advanced.")
        if kept is not None:
            advanced[key] = kept
    ordered = {k: advanced[k] for k in ADVANCED if k in advanced}
    return TaskSettings(reasoning, temperature, max_tokens, ordered), unknown


def parse_task(value: object) -> TaskSettings:
    """Task settings from a request body; raises TaskSettingError. `None` means nothing set."""
    return _parse(value, unknown_advanced="refuse")[0]


# --- the file --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class TaskFile:
    problem: Problem | None
    exists: bool = False
    settings: TaskSettings = field(default_factory=TaskSettings)
    raw: dict = field(default_factory=dict)


def empty_raw() -> dict:
    return {"format_version": FORMAT_VERSION, **TaskSettings().saved_shape()}


def read(folder: Path) -> TaskFile:
    """Read and validate `<folder>/task_settings.json` (Section 7.5)."""
    raw, problem = read_document(Path(folder) / FILE, FILE)
    if problem is not None:
        return TaskFile(problem)
    if raw is None:
        return TaskFile(None, exists=False, raw=empty_raw())
    try:
        if check_format_version(raw) > FORMAT_VERSION:
            return TaskFile(Problem(FILE, "newer_format",
                                    f"written by a newer AgentWiz (format_version greater than {FORMAT_VERSION})"))
        settings, _unknown = _parse(raw, unknown_advanced="keep")
    except (InvalidDocument, TaskSettingError) as exc:
        return TaskFile(Problem(FILE, "invalid_field", str(exc)))
    return TaskFile(None, exists=True, settings=settings, raw=raw)


def build_raw(previous: Mapping, settings: TaskSettings) -> dict:
    """The new file: the previous file's unknown fields and unknown `advanced` keys kept."""
    raw = copy.deepcopy(dict(previous))
    old_adv = raw.get("advanced") if isinstance(raw.get("advanced"), Mapping) else {}
    unknown = {k: v for k, v in old_adv.items() if k not in ADVANCED}
    shape = settings.saved_shape()
    raw["format_version"] = FORMAT_VERSION
    raw["reasoning"] = shape["reasoning"]
    raw["temperature"] = shape["temperature"]
    raw["max_tokens"] = shape["max_tokens"]
    raw["advanced"] = {**shape["advanced"], **unknown}
    return raw


def write(folder: Path, raw: Mapping[str, Any]) -> None:
    write_atomic(Path(folder), FILE, raw)
