"""Agentic Chat: the headless loop, run capture, limits, the deadline, turn classification and the trace
(0.2.2, section 4), with the conversation manager that owns the one live conversation and the one agent run of
this process (F-20, F-27).

`AgentRun.run()` is headless and knows no HTTP: it reports to an observer whose failures are ignored, as block
runs do. The model may ask AgentWiz to run one approved capability per turn; AgentWiz validates every request,
decides what may run and where data may go, and returns a checked result. Nothing in a result, a document or a
model turn can change the inventory, a classification, a trust grant, a label, a limit or an approval.

Lock order: the manager's lock, then a conversation's lock (`while_idle` commits an upload inside the manager's
hold); never the reverse. The manager's lock is never held while a state, store or token lock is taken, nor during
a model or tool call, a wait, a check or a content read or write, and `Conversation.close()`, which waits for
pending writes, is always called after it is released. Decide under the lock, act after releasing it.

From before a run's scope is captured until it detaches, the manager is never idle: `prepare_run` holds an owned
run reservation, which every idle-only change honours and `attach` verifies (addendum 3.2).
Messages name rules and locations, never values.
"""
from __future__ import annotations

import copy
import dataclasses
from contextlib import contextmanager
import json
import secrets
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Mapping

import modelspell
from modelspell import (Cancelled, ConfigError, ModelSpellError, ToolDefinition, ToolOutput,
                        UnsupportedOperation, UnsupportedSetting)

from . import (__version__, agent_capabilities as caps, agent_documents as docs, agent_tools, chat_history, chat_tasks, checks, jsonio,
               model_call, outbound_policy, python_step, runner, runs)
from .agent_tools import ToolError
from .errors import report_internal
from .task_settings import TaskSettingError, parse_task

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "agent_system.md")
SYSTEM_PROMPT_ID = "agent_system"
SYSTEM_PROMPT_VERSION = 2
RECORD_VERSION = 1
# User-editable limits (4.6, F-14): (default, maximum).
LIMITS = {"model_turns": (8, 20), "actions": (6, 20), "seconds": (300, 1800)}
# Constants (4.6).
CORRECTION_LIMIT = 3
CHILD_SEND_LIMIT = 10
ARGUMENT_BYTES = 262_144
RESULT_BYTES = 32_768
REQUEST_BYTES = 1 << 20
OUTPUT_BYTES = 1 << 20
RUNS_KEPT = chat_history.MAX_TURNS
CHAT_CONTEXT_TURNS = 6
CHAT_CONTEXT_BYTES = 64 << 10
ROUTE_REMEDIES = {"lmstudio": "choose a reasoning level other than Off, or clear the setting the route does not send",
                  "openrouter": "choose Automatic routing or pin one provider in Model settings",
                  "openai_compat": "clear the reasoning level"}
PREFLIGHT_NOTE = ("Preflight shows that modelSpell accepts the request shape; it does not show that this server, "
                  "model and chat template produce usable tool calls.")
FEEDBACK = {
    "unknown_capability": "no offered tool has that name; use one of the offered tools",
    "malformed_arguments": "the arguments are not one JSON object within the size limit",
    "schema_mismatch": "the arguments do not match the tool's schema",
    "input_check_failed": "the arguments do not pass the block's input checks",
    "one_call_per_turn": "ask for at most one tool call per turn; none of these calls was run",
    "denied": "the user denied this call",
    "no_result": "the call's outcome is unknown; it was not repeated",
    "tool_failed": "the tool reported a failure",
    "block_failed": "the block run failed",
    "refused": "AgentWiz refused this call before anything was sent",
    "configuration_changed": "the configuration changed since the run started; this call was not run",
    "busy": "the execution slots are busy; nothing was sent",
}


class AgentError(Exception):
    """A refusal before a run is created: a real HTTP status with a fixed code (4.9)."""

    def __init__(self, code: str, status: int = 409, **extra: Any):
        super().__init__(code)
        self.code = code
        self.status = status
        self.extra = extra


def _system_text() -> str:
    return SYSTEM_PROMPT.read_text(encoding="utf-8")


def _limits(raw: object) -> dict:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or set(raw) - {*LIMITS, "tokens", "cost"}:
        raise AgentError("invalid_limits", 400)
    out = {}
    for name, (default, maximum) in LIMITS.items():
        value = raw.get(name, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise AgentError("invalid_limits", 400, limit=name)
        out[name] = value
    for name in ("tokens", "cost"):
        value = raw.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0):
            raise AgentError("invalid_limits", 400, limit=name)
        out[name] = value
    return out


def _plain(value: Any) -> Any:
    """modelSpell's frozen replay as plain JSON values, for measuring."""
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _size(value: Any) -> int:
    return len(json.dumps(_plain(value), ensure_ascii=False, default=str).encode("utf-8"))


def _envelope(value: Mapping) -> str:
    return jsonio.encode_exact(dict(value))


# --- the observer --------------------------------------------------------------------------------------------------
class Observer:
    """What a run reports to. Every method may fail; failures are ignored (delivery never affects the run)."""

    def event(self, event: dict) -> None: ...


class _Safe:
    def __init__(self, observer: Observer | None):
        self.observer = observer or Observer()

    def event(self, event: dict) -> None:
        try:
            self.observer.event(event)
        except Exception:  # noqa: BLE001
            pass


class _ChildObserver(runner.Observer):
    """A child block run's observer: learns its run ID, stops it if the parent was stopped first, and charges
    reported usage to the parent's ceilings."""

    def __init__(self, parent: "AgentRun", action: dict):
        self.parent = parent
        self.action = action

    def started(self, event: dict) -> None:
        run_id = event.get("run_id")
        with self.parent.lock:
            self.parent.child_run_id = run_id
            stop = self.parent.token.cancelled
        if stop and run_id:
            try:
                self.parent.state.store.stop_run(run_id, runs.USER_STOP)
            except runs.NoSuchRunInMemory:
                pass

    def attempt_committed(self, record: dict) -> None:
        if record.get("routing", {}).get("probe_usage") == "unreported":
            self.parent.note_unreported_usage()
        if record.get("model_called") and record.get("sent") is not False:
            self.parent.add_usage(record.get("result") or record.get("partial"))


# --- capture (4.3) -------------------------------------------------------------------------------------------------
@dataclasses.dataclass
class Capture:
    revision: int                       # the configuration revision of the request snapshot
    snapshot: Any                       # model_call.RequestSnapshot, key included
    task: Any
    arguments: dict
    trusted: Any                        # outbound_policy.Dispatch for a trusted local orchestrator, else None
    capabilities: list
    by_name: dict
    definitions: list                   # modelspell.ToolDefinition
    definition_bytes: int
    classification_revision: str | None
    allowed: list                       # document and draft IDs the run may use
    excluded: list
    memory: list
    limits: dict
    branch: str                         # "tools" | "text"
    mcp_snapshots: dict                 # server -> mcp_client.Snapshot taken at capture
    role_captures: dict                 # role -> state.RoleCapture with the key resolved
    mode: str | None = None
    role: str = "fast"
    model_choice: dict | None = None
    chat_task: str = "general"


def _snapshot(state, revision, mode=None, role="fast", choice=None):
    return state.snapshot(revision) if mode is None else state.chat_snapshot(revision, role, mode, choice)


def _trusted(state: Any, snapshot: Any, revision: int, mode=None, role="fast", choice=None):
    def unchanged() -> bool:
        try:
            live = _snapshot(state, revision, mode, role, choice)
        except Exception:  # noqa: BLE001 - StaleRevision
            return False
        return live == snapshot
    try:
        return outbound_policy.Dispatch.prepare(state, outbound_policy.model_identity(snapshot), unchanged)
    except outbound_policy.Refused:
        return None


def _model_choice(body, mode):
    try:
        choice = chat_history.model_choice(body.get("model_choice"))
    except ValueError:
        raise AgentError("invalid_model_choice", 400) from None
    if mode is None and choice is not None:
        raise AgentError("invalid", 400)
    return choice


def plan(state: Any, conversation: Any, body: Mapping, *, for_send: bool) -> Capture:
    """Filter the inventory and choose the branch. The panel's preview and Send use this one function, so the
    preview never shows a refusal that Send would not make, or the reverse (4.2)."""
    revision = body.get("revision")
    if type(revision) is not int:
        raise AgentError("invalid", 400)
    mode, role = body.get("mode"), body.get("role", "fast")
    if mode is not None and (mode not in ("cloud", "local") or not isinstance(role, str)):
        raise AgentError("invalid", 400)
    if mode == "cloud" and conversation is not None and conversation.private:
        raise AgentError("private_conversation", 409, remedies=["new_conversation", "trusted_local_model"])
    choice = _model_choice(body, mode)
    chat_task = body.get("chat_task", "general")
    if not isinstance(chat_task, str) or chat_task not in chat_tasks.TASKS or ("chat_task" in body and mode is None):
        raise AgentError("invalid_chat_task", 400)
    use_context = body.get("use_context", False)
    if (type(use_context) is not bool or (use_context and body.get("follow_up") is not None)
            or (mode is None and use_context)):
        raise AgentError("invalid", 400)
    snapshot = _snapshot(state, revision, mode, role, choice)
    if snapshot.model.strip() == "":
        raise AgentError("no_model", 409)
    task = parse_task(body.get("task"))
    arguments = model_call.task_arguments(task, snapshot.kind, snapshot.provider)   # TaskSettingError: 400
    trusted = _trusted(state, snapshot, revision, mode, role, choice)
    if mode == "local" and trusted is None:
        raise AgentError("destination_refused", 409, remedies=["trusted_local_model"])
    items = {item["id"]: item for item in conversation.items_view()} if conversation is not None else {}
    selected = body.get("items", []) if mode is not None else list(items)
    if not isinstance(selected, list) or any(not isinstance(i, str) or i not in items for i in selected):
        raise AgentError("unknown_document", 400)
    if mode == "cloud" and any(items[i]["local_only"] for i in selected):
        raise AgentError("local_only_document", 409, remedies=["trusted_local_model"])
    allowed = [i for i in selected if trusted is not None or not items[i]["local_only"]]
    found = caps.inventory(state, conversation, trusted_local=trusted is not None,
                           has_documents=bool(allowed if mode is not None else items),
                           builtins=(body.get("builtins", True) is True
                                     and (mode is None or state.chat_preferences()["builtins"])),
                           mode=mode, web_on=bool(mode == "cloud" and state.chat_preferences()["webspell"]
                                                  and body.get("webspell", True) is True))
    by_name = {c.name: c for c in found["capabilities"]}
    ticked = body.get("capabilities")
    if "capabilities" not in body:
        ticked = [{"name": c.name, **({"revision": c.revision} if c.revision is not None else {})}
                  for c in found["capabilities"] if mode is not None and not c.excluded_reason
                  and (c.kind == "builtin" or c.server == "webspell"
                       or (c.kind == "block" and state.block_library.provenance(c.key)))]
    if not isinstance(ticked, list):
        raise AgentError("invalid", 400)
    chosen = []
    for entry in ticked:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or set(entry) - {"name", "revision"}:
            raise AgentError("invalid", 400)
        capability = by_name.get(entry["name"])
        if capability is None:
            if for_send:
                raise AgentError("capability_unavailable", 409, capability=entry["name"])
            continue
        if capability.revision is not None and entry.get("revision") != capability.revision:
            raise AgentError("configuration_changed", 409, capability=entry["name"])
        if capability.excluded_reason:
            if mode is not None and for_send:
                raise AgentError("capability_unavailable", 409, capability=capability.name)
            continue                                            # listed under exclusions, never offered
        chosen.append(capability)
    name = caps.collision(chosen)
    if name is not None:
        raise AgentError("capability_name_collision", 409, capability=name)
    definitions = [ToolDefinition(c.name, c.description, c.parameters) for c in chosen]
    size = caps.definitions_size(chosen)
    if size > caps.DEFINITION_BYTES:
        raise AgentError("definition_limit", 409, limit=caps.DEFINITION_BYTES)
    excluded = list(found["excluded"]) + [
        {"name": i, "reason": "local_only_document"} for i in selected if i not in allowed]
    memory = conversation.memory_entries() if conversation is not None and mode is None else []
    if use_context and conversation is not None:
        memory = state.agent.chat_context(conversation)
        if mode == "cloud" and any(entry.restricted for entry in memory):
            raise AgentError("private_conversation", 409)
    if mode is not None and body.get("follow_up") is not None:
        parent = state.agent._run(body["follow_up"])
        if parent.conversation is not conversation or parent.answer is None or parent.state_name == "running":
            raise AgentError("invalid_follow_up", 409)
        if mode == "cloud" and parent.restricted:
            raise AgentError("private_conversation", 409)
        memory = [docs.MemoryEntry(parent.message, parent.answer, [], parent.restricted)]
    if conversation is not None and mode is None:
        view = conversation.memory_view()
        if view["on"] and view["entries"] >= docs.MEMORY_LIMIT:
            raise AgentError("memory_full", 409, remedies=["new_conversation", "clear_memory"])
        if trusted is None and any(e.restricted for e in memory):
            raise AgentError("memory_restricted", 409,
                             remedies=["memory_off", "clear_memory", "trusted_local_model"])
    branch = "tools" if chosen else "text"
    try:
        if branch == "tools":
            model_call.preflight_request(snapshot, arguments, tools=definitions)
        else:
            model_call.preflight_request(snapshot, arguments)
    except (ConfigError, UnsupportedSetting, UnsupportedOperation) as exc:   # pre-send validation text only
        raise AgentError("route_refused", 409, message=f"{type(exc).__name__}: {exc}",
                         remedy=ROUTE_REMEDIES.get(snapshot.kind), branch=branch) from None
    mcp_snapshots, role_captures = {}, {}
    if for_send:
        for capability in chosen:
            if capability.kind == "mcp" and capability.server not in mcp_snapshots:
                mcp_snapshots[capability.server] = state.mcp_snapshot_for_block(capability.server)
            if capability.kind == "block":
                step = capability.loaded.block.step
                if step.kind == "model" and step.role not in role_captures:
                    try:
                        binding = state.role_capture(step.role) if mode is None else state.role_capture(step.role, mode)
                    except Exception:  # noqa: BLE001 - RoleUnresolved: refused when the block is called
                        continue
                    key = outbound_policy.credential(binding.snapshot.endpoint)
                    snap = dataclasses.replace(binding.snapshot, endpoint=dataclasses.replace(
                        binding.snapshot.endpoint, api_key=key, api_key_env=None))
                    role_captures[step.role] = dataclasses.replace(binding, snapshot=snap)
                if step.mcp and step.server not in mcp_snapshots:
                    mcp_snapshots[step.server] = state.mcp_snapshot_for_block(step.server)
        # Re-verify every revision captured above (4.3 step 3).
        if _snapshot(state, revision, mode, role, choice) != snapshot:
            raise AgentError("configuration_changed", 409)
        if state.agent.store.revision() != found["classification_revision"]:
            raise AgentError("configuration_changed", 409)
        for capability in chosen:
            if capability.kind == "block":
                live = state.block_loaded(capability.key)
                if live is None or live.revision != capability.revision:
                    raise AgentError("configuration_changed", 409, capability=capability.name)
    return Capture(revision, snapshot, task, arguments, trusted, chosen, {c.name: c for c in chosen}, definitions,
                   size, found["classification_revision"], allowed, excluded, memory,
                   _limits(body.get("limits")), branch, mcp_snapshots, role_captures, mode, role, choice, chat_task)


def preview(state: Any, conversation: Any, body: Mapping) -> dict:
    """The panel's view before Send (4.1): destination, preflight, capabilities, exclusions and limits."""
    mode, role = body.get("mode"), body.get("role", "fast")
    choice = _model_choice(body, mode)
    snapshot = (_snapshot(state, body["revision"], mode, role, choice)
                if type(body.get("revision")) is int else None)
    out: dict = {"format_version": 1, "preflight_note": PREFLIGHT_NOTE,
                 "limits": {name: {"default": d, "maximum": m} for name, (d, m) in LIMITS.items()},
                 "constants": {"corrections": CORRECTION_LIMIT, "approval_wait_s": caps.APPROVAL_WAIT_S,
                               "child_sends": CHILD_SEND_LIMIT,
                               "definition_bytes": caps.DEFINITION_BYTES, "argument_bytes": ARGUMENT_BYTES,
                               "result_bytes": RESULT_BYTES, "request_bytes": REQUEST_BYTES,
                               "output_bytes": OUTPUT_BYTES},
                 "usage_note": "Token and cost ceilings act only after the backend reports usage; they cannot "
                               "prevent a request already sent."}
    if snapshot is not None:
        trusted = _trusted(state, snapshot, body["revision"], mode, role, choice)
        out["destination"] = {"kind": snapshot.kind, "url": snapshot.endpoint.base_url, "model": snapshot.model,
                              "provider": snapshot.provider,
                              "locality": outbound_policy.locality(snapshot.endpoint.base_url),
                              "trusted_local": trusted is not None}
        found = caps.inventory(state, conversation, trusted_local=trusted is not None,
                               has_documents=bool(conversation and (conversation.items if mode is None else
                                   any(i["id"] in body.get("items", []) and
                                       (trusted or not i["local_only"]) for i in conversation.items_view()))),
                               builtins=(body.get("builtins", True) is True
                                         and (mode is None or state.chat_preferences()["builtins"])),
                               mode=mode, web_on=bool(mode == "cloud" and state.chat_preferences()["webspell"]
                                                      and body.get("webspell", True) is True))
        out["capabilities"] = [c.view() for c in found["capabilities"]]
        out["excluded"] = list(found["excluded"])
        out["mcp_refused"] = found["mcp_refused"]
        out["blocks_refused"] = found["blocks_refused"]
        out["classification_revision"] = found["classification_revision"]
        if conversation is not None:
            for item in conversation.items_view():
                if item["local_only"] and trusted is None:
                    out["excluded"].append({"name": item["id"], "reason": "local_only_document"})
        try:
            captured = plan(state, conversation, body, for_send=False)
        except AgentError as exc:
            out["preflight"] = {"ok": False, "code": exc.code, **exc.extra}
        except TaskSettingError as exc:
            out["preflight"] = {"ok": False, "code": "task_setting", "message": f"TaskSettingError: {exc}"}
        else:
            out["preflight"] = {"ok": True, "branch": captured.branch,
                                "offered": [c.name for c in captured.capabilities],
                                "definition_bytes": captured.definition_bytes}
    if conversation is not None:
        out["documents"] = conversation.items_view()
        out["memory"] = conversation.memory_view()
    return out


# --- the run ---------------------------------------------------------------------------------------------------------
class _End(Exception):
    """Ends the loop with a stop reason."""

    def __init__(self, reason: str, detail: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


class AgentRun:
    def __init__(self, manager: "Manager", conversation: Any, capture: Capture, message: str, observer: Observer | None):
        self.manager = manager
        self.state = manager.state
        self.conversation = conversation
        self.capture = capture
        self.message = message
        from . import webspell
        self.web_urls = webspell.prompt_urls(message)
        self.web_sources: set[str] = set()
        self.web_mixed = bool(capture.allowed or capture.memory or any(
            c.kind == "mcp" and c.server != webspell.SERVER
            or c.kind == "block" and (not self.state.block_library.provenance(c.key)
                                      or c.loaded.workshop.get("revision_number") != 1)
            for c in capture.capabilities))
        self.observer = _Safe(observer)
        self.lock = threading.Lock()
        self.token = runs.CancelToken()
        self.handle = modelspell.CancelHandle()
        self.token.on_cancel(self.handle.cancel)
        self.token.on_cancel(self._wake)
        self.run_id = ""
        self.store_run: Any = None
        self.started = time.monotonic()
        self.deadline = self.started + capture.limits["seconds"]
        self.timer: Any = None
        self.scope = docs.Scope(conversation, capture.allowed)
        self.transcript: list = []
        self.transcript_bytes = 0
        self.resolved_arguments = None
        self.routing_selection = None
        self.served_provider = None
        self.served_model = None
        self.turns = 0
        self.actions = 0
        self.corrections = 0
        self.child_sends = 0
        self.nested_exceeded = False
        self.restricted = capture.mode == "local"
        self.trace: list[dict] = []           # live, with payloads (in process memory only)
        self.saved_trace: list[dict] = []     # identities, outcomes, sizes and digests
        self.pending: caps.Approval | None = None
        self.child_run_id: str | None = None
        self.state_name = "running"
        self.stop_reason: str | None = None
        self.stop_detail: str | None = None
        self.answer: str | None = None
        self.partial = ""
        self.reasoning = ""
        self.drafts: list = []
        self.reservation: Reservation | None = None
        self.usage = {"input_tokens": 0, "output_tokens": 0, "reported": 0, "unreported": 0}
        self.accounting = None
        self.cost = 0.0
        self.cost_reported = False
        self.sent_flags: list = []
        self.output_exceeded = False
        self.turn_output = 0
        self.current_approval: caps.Approval | None = None
        self.save_status = "not_requested"
        self.save_detail: dict = {}
        self.ended_at: str | None = None
        self.started_at = runs.utc_now()

    # --- control ------------------------------------------------------------------------------------------------
    def cancel(self, reason: str) -> bool:
        return self.token.cancel(reason)

    def _wake(self) -> None:
        with self.lock:
            approval = self.pending
        if approval is not None:
            with approval.condition:
                approval.condition.notify_all()

    def _stop_code(self) -> str:
        reason = self.token.reason
        return {"time_limit": "time_limit", "shutdown": "interrupted", "output_limit": "output_limit"}.get(
            reason or "", "stopped")

    def _emit(self, event: dict) -> None:
        self.observer.event({**event, "run_id": self.run_id})

    def note_unreported_usage(self) -> None:
        with self.lock:
            self.accounting = chat_history.merge_usage(self.accounting, chat_history.usage_summary(None))

    def add_usage(self, result: dict | None) -> None:
        accounting = chat_history.usage_summary(result)
        with self.lock:
            self.accounting = chat_history.merge_usage(self.accounting, accounting)
            for key in ("input_tokens", "output_tokens"):
                self.usage[key] += accounting[key] or 0
            reported = all(accounting[key] is not None for key in ("input_tokens", "output_tokens"))
            self.usage["reported" if reported else "unreported"] += 1
            if accounting["cost"] is not None:
                self.cost += accounting["cost"]
                self.cost_reported = True

    def _record_chat_model(self, result) -> None:
        with self.lock:
            self.served_provider = result.provider
            self.served_model = result.model
        self.add_usage(model_call.result_to_dict(result))

    def _ceilings(self) -> None:
        limits = self.capture.limits
        with self.lock:
            tokens = self.usage["input_tokens"] + self.usage["output_tokens"]
            cost, reported = self.cost, self.cost_reported
        if limits["tokens"] is not None and tokens >= limits["tokens"]:
            raise _End("token_limit")
        if limits["cost"] is not None and reported and cost >= limits["cost"]:
            raise _End("cost_limit")

    def _config_unchanged(self) -> bool:
        try:
            return _snapshot(self.state, self.capture.revision, self.capture.mode, self.capture.role,
                             self.capture.model_choice) == self.capture.snapshot
        except Exception:  # noqa: BLE001 - StaleRevision
            return False

    def parent_guard(self) -> None:
        """The parent's Stop, deadline, configuration and admission checks, run at every child send gate (5.6)."""
        if self.token.cancelled:
            raise outbound_policy.Refused(self._stop_code())
        if time.monotonic() >= self.deadline:
            raise outbound_policy.Refused("time_limit")
        if not self._config_unchanged():
            raise outbound_policy.Refused("configuration_changed")
        if self.state.store.closed:
            raise outbound_policy.Refused("interrupted")

    def child_gate(self) -> bool:
        with self.lock:
            self.child_sends += 1
            if self.child_sends > CHILD_SEND_LIMIT:
                self.nested_exceeded = True
                return False
            return True

    def _promote(self) -> None:
        self.restricted = True
        if self.capture.mode is not None:
            with self.conversation.lock:
                self.conversation.private = True

    def _still_running(self) -> bool:
        return not self.token.cancelled and time.monotonic() < self.deadline

    def _check_scan(self) -> None:
        if not self._still_running():
            raise ToolError("stopped")

    # --- transcript ---------------------------------------------------------------------------------------------
    def _append(self, entry: Any, size: int) -> None:
        self.transcript.append(entry)
        self.transcript_bytes += size

    def _append_turn(self, turn: Any) -> None:
        self._append(turn, _size(turn.replay))

    def _append_output(self, call_id: str, content: str) -> None:
        self._append(ToolOutput(call_id, content), len(content.encode("utf-8")) + len(call_id) + 32)

    # --- the gate before every send (4.4 step 2, 8.3) ------------------------------------------------------------
    def _gate_send(self) -> None:
        if self.token.cancelled:
            raise _End(self._stop_code())
        if time.monotonic() >= self.deadline:
            raise _End("time_limit")
        if self.turns >= self.capture.limits["model_turns"]:
            raise _End("turn_limit")
        self._ceilings()
        if not self._config_unchanged():
            raise _End("configuration_changed")
        if self.capture.definition_bytes + self.transcript_bytes > REQUEST_BYTES:
            raise _End("request_limit")
        if self.restricted:
            # Restricted material only to a trusted local orchestrator whose grant still holds (8.3).
            if self.capture.trusted is None:
                raise _End("restricted_result")
            try:
                self.capture.trusted.check()
            except outbound_policy.Refused:
                raise _End("destination_refused") from None

    def _on_text(self, text: str) -> None:
        self.partial += text
        self._count(text)
        self._emit({"type": "text", "text": text, "model_turn": self.turns})

    def _on_reasoning(self, text: str) -> None:
        self.reasoning += text
        self._count(text)
        self._emit({"type": "reasoning", "text": text})

    def _count(self, text: str) -> None:
        self.turn_output += len(text.encode("utf-8"))
        if self.turn_output > OUTPUT_BYTES and not self.output_exceeded:
            self.output_exceeded = True
            self.token.cancel("output_limit")

    def _send(self, tools: bool) -> Any:
        self._gate_send()
        budget = self.state.budget
        if not budget.try_acquire():
            raise _End("busy")
        self.turn_output = 0
        self.partial = ""
        snap, arguments = self.capture.snapshot, self.capture.arguments
        completing = False
        try:
            if self.resolved_arguments is None:
                self.resolved_arguments, self.routing_selection = model_call.resolve_request(
                    snap, arguments, tools=self.capture.definitions if tools else None,
                    cancel=self.handle, timeout=max(0.001, min(30.0, self.deadline - time.monotonic())))
                if self.routing_selection and snap.routing_mode == "automatic":
                    # ModelSpell's routing probes have no public token or cost results.
                    self.note_unreported_usage()
            arguments = self.resolved_arguments
            self._gate_send()
            self.turns += 1
            completing = True
            if tools:
                return modelspell.complete_with_tools(
                    snap.endpoint, snap.model, self.transcript, tools=self.capture.definitions,
                    on_text=self._on_text, on_reasoning=self._on_reasoning, cancel=self.handle,
                    rate_limit_retries=0, **arguments)
            return modelspell.complete(snap.endpoint, snap.model, self.transcript, on_text=self._on_text,
                                       on_reasoning=self._on_reasoning, cancel=self.handle, rate_limit_retries=0,
                                       **arguments)
        except Cancelled as exc:
            self.sent_flags.append(exc.sent)
            if completing and exc.sent is not False:
                if exc.partial is not None:
                    self._record_chat_model(exc.partial)
                else:
                    self.note_unreported_usage()
            elif not completing and exc.sent is not False:
                self.note_unreported_usage()
            raise _End(self._stop_code()) from None
        except ModelSpellError as exc:
            if ((completing and getattr(exc, "sent", None) is not False)
                    or isinstance(exc, modelspell.ProviderSelectionError) and exc.evidence.get("attempts")):
                self.note_unreported_usage()
            raise _End("model_error", model_call.describe_error(exc, snap.endpoint.base_url)) from None
        finally:
            budget.release()

    # --- the loop (4.4) -----------------------------------------------------------------------------------------
    def _initial(self) -> None:
        name, instruction = chat_tasks.TASKS[self.capture.chat_task]
        system = _system_text()
        if instruction:
            system += f"\n\nChat task — {name}\n{instruction} Follow the existing privacy and tool rules."
        self._append({"role": "system", "content": system}, 0)
        self.transcript_bytes = len(self.transcript[0]["content"].encode("utf-8"))
        for entry in self.capture.memory:
            self._append({"role": "user", "content": entry.question}, len(entry.question.encode("utf-8")))
            answer = entry.answer
            if entry.drafts:
                answer += "\nDrafts created: " + ", ".join(f"{i} ({n})" for i, n in entry.drafts)
            self._append({"role": "assistant", "content": answer}, len(answer.encode("utf-8")))
        self._append({"role": "user", "content": self.message}, len(self.message.encode("utf-8")))
        # The initial label covers everything the first request holds (8.3).
        if any(c.restricted_definition for c in self.capture.capabilities) or \
                any(e.restricted for e in self.capture.memory):
            self._promote()

    def _text_branch(self) -> None:
        result = self._send(tools=False)
        self._record_chat_model(result)
        outcome = model_call.classify_outcome(result)
        if outcome == "complete":
            self.answer = result.text
            raise _End("answered")
        if outcome in ("truncated", "truncated_no_answer"):
            self.partial = result.text
            raise _End("incomplete_turn")
        raise _End("empty_answer")

    def _tool_branch(self) -> None:
        while True:
            turn = self._send(tools=True)
            self._record_chat_model(turn.result)
            self._ceilings()
            if not turn.clean:
                self.partial = turn.result.text or self.partial
                raise _End("incomplete_turn")                     # F-19: no append, no dispatch
            calls = turn.tool_calls
            if not calls:
                if turn.result.text.strip() == "":
                    raise _End("empty_answer")
                self.answer = turn.result.text
                raise _End("answered")
            if len(calls) > 1:
                self._append_turn(turn)
                for call in calls:
                    self._feedback(call, "one_call_per_turn")
                self._correction()
                continue
            call = calls[0]
            checked = self._validate(call)
            if isinstance(checked, str):
                self._append_turn(turn)
                self._feedback(call, checked, getattr(self, "_locations", []))
                self._correction()
                continue
            capability, arguments = checked
            self._dispatch(turn, call, capability, arguments)

    def _correction(self) -> None:
        self.corrections += 1
        if self.corrections > CORRECTION_LIMIT:
            raise _End("correction_limit")

    def _feedback(self, call: Any, code: str, locations: list | None = None) -> None:
        error = {"code": code, "message": FEEDBACK.get(code, FEEDBACK["refused"])}
        if locations:
            error["locations"] = locations[:20]
        content = _envelope({"ok": False, "error": error})
        self._append_output(call.id, content)
        self._record_action(call, None, outcome="feedback", code=code, arguments_text=call.arguments_json,
                            result_text=content, restricted=False)

    # --- validation (4.5) ---------------------------------------------------------------------------------------
    def _validate(self, call: Any):
        self._locations = []
        capability = self.capture.by_name.get(call.name)
        if capability is None:
            return "unknown_capability"
        if len(call.arguments_json.encode("utf-8")) > ARGUMENT_BYTES:
            return "malformed_arguments"
        try:
            arguments = jsonio.decode_exact(call.arguments_json or "{}")
        except (jsonio.JSONSyntax, jsonio.ExactLimit, ValueError):
            return "malformed_arguments"
        if not isinstance(arguments, dict):
            return "malformed_arguments"
        check = checks.check_value(caps.schema_contract(capability.parameters), arguments)
        if check["overall"] != "pass":
            self._locations = [{"location": i["location"], "rule": i["message"].split(".", 1)[-1]}
                               for i in check.get("issues", [])]
            return "schema_mismatch"
        if capability.kind == "block":
            texts = self._input_texts(capability, arguments)
            inputs = runner.check_inputs(capability.loaded.block, texts)
            if not inputs.passed:
                self._locations = [{"location": i.get("location", "")} for i in inputs.issues]
                return "input_check_failed"
        if self.token.cancelled:
            raise _End(self._stop_code())
        if time.monotonic() >= self.deadline:
            raise _End("time_limit")
        if self.actions >= self.capture.limits["actions"]:
            raise _End("action_limit")
        if not self._config_unchanged():
            raise _End("configuration_changed")
        return capability, arguments

    @staticmethod
    def _input_texts(capability: caps.Capability, arguments: dict) -> dict:
        block = capability.loaded.block
        texts = {}
        for declared in block.inputs:
            value = arguments[declared.name]
            fmt = block.contracts["inputs"][declared.name].parsed.format
            texts[declared.name] = value if fmt == "text" else jsonio.encode_exact(value)
        return texts

    # --- dispatch (4.4 steps 6-7) --------------------------------------------------------------------------------
    def _dispatch(self, turn: Any, call: Any, capability: caps.Capability, arguments: dict) -> None:
        action = {"call_id": call.id, "capability": capability.name, "kind": capability.kind,
                  "arguments_text": jsonio.encode_exact(arguments), "state": "proposed"}
        self._emit({"type": "action", "state": "proposed", "call_id": call.id, "capability": capability.name,
                    "arguments_text": action["arguments_text"]})
        started = time.monotonic()
        try:
            if capability.kind == "builtin":
                outcome = self._run_builtin(capability, arguments, call)
            elif capability.kind == "mcp":
                outcome = self._run_mcp(capability, arguments, call)
            else:
                outcome = self._run_block(capability, arguments, call)
        except _End:
            raise
        envelope, restricted, status = outcome["envelope"], outcome["restricted"], outcome["status"]
        if status in ("dispatched", "denied"):
            self.actions += 1
        content = _envelope(envelope)
        if status == "dispatched" and envelope.get("ok") is True and len(content.encode("utf-8")) > RESULT_BYTES:
            size = len(content.encode("utf-8"))
            content = _envelope({"ok": True, "result_omitted": True, "reason": "result_too_large", "bytes": size})
        effective = restricted or self.restricted
        self._record_action(call, capability, outcome=outcome.get("outcome", status), code=outcome.get("code"),
                            arguments_text=action["arguments_text"], result_text=content, restricted=effective,
                            approval=outcome.get("approval"), elapsed=time.monotonic() - started)
        self._emit({"type": "action", "state": "finished", "call_id": call.id, "capability": capability.name,
                    "outcome": outcome.get("outcome", status), "restricted": effective, "result_text": content})
        if restricted and not self.restricted:
            if self.capture.mode is not None:
                self._promote()
            if self.capture.trusted is None:
                raise _End(outcome.get("end") or "restricted_result")
            self._promote()
        if outcome.get("end"):
            raise _End(outcome["end"])
        from . import webspell
        if capability.server == webspell.SERVER and envelope.get("ok"):
            result = envelope.get("result")
            if isinstance(result, dict):
                urls = webspell.result_urls(result.get("structured"))
                self.web_urls.update(urls)
                with self.lock:
                    self.web_sources.update(list(sorted(urls))[:max(0, 64 - len(self.web_sources))])
            if len(self.web_urls) > 256:
                self.web_urls.clear()
        self.web_mixed = True
        self._append_turn(turn)
        self._append_output(call.id, content)
        if self.nested_exceeded:
            raise _End("nested_request_limit")

    def _record_action(self, call: Any, capability: caps.Capability | None, *, outcome: str, code: str | None,
                       arguments_text: str, result_text: str, restricted: bool, approval: dict | None = None,
                       elapsed: float = 0.0) -> None:
        saved = {"call_id": call.id, "capability": call.name if capability is None else capability.name,
                 "identity": capability.identity if capability else None,
                 "effects": capability.effects if capability else None,
                 "approval": approval, "outcome": outcome, "code": code,
                 "arguments": {"bytes": len(arguments_text.encode("utf-8")), "sha256": caps.sha256(arguments_text)},
                 "result": {"bytes": len(result_text.encode("utf-8")), "sha256": caps.sha256(result_text)},
                 "label": "restricted" if restricted else "unrestricted", "elapsed_s": round(elapsed, 3)}
        live = {**saved, "arguments_text": arguments_text, "result_text": result_text}
        with self.lock:
            self.saved_trace.append(saved)
            self.trace.append(live)

    @staticmethod
    def _outcome(envelope: dict, *, restricted: bool = False, status: str = "dispatched", **extra: Any) -> dict:
        return {"envelope": envelope, "restricted": restricted, "status": status, **extra}

    def _refusal(self, code: str) -> dict:
        return self._outcome({"ok": False, "error": {"code": code, "message": FEEDBACK.get(code, FEEDBACK["refused"])}},
                             status="refused", outcome="refused", code=code)

    # --- built-in tools ---------------------------------------------------------------------------------------
    def _run_builtin(self, capability: caps.Capability, arguments: dict, call: Any) -> dict:
        self._emit({"type": "action", "state": "started", "call_id": call.id, "capability": capability.name})
        tool = capability.builtin
        budget = RESULT_BYTES - 64
        inspected: list = []
        try:
            if tool == "list_documents":
                result, inspected = docs.list_documents(self.scope)
            elif tool == "search_documents":
                result, inspected = docs.search_documents(self.scope, arguments, budget, self._check_scan)
            elif tool == "read_document":
                result, inspected = docs.read_document(self.scope, arguments, budget)
            elif tool == "create_draft":
                item = self.conversation.create_draft(self.run_id, arguments["name"], arguments["format"],
                                                      arguments["content"], self.restricted, self._still_running)
                self.scope.add(item.id)
                with self.lock:
                    self.drafts.append((item.id, item.name))
                result = {"draft_id": item.id, "lines": item.lines, "characters": item.characters}
            elif tool in ("copy_to_draft", "revise_draft"):
                # Resolution and labels (addendum 3.1): resolve every input, then compute and apply the promotion
                # before anything is read, so a restricted source never reaches an untrusted orchestrator.
                inspected = docs.draft_inputs(self.scope, tool, arguments)
                label = self.restricted or any(i.local_only for i in inspected)
                if label and not self.restricted:
                    if self.capture.trusted is None:
                        return self._outcome({"ok": False, "error": {"code": "restricted_result",
                                                                     "message": FEEDBACK.get("refused")}},
                                             outcome="refused", code="restricted_result", restricted=True,
                                             end="restricted_result")
                    self._promote()
                source = inspected[0]
                if tool == "copy_to_draft":
                    content, copied = docs.copy_content(self.scope, arguments, inspected)
                    extra = {"lines_copied": copied}
                    name, fmt = arguments["name"], arguments["format"]
                else:
                    content, counts = docs.revise_content(self.scope, arguments, source)
                    extra = {"counts": counts}
                    fmt = arguments.get("format") or (source.format if source.format in ("md", "txt") else "txt")
                    name = arguments.get("name") or source.name
                item = self.conversation.create_draft(self.run_id, name, fmt, content, label, self._still_running)
                self.scope.add(item.id)
                with self.lock:
                    self.drafts.append((item.id, item.name))
                result = {"draft_id": item.id, "lines": item.lines, "characters": item.characters, **extra}
            elif tool == "compare_documents":
                inspected = [self.scope.resolve(arguments["before_id"]), self.scope.resolve(arguments["after_id"])]
                result = docs.compare_documents(self.scope, arguments, budget, self._check_scan, inspected)
            elif tool == "read_table":
                inspected = [self.scope.resolve(arguments["document_id"])]
                result = docs.read_table(self.scope, arguments, budget, self._check_scan, inspected[0])
            else:
                # The Chat result limit is passed in, so an output that would not fit is refused up front with
                # its remedy; the omission envelope stays only as a backstop (addendum 2.1).
                result = agent_tools.run_utility(tool, arguments, result_limit=budget, check=self._check_scan)
        except ToolError as exc:
            if exc.code == "stopped":
                raise _End(self._stop_code() if self.token.cancelled else "time_limit") from None
            return self._outcome({"ok": False, "error": exc.view()}, outcome="error", code=exc.code,
                                 restricted=any(i.local_only for i in inspected))
        except docs.StagingError as exc:
            return self._outcome({"ok": False, "error": {"code": exc.code, "message": "staging is unavailable"}},
                                 outcome="error", code=exc.code, restricted=any(i.local_only for i in inspected))
        return self._outcome({"ok": True, "result": result}, outcome="completed",
                             restricted=any(i.local_only for i in inspected))

    # --- MCP, direct and wrapped (5.5) -------------------------------------------------------------------------
    def _approval(self, call: Any, capability: caps.Capability, destination: dict, arguments_text: str,
                  digest: str) -> dict:
        """Wait for the user's decision outside every lock (5.4). Returns the approval view for the trace, or
        raises outbound_policy.Refused('denied' | 'approval_expired' | <stop>)."""
        expires = min(time.monotonic() + caps.APPROVAL_WAIT_S, self.deadline)
        approval = caps.Approval(self.run_id, call.id, capability.name, destination, digest,
                                 self.capture.classification_revision, caps.sha256(arguments_text), arguments_text,
                                 expires)
        with self.lock:
            self.pending = approval
        self._emit({"type": "action", "state": "awaiting_approval", "call_id": call.id,
                    "capability": capability.name, "approval": approval.view()})
        with approval.condition:
            while approval.decision is None and not self.token.cancelled:
                remaining = approval.expires - time.monotonic()
                if remaining <= 0:
                    approval.decision = "expired"
                    break
                approval.condition.wait(remaining)
            if approval.decision is None:
                approval.decision = "void"
        with self.lock:
            self.pending = None
        self.current_approval = approval
        view = {"decision": approval.decision, "decided_at": approval.decided_at}
        if approval.decision == "deny":
            raise outbound_policy.Refused("denied")
        if approval.decision == "expired":
            raise outbound_policy.Refused("time_limit" if time.monotonic() >= self.deadline else "approval_expired")
        if approval.decision == "void":
            raise outbound_policy.Refused(self._stop_code())
        return view

    def _mcp_guard(self, call: Any, capability: caps.Capability, snapshot: Any, arguments: Any,
                   restricted: bool) -> tuple[Any, dict | None]:
        """Steps 2-4 of 5.5 for the materialised arguments. Returns (the send-gate check, approval view)."""
        from . import webspell
        web = capability.server == webspell.SERVER
        require_approval = capability.approval
        if web:
            if restricted or self.capture.mode != "cloud" or not self.state.chat_preferences()["webspell"]:
                raise outbound_policy.Refused("destination_refused")
            proposed = arguments.get("urls") or []
            if "url" in arguments:
                proposed = [arguments["url"]]
            if any(not webspell.public_url(url) or url not in self.web_urls for url in proposed):
                raise outbound_policy.Refused("destination_refused")
            require_approval = ("query" in arguments and self.web_mixed
                                or bool(arguments.get("domains")))
        found = self.state.mcp_client.observed_schema(snapshot.identity, capability.tool)
        if found is None or found[0] != capability.schema_digest:
            raise outbound_policy.Refused("configuration_changed")
        check = checks.check_value(caps.schema_contract(found[1]), arguments)
        if check["overall"] != "pass":
            raise outbound_policy.Refused("schema_mismatch")
        arguments_text = jsonio.encode_exact(arguments)
        destination = {"kind": "mcp", "url": snapshot.url, "server": capability.server, "tool": capability.tool}
        grant = None
        if restricted:
            # An MCP send with restricted arguments needs that tool's own exact grant (8.3).
            def unchanged() -> bool:
                try:
                    return self.state.mcp_snapshot_for_block(capability.server) == snapshot
                except Exception:  # noqa: BLE001
                    return False
            grant = outbound_policy.Dispatch.prepare(self.state, caps.restricted_arguments_destination(
                snapshot, capability.tool), unchanged)
        approval_view = None
        approval = None
        if require_approval:
            approval_view = self._approval(call, capability, destination, arguments_text, capability.schema_digest)
            approval = self.current_approval

        def authorize() -> None:
            # Re-verified at the MCP send gate (5.5 step 5).
            self.parent_guard()
            revision, rows = self.state.agent.store.read()
            if revision != self.capture.classification_revision:
                raise outbound_policy.Refused("configuration_changed")
            row = self.state.agent.store.row(rows, snapshot.url, capability.server, capability.tool,
                                             capability.schema_digest)
            if web:
                if (self.restricted or not self.state.chat_preferences()["webspell"]
                        or not webspell.compatible(self.state.mcp_client, snapshot)):
                    raise outbound_policy.Refused("configuration_changed")
            elif not row["enabled"] or not row["no_host_files"]:
                raise outbound_policy.Refused("configuration_changed")
            current = self.state.mcp_client.observed_schema(snapshot.identity, capability.tool)
            if current is None or current[0] != capability.schema_digest:
                raise outbound_policy.Refused("configuration_changed")
            if approval is not None:
                if not approval.valid_for(arguments_text, capability.schema_digest,
                                          self.capture.classification_revision):
                    raise outbound_policy.Refused("approval_invalid")
                approval.used = True
            if grant is not None:
                grant.check()
        return authorize, approval_view

    def _run_mcp(self, capability: caps.Capability, arguments: dict, call: Any) -> dict:
        from . import mcp_ops
        snapshot = self.capture.mcp_snapshots.get(capability.server)
        try:
            live = self.state.mcp_snapshot_for_block(capability.server)
        except Exception:  # noqa: BLE001
            live = None
        if snapshot is None or live != snapshot:
            return self._refusal("configuration_changed")
        try:
            authorize, approval = self._mcp_guard(call, capability, snapshot, arguments, self.restricted)
        except outbound_policy.Refused as exc:
            code = str(exc)
            if code == "denied":
                view = {"decision": "deny", "decided_at": self.current_approval.decided_at
                        if self.current_approval is not None else None}
                return self._outcome({"ok": False, "error": {"code": "denied", "message": FEEDBACK["denied"]}},
                                     status="denied", outcome="denied", code="denied", approval=view)
            if code in ("approval_expired", "time_limit", "stopped", "interrupted"):
                raise _End(code) from None
            return self._refusal(code)
        reservation = self.state.mcp_ops.reserve()
        if reservation is None:
            return self._refusal("busy")
        self._emit({"type": "action", "state": "started", "call_id": call.id, "capability": capability.name})
        client, tool = self.state.mcp_client, capability.tool

        def work(operation):
            operation.authorize = authorize
            operation.allow_call_retry = False                  # never a resend after a stale-session reply
            return client.call(operation, snapshot, tool, arguments)

        try:
            op = self.state.mcp_ops.start("call", snapshot, work, reservation=reservation)
        except mcp_ops.Closed:
            reservation.release()
            raise _End("interrupted") from None
        self.token.on_cancel(lambda: op.abandon("stopped"))
        remaining = max(0.0, self.deadline - time.monotonic())
        deadline_timer = runner.make_timer(remaining, lambda: op.abandon("deadline"))
        try:
            committed = _wait_committed(op)
        finally:
            deadline_timer.cancel()
        result_restricted = capability.results_local_only or self.restricted
        outcome = committed["outcome"]
        if outcome == "ok":
            value = committed["value"].result
            content = value.get("content") if isinstance(value.get("content"), list) else []
            result = {"text": "\n\n".join(i["text"] for i in content if i.get("type") == "text")}
            if "structuredContent" in value:
                result["structured"] = value["structuredContent"]
            return self._outcome({"ok": True, "result": result}, restricted=result_restricted, outcome="completed",
                                 approval=approval)
        if outcome == "tool_error":
            return self._outcome({"ok": False, "dispatched": True,
                                  "error": {"code": "tool_failed", "message": FEEDBACK["tool_failed"]}},
                                 restricted=result_restricted, outcome="tool_failed", approval=approval)
        if outcome == "rejected" and not op.ctx.call_gate_passed:
            code = committed.get("reason") or "refused"
            return self._refusal(code if code.startswith(("destination_", "configuration", "approval")) else "refused")
        if outcome == "rejected":
            return self._outcome({"ok": False, "dispatched": True,
                                  "error": {"code": "tool_failed", "message": FEEDBACK["tool_failed"]}},
                                 restricted=result_restricted, outcome="tool_failed", approval=approval)
        return self._uncertain(capability, approval)

    def _uncertain(self, capability: caps.Capability, approval: dict | None) -> dict:
        """The request may have been received: never resent (4.7, F-17)."""
        if self.token.cancelled:
            raise _End(self._stop_code())
        if capability.effects == "read-only":
            return self._outcome({"ok": False, "error": {"code": "no_result", "message": FEEDBACK["no_result"]}},
                                 outcome="unknown", code="no_result", approval=approval)
        return self._outcome({}, outcome="unknown", code="unknown_effect", end="unknown_effect", approval=approval)

    # --- child blocks (5.6) ----------------------------------------------------------------------------------------
    def _run_block(self, capability: caps.Capability, arguments: dict, call: Any) -> dict:
        loaded = capability.loaded
        block = loaded.block
        live = self.state.block_loaded(capability.key)
        if live is None or live.revision != capability.revision:
            return self._refusal("configuration_changed")
        step = block.step
        if step.kind == 'transform':
            from .private_transform import OPERATIONS
            if step.raw['operation'] in OPERATIONS:
                return self._refusal('refused')
        snapshot, role = None, None
        if step.kind == "model":
            role = self.capture.role_captures.get(step.role)
            if role is None:
                return self._refusal("refused")
            snapshot = role.snapshot
        elif step.mcp:
            snapshot = self.capture.mcp_snapshots.get(step.server)
            if snapshot is None:
                return self._refusal("configuration_changed")
        decision: dict = {}
        before_tool = None
        if step.mcp and capability.wrapped is not None:
            wrapped = capability.wrapped

            def before_tool(prepared: Any):
                # Approval for the request the MCP server will actually receive: the filled template (B4).
                try:
                    authorize, view = self._mcp_guard(call, wrapped, prepared.snapshot, prepared.arguments,
                                                      self.restricted)
                except outbound_policy.Refused as exc:
                    decision["refused"] = str(exc)
                    raise
                decision["approval"] = view
                return authorize
        observer = _ChildObserver(self, {})
        dispatch = None
        if role is not None and self.capture.mode is not None:
            try:
                dispatch = outbound_policy.block_dispatch(
                    self.state, block, snapshot, protected=self.restricted or block.context_local_only
                    or bool(loaded.workshop.get("protection_clear_pending"))
                    or any(port.local_only for port in block.inputs),
                    defaults=role.defaults, mode=self.capture.mode)
            except outbound_policy.Refused:
                return self._refusal("destination_refused")
        frozen = runner.FrozenSource(snapshot, False, {"agent_run": self.run_id, "call_id": call.id},
                                     capture=role, protected=self.restricted, dispatch=dispatch,
                                     attempt_gate=self.child_gate, parent_guard=self.parent_guard,
                                     before_tool=before_tool)

        def stop_child() -> None:
            with self.lock:
                child = self.child_run_id
            if child:
                try:
                    self.state.store.stop_run(child, runs.USER_STOP)
                except runs.NoSuchRunInMemory:
                    pass
        with self.lock:
            self.child_run_id = None
        self.token.on_cancel(stop_child)                     # registered before the child exists (4.7)
        self._emit({"type": "action", "state": "started", "call_id": call.id, "capability": capability.name})
        try:
            finished = runner.run_block(self.state, loaded, self._input_texts(capability, arguments), observer,
                                        frozen=frozen)
        except runner.Busy:
            return self._refusal("busy")
        except Exception as exc:  # noqa: BLE001 - ShuttingDown and other admission refusals
            if self.state.store.closed:
                raise _End("interrupted") from None
            report_internal("agent child block", exc)
            return self._refusal("refused")
        result = finished.result
        approval = decision.get("approval")
        refused = decision.get("refused")
        if refused == "denied":
            return self._outcome({"ok": False, "error": {"code": "denied", "message": FEEDBACK["denied"]}},
                                 status="denied", outcome="denied", code="denied",
                                 approval={"decision": "deny"})
        if refused in ("approval_expired", "time_limit", "stopped", "interrupted"):
            raise _End(refused)
        restricted = bool(result.get("protected") or self.restricted
                          or any(port.local_only for port in block.outputs))
        outcome = result["outcome"]
        if self.nested_exceeded and outcome != "complete":
            raise _End("nested_request_limit")
        if outcome == "complete":
            values = {name: (view["text"] if view["format"] == "text" else jsonio.decode_exact(view["text"]))
                      for name, view in result["outputs"].items()}
            return self._outcome({"ok": True, "result": values}, restricted=restricted, outcome="completed",
                                 approval=approval)
        if outcome in ("stopped", "budget_exceeded") and self.token.cancelled:
            raise _End(self._stop_code())
        if outcome == "rejected" or refused:
            return self._refusal(refused if refused and refused.startswith("destination_") else "refused")
        if outcome == "unknown":
            target = capability.wrapped or capability
            return self._uncertain(target, approval)
        steps = result.get("steps") or []
        sent = any(step_.get("attempts") or step_.get("operation") for step_ in steps)
        failure = steps[0].get("failure") if steps else None
        if not sent or (isinstance(failure, str) and failure.startswith("destination_")):
            return self._refusal(failure if isinstance(failure, str) and failure.startswith("destination_")
                                 else "refused")
        return self._outcome({"ok": False, "dispatched": True,
                              "error": {"code": "block_failed", "message": FEEDBACK["block_failed"]}},
                             restricted=restricted, outcome="block_failed", approval=approval)

    # --- running and finishing ---------------------------------------------------------------------------------
    def run(self) -> dict:
        state = self.state
        try:
            writer = state.register_writer("agent run")
        except BaseException:
            self.manager.abandon(self)
            raise
        try:
            self.store_run = state.store.new_agent_run(save_runs=state.save_runs_now(), token=self.token,
                                                       handle=self.handle)
            self.run_id = self.store_run.run_id
            try:
                self.manager.suspend_chat_save(self)
                self.manager.attach(self)
            except BaseException:
                # No `started` event and no model request; the store entry and the writer go with it.
                state.store.discard_agent_run(self.store_run)
                raise
            return self._attached()
        finally:
            writer.release()
            self.manager.abandon(self)

    def _attached(self) -> dict:
        state = self.state
        try:
            if self.store_run.save_runs:
                state.history.start_run(self.store_run)
            self.timer = runner.make_timer(max(0.0, self.deadline - time.monotonic()),
                                           lambda: self.token.cancel("time_limit"))
            self._emit({"type": "started", "branch": self.capture.branch,
                        "offered": [c.name for c in self.capture.capabilities],
                        "destination": {"kind": self.capture.snapshot.kind,
                                        "url": self.capture.snapshot.endpoint.base_url,
                                        "model": self.capture.snapshot.model}})
            try:
                self._initial()
                if self.capture.branch == "text":
                    self._text_branch()
                else:
                    self._tool_branch()
            except _End as end:
                self.stop_reason, self.stop_detail = end.reason, end.detail
            except BaseException as exc:  # noqa: BLE001 - committed as internal_error
                self.stop_reason = "internal_error"
                if isinstance(exc, Exception):
                    report_internal("agent run", exc)
                else:
                    raise
            return self._finalise()
        finally:
            if self.timer is not None:
                self.timer.cancel()
            self.manager.detach(self)

    def record(self) -> dict:
        snap = self.capture.snapshot
        label = "restricted" if self.restricted else "unrestricted"
        return {"record_version": RECORD_VERSION, "run_id": self.run_id, "source": "agent",
                "started_at": self.started_at, "ended_at": self.ended_at, "outcome": self.stop_reason,
                "stop_reason": self.stop_reason, "stop_detail": self.stop_detail,
                "destination": {"kind": snap.kind, "url": snap.endpoint.base_url, "model": snap.model,
                                "provider": snap.provider, "profile": snap.profile_name},
                "routing": model_call.routing_record(snap, self.resolved_arguments or self.capture.arguments, self.routing_selection),
                "served_provider": self.served_provider,
                "task": dict(self.capture.task.request_shape()), "limits": dict(self.capture.limits),
                "branch": self.capture.branch,
                "system_prompt": {"id": SYSTEM_PROMPT_ID, "version": SYSTEM_PROMPT_VERSION},
                "capabilities": [{"name": c.name, "identity": c.identity, "effects": c.effects}
                                 for c in self.capture.capabilities],
                "message": self.message, "answer": self.answer,
                "trace": list(self.saved_trace),
                "counters": {"model_turns": self.turns, "actions": self.actions, "corrections": self.corrections,
                             "child_sends": self.child_sends},
                "cancelled_sent": list(self.sent_flags), "usage": dict(self.usage),
                "cost": self.cost if self.cost_reported else None,
                "label": label, "local_only": self.restricted,
                "drafts": [{"id": i, "name": n} for i, n in self.drafts],
                "agentwiz_version": __version__, "modelspell_version": modelspell.__version__,
                "scriptspell_version": python_step.version()}

    def _finalise(self) -> dict:
        state = self.state
        self.ended_at = runs.utc_now()
        if self.stop_reason == "answered" and self.answer is not None:
            self.conversation.add_memory(docs.MemoryEntry(self.message, self.answer, list(self.drafts),
                                                          self.restricted))
        record = self.record()
        status = state.store.end_agent_run(self.store_run, bool(self.store_run.save_runs))
        with self.lock:
            self.state_name = "ended"
            self.save_status = status
        self.manager.finish_chat_turn(self)
        self._emit({"type": "final", "stop_reason": self.stop_reason, "answer": self.answer,
                    "partial": self.partial if self.answer is None else None, "restricted": self.restricted,
                    "save_status": status, "view": self.view()})
        if status == "pending":
            try:
                saved = state.history.write_result(self.store_run, record)
            except Exception as exc:  # noqa: BLE001 - settle exactly once
                report_internal("history", exc)
                saved = {"save_status": "failed", "reason": "the record could not be written (internal error)"}
            detail = {k: v for k, v in saved.items() if k != "save_status"}
            state.store.settle_result_save(self.store_run, saved["save_status"], **detail)
            with self.lock:
                self.save_status, self.save_detail = saved["save_status"], detail
            self._emit({"type": "save", "save_status": saved["save_status"], **detail})
        return record

    def view(self, *, chat=False) -> dict:
        with self.lock:
            pending = self.pending.view() if self.pending is not None else None
            out = {"run_id": self.run_id, "state": self.state_name, "stop_reason": self.stop_reason,
                    "stop_detail": self.stop_detail, "answer": self.answer,
                    "partial": self.partial if self.answer is None else None,
                    "restricted": self.restricted, "branch": self.capture.branch,
                    "pending_approval": pending, "trace": [dict(t) for t in self.trace],
                    "counters": {"model_turns": self.turns, "actions": self.actions,
                                 "corrections": self.corrections, "child_sends": self.child_sends},
                    "drafts": [{"id": i, "name": n} for i, n in self.drafts],
                    "routing": model_call.routing_record(self.capture.snapshot,
                        self.resolved_arguments or self.capture.arguments, self.routing_selection),
                    "served_provider": self.served_provider, "sources": sorted(self.web_sources),
                    "served_route": {"kind": self.capture.snapshot.kind,
                                     "model": self.served_model or self.capture.snapshot.model,
                                     "provider": self.served_provider},
                    "accounting": self.accounting,
                    "usage": dict(self.usage), "cost": self.cost if self.cost_reported else None,
                    "save_status": self.save_status, **self.save_detail}
            if chat:
                out["message"] = self.message
            return out

    def decide(self, call_id: str, decision: str) -> str:
        with self.lock:
            approval = self.pending
        if approval is None or approval.call_id != call_id:
            return "not_pending"
        with approval.condition:
            if approval.decision is not None or time.monotonic() >= approval.expires:
                return "not_pending"
            approval.decision = "approve" if decision == "approve" else "deny"
            approval.decided_at = runs.utc_now()
            approval.condition.notify_all()
        return "recorded"


def _wait_committed(op: Any) -> dict:
    while True:
        kind, value = op.wait(op.time_left())
        if kind == "final":
            return value
        if kind == "timeout":
            op.abandon("deadline")
            kind, value = op.wait(30.0)
            while kind == "progress":
                kind, value = op.wait(30.0)
            if kind == "final":
                return value
            raise RuntimeError("an MCP operation did not commit after its deadline")


def company_summary(entry):
    """A conversation-view copy of a company entry; brief and report bodies are fetched on demand."""
    entry = copy.deepcopy(entry)
    if entry["type"] == "handoff":
        entry["brief_size"] = len(entry.pop("brief").encode("utf-8"))
    if entry["type"] == "run" and entry.get("report"):
        report = entry["report"]
        entry["report"] = {key: report[key] for key in ("sha256", "size", "status")}
    return entry


# --- the manager ---------------------------------------------------------------------------------------------------------
@dataclasses.dataclass
class Reservation:
    """`prepare_run`'s hold on the manager: it names the run (once built) and its conversation."""
    conversation: Any
    run: Any = None


class Manager:
    """The process's one live conversation and its runs (F-20, F-27)."""

    def __init__(self, state: Any):
        self.state = state
        self.store = caps.Store(state.workspace.path)
        self.chat_history = chat_history.History(state.workspace.path)
        self.lock = threading.Lock()
        self.conversation: docs.Conversation | None = None
        self.active: AgentRun | None = None
        self.reserved: Reservation | None = None
        self.company_hold: tuple[str, str] | None = None     # (conversation id, company work ident)
        self.save_lock = threading.Lock()
        self.runs: "OrderedDict[str, AgentRun]" = OrderedDict()
        self.closed = False
        if not state.workspace.read_only:
            try:
                docs.sweep(state.workspace.path)
            except OSError:
                pass

    # --- conversation ----------------------------------------------------------------------------------------
    def _require(self, conversation_id: object) -> docs.Conversation:
        with self.lock:
            conversation = self.conversation
        if conversation is None or conversation.id != conversation_id:
            raise AgentError("no_such_conversation", 404)
        return conversation

    def _busy_locked(self) -> bool:
        return self.active is not None or self.reserved is not None or self.company_hold is not None

    # --- company (CEO mode) entries ----------------------------------------------------------------------------
    # Lock order: Company.lock may be held while taking Manager.lock; Manager never takes Company.lock.
    def _company_live_locked(self, conversation_id):
        if self.closed:
            raise AgentError("shutting_down", 503)
        conversation = self.conversation
        if conversation is None or conversation.id != conversation_id:
            raise AgentError("no_such_conversation", 404)
        if conversation.private:
            raise AgentError("private_conversation", 409, remedies=["new_conversation"])
        return conversation

    def company_conversation(self, conversation_id):
        with self.lock:
            return self._company_live_locked(conversation_id)

    def live_conversation_id(self):
        with self.lock:
            return self.conversation.id if self.conversation is not None else None

    def hold_company(self, conversation_id, ident):
        """Company work (a representative call or a CEO run) makes the manager busy, and needs it idle."""
        with self.lock:
            self._company_live_locked(conversation_id)
            if self._busy_locked():
                raise AgentError("run_active", 409)
            self.company_hold = (conversation_id, ident)

    def release_company(self, ident):
        with self.lock:
            if self.company_hold is not None and self.company_hold[1] == ident:
                self.company_hold = None

    def company_entries(self, conversation_id):
        with self.lock:
            conversation = self.conversation
            if conversation is None or conversation.id != conversation_id:
                return OrderedDict()
            return copy.deepcopy(conversation.company_entries)

    def record_company(self, conversation_id, entry, *, save=True, usage=None):
        """Insert or replace one company entry in the live conversation, then save the chat when enabled."""
        with self.lock:
            conversation = self.conversation
            if conversation is None or conversation.id != conversation_id:
                return
            if entry["id"] not in conversation.company_entries:
                conversation.timeline.append(("company", entry["id"]))
            conversation.company_entries[entry["id"]] = copy.deepcopy(entry)
            if usage is not None:
                conversation.chat_totals = chat_history.merge_usage(conversation.chat_totals, usage)
            self._trim_company_locked(conversation)
        if save:
            self.save_company(conversation_id)

    def remove_company(self, conversation_id, entry_id):
        with self.lock:
            conversation = self.conversation
            if conversation is not None and conversation.id == conversation_id:
                conversation.company_entries.pop(entry_id, None)
                conversation.timeline = [item for item in conversation.timeline if item != ("company", entry_id)]

    def add_company_usage(self, conversation_id, usage):
        with self.lock:
            conversation = self.conversation
            if conversation is not None and conversation.id == conversation_id:
                conversation.chat_totals = chat_history.merge_usage(conversation.chat_totals, usage)

    @staticmethod
    def _trim_company_locked(conversation):
        entries = conversation.company_entries
        while (len(entries) > chat_history.MAX_COMPANY
               or sum(row["type"] == "run" for row in entries.values()) > chat_history.MAX_COMPANY_RUNS):
            order = [ident for kind, ident in conversation.timeline if kind == "company" and ident in entries]
            segment = order[:1]
            for ident in order[1:]:
                if entries[ident]["type"] == "user":
                    break
                segment.append(ident)
            if not segment or any(entries[ident]["type"] == "run" and entries[ident]["state"] == "running"
                                  for ident in segment):
                return
            for ident in segment:
                del entries[ident]
            dropped = {("company", ident) for ident in segment}
            conversation.timeline = [item for item in conversation.timeline if item not in dropped]

    def save_company(self, conversation_id):
        with self.lock:
            conversation = self.conversation
            if conversation is None or conversation.id != conversation_id or not conversation.chat_save:
                return
        error = None
        try:
            self._save_chat(conversation)
        except chat_history.HistoryError as exc:
            error = str(exc)
        with self.lock:
            conversation.chat_save_error = error

    def _idle(self) -> None:
        """An early, cheap refusal: the decisive check is `while_idle`."""
        with self.lock:
            if self._busy_locked():
                raise AgentError("run_active", 409)

    def while_idle(self, conversation_id: object, fn):
        """Run `fn` (which must be short) inside one hold of the manager lock in which the manager is not shutting
        down, the named conversation is the live one, no run is active and nothing is reserved."""
        with self.lock:
            if self.closed:
                raise AgentError("shutting_down", 503)
            if self.conversation is None or self.conversation.id != conversation_id:
                raise AgentError("no_such_conversation", 404)
            if self._busy_locked():
                raise AgentError("run_active", 409)
            return fn()

    @contextmanager
    def idle_reservation(self, *, conversation_id=None, cloud_discovery=False, discovery=False):
        """Own the idle slot through discovery or a chat lifecycle change, without holding locks over I/O."""
        with self.lock:
            conversation = self.conversation
            if self.closed:
                raise AgentError("shutting_down", 503)
            if cloud_discovery and conversation and conversation.private:
                raise AgentError("destination_refused", 409)
            if self._busy_locked():
                raise AgentError("destination_refused" if discovery else "run_active", 409)
            if conversation_id is not None and (conversation is None or conversation.id != conversation_id):
                raise AgentError("no_such_conversation", 404)
            reservation = Reservation(conversation)
            self.reserved = reservation
        try:
            yield conversation
        finally:
            with self.lock:
                if self.reserved is reservation:
                    self.reserved = None

    def public_discovery(self):
        return self.idle_reservation(cloud_discovery=True, discovery=True)

    def start_conversation(self, *, require_confirmation=False, saved_id=None, close_private=None) -> dict:
        if self.state.workspace.read_only:
            raise AgentError("read_only", 409)
        with self.state.writing("start conversation"), self.idle_reservation() as previous:
            if (require_confirmation and previous is not None and previous.private
                    and close_private != previous.id):
                raise AgentError("private_close_confirmation", 409)
            try:
                saved = self.chat_history.reopen(saved_id) if saved_id is not None else None
            except chat_history.HistoryError as exc:
                raise AgentError(str(exc), 409) from None
            if saved and previous is not None and saved_id == previous.id:
                return self.view()
            ident = saved_id
            if saved is None:
                while True:
                    ident = "conv_" + secrets.token_hex(4)
                    if not self.chat_history.contains(ident) and (previous is None or ident != previous.id):
                        break
            try:
                conversation = docs.Conversation(self.state.workspace.path, ident)
            except (OSError, docs.StagingError):
                raise AgentError("staging_unavailable", 409) from None
            conversation.chat_save = require_confirmation or saved is not None
            if saved:
                conversation.chat_totals = (saved["totals"] if "totals" in saved else chat_history.usage_summary(None))
                conversation.chat_turns = saved["turns"]
                conversation.chat_model = saved["model"]
                conversation.company_entries = OrderedDict(
                    (entry["id"], chat_history.interrupt(entry)) for entry in saved.get("company", []))
                conversation.timeline = [tuple(item) for item in saved.get("timeline", [])]
            with self.lock:
                self.conversation = conversation
                self.runs.clear()
            if previous is not None:
                previous.close()
            return self.view()

    def _chat_views(self, conversation):
        with self.lock:
            live = [run for run in self.runs.values()
                    if run.conversation is conversation and run.capture.mode is not None]
            restored = list(conversation.chat_turns)
        return restored + [run.view(chat=True) for run in live]

    def chat_context(self, conversation):
        memory = []
        remaining = CHAT_CONTEXT_BYTES
        for turn in reversed(self._chat_views(conversation)):
            if turn["stop_reason"] != "answered" or not turn["answer"]:
                continue
            if turn.get("state", "ended") != "ended":
                continue
            question, answer = turn["message"], turn["answer"]
            remaining -= len(question.encode("utf-8")) + len(answer.encode("utf-8"))
            if remaining < 0:
                break
            memory.append(docs.MemoryEntry(question, answer, [], turn.get("restricted", False)))
            if len(memory) >= CHAT_CONTEXT_TURNS:
                break
        return list(reversed(memory))

    def _save_chat(self, conversation):
        with self.save_lock:
            turns = self._chat_views(conversation)
            if conversation.private or any(turn.get("restricted") for turn in turns):
                raise chat_history.HistoryError("private_conversation")
            with self.lock:
                company = copy.deepcopy(list(conversation.company_entries.values()))
                timeline = [list(item) for item in conversation.timeline]
                totals = conversation.chat_totals
            self.chat_history.save(conversation.id, [chat_history.turn_snapshot(turn) for turn in turns],
                                   conversation.chat_model, totals, self.state.credentials(),
                                   company=company, timeline=timeline)

    def change_chat_save(self, conversation_id, enabled):
        if not isinstance(conversation_id, str) or type(enabled) is not bool:
            raise AgentError("invalid", 400)
        if self.state.workspace.read_only:
            raise AgentError("read_only", 409)
        with self.state.writing("chat history"), self.idle_reservation(conversation_id=conversation_id) as conversation:
            if conversation.private:
                raise AgentError("private_conversation", 409)
            try:
                if enabled:
                    self._save_chat(conversation)
                else:
                    self.chat_history.delete(conversation.id)
            except chat_history.HistoryError as exc:
                raise AgentError(str(exc), 409) from None
            with self.lock:
                conversation.chat_save = enabled
                conversation.chat_save_error = None
        return self.view()

    def delete_saved_chat(self, ident):
        if self.state.workspace.read_only:
            raise AgentError("read_only", 409)
        with self.state.writing("chat history"), self.idle_reservation() as conversation:
            current = conversation is not None and conversation.id == ident
            if current and conversation.private:
                raise AgentError("private_close_confirmation", 409)
            try:
                self.chat_history.delete(ident)
            except chat_history.HistoryError as exc:
                raise AgentError(str(exc), 409) from None
            if current:
                with self.lock:
                    self.conversation = None
                    self.runs.clear()
                conversation.close()

    def rename_saved_chat(self, ident, title):
        if self.state.workspace.read_only:
            raise AgentError("read_only", 409)
        with self.state.writing("rename chat"), self.idle_reservation():
            try:
                self.chat_history.rename(ident, title, self.state.credentials())
            except chat_history.HistoryError as exc:
                raise AgentError(str(exc), 409) from None

    def suspend_chat_save(self, run):
        conversation = run.conversation
        if run.capture.mode is not None and conversation.chat_save:
            try:
                if run.capture.mode == "local":
                    self.chat_history.delete(conversation.id)
                    with self.lock:
                        conversation.chat_save = False
                        conversation.chat_save_error = None
                else:
                    self.chat_history.suspend(conversation.id)
            except chat_history.HistoryError as exc:
                raise AgentError(str(exc), 409) from None

    def finish_chat_turn(self, run):
        if run.capture.mode is None:
            return
        conversation = run.conversation
        with self.lock:
            conversation.chat_totals = chat_history.merge_usage(conversation.chat_totals, run.accounting)
        if not conversation.chat_save:
            return
        error = None
        try:
            if conversation.private:
                self.chat_history.delete(conversation.id)
            else:
                self._save_chat(conversation)
        except chat_history.HistoryError as exc:
            error = str(exc)
        with self.lock:
            conversation.chat_save_error = error
            if conversation.private:
                conversation.chat_save = False

    def close_conversation(self, conversation_id: object, *, confirm=True) -> dict:
        conversation = self._require(conversation_id)
        with self.state.writing("close conversation"), self.idle_reservation(conversation_id=conversation.id):
            with self.lock:
                if conversation.private and not confirm:
                    raise AgentError("private_close_confirmation", 409)
                self.conversation = None
                self.runs.clear()
            left = conversation.close()
            return {"closed": conversation.id, "left": left}

    def view(self) -> dict:
        with self.lock:
            conversation, active = self.conversation, self.active
            recent = list(self.runs.values())
            chat = ({"saved": conversation.chat_save, "save_error": conversation.chat_save_error,
                     "restored_runs": [turn["run_id"] for turn in conversation.chat_turns],
                     "model_choice": conversation.chat_model, "totals": conversation.chat_totals}
                    if conversation is not None else {})
            if conversation is not None:
                chats = {turn["run_id"] for turn in conversation.chat_turns} | {
                    run.run_id for run in recent if run.conversation is conversation}
                chat["timeline"] = [list(item) for item in conversation.timeline
                                    if (item[0] == "chat" and item[1] in chats)
                                    or (item[0] == "company" and item[1] in conversation.company_entries)]
                chat["company"] = [company_summary(entry) for entry in conversation.company_entries.values()]
            hold = self.company_hold
            company_active = None
            if hold is not None and conversation is not None and hold[0] == conversation.id:
                entry = conversation.company_entries.get(hold[1])
                company_active = {"kind": "run" if entry is not None else "intake", "id": hold[1]}
        out: dict = {"format_version": 1, "conversation": None, "active_run": None, "pending_approval": None,
                     "company_active": company_active}
        if conversation is not None:
            out["conversation"] = {"id": conversation.id, "documents": conversation.items_view(),
                                   "memory": conversation.memory_view(), "private": conversation.private,
                                   "recent_runs": [run.run_id for run in recent if run.conversation is conversation], **chat}
        if active is not None:
            view = active.view()
            out["active_run"] = active.run_id
            out["pending_approval"] = view["pending_approval"]
        return out

    def memory(self, body: Mapping) -> dict:
        conversation = self._require(body.get("conversation"))
        if set(body) - {"conversation", "on", "clear"}:
            raise AgentError("invalid", 400)
        on, clear = body.get("on"), body.get("clear", False)
        if (on is not None and type(on) is not bool) or type(clear) is not bool:
            raise AgentError("invalid", 400)
        def change() -> None:
            with conversation.lock:
                if on is not None:
                    conversation.memory_on = on
                if clear:
                    conversation.memory = []
                    conversation.memory_cleared_reason = "cleared"
        self.while_idle(conversation.id, change)
        return self.view()

    def stage(self, conversation_id: object, filename: str, data: bytes, local_only: bool,
              extract: Any = None) -> dict:
        """The upload route's order (addendum 3.2): an early idle check, extraction, reserve and write, then the
        commit inside `while_idle`. A run that started, or a conversation that closed or was replaced, during
        extraction refuses the upload: its temporary file is removed and nothing is staged. `extract(data,
        filename)` returns (document, rendered) and may take the PDF deadline; it defaults to the basic reader."""
        from . import attachments
        conversation = self._require(conversation_id)
        self._idle()
        if extract is None:
            document = attachments.extract(data, filename=filename)
            attachments.check_document(document)
            rendered = attachments.render(document)
        else:
            document, rendered = extract(data, filename)
        attachments.require_text(document)
        coverage = attachments.coverage(document)
        try:
            item = conversation.prepare_stage(filename, document["format"], rendered["text"], local_only, coverage)
        except docs.StagingError as exc:
            raise AgentError(exc.code, exc.status, **({"limit": exc.limit} if exc.limit else {})) from None
        committed = False
        try:
            try:
                self.while_idle(conversation.id, lambda: conversation.commit(item))
                committed = True
            except docs.StagingError as exc:
                raise AgentError(exc.code, exc.status, **({"limit": exc.limit} if exc.limit else {})) from None
        finally:
            if not committed:
                conversation.discard(item.id)
            conversation.end_write()
        document_view = {"id": item.id, "name": item.name, "kind": item.kind, "format": item.format,
                         "lines": item.lines, "characters": item.characters, "local_only": item.local_only}
        if coverage is not None:
            document_view["coverage"] = coverage
        return {"document": document_view, **self.view()}

    def update_document(self, body: Mapping, *, chat=False) -> dict:
        conversation = self._require(body.get("conversation"))
        ident = body.get("id")
        if set(body) - {"conversation", "id", "local_only", "remove"} or not isinstance(ident, str):
            raise AgentError("invalid", 400)
        remove = body.get("remove") is True
        local_only = body.get("local_only")
        if not remove and type(local_only) is not bool:
            raise AgentError("invalid", 400)
        try:
            if remove:
                item = self.while_idle(conversation.id, lambda: conversation.unregister(ident))
                conversation.unlink(item)
                return self.view()
            if chat and local_only:
                with self.state.writing("tighten Chat document label"), self.idle_reservation(
                        conversation_id=conversation.id):
                    item = conversation.item(ident)
                    if item is not None and not item.local_only:
                        # Previously rendered answers can quote this item. Keep them private too.
                        if conversation.chat_save:
                            self.chat_history.delete(conversation.id)
                        with self.lock, conversation.lock:
                            conversation.private = True
                            conversation.chat_save = False
                            conversation.chat_save_error = None
                    cleared = conversation.set_label(ident, local_only)
            else:
                cleared = self.while_idle(conversation.id, lambda: conversation.set_label(ident, local_only))
        except chat_history.HistoryError as exc:
            raise AgentError(str(exc), 409) from None
        except docs.StagingError as exc:
            raise AgentError(exc.code, exc.status) from None
        return {**self.view(), "memory_cleared": cleared}

    def draft_bytes(self, draft_id: str, conversation_id: object) -> tuple[str, bytes]:
        """A draft of the live conversation; a named conversation must be the live one."""
        if conversation_id is None:
            with self.lock:
                conversation = self.conversation
            if conversation is None:
                raise AgentError("no_such_conversation", 404)
        else:
            conversation = self._require(conversation_id)
        item = conversation.item(draft_id)
        if item is None or item.kind != "draft":
            raise AgentError("unknown_document", 404)
        try:
            return item.name, conversation.read_text(item.id).encode("utf-8")
        except (OSError, docs.StagingError):
            raise AgentError("staging_unavailable", 409) from None

    # --- runs ----------------------------------------------------------------------------------------------------
    def inventory(self, body: Mapping) -> dict:
        with self.lock:
            conversation = self.conversation
        return preview(self.state, conversation, body)

    def prepare_run(self, body: Mapping, observer: Observer | None) -> AgentRun:
        if set(body) - {"conversation", "revision", "task", "message", "capabilities", "limits",
                        "role", "mode", "items", "follow_up", "builtins", "webspell",
                        "model_choice", "use_context", "chat_task"}:
            raise AgentError("invalid", 400)
        conversation = self._require(body.get("conversation"))
        message = body.get("message")
        if not isinstance(message, str) or not message.strip():
            raise AgentError("message_required", 400)
        reservation = Reservation(conversation)
        with self.lock:
            if self.closed:
                raise AgentError("shutting_down", 503)
            if self.conversation is not conversation:
                raise AgentError("no_such_conversation", 404)
            if self._busy_locked():
                raise AgentError("run_active", 409)
            self.reserved = reservation
        try:
            capture = plan(self.state, conversation, body, for_send=True)
            run = AgentRun(self, conversation, capture, message, observer)
        except BaseException:
            with self.lock:
                if self.reserved is reservation:
                    self.reserved = None
            raise
        run.reservation = reservation
        reservation.run = run
        return run

    def abandon(self, run: AgentRun) -> None:
        """Release `run`'s reservation if it still holds it. Idempotent."""
        reservation = getattr(run, "reservation", None)
        with self.lock:
            if reservation is not None and self.reserved is reservation:
                self.reserved = None

    def attach(self, run: AgentRun) -> None:
        """Verify, then commit, in one hold: the reservation is still this run's, its conversation is still the
        live one and shutdown has not started. Only then is the run active and the reservation cleared."""
        with self.lock:
            reservation = getattr(run, "reservation", None)
            if self.closed:
                raise AgentError("shutting_down", 503)
            if reservation is None or self.reserved is not reservation or reservation.run is not run:
                raise AgentError("run_active", 409)
            if self.conversation is not run.conversation:
                raise AgentError("no_such_conversation", 404)
            self.reserved = None
            self.active = run
            if run.capture.mode is not None:
                run.conversation.chat_model = run.capture.model_choice
                if run.capture.mode == "local":
                    run._promote()
            run.conversation.active_run = run
            self.runs[run.run_id] = run
            while len(self.runs) > RUNS_KEPT:
                self.runs.popitem(last=False)
            excess = len(run.conversation.chat_turns) + len(self.runs) - RUNS_KEPT
            if excess > 0:
                dropped = {("chat", turn["run_id"]) for turn in run.conversation.chat_turns[:excess]}
                del run.conversation.chat_turns[:excess]
                run.conversation.timeline = [item for item in run.conversation.timeline if item not in dropped]
            run.conversation.timeline.append(("chat", run.run_id))

    def detach(self, run: AgentRun) -> None:
        with self.lock:
            if self.active is run:
                self.active = None
            run.conversation.active_run = None

    def _run(self, run_id: object) -> AgentRun:
        if not runs.valid_run_id(run_id):
            raise AgentError("invalid", 400)
        with self.lock:
            run = self.runs.get(run_id)
            conversation = self.conversation
        if run is None or conversation is None or run.conversation is not conversation:
            raise AgentError("no_such_run", 404)
        return run

    def run_view(self, run_id: object, *, chat=False) -> dict:
        if chat:
            with self.lock:
                if self.conversation is not None:
                    for turn in self.conversation.chat_turns:
                        if turn["run_id"] == run_id:
                            return {**turn, "state": "ended"}
        return self._run(run_id).view(chat=chat)

    def stop(self, run_id: object) -> dict:
        run = self._run(run_id)
        with run.lock:
            ended = run.state_name != "running"
        if ended:
            return {"status": "already_finished"}
        run.cancel(runs.USER_STOP)
        return {"status": "stopping"}

    def approve(self, body: Mapping) -> dict:
        if set(body) != {"run_id", "call_id", "decision"} or body.get("decision") not in ("approve", "deny") \
                or not isinstance(body.get("call_id"), str):
            raise AgentError("invalid", 400)
        run = self._run(body["run_id"])
        status = run.decide(body["call_id"], body["decision"])
        if status != "recorded":
            raise AgentError("not_pending", 409)
        return {"status": status}

    # --- shutdown (7.5) ----------------------------------------------------------------------------------------
    def cancel_all(self) -> None:
        """Shutdown: refuse new work and cancel the active run and a reserved one (whose `attach` then refuses)."""
        with self.lock:
            self.closed = True
            active = self.active
            reserved = self.reserved.run if self.reserved is not None else None
        for run in (active, reserved):
            if run is not None:
                run.cancel("shutdown")

    def close_all(self) -> None:
        with self.lock:
            conversation, self.conversation = self.conversation, None
            self.runs.clear()
        if conversation is not None:
            conversation.close()
