"""Process state: settings, revision, keys, request budget (techspec 7.5; R0-2, R0-10, R0-11).

Settings belong to the process and every tab shares them. Each change increments a revision, and
every completion and save states the revision its page was showing, so a tab can never act on
settings it is not showing. The state lock is held only to compare, snapshot, view and apply,
never during a network call. It is not reentrant, so no method holding it calls another public
method.
"""
from __future__ import annotations

import os
import threading
from collections.abc import Mapping

from modelspell import Endpoint

from .model_call import (ModelSettings, RequestSnapshot, SettingsError, complete_kwargs,
                         settings_from_body, validate_key)


class StaleRevision(Exception):
    def __init__(self, current: int):
        super().__init__("settings were changed elsewhere")
        self.current = current


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


class ProcessState:
    def __init__(self, request_limit: int = 8):
        self.budget = RequestBudget(request_limit)
        self._lock = threading.Lock()
        self._settings = ModelSettings()
        self._revision = 1
        self._keys: dict[tuple[str, str], str] = {}

    # --- views -------------------------------------------------------------------------------
    def _view_locked(self) -> dict:
        s = self._settings
        ep = _endpoint(s)
        if (ep.kind, ep.base_url) in self._keys:
            key_source = "session"
        elif ep.api_key_env and os.environ.get(ep.api_key_env, "").strip():
            key_source = "env:" + ep.api_key_env
        else:
            key_source = "none"
        return {"kind": s.kind, "base_url": s.base_url, "model": s.model, "provider": s.provider,
                "reasoning": s.reasoning, "max_tokens": s.max_tokens, "key_source": key_source,
                "revision": self._revision}

    def view(self) -> dict:
        with self._lock:
            return self._view_locked()

    # --- changes -----------------------------------------------------------------------------
    def apply_config(self, body: Mapping, revision: int) -> dict:
        with self._lock:
            if revision != self._revision:
                raise StaleRevision(self._revision)
            new = settings_from_body(self._settings, body)
            ep = _endpoint(new)
            slot = (ep.kind, ep.base_url)
            keys = dict(self._keys)
            api_key = body.get("api_key")
            if api_key == "":
                keys.pop(slot, None)
            elif api_key is not None:
                keys[slot] = validate_key(api_key)
            if new != self._settings or keys != self._keys:
                self._settings, self._keys = new, keys
                self._revision += 1
            return self._view_locked()

    def snapshot(self, revision: int) -> RequestSnapshot:
        with self._lock:
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


__all__ = ["ProcessState", "RequestBudget", "StaleRevision", "SettingsError"]
