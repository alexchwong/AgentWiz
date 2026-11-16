"""The starter block library: the packaged entries and their installer (starter blocks addendum, Section 5).

`agentwiz/library/index.json` lists the keys in display order; only listed keys can be listed or added. Each
`agentwiz/library/<key>/` holds `block.json` (format 4, inline contracts only, explicit Local only labels, model
steps on role `fast`), `library.json` (title, group, summary, library_version, three examples and the standard
description) and, for a model block, `prompts/system.md` and `prompts/user.md`.

The installer keeps the control an ordinary v4 save has: the user reviews each port's Local only label (and a
model block's destination) on a plan, and Add submits that plan with its confirmations. It never calls a write path
that skips the plan check. It writes through three ordinary Workshop operations, `save`, `save_examples` and
`save_description`, each with its own reservation, writer registration and stale check; the internal keywords
`provenance` and `only_revision` cannot be set by any HTTP body. Messages name rules and keys, never values.
"""
from __future__ import annotations

import copy
import hashlib
import json
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

import threading

from . import draft_lineage, jsonio, outbound_policy
from . import workshop_descriptions as descriptions
from .workshop import WorkshopError
from .workshop_assets import document, head, key_name

LIBRARY = Path(__file__).parent / "library"
GROUPS = ("llm", "tool", "transform")
GROUP_LABELS = {"llm": "Model blocks", "tool": "Tool blocks", "transform": "Transform blocks"}
ENTRY_LIMIT = 30
DEFAULT_ROLE = "fast"


class LibraryError(Exception):
    def __init__(self, code: str, status: int = 400, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class Entry:
    key: str
    definition: dict
    files: dict                  # relative path -> text
    info: dict                   # library.json

    @property
    def group(self) -> str:
        return self.info["group"]

    @property
    def version(self) -> int:
        return self.info["library_version"]


# --- the packaged entries (5.1) ---------------------------------------------------------------------------------------
def index() -> list[str]:
    return list(json.loads((LIBRARY / "index.json").read_text(encoding="utf-8"))["keys"])


def entry(key: object) -> Entry:
    """The packaged entry `key`, which must be in the index."""
    if not isinstance(key, str) or key not in index():
        raise LibraryError("unknown_entry", 404, "no library entry has that key")
    folder = LIBRARY / key
    definition = jsonio.decode_exact((folder / "block.json").read_text(encoding="utf-8"))
    info = jsonio.decode_exact((folder / "library.json").read_text(encoding="utf-8"))
    files = {}
    prompts = folder / "prompts"
    if prompts.is_dir():
        for path in sorted(prompts.iterdir()):
            files[f"prompts/{path.name}"] = path.read_text(encoding="utf-8")
    return Entry(key, definition, files, info)


def rewrite(item: Entry, as_key: str, role: str | None) -> dict:
    """The packaged definition with the block name rewritten to `as_key` and, for a model step, the role."""
    definition = copy.deepcopy(item.definition)
    definition["name"] = as_key
    if role is not None:
        for step in definition["steps"]:
            if step.get("kind") == "model":
                step["role"] = role
    return definition


def definition_sha256(definition: dict) -> str:
    return hashlib.sha256(jsonio.encode_exact(definition).encode("utf-8")).hexdigest()


def ports(definition: dict) -> dict:
    return {side: [{"name": name, "local_only": port.get("local_only") is True,
                    "format": port["contract"].get("format")} for name, port in definition[side].items()]
            for side in ("inputs", "outputs")}


def _digest(value) -> str:
    return hashlib.sha256(jsonio.encode_exact(value).encode("utf-8")).hexdigest()


# --- the installer (5.2) ----------------------------------------------------------------------------------------------
class Installer:
    def __init__(self, state):
        self.state = state
        self._lock = threading.Lock()
        self._refs: dict[tuple[str, str], str] = {}     # (as, definition digest) -> Workshop draft reference

    @property
    def service(self):
        return self.state.workshop

    # --- what is installed ---
    def _blocks(self) -> list[str]:
        root = self.state.workspace.path / "blocks"
        if not root.is_dir():
            return []
        return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.is_symlink())

    def _imported(self, key: str):
        """(provenance of the block's first retained revision or None, head or None)."""
        try:
            root = self.service.assets.root(key)
            h = head(root)
            if h is None:
                return None, None
            first = h["retained"][-1]
            metadata = document(root / ".workshop" / "revisions" / first, "workshop.json")
        except (WorkshopError, OSError, ValueError):
            return None, None
        library = metadata.get("library")
        if not isinstance(library, dict) or metadata.get("revision_number", 1) not in (1, None):
            return None, h
        return library, h

    def provenance(self, key: str) -> dict | None:
        """{key, version} when the block's first revision was imported from the library (Design shows it)."""
        provenance, _h = self._imported(key)
        if provenance is None:
            return None
        return {"key": provenance.get("key"), "version": provenance.get("version")}

    def _missing(self, key: str, revision: str) -> list[str]:
        missing = []
        if self.service.collection(key)["revision"] is None:
            missing.append("examples")
        state, _data = descriptions.read_sidecar(self.service.assets.root(key), revision)
        if state == "absent":
            missing.append("description")
        return missing

    def _role_view(self, role: str) -> dict:
        found = self.state.role_destination(role)
        if "unresolved" in found:
            return {"role": role, "profile": None, "location": None, "status": "unassigned"}
        location = outbound_policy.locality(found["url"])
        return {"role": role, "profile": found["profile"], "location": location,
                "status": "remote" if location == "remote" else "ready"}

    def destination(self, role: str) -> dict:
        """The credential-free destination the role resolves to now: profile, kind, effective URL, model,
        provider pin, location and readiness. These fields are what a run records as its destination."""
        found = self.state.role_destination(role)
        if "unresolved" in found:
            return {"role": role, "profile": None, "kind": None, "url": None, "model": None, "provider": None,
                    "location": None, "readiness": "unassigned"}
        return {"role": role, **found, "location": outbound_policy.locality(found["url"]), "readiness": "ready"}

    def listing(self) -> dict:
        blocks = self._blocks()
        imported: dict[str, list] = {}
        for key in blocks:
            provenance, h = self._imported(key)
            if provenance is not None:
                imported.setdefault(provenance.get("key"), []).append((key, provenance, h))
        roles = [self._role_view(role) for role in self.state.defined_roles()]
        if DEFAULT_ROLE not in {r["role"] for r in roles}:
            roles.insert(0, self._role_view(DEFAULT_ROLE))
        rows = []
        for key in index():
            item = entry(key)
            installed = imported.get(key, [])
            incomplete = []
            for block_key, provenance, h in installed:
                if h is not None and h["retained"] == [h["current"]] and self._missing(block_key, h["current"]):
                    incomplete.append(block_key)
            if incomplete:
                status = "incomplete"
            elif installed:
                status = "installed"
            elif any(b.casefold() == key.casefold() for b in blocks):
                status = "key_in_use"
            else:
                status = "available"
            row = {"key": key, "title": item.info["title"], "group": item.group, "summary": item.info["summary"],
                   "ports": ports(item.definition), "library_version": item.version, "status": status,
                   "installed": [b for b, _p, _h in installed], "incomplete": incomplete,
                   "newer_version": any(p.get("version", 0) < item.version for _b, p, _h in installed)}
            if item.group == "llm":
                row["roles"] = roles
            rows.append(row)
        return {"format_version": 1, "read_only": self.state.workspace.read_only,
                "groups": [{"id": g, "label": GROUP_LABELS[g]} for g in GROUPS], "entries": rows}

    # --- planning ---
    @staticmethod
    def _entries(body) -> list[dict]:
        rows = body.get("entries") if isinstance(body, dict) else None
        if not isinstance(rows, list) or not 1 <= len(rows) <= ENTRY_LIMIT or any(not isinstance(r, dict) for r in rows):
            raise LibraryError("invalid", 400, f"entries must hold 1 to {ENTRY_LIMIT} objects")
        return rows

    def _request(self, row: dict):
        item = entry(row.get("key"))
        as_key = row.get("as", item.key)
        try:
            key_name(as_key)
        except WorkshopError:
            raise LibraryError("invalid_key", 400, "the block key does not follow the name rule") from None
        role = None
        if item.group == "llm":
            role = row.get("role", DEFAULT_ROLE)
            if not isinstance(role, str) or not role:
                raise LibraryError("invalid", 400, "role must be a role name")
        return item, as_key, role

    def _draft(self, item: Entry, as_key: str, role: str | None) -> tuple[dict, dict]:
        """The Workshop draft of an entry. Its draft reference is reused for the same key and definition, so a
        plan and Add's recheck digest the same body; a reference Workshop has since dropped is replaced, which
        changes the plan and asks for confirmation again."""
        definition = rewrite(item, as_key, role)
        draft = {"key": as_key, "definition_text": jsonio.encode_exact(definition), "files": dict(item.files),
                 "metadata": {}}
        identity = (as_key, definition_sha256(definition))
        with self._lock:
            ref = self._refs.get(identity)
        if ref is not None:
            try:
                self.service.drafts.get(ref)
                draft["draft_ref"] = ref
                return definition, draft
            except WorkshopError:
                pass
        self.service.drafts.issue(draft, draft_lineage.Lineage.of(draft))
        with self._lock:
            self._refs[identity] = draft["draft_ref"]
            while len(self._refs) > 256:
                self._refs.pop(next(iter(self._refs)))
        return definition, draft

    def _plan_one(self, row: dict) -> dict:
        item, as_key, role = self._request(row)
        out = {"key": item.key, "as": as_key, "role": role}
        if self.state.workspace.read_only:
            return {**out, "outcome": "read_only"}
        if any(b.casefold() == as_key.casefold() for b in self._blocks()):
            return {**out, "outcome": "key_in_use"}
        definition, draft = self._draft(item, as_key, role)
        try:
            plan = self.service.plan("save", {"draft": draft, "expected_identity": None})
            with self.service.assets.validate(draft) as captured:
                block = captured.parsed.block
                if block is None:
                    raise WorkshopError("block_invalid", 409)
                self.service.validate_examples(copy.deepcopy(item.info["examples"]), block)
                descriptions.normalise(item.info["description"]["sections"],
                                       item.info["description"]["port_summaries"], descriptions.ports_of(block))
        except descriptions.DescriptionInvalid as exc:
            return {**out, "outcome": "invalid", "code": "description_invalid", "issues": exc.issues[:20]}
        except WorkshopError as exc:
            if exc.code == "read_only":
                return {**out, "outcome": "read_only"}
            if exc.code in ("stale", "conflict"):
                return {**out, "outcome": "key_in_use"}
            # A packaging bug, which the offline tests prevent. Issues are located and never quote values.
            return {**out, "outcome": "invalid", "code": exc.code, "issues": list(exc.issues or [])[:20]}
        destination = self.destination(role) if role is not None else None
        binding = _digest({"key": item.key, "version": item.version, "as": as_key,
                           "definition_sha256": definition_sha256(definition), "destination": destination})
        return {**out, "outcome": "planned", "port_labels": plan["descriptor"]["port_labels"],
                "destination": destination, "expected_plan": plan["expected_plan"],
                "required_confirmations": plan["required_confirmations"], "binding": binding}

    def plan(self, body) -> dict:
        return {"format_version": 1, "entries": [self._plan_one(row) for row in self._entries(body)]}

    # --- adding ---
    def add(self, body) -> dict:
        results = []
        for row in self._entries(body):
            try:
                mode = row.get("mode", "add")
                if mode == "finish":
                    results.append(self._finish(row))
                elif mode == "add":
                    results.append(self._add(row))
                else:
                    raise LibraryError("invalid", 400, "mode must be add or finish")
            except LibraryError as exc:
                results.append({"key": row.get("key") if isinstance(row.get("key"), str) else None,
                                "outcome": exc.code if exc.code in ("unknown_entry", "invalid_key") else "invalid"})
        return {"format_version": 1, "entries": results}

    def _add(self, row: dict) -> dict:
        item, as_key, role = self._request(row)
        hold = self.state.settings_hold() if item.group == "llm" else nullcontext()
        with hold:
            fresh = self._plan_one(row)
            if fresh["outcome"] != "planned":
                return fresh
            out = {"key": item.key, "as": as_key, "role": role}
            if row.get("binding") != fresh["binding"]:
                return {**fresh, "outcome": "confirmation_required"}
            definition, draft = self._draft(item, as_key, role)
            provenance = {"key": item.key, "version": item.version,
                          "definition_sha256": definition_sha256(definition)}
            body = {"draft": draft, "expected_identity": None, "expected_plan": row.get("expected_plan"),
                    "confirmations": row.get("confirmations", [])}
            try:
                published = self.service.save(body, provenance=provenance)
            except WorkshopError as exc:
                if exc.code == "confirmation_required":
                    return {**fresh, "outcome": "confirmation_required"}
                if exc.code in ("stale", "conflict", "busy"):
                    # The key appeared since planning, or another write to it (a concurrent import) holds it.
                    return {**out, "outcome": "key_in_use"}
                if exc.code == "read_only":
                    return {**out, "outcome": "read_only"}
                return {**out, "outcome": "failed", "code": exc.code}
            except Exception as exc:  # noqa: BLE001 - shutdown or an unexpected write failure: nothing published
                return {**out, "outcome": "failed", "code": type(exc).__name__}
        revision = published["durable_id"]
        return {**out, "revision": revision, **self._assets(item, as_key, revision, ["examples", "description"],
                                                            first_edit="partial")}

    def _assets(self, item: Entry, as_key: str, revision: str, wanted: list[str], first_edit: str) -> dict:
        """Steps 2 and 3, each with `only_revision`. `first_edit` is the outcome when the first write finds the
        block edited ("partial" in Add, "finish_unavailable" in Finish)."""
        written, kept = [], []
        for asset in wanted:
            try:
                if asset == "examples":
                    self.service.save_examples(as_key, {"expected_revision": None,
                                                        "examples": copy.deepcopy(item.info["examples"])},
                                               only_revision=revision)
                else:
                    basis = self.service.assets.saved(as_key, revision).basis
                    record = item.info["description"]
                    self.service.save_description(as_key, revision, {
                        "expected_token": None, "basis": basis, "sections": record["sections"],
                        "port_summaries": record["port_summaries"]}, only_revision=revision)
                written.append(asset)
            except WorkshopError as exc:
                if exc.code == "stale":
                    kept.append(asset)                            # the user's own asset is kept
                    continue
                reason = {"edited": "edited", "busy": "busy"}.get(exc.code, "failed")
                if reason == "edited" and first_edit == "finish_unavailable" and not written and not kept:
                    return {"outcome": "finish_unavailable", "reason": "edited"}
                return {"outcome": "partial", "reason": reason, "written": written, "kept": kept,
                        "missing": [a for a in wanted if a not in written and a not in kept]}
            except Exception as exc:  # noqa: BLE001 - e.g. shutdown began: the remaining writes do not happen
                return {"outcome": "partial", "reason": "failed", "written": written, "kept": kept,
                        "missing": [a for a in wanted if a not in written and a not in kept],
                        "code": type(exc).__name__}
        return {"outcome": "added", "written": written, "kept": kept}

    def _finish(self, row: dict) -> dict:
        """Write only the missing assets of an imported first revision that is still the block's only revision,
        names this entry and version, and equals the packaged rewritten definition."""
        item = entry(row.get("key"))
        as_key = row.get("as", item.key)
        try:
            key_name(as_key)
        except WorkshopError:
            raise LibraryError("invalid_key", 400) from None
        out = {"key": item.key, "as": as_key}
        if self.state.workspace.read_only:
            return {**out, "outcome": "read_only"}
        provenance, h = self._imported(as_key)
        if h is None:
            return {**out, "outcome": "finish_unavailable", "reason": "different_source"}
        if h["retained"] != [h["current"]]:
            return {**out, "outcome": "finish_unavailable", "reason": "edited"}
        revision = h["current"]
        if (provenance is None or provenance.get("key") != item.key
                or provenance.get("version") != item.version):
            return {**out, "outcome": "finish_unavailable", "reason": "different_source"}
        try:
            saved = self.service.assets.saved(as_key, revision)
        except WorkshopError:
            return {**out, "outcome": "finish_unavailable", "reason": "edited"}
        role = row.get("role")
        if item.group == "llm" and role is None:
            role = next((s.get("role") for s in saved.definition.get("steps", []) if s.get("kind") == "model"),
                        DEFAULT_ROLE)
        packaged = definition_sha256(rewrite(item, as_key, role if item.group == "llm" else None))
        if packaged != definition_sha256(saved.definition) or provenance.get("definition_sha256") != packaged:
            return {**out, "outcome": "finish_unavailable", "reason": "different_source"}
        missing = self._missing(as_key, revision)
        if not missing:
            return {**out, "revision": revision, "outcome": "added", "written": [], "kept": []}
        return {**out, "revision": revision,
                **self._assets(item, as_key, revision, missing, first_edit="finish_unavailable")}
