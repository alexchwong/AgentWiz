"""Process state: settings, revision, keys, profiles, request budget (techspec 7.6; R0-2, R0-10,
R0-11, R1-3, R1-4, P11).

Settings belong to the process and every tab shares them. Each change increments a revision, and
every completion, save and profile operation states the revision its page was showing, so a tab can
never act on settings or profiles it is not showing. One revision covers settings, keys and
profiles.

The state lock is held to compare, snapshot, view and apply, and across the read-validate-change-
replace of workspace profiles (through profiles.py), so one profile change can never overwrite another.
It is never held during a network call. It is not reentrant, so no method holding it calls another
public method.
"""
from __future__ import annotations

import copy
import dataclasses
import os
import threading
from collections.abc import Mapping
from pathlib import Path

from modelspell import Endpoint

from . import profiles as pf
from . import workspace as wsmod
from .model_call import (ModelSettings, RequestSnapshot, SettingsError, complete_kwargs,
                         settings_from_body, validate_key)
from .workspace import Problem, Workspace


class StaleRevision(Exception):
    def __init__(self, current: int):
        super().__init__("settings were changed elsewhere")
        self.current = current


class ReadOnly(Exception):
    """Profiles cannot be changed until AgentWiz restarts. The message names the file and the rule."""

    def __init__(self, problems: list[Problem], whole_workspace: bool):
        first = problems[0] if problems else None
        what = "the workspace is read-only" if whole_workspace else "profiles are read-only"
        super().__init__(f"{what}: {first.file}: {first.message}" if first else what)
        self.problems = problems
        self.whole_workspace = whole_workspace


class DefaultConflict(Exception):
    """Export requires explicit overwrite approval."""


class NoSuchProfile(Exception):
    def __init__(self):
        super().__init__("no such profile")


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

    @property
    def in_flight(self) -> int:
        return self._in_flight

    def try_acquire(self) -> bool:
        with self._lock:
            if self._in_flight >= self.limit:
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


class ProcessState:
    def __init__(self, workspace: Workspace, request_limit: int = 8, *, profiles_dir: Path):
        self.budget = RequestBudget(request_limit)
        self.workspace = workspace
        self._lock = threading.Lock()
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
    @property
    def problems(self) -> list[Problem]:
        """Workspace problems, then the profile problem, if any (for the terminal at launch)."""
        with self._lock:
            return self._problems_locked()

    def _problems_locked(self) -> list[Problem]:
        extra = [p for p in (self._profile_problem, self._defaults.problem) if p is not None]
        return list(self.workspace.problems) + extra

    def _profiles_read_only_locked(self) -> bool:
        return self.workspace.read_only or self._profile_problem is not None

    def _view_locked(self) -> dict:
        s = self._settings
        ep = _endpoint(s)
        if (ep.kind, ep.base_url) in self._keys:
            key_source = "session"
        elif ep.api_key_env and os.environ.get(ep.api_key_env, "").strip():
            key_source = "env:" + ep.api_key_env
        else:
            key_source = "none"
        ws = self.workspace
        active = self._active
        return {"kind": s.kind, "base_url": s.base_url, "model": s.model, "provider": s.provider,
                "reasoning": s.reasoning, "max_tokens": s.max_tokens, "key_source": key_source,
                "revision": self._revision,
                "workspace": {"name": ws.name, "path": str(ws.path), "created": ws.created,
                              "read_only": ws.read_only,
                              "problems": [dataclasses.asdict(p) for p in self._problems_locked()]},
                "profiles": {"names": sorted(self._profiles), "active": active,
                             "path": str(self.profiles_path / pf.PROFILES_FILE),
                              "defaults_path": str(self.library.path / pf.PROFILES_FILE),
                              "defaults_read_only": self._defaults.problem is not None,
                              "default_names": sorted(self._defaults.profiles),
                              "conflicts": self._conflicts_locked(),
                              "unresolved_active": self._unresolved,
                              "modified": active is not None and self._profiles.get(active) != s,
                             "read_only": self._profiles_read_only_locked()}}

    def view(self) -> dict:
        with self._lock:
            self._refresh_locked()
            return self._view_locked()

    # --- settings ----------------------------------------------------------------------------
    def _settings_and_keys_locked(self, body: Mapping) -> tuple[ModelSettings, dict]:
        """The settings and keys a /api/config body would produce; changes nothing."""
        new = settings_from_body(self._settings, body)
        ep = _endpoint(new)
        slot = (ep.kind, ep.base_url)
        keys = dict(self._keys)
        api_key = body.get("api_key")
        if api_key == "":
            keys.pop(slot, None)
        elif api_key is not None:
            keys[slot] = validate_key(api_key)
        return new, keys

    def apply_config(self, body: Mapping, revision: int) -> dict:
        with self._lock:
            self._refresh_locked()
            if revision != self._revision:
                raise StaleRevision(self._revision)
            new, keys = self._settings_and_keys_locked(body)
            if new != self._settings or keys != self._keys:
                self._settings, self._keys = new, keys
                self._revision += 1
            return self._view_locked()

    # --- profiles ----------------------------------------------------------------------------
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

    def select_profile(self, name: str | None, revision: int) -> dict:
        """Make profile `name` active and apply its settings; `None` detaches (settings unchanged)."""
        def plan(disk, _checked):
            if name is not None and name not in disk.profiles:
                raise NoSuchProfile()
            raw = copy.deepcopy(disk.raw)
            settings = disk.profiles[name] if name is not None else self._settings
            return raw, settings, self._keys, disk.profiles, name

        return self._profile_operation(revision, lambda: _name(name, allow_none=True), plan)

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

    # --- requests ----------------------------------------------------------------------------
    def snapshot(self, revision: int) -> RequestSnapshot:
        with self._lock:
            self._refresh_locked()
            if revision != self._revision:
                raise StaleRevision(self._revision)
            s = self._settings
            ep = _endpoint(s)
            key = self._keys.get((ep.kind, ep.base_url))
            return RequestSnapshot(endpoint=_endpoint(s, key), model=s.model,
                                   kwargs=complete_kwargs(s, s.kind))

    def discovery_endpoint(self, kind: object, base_url: object, api_key: object) -> Endpoint:
        """Endpoint for Load models / Load providers, from the dialog's typed values."""
        typed = settings_from_body(ModelSettings(), {"kind": kind, "base_url": base_url})
        ep = _endpoint(typed)
        if api_key is None or api_key == "":
            with self._lock:
                key = self._keys.get((ep.kind, ep.base_url))
        else:
            key = validate_key(api_key)
        return _endpoint(typed, key)


__all__ = ["DefaultConflict", "NoSuchProfile", "ProcessState", "ReadOnly", "RequestBudget", "SettingsError", "StaleRevision",
           "WriteFailed"]
