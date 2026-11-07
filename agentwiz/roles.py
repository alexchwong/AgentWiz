"""Roles: `roles.json`, the workspace's map from role names to profile names (0.1.4, Section 7).

A role is a name such as `fast` that a block step uses instead of a profile (D61). This module is the file
half only, mirroring `task_settings.py`: it reads, validates, builds and writes `<workspace>/roles.json`.
It is called from `state.py` under the state lock, with the roles revision, and knows nothing of HTTP.

    {"format_version": 1, "roles": {"fast": "lmstudio-qwen", "reasoning": "openrouter-big"}}

Version 2 adds definitions (description and local/cloud location), including unassigned roles.
Version 3 adds each definition's optional `task_defaults` (temperature and reasoning only): the
values a model call through that role inherits when its block neither sets nor resets them.
Version 1 assignments remain readable without imposing a new location policy. A missing file exposes
unconfigured starter definitions without writing to disk. Unknown top-level fields are kept. A damaged file
(`unreadable`, `invalid_json`, `invalid_field`, `newer_format`) makes roles read-only; AgentWiz never writes it.
Messages name locations and rules, never values. Standard library only.
"""
from __future__ import annotations

import copy
import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .workspace import (NAME_RULE, InvalidDocument, Problem, check_format_version, read_document, valid_name,
                        write_atomic)
from .task_settings import TaskSettingError, parse_defaults

FILE = "roles.json"
FORMAT_VERSION = 3
ROLE_LIMIT = 100
DEFINITION_KEYS = {"description", "location", "family", "task_defaults"}
DEFAULTS = {
    "fast": {"description": "Routine generation, extraction and classification", "location": "local"},
    "assistant": {"description": "Create, improve and explain Workshop blocks", "location": "local"},
    "reasoning": {"description": "Tasks needing a more capable reasoning model", "location": "local"},
    # Unassigned by default: no implicit profile or fallback. Editable like any other definition.
    "write_contract": {"description": "Create and revise reusable contracts", "location": "cloud",
                       "family": "Workshop"},
    "write_block_description": {"description": "Write and refine Workshop block descriptions", "location": "local",
                                "family": "Workshop"},
}


def profile_location(settings) -> str:
    """Classify without DNS or network access. Local means a loopback model server."""
    if settings.kind not in ("lmstudio", "openai_compat"):
        return "cloud"
    host = urlsplit(settings.base_url or "http://127.0.0.1:1234/v1").hostname
    if host == "localhost":
        return "local"
    try:
        if ipaddress.ip_address(host or "").is_loopback:
            return "local"
    except ValueError:
        pass
    return "cloud"


def parse_definitions(value: object) -> dict:
    if not isinstance(value, Mapping) or len(value) > ROLE_LIMIT:
        raise RolesError("definitions must be an object with at most 100 entries")
    out = {}
    for name, definition in value.items():
        if not valid_name(name) or not isinstance(definition, Mapping):
            raise RolesError("definitions must contain named role objects")
        if not {"description", "location"} <= set(definition) or set(definition) - DEFINITION_KEYS:
            raise RolesError(f"definitions.{name} requires description and location")
        if not isinstance(definition["description"], str) or len(definition["description"]) > 500:
            raise RolesError(f"definitions.{name}.description must be text of at most 500 characters")
        if definition["location"] not in ("local", "cloud"):
            raise RolesError(f"definitions.{name}.location must be local or cloud")
        family = definition.get("family", "General")
        if not isinstance(family, str) or not family.strip() or family != family.strip() or len(family) > 64:
            raise RolesError(f"definitions.{name}.family must be nonblank trimmed text of at most 64 characters")
        kept = dict(definition)
        try:
            defaults = parse_defaults(definition.get("task_defaults"), f"definitions.{name}.task_defaults")
        except TaskSettingError as exc:
            raise RolesError(str(exc)) from None
        if defaults:
            kept["task_defaults"] = defaults
        else:
            kept.pop("task_defaults", None)
        out[name] = kept
    return out


class RolesError(ValueError):
    """A role map that breaks a rule; the message names the location and the rule, never a value."""


@dataclass(frozen=True)
class RolesFile:
    problem: Problem | None
    exists: bool = False
    roles: Mapping[str, str] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    definitions: Mapping = field(default_factory=dict)


def empty_raw() -> dict:
    return {"format_version": 1, "roles": {}}


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
        return RolesFile(None, exists=False, raw=empty_raw(), definitions=copy.deepcopy(DEFAULTS))
    try:
        if check_format_version(raw) > FORMAT_VERSION:
            return RolesFile(Problem(FILE, "newer_format",
                                     f"written by a newer AgentWiz (format_version greater than {FORMAT_VERSION})"))
        if "roles" not in raw:
            raise InvalidDocument("roles is missing")
        roles = parse_roles(raw["roles"])
        definitions = {}
        if raw["format_version"] in (2, 3):
            definitions = parse_definitions(raw.get("definitions"))
            if not set(roles) <= set(definitions):
                raise RolesError("every assigned role requires a definition")
    except (InvalidDocument, RolesError) as exc:
        return RolesFile(Problem(FILE, "invalid_field", str(exc)))
    return RolesFile(None, exists=True, roles=roles, raw=raw, definitions=definitions)


def build_raw(previous: Mapping, roles: Mapping[str, str], definitions=None) -> dict:
    """The new file: the previous file's unknown top-level fields kept."""
    raw = copy.deepcopy(dict(previous))
    raw["format_version"] = FORMAT_VERSION
    raw["roles"] = dict(roles)
    kept = copy.deepcopy(previous.get("definitions", {}) if definitions is None else definitions)
    for name in roles:
        kept.setdefault(name, {"description": "", "location": "local"})
    raw["definitions"] = kept
    return raw


def write(folder: Path, raw: Mapping) -> None:
    write_atomic(Path(folder), FILE, raw)
