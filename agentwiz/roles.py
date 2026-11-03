"""Roles: `roles.json`, the workspace's map from role names to profile names (0.1.4, Section 7).

A role is a name such as `fast` that a block step uses instead of a profile (D61). This module is the file
half only, mirroring `task_settings.py`: it reads, validates, builds and writes `<workspace>/roles.json`.
It is called from `state.py` under the state lock, with the roles revision, and knows nothing of HTTP.

    {"format_version": 1, "roles": {"fast": "lmstudio-qwen", "reasoning": "openrouter-big"}}

Unknown top-level fields are kept when the file is rewritten. A missing file means no roles. A damaged file
(`unreadable`, `invalid_json`, `invalid_field`, `newer_format`) makes roles read-only; AgentWiz never writes it.
Messages name locations and rules, never values. Standard library only.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .workspace import (NAME_RULE, InvalidDocument, Problem, check_format_version, read_document, valid_name,
                        write_atomic)

FILE = "roles.json"
FORMAT_VERSION = 1
ROLE_LIMIT = 100


class RolesError(ValueError):
    """A role map that breaks a rule; the message names the location and the rule, never a value."""


@dataclass(frozen=True)
class RolesFile:
    problem: Problem | None
    exists: bool = False
    roles: Mapping[str, str] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


def empty_raw() -> dict:
    return {"format_version": FORMAT_VERSION, "roles": {}}


def parse_roles(value: object) -> dict[str, str]:
    """The validated role map. Raises RolesError naming the location and rule."""
    if not isinstance(value, Mapping):
        raise RolesError("roles must be an object")
    if len(value) > ROLE_LIMIT:
        raise RolesError(f"roles may hold at most {ROLE_LIMIT} entries")
    out: dict[str, str] = {}
    for role, profile in value.items():
        if not valid_name(role):
            raise RolesError(f"roles has a role name that does not follow the name rule ({NAME_RULE})")
        if not valid_name(profile):
            raise RolesError(f"roles.{role} must be a profile name ({NAME_RULE})")
        out[role] = profile
    return out


def read(folder: Path) -> RolesFile:
    """Read and validate `<folder>/roles.json` (Section 7.1)."""
    raw, problem = read_document(Path(folder) / FILE, FILE)
    if problem is not None:
        return RolesFile(problem)
    if raw is None:
        return RolesFile(None, exists=False, raw=empty_raw())
    try:
        if check_format_version(raw) > FORMAT_VERSION:
            return RolesFile(Problem(FILE, "newer_format",
                                     f"written by a newer AgentWiz (format_version greater than {FORMAT_VERSION})"))
        if "roles" not in raw:
            raise InvalidDocument("roles is missing")
        roles = parse_roles(raw["roles"])
    except (InvalidDocument, RolesError) as exc:
        return RolesFile(Problem(FILE, "invalid_field", str(exc)))
    return RolesFile(None, exists=True, roles=roles, raw=raw)


def build_raw(previous: Mapping, roles: Mapping[str, str]) -> dict:
    """The new file: the previous file's unknown top-level fields kept."""
    raw = copy.deepcopy(dict(previous))
    raw["format_version"] = FORMAT_VERSION
    raw["roles"] = dict(roles)
    return raw


def write(folder: Path, raw: Mapping) -> None:
    write_atomic(Path(folder), FILE, raw)
