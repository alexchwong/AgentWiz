"""Block runs end to end (0.1.4, Section 10): input checks, admission, preparation, execution and the one finaliser.

`run_block` runs in the caller's thread (the HTTP handler's, or a test's) and never touches a socket: it reports
through an observer whose failures are ignored (10.9). Every run, rejected or executing, ends through `_finalise`,
which reaches the end point, decides the outcome (10.6), commits, releases what the run still owns, and hands the
result to delivery and saving.

Messages name rules and locations, never values.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Mapping

import modelspell

from . import (__version__, checks, executor, history, insertion, jsonio, model_call, model_step,
               output_protocol, runs, script_step, templates, task_settings)
from .errors import ShuttingDown, report_internal
from .state import NoSuchServer, RoleUnresolved
from .task_settings import TaskSettingError

RECORD_VERSION = 1


@dataclass
class FrozenSource:
    snapshot: Any
    save_runs: bool
    provenance: dict
    owner: Any = None
    capture: Any = None          # state.RoleCapture: the destination and the role's defaults, taken together



# --- hooks tests replace ----------------------------------------------------------------------------------------
def make_timer(seconds: float, callback) -> threading.Timer:
    """A started daemon timer. Tests replace this to fire timers on demand (Section 20.1)."""
    timer = threading.Timer(seconds, callback)
    timer.daemon = True
    timer.start()
    return timer


def before_run_end() -> None:
    """Called by the finaliser before it reaches the run's end point; does nothing in shipped code. Tests use it to
    place a Stop, or a timer, exactly before the end point."""


# --- what the caller supplies and receives -----------------------------------------------------------------------
class Busy(Exception):
    """A process budget is full: nothing was created or started. `budget` names it."""

    def __init__(self, budget: str, limit: int):
        super().__init__(f"all {limit} {budget} are in use; wait for one to finish")
        self.budget = budget
        self.limit = limit


class Observer:
    """What a run reports to. Every method may fail; the runner ignores every failure. The default does nothing,
    which is what a headless caller passes."""

    def started(self, event: dict) -> None: ...
    def attempt_committed(self, record: dict) -> None: ...
    def operation_started(self, kind: str) -> None: ...
    def final(self, event: dict) -> None: ...
    def saved(self, event: dict) -> None: ...


class _Safe:
    """Wraps an observer so that no failure of it reaches the run."""

    def __init__(self, observer: Observer | None):
        self._observer = observer if observer is not None else Observer()

    def __getattr__(self, name: str):
        method = getattr(self._observer, name)

        def call(*args: Any) -> None:
            try:
                method(*args)
            except Exception:  # noqa: BLE001 - delivery never affects the run (10.9)
                pass
        return call


@dataclass
class Finished:
    """What `run_block` returns once the run has ended and its result is committed."""
    run: runs.Run
    result: dict
    save_status: str                       # "pending" -> settled below; "not_requested" | "saved" | "failed"
    save_detail: dict = field(default_factory=dict)


# --- input checks (9.1, 9.2) -----------------------------------------------------------------------------------------
def _prefixed(name: str, check: dict) -> list[dict]:
    out = []
    for issue in check.get("issues", []):
        location = issue.get("location", "")
        if location in ("", "answer"):
            where = f"inputs.{name}"
        elif location.startswith("$"):
            where = f"inputs.{name}{location[1:]}"
        else:
            where = f"inputs.{name}: {location}"
        out.append({**issue, "location": where})
    return out


def _internal_check(message: str) -> dict:
    return {"overall": "contract_error", "checks": [], "issue_count": 1, "notes": [],
            "issues": [{"location": "answer", "message": message, "check": "checks"}]}


@dataclass
class InputChecks:
    values: dict            # parsed values of the inputs that parsed
    views: dict             # value views of the inputs that parsed
    results: dict           # check result by input name
    issues: list            # located issues of the inputs that did not pass
    passed: bool


def check_inputs(block: Any, input_texts: Mapping[str, str]) -> InputChecks:
    """Parse and check every input in declaration order, even after a failure (9.2). No lock is held."""
    values: dict = {}
    views: dict = {}
    results: dict = {}
    issues: list = []
    for declared in block.inputs:
        name = declared.name
        contract = block.contracts["inputs"][name].parsed
        text = input_texts[name]
        value: Any = checks.NO_VALUE
        try:
            if contract.format == "text":
                value, check = text, checks.check_value(contract, text)
            else:
                try:
                    value = jsonio.decode_exact(text)
                except (jsonio.JSONSyntax, jsonio.ExactLimit) as exc:
                    value, check = checks.NO_VALUE, checks.check_unparsed_input(contract, text, exc)
                else:
                    check = checks.check_value(contract, value)
        except Exception as exc:  # noqa: BLE001 - never read as a pass
            report_internal("block input checks", exc)
            check = _internal_check("the checks failed internally")
        results[name] = check
        if value is not checks.NO_VALUE:
            values[name] = value
            views[name] = executor.view_of(contract, value)
        if check["overall"] != "pass":
            issues.extend(_prefixed(name, check))
            if not check.get("issues"):
                issues.append({"location": f"inputs.{name}",
                               "message": f"the input's checks ended {check['overall']}", "check": "checks"})
    return InputChecks(values, views, results, issues, all(c["overall"] == "pass" for c in results.values()))


# --- the run's slots ---------------------------------------------------------------------------------------------------
class _Slot:
    """A process-budget slot the run owns, released exactly once."""

    def __init__(self, release):
        self._release = release
        self._lock = threading.Lock()
        self.released = False

    def release(self) -> None:
        with self._lock:
            if self.released:
                return
            self.released = True
        self._release()


def _admit(state: Any, kind: str):
    """Reserve the step kind's slot with a non-blocking acquire (10.3). Returns (slot, reservation). Raises Busy
    or ShuttingDown; nothing is taken then."""
    if kind == "model":
        budget, name = state.budget, "model requests"
    elif kind == "script":
        budget, name = state.scripts, "script processes"
    else:
        reservation = state.mcp_ops.reserve()                      # raises mcp_ops.Closed (a ShuttingDown)
        if reservation is None:
            raise Busy("MCP workers", 8)
        return _Slot(lambda: None), reservation
    if not budget.try_acquire():
        if budget.closed:
            raise ShuttingDown()
        raise Busy(name, budget.limit)
    return _Slot(budget.release), None


# --- preparation (10.5) --------------------------------------------------------------------------------------------------
@dataclass
class _Preparation:
    status: str                              # "prepared" | "rejected" | "cancelled"
    prepared: Any = None
    issues: list = field(default_factory=list)


def _rejected(location: str, message: str) -> _Preparation:
    return _Preparation("rejected", issues=[{"location": location, "message": message}])


@dataclass(frozen=True)
class ModelMessages:
    """One model block's prepared prompt: the author's system text, the generated output section, the
    filled user prompt, the messages actually sent, and the effective settings with their sources.

    Preview and dispatch both build this, so what a preview shows is what a run sends.
    """

    system_author: str
    system_sent: str
    generated: str
    prompt: str
    fills: list
    effective: Any
    plan: Any


def model_messages(block: Any, values: Mapping, defaults: Mapping) -> ModelMessages:
    """Fill a model block's prompts and resolve its settings. Raises `templates.FillError` or
    `TaskSettingError`; the caller reports them as located issues."""
    step = block.step
    effective = task_settings.resolve_effective(step.task, step.task_resets, defaults)
    plan = model_step.OutputPlan.of(block.outputs, block.contracts["outputs"])
    descriptions = {spec.name: output_protocol.describe(spec) for spec in plan.specs()}
    system, system_fills = templates.fill_model_text(step.system or "", "system", values, descriptions)
    prompt, prompt_fills = templates.fill_model_text(step.prompt or "", "prompt", values, descriptions)
    generated = output_protocol.instructions(plan.specs())
    system_sent = f"{system}\n\n{generated}" if system else generated
    if len(jsonio.encode({"messages": model_call.build_messages(system_sent, prompt)})) > insertion.REQUEST_LIMIT:
        raise templates.FillError("prompt", "the prepared model request exceeds the request size limit")
    return ModelMessages(system_author=system, generated=generated, prompt=prompt,
                         system_sent=system_sent,
                         fills=system_fills + prompt_fills, effective=effective, plan=plan)


def _prepare_model(state: Any, block: Any, values: Mapping) -> _Preparation:
    step = block.step
    try:
        capture = state.role_capture(step.role)
    except RoleUnresolved as exc:
        return _rejected("steps[0].role", str(exc))
    try:
        prepared_messages = model_messages(block, values, capture.defaults)
    except templates.FillError as exc:
        return _rejected("steps[0]." + exc.location, exc.message)
    except TaskSettingError as exc:
        return _rejected("steps[0].task", str(exc))
    messages = model_call.build_messages(prepared_messages.system_sent, prepared_messages.prompt)
    if len(jsonio.encode({"messages": messages})) > insertion.REQUEST_LIMIT:
        return _rejected("steps[0].prompt", "the filled request is larger than 32 MiB")
    try:
        arguments = model_call.task_arguments(prepared_messages.effective.task, capture.snapshot.kind,
                                              capture.snapshot.provider)
    except TaskSettingError as exc:
        return _rejected("steps[0].task", str(exc))
    prepared = executor.PreparedModel(snapshot=capture.snapshot, task=prepared_messages.effective.task,
                                      arguments=arguments, messages=messages, fills=prepared_messages.fills,
                                      role=step.role, settings=prepared_messages.effective.sources)
    return _Preparation("prepared", prepared)


def _prepare_tool(state: Any, block: Any, values: Mapping, reservation: Any) -> _Preparation:
    step = block.step
    try:
        snapshot = state.mcp_snapshot_for_block(step.server)
    except NoSuchServer:
        return _rejected("steps[0].server", "the MCP server is not in the library")
    try:
        arguments, fills = templates.fill_arguments(step.arguments, values)
    except templates.FillError as exc:
        return _rejected("steps[0]." + exc.location, exc.message)
    if not isinstance(arguments, dict):
        return _rejected("steps[0].arguments", "the filled arguments are not an object")
    prepared = executor.PreparedTool(snapshot=snapshot, tool=step.tool, arguments=arguments,
                                     arguments_text=jsonio.encode_exact(arguments), fills=fills,
                                     reservation=reservation)
    return _Preparation("prepared", prepared)


def _prepare_script(block: Any, run: runs.Run) -> _Preparation:
    step = block.step
    sha = block.files.get(step.script_file)
    if sha is None:
        return _rejected("steps[0].file", "the script file is not part of the block")
    folder = run.folder if run.save_runs else None
    return _Preparation("prepared", executor.PreparedScript(path=step.script_path, sha256=sha, file=step.script_file,
                                                            timeout_s=step.timeout_s, run_folder=folder))


# --- outcome (10.6) --------------------------------------------------------------------------------------------------------
def decide_outcome(inputs_passed: bool, preparation: _Preparation | None, step: dict | None,
                   end: runs.EndPoint) -> str:
    """The first rule of 10.6's table that applies."""
    if not inputs_passed or (preparation is not None and preparation.status == "rejected"):
        return "rejected"
    reason = end.reason or runs.USER_STOP
    if preparation is not None and preparation.status == "cancelled":
        return "budget_exceeded" if reason == runs.BUDGET_EXCEEDED else "stopped"
    if step is None:
        return "failed"
    if step["outcome"] == "stopped":
        return "budget_exceeded" if reason == runs.BUDGET_EXCEEDED else "stopped"
    if step["outcome"] == "failed" and step["failure"] == runs.BUDGET_EXCEEDED:
        return "budget_exceeded"
    if step["outcome"] == "unknown":
        return "unknown"
    if step["outcome"] == "ok":
        return "complete"
    return "failed"


# --- the run -------------------------------------------------------------------------------------------------------------------
class _Execution:
    """Everything a block run owns, so the finaliser can release it on every path."""

    def __init__(self, state: Any, loaded: Any, input_texts: Mapping[str, str], checked: InputChecks,
                 observer: _Safe, writer: Any):
        self.frozen = None
        self.state = state
        self.loaded = loaded
        self.block = loaded.block
        self.input_texts = dict(input_texts)
        self.checked = checked
        self.observer = observer
        self.writer = writer
        self.run: runs.Run | None = None
        self.slot: _Slot | None = None
        self.reservation: Any = None
        self.timer: Any = None
        self.handle = script_step.Handle()
        self.preparation: _Preparation | None = None
        self.step: dict | None = None
        self.produced: dict = {}
        self.tally = executor.Tally(loaded.block.budget.max_model_tokens)
        self.error: BaseException | None = None


def _contracts_view(block: Any) -> dict:
    return {"inputs": {n: c.view() for n, c in block.contracts["inputs"].items()},
            "outputs": {n: c.view() for n, c in block.contracts["outputs"].items()}}


def _create_run(ex: _Execution, executing: bool) -> runs.Run:
    state, block = ex.state, ex.block
    if executing:
        ex.slot, ex.reservation = _admit(state, block.step.kind)
    try:
        run = state.store.new_block_run(
            save_runs=ex.frozen.save_runs if ex.frozen else state.save_runs_now(),
            block={"name": block.name, "revision": ex.loaded.revision, "files": dict(block.files)},
            inputs=ex.checked.views, input_texts=ex.input_texts, contracts=_contracts_view(block),
            budget=block.budget.view(), step_id=block.step.id, slot=ex.slot)
    except BaseException:
        _release_slots(ex)
        raise
    ex.run = run
    return run


def _release_slots(ex: _Execution) -> None:
    if ex.slot is not None and not ex.handle.retained:
        ex.slot.release()
    if ex.reservation is not None:
        ex.reservation.release()


def _expire(store: runs.AttemptStore, run_id: str) -> None:
    try:
        store.stop_run(run_id, runs.BUDGET_EXCEEDED)
    except runs.NoSuchRunInMemory:
        pass


def _step_for_error(ex: _Execution) -> dict:
    step = executor.blank_step(ex.block.step.id, ex.block.step.kind)
    step["failure"] = "internal_error"
    return step


def _build_result(ex: _Execution, outcome: str, end: runs.EndPoint) -> dict:
    run, block = ex.run, ex.block
    token = run.token
    cancelled_before = end.cancelled
    outputs: dict = {}
    if outcome == "complete":
        outputs = {declared.name: ex.produced[declared.name] for declared in block.outputs}
    issues = []
    if outcome == "rejected":
        issues = list(ex.checked.issues) if not ex.checked.passed else list(ex.preparation.issues)
    return {"record_version": RECORD_VERSION, "run_id": run.run_id, "source": "block", "block": run.block,
            "started_at": run.started_at, "ended_at": runs.utc_now(), "outcome": outcome,
            "stop_requested_at": end.stop_requested_at,
            "cancel_reason": end.reason if cancelled_before else None,
            "cancelled_at": token.cancelled_at if cancelled_before else None,
            "input_texts": run.input_texts, "inputs": run.inputs, "contracts": run.contracts,
            "input_checks": ex.checked.results, "outputs": outputs, "issues": issues,
            "steps": [ex.step] if ex.step is not None else [],
            "budget": run.budget, "usage": ex.tally.view(),
            "workshop": ex.frozen.provenance if ex.frozen else {},
            "agentwiz_version": __version__, "modelspell_version": modelspell.__version__}


def _finalise(ex: _Execution) -> Finished:
    """The one finaliser (10.6). Reaches the end point, decides, commits, releases, delivers, saves."""
    state, run, store = ex.state, ex.run, ex.state.store
    saved_status, detail = "not_requested", {}
    try:
        try:
            before_run_end()
        except Exception as exc:  # noqa: BLE001
            report_internal("block run", exc)
        if ex.error is not None and ex.step is None:
            ex.step = _step_for_error(ex)
        end = store.block_end_point(run)
        outcome = decide_outcome(ex.checked.passed, ex.preparation, ex.step, end)
        if ex.error is not None and outcome not in ("rejected", "stopped", "budget_exceeded"):
            outcome = "failed"
        result = _build_result(ex, outcome, end)
        if ex.timer is not None:
            try:
                ex.timer.cancel()
            except Exception as exc:  # noqa: BLE001
                report_internal("block run", exc)
        status = store.end_block_run(run, result, bool(run.save_runs))
        saved_status = status
        _release_slots(ex)
        ex.observer.final({"type": "final", "run_id": run.run_id, "outcome": outcome, "result": result,
                           "save_status": status})
        if status == "pending":
            try:
                saved = state.history.write_result(run, result)
            except Exception as exc:  # noqa: BLE001 - settle exactly once, whatever happened
                report_internal("history", exc)
                saved = {"save_status": "failed", "reason": "the result file could not be written (internal error)"}
            saved_status = saved["save_status"]
            detail = {k: v for k, v in saved.items() if k != "save_status"}
            store.settle_result_save(run, saved_status, **detail)
            ex.observer.saved({"type": "save", "run_id": run.run_id, "save_status": saved_status, **detail})
        return Finished(run, result, saved_status, detail)
    finally:
        _release_slots(ex)                       # idempotent: a path that failed before the commit still frees them
        if ex.timer is not None:
            try:
                ex.timer.cancel()
            except Exception:  # noqa: BLE001
                pass
        ex.writer.release()


def _execute(ex: _Execution) -> None:
    """Preparation and the executor. Raises nothing but BaseException that must propagate; the caller catches."""
    state, block, run = ex.state, ex.block, ex.run
    values = ex.checked.values
    kind = block.step.kind
    if ex.loaded.workshop.get('assets') and ex.frozen is None:
        from .workshop_assets import validate_generation
        root = ex.loaded.physical_root
        validate_generation(root, ex.loaded.workshop)
    if ex.loaded.workshop.get('origin') in ('llm_generated', 'derived_from_llm'):
        from .workshop_assets import read, fingerprint
        source = {p: read(ex.loaded.physical_root, p) for p in [block.step.script_file, *ex.loaded.workshop.get('support', [])]} if kind == 'script' else {}
        if kind == 'script' and ex.loaded.workshop.get('source_review') != fingerprint(source):
            ex.preparation = _rejected('source', 'generated source review is required')
            return
    if ex.frozen is not None and kind == 'model':
        if ex.frozen.capture is None:
            ex.preparation = _rejected('steps[0].role', 'the captured role defaults are missing')
            return

        class CapturedState:
            def role_capture(self, role):
                return ex.frozen.capture
        ex.preparation = _prepare_model(CapturedState(), block, values)
    elif ex.frozen is not None and kind == 'tool':
        current = state.mcp_snapshot_for_block(block.step.server)
        if current != ex.frozen.snapshot:
            ex.preparation = _rejected('server', 'captured server configuration changed')
        else:
            class CapturedState:
                def mcp_snapshot_for_block(self, server):
                    return ex.frozen.snapshot
            ex.preparation = _prepare_tool(CapturedState(), block, values, ex.reservation)
    elif kind == "model":
        ex.preparation = _prepare_model(state, block, values)
    elif kind == "tool":
        ex.preparation = _prepare_tool(state, block, values, ex.reservation)
    else:
        ex.preparation = _prepare_script(block, run)
    if ex.preparation.status == "prepared" and run.token.cancelled:
        ex.preparation = _Preparation("cancelled")
    if ex.preparation.status != "prepared":
        return
    sink = state.history if run.save_runs else None
    script_env = None
    if kind == "script":
        script_env = script_step.Env(
            make_timer=lambda seconds, callback: make_timer(seconds, callback),
            registry=state.cleanups, writer=ex.writer, release_slot=ex.slot.release,
            protect=lambda: state.store.protect_run(run), unprotect=lambda: state.store.unprotect_run(run),
            on_started=lambda: ex.observer.operation_started("script"),
            capture_owner=ex.frozen.owner if ex.frozen else ex.loaded,
            no_bytecode=bool(ex.frozen or ex.loaded.workshop))
    ctx = executor.StepContext(
        run=run, block=block, values=values, prepared=ex.preparation.prepared,
        contracts=block.contracts["outputs"], store=state.store, sink=sink, ops=state.mcp_ops,
        observer=ex.observer, client=state.mcp_client, key_for=(lambda kind, url: ex.frozen.snapshot.endpoint.api_key) if ex.frozen and kind == 'model' else state.key_for, script_env=script_env,
        script_handle=ex.handle, tally=ex.tally, produced=ex.produced)
    ex.step = executor.execute_step(ctx)


def run_block(state: Any, loaded: Any, input_texts: Mapping[str, str], observer: Observer | None = None, *, frozen: FrozenSource | None = None) -> Finished:
    """Run a loaded, usable block with the submitted input texts (one per declared input; the caller has checked
    the shape). Raises `Busy` or `ShuttingDown` before any run exists; otherwise returns the ended run."""
    block = loaded.block
    checks.require_validation(copy.parsed for ports in block.contracts.values() for copy in ports.values())
    safe = _Safe(observer)
    checked = check_inputs(block, input_texts)                       # no slot, no lock (BD-03)
    writer = state.register_writer(f"block run {block.name}")        # refused once shutdown has begun
    ex = _Execution(state, loaded, input_texts, checked, safe, writer)
    ex.frozen = frozen
    try:
        run = _create_run(ex, executing=checked.passed)
    except BaseException:
        writer.release()
        raise
    # From here the run exists and ends through _finalise, which releases the writer.
    try:
        if run.save_runs:
            state.history.start_run(run)
        if checked.passed:
            ex.timer = make_timer(block.budget.max_seconds, lambda: _expire(state.store, run.run_id))
        ex.observer.started({"type": "started", "run_id": run.run_id, "block": block.name,
                             "revision": loaded.revision, "step_id": block.step.id, "kind": block.step.kind})
        if checked.passed:
            _execute(ex)
    except BaseException as exc:  # noqa: BLE001 - committed as failed/internal_error by the finaliser
        ex.error = exc
        if isinstance(exc, Exception):
            report_internal("block run", exc)
    finished = _finalise(ex)
    if ex.error is not None and not isinstance(ex.error, Exception):
        raise ex.error
    return finished
