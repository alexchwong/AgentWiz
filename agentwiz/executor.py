"""The step executor: runs one step of a block run, of any kind, and returns its step result (0.1.4, Section 11).

Only `runner.py` calls it, and it knows nothing of HTTP. The runner has already admitted the run, checked the
inputs and prepared the step (Section 10.5). Each kind has one terminal decision (Section 10.6):

* work that finished on its own is judged on its result; work ended by the run's cancellation, or never started
  because cancellation won the dispatch gate, is `stopped`;
* an accepted output survives a late Stop; an output that is not accepted, with the token cancelled, is `stopped`:
  cancellation is inspected before any repair, exhaustion or allowance decision.

Messages name rules and locations, never values.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from . import checks, jsonio, mcp_ops, model_step, runs, script_step, templates, transforms
from .errors import report_internal


# --- what the runner prepared --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class PreparedModel:
    snapshot: Any                      # model_call.RequestSnapshot, resolved once from the role
    task: Any                          # task_settings.TaskSettings, resolved for this call
    arguments: dict                    # model_call.task_arguments(...)
    messages: list                     # the filled system and prompt messages
    fills: list
    role: str
    settings: Any = None               # {field: "block_override" | "block_reset" | "role_default" | "backend_default"}


@dataclass(frozen=True)
class PreparedTool:
    snapshot: Any                      # mcp_client.Snapshot
    tool: str
    arguments: Any                     # filled, exact
    arguments_text: str
    fills: list
    reservation: mcp_ops.Reservation


@dataclass(frozen=True)
class PreparedScript:
    path: Path
    sha256: str
    file: str
    timeout_s: int
    run_folder: Path | None            # the run's folder when it is being saved (scratch goes under it)


class Tally:
    """Reported usage of a run's model attempts (10.4): only what the backend reported."""

    def __init__(self, limit: int | None):
        self.limit = limit
        self.reported = 0
        self.unreported: list[str] = []

    def add(self, record: dict) -> None:
        usage = (record.get("result") or {}).get("usage") if isinstance(record.get("result"), dict) else None
        count = None
        if isinstance(usage, dict):
            total = usage.get("total_tokens")
            if isinstance(total, int) and not isinstance(total, bool):
                count = total
            elif all(isinstance(usage.get(k), int) and not isinstance(usage.get(k), bool)
                     for k in ("input_tokens", "output_tokens")):
                count = usage["input_tokens"] + usage["output_tokens"]
        if count is None:
            if record.get("model_called", True):
                self.unreported.append(record["id"])
        else:
            self.reported += count

    def reached(self) -> bool:
        return self.limit is not None and self.reported >= self.limit

    def view(self) -> dict:
        over = None
        if self.limit is not None:
            over = max(0, self.reported - self.limit)
        return {"reported_tokens": self.reported, "limit": self.limit, "over_by": over,
                "unreported_attempts": list(self.unreported)}


@dataclass
class StepContext:
    run: runs.Run
    block: Any                                   # blocks.Block
    values: Mapping[str, object]                 # checked input values (exact)
    prepared: Any                                # PreparedModel | PreparedTool | PreparedScript
    contracts: Mapping[str, Any]                 # captured output contracts by output name (ContractCopy)
    store: runs.AttemptStore
    sink: Any                                    # history.Writer | None
    ops: mcp_ops.Ops
    observer: Any                                # attempt_committed(record), operation_started(kind); never raises
    client: Any = None                           # mcp_client.MCPClient
    key_for: Callable[[str, str], str | None] | None = None
    script_env: script_step.Env | None = None
    script_handle: script_step.Handle = field(default_factory=script_step.Handle)
    tally: Tally = field(default_factory=lambda: Tally(None))
    dispatch: Any = None
    repair_dispatch: Any = None
    attempt_gate: Any = None
    produced: dict = field(default_factory=dict)  # output name -> value view, filled when the step is ok


# --- helpers ---------------------------------------------------------------------------------------------------------
def _stop_code(ctx: StepContext) -> str:
    """The failure code of a step ended by cancellation: `budget_exceeded` or `stopped`, by the token's reason."""
    return runs.BUDGET_EXCEEDED if ctx.run.token.reason == runs.BUDGET_EXCEEDED else "stopped"


def view_of(contract: checks.Contract, value: object) -> dict:
    """A value view (9.5): the text itself for a text contract, `encode_exact(value)` for a JSON contract."""
    return {"format": contract.format, "text": value if contract.format == "text" else jsonio.encode_exact(value)}


def _base(ctx: StepContext, kind: str, fills: list) -> dict:
    return blank_step(ctx.run.step_id, kind, fills)


def blank_step(step_id: str, kind: str, fills: list = ()) -> dict:
    """A step result with every field of Section 11.1 present (outcome `failed`, nothing produced)."""
    return {"step_id": step_id, "kind": kind, "outcome": "failed", "failure": None, "role": None,
            "profile": None, "endpoint": None, "model": None, "fills": [dict(f) for f in fills], "attempts": None,
            "accepted_attempt": None, "max_repairs": None, "repairs_used": None, "truncated_attempts": None,
            "max_tokens": None, "operation": None, "output_checks": {}}


def _ok(result: dict) -> dict:
    result["outcome"], result["failure"] = "ok", None
    return result


def _failed(result: dict, code: str) -> dict:
    result["outcome"], result["failure"] = "failed", code
    return result


def _stopped(ctx: StepContext, result: dict) -> dict:
    result["outcome"], result["failure"] = "stopped", _stop_code(ctx)
    return result


def _publish(ctx: StepContext, plan, values: object) -> None:
    """Stage every accepted output, then publish them together: a staged failure publishes nothing."""
    if not isinstance(values, Mapping):
        raise ValueError("the accepted value is not a named envelope")
    staged: dict = {}
    for name, _description, copy in plan.outputs:
        if name not in values:
            raise ValueError("an accepted output is missing")
        staged[name] = view_of(copy.parsed, values[name])
    ctx.produced.update(staged)


# --- model steps (11.2) ------------------------------------------------------------------------------------------------
def _execute_model(ctx: StepContext) -> dict:
    step, p, token = ctx.block.step, ctx.prepared, ctx.run.token
    plan = model_step.OutputPlan.of(ctx.block.outputs, ctx.contracts)
    snap = p.snapshot
    result = _base(ctx, "model", p.fills)
    result.update(role=p.role, profile=snap.profile_name,
                  endpoint={"kind": snap.kind, "base_url": snap.endpoint.base_url}, model=snap.model, attempts=[],
                  max_repairs=step.max_repairs, repairs_used=0, truncated_attempts=0,
                  max_tokens=getattr(p.task, "max_tokens", None))
    tally = ctx.tally
    callbacks = model_step.Callbacks(committed=lambda record, status: ctx.observer.attempt_committed(record))
    from . import outbound_policy
    parent: dict | None = None
    dispatch = ctx.dispatch
    while True:
        try:
            if parent is not None and ctx.repair_dispatch and (dispatch is None or dispatch.destination is None):
                dispatch = ctx.repair_dispatch()
            if dispatch:
                dispatch.check()
        except outbound_policy.Refused as exc:
            return _failed(result, str(exc))
        try:
            number = ctx.store.reserve_attempt(ctx.run)            # the dispatch gate
        except runs.RunStopped:
            return _stopped(ctx, result)
        if ctx.attempt_gate and not ctx.attempt_gate():
            ctx.store.abandon_run(ctx.run, number)
            return _failed(result, runs.BUDGET_EXCEEDED)
        if parent is None:
            prepared = model_step.prepare_block_first(ctx.run, number, snapshot=snap, task=p.task,
                                                      arguments=p.arguments, messages=p.messages, plan=plan,
                                                      role=p.role, fills=p.fills, settings_sources=p.settings)
        else:
            try:
                prepared = model_step.prepare_repair(ctx.run, number, parent, ctx.key_for)
            except BaseException:
                ctx.store.abandon_run(ctx.run, number)
                raise
            result["repairs_used"] += 1
        record, value = model_step.run_attempt_with_value(
            ctx.store, prepared, callbacks, ctx.sink, dispatch=dispatch) if dispatch else (
            model_step.run_attempt_with_value(ctx.store, prepared, callbacks, ctx.sink))
        result["attempts"].append(record["id"])
        tally.add(record)
        if record["outcome"] == "truncated":
            result["truncated_attempts"] += 1
        check = record.get("check")
        overall = check.get("overall") if isinstance(check, dict) else None
        if isinstance(check, dict) and isinstance(check.get("outputs"), Mapping):
            result["output_checks"] = dict(check["outputs"])
        if record["outcome"] == "complete" and overall == "pass":
            try:
                _publish(ctx, plan, value)
            except Exception as exc:  # noqa: BLE001 - never publish a partial answer
                report_internal("block outputs", exc)
                return _failed(result, "contract_error")
            result["accepted_attempt"] = record["id"]
            return _ok(result)
        if token.cancelled:                                         # inspected before any other decision
            return _stopped(ctx, result)
        outcome = record["outcome"]
        if outcome == "stopped":
            return _stopped(ctx, result)
        if outcome == "error":
            error = record.get("error") or {}
            return _failed(result, error["description"] if error.get("class") == "DestinationRefused" else "model_error")
        if outcome in model_step.BLANK_OUTCOMES:
            return _failed(result, "blank_answer")
        if overall == "incomplete":
            return _failed(result, "check_incomplete")
        if overall == "contract_error" or overall is None:
            return _failed(result, "contract_error")
        # overall is "fail" (complete or truncated), or "pass" on a truncated answer: a repair, while allowed
        if result["repairs_used"] >= step.max_repairs:
            return _failed(result, "truncated" if outcome == "truncated" else "repairs_exhausted")
        if tally.reached():
            return _failed(result, runs.BUDGET_EXCEEDED)
        parent = record


# --- tool steps (11.3) ----------------------------------------------------------------------------------------------------
def _error_fields(err: Any) -> dict:
    out = {"http_status": err.http_status, "jsonrpc_code": err.jsonrpc_code, "description": err.description}
    if err.server_text is not None:
        out["server_text"] = err.server_text
    return out


def _tool_operation(ctx: StepContext, p: PreparedTool, op: Any, committed: dict, started_at: str) -> dict:
    snap = p.snapshot
    era = ctx.client.era_of(snap.url) if ctx.client is not None else None
    with op.lock:
        cancel_status = op.cancel_status if op.ctx.abandoned is not None else None
    record = {"server": snap.server, "tool": p.tool, "arguments_text": p.arguments_text,
              "outcome": committed["outcome"], "reason": committed.get("reason"), "cancel_status": cancel_status,
              "era": era[0] if era else None, "protocol_version": era[1] if era else None, "result": None,
              "started_at": started_at, "ended_at": runs.utc_now()}
    if "error" in committed:
        record["reason"] = committed["error"].reason
        record.update(_error_fields(committed["error"]))
        return record
    value = committed["value"]
    record["era"], record["protocol_version"] = value.era, value.version
    result = value.result
    content = result.get("content") if isinstance(result.get("content"), list) else []
    texts = [item["text"] for item in content if item.get("type") == "text"]
    others = [{"type": item["type"]} for item in content if item.get("type") != "text"]
    structured = jsonio.encode_exact(result["structuredContent"]) if "structuredContent" in result else None
    record["result"] = {"text": "\n\n".join(texts), "structured_text": structured,
                        "is_error": value.outcome == "tool_error", "other_items": others}
    return record


def _tool_output(contract: checks.Contract, committed_value: Any) -> tuple[dict, object]:
    """(check result, value) for an `ok` call's result (R6-30)."""
    result = committed_value.result
    content = result.get("content") if isinstance(result.get("content"), list) else []
    texts = [item["text"] for item in content if item.get("type") == "text"]
    has_structured = "structuredContent" in result
    if contract.format == "json":
        if has_structured:
            return checks.check_value(contract, result["structuredContent"]), result["structuredContent"]
        return checks.check_answer_value(contract, "\n\n".join(texts))
    text = "\n\n".join(texts) if texts else (jsonio.encode_exact(result["structuredContent"]) if has_structured else "")
    return checks.check_value(contract, text), text


def _execute_tool(ctx: StepContext) -> dict:
    step, p, token = ctx.block.step, ctx.prepared, ctx.run.token
    contract = ctx.contracts[step.output]
    result = _base(ctx, "tool", p.fills)
    step_lock = threading.Lock()
    state: dict = {"op": None, "never": False}

    def stop_action() -> None:
        with step_lock:                                  # decide only
            op = state["op"]
            if op is None:
                state["never"] = True
        if op is None:
            p.reservation.release()                      # chain C4, outside the step lock
        else:
            op.abandon("stopped")                        # chain C5, outside the step lock

    token.on_cancel(stop_action)
    started_at = runs.utc_now()
    snap, client = p.snapshot, ctx.client
    def call(operation):
        if ctx.dispatch:
            operation.authorize = ctx.dispatch.check
        if ctx.run.source == "block" and ctx.attempt_gate is not None:
            operation.allow_call_retry = False
        return client.call(operation, snap, p.tool, p.arguments)

    with step_lock:
        if state["never"]:
            op = None
        else:
            try:
                op = ctx.ops.start("call", snap, call,
                                   reservation=p.reservation)          # chain C2 inside
            except mcp_ops.Closed:
                state["never"] = True
                op = None
            else:
                state["op"] = op
    if op is None:
        p.reservation.release()
        return _stopped(ctx, result)
    ctx.observer.operation_started("tool")
    committed = _wait_committed(op)
    operation = _tool_operation(ctx, p, op, committed, started_at)
    result["operation"] = operation
    if committed["outcome"] == "unknown":
        result["outcome"], result["failure"] = "unknown", "call_unknown"
        if token.cancelled:
            result["trigger"] = _stop_code(ctx)
        return result
    if op.ctx.abandoned == "stopped":                    # abandonment for cancellation won the gate
        return _stopped(ctx, result)
    outcome = committed["outcome"]
    accepted = False
    if outcome == "ok":
        check, value = _tool_output(contract.parsed, committed["value"])
        accepted = check["overall"] == "pass"
        if accepted:
            result["output_checks"] = {step.output: check}
            ctx.produced[step.output] = view_of(contract.parsed, value)
            return _ok(result)
        result["output_checks"] = {step.output: check}
    if token.cancelled:
        return _stopped(ctx, result)
    if outcome == "ok":
        return _failed(result, "output_failed")
    if outcome == "tool_error":
        return _failed(result, "tool_error")
    if outcome == "rejected":
        return _failed(result, "call_rejected")
    result["outcome"], result["failure"] = "unknown", "call_unknown"
    return result


def _wait_committed(op: Any) -> dict:
    """Wait for the operation's commit, ignoring progress; past the server's call deadline abandon it with
    "deadline", as the MCP tools tab does. Nothing is resent (P10)."""
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


# --- script steps (11.4) ------------------------------------------------------------------------------------------------------
def _output_checks(ctx: StepContext, values: Mapping) -> tuple[dict, bool]:
    results: dict = {}
    for declared in ctx.block.outputs:
        contract = ctx.contracts[declared.name].parsed
        value = values[declared.name]
        if contract.format == "text" and not isinstance(value, str):
            check = {"overall": "fail", "checks": [], "issue_count": 1, "notes": [],
                     "issues": [{"location": f"outputs.{declared.name}", "message": "a text output must be a string",
                                 "check": "parse"}]}
        else:
            check = checks.check_value(contract, value)
        results[declared.name] = check
    return results, all(c["overall"] == "pass" for c in results.values())


def _execute_script(ctx: StepContext) -> dict:
    p, token = ctx.prepared, ctx.run.token
    result = _base(ctx, "script", [])
    outputs = tuple(d.name for d in ctx.block.outputs)
    done = script_step.run(p.path, ctx.values, step_id=ctx.run.step_id, run_folder=p.run_folder,
                           timeout_s=p.timeout_s, token=token, run_id=ctx.run.run_id,
                           block_name=ctx.block.name, sha256=p.sha256, outputs=outputs, file=p.file,
                           env=ctx.script_env, handle=ctx.script_handle)
    operation = done["operation"]
    result["operation"] = operation
    if done["never"] or done["stopped"]:
        return _stopped(ctx, result)
    if operation["outcome"] == "ok":
        checked, accepted = _output_checks(ctx, done["values"])
        result["output_checks"] = checked
        if accepted:
            for declared in ctx.block.outputs:
                ctx.produced[declared.name] = view_of(ctx.contracts[declared.name].parsed, done["values"][declared.name])
            return _ok(result)
        if token.cancelled:
            return _stopped(ctx, result)
        return _failed(result, "output_failed")
    if token.cancelled:
        return _stopped(ctx, result)
    if operation["outcome"] == "timeout":
        return _failed(result, "timeout")
    return _failed(result, "script_error")


def _execute_transform(ctx: StepContext) -> dict:
    step = ctx.block.step
    result = _base(ctx, "transform", [])
    result["operation"] = {"name": step.raw["operation"], "version": step.raw["operation_version"]}
    if ctx.run.token.cancelled:
        return _stopped(ctx, result)
    try:
        value, details = transforms.execute(step.raw["operation"], step.raw["parameters"], ctx.values)
    except templates.FillError as exc:
        result["issues"] = [{"location": exc.location, "message": exc.message}]
        return _failed(result, "transform_error")
    checked, accepted = _output_checks(ctx, {step.output: value})
    result["output_checks"] = checked
    result["fills"] = details
    if ctx.run.token.cancelled:
        return _stopped(ctx, result)
    if not accepted:
        return _failed(result, "output_failed")
    ctx.produced[step.output] = view_of(ctx.contracts[step.output].parsed, value)
    return _ok(result)


def execute_step(ctx: StepContext) -> dict:
    """Run the block's one step and return its step result (11.1). The accepted output values, as value views,
    are left in `ctx.produced`."""
    kind = ctx.block.step.kind
    if kind == "model":
        return _execute_model(ctx)
    if kind == "tool":
        return _execute_tool(ctx)
    if kind == "transform":
        return _execute_transform(ctx)
    return _execute_script(ctx)
