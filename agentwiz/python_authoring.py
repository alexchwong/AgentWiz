"""Python authoring in Workshop: Describe it (0.2.2, 10.7, F-22, F-30).

The flow: the user writes an instruction and may declare ports; **Generate examples** asks the model once (plus
scriptSpell's one format retry) for YAML examples; the examples are reviewed as fields and edited only as YAML,
validated locally by scriptSpell; **Generate and check** asks the model for the Python source and runs every
example through the published path: each example is built once as the saved-example object Workshop stores
(per-port strings), checked with `runner.check_inputs`, executed by the shared adapter and compared with
Workshop's own saved-example assertion. **Apply** puts exactly those objects and the source into the draft;
Test and Save stay the existing, separate Workshop actions.

The flow object lives in process memory with the Workshop draft. AgentWiz writes no YAML of its own and never
imports `yaml`; `AuthoringSession` (which writes prompts and replies to disk) is not used.
Knows nothing of HTTP.
"""
from __future__ import annotations

import secrets
import threading
from collections import OrderedDict
from typing import Any, Mapping

import modelspell

from . import blocks, checks, jsonio, model_call, outbound_policy, python_step, runner, runs
from .workshop import WorkshopError

FLOW_LIMIT = 16
REQUEST_LIMIT = 3              # examples (and one format retry) and Python: at most three requests
EXAMPLE_COUNT = 3
ALLOWED_LEAVES = ("str", "bool", "None", "int | Decimal", "dict")


def fail(code: str, status: int = 409, issues: list | None = None):
    raise WorkshopError(code, status, issues or [])


# --- the contract mapping (closed) ----------------------------------------------------------------------------------
def _schema_type(schema: Any, where: str) -> str:
    if not isinstance(schema, dict):
        fail("unsupported_contract", 409, [{"location": where, "message": "this contract has no Python mapping"}])
    kind = schema.get("type")
    if kind == "string":
        return "str"
    if kind == "boolean":
        return "bool"
    if kind == "null":
        return "None"
    if kind in ("integer", "number"):
        return "int | Decimal"
    if kind == "array" and "items" in schema:
        return f"list[{_schema_type(schema['items'], where + '[]')}]"
    if kind == "object":
        extra = schema.get("additionalProperties")
        if not schema.get("properties") and isinstance(extra, dict):
            return f"dict[str, {_schema_type(extra, where + '.*')}]"
        return "dict"
    fail("unsupported_contract", 409, [{"location": where, "message": "this contract has no Python mapping"}])


def port_type(contract: checks.Contract, where: str) -> str:
    if contract.format == "text":
        return "str"
    if contract.schema is None:
        fail("unsupported_contract", 409, [{"location": where, "message": "a JSON port needs a schema to be mapped"}])
    return _schema_type(contract.schema, where)


def _check_declaration(expression: object, where: str) -> None:
    """Refuse an inferred type outside the mapping (a float included), naming the port."""
    text = expression.strip() if isinstance(expression, str) else None
    while text is not None and text.startswith(("list[", "dict[str, ")) and text.endswith("]"):
        text = text[5:-1] if text.startswith("list[") else text[10:-1]
    if text not in ALLOWED_LEAVES:
        fail("unsupported_type", 409, [{"location": where, "message": "use str, bool, None, int | Decimal, "
                                                                        "list[...], dict[str, ...] or dict"}])


def contract_for(expression: str, is_input: bool) -> dict:
    """The port contract an inferred type declares (the inverse mapping)."""
    text = expression.strip()

    def schema(t: str) -> dict:
        if t == "str":
            return {"type": "string"}
        if t == "bool":
            return {"type": "boolean"}
        if t == "None":
            return {"type": "null"}
        if t == "int | Decimal":
            return {"type": "number"}
        if t == "dict":
            return {"type": "object"}
        if t.startswith("list["):
            return {"type": "array", "items": schema(t[5:-1])}
        return {"type": "object", "additionalProperties": schema(t[10:-1])}
    if text == "str":
        return {"format": "text"}
    return {"format": "json", "schema": schema(text)}


# --- the flow ----------------------------------------------------------------------------------------------------------
class Flow:
    def __init__(self, ident: str, key: str, instruction: str, interface: dict | None, ports: dict | None,
                 role: Any, protected: bool, dispatch_guard: Any, labels: dict):
        import scriptspell
        self.id = ident
        self.key = key
        self.instruction = instruction
        self.interface = interface
        self.ports = ports                     # {"inputs": {name: contract}, "outputs": {...}} when declared
        self.role = role                       # state.RoleCapture with the key resolved, taken at start
        self.protected = protected
        self.guard = dispatch_guard            # outbound_policy.Dispatch when protected, else None
        self.labels = labels                   # {side: {port: bool}} carried from the draft
        self.authoring = scriptspell.YamlAuthoring(instruction, count=EXAMPLE_COUNT, interface=interface)
        self.attempts: list[dict] = []
        self.handle = modelspell.CancelHandle()
        self.lock = threading.Lock()
        self.busy = False
        self.source: str | None = None
        self.examples: list[dict] = []         # saved-example objects (per-port strings), built once
        self.results: list[dict] = []

    def interface_now(self) -> dict | None:
        document = self.authoring.document
        if document is None:
            return None
        return {side: dict(document["interface"][side]) for side in ("inputs", "outputs")}


class Authoring:
    """The process's authoring flows, bounded; each is addressed by its opaque ID."""

    def __init__(self, state: Any):
        self.state = state
        self.lock = threading.Lock()
        self.flows: "OrderedDict[str, Flow]" = OrderedDict()

    def _flow(self, ident: object) -> Flow:
        with self.lock:
            flow = self.flows.get(ident) if isinstance(ident, str) else None
        if flow is None:
            fail("flow_expired", 410)
        return flow

    # --- start ----------------------------------------------------------------------------------------------------
    def start(self, body: Mapping) -> dict:
        if set(body) - {"key", "instruction", "role", "draft"} or not isinstance(body.get("instruction"), str):
            fail("invalid", 400)
        key = body.get("key")
        if not isinstance(key, str) or blocks.NAME_RE.match(key) is None:
            fail("invalid", 400)
        instruction = body["instruction"].strip()
        if not instruction:
            fail("instruction_required", 400)
        role_name = body.get("role")
        if not isinstance(role_name, str):
            fail("invalid", 400)
        interface, ports, labels, protected, context = None, None, {"inputs": {}, "outputs": {}}, False, False
        draft = body.get("draft")
        if draft is not None:
            parsed = self._parse_draft(draft)
            protected = bool(parsed["protected"])
            labels = parsed["labels"]
            context = parsed["context"]
            if parsed["block"] is not None and (parsed["block"].inputs or parsed["block"].outputs):
                block = parsed["block"]
                interface = {"inputs": {}, "outputs": {}}
                ports = {"inputs": {}, "outputs": {}}
                for side in ("inputs", "outputs"):
                    for declared in getattr(block, side):
                        copy_ = block.contracts[side][declared.name]
                        interface[side][declared.name] = port_type(copy_.parsed, f"{side}.{declared.name}")
                        ports[side][declared.name] = dict(copy_.parsed.raw)
                if not interface["outputs"]:
                    interface, ports = None, None
        from .state import RoleUnresolved
        try:
            role = self.state.role_capture(role_name)
        except RoleUnresolved as exc:
            fail("role_unresolved", 409, [{"location": "role", "message": str(exc)}])
        import dataclasses
        key_value = outbound_policy.credential(role.snapshot.endpoint)
        role = dataclasses.replace(role, snapshot=dataclasses.replace(role.snapshot, endpoint=dataclasses.replace(
            role.snapshot.endpoint, api_key=key_value, api_key_env=None)))
        guard = None
        if protected:
            # A protected design or protected example values need a trusted local model (10.7).
            def unchanged() -> bool:
                try:
                    return self.state.role_capture(role_name).snapshot.model == role.snapshot.model and \
                        self.state.role_capture(role_name).snapshot.endpoint.base_url == role.snapshot.endpoint.base_url
                except Exception:  # noqa: BLE001
                    return False
            try:
                guard = outbound_policy.Dispatch.prepare(self.state, outbound_policy.model_identity(role.snapshot),
                                                         unchanged)
            except outbound_policy.Refused as exc:
                fail(str(exc), 409, [{"location": "role", "message": "protected authoring needs a trusted local model"}])
        import scriptspell
        try:
            flow = Flow(secrets.token_hex(16), key, instruction, interface, ports, role, protected, guard, labels)
            flow.context_local_only = context
        except scriptspell.AuthoringError as exc:
            fail(str(exc) or "invalid_authoring_context", 400)
        with self.lock:
            self.flows[flow.id] = flow
            while len(self.flows) > FLOW_LIMIT:
                self.flows.popitem(last=False)
        return self.view(flow)

    def _parse_draft(self, draft: Mapping) -> dict:
        service = self.state.workshop
        source = service.source({"draft": draft})
        definition = jsonio.decode_exact(source["definition_text"])
        labels = {side: {name: port.get("local_only") for name, port in definition.get(side, {}).items()
                         if isinstance(port, dict) and type(port.get("local_only")) is bool}
                  for side in ("inputs", "outputs")}
        context = definition.get("context_local_only") is True
        protected = bool(context or any(v for side in labels.values() for v in side.values()))
        with service.assets.validate(source) as captured:
            block = captured.parsed.block
        return {"block": block, "labels": labels, "protected": protected, "context": context}

    # --- the injected dispatcher (10.7) ----------------------------------------------------------------------
    def _dispatcher(self, flow: Flow):
        state = self.state

        def dispatch(materials: Mapping, settings: Mapping):
            import scriptspell
            with flow.lock:
                if len(flow.attempts) >= REQUEST_LIMIT:
                    raise scriptspell.AuthoringError("request_limit")
            if flow.handle.cancelled:
                raise scriptspell.AuthoringError("stopped")
            live = state.role_capture(flow.role.role)
            if live.snapshot != flow.role.snapshot:
                raise scriptspell.AuthoringError("role_changed")
            messages = scriptspell.model_messages(materials)
            if flow.guard is not None:
                try:
                    flow.guard.check()                                   # the outbound decision for these messages
                except outbound_policy.Refused as exc:
                    raise scriptspell.AuthoringError(str(exc)) from None
            if not state.budget.try_acquire():
                raise scriptspell.AuthoringError("busy")
            context = dict(materials.get("request_context") or {})
            record = {"stage": context.get("stage"), "attempt": context.get("attempt"), "outcome": None,
                      "destination": {"kind": flow.role.snapshot.kind, "url": flow.role.snapshot.endpoint.base_url,
                                      "model": flow.role.snapshot.model}}
            with flow.lock:
                flow.attempts.append(record)
            try:
                from .task_settings import TaskSettings, resolve_effective
                effective = resolve_effective(TaskSettings(), (), flow.role.defaults)
                arguments = model_call.task_arguments(effective.task, flow.role.snapshot.kind,
                                                      flow.role.snapshot.provider)
                record["routing"] = {}
                result = model_call.call_model(flow.role.snapshot, messages, arguments,
                                               cancel=flow.handle, on_text=None, on_reasoning=None,
                                               before_complete=flow.guard.check if flow.guard is not None else None,
                                               routing=record["routing"])
            except modelspell.Cancelled:
                record["outcome"] = "stopped"
                raise scriptspell.AuthoringError("stopped") from None
            except modelspell.ModelSpellError as exc:
                record["outcome"] = "error"
                record["error"] = model_call.describe_error(exc, flow.role.snapshot.endpoint.base_url)
                raise scriptspell.AuthoringError("model_error") from None
            finally:
                state.budget.release()
            record["outcome"] = model_call.classify_outcome(result)
            return result
        return dispatch

    def _call(self, flow: Flow, work):
        import scriptspell
        with flow.lock:
            if flow.busy:
                fail("flow_busy", 409)
            flow.busy = True
        try:
            return work()
        except scriptspell.AuthoringError as exc:
            details = getattr(exc, "details", {}) or {}
            issues = [{"location": "examples", "message": str(issue)} for issue in details.get("issues", [])[:20]]
            fail(str(exc) or "authoring_failed", 409, issues)
        finally:
            with flow.lock:
                flow.busy = False

    # --- steps --------------------------------------------------------------------------------------------------
    def generate_examples(self, ident: object) -> dict:
        flow = self._flow(ident)
        self._call(flow, lambda: flow.authoring.generate("examples", self._dispatcher(flow), {}))
        self._check_types(flow)
        return self.view(flow)

    def accept_examples(self, ident: object, text: object) -> dict:
        """Edit as YAML: validated locally by scriptSpell, with no model call (F-30)."""
        flow = self._flow(ident)
        if not isinstance(text, str):
            fail("invalid", 400)
        self._call(flow, lambda: flow.authoring.accept("examples", text))
        self._check_types(flow)
        flow.source, flow.examples, flow.results = None, [], []
        return self.view(flow)

    def _check_types(self, flow: Flow) -> None:
        interface = flow.interface_now() or {}
        for side, fields in interface.items():
            for name, expression in fields.items():
                _check_declaration(expression, f"interface.{side}.{name}")
        for index, pair in enumerate(flow.authoring.pairs or [], 1):
            for side in ("inputs", "outputs"):
                for name, value in pair.get(side, {}).items():
                    if python_step.float_path(value) is not None or not python_step.is_exact(value):
                        fail("float_value", 409, [{"location": f"examples[{index}].{side}.{name}",
                                                   "message": "use an exact number: an integer or a quoted decimal"}])

    def generate_and_check(self, ident: object) -> dict:
        flow = self._flow(ident)
        if flow.authoring.document is None:
            fail("stage_not_ready", 409)
        self._call(flow, lambda: flow.authoring.generate("python", self._dispatcher(flow), {}))
        draft = self._call(flow, flow.authoring.draft)
        flow.source = draft["source"]
        self._build_examples(flow)
        self._check(flow)
        return self.view(flow)

    def _contracts(self, flow: Flow) -> dict:
        interface = flow.interface_now()
        if flow.ports is not None:
            return flow.ports
        return {side: {name: contract_for(expression, side == "inputs") for name, expression in fields.items()}
                for side, fields in interface.items()}

    def _build_examples(self, flow: Flow) -> None:
        """Each example is built once as the object Workshop stores: per-port strings (10.7, v4)."""
        contracts = self._contracts(flow)
        examples = []
        for index, pair in enumerate(flow.authoring.pairs or [], 1):
            def text_of(side: str, name: str, value: Any) -> str:
                contract = contracts[side][name]
                if contract.get("format") == "text":
                    if not isinstance(value, str):
                        fail("example_type", 409, [{"location": f"examples[{index}].{side}.{name}",
                                                    "message": "a Text port takes a string"}])
                    return value
                return jsonio.encode_exact(value)
            examples.append({"id": f"authored-{index}", "version": 1, "label": f"authored {index}",
                             "inputs": {n: text_of("inputs", n, v) for n, v in pair["inputs"].items()},
                             "expectation": {"kind": "whole_output",
                                             "values": {n: text_of("outputs", n, v) for n, v in pair["outputs"].items()}},
                             "origin": "llm_generated", "verified": False, "held_out": False})
        flow.examples = examples

    def draft_for(self, flow: Flow, *, for_check: bool = False) -> dict:
        """The Workshop draft of the flow. Labels the author has not chosen stay unset, so Save is refused until
        each port is chosen; `for_check` fills them with False only to parse the block for Generate and check,
        where labels have no effect (no destination)."""
        from .workshop_editor import PYTHON_FILE, python_definition
        from .workshop_assets import wire_files
        contracts = self._contracts(flow)
        definition = python_definition(flow.key, contracts["inputs"], contracts["outputs"])
        definition["description"] = flow.instruction[:4096]
        for side in ("inputs", "outputs"):
            for name, declaration in definition[side].items():
                if name in flow.labels.get(side, {}):
                    declaration["local_only"] = flow.labels[side][name]
                elif for_check:
                    declaration["local_only"] = False
        if flow.context_local_only:
            definition["context_local_only"] = True
        snap = flow.role.snapshot
        metadata = {"format_version": 1, "origin": "llm_generated", "effects": "read-only",
                    "authoring": {"scriptspell_version": python_step.version(), **python_step.engine(),
                                  "prompt_version": "scriptspell-" + python_step.version(),
                                  "destination": {"kind": snap.kind, "url": snap.endpoint.base_url,
                                                  "model": snap.model}}}
        return {"key": flow.key, "definition_text": jsonio.encode_exact(definition),
                "files": wire_files({PYTHON_FILE: (flow.source or "").encode("utf-8")}), "metadata": metadata}

    def _check(self, flow: Flow) -> None:
        """Run every example through the published path: check_inputs, the shared adapter, output checks and
        Workshop's saved-example assertion. One execution and one expected/actual rule."""
        from .workshop_service import judgement
        service = self.state.workshop
        draft = self.draft_for(flow, for_check=True)
        results = []
        with service.assets.validate(draft) as captured:
            block = captured.parsed.block
            if block is None:
                problem = captured.parsed.problem
                flow.results = [{"id": ex["id"], "status": "invalid",
                                 "issues": list(problem.issues) if problem else []} for ex in flow.examples]
                return
            for example in flow.examples:
                row = {"id": example["id"], "inputs": example["inputs"], "expected": example["expectation"]["values"],
                       "actual": None, "status": None}
                inputs = runner.check_inputs(block, example["inputs"])
                if not inputs.passed:
                    row.update(status="input_check_failed", issues=inputs.issues[:20])
                    results.append(row)
                    continue
                if not self.state.python.try_acquire():
                    row["status"] = "busy"
                    results.append(row)
                    continue
                try:
                    done = python_step.run(block.texts[block.step.script_file], inputs.values,
                                           tuple(d.name for d in block.outputs), token=runs.CancelToken(),
                                           on_cleanup_failed=self.state.python_cleanup_failed)
                finally:
                    self.state.python.release()
                if done["outcome"] != "ok":
                    row.update(status=done["failure"], operation=done["operation"])
                    results.append(row)
                    continue
                outputs, passed = {}, True
                for declared in block.outputs:
                    contract = block.contracts["outputs"][declared.name].parsed
                    value = done["values"][declared.name]
                    check = checks.check_value(contract, value) if contract.format == "json" or isinstance(value, str) \
                        else {"overall": "fail"}
                    passed = passed and check["overall"] == "pass"
                    outputs[declared.name] = {"format": contract.format,
                                              "text": value if contract.format == "text" and isinstance(value, str)
                                              else jsonio.encode_exact(value)}
                row["actual"] = {name: view["text"] for name, view in outputs.items()}
                if not passed:
                    row["status"] = "contract_failed"
                else:
                    row["status"] = "match" if judgement(outputs, example["expectation"], True) == "correct" \
                        else "mismatch"
                results.append(row)
        flow.results = results

    def apply(self, ident: object) -> dict:
        """The draft and examples exactly as reviewed. Nothing is tested or saved here (10.7 step 6)."""
        flow = self._flow(ident)
        if flow.source is None:
            fail("stage_not_ready", 409)
        return {"draft": self.draft_for(flow), "examples": [dict(ex) for ex in flow.examples]}

    def stop(self, ident: object) -> dict:
        flow = self._flow(ident)
        flow.handle.cancel()
        return {"status": "stopping"}

    def view(self, flow: Flow) -> dict:
        document = flow.authoring.document
        out = {"format_version": 1, "flow": flow.id, "key": flow.key, "requests": len(flow.attempts),
               "request_limit": REQUEST_LIMIT, "attempts": [dict(a) for a in flow.attempts],
               "interface": flow.interface_now() or flow.interface, "protected": flow.protected,
               "examples": None, "examples_yaml": None, "source": flow.source, "results": flow.results}
        if document is not None:
            out["examples_yaml"] = flow.authoring.examples_yaml()
            out["examples"] = [{"inputs": {k: jsonio.encode_exact(v) for k, v in pair["inputs"].items()},
                                "outputs": {k: jsonio.encode_exact(v) for k, v in pair["outputs"].items()}}
                               for pair in flow.authoring.pairs or []]
        return out

