"""Cloud company intake, pathway execution and report ownership."""
from __future__ import annotations

import dataclasses
import hashlib
from functools import lru_cache
import re
import secrets
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import modelspell

from . import (agent_capabilities, agent_tools, chat_history, checks, company_activity, company_catalogue, company_tools,
               history, jsonio, model_call, outbound_policy, roles, runner, runs, task_settings, workflow_definition,
               workspace)
from .workshop_service import load_capture
from .errors import report_internal

ASSETS = Path(__file__).with_name("company_assets")
CLASSES = ("Limited", "Standard", "Strong", "Unknown")
PATHWAYS = {"A": "solo", "B": "tools", "C": "workflow", "D": "specialists"}
MINIMUM = {"A": 1, "B": 2, "C": 4, "D": 4, "E": 2}
OUTCOMES = ("complete", "partial", "needs_clarification", "blocked", "failed", "stopped")
TEXT_LIMIT = 32 << 10
RETAIN = 8
PRIMARY = "chat"
REPORT_CONTEXT = 16 << 10
BLOCKED = {"pathway_unsupported", "admission_busy", "missing_credential", "binding_changed", "stale_revision",
           "attempts_exhausted", "credential_in_model_output", "deadline_exceeded", "budget_bound_unavailable",
           "tool_call_refused", "invalid_workflow_input", "tool_denied", "approval_expired", "approval_invalid",
           "destination_refused"}


class Refusal(Exception):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code = code
        self.status = status


def fields(value, required, optional=()):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise Refusal("invalid_fields", 400)
    return value


def text(value, limit=TEXT_LIMIT):
    try:
        if isinstance(value, str) and value.strip() and len(value.encode("utf-8")) <= limit:
            return value
    except UnicodeError:
        pass
    raise Refusal("invalid_text", 400)


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise Refusal("invalid_limit", 400)
    return value


def digest(value):
    return hashlib.sha256(value).hexdigest()


def decode(value):
    try:
        return jsonio.decode_exact(value)
    except (TypeError, ValueError):
        raise Refusal("invalid_model_output") from None


@dataclass
class Intake:
    ident: str
    revision: int
    binding: str
    policy: str
    max_attempts: int
    seconds: int
    caps: bool
    tools: tuple
    alternatives: dict
    external_tools: dict
    workflow_preset: str | None = None
    minimum: int = 0
    attempts: int = 0
    active_time: float = 0
    messages: list = field(default_factory=list)
    usage: dict | None = None
    routing: dict = field(default_factory=dict)
    consumed: bool = False
    stopped: bool = False
    conversation_id: str | None = None
    primary: dict | None = None

    @property
    def window(self):
        return min(45, max(15, (self.seconds + 2) // 3))


@dataclass
class Work:
    intake: Intake
    deadline: float
    ident: str = field(default_factory=lambda: secrets.token_hex(16))
    cancel: runs.CancelToken = field(default_factory=runs.CancelToken)
    handle: object = None
    child: object = None
    mcp_operation: object = None
    approval: agent_capabilities.Approval | None = None
    web_urls: set = field(default_factory=set)
    brief: str = ""
    binding: str | None = None
    phase: str = "starting"
    pathway: str | None = None
    presets: dict = field(default_factory=dict)
    assessment: dict | None = None
    decision: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    conditions: list = field(default_factory=list)
    trace: list = field(default_factory=list)
    usage: dict | None = None
    outcome: str | None = None
    reason: str | None = None
    report: bytes | None = None
    question: str | None = None
    save: bool = False
    persisted: bool = False
    save_error: str | None = None
    workflow: object = None
    started: float = field(default_factory=time.monotonic)
    record: company_activity.Recorder = None
    current_op: str | None = None
    tool_op: str | None = None
    handoff_id: str | None = None
    dirty: bool = False

    def __post_init__(self):
        if self.record is None:
            self.record = company_activity.Recorder(self.started)


class Company:
    def __init__(self, state):
        self.state = state
        self.lock = threading.RLock()
        self.active = None
        self.intakes = {}
        self.jobs = {}
        self.requests = {}
        self.catalogue = packaged_catalogue()
        self.assets = self.catalogue.files

    def bindings(self, revision):
        from .state import StaleRevision
        with self.state._lock:
            self.state._refresh_locked()
            if revision != self.state._revision:
                raise StaleRevision(self.state._revision)
            return self._bindings_locked()

    def _bindings_locked(self):
        from .state import RoleUnresolved
        result = {}
        for role in roles.CHAT_MODELS:
            try:
                snap = self.state._chat_role_locked(role, "cloud").snapshot
            except RoleUnresolved:
                continue
            if snap.kind == "openrouter":
                result[role] = snap
        present = {(snap.profile_name, snap.model) for snap in result.values()}
        for name, settings in self.state._profiles.items():
            if settings.kind != "openrouter" or not settings.model or (name, settings.model) in present:
                continue
            endpoint = self.state._resolved_endpoint(settings)
            snap = model_call.RequestSnapshot(endpoint, settings.model, settings.kind, settings.provider, name,
                                              **{key: getattr(settings, key) for key in model_call.ROUTING_FIELDS})
            result[f"profile-{digest(name.encode())[:16]}"] = snap
        return result

    def hints(self):
        raw, _ = workspace.read_workspace(self.state.workspace)
        hints = (raw or {}).get("company_model_classes", {})
        return hints if isinstance(hints, dict) else {}

    def settings(self):
        view = self.state.view()
        hints = self.hints()
        bindings = self.bindings(view["revision"])
        return {"revision": view["revision"], "key_source": view["openrouter_key_source"],
                "classes": CLASSES, "catalogue_revision": self.catalogue.revision,
                "workflow_presets": self.catalogue.views(), "example": self.assets["example.txt"].decode("utf-8"),
                "default": self.model_view(view["revision"], None, hints),
                "bindings": [{"id": key, "model": snap.model, "profile": snap.profile_name,
                              "class": hints.get(self.model_key(snap), "Unknown")}
                             for key, snap in bindings.items()]}

    def primary_snapshot(self, revision, choice):
        """The Chat model picker's Cloud choice (or the Chat default) as the company's primary binding."""
        from .state import RoleUnresolved, StaleRevision
        try:
            snap = self.state.chat_snapshot(revision, "fast", "cloud", choice)
        except StaleRevision:
            raise Refusal("stale_revision") from None
        except (RoleUnresolved, model_call.SettingsError):
            raise Refusal("cloud_model_required", 400) from None
        if snap.kind != "openrouter" or not snap.model.strip():
            raise Refusal("cloud_model_required", 400)
        return snap

    @staticmethod
    def parse_choice(value):
        try:
            choice = chat_history.model_choice(value)
        except ValueError:
            raise Refusal("invalid_model_choice", 400) from None
        if choice is not None and choice["kind"] != "openrouter":
            raise Refusal("cloud_model_required", 400)
        return choice

    def model_view(self, revision, choice, hints=None):
        hints = self.hints() if hints is None else hints
        try:
            snap = self.primary_snapshot(revision, choice)
        except Refusal as exc:
            return {"model": (choice or {}).get("model"), "profile": None, "class": "Unknown",
                    "eligible": False, "reason": exc.code, "route": None}
        reason = None if outbound_policy.credential(snap.endpoint) else "missing_credential"
        return {"model": snap.model, "profile": snap.profile_name, "class": hints.get(self.model_key(snap), "Unknown"),
                "eligible": reason is None, "reason": reason,
                "route": {"provider": "OpenRouter", "routing_mode": snap.routing_mode,
                          "data_collection": snap.routing_data_collection, "zdr": bool(snap.routing_zdr)}}

    def model(self, body):
        fields(body, ("revision", "model_choice"))
        revision = integer(body["revision"], 1, (1 << 31) - 1)
        choice = self.parse_choice(body["model_choice"])
        if revision != self.state.view()["revision"]:
            raise Refusal("stale_revision")
        return self.model_view(revision, choice)

    @staticmethod
    def model_key(snapshot):
        return digest(jsonio.encode({"kind": snapshot.kind, "url": snapshot.endpoint.base_url,
                                     "model": snapshot.model}))

    def set_class(self, body):
        fields(body, ("revision", "class"), ("binding", "model_choice"))
        if ("binding" in body) == ("model_choice" in body):
            raise Refusal("invalid_fields", 400)
        revision = integer(body["revision"], 1, (1 << 31) - 1)
        if body["class"] not in CLASSES:
            raise Refusal("invalid_class", 400)
        primary = None
        if "model_choice" in body:
            primary = self.primary_snapshot(revision, self.parse_choice(body["model_choice"]))
        with self.state.register_writer("company model class"), self.state._lock:
            self.state._refresh_locked()
            if revision != self.state._revision:
                raise Refusal("stale_revision")
            binding = primary or self._bindings_locked().get(text(body["binding"], 64))
            if binding is None:
                raise Refusal("binding_unavailable", 400)
            raw, problem = workspace.read_workspace(self.state.workspace)
            if problem or self.state.workspace.read_only or raw is None:
                raise Refusal("read_only")
            hints = raw.setdefault("company_model_classes", {})
            if not isinstance(hints, dict):
                raise Refusal("invalid_class_store")
            hints[self.model_key(binding)] = body["class"]
            workspace.write_atomic(self.state.workspace.path, "workspace.json", raw)
            self.state._revision += 1
        return self.settings()

    def create(self, body):
        fields(body, ("revision", "conversation", "model_choice", "policy"),
               ("attempts", "seconds", "token_cap", "cost_cap", "eligible_bindings", "workflow_preset"))
        choice = self.parse_choice(body["model_choice"])
        conversation = text(body["conversation"], 64)
        self.state.agent.company_conversation(conversation)
        policy = text(body["policy"], 1)
        if policy not in MINIMUM:
            raise Refusal("invalid_policy", 400)
        cap = False
        if body.get("token_cap") is not None:
            integer(body["token_cap"], 1, 10**10)
            cap = True
        if body.get("cost_cap") is not None:
            if type(body["cost_cap"]) is not int and type(body["cost_cap"]) is not float:
                raise Refusal("invalid_limit", 400)
            if not 0 < body["cost_cap"] <= 10**6:
                raise Refusal("invalid_limit", 400)
            cap = True
        revision = integer(body["revision"], 1, (1 << 31) - 1)
        binding = PRIMARY
        with self.lock:
            settings = self.settings()
            if revision != settings["revision"]:
                raise Refusal("stale_revision")
            bindings = self.bindings(revision)
            assert PRIMARY not in bindings
            primary = self.primary_snapshot(revision, choice)
            if not outbound_policy.credential(primary.endpoint):
                raise Refusal("missing_credential")
            requested = strings(body.get("eligible_bindings", []), 8, 64)
            if set(requested) - bindings.keys():
                raise Refusal("binding_unavailable", 400)
            classes = {row["id"]: row["class"] for row in settings["bindings"]}
            alternatives = {PRIMARY: (primary, self.hints().get(self.model_key(primary), "Unknown"))}
            alternatives.update({key: (bindings[key], classes[key]) for key in dict.fromkeys(requested)})
            tools = tuple(c for c in agent_capabilities.builtin_capabilities(False)
                          if c.builtin in agent_tools.UTILITY_TOOLS and c.effects == "read-only"
                          and not c.results_local_only and not c.restricted_definition)
            preferences = self.state.chat_preferences()
            if preferences["builtins"]:
                for key, entry in self.catalogue.manifest["capabilities"].items():
                    if policy not in entry["visibility_policies"]:
                        continue
                    capture = self.catalogue.reference(entry["block"], "block")
                    snapshot = capture["snapshot"]
                    ports = {name: decode(contract) for name, contract in snapshot["contracts"]["inputs"].items()}
                    properties = {name: config["schema"] if config["format"] == "json" else
                                  {"type": "string", "maxLength": config.get("max_length", TEXT_LIMIT)}
                                  for name, config in ports.items()}
                    tools += (agent_capabilities.Capability(key, "block", {"asset_digest": capture["revision"]},
                             entry["title"], {"type": "object", "properties": properties, "required": list(ports),
                                              "additionalProperties": False}, "read-only", False),)
            else:
                tools = ()
            external = company_tools.capture(self.state, preferences) if policy in ("B", "E") else {}
            tools += tuple(item.capability for item in external.values())
            if (agent_capabilities.collision(list(tools)) is not None
                    or agent_capabilities.definitions_size(list(tools)) > agent_capabilities.DEFINITION_BYTES):
                raise Refusal("capability_inventory_limit")
            intake = Intake(secrets.token_hex(16), revision, binding, policy,
                            integer(body.get("attempts", 12), 1, 20),
                            integer(body.get("seconds", 300), 1, 1800), cap, tools, alternatives, external,
                            conversation_id=conversation, primary=choice)
            selection = body.get("workflow_preset")
            if "workflow_preset" in body:
                if policy != "C":
                    raise Refusal("invalid_policy", 400)
                fields(selection, ("id", "revision"))
                selected = self.catalogue.presets.get(text(selection["id"], 128))
                if selected is None or selection["revision"] != selected["revision"]:
                    raise Refusal("stale_workflow_preset")
                intake.workflow_preset = selected["id"]
            intake.minimum = self.minimum(intake)
            self.intakes[intake.ident] = intake
            for ident, prior in list(self.intakes.items())[:-RETAIN]:
                if not self.active or self.active.intake is not prior:
                    del self.intakes[ident]
            return {"id": intake.ident}

    def get_intake(self, ident):
        intake = self.intakes.get(ident)
        if intake is None:
            raise Refusal("not_found", 404)
        return intake

    def current(self, intake, binding=None):
        from .state import StaleRevision
        binding = binding or intake.binding
        try:
            if binding == PRIMARY:
                try:
                    live = self.primary_snapshot(intake.revision, intake.primary)
                except Refusal as exc:
                    raise Refusal("stale_revision" if exc.code == "stale_revision" else "binding_changed") from None
            else:
                live = self.bindings(intake.revision).get(binding)
        except StaleRevision:
            raise Refusal("stale_revision") from None
        if live is None or live.kind != "openrouter" or binding not in intake.alternatives:
            raise Refusal("binding_changed")
        key = outbound_policy.credential(live.endpoint)
        if not key:
            raise Refusal("missing_credential")
        captured, _ = intake.alternatives[binding]
        if live != captured or key != captured.endpoint.api_key:
            raise Refusal("binding_changed")
        return live

    def public_text(self, value, limit=TEXT_LIMIT):
        value = text(value, limit)
        _, has_secret = history.redact(value, self.state.credentials())
        if has_secret:
            raise Refusal("credential_in_company_text", 400)
        return value

    def reserve(self, work, keep=0):
        with self.lock:
            if work.cancel.cancelled or self.state.store.closed:
                raise Refusal("stopped")
            if time.monotonic() >= work.deadline:
                raise Refusal("deadline_exceeded")
            if work.intake.caps:
                raise Refusal("budget_bound_unavailable")
            if work.intake.attempts + keep >= work.intake.max_attempts:
                raise Refusal("attempts_exhausted")
            return self.current(work.intake, work.binding)

    def account(self, work, result=None, *, op=None, **trace):
        with self.lock:
            reported = model_call.result_to_dict(result) if result is not None else None
            usage = chat_history.usage_summary(reported)
            work.usage = chat_history.merge_usage(work.usage, usage)
            served = None
            if result is not None:
                trace.update(served_provider=result.provider, served_model=result.model)
                served = {"model": result.model or None, "provider": result.provider or None,
                          "selected_provider": trace.get("selected_provider")}
            work.record.model_call(op or work.current_op, usage, served)
            work.trace.append(trace)

    def requested(self, work, binding=None):
        binding = binding or work.binding or work.intake.binding
        snapshot, _ = work.intake.alternatives[binding]
        return {"model": snapshot.model, "profile": snapshot.profile_name, "binding_id": binding}

    @contextmanager
    def operation(self, work, kind, path, activity, *, ident=None, parent=None, model=True):
        """Record one operation around a block of engine work; the outcome follows the block's exit."""
        with self.lock:
            requested = self.requested(work) if model else None
            if ident is None:
                ident = work.record.start_op(kind, path, activity, requested_model=requested,
                                             parent=parent if parent is not None else work.current_op)
            else:
                work.record.run_op(ident, activity, requested)
            previous, work.current_op = work.current_op, ident
        try:
            yield ident
        except Refusal as exc:
            with self.lock:
                state = ("cancelled" if exc.code == "stopped" or work.cancel.cancelled
                         else "blocked" if exc.code in BLOCKED else "failed")
                work.record.finish_op(ident, state, code=exc.code)
            raise
        except BaseException:
            with self.lock:
                work.record.finish_op(ident, "cancelled" if work.cancel.cancelled else "failed", code="internal_error")
            raise
        else:
            with self.lock:
                work.record.finish_op(ident, "complete")
        finally:
            with self.lock:
                work.current_op = previous

    def report_event(self, work, kind, **data):
        """Record an arrival at the CEO side of the engine and publish its progress update."""
        with self.lock:
            work.record.event(kind, op=work.current_op, reportable=True, **data)
            if work.record.publish_pending():
                work.dirty = True
        self.flush(work)

    def ceo_path(self):
        return [company_activity.agent_path(self.catalogue, "planner")]

    def call(self, work, system, data, *, tools=(), keep=0, repair=None, transcript=()):
        snap = self.reserve(work, keep)
        defs = [modelspell.ToolDefinition(tool.name, tool.description, tool.parameters) for tool in tools]
        settings = task_settings.TaskSettings(max_tokens=4096)
        arguments = model_call.task_arguments(settings, snap.kind, snap.provider)
        messages = model_call.build_messages(system, jsonio.encode_exact(data))
        messages.extend(transcript)
        if repair:
            messages.append({"role": "user", "content": "Correct the envelope. Return only a valid JSON object."})
        handle = modelspell.CancelHandle()
        with self.lock:
            self.reserve(work, keep)
            work.handle = handle
        output_size = 0

        def bound_output(chunk):
            nonlocal output_size
            output_size += len(chunk.encode("utf-8"))
            if output_size > 64 << 10:
                handle.cancel()

        timer = threading.Timer(max(0, work.deadline - time.monotonic()), handle.cancel)
        timer.daemon = True
        started = False
        acquired = False
        routing = (work.binding or work.intake.binding, bool(defs))
        try:
            if not self.state.budget.try_acquire():
                raise Refusal("admission_busy", 503)
            acquired = True
            timer.start()
            cached = work.intake.routing.get(routing)
            if cached is None:
                self.reserve(work, keep)
                selection_op = None
                if snap.routing_mode == "automatic":
                    with self.lock:
                        selection_op = work.record.start_op(
                            "provider_selection", work.record.path_of(work.current_op), "Selecting a provider",
                            parent=work.current_op, requested_model=self.requested(work))
                resolved_ok = False
                try:
                    cached = model_call.resolve_request(
                        snap, arguments, tools=defs or None, cancel=handle,
                        timeout=max(.001, min(30, work.deadline - time.monotonic())))
                    resolved_ok = True
                finally:
                    if selection_op is not None:
                        self.account(work, stage="provider_selection", op=selection_op)
                        with self.lock:
                            work.record.finish_op(selection_op, "complete" if resolved_ok else
                                                  "cancelled" if work.cancel.cancelled else "failed",
                                                  code=None if resolved_ok else "model_error")
                work.intake.routing[routing] = cached
            resolved, selection = cached
            with self.lock:
                self.reserve(work, keep)
                work.intake.attempts += 1
                started = True
            kwargs = {"cancel": handle, "on_text": bound_output, "on_reasoning": bound_output,
                      "rate_limit_retries": 0, **resolved}
            if defs:
                turn = modelspell.complete_with_tools(snap.endpoint, snap.model, messages, tools=defs, **kwargs)
                result, calls = turn.result, turn.tool_calls
                clean = turn.clean
            else:
                result, calls = modelspell.complete(snap.endpoint, snap.model, messages, **kwargs), []
                clean = model_call.classify_outcome(result) == "complete"
            self.account(work, result, stage=work.phase,
                         selected_provider=selection.slug if selection else snap.provider or None)
            if work.cancel.cancelled:
                raise Refusal("stopped")
            if time.monotonic() >= work.deadline:
                raise Refusal("deadline_exceeded")
            if not clean:
                raise Refusal("invalid_model_output")
            _, has_secret = history.redact(result.text or "", self.state.credentials())
            if has_secret:
                raise Refusal("credential_in_model_output")
            return result.text, calls, turn if defs and calls else None
        except modelspell.ModelSpellError as exc:
            sent = False if isinstance(exc, (modelspell.ConfigError, modelspell.UnsupportedSetting,
                                             modelspell.UnsupportedOperation)) else getattr(exc, "sent", None)
            if started:
                if sent is False:
                    with self.lock:
                        work.intake.attempts -= 1
                else:
                    self.account(work, getattr(exc, "partial", None), stage=work.phase, sent=sent)
            if work.cancel.cancelled:
                raise Refusal("stopped") from None
            if output_size > 64 << 10:
                raise Refusal("output_limit") from None
            if getattr(exc, "status", None) == 429:
                raise Refusal("provider_rate_limit") from None
            if time.monotonic() >= work.deadline:
                raise Refusal("deadline_exceeded") from None
            code = "model_error" if sent is False or not started else "external_outcome_uncertain"
            raise Refusal(code) from None
        finally:
            timer.cancel()
            if acquired:
                self.state.budget.release()
            with self.lock:
                work.handle = None

    def envelope(self, work, instruction, data, validator, *, keep=0):
        for correction in (False, True):
            raw, _, _ = self.call(work, instruction, data, keep=keep, repair=correction)
            try:
                return validator(decode(raw))
            except Refusal as exc:
                if correction or exc.code not in ("invalid_model_output", "invalid_fields", "invalid_text",
                                                  "invalid_quote", "invalid_report"):
                    raise
        raise AssertionError("unreachable")

    def message(self, ident, body):
        fields(body, ("text",))
        message = self.public_text(body["text"])
        manager = self.state.agent
        with self.lock:
            intake = self.get_intake(ident)
            if self.active or intake.consumed or intake.stopped:
                raise Refusal("company_busy" if self.active else "intake_unavailable")
            self.current(intake)
            spare_time = intake.seconds - intake.active_time - intake.window
            if intake.attempts + intake.minimum >= intake.max_attempts or spare_time <= 0 or intake.caps:
                raise Refusal("allowance_reserved")
            work = Work(intake, time.monotonic() + spare_time, phase="representative")
            manager.hold_company(intake.conversation_id, work.ident)
            try:
                writer = self.state.register_writer("company representative")
            except BaseException:
                manager.release_company(work.ident)
                raise
            self.active = work
        conversation = intake.conversation_id
        user = {"id": entry_id(), "type": "user", "at": runs.utc_now(), "text": message, "intake_id": intake.ident,
                "error": None}
        succeeded = False
        try:
            entries = manager.company_entries(conversation)
            history_rows, last_run = representative_context(entries)
            manager.record_company(conversation, user)
            data = {"prior_company_turns": history_rows, "message": message, "last_run": last_run}
            while len(jsonio.encode(data)) > TEXT_LIMIT and data["prior_company_turns"]:
                data["prior_company_turns"] = data["prior_company_turns"][1:]
            if len(jsonio.encode(data)) > TEXT_LIMIT and last_run and last_run["report_markdown"]:
                spare = max(0, TEXT_LIMIT - len(jsonio.encode({**data, "last_run": {**last_run, "report_markdown": ""}})) - 64)
                last_run.update(report_markdown=clip_bytes(last_run["report_markdown"], spare), report_truncated=True)
            handoff = {}

            def validate(value):
                handoff.clear()
                if isinstance(value, dict) and "handoff" in value:
                    handoff["value"] = value.pop("handoff")
                self.stage_value("representative", "output_contract", value)
                fields(value, ("kind", "reply"), ("brief",))
                if value["kind"] not in ("simple_answer", "clarification", "brief_proposal"):
                    raise Refusal("invalid_model_output")
                self.public_text(value["reply"], 4096)
                if value["kind"] == "brief_proposal":
                    self.public_text(value.get("brief"))
                elif "brief" in value:
                    raise Refusal("invalid_model_output")
                return value

            reply = self.envelope(work, self.catalogue.prompt("representative"), data, validate, keep=intake.minimum)
            with self.lock:
                if work.cancel.cancelled:
                    raise Refusal("stopped")
                intake.messages = (intake.messages + [{"message": message, **reply}])[-4:]
                served = next((row.get("served_model") for row in reversed(work.trace) if row.get("served_model")), None)
                model = served or self.requested(work)["model"]
            finished = [row for row in entries.values() if row["type"] == "run" and row["state"] != "running"]
            rows = [{"id": entry_id(), "type": "representative", "at": runs.utc_now(), "kind": reply["kind"],
                     "text": reply["reply"], "usage": work.usage, "model": model,
                     "commentary_on": finished[-1]["id"] if finished else None}]
            if reply["kind"] == "brief_proposal":
                for row in entries.values():
                    if row["type"] == "handoff" and row["state"] == "draft":
                        manager.record_company(conversation, {**row, "state": "superseded"}, save=False)
                rows.append(handoff_entry(reply["brief"], intake.ident, "representative",
                                          handoff_summary(self, handoff.get("value"))))
            for row in rows:
                manager.record_company(conversation, row, save=False,
                                       usage=work.usage if row["type"] == "representative" else None)
            succeeded = True
            return {**reply, "entries": [user["id"], *(row["id"] for row in rows)],
                    "usage": chat_history.merge_usage(intake.usage, work.usage)}
        except Refusal as exc:
            manager.record_company(conversation, {**user, "error": exc.code}, save=False)
            raise
        finally:
            with self.lock:
                intake.usage = chat_history.merge_usage(intake.usage, work.usage)
                intake.active_time += time.monotonic() - work.started
                self.active = None
            if not succeeded and work.usage:
                manager.add_company_usage(conversation, work.usage)
            manager.save_company(conversation)
            manager.release_company(work.ident)
            writer.release()

    def handoff(self, ident, body):
        """Create a handoff from text the user typed or edited. No model call; no summary is invented."""
        fields(body, ("brief",), ("supersedes",))
        brief = self.public_text(body["brief"])
        manager = self.state.agent
        with self.lock:
            intake = self.get_intake(ident)
            if self.active:
                raise Refusal("company_busy")
            if intake.consumed or intake.stopped:
                raise Refusal("intake_unavailable")
            manager.company_conversation(intake.conversation_id)
            entries = manager.company_entries(intake.conversation_id)
            origin = "direct"
            if "supersedes" in body:
                prior = entries.get(text(body["supersedes"], 64))
                if prior is None or prior["type"] != "handoff" or prior["state"] != "draft":
                    raise Refusal("handoff_unavailable")
                origin = "edited"
            for row in entries.values():
                if row["type"] == "handoff" and row["state"] == "draft":
                    manager.record_company(intake.conversation_id, {**row, "state": "superseded"}, save=False)
            entry = handoff_entry(brief, intake.ident, origin, None)
            manager.record_company(intake.conversation_id, entry, save=False)
        manager.save_company(intake.conversation_id)
        return {"entry": entry["id"]}

    def handoff_text(self, entry):
        manager = self.state.agent
        conversation = manager.live_conversation_id()
        row = manager.company_entries(conversation).get(entry) if conversation else None
        if row is None or row["type"] != "handoff":
            raise Refusal("not_found", 404)
        return {"brief": row["brief"], "brief_sha256": row["brief_sha256"]}

    def stop(self, ident, intake=False):
        with self.lock:
            target = self.get_intake(ident) if intake else self.get_job(ident)
            work = self.active if intake and self.active and self.active.intake is target and not target.consumed else target
            if isinstance(work, Work) and work.outcome is None:
                if intake:
                    target.stopped = True
                self.cancel(work)
            return {"stopping": isinstance(work, Work) and work.cancel.cancelled}

    @staticmethod
    def cancel(work):
        work.cancel.cancel()
        if work.handle is not None:
            work.handle.cancel()
        if work.child is not None:
            work.child.cancel()
        if work.mcp_operation is not None:
            work.mcp_operation.abandon("stopped")
        if work.approval is not None:
            with work.approval.condition:
                work.approval.condition.notify_all()

    def shutdown(self):
        with self.lock:
            if self.active is not None and self.active.outcome is None:
                self.cancel(self.active)

    def start(self, body):
        fields(body, ("intake_id", "handoff_id", "brief_sha256", "revision", "client_request_id"))
        ident = text(body["intake_id"], 64)
        request_id = text(body["client_request_id"], 128)
        handoff_id = text(body["handoff_id"], 64)
        brief_sha = text(body["brief_sha256"], 64)
        revision = integer(body["revision"], 1, (1 << 31) - 1)
        payload = digest(jsonio.encode({key: body[key] for key in sorted(body)}))
        manager = self.state.agent
        with self.lock:
            prior = self.requests.get((ident, request_id))
            if prior:
                if prior[0] != payload:
                    raise Refusal("changed_duplicate")
                return self.view(prior[1])
            intake = self.get_intake(ident)
            if self.active or intake.consumed or intake.stopped:
                raise Refusal("company_busy" if self.active else "intake_unavailable")
            if revision != intake.revision:
                raise Refusal("stale_revision")
            entries = manager.company_entries(intake.conversation_id)
            handoff = entries.get(handoff_id)
            drafts = [row for row in entries.values() if row["type"] == "handoff" and row["state"] != "superseded"]
            if handoff is None or handoff["type"] != "handoff" or handoff["state"] != "draft" or drafts[-1] is not handoff:
                raise Refusal("handoff_unavailable")
            if brief_sha != handoff["brief_sha256"]:
                raise Refusal("handoff_changed")
            brief = self.public_text(handoff["brief"])
            self.current(intake)
            work = Work(intake, time.monotonic() + intake.seconds - intake.active_time, brief=brief,
                        save=self.state.view()["history"]["save_runs"], handoff_id=handoff_id)
            manager.hold_company(intake.conversation_id, work.ident)
            writer = None
            try:
                writer = self.state.register_writer("company CEO run")
                if intake.policy == "C":
                    acquire_workflow(self.state, work)
                intake.consumed = True
                self.active = work
                self.jobs[work.ident] = work
                self.requests[(ident, request_id)] = payload, work
                manager.record_company(intake.conversation_id, {**handoff, "state": "sent", "run_id": work.ident},
                                       save=False)
                manager.record_company(intake.conversation_id, self.run_entry(work), save=False)
                thread = threading.Thread(target=self.execute, args=(work, writer), daemon=True)
                thread.start()
            except BaseException:
                release_workflow(self.state, work)
                self.active = None
                intake.consumed = False
                self.jobs.pop(work.ident, None)
                self.requests.pop((ident, request_id), None)
                manager.remove_company(intake.conversation_id, work.ident)
                manager.record_company(intake.conversation_id, handoff, save=False)
                manager.release_company(work.ident)
                if writer is not None:
                    writer.release()
                raise
            while len(self.jobs) > RETAIN:
                evicted = self.jobs.pop(next(iter(self.jobs)))
                self.requests = {key: row for key, row in self.requests.items() if row[1] is not evicted}
            view = self.view(work)
        manager.save_company(intake.conversation_id)
        return view

    def run_entry(self, work):
        """The conversation's durable copy of a run: state, updates, operations, usage and the exact report."""
        with self.lock:
            intake = work.intake
            snapshot, hint = intake.alternatives[work.binding or intake.binding]
            activity = work.record.view(finished=work.outcome is not None,
                                        attempts={"used": intake.attempts, "limit": intake.max_attempts})
            operations = [{key: value for key, value in op.items() if key != "detail"}
                          for op in activity["operations"]][-128:]
            report = None
            if work.report is not None:
                report = {"markdown": work.report.decode("utf-8"), "sha256": digest(work.report),
                          "size": len(work.report), "status": work.outcome}
            preset = next((row.get("preset_id") for row in reversed(work.decision) if row.get("preset_id")), None)
            return {"id": work.ident, "type": "run", "at": runs.utc_now(), "handoff_id": work.handoff_id,
                    "policy": intake.policy, "model": snapshot.model, "class": hint,
                    "state": "finished" if work.outcome else "running", "outcome": work.outcome,
                    "reason": work.reason, "pathway": work.pathway, "preset_id": preset,
                    "updates": activity["updates"], "operations": operations, "summary": activity["summary"],
                    "usage": work.usage, "open_conditions": list(work.conditions), "question": work.question,
                    "report": report}

    def flush(self, work, *, force=False, usage=None):
        if not (work.dirty or force) or work.intake.conversation_id is None:
            return
        work.dirty = False
        try:
            self.state.agent.record_company(work.intake.conversation_id, self.run_entry(work), usage=usage)
        except Exception as exc:  # noqa: BLE001 - a failed checkpoint never fails the run
            report_internal("company checkpoint", exc)

    def report_bytes(self, ident):
        with self.lock:
            work = self.jobs.get(ident)
            if work is not None and work.report is not None:
                return work.report
        manager = self.state.agent
        conversation = manager.live_conversation_id()
        entry = manager.company_entries(conversation).get(ident) if conversation else None
        if entry is None or entry["type"] != "run" or not entry.get("report"):
            raise Refusal("report_unavailable", 404)
        data = entry["report"]["markdown"].encode("utf-8")
        if digest(data) != entry["report"]["sha256"] or len(data) != entry["report"]["size"]:
            raise Refusal("report_integrity")
        return data

    def stored_view(self, ident):
        """A run that is no longer in memory, rendered from the conversation's durable entry."""
        manager = self.state.agent
        conversation = manager.live_conversation_id()
        entry = manager.company_entries(conversation).get(ident) if conversation else None
        if entry is None or entry["type"] != "run":
            raise Refusal("not_found", 404)
        report = entry.get("report")
        return {"id": ident, "live": False, "state": entry["state"], "phase": "finished", "policy": entry["policy"],
                "pathway": entry["pathway"], "outcome": entry["outcome"], "reason": entry["reason"],
                "report_available": bool(report), "report_digest": report["sha256"] if report else None,
                "report_size": report["size"] if report else None, "model": entry["model"], "class": entry["class"],
                "usage": entry["usage"], "open_conditions": entry["open_conditions"], "question": entry["question"],
                "pending_approval": None, "conversation": conversation, "handoff_id": entry["handoff_id"],
                "activity": {"operations": entry["operations"], "updates": entry["updates"],
                             "summary": entry["summary"]}}

    def get_job(self, ident):
        work = self.jobs.get(ident)
        if work is None:
            raise Refusal("not_found", 404)
        return work

    def view(self, work):
        with self.lock:
            intake = work.intake
            binding = work.binding or intake.binding
            snapshot, hint = intake.alternatives[binding]
            return {"id": work.ident, "phase": work.phase, "policy": intake.policy, "pathway": work.pathway,
                    "outcome": work.outcome, "reason": work.reason, "report_available": work.report is not None,
                    "report_digest": digest(work.report) if work.report is not None else None,
                    "report_size": len(work.report) if work.report is not None else None,
                    "persisted": work.persisted, "save_error": work.save_error,
                    "model": snapshot.model, "class": hint,
                    "attempts": intake.attempts, "remaining_attempts": max(0, intake.max_attempts - intake.attempts),
                    "remaining_seconds": max(0, round(work.deadline - time.monotonic(), 1)) if not work.outcome else 0,
                    "usage": chat_history.merge_usage(intake.usage, work.usage), "intake_usage": intake.usage,
                    "trace": list(work.trace), "decisions": list(work.decision),
                    "catalogue_revision": self.catalogue.revision,
                    "requested_workflow_preset": intake.workflow_preset,
                    "evidence": dict(work.evidence), "open_conditions": list(work.conditions),
                    "pending_approval": work.approval.view() if work.approval else None,
                    "question": work.question, "elapsed_ms": round((time.monotonic() - work.started) * 1000),
                    "live": True, "state": "finished" if work.outcome else "running",
                    "conversation": intake.conversation_id, "handoff_id": work.handoff_id,
                    "activity": work.record.view(finished=work.outcome is not None,
                                                 attempts={"used": intake.attempts, "limit": intake.max_attempts})}

    def result(self, work, *, tools=(), staged_until=None):
        work.phase = "report"
        prompt = self.catalogue.prompt("reporter")
        def report_value(value):
            self.stage_value("reporter", "output_contract", value)
            return validate_report(work, value)

        def report_data():
            return {"brief": work.brief, "evidence": work.evidence, "open_conditions": work.conditions,
                    "required_status": "partial" if work.conditions and work.evidence else
                    "blocked" if work.conditions else None}

        transcript = []
        full_deadline = work.deadline
        if staged_until is not None:
            work.deadline = staged_until
        try:
            while tools and time.monotonic() < work.deadline:
                if work.intake.attempts + 1 >= work.intake.max_attempts:
                    break
                with self.operation(work, "ceo_report", self.ceo_path(), "Working with reviewed tools"):
                    raw, calls, replay = self.call(work, prompt, report_data(), tools=tools, keep=1,
                                                   transcript=transcript)
                    if not calls:
                        try:
                            return report_value(decode(raw))
                        except Refusal:
                            calls = None
                    if calls is not None:
                        if len(calls) != 1:
                            raise Refusal("tool_call_refused")
                        call = calls[0]
                        result = self.tool(work, tools, call)
                        transcript.extend((replay, modelspell.ToolOutput(call.id, jsonio.encode_exact(result))))
                if calls is None:
                    break
        except Refusal as exc:
            if exc.code != "deadline_exceeded" or not work.evidence:
                raise
            work.conditions.append(exc.code)
        finally:
            work.deadline = full_deadline
        with self.operation(work, "ceo_report", self.ceo_path(), "Writing the report"):
            return self.envelope(work, prompt, report_data(), report_value)

    def tool(self, work, tools, call):
        capability = next((tool for tool in tools if tool.name == call.name), None)
        if capability is None or (capability.kind != "mcp" and not self.state.chat_preferences()["builtins"]):
            raise Refusal("tool_call_refused")
        args = decode(self.public_text(call.arguments_json, 32 << 10))
        contract = agent_capabilities.schema_contract(capability.parameters)
        if checks.check_value(contract, args)["overall"] != "pass":
            raise Refusal("tool_call_refused")
        self.reserve(work)
        label = tool_label(capability)
        with self.lock:
            path = work.record.path_of(work.current_op) + [{"tier": "tool", "id": capability.name, "label": label}]
        with self.operation(work, "tool", path, f"Running {label}", model=False) as op:
            previous, work.tool_op = work.tool_op, op
            try:
                if capability.kind == "mcp":
                    if work.pathway not in ("tools", "specialists"):
                        raise Refusal("tool_call_refused")
                    reviewed = work.intake.external_tools.get(capability.name)
                    if reviewed is None:
                        raise Refusal("tool_call_refused")
                    output = company_tools.send(self, work, reviewed, args)
                elif capability.kind == "block":
                    output = self.run_packaged_block(work, capability.name, args)
                else:
                    output = agent_tools.run_utility(capability.builtin, args, result_limit=32 << 10,
                                                     check=lambda: self.reserve(work))
            except agent_tools.ToolError:
                raise Refusal("tool_call_refused") from None
            finally:
                work.tool_op = previous
            _, contains_credential = history.redact(output, self.state.credentials())
            if contains_credential:
                raise Refusal("credential_in_model_output")
            if len(jsonio.encode_exact(output).encode("utf-8")) > 32 << 10:
                raise Refusal("tool_result_limit")
            key = f"tool-{len(work.evidence) + 1}"
            with self.lock:
                if work.cancel.cancelled:
                    raise Refusal("stopped")
                work.evidence[key] = {"capability": capability.name, "result": output}
                work.trace.append({"stage": "tool", "id": key, "capability": capability.name})
                work.record.set_detail(op, {"evidence_id": key})
        self.report_event(work, "tool_result", title=label, evidence_id=key)
        return {"evidence_id": key, "result": output}

    def run_packaged_block(self, work, name, args):
        entry = self.catalogue.manifest["capabilities"][name]
        snapshot = self.catalogue.reference(entry["block"], "block")["snapshot"]
        ident = workflow_definition.digest(snapshot)

        def guard():
            try:
                self.reserve(work)
            except Refusal as exc:
                raise outbound_policy.Refused(exc.code) from None

        with self.lock:
            path = work.record.path_of(work.current_op)[:-1] + [
                {"tier": "block", "id": name, "label": entry["title"]}]
        with ExitStack() as stack:
            stack.enter_context(self.operation(work, "packaged_block", path, "Running reviewed block", model=False))
            block = workflow_definition.open_snapshot(ident, snapshot, stack, "company.block")
            frozen = runner.FrozenSource(None, False, {"company_asset": ident}, owner=block, parent_guard=guard)
            observer = CompanyWorkflow(self, work)
            with self.lock:
                self.reserve(work)
                work.child = observer
            try:
                ports = snapshot["contracts"]["inputs"]
                values = {key: value if decode(ports[key])["format"] == "text" else jsonio.encode_exact(value)
                          for key, value in args.items()}
                result = runner.run_block(self.state, load_capture(block, block.parsed.block.name), values, observer,
                                          frozen=frozen).result
            finally:
                with self.lock:
                    work.child = None
            if result["outcome"] != "complete":
                raise Refusal("stopped" if work.cancel.cancelled else "block_failed")
            outputs = {key: decode(value["text"]) if value["format"] == "json" else value["text"]
                       for key, value in result["outputs"].items()}
            return next(iter(outputs.values())) if len(outputs) == 1 else outputs

    def available_structures(self, intake):
        return {key: structure for key, structure in self.catalogue.manifest["structures"].items()
                if intake.policy in structure["allowed_policies"] and all(
                    not stage["method_scope"] or intake.tools for stage in structure["stages"])}

    def minimum(self, intake):
        if intake.policy == "C":
            options = [row["model_calls"] + 2 for row in self.catalogue.presets.values()
                       if intake.workflow_preset in (None, row["id"])
                       and (not row["uses_tools"] or self.state.chat_preferences()["builtins"])]
            return min(options, default=2)
        if intake.policy == "D":
            return min((len(item["stages"]) + 2 for item in self.available_structures(intake).values()), default=2)
        return MINIMUM[intake.policy]

    def eligible_presets(self, work):
        result = {}
        for ident, preset in self.catalogue.presets.items():
            intake = work.intake
            if (intake.workflow_preset not in (None, ident)
                    or intake.max_attempts - intake.attempts < preset["model_calls"] + 2):
                continue
            try:
                if preset.get("preflight", {}).get("format") == "csv":
                    company_catalogue.csv_source(preset, work.brief)
                with ExitStack() as stack:
                    graph_for(self, work, stack, preset)
            except (Refusal, ValueError, agent_tools.ToolError):
                continue
            result[ident] = preset
        return result

    def plan(self, work, options, *, keep):
        work.phase = "planning"
        structures = self.available_structures(work.intake)
        presets = work.presets if "workflow" in options else {}

        update = {}

        def validate(value):
            update.clear()
            if isinstance(value, dict) and "status_update" in value:
                update["text"] = value.pop("status_update")
            self.stage_value("planner", "output_contract", value)
            fields(value, ("strategy", "purpose", "conditions"), ("preset_id", "parameters", "structure", "binding_id"))
            if value["strategy"] not in options:
                raise Refusal("pathway_unsupported")
            binding = value.get("binding_id", work.intake.binding)
            if not isinstance(binding, str) or binding not in work.intake.alternatives:
                raise Refusal("pathway_unsupported")
            self.current(work.intake, binding)
            text(value["purpose"], 1000)
            strings(value["conditions"], 8, 300)
            if value["strategy"] == "workflow":
                preset = presets.get(text(value.get("preset_id"), 128))
                if not preset:
                    raise Refusal("pathway_unsupported")
                try:
                    company_catalogue.bind(preset, work.brief, value.get("parameters"))
                except (ValueError, agent_tools.ToolError):
                    raise Refusal("invalid_workflow_input") from None
                needed = preset["model_calls"] + 1
            elif value["strategy"] == "specialists":
                # A forced structure can be omitted when there is exactly one eligible choice.
                if "structure" not in value and len(structures) == 1:
                    value["structure"] = next(iter(structures))
                structure = structures.get(text(value.get("structure"), 128))
                if structure is None:
                    raise Refusal("pathway_unsupported")
                needed = len(structure["stages"]) + 1
            else:
                needed = 1
            if work.intake.max_attempts - work.intake.attempts < needed:
                raise Refusal("pathway_unsupported")
            return value

        planner_data = {
            "brief": work.brief, "strategy_inventory": options,
            "specialist_structures": [{"id": key, "title": row["title"]} for key, row in structures.items()],
            "workflow_presets": [{key: row[key] for key in ("id", "title", "fit", "revision", "model_calls", "parameters")}
                                 for row in presets.values()],
            "remaining_attempts": work.intake.max_attempts - work.intake.attempts,
            "model_inventory": [{"id": ident, "model": snap.model, "class": hint, "route": "OpenRouter",
                                 "known_limits": "Unknown"} for ident, (snap, hint) in work.intake.alternatives.items()]}
        with self.operation(work, "ceo_plan", self.ceo_path(), "Choosing an approach"):
            decision = self.envelope(work, self.catalogue.prompt("planner"), planner_data, validate, keep=keep)
        with self.lock:
            work.decision.append({key: decision[key] for key in
                                  ("strategy", "purpose", "structure", "binding_id", "preset_id")
                                  if key in decision})
        data = {"strategy": decision["strategy"], "ceo_update": self.ceo_update(update.get("text"))}
        if decision["strategy"] == "workflow":
            data["title"] = presets[decision["preset_id"]]["title"]
        elif decision["strategy"] == "specialists":
            structure = structures[decision["structure"]]
            data.update(title=structure["title"],
                        first=company_activity.agent_path(self.catalogue, structure["stages"][0]["entrypoint"])["label"])
        if update.get("text") is not None and data["ceo_update"] is None:
            data["ceo_update_rejected"] = True
        self.report_event(work, "plan_decided", **data)
        return decision

    def ceo_update(self, value):
        """The planner's optional customer-facing status line; rejected text never causes a repair."""
        if not isinstance(value, str) or not company_activity.fits(value) or re.search(r"https?://", value):
            return None
        try:
            return self.public_text(value, 1024)
        except Refusal:
            return None

    def stage_value(self, stage, key, value):
        record = self.catalogue.manifest["entrypoints"][stage]
        if not company_catalogue.check_schema(self.catalogue.json(record[key]), value):
            raise Refusal("invalid_model_output")
        return value

    def specialists(self, work, decision):
        structure = self.available_structures(work.intake)[decision["structure"]]
        stages = structure["stages"]
        results = {}
        labels = [company_activity.agent_path(self.catalogue, stage["entrypoint"]) for stage in stages]
        with self.lock:
            queued = [work.record.queue_op("specialist", [label], "Waiting to start") for label in labels]
        for index, stage in enumerate(stages):
            with self.operation(work, "specialist", [labels[index]], "Working on the assignment", ident=queued[index]):
                output = self.specialist_stage(work, decision, stages, index, results)
            data = {"stage": labels[index]["label"],
                    "quotes": sum(1 for key in work.evidence if key.startswith(f"{stage['id']}-quote-"))}
            if index + 1 < len(stages):
                data["next"] = labels[index + 1]["label"]
            if "passed" in output:
                data.update(passed=output["passed"] is True, findings=len(output.get("findings") or []))
            with self.lock:
                work.record.set_detail(queued[index], {key: data[key] for key in ("quotes", "passed", "findings")
                                                       if key in data})
            self.report_event(work, "specialist_result", **data)

    def specialist_stage(self, work, decision, stages, index, results):
        stage = stages[index]
        ident, entrypoint = stage["id"], stage["entrypoint"]
        work.phase = ident
        data = {}
        for name, binding in stage["bindings"].items():
            origin = binding["from"]
            data[name] = (work.brief if origin == "brief" else decision[binding["key"]]
                          if origin == "parameter" else results[binding["id"]][binding["field"]])
        self.stage_value(entrypoint, "input_contract", data)
        prompt = self.catalogue.prompt(entrypoint)

        def validate(value):
            self.stage_value(entrypoint, "output_contract", value)
            for check in stage["checks"]:
                if check["op"] == "same_candidate" and value[check["field"]] != data[check["field"]]:
                    raise Refusal("invalid_model_output")
                if check["op"] == "quotes":
                    quotations(work, value[check["field"]])
            return value

        keep = len(stages) - index
        output = None
        if "reviewed_tools" in stage["method_scope"]:
            transcript = []
            while work.intake.max_attempts - work.intake.attempts > keep + 1:
                raw, calls, replay = self.call(work, prompt, data, tools=work.intake.tools,
                                               keep=keep + 1, transcript=transcript)
                if not calls:
                    output = validate(decode(raw))
                    break
                if len(calls) != 1:
                    raise Refusal("tool_call_refused")
                result = self.tool(work, work.intake.tools, calls[0])
                transcript.extend((replay, modelspell.ToolOutput(calls[0].id, jsonio.encode_exact(result))))
            data["tool_evidence"] = {key: value for key, value in work.evidence.items() if key.startswith("tool-")}
        if output is None:
            output = self.envelope(work, prompt, data, validate, keep=keep)
        with self.lock:
            if work.cancel.cancelled:
                raise Refusal("stopped")
            for check in stage["checks"]:
                value = output[check["field"]]
                if check["op"] == "same_candidate":
                    work.assessment = output
                elif check["op"] == "candidate":
                    output["candidate_id"] = digest(value.encode("utf-8"))
                elif check["op"] == "quotes":
                    for number, quote in enumerate(quotations(work, value), 1):
                        work.evidence[f"{ident}-quote-{number}"] = quote
                elif check["op"] == "required_truth" and not value:
                    work.conditions.append(check["condition"])
            results[ident] = output
            work.evidence[ident] = output
            work.trace.append({"stage": ident, "outcome": "complete", "candidate_id": output.get("candidate_id")})
        return output

    def run_pathway(self, work):
        intake = work.intake
        if intake.max_attempts - intake.attempts < intake.minimum or intake.caps:
            raise Refusal("budget_bound_unavailable" if intake.caps else "attempts_exhausted")
        self.reserve(work)
        full_deadline = work.deadline
        forced = PATHWAYS.get(intake.policy)
        if forced == "solo":
            work.pathway = forced
            return self.result(work)
        work.deadline -= intake.window
        try:
            if forced == "tools":
                work.pathway = forced
                work.deadline = full_deadline
                return self.result(work, tools=intake.tools, staged_until=full_deadline - intake.window)
            if forced:
                if forced == "workflow":
                    work.presets = self.eligible_presets(work)
                    if not work.presets:
                        raise Refusal("pathway_unsupported")
                    keep = min(row["model_calls"] for row in work.presets.values()) + 1
                else:
                    keep = intake.minimum - 1
                decision = self.plan(work, [forced], keep=keep)
            else:
                for attempt in range(2):
                    options = ["solo", "tools"] if intake.tools else ["solo"]
                    if intake.max_attempts - intake.attempts >= 3 and self.available_structures(intake):
                        options.append("specialists")
                    with self.state.workshop.lock:
                        available = self.state.workshop.active is None
                    work.presets = self.eligible_presets(work) if available else {}
                    if work.presets:
                        options.append("workflow")
                    try:
                        decision = self.plan(work, options, keep=1)
                        if decision["strategy"] == "workflow":
                            acquire_workflow(self.state, work)
                        break
                    except Refusal as exc:
                        if attempt or exc.code not in ("pathway_unsupported", "admission_busy"):
                            raise
            work.binding = decision.get("binding_id", intake.binding)
            work.pathway = strategy = decision["strategy"]
            if strategy == "workflow":
                execute_workflow(self, work, decision)
            if strategy == "specialists":
                self.specialists(work, decision)
        except Refusal as exc:
            if not work.evidence or exc.code not in ("deadline_exceeded", "attempts_exhausted", "invalid_model_output"):
                raise
            work.conditions.append(exc.code)
        finally:
            work.deadline = full_deadline
            release_workflow(self.state, work)
        return self.result(work, tools=intake.tools if work.pathway == "tools" else (),
                           staged_until=full_deadline - intake.window)

    def execute(self, work, writer):
        result = None
        reason = None
        try:
            self.report_event(work, "run_started", policy=work.intake.policy)
            result = self.run_pathway(work)
        except Refusal as exc:
            reason = exc.code
        except Exception as exc:
            report_internal("company CEO", exc)
            reason = "internal_error"
        finally:
            release_workflow(self.state, work)
            with self.lock:
                work.phase = "finished"
                if work.cancel.cancelled:
                    work.outcome, work.reason = "stopped", "user_stop"
                elif time.monotonic() >= work.deadline:
                    work.outcome, work.reason = ("partial" if work.evidence else "blocked"), "deadline_exceeded"
                elif result is not None:
                    work.outcome = result["status"]
                    work.reason = next(iter(work.conditions), None)
                    work.question = result.get("question")
                    work.report = result["markdown"].encode("utf-8")
                    work.evidence.update(result.get("verified_quotes", {}))
                    work.conditions = list(dict.fromkeys([*work.conditions, *result["open_conditions"]]))
                    if work.save:
                        self.persist(work)
                else:
                    blocked = reason in BLOCKED
                    work.outcome = "partial" if work.evidence else "blocked" if blocked else "failed"
                    work.reason = reason
                    if reason and reason not in work.conditions:
                        work.conditions.append(reason)
                record = work.record
                record.close(work.outcome, work.reason)
                states = [op["state"] for op in record.operations.values()]
                record.event("run_finished", reportable=True, outcome=work.outcome, reason=work.reason,
                             report_status=work.outcome if work.report is not None else None,
                             open_conditions=len(work.conditions), has_question=bool(work.question),
                             completed=states.count("complete"), not_run=states.count("not_run"))
                record.publish_pending(final=True)
                self.active = None
            try:
                self.flush(work, force=True, usage=work.usage)
            finally:
                self.state.agent.release_company(work.ident)
                writer.release()

    def persist(self, work):
        try:
            folder = self.state.workspace.path / "runs"
            if folder.is_symlink():
                raise OSError("unsafe evidence folder")
            folder.mkdir(mode=0o700, exist_ok=True)
            record = {"format_version": 1, "brief_digest": digest(work.brief.encode("utf-8")),
                      "brief": work.brief, "report": work.report.decode("utf-8"),
                      "report_digest": digest(work.report), "report_size": len(work.report),
                      "trace": {**self.view(work), "persisted": True}}
            _, has_secret = history.redact(record, self.state.credentials())
            if has_secret or self.state.view()["history"]["short_credential_held"]:
                work.save_error = "evidence_contains_credential"
                return
            workspace.write_atomic(folder, f"company-{work.ident}.json", record)
            work.persisted = True
        except (OSError, ValueError):
            work.save_error = "evidence_write_failed"


def strings(value, count, limit):
    if not isinstance(value, list) or len(value) > count:
        raise Refusal("invalid_model_output")
    return [text(item, limit) for item in value]


def quotations(work, values, *, count=8):
    source = work.brief.encode("utf-8")
    result = []
    for quote in strings(values, count, 2000):
        encoded = quote.encode("utf-8")
        start = source.find(encoded)
        if start < 0:
            raise Refusal("invalid_quote")
        result.append({"quote": quote, "source_digest": digest(source), "start": start, "end": start + len(encoded)})
    return result


def validate_report(work, result):
    fields(result, ("status", "markdown", "evidence_ids", "open_conditions"), ("question", "source_quotes", "checked_candidate_id"))
    status = result["status"]
    if status not in ("complete", "partial", "needs_clarification", "blocked"):
        raise Refusal("invalid_report")
    report = text(result["markdown"])
    ids = strings(result["evidence_ids"], 48, 80)
    conditions = strings(result["open_conditions"], 24, 500)
    verified = {f"brief-quote-{i}": quote for i, quote in
                enumerate(quotations(work, result.get("source_quotes", [])), 1)}
    evidence = {**work.evidence, **verified}
    if (len(set(ids)) != len(ids) or set(verified) - set(ids) or set(ids) - evidence.keys()
            or set(work.conditions) - set(conditions)):
        raise Refusal("invalid_report")
    checked = work.assessment
    if status == "complete" and (conditions or (checked and
            (not checked["passed"] or result.get("checked_candidate_id") != checked["candidate_id"]))):
        raise Refusal("invalid_report")
    if status == "partial" and work.conditions and not evidence:
        raise Refusal("invalid_report")
    if status == "blocked" and work.conditions and evidence:
        raise Refusal("invalid_report")
    if status == "needs_clarification":
        text(result.get("question"), 4096)
    elif "question" in result:
        raise Refusal("invalid_report")
    if re.findall(r"(?im)^\s*Status: *(\w+)\s*$", report) != [status]:
        raise Refusal("invalid_report")
    headings = re.split(r"(?m)^## (Result|Evidence|Open conditions)\s*$", report)
    if len(headings) != 7 or headings[1::2] != ["Result", "Evidence", "Open conditions"]:
        raise Refusal("invalid_report")
    if any(not section.strip() for section in headings[2::2]):
        raise Refusal("invalid_report")
    permitted = {evidence[key].get("quote") for key in ids}
    for quote in re.findall(r'"([^"\n]+)"|“([^”\n]+)”|(?m:^>\s?([^\n]+))', headings[4]):
        if next(part for part in quote if part) not in permitted:
            raise Refusal("invalid_quote")
    return {**result, "verified_quotes": verified}


def entry_id():
    return "ce_" + secrets.token_hex(8)


def clip_bytes(value, limit):
    data = value.encode("utf-8")[:max(0, limit)]
    return data.decode("utf-8", "ignore")


def tool_label(capability):
    if capability.kind == "block":
        return capability.description
    return capability.tool or capability.builtin or capability.name


def handoff_entry(brief, intake, origin, summary):
    return {"id": entry_id(), "type": "handoff", "at": runs.utc_now(), "intake_id": intake, "origin": origin,
            "summary": summary, "brief": brief, "brief_sha256": digest(brief.encode("utf-8")),
            "state": "draft", "run_id": None}


def handoff_summary(service, value):
    """The representative's structured summary, or None. An invalid summary never fails the turn."""
    try:
        fields(value, ("objective", "constraints", "deliverable"))
        constraints = value["constraints"]
        if not isinstance(constraints, list) or len(constraints) > 6:
            return None
        return {"objective": service.public_text(value["objective"], 300),
                "constraints": [service.public_text(item, 200) for item in constraints],
                "deliverable": service.public_text(value["deliverable"], 300)}
    except Refusal:
        return None


def representative_context(entries):
    """Company-only context for the representative: earlier company turns and the latest finished run."""
    rows, pending, last = [], None, None
    for entry in entries.values():
        if entry["type"] == "user":
            pending = entry
        elif entry["type"] == "representative" and pending is not None:
            rows.append({"message": pending["text"], "kind": entry["kind"], "reply": entry["text"]})
            pending = None
        elif entry["type"] == "handoff" and entry["origin"] == "representative" and rows:
            rows[-1]["brief"] = entry["brief"]
        elif entry["type"] == "run" and entry["state"] != "running":
            last = entry
    last_run = None
    if last is not None:
        handoff = entries.get(last["handoff_id"]) or {}
        report = (last.get("report") or {}).get("markdown")
        clipped = clip_bytes(report, REPORT_CONTEXT) if report else None
        last_run = {"brief": handoff.get("brief", ""), "outcome": last["outcome"], "question": last["question"],
                    "open_conditions": last["open_conditions"], "report_markdown": clipped,
                    "report_truncated": bool(report) and clipped != report}
    return rows[-6:], last_run


@lru_cache(maxsize=1)
def packaged_catalogue():
    return company_catalogue.Catalogue(ASSETS)


from .company_workflow import CompanyWorkflow, acquire_workflow, execute_workflow, graph_for, release_workflow
