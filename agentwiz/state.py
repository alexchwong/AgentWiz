"""Process state: settings, revision, keys, profiles, task settings, request budget (techspec 0.1.1,
7.6; 0.1.2, 7.5, 10.1; R0-2, R0-10, R0-11, R1-3, R1-4, P11).

Settings belong to the process and every tab shares them. Each change increments a revision, and
every completion, save and profile operation states the revision its page was showing, so a tab can
never act on settings or profiles it is not showing. Three revisions exist, and a change to one never
makes another stale: the configuration revision (settings, keys, profiles), the task-settings
revision (the workspace's saved task settings) and the MCP revision (the MCP library and tokens).
Each saved contract has a revision of its own (techspec 0.1.3, 7.8), so a contract change never makes a
settings dialog stale either.

The state lock is held to compare, snapshot, view and apply, and across the read-validate-change-
replace of workspace profiles (through profiles.py), so one profile change can never overwrite another.
It is never held during a network call. It is not reentrant, so no method holding it calls another
public method.
"""
from __future__ import annotations

import contextlib
import copy
import dataclasses
import functools
import os
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import modelspell
from modelspell import Endpoint

from . import workshop, blocks, checks, contracts, history, mcp_client, mcp_ops, roles, runs, script_step, writers
from . import mcp_library as ml
from . import profiles as pf
from . import task_settings as ts
from . import workspace as wsmod
from .model_call import ModelSettings, RequestSnapshot, SettingsError, settings_from_body, validate_key
from .errors import report_internal
from .workspace import Problem, Workspace


SHUTDOWN_GRACE_S = 5.0


def before_register_writer(label: str) -> None:
    """Does nothing in shipped code; tests make it block just before the writer barrier's lock is taken (10.10)."""


def before_write(label: str) -> None:
    """Does nothing in shipped code; tests make it block inside a registered writer, immediately before the first
    filesystem mutation of a configuration, profile, task settings, contract, MCP, history-switch or role save,
    or a run deletion (10.10)."""


def _writes(label: str):
    """A ProcessState method that writes the workspace: registered with the writer barrier before any other lock
    is taken (503 when shutdown has begun), released after the last possible mutation (10.10)."""
    def decorate(fn):
        @functools.wraps(fn)
        def inner(self, *args, **kwargs):
            with self.writing(label):
                before_write(label)
                return fn(self, *args, **kwargs)
        return inner
    return decorate


class StaleRevision(Exception):
    def __init__(self, current: int):
        super().__init__("settings were changed elsewhere")
        self.current = current


class ReadOnly(Exception):
    """Something cannot be changed until it is repaired (and, for workspace files, AgentWiz restarts).
    The message names the file and the rule."""

    def __init__(self, problems: list[Problem], whole_workspace: bool, what: str = "profiles"):
        first = problems[0] if problems else None
        what = "the workspace is read-only" if whole_workspace else f"{what} are read-only"
        super().__init__(f"{what}: {first.file}: {first.message}" if first else what)
        self.problems = problems
        self.whole_workspace = whole_workspace


class DefaultConflict(Exception):
    """Export requires explicit overwrite approval."""


class NoSuchServer(Exception):
    def __init__(self):
        super().__init__("no such MCP server")


class NoSuchProfile(Exception):
    def __init__(self):
        super().__init__("no such profile")


class NoSuchBlock(Exception):
    def __init__(self):
        super().__init__("no such block")


class BlockUnusable(Exception):
    """The block has a problem and cannot be run (409 `block_unusable`); `problem` is its BlockProblem."""

    def __init__(self, problem):
        super().__init__("the block has a problem and cannot be run")
        self.problem = problem


class RoleProfileMissing(Exception):
    """A roles save that names a profile the workspace does not have. Names the role, never the profile (7.3)."""

    def __init__(self, role: str):
        super().__init__(f"role {role} names a profile that does not exist")
        self.role = role


class RoleUnresolved(Exception):
    """A role a block step uses cannot be resolved to a profile (7.4). Names the role, never a key."""

    MESSAGES = {"undefined": "role {role} is not defined",
                "no_profile": "role {role} names a profile that does not exist",
                "no_model": "role {role}'s profile has no model",
                "unconfigured": "role {role} is not configured; open Settings → Roles",
                "location": "role {role}'s profile is incompatible with its local/cloud location"}

    def __init__(self, role: str, reason: str):
        super().__init__(self.MESSAGES[reason].format(role=role))
        self.role = role
        self.reason = reason


class WriteFailed(Exception):
    def __init__(self, file: str, strerror: str):
        super().__init__(f"could not write {file}: {strerror}")
        self.file = file
        self.strerror = strerror


class RequestBudget:
    """At most `limit` model requests in progress; never queues."""

    def __init__(self, limit: int = 8):
        self.limit = limit
        self._in_flight = 0
        self._lock = threading.Lock()
        self.closed = False

    def close(self) -> None:
        """Refuse every later `try_acquire` (shutdown, 10.10)."""
        with self._lock:
            self.closed = True

    @property
    def in_flight(self) -> int:
        return self._in_flight

    def try_acquire(self) -> bool:
        with self._lock:
            if self.closed or self._in_flight >= self.limit:
                return False
            self._in_flight += 1
            return True

    def release(self) -> None:
        with self._lock:
            if self._in_flight <= 0:
                raise RuntimeError("request budget released more often than acquired")
            self._in_flight -= 1


def _endpoint(settings: ModelSettings, api_key: str | None = None) -> Endpoint:
    return Endpoint(settings.kind, settings.base_url or None, api_key=api_key)


def _name(value: object, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not wsmod.valid_name(value):
        raise SettingsError(f"profile name must be {wsmod.NAME_RULE}")
    return value  # type: ignore[return-value]


def supported_view(kind: str) -> dict:
    """For each reasoning level the kind takes, the setting names modelSpell sends (Section 10.2)."""
    levels = ("",) if kind == "openai_compat" else ts.REASONING_LEVELS
    return {level: sorted(modelspell.supported_settings(kind, level or None) - {"reasoning", "provider"})
            for level in levels}


@dataclasses.dataclass(frozen=True)
class RoleCapture:
    """One role as a model call uses it: the destination snapshot and the role's saved defaults, taken
    under one lock acquisition so the two can never come from different versions of `roles.json`."""

    role: str
    profile: str
    snapshot: RequestSnapshot
    defaults: Mapping[str, object]      # detached copy; empty means the backend's own defaults
    revision: int                       # the roles revision the two were read at


class ProcessState:
    def __init__(self, workspace: Workspace, request_limit: int = 8, *, profiles_dir: Path,
                 mcp_servers_dir: Path | None = None, servers, config_path=None):
        self.budget = RequestBudget(request_limit)
        self.workspace = workspace
        self._lock = threading.Lock()
        self.servers = MappingProxyType(dict(servers))
        self.config_path = config_path
        self._settings = ModelSettings()
        self._revision = 1
        self._keys: dict[tuple[str, str], str] = {}
        self._profiles: dict[str, ModelSettings] = {}
        self._active: str | None = None
        self._profile_problem: Problem | None = None
        self.library = pf.Library(profiles_dir)
        self.profiles_path = workspace.path / "model-profiles"
        self._defaults = pf.ProfilesFile(None)
        self._default_signature = None
        with self._lock:
            with self.library.locked():
                self._refresh_defaults_locked(initial=True)
                if workspace.created and not (self.profiles_path / pf.PROFILES_FILE).exists():
                    self.profiles_path.mkdir(parents=True, exist_ok=True)
                    pf.write_profiles(self.profiles_path, self._defaults.raw or pf.empty_profiles_raw())
            loaded = pf.read_profiles(self.profiles_path)
            self._profile_problem = loaded.problem
            self._profiles = dict(loaded.profiles)
            raw, problem = wsmod.read_workspace(workspace)
            self._active = raw.get("active_profile") if raw and problem is None else None
            self._unresolved = self._active if self._active not in self._profiles else None
            if self._unresolved:
                self._active = None
            if self._active is not None:
                self._settings = self._profiles[self._active]
            loaded_task = ts.read(workspace.path)
            self._task_problem: Problem | None = loaded_task.problem
            self._task_saved = loaded_task.settings
            self._task_revision = 1
            self.contracts = contracts.ContractStore(workspace.path)
            self.contracts.rescan()
            # Roles (techspec 0.1.4, 7): the map, its problem and a revision of their own (P11).
            loaded_roles = roles.read(workspace.path)
            self._roles_problem: Problem | None = loaded_roles.problem
            self._roles = dict(loaded_roles.roles)
            self._role_definitions = dict(loaded_roles.definitions)
            self._roles_revision = 1
            # Block definitions (8.8) and the script-process budget (11.4).
            self.blocks = blocks.BlockStore(workspace.path, self.contracts)
            self.scripts = RequestBudget(4)
            self.writers = writers.WriterBarrier()
            self.workshop_operations = workshop.Operations()
            self.cleanups = script_step.Registry()            # cleanup owners and running script steps
            self._deliveries: set = set()                       # objects with interrupt(), under their own lock
            self._deliveries_lock = threading.Lock()
            self._shutdown_lock = threading.Lock()              # leaf: decides the first call
            self._shutdown_started = False
            # Run history (techspec 0.1.3, 10): the switch, its own revision, and every credential held.
            self._save_runs = bool(raw.get("save_runs")) if raw and problem is None and not workspace.read_only else False
            self._history_revision = 1
            self._credentials: set[str] = set()      # typed keys and tokens, kept until the process exits
            if self.servers:
                self._credentials.update(s.api_key for s in self.servers.values() if s.api_key)
            self._env_names: set[str] = {"OPENROUTER_API_KEY"}
            self.store = runs.AttemptStore()
            self.history = history.Writer(workspace.path, self.store, self.credentials)
            # The MCP library (Section 9.1), its revision and typed tokens (bound to a URL).
            self.mcp_library = ml.Library(mcp_servers_dir if mcp_servers_dir is not None else Path("./mcp_servers"))
            self.mcp_library.ensure()
            self.mcp_client = mcp_client.MCPClient()
            self.mcp_ops = mcp_ops.Ops(self.mcp_client)
            self._mcp_revision = 1
            self._mcp_tokens: dict[str, str] = {}
            self._mcp_signature = None
            self._mcp = ml.LibraryFile(None)
            with self.mcp_library.locked():
                self._refresh_mcp_locked(initial=True)
            from .workshop_service import Service
            self.workshop = Service(self)

    def _refresh_defaults_locked(self, initial=False):
        signature = pf.fingerprint(self.library.path)
        if initial or signature != self._default_signature:
            self._defaults = pf.read_profiles(self.library.path)
            self._default_signature = signature
            if not initial:
                self._revision += 1

    def _refresh_locked(self):
        with self.library.locked():
            self._refresh_defaults_locked()

    def _conflicts_locked(self):
        return sorted(name for name, settings in self._profiles.items()
                      if name in self._defaults.profiles and self._defaults.profiles[name] != settings)

    # --- views -------------------------------------------------------------------------------
    def _configured_settings(self, settings: ModelSettings) -> ModelSettings:
        return dataclasses.replace(settings, base_url=self.servers[settings.kind].base_url)

    def _endpoint(self, settings: ModelSettings, api_key=None) -> Endpoint:
        settings = self._configured_settings(settings)
        if api_key is None:
            api_key = self.servers[settings.kind].api_key
        return _endpoint(settings, api_key)

    def _location(self, settings: ModelSettings) -> str:
        return roles.profile_location(self._configured_settings(settings))

    @property
    def problems(self) -> list[Problem]:
        """Workspace problems, then the profile problem, if any (for the terminal at launch)."""
        with self._lock:
            return self._problems_locked()

    def _problems_locked(self) -> list[Problem]:
        extra = [p for p in (self._profile_problem, self._defaults.problem, self._task_problem, self._roles_problem,
                                      self._mcp.problem)
                 if p is not None]
        return list(self.workspace.problems) + extra

    def _profiles_read_only_locked(self) -> bool:
        return self.workspace.read_only or self._profile_problem is not None

    def _view_locked(self) -> dict:
        s = self._settings
        ep = self._endpoint(s)
        if (ep.kind, ep.base_url) in self._keys:
            key_source = "session"
        elif ep.api_key_env and os.environ.get(ep.api_key_env, "").strip():
            key_source = "env:" + ep.api_key_env
        elif self.servers and self.servers[s.kind].api_key:
            key_source = "config"
        else:
            key_source = "none"
        ws = self.workspace
        active = self._active
        router = self._endpoint(ModelSettings(kind="openrouter"))
        router_source = ("session" if (router.kind, router.base_url) in self._keys else
                         "env:OPENROUTER_API_KEY" if os.environ.get("OPENROUTER_API_KEY", "").strip() else "none")
        return {"kind": s.kind, "base_url": ep.base_url if self.servers else s.base_url, "model": s.model, "provider": s.provider,
                "key_source": key_source,
                "openrouter_key_source": router_source, "server_config_path": str(self.config_path or ""),
                "legacy_server_profiles": sorted(n for n, p in self._profiles.items()
                    if self.servers and p.base_url and p.base_url.rstrip("/") != self.servers[p.kind].base_url),
                "revision": self._revision,
                "task": {"saved": self._task_saved.saved_shape(), "revision": self._task_revision,
                         "read_only": self._task_read_only_locked(),
                         "path": str(self.workspace.path / ts.FILE)},
                "supported": supported_view(s.kind),
                "mcp": self._mcp_view_locked(),
                "roles": self._roles_view_locked(),
                "blocks": {"path": str(self.blocks.path), "active_runs": len(self.store.active_block_runs())},
                "scripts": {"running": self.scripts.in_flight, "limit": self.scripts.limit},
                "contracts": self._contracts_view_locked(),
                "history": self._history_view_locked(),
                "attempts": {"running": self.store.running_count()},
                "workspace": {"name": ws.name, "path": str(ws.path), "created": ws.created,
                              "read_only": ws.read_only,
                              "problems": [dataclasses.asdict(p) for p in self._problems_locked()]},
                "profiles": {"names": sorted(self._profiles), "active": active,
                             "details": {n: {**dataclasses.asdict(self._configured_settings(p)), "location": self._location(p)}
                                         for n, p in self._profiles.items()},
                             "path": str(self.profiles_path / pf.PROFILES_FILE),
                              "defaults_path": str(self.library.path / pf.PROFILES_FILE),
                              "defaults_read_only": self._defaults.problem is not None,
                              "default_names": sorted(self._defaults.profiles),
                              "conflicts": self._conflicts_locked(),
                              "unresolved_active": self._unresolved,
                              "modified": active is not None and self._profiles.get(active) != s,
                             "read_only": self._profiles_read_only_locked()}}

    def _roles_read_only_locked(self) -> bool:
        return self.workspace.read_only or self._roles_problem is not None

    def _role_failure_locked(self, role: str) -> RoleUnresolved | None:
        """Why `role` cannot be resolved now (7.4), or None."""
        profile = self._roles.get(role)
        if profile is None:
            return RoleUnresolved(role, "unconfigured" if role in self._role_definitions else "undefined")
        settings = self._profiles.get(profile)
        if settings is None:
            return RoleUnresolved(role, "no_profile")
        definition = self._role_definitions.get(role)
        if definition and definition["location"] != self._location(settings):
            return RoleUnresolved(role, "location")
        if not settings.model.strip():
            return RoleUnresolved(role, "no_model")
        return None

    def _unresolved_roles_locked(self) -> list[dict]:
        out = []
        for role in sorted(self._roles.keys() | self._role_definitions.keys()):
            failure = self._role_failure_locked(role)
            if failure is not None:
                out.append({"role": role, "reason": str(failure)})
        return out

    def _roles_view_locked(self) -> dict:
        return {"map": dict(sorted(self._roles.items())), "revision": self._roles_revision,
                "definitions": copy.deepcopy(self._role_definitions), "defaults": copy.deepcopy(roles.DEFAULTS),
                "read_only": self._roles_read_only_locked(), "path": str(self.workspace.path / roles.FILE),
                "problem": dataclasses.asdict(self._roles_problem) if self._roles_problem else None,
                "unresolved": self._unresolved_roles_locked()}

    def _contracts_view_locked(self) -> dict:
        available, detail = checks.schema_support()
        return {"format_version": contracts.FORMAT_VERSION,
                "items": [entry.list_view() for entry in self.contracts.entries()],
                "families": self.contracts.families(),
                "read_only": self.workspace.read_only, "path": str(self.contracts.path),
                "validation": {"available": available, "detail": detail}}

    def _history_view_locked(self) -> dict:
        return {"save_runs": self._save_runs and not self.workspace.read_only, "revision": self._history_revision,
                "read_only": self.workspace.read_only, "path": str(self.history.path),
                "short_credential_held": self._short_credential_locked()}

    def _task_read_only_locked(self) -> bool:
        return self.workspace.read_only or self._task_problem is not None

    def view(self) -> dict:
        with self._lock:
            self._refresh_locked()
            self._refresh_mcp_locked()
            self.contracts.rescan()
            return self._view_locked()

    # --- settings ----------------------------------------------------------------------------
    def _settings_and_keys_locked(self, body: Mapping) -> tuple[ModelSettings, dict]:
        """The settings and keys a /api/config body would produce; changes nothing."""
        if "base_url" in body or "api_key" in body:
            raise SettingsError("server URLs and keys are configured outside model settings")
        new = settings_from_body(self._settings, body)
        return new, dict(self._keys)

    def apply_config(self, body: Mapping, revision: int) -> dict:
        with self._lock:
            self._refresh_locked()
            if revision != self._revision:
                raise StaleRevision(self._revision)
            new, keys = self._settings_and_keys_locked(body)
            if new != self._settings or keys != self._keys:
                self._settings, self._keys = new, keys
                self._credentials.update(keys.values())
                self._revision += 1
            return self._view_locked()

    def set_openrouter_key(self, value: object, revision: int) -> dict:
        with self._lock:
            self._refresh_locked()
            if revision != self._revision:
                raise StaleRevision(self._revision)
            ep = self._endpoint(ModelSettings(kind="openrouter"))
            slot = (ep.kind, ep.base_url)
            if value == "":
                self._keys.pop(slot, None)
            else:
                key = validate_key(value)
                self._keys[slot] = key
                self._credentials.add(key)
            self._revision += 1
            return self._view_locked()

    # --- profiles ----------------------------------------------------------------------------
    @_writes("profile save")
    def save_profile(self, body: Mapping, revision: int) -> dict:
        """Store the settings `body` produces as profile `body["name"]` and make it active."""
        def validate():
            return _name(body.get("name")), self._settings_and_keys_locked(body)

        def plan(disk, checked):
            name, (new, keys) = checked
            raw = copy.deepcopy(disk.raw)
            raw["profiles"][name] = pf.profile_object(new, raw["profiles"].get(name))
            return raw, new, keys, {**disk.profiles, name: new}, name

        return self._profile_operation(revision, validate, plan, export_body=body)

    @_writes("profile select")
    def select_profile(self, name: str | None, revision: int) -> dict:
        """Make profile `name` active and apply its settings; `None` detaches (settings unchanged)."""
        def plan(disk, _checked):
            if name is not None and name not in disk.profiles:
                raise NoSuchProfile()
            raw = copy.deepcopy(disk.raw)
            settings = disk.profiles[name] if name is not None else self._settings
            return raw, settings, self._keys, disk.profiles, name

        return self._profile_operation(revision, lambda: _name(name, allow_none=True), plan)

    @_writes("profile delete")
    def delete_profile(self, name: str, revision: int) -> dict:
        """Remove profile `name`; if it was active, nothing is active and the settings stay."""
        def plan(disk, _checked):
            if name not in disk.profiles:
                raise NoSuchProfile()
            raw = copy.deepcopy(disk.raw)
            del raw["profiles"][name]
            active = None if self._active == name else self._active
            profiles = {k: v for k, v in disk.profiles.items() if k != name}
            return raw, self._settings, self._keys, profiles, active

        return self._profile_operation(revision, lambda: _name(name), plan)

    def _write_locked(self, folder, file, raw):
        try:
            if file == pf.PROFILES_FILE:
                pf.write_profiles(folder, raw)
            else:
                wsmod.write_workspace(self.workspace, raw)
        except OSError as exc:
            raise WriteFailed(str(folder / file), exc.strerror or type(exc).__name__) from None

    def _profile_operation(self, revision, validate, plan, export_body=None):
        with self._lock:
            with self.library.locked():
                self._refresh_defaults_locked()
                if revision != self._revision:
                    raise StaleRevision(self._revision)
                if self._profiles_read_only_locked():
                    raise ReadOnly(self._problems_locked(), self.workspace.read_only)
                checked = validate()
                disk = pf.read_profiles(self.profiles_path)
                if disk.problem is not None:
                    self._profile_problem = disk.problem
                    self._revision += 1
                    raise ReadOnly([disk.problem], False)
                workspace_raw, problem = wsmod.read_workspace(self.workspace)
                if problem:
                    self.workspace.problems = [problem]
                    self.workspace.read_only = True
                    self._revision += 1
                    raise ReadOnly([problem], True)
                raw, settings, keys, profiles, active = plan(disk, checked)
                export = export_body is not None and export_body.get("export_default", False)
                if export_body is not None:
                    for flag in ("export_default", "overwrite_default"):
                        if flag in export_body and not isinstance(export_body[flag], bool):
                            raise SettingsError(f"{flag} must be true or false")
                if export:
                    if self._defaults.problem:
                        raise ReadOnly([self._defaults.problem], False)
                    name = export_body["name"]
                    if (name in self._defaults.profiles and self._defaults.profiles[name] != settings
                            and not export_body.get("overwrite_default", False)):
                        raise DefaultConflict()
                workspace_changed = workspace_raw.get("active_profile") != active
                memory_changed = (settings != self._settings or keys != self._keys
                                  or profiles != self._profiles or active != self._active or self._unresolved)
                changed = raw != disk.raw or workspace_changed or memory_changed
                # Local writes precede export: an export failure never loses a successful local save.
                if raw != disk.raw:
                    self.profiles_path.mkdir(parents=True, exist_ok=True)
                    self._write_locked(self.profiles_path, pf.PROFILES_FILE, raw)
                if workspace_changed:
                    workspace_raw["active_profile"] = active
                    try:
                        self._write_locked(self.workspace.path, wsmod.WORKSPACE_FILE, workspace_raw)
                    except WriteFailed:
                        self._profiles = dict(profiles)
                        if self._active is not None and self._active not in self._profiles:
                            self._unresolved, self._active = self._active, None
                            self._settings = ModelSettings()
                        self._revision += 1
                        raise
                self._settings, self._keys = settings, keys
                self._credentials.update(keys.values())
                self._profiles, self._active = dict(profiles), active
                self._unresolved = None
                if changed:
                    self._revision += 1
                if export:
                    defaults = copy.deepcopy(self._defaults.raw)
                    name = export_body["name"]
                    defaults["profiles"][name] = pf.profile_object(settings, defaults["profiles"].get(name))
                    if defaults != self._defaults.raw:
                        self._write_locked(self.library.path, pf.PROFILES_FILE, defaults)
                        self._refresh_defaults_locked()
                return self._view_locked()

    @_writes("profile update")
    def update_profiles(self, body: Mapping, revision: int) -> dict:
        """Import missing defaults and explicitly approved conflicts, under both locks."""
        def validate():
            overwrite = body.get("overwrite", [])
            if not isinstance(overwrite, list) or any(not wsmod.valid_name(n) for n in overwrite):
                raise SettingsError("overwrite must be an array of profile names")
            if self._defaults.problem:
                raise ReadOnly([self._defaults.problem], False)
            return set(overwrite)

        def plan(disk, overwrite):
            raw, profiles = copy.deepcopy(disk.raw), dict(disk.profiles)
            for name, settings in self._defaults.profiles.items():
                if name not in profiles or (name in overwrite and profiles[name] != settings):
                    raw["profiles"][name] = copy.deepcopy(self._defaults.raw["profiles"][name])
                    profiles[name] = settings
            settings = self._settings
            if self._active in overwrite and self._active in self._defaults.profiles:
                settings = profiles[self._active]
            return raw, settings, self._keys, profiles, self._active

        return self._profile_operation(revision, validate, plan)

    # --- task settings (Section 7.5) ----------------------------------------------------------
    @_writes("task settings save")
    def save_task_settings(self, task_body: object, revision: int) -> dict:
        """Save the panel's values as the workspace's task settings, under the state lock."""
        with self._lock:
            if revision != self._task_revision:
                raise StaleRevision(self._task_revision)
            if self._task_read_only_locked():
                problems = [p for p in (self._task_problem,) if p] or list(self.workspace.problems)
                raise ReadOnly(problems, self.workspace.read_only, "task settings")
            settings = ts.parse_task(task_body)
            disk = ts.read(self.workspace.path)
            if disk.problem is not None:
                self._task_problem = disk.problem
                self._task_revision += 1
                raise ReadOnly([disk.problem], False, "task settings")
            raw = ts.build_raw(disk.raw, settings)
            if disk.exists and raw == disk.raw:
                self._task_saved = settings
                return self._view_locked()
            try:
                ts.write(self.workspace.path, raw)
            except OSError as exc:
                raise WriteFailed(str(self.workspace.path / ts.FILE), exc.strerror or type(exc).__name__) from None
            self._task_saved = settings
            self._task_revision += 1
            return self._view_locked()

    # --- roles (techspec 0.1.4, 7.3, 7.4) -----------------------------------------------------
    @_writes("roles save")
    def save_roles(self, roles_body: object, revision: int, definitions=None) -> dict:
        """Save the role map under the state lock (7.3). The writer-barrier registration the route makes before
        taking this lock arrives with the barrier (Phase B)."""
        with self._lock:
            if revision != self._roles_revision:
                raise StaleRevision(self._roles_revision)
            if self._roles_read_only_locked():
                problems = [p for p in (self._roles_problem,) if p] or list(self.workspace.problems)
                raise ReadOnly(problems, self.workspace.read_only, "roles")
            disk = roles.read(self.workspace.path)
            if disk.problem is not None:
                self._roles_problem = disk.problem
                self._roles_revision += 1
                raise ReadOnly([disk.problem], False, "roles")
            try:
                parsed = roles.parse_roles(roles_body)
                if definitions is None:
                    # Legacy clients may change assignments, but cannot remove existing routing policy.
                    defined = {}
                    if disk.raw.get("format_version") in (2, 3):
                        defined = copy.deepcopy(dict(self._role_definitions))
                    for n, p in parsed.items():
                        if n not in defined and p in self._profiles:
                            defined[n] = {"description": "", "location": self._location(self._profiles[p])}
                else:
                    defined = roles.parse_definitions(definitions)
                    if not set(parsed) <= set(defined):
                        raise roles.RolesError("every assigned role requires a definition")
            except roles.RolesError as exc:
                raise SettingsError(str(exc)) from None
            for role, profile in parsed.items():
                if profile not in self._profiles:
                    raise RoleProfileMissing(role)
                if role in defined and defined[role]["location"] != self._location(self._profiles[profile]):
                    raise SettingsError(f"role {role}'s profile is incompatible with its local/cloud location")
            raw = roles.build_raw(disk.raw, parsed, defined)
            if disk.exists and raw == disk.raw:
                self._roles = parsed
                self._role_definitions = defined
                return self._view_locked()
            try:
                roles.write(self.workspace.path, raw)
            except OSError as exc:
                raise WriteFailed(str(self.workspace.path / roles.FILE), exc.strerror or type(exc).__name__) from None
            self._roles = parsed
            self._role_definitions = defined
            self._roles_revision += 1
            return self._view_locked()

    def role_capture(self, role: str) -> RoleCapture:
        """The destination snapshot and the saved defaults of `role`, read together under the state lock
        (7.4). Raises RoleUnresolved."""
        with self._lock:
            failure = self._role_failure_locked(role)
            if failure is not None:
                raise failure
            profile = self._roles[role]
            s = self._profiles[profile]
            ep = self._endpoint(s)
            key = self._keys.get((ep.kind, ep.base_url))
            if ep.api_key_env:
                self._env_names.add(ep.api_key_env)
            snapshot = RequestSnapshot(endpoint=self._endpoint(s, key), model=s.model, kind=s.kind,
                                       provider=s.provider if s.kind == "openrouter" else "",
                                       profile_name=profile, profile_modified=False)
            defaults = copy.deepcopy(dict((self._role_definitions.get(role) or {}).get("task_defaults") or {}))
            return RoleCapture(role, profile, snapshot, defaults, self._roles_revision)

    def resolve_role(self, role: str) -> RequestSnapshot:
        """The snapshot a block step uses for `role` (7.4), built from the profile's saved settings. Raises
        RoleUnresolved."""
        return self.role_capture(role).snapshot

    def workshop_ticket(self) -> dict:
        return self.workshop_operations.issue()

    def workshop_ticket_status(self, ticket: object) -> dict:
        return self.workshop_operations.lookup(ticket)

    # --- block definitions (techspec 0.1.4, 8.8) ------------------------------------------------
    def block_listing(self, reload: bool = False) -> dict:
        with self._lock:
            return {**self.blocks.listing(reload), "path": str(self.blocks.path)}

    def block_loaded(self, name: object, reload: bool = False) -> blocks.Loaded | None:
        """The named block as it is now (None when there is no such block). Loads that block directly, whether
        or not the listing shows it."""
        with self._lock:
            return self.blocks.get(name, reload) if isinstance(name, str) else None

    def block_for_run(self, name: object, revision: int) -> blocks.Loaded:
        """The block a run request names, at its effective revision (15.3). Raises NoSuchBlock, StaleRevision
        (carrying the current revision) or BlockUnusable."""
        with self._lock:
            loaded = self.blocks.get(name) if isinstance(name, str) else None
            if loaded is None:
                raise NoSuchBlock()
            if loaded.revision != revision:
                raise StaleRevision(loaded.revision)
            if loaded.problem is not None or loaded.block is None:
                raise BlockUnusable(loaded.problem)
            return loaded

    def block_detail(self, name: object) -> dict:
        """The `GET /api/blocks/<name>` body (15.3). Raises NoSuchBlock."""
        with self._lock:
            loaded = self.blocks.get(name) if isinstance(name, str) else None
            if loaded is None:
                raise NoSuchBlock()
            return blocks.detail(loaded)

    # --- contracts (overhaul spec 9-13) ---------------------------------------------------------
    def contracts_listing(self) -> dict:
        with self._lock:
            self.contracts.rescan()
            return self._contracts_view_locked()

    def _used_by(self, name: str, latest: int | None) -> tuple[list, list]:
        from .workshop_assets import reference_scan
        return reference_scan(self.workspace.path / blocks.DIRECTORY, name, latest)

    def contract_detail(self, name: object, revision: int | None = None) -> dict:
        """One contract read from its current bytes: identity, file token, the selected numbered revision (the
        latest at this read by default), its exact definition text, revision summaries and Used by. A file with
        a problem returns its metadata and problem but no editable projection. Raises contracts.StoreError."""
        if not wsmod.valid_name(name):
            raise contracts.StoreError("no_such_contract", 404, "no such contract")
        entry = contracts.ContractStore(self.workspace.path).read_current(name)     # never the stat cache
        if entry is None:
            raise contracts.StoreError("no_such_contract", 404, "no such contract")
        with self._lock:
            self.contracts.rescan()
        blockers, unreadable = self._used_by(name, entry.latest if entry.problem is None else None)
        out = {"format_version": contracts.FORMAT_VERSION, "name": entry.name, "family": entry.family,
               "file_token": entry.token, "problem": entry.problem_view(),
               "used_by": {"items": blockers, "unreadable": unreadable}, "read_only": self.workspace.read_only}
        if entry.problem is not None:
            return {**out, "latest": None, "revision": None, "revisions": []}
        selected = entry.revisions[-1] if revision is None else entry.revision(revision)
        if selected is None:
            raise contracts.StoreError("no_such_revision", 404, "no such revision")
        return {**out, "latest": entry.latest, "revision": selected.revision, "digest": selected.digest,
                "label": selected.label, "origin": selected.origin, "created_at": selected.created_at,
                "basis": selected.basis, "definition_text": selected.definition_text(),
                "revisions": [r.summary() for r in entry.revisions]}

    def _contract_write(self, label: str, operation):
        """Writer registration first, then the logical registry reservation (claimed under C6, which is released
        at once); the filesystem work holds neither C1 nor C6. The shared cache is refreshed briefly after."""
        if self.workspace.read_only:
            raise contracts.StoreError("read_only", 409, "the workspace is read-only")
        with self.writing(label):
            try:
                with self.workshop.assets.registry():
                    before_write(label)
                    result = operation(contracts.ContractStore(self.workspace.path))
            except OSError as exc:
                raise WriteFailed(str(self.contracts.path), exc.strerror or type(exc).__name__) from None
            finally:
                with self._lock:
                    self.contracts.rescan()
            return result

    def save_contract(self, name: str, family: str, expected_token: str | None, definition_text: str, label: str,
                      *, origin: str = "manual", basis: dict | None = None) -> dict:
        return self._contract_write("contract save", lambda store: store.save(
            name, family, expected_token, definition_text, label, origin=origin, basis=basis,
            read_only=self.workspace.read_only))

    def set_contract_family(self, name: str, expected_token: str, family: str) -> dict:
        return self._contract_write("contract family", lambda store: store.set_family(
            name, expected_token, family, read_only=self.workspace.read_only))

    def restore_contract(self, name: str, expected_token: str, revision: int, label: str) -> dict:
        return self._contract_write("contract restore", lambda store: store.restore(
            name, expected_token, revision, label, read_only=self.workspace.read_only))

    def delete_contract(self, name: str, expected_token: str) -> dict:
        return self._contract_write("contract delete", lambda store: store.delete(
            name, expected_token, lambda n: self._used_by(n, None), read_only=self.workspace.read_only))

    # --- MCP library, tokens and snapshots (Sections 9.1, 9.2) ------------------------------
    def _refresh_mcp_locked(self, initial: bool = False) -> None:
        """Reload the library when another process changed it (inode, mtime, size)."""
        signature = self.mcp_library.fingerprint()
        if initial or signature != self._mcp_signature:
            self._mcp = self.mcp_library.read()
            self._mcp_signature = signature
            if not initial:
                self._mcp_revision += 1
            self._identities_changed_locked()

    def _token_for_locked(self, config: ml.ServerConfig) -> tuple[str | None, str]:
        typed = self._mcp_tokens.get(config.url)
        if typed:
            return typed, "session"
        if config.token_env:
            value = os.environ.get(config.token_env, "").strip()
            if value:
                return value, "env:" + config.token_env
        return None, "none"

    def _identities_changed_locked(self) -> None:
        identities = {(c.url, self._token_for_locked(c)[0]) for c in self._mcp.servers.values()}
        self.mcp_client.set_current_identities(identities)

    def _mcp_view_locked(self) -> dict:
        servers = {}
        for name, config in sorted(self._mcp.servers.items()):
            era = self.mcp_client.era_of(config.url)
            servers[name] = {**config.view(), "token_source": self._token_for_locked(config)[1],
                             "era": era[0] if era else None, "protocol_version": era[1] if era else None}
        return {"path": str(self.mcp_library.path), "read_only": self._mcp.problem is not None,
                "problems": [dataclasses.asdict(self._mcp.problem)] if self._mcp.problem else [],
                "revision": self._mcp_revision, "servers": servers,
                "protocols": {"modern": [mcp_client.MODERN], "legacy": list(mcp_client.LEGACY)},
                "operations": self.mcp_ops.view()}

    def _mcp_check_locked(self, revision: int) -> None:
        self._refresh_mcp_locked()
        if revision != self._mcp_revision:
            raise StaleRevision(self._mcp_revision)

    @_writes("MCP servers save")
    def save_mcp_servers(self, servers_body: object, revision: int) -> dict:
        with self._lock:
            with self.mcp_library.locked():
                self._mcp_check_locked(revision)
                if self._mcp.problem is not None:
                    raise ReadOnly([self._mcp.problem], False, "MCP servers")
                try:
                    servers = ml.parse_servers(servers_body)
                except wsmod.InvalidDocument as exc:
                    raise SettingsError(str(exc)) from None
                disk = self.mcp_library.read()
                if disk.problem is not None:
                    self._mcp = disk
                    self._mcp_signature = self.mcp_library.fingerprint()
                    self._mcp_revision += 1
                    raise ReadOnly([disk.problem], False, "MCP servers")
                raw = ml.build_raw(disk.raw, servers)
                if raw != disk.raw or not disk.exists:
                    try:
                        self.mcp_library.write(raw)
                    except OSError as exc:
                        raise WriteFailed(str(self.mcp_library.path), exc.strerror or type(exc).__name__) from None
                    self._mcp = self.mcp_library.read()
                    self._mcp_signature = self.mcp_library.fingerprint()
                    self._mcp_revision += 1
                    self._identities_changed_locked()
                return self._view_locked()

    def set_mcp_token(self, server: object, token: object, revision: int) -> dict:
        with self._lock:
            self._mcp_check_locked(revision)
            config = self._mcp.servers.get(server) if isinstance(server, str) else None
            if config is None:
                raise NoSuchServer()
            if token == "":
                changed = self._mcp_tokens.pop(config.url, None) is not None
            else:
                if not isinstance(token, str) or not token.strip() or \
                        any(not 0x21 <= ord(c) <= 0x7E for c in token.strip()):
                    raise SettingsError("the token must be visible ASCII without spaces")
                value = token.strip()
                changed = self._mcp_tokens.get(config.url) != value
                self._mcp_tokens[config.url] = value
                self._credentials.add(value)
            if changed:
                self._mcp_revision += 1
                self._identities_changed_locked()
            return self._view_locked()

    def mcp_snapshot(self, server: object, revision: int) -> mcp_client.Snapshot:
        with self._lock:
            self._mcp_check_locked(revision)
            config = self._mcp.servers.get(server) if isinstance(server, str) else None
            if config is None:
                raise NoSuchServer()
            token, _source = self._token_for_locked(config)
            return mcp_client.Snapshot(server, config.url, config.idle_timeout_s, config.call_deadline_s,
                                       config.discovery_deadline_s, token)

    def mcp_snapshot_for_block(self, server: str) -> mcp_client.Snapshot:
        """A block tool step's snapshot of `server` with its token. No revision: it uses the library as it is
        now (10.5). Raises NoSuchServer."""
        with self._lock:
            self._refresh_mcp_locked()
            config = self._mcp.servers.get(server)
            if config is None:
                raise NoSuchServer()
            token, _source = self._token_for_locked(config)
            return mcp_client.Snapshot(server, config.url, config.idle_timeout_s, config.call_deadline_s,
                                       config.discovery_deadline_s, token)

    # --- contracts for a request (overhaul spec 13.1) ----------------------------------------
    def contract_for_request(self, name: str, revision: int, digest: str):
        """The exact pinned revision a Checked request reviewed, read from the current bytes. A newer appended
        revision does not invalidate it; a missing revision, an unusable file or a different digest refuses
        before dispatch. Raises contracts.StoreError."""
        if not wsmod.valid_name(name):
            raise contracts.StoreError("no_such_contract", 404, "no such contract")
        entry = contracts.ContractStore(self.workspace.path).read_current(name)
        found, refused = contracts.resolve_pin(entry, name, revision, digest)
        if found is None:
            code, message = refused
            status = 404 if code in ("no_such_contract", "no_such_revision") else 409
            raise contracts.StoreError(code, status, message)
        return found

    # --- run history (techspec 10.1) -------------------------------------------------------------
    def credentials(self) -> list[str]:
        """Every credential AgentWiz has held: typed keys and tokens (even cleared ones), and the current
        values of the environment variables it reads for credentials (10.4)."""
        with self._lock:
            return self._credentials_locked()

    def _credentials_locked(self) -> list[str]:
        names = set(self._env_names)
        names.update(c.token_env for c in self._mcp.servers.values() if c.token_env)
        values = set(self._credentials)
        values.update(v for v in (os.environ.get(n, "").strip() for n in names) if v)
        return sorted(values)

    def _short_credential_locked(self) -> bool:
        return any(0 < len(c) < history.MIN_CREDENTIAL for c in self._credentials_locked())

    def save_runs_now(self) -> bool:
        """The switch's value for a run created now (R5-8)."""
        with self._lock:
            return self._save_runs and not self.workspace.read_only

    @_writes("history switch")
    def set_history_switch(self, save_runs: bool, revision: int) -> dict:
        with self._lock:
            if revision != self._history_revision:
                raise StaleRevision(self._history_revision)
            if self.workspace.read_only:
                raise ReadOnly(list(self.workspace.problems), True)
            raw, problem = wsmod.read_workspace(self.workspace)
            if problem:
                self.workspace.problems = [problem]
                self.workspace.read_only = True
                self._revision += 1
                raise ReadOnly([problem], True)
            if raw.get("save_runs") != save_runs:
                raw["save_runs"] = save_runs
                self._write_locked(self.workspace.path, wsmod.WORKSPACE_FILE, raw)
                self._history_revision += 1
            self._save_runs = save_runs
            return self._view_locked()

    # --- the writer barrier, deliveries and shutdown (techspec 0.1.4, 10.10) ------------------------
    def register_writer(self, label: str) -> writers.Writer:
        """One decision under the barrier's lock: counted, or refused with 503 `busy` once shutdown has begun."""
        before_register_writer(label)
        return self.writers.register(label)

    @contextlib.contextmanager
    def writing(self, label: str):
        writer = self.register_writer(label)
        try:
            yield writer
        finally:
            writer.release()

    def delete_run(self, run_id: str, *, read_only: bool) -> None:
        """Run deletion, registered as a workspace writer."""
        with self.writing(f"delete run {run_id}"):
            before_write(f"delete run {run_id}")
            self.history.delete_run(run_id, read_only=read_only)

    def add_delivery(self, delivery) -> None:
        with self._deliveries_lock:
            self._deliveries.add(delivery)
            gone = self._shutdown_started
        if gone:
            delivery.interrupt()

    def remove_delivery(self, delivery) -> None:
        with self._deliveries_lock:
            self._deliveries.discard(delivery)

    def _interrupt_deliveries(self) -> None:
        with self._deliveries_lock:
            targets = list(self._deliveries)
        for target in targets:
            try:
                target.interrupt()
            except Exception as exc:  # noqa: BLE001 - the others must still be interrupted
                report_internal("shutdown", exc)

    def _report_outstanding(self, labels: list[str]) -> None:
        print("[AgentWiz] shutdown is waiting for: " + "; ".join(labels), file=sys.stderr, flush=True)

    def shutdown(self, grace_s: float = SHUTDOWN_GRACE_S, *, wait: bool = True) -> list[str]:
        """Close admission, cancel what was admitted, escalate, and drain every managed writer (10.10). Returns the
        labels still outstanding (empty once drained). Idempotent: a second call never repeats the cancellation or
        the grace. Installs no signal handler; may be called from any thread."""
        with self._shutdown_lock:
            first = not self._shutdown_started
            self._shutdown_started = True
        if first:
            self.workshop_operations.close()
            if self.workshop.active:
                self.workshop.stop(self.workshop.active)
            self.writers.close()                      # 1. close admission, one gate at a time
            self.store.close()
            self.mcp_ops.close()
            self.scripts.close()
            self._interrupt_deliveries()              # 2. delivery never delays anything
            self.store.stop_all()                     # 3. cancel admitted work through the token path
            self.mcp_ops.cancel_all()                 #    and abandon every registered MCP operation
            end = time.monotonic() + grace_s          # 4. grace
            while time.monotonic() < end:
                if not self.store.active_block_runs() and self.store.running_count() == 0:
                    break
                time.sleep(script_step.POLL_S / 5)
            self.cleanups.kill_all()
        elif not wait:
            return self.writers.outstanding()
        while True:                                   # 5. drain
            end = time.monotonic() + grace_s
            while time.monotonic() < end:
                self.cleanups.service_idle()
                if self.writers.wait_drained(min(script_step.POLL_S, max(0.0, end - time.monotonic()))):
                    return []
            outstanding = self.writers.outstanding()
            if not outstanding:
                return []
            self._report_outstanding(outstanding)
            if not wait:
                return outstanding

    # --- requests ----------------------------------------------------------------------------
    def snapshot(self, revision: int) -> RequestSnapshot:
        with self._lock:
            self._refresh_locked()
            if revision != self._revision:
                raise StaleRevision(self._revision)
            s = self._settings
            ep = self._endpoint(s)
            key = self._keys.get((ep.kind, ep.base_url))
            if ep.api_key_env:
                self._env_names.add(ep.api_key_env)
            active = self._active
            return RequestSnapshot(endpoint=self._endpoint(s, key), model=s.model, kind=s.kind,
                                   provider=s.provider if s.kind == "openrouter" else "",
                                   profile_name=active,
                                   profile_modified=active is not None and self._profiles.get(active) != s)

    def key_for(self, kind: str, base_url: str) -> str | None:
        """The typed key held for an endpoint, or None (the environment variable then applies). Used by a
        repair, which sends to the endpoint its parent captured (techspec 9.2)."""
        with self._lock:
            ep = Endpoint(kind, base_url or None)
            if ep.base_url != self.servers[kind].base_url:
                raise SettingsError("the recorded server differs from the configured server; repair is unavailable")
            if ep.api_key_env:
                self._env_names.add(ep.api_key_env)
            return self._keys.get((ep.kind, ep.base_url)) or self.servers[kind].api_key

    def discovery_endpoint(self, kind: object, base_url: object, api_key: object) -> Endpoint:
        """Discovery uses the same fixed server and session key as model execution."""
        if base_url not in (None, "") or api_key not in (None, ""):
            raise SettingsError("server URLs and keys are configured outside model settings")
        if not isinstance(kind, str) or kind not in self.servers:
            raise SettingsError("invalid model source")
        with self._lock:
            ep = self._endpoint(ModelSettings(kind=kind))
            key = self._keys.get((ep.kind, ep.base_url))
            return self._endpoint(ModelSettings(kind=kind), key)


__all__ = ["DefaultConflict", "NoSuchProfile", "NoSuchBlock", "NoSuchServer", "ProcessState", "ReadOnly", "RequestBudget",
           "RoleProfileMissing", "RoleUnresolved", "SettingsError", "StaleRevision", "WriteFailed"]
