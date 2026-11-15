"""Agentic Chat capabilities (0.2.2, section 5): names, eligibility, the classification store, the inventory,
approval binding and the MCP guard.

A capability is one built-in tool, one observed MCP tool, or one saved v4 block at its exact revision. AgentWiz
decides what may run: the model sees a fixed inventory captured at Send, and nothing in a result, a document or
a model turn can change it, a classification, a trust grant, a label, a limit or an approval.

Every MCP tool is unknown-effect and disabled until the owner classifies it in Settings → MCP servers (O-4).
`no_host_files` is the owner's attestation that a tool cannot read or write files on this computer; AgentWiz
cannot verify it, and a tool without it is never offered, directly or wrapped by a Tool block.

Knows nothing of HTTP. Holds no lock of AgentWiz's state while it reads a file.
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from . import agent_tools, blocks, checks, jsonio, outbound_policy
from .workshop import WorkshopError
from .workspace import write_atomic_bytes

FILE = "agent_capabilities.json"
LIMIT = 1 << 20
ROW_LIMIT = 512
EFFECTS = ("read-only", "changes state", "unknown")
NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
CAPABILITY_LIMIT = 24
DEFINITION_BYTES = 65_536
DESCRIPTION_LIMIT = 1024
APPROVAL_WAIT_S = 120.0
DEFAULTS = {"enabled": False, "effects": "unknown", "no_host_files": False, "results_local_only": True}
_KEYS = ("url", "server", "tool", "schema_digest")


# --- names (5.1, F-15) ------------------------------------------------------------------------------------------
def _h8(identity: object) -> str:
    return hashlib.sha256(jsonio.encode_exact(identity).encode("utf-8")).hexdigest()[:8]


def _clean(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", text)


def compose(prefix: str, parts: list[str], identity: object) -> str:
    """`<prefix>_<parts...>_<h8>`, cut so the whole name fits 64 characters; the suffix is always present."""
    suffix = "_" + _h8(identity)
    readable = prefix + "_" + "_".join(_clean(p) for p in parts)
    return readable[:64 - len(suffix)] + suffix


def mcp_name(url: str, server: str, tool: str) -> str:
    return compose("mcp", [server, tool], [url, server, tool])


def block_fingerprint(block: Any) -> str:
    """The revision fingerprint of a block: its referenced files by content and its captured contracts."""
    return hashlib.sha256(jsonio.encode_exact(
        {"files": dict(sorted(block.files.items())),
         "contracts": {side: {n: c.digest for n, c in ports.items()} for side, ports in block.contracts.items()}}
    ).encode("utf-8")).hexdigest()


def block_name(key: str, fingerprint: str) -> str:
    return compose("block", [key], [key, fingerprint])


# --- the classification store (5.3) ------------------------------------------------------------------------------
class Store:
    """`<workspace>/agent_capabilities.json`, format_version 1, with its own revision (the file's digest).
    Edited only from Settings → MCP servers, by a person, through `update`."""

    def __init__(self, workspace_path):
        self.path = workspace_path
        self._lock = threading.Lock()

    def read(self) -> tuple[str | None, list[dict]]:
        result = blocks.read_file(self.path, FILE, LIMIT)
        if result.fingerprint == ("missing",):
            return None, []
        if result.data is not None:
            try:
                obj = jsonio.decode_file_exact(result.data)
                rows = obj["rows"]
                if (set(obj) != {"format_version", "rows"} or type(obj["format_version"]) is not int
                        or obj["format_version"] != 1 or not isinstance(rows, list) or len(rows) > ROW_LIMIT):
                    raise ValueError
                for row in rows:
                    _check_row(row)
                return hashlib.sha256(result.data).hexdigest(), rows
            except (ValueError, KeyError, TypeError):
                pass
        raise WorkshopError("capabilities_unavailable", 409)

    def revision(self) -> str | None:
        return self.read()[0]

    def row(self, rows: list[dict], url: str, server: str, tool: str, digest: str) -> dict:
        for row in rows:
            if (row["url"], row["server"], row["tool"], row["schema_digest"]) == (url, server, tool, digest):
                return row
        return {"url": url, "server": server, "tool": tool, "schema_digest": digest, **DEFAULTS}

    def update(self, state: Any, body: Mapping) -> dict:
        """Replace one row's classification. Body: expected_revision and the row (all fields). A changed input
        schema is a new row with the defaults: rows are keyed by the schema digest."""
        if set(body) != {"expected_revision", "row"} or not isinstance(body["row"], dict):
            raise WorkshopError("invalid", 400)
        if state.workspace.read_only:
            raise WorkshopError("read_only", 409)
        row = dict(body["row"])
        _check_row(row)
        writer = state.register_writer("agent capabilities")
        try:
            with self._lock:
                revision, rows = self.read()
                if revision != body["expected_revision"]:
                    raise WorkshopError("stale", 409)
                kept = [r for r in rows if tuple(r[k] for k in _KEYS) != tuple(row[k] for k in _KEYS)]
                kept.append(row)
                if len(kept) > ROW_LIMIT:
                    raise WorkshopError("too_large", 400)
                write_atomic_bytes(self.path, FILE, jsonio.encode_file_exact({"format_version": 1, "rows": kept}))
        finally:
            writer.release()
        revision, rows = self.read()
        return {"format_version": 1, "revision": revision, "rows": rows}


def _check_row(row: object) -> None:
    if (not isinstance(row, dict) or set(row) != {*_KEYS, *DEFAULTS}
            or any(not isinstance(row[k], str) or not row[k] or len(row[k]) > 4096 for k in _KEYS)
            or re.fullmatch(r"[0-9a-f]{64}", row["schema_digest"]) is None
            or row["effects"] not in EFFECTS
            or any(type(row[k]) is not bool for k in ("enabled", "no_host_files", "results_local_only"))):
        raise ValueError("invalid classification row")


# --- capabilities ---------------------------------------------------------------------------------------------------
@dataclass
class Capability:
    name: str
    kind: str                       # "builtin" | "mcp" | "block"
    identity: dict                  # the full identity recorded in the trace
    description: str
    parameters: dict
    effects: str                    # read-only | changes state | unknown
    approval: bool                  # every call asks (5.4)
    restricted_definition: bool = False   # name/description/schema are protected (8.1)
    results_local_only: bool = False
    builtin: str | None = None
    # MCP
    url: str | None = None
    server: str | None = None
    tool: str | None = None
    schema_digest: str | None = None
    # block
    key: str | None = None
    revision: int | None = None
    loaded: Any = None
    fingerprint: str | None = None
    wrapped: "Capability | None" = None     # an MCP-bound Tool block's underlying tool
    excluded_reason: str | None = None

    def definition_view(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}

    def view(self) -> dict:
        out = {"name": self.name, "kind": self.kind, "identity": self.identity, "description": self.description,
               "effects": self.effects, "approval": self.approval, "local_only_results": self.results_local_only,
               "protected_design": self.restricted_definition}
        if self.revision is not None:
            out["revision"] = self.revision
        if self.excluded_reason:
            out["excluded"] = self.excluded_reason
        return out


def schema_contract(schema: Any) -> checks.Contract:
    """A JSON contract over `schema` with user-policy messages: locations and rule names, never values."""
    return checks.Contract("json", False, "", schema, (), {"format": "json", "schema": schema})


def builtin_capabilities(has_documents: bool) -> list[Capability]:
    out = []
    for tool in (*agent_tools.DOCUMENT_TOOLS, *agent_tools.UTILITY_TOOLS):
        if tool in ("list_documents", "search_documents", "read_document") and not has_documents:
            continue
        out.append(Capability(agent_tools.capability_name(tool), "builtin", {"builtin": tool},
                              agent_tools.DESCRIPTIONS[tool], agent_tools.SCHEMAS[tool], "read-only"
                              if tool != "create_draft" else "changes state", False, builtin=tool))
    return out


def _bounded(text: object) -> str:
    return text[:DESCRIPTION_LIMIT] if isinstance(text, str) else ""


def mcp_candidates(state: Any, rows: list[dict]) -> tuple[list[Capability], list[dict]]:
    """Every observed tool of every configured server: (eligible capabilities, ineligible rows with reasons)."""
    from .state import NoSuchServer
    eligible, refused = [], []
    servers = state.view()["mcp"]["servers"]
    for server in servers:
        try:
            snapshot = state.mcp_snapshot_for_block(server)
        except NoSuchServer:
            continue
        entry = state.mcp_client.entry(snapshot.identity)
        for tool in state.mcp_client.observed_tools(snapshot.identity):
            found = state.mcp_client.observed_schema(snapshot.identity, tool)
            if found is None:
                continue
            digest, schema, description = found
            row = state.agent.store.row(rows, snapshot.url, server, tool, digest)
            base = {"url": snapshot.url, "server": server, "tool": tool, "schema_digest": digest,
                    "classification": {k: row[k] for k in DEFAULTS}}
            reason = None
            if not row["enabled"]:
                reason = "not_enabled"
            elif not row["no_host_files"]:
                reason = "host_files"
            elif entry is not None and (tool in entry.not_offered or tool not in entry.offered):
                reason = "not_offered"
            elif not isinstance(schema, dict) or schema.get("type", "object") != "object":
                reason = "schema_unsupported"
            if reason:
                refused.append({**base, "reason": reason})
                continue
            eligible.append(Capability(mcp_name(snapshot.url, server, tool), "mcp",
                                       {"url": snapshot.url, "server": server, "tool": tool},
                                       _bounded(description), schema, row["effects"],
                                       row["effects"] != "read-only", results_local_only=row["results_local_only"],
                                       url=snapshot.url, server=server, tool=tool, schema_digest=digest))
    return eligible, refused


def _port_schema(block: Any, declared: Any) -> Any:
    contract = block.contracts["inputs"][declared.name].parsed
    if contract.format == "text":
        node: Any = {"type": "string"}
    else:
        node = contract.schema if contract.schema is not None else {}
    if isinstance(node, dict):
        node = dict(node)
        if declared.description and "description" not in node:
            node["description"] = declared.description
    return node


def block_candidate(state: Any, loaded: Any, mcp: dict[tuple, Capability]) -> tuple[Capability | None, str | None]:
    """The capability of one saved block, or the reason it is not eligible (5.2)."""
    block = loaded.block
    if block is None or loaded.problem is not None:
        return None, "unusable"
    if block.format_version != blocks.WORKFLOW_FORMAT_VERSION:
        return None, "format"
    step = block.step
    if step.kind not in ("model", "tool", "transform", "python"):
        return None, "kind"
    properties = {}
    for declared in block.inputs:
        node = _port_schema(block, declared)
        if not isinstance(node, (dict, bool)):
            return None, "input_contract"
        properties[declared.name] = node
    parameters = {"type": "object", "properties": properties, "required": [d.name for d in block.inputs],
                  "additionalProperties": False}
    wrapped = None
    effects, approval = "read-only", False
    if step.mcp:
        try:
            snapshot = state.mcp_snapshot_for_block(step.server)
        except Exception:  # noqa: BLE001 - NoSuchServer
            return None, "server_missing"
        wrapped = mcp.get((snapshot.url, step.server, step.tool))
        if wrapped is None:
            return None, "tool_ineligible"
        effects, approval = wrapped.effects, wrapped.approval
    fingerprint = block_fingerprint(block)
    protected = bool(block.context_local_only or loaded.workshop.get("protection_clear_pending"))
    capability = Capability(block_name(loaded.name, fingerprint), "block",
                            {"block": loaded.name, "revision": loaded.revision, "fingerprint": fingerprint},
                            _bounded(block.description), parameters, effects, approval,
                            restricted_definition=protected,
                            results_local_only=any(port.local_only for port in block.outputs),
                            key=loaded.name, revision=loaded.revision, loaded=loaded, fingerprint=fingerprint,
                            wrapped=wrapped)
    return capability, None


def inventory(state: Any, conversation: Any, *, trusted_local: bool, has_documents: bool) -> dict:
    """Every capability the panel can tick, the excluded items with reasons, and the classification revision.
    Exclusion for a non-local orchestrator (8.2) never changes a label."""
    revision, rows = state.agent.store.read()
    capabilities = builtin_capabilities(has_documents)
    mcp, refused = mcp_candidates(state, rows)
    by_identity = {(c.url, c.server, c.tool): c for c in mcp}
    excluded: list[dict] = []
    for capability in mcp:
        if not trusted_local and capability.results_local_only:
            capability.excluded_reason = "results_local_only"
    capabilities.extend(mcp)
    listing = state.block_listing()
    blocks_refused = []
    for row in listing["blocks"]:
        loaded = state.block_loaded(row.get("name"))
        if loaded is None:
            continue
        capability, reason = block_candidate(state, loaded, by_identity)
        if capability is None:
            blocks_refused.append({"block": loaded.name, "revision": loaded.revision, "reason": reason})
            continue
        if not trusted_local and (capability.restricted_definition or capability.results_local_only):
            capability.excluded_reason = "protected_design" if capability.restricted_definition else "local_only_output"
        capabilities.append(capability)
    for capability in capabilities:
        if capability.excluded_reason:
            excluded.append({"name": capability.name, "reason": capability.excluded_reason})
    return {"classification_revision": revision, "capabilities": capabilities, "excluded": excluded,
            "mcp_refused": refused, "blocks_refused": blocks_refused}


def definitions_size(capabilities: list[Capability]) -> int:
    return len(jsonio.encode_exact([c.definition_view() for c in capabilities]).encode("utf-8"))


def collision(capabilities: list[Capability]) -> str | None:
    seen = set()
    for capability in capabilities:
        if capability.name in seen:
            return capability.name
        seen.add(capability.name)
    return None


# --- approval (5.4, F-9) -----------------------------------------------------------------------------------------------
@dataclass
class Approval:
    """One pending approval, bound to the request the MCP server will actually receive. Single-use."""
    run_id: str
    call_id: str
    capability: str
    destination: dict
    schema_digest: str
    classification_revision: str | None
    arguments_sha256: str
    arguments_text: str
    expires: float                       # monotonic
    decision: str | None = None          # "approve" | "deny" | "expired" | "void"
    decided_at: str | None = None
    used: bool = False
    condition: threading.Condition = field(default_factory=threading.Condition)

    def view(self) -> dict:
        return {"run_id": self.run_id, "call_id": self.call_id, "capability": self.capability,
                "destination": self.destination, "arguments_text": self.arguments_text,
                "arguments_sha256": self.arguments_sha256,
                "expires_in_s": max(0, round(self.expires - time.monotonic(), 1))}

    def valid_for(self, arguments_text: str, schema_digest: str, classification_revision: str | None) -> bool:
        return (self.decision == "approve" and not self.used
                and hashlib.sha256(arguments_text.encode("utf-8")).hexdigest() == self.arguments_sha256
                and schema_digest == self.schema_digest and classification_revision == self.classification_revision)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def restricted_arguments_destination(snapshot: Any, tool: str) -> dict:
    """The exact MCP identity whose grant restricted arguments need (8.3)."""
    return outbound_policy.tool_identity(snapshot, tool)
