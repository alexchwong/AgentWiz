"""Workspace profile copies and the shared defaults library. No HTTP.

Call mutations from state.py under its lock. Shared defaults additionally use the
permanent .profiles.lock, held only across a read/change/replace operation.
"""
from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from modelspell import ModelSpellError
from .model_call import _FIELDS, KINDS, REASONING_LEVELS, ModelSettings, SettingsError, settings_from_body
from .workspace import (FORMAT_VERSION, NAME_RULE, Problem, InvalidDocument as _Invalid,
                        check_format_version as _format_version, read_document, valid_name, write_atomic)

PROFILES_FILE = "profiles.json"
PROFILE_FIELDS = _FIELDS
_NEWER = f"written by a newer AgentWiz (format_version greater than {FORMAT_VERSION})"

def _check_profile(name: str, obj: Any) -> ModelSettings:
    where = f"profiles.{name}"
    if not isinstance(obj, dict):
        raise _Invalid(f"{where} must be an object")
    for f in PROFILE_FIELDS:
        if f not in obj:
            raise _Invalid(f"{where}.{f} is missing")
    if obj["kind"] not in KINDS:
        raise _Invalid(f"{where}.kind must be one of {', '.join(KINDS)}")
    for f in ("base_url", "model", "provider"):
        if not isinstance(obj[f], str):
            raise _Invalid(f"{where}.{f} must be a string")
    if obj["reasoning"] not in REASONING_LEVELS:
        raise _Invalid(f"{where}.reasoning must be one of: \"\", off, low, medium, high")
    mt = obj["max_tokens"]
    if mt is not None and not (isinstance(mt, int) and not isinstance(mt, bool) and mt >= 1):
        raise _Invalid(f"{where}.max_tokens must be null or an integer of at least 1")
    try:
        return settings_from_body(ModelSettings(), {f: obj[f] for f in PROFILE_FIELDS})
    except SettingsError as exc:  # AgentWiz's own messages; they name fields, never values
        raise _Invalid(f"{where}: {exc}") from None
    except ModelSpellError:
        raise _Invalid(f"{where}.base_url must start with http:// or https:// "
                       "(and is required for openai_compat)") from None


@dataclass(frozen=True)
class ProfilesFile:
    """The result of reading profiles.json."""
    problem: Problem | None
    exists: bool = False
    profiles: dict[str, ModelSettings] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)   # the file's object, for carrying unknown fields over


def empty_profiles_raw() -> dict:
    return {"format_version": FORMAT_VERSION, "profiles": {}}


def read_profiles(folder: Path) -> ProfilesFile:
    """Read and validate the whole of profiles.json (techspec 7.5.3)."""
    raw, problem = read_document(folder / PROFILES_FILE, str(folder / PROFILES_FILE))
    if problem is not None:
        return ProfilesFile(problem)
    if raw is None:
        return ProfilesFile(None, exists=False, raw=empty_profiles_raw())
    try:
        if _format_version(raw) > FORMAT_VERSION:
            return ProfilesFile(Problem(str(folder / PROFILES_FILE), "newer_format", _NEWER))
        if "profiles" not in raw or not isinstance(raw["profiles"], dict):
            raise _Invalid("profiles must be an object")
        profiles: dict[str, ModelSettings] = {}
        for name, obj in raw["profiles"].items():
            if not valid_name(name):
                raise _Invalid(f"profiles has a profile name that breaks the name rule ({NAME_RULE})")
            profiles[name] = _check_profile(name, obj)
    except _Invalid as exc:
        return ProfilesFile(Problem(str(folder / PROFILES_FILE), "invalid_field", str(exc)))
    return ProfilesFile(None, exists=True, profiles=profiles, raw=raw)


def profile_object(settings: ModelSettings, previous: Mapping | None = None) -> dict:
    """A profile's object: the previous object's unknown fields kept, the six fields replaced.

    Only the six ModelSettings fields are written; ModelSettings holds no key (P6).
    """
    out = dict(previous) if isinstance(previous, Mapping) else {}
    for f in PROFILE_FIELDS:
        out[f] = getattr(settings, f)
    return out



def write_profiles(folder: Path, raw: Mapping[str, Any]) -> None:
    write_atomic(folder, PROFILES_FILE, raw)


def fingerprint(folder: Path) -> tuple | None:
    """Missing and inaccessible files have stable, distinct signatures."""
    try:
        st = (folder / PROFILES_FILE).stat()
        return st.st_ino, st.st_mtime_ns, st.st_size
    except FileNotFoundError:
        return None
    except OSError as exc:
        return ("unreadable", exc.errno)


class Library:
    """Absolute defaults path; separate descriptors make flock work across instances too."""
    def __init__(self, folder: Path):
        self.path = Path(folder).resolve()

    @contextmanager
    def locked(self):
        self.path.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path / ".profiles.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield self
        finally:
            os.close(fd)
