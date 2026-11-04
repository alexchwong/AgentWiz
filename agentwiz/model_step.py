"""The headless model-step function (techspec 0.1.3, 8.4, 8.5, 9.2; R4-9, R4-17, R4-21, R4-24).

`run_attempt` runs one model attempt from creation to commit. It knows nothing of HTTP: the page
handlers call it with callbacks that write events, and 0.1.4's step executor will call it with none.

The attempt reaches its end point and its commit exactly once, whatever is raised: everything between
creating the record and the commit is inside one `try`, and the end point, the checks and the commit
follow it unconditionally. The request budget is not this module's business.
"""
from __future__ import annotations

import modelspell
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Callable

from modelspell import Endpoint, ModelSpellError

from .errors import report_internal
from . import __version__, checks, history, jsonio, model_call, output_protocol, runs
from .model_call import RequestSnapshot, build_messages, classify_outcome, describe_error, result_to_dict
from .task_settings import TaskSettings, parse_task

BLANK_OUTCOMES = ("empty", "reasoning_only", "truncated_no_answer")
RECORD_VERSION = 1


def before_end_point() -> None:
    """A hook that does nothing in shipped code. Tests make it block, to put a Stop between modelSpell's
    return and the end point (techspec 8.4, step 5)."""


class PageGone(Exception):
    """Raised by a callback when its page can no longer be written to."""


@dataclass(frozen=True)
class ContractCopy:
    """A contract as an attempt uses it: the record's `{"name", "revision", "text"}` and the parsed contract."""
    name: str
    revision: int
    text: str
    parsed: checks.Contract

    @classmethod
    def of(cls, name: str, revision: int, parsed: checks.Contract) -> "ContractCopy":
        return cls(name, revision, jsonio.encode_exact(dict(parsed.raw), indent=2), parsed)

    @classmethod
    def from_record(cls, saved: dict) -> "ContractCopy":
        return cls(saved["name"], saved["revision"], saved["text"], checks.contract_from_text(saved["text"]))

    def view(self) -> dict:
        return {"name": self.name, "revision": self.revision, "text": self.text}


@dataclass(frozen=True)
class OutputPlan:
    """A model block's captured output plan: every declared output with its description and contract.

    Repairs rebuild a plan from the parent's record, never from the block as it is now, so a repair
    checks the answer against what the reviewed run promised.
    """

    outputs: tuple[tuple[str, str, ContractCopy], ...]      # (name, description, captured contract)

    @classmethod
    def of(cls, declared: Iterable, captured: Mapping) -> "OutputPlan":
        """The plan of a loaded block: declaration order, with its captured output contracts."""
        return cls(tuple((item.name, item.description, captured[item.name]) for item in declared))

    def names(self) -> tuple[str, ...]:
        return tuple(name for name, _description, _contract in self.outputs)

    def contracts(self) -> dict[str, checks.Contract]:
        return {name: contract.parsed for name, _description, contract in self.outputs}

    def specs(self) -> tuple[output_protocol.Spec, ...]:
        return tuple(output_protocol.Spec(name, description, contract.parsed)
                     for name, description, contract in self.outputs)

    def view(self) -> dict:
        """The record form: names, descriptions and exact contract text, and nothing about the run."""
        return {"mode": output_protocol.MODE, "renderer": output_protocol.RENDERER_VERSION,
                "digest": self.digest(),
                "outputs": [{"name": name, "description": description, "revision": contract.revision,
                             "text": contract.text} for name, description, contract in self.outputs]}

    @classmethod
    def from_record(cls, saved: dict) -> "OutputPlan":
        return cls(tuple((item["name"], item.get("description", ""),
                          ContractCopy.from_record(item)) for item in saved["outputs"]))

    def digest(self) -> str:
        return output_protocol.digest(self.specs())


@dataclass(frozen=True)
class PreparedAttempt:
    run: runs.Run
    number: int                      # reserved in the store
    parent: dict | None              # the parent's committed record, for a repair
    snapshot: RequestSnapshot
    task: TaskSettings
    arguments: dict
    input_messages: list
    messages_sent: list
    contract: ContractCopy | None = None
    plan: OutputPlan | None = None   # a block's model step: its captured named envelope
    # 0.1.4: defaults reproduce 0.1.3's page records
    step_id: str = runs.STEP_ID
    role: str | None = None                  # a block's model step: the role name
    fills: tuple = ()                        # a block's fills with provenance (first attempt only)
    repair_reason: str | None = None         # "check_failed" or "truncated" for a repair, else None
    settings_sources: Mapping | None = None


@dataclass(frozen=True)
class Callbacks:
    started: Callable[[dict], None] | None = None          # identity fields; may raise PageGone
    text: Callable[[str], None] | None = None               # may raise PageGone
    reasoning: Callable[[str], None] | None = None          # may raise PageGone
    committed: Callable[[dict, str], None] | None = None    # (record, save_status); failures are ignored


# --- preparing ------------------------------------------------------------------------------------------
def prepare_first(store: runs.AttemptStore, *, snapshot: RequestSnapshot, system: str, prompt: str,
                  task: TaskSettings, source: str, save_runs: bool, contract: ContractCopy | None = None,
                  arguments: dict | None = None) -> PreparedAttempt:
    """A new run with attempt 1 reserved. The caller must run it, or `store.abandon_run` it."""
    if arguments is None:
        arguments = model_call.task_arguments(task, snapshot.kind, snapshot.provider)
    messages = build_messages(system, prompt)
    run = store.new_run(source=source, save_runs=save_runs, system=system, prompt=prompt,
                        contract=({"name": contract.name, "revision": contract.revision} if contract else None))
    return PreparedAttempt(run, 1, None, snapshot, task, arguments, messages, list(messages), contract)


def repair_plan(record: dict) -> dict:
    """What a repair of this committed record would send (9.2, 9.4; 0.1.4, 12.1): used by the preview, by the
    manual send and by automatic repair, so they cannot differ. An attempt that ended `truncated` gets the
    truncation message and `repair_reason` "truncated"; any other gets "check_failed". A block's named
    envelope repairs against its captured plan; a page attempt repairs against its captured contract."""
    plan = OutputPlan.from_record(record["output_plan"]) if record.get("output_plan") else None
    contract = ContractCopy.from_record(record["contract"]) if record.get("contract") else None
    truncated = record["outcome"] == "truncated"
    text = checks.repair_message(None if plan is not None else contract.parsed, record["check"],
                                 truncated=truncated,
                                 instruction=checks.ENVELOPE_REPLY if plan is not None else None)
    messages = [*record["input_messages"], {"role": "assistant", "content": record["result"]["text"]},
                {"role": "user", "content": text}]
    return {"messages": messages, "repair_message": text, "contract": contract, "plan": plan,
            "repair_reason": "truncated" if truncated else "check_failed",
            "settings": {"kind": record["endpoint"]["kind"], "base_url": record["endpoint"]["base_url"],
                         "model": record["model"], "provider": record["provider"], "task": record["task"]}}


def prepare_repair(run: runs.Run, number: int, parent: dict,
                   key_for: Callable[[str, str], str | None]) -> PreparedAttempt:
    """The next attempt of the parent's step, from the parent's captured settings (9.2). The key is resolved
    now, for the captured endpoint. No setting is changed for a repair."""
    plan = repair_plan(parent)
    s = plan["settings"]
    key = key_for(s["kind"], s["base_url"])
    snapshot = RequestSnapshot(endpoint=Endpoint(s["kind"], s["base_url"], api_key=key), model=s["model"],
                               kind=s["kind"], provider=s["provider"],
                               profile_name=parent["profile"]["name"], profile_modified=parent["profile"]["modified"])
    task = parse_task(s["task"])
    arguments = model_call.task_arguments(task, snapshot.kind, snapshot.provider)
    return PreparedAttempt(run, number, parent, snapshot, task, arguments, list(parent["input_messages"]),
                           plan["messages"], plan["contract"], plan["plan"], step_id=parent["step_id"],
                           role=parent.get("role"), fills=(), repair_reason=plan["repair_reason"],
                           settings_sources=parent.get("settings_sources"))


def prepare_block_first(run: runs.Run, number: int, *, snapshot: RequestSnapshot, task: TaskSettings,
                        arguments: dict, messages: list, plan: OutputPlan, role: str,
                        fills: list, settings_sources: Mapping | None = None) -> PreparedAttempt:
    """The first attempt of a block's model step (11.2): the run's step ID, the role's snapshot, the
    resolved task settings mapped for the role's backend, the filled messages as both `input_messages` and
    `messages_sent`, and the block's captured named-envelope plan. The number was reserved by
    `AttemptStore.reserve_attempt`."""
    return PreparedAttempt(run, number, None, snapshot, task, arguments, list(messages), list(messages),
                           plan=plan, step_id=run.step_id, role=role, fills=tuple(fills), repair_reason=None,
                           settings_sources=settings_sources)


# --- the record ---------------------------------------------------------------------------------------------
def _new_record(p: PreparedAttempt, aid: str) -> dict:
    run = p.run
    return {"record_version": RECORD_VERSION, "id": aid, "run_id": run.run_id, "step_id": p.step_id,
            "attempt": p.number, "parent_id": p.parent["id"] if p.parent else None, "source": run.source,
            "accepted_at": runs.utc_now(),
            "profile": {"name": p.snapshot.profile_name, "modified": p.snapshot.profile_modified},
            "endpoint": {"kind": p.snapshot.kind, "base_url": p.snapshot.endpoint.base_url},
            "model": p.snapshot.model, "provider": p.snapshot.provider,
            "task": p.task.request_shape(),
            "settings_sources": dict(p.settings_sources) if p.settings_sources is not None else None,
            "input_messages": p.input_messages, "messages_sent": p.messages_sent,
            "contract": p.contract.view() if p.contract else None,
            "output_plan": p.plan.view() if p.plan else None,
            "role": p.role, "fills": [dict(f) for f in p.fills], "repair_reason": p.repair_reason,
            "save_run": run.save_runs, "agentwiz_version": __version__,
            "modelspell_version": modelspell.__version__}


def _error_fields(exc: BaseException, p: PreparedAttempt) -> dict:
    """{"class", "status", "description", "hint"?}: AgentWiz's own text, never backend text."""
    if isinstance(exc, ModelSpellError):
        status = getattr(exc, "status", None)
        status = status if isinstance(status, int) and not isinstance(status, bool) else None
        text = describe_error(exc, p.snapshot.endpoint.base_url)
        out = {"class": type(exc).__name__, "status": status, "description": text}
        if p.snapshot.kind == "openrouter" and isinstance(exc, modelspell.BackendError) and status == 404:
            hint = model_call.OPENROUTER_HINT
            if (p.arguments.get("provider") or {}).get("require_parameters"):
                hint += ", and whether any provider supports every task setting sent"
            out["description"] = f"{text} {hint}."
            out["hint"] = "openrouter_no_provider"
        return out
    report_internal("/api/complete", exc)
    return {"class": "InternalError", "status": None, "description": f"InternalError: {type(exc).__name__}"}


# --- run_attempt -------------------------------------------------------------------------------------------
def _failed_checks() -> dict:
    """A check result for an internal failure: never read as a pass."""
    return {"overall": "contract_error", "checks": [],
            "issues": [{"location": "answer", "message": "the checks failed internally",
                        "check": "checks"}], "issue_count": 1, "notes": []}


def run_attempt(store: runs.AttemptStore, prepared: PreparedAttempt, callbacks: Callbacks = Callbacks(),
                sink: history.Writer | None = None) -> dict:
    """Section 8.4. Returns the committed record. The 0.1.3 entry point, now a wrapper (0.1.4, 9.3)."""
    return run_attempt_with_value(store, prepared, callbacks, sink)[0]


def run_attempt_with_value(store: runs.AttemptStore, prepared: PreparedAttempt, callbacks: Callbacks = Callbacks(),
                           sink: history.Writer | None = None) -> tuple[dict, object]:
    """Section 8.4, returning (the committed record, the checked value). For a page attempt the value is what
    the one check that decided the attempt parsed (`checks.check_answer_value`); for a block's named envelope
    it is `{name: value}` for every declared output (`checks.check_named_answer`). Both are
    `checks.NO_VALUE` unless the check ran. A value never enters the record, so records stay plain JSON; a
    caller uses it only for an attempt that ended `complete` with check `pass` (0.1.4, 9.3, 11.2)."""
    run, p = prepared.run, prepared
    aid = runs.attempt_id(run.run_id, p.number)
    base = _new_record(p, aid)
    store.start(run, p.number, base)

    page_gone = False
    model_called = False
    lookup: str | None = None
    started_failed = False
    result = None
    cancelled_exc: modelspell.Cancelled | None = None
    failure: BaseException | None = None
    crash: BaseException | None = None

    def wrap(callback: Callable[[str], None] | None) -> Callable[[str], None]:
        def inner(text: str) -> None:
            nonlocal page_gone
            if callback is None:
                return
            try:
                callback(text)
            except PageGone:
                page_gone = True
                raise modelspell.Cancelled("page disconnected") from None   # modelSpell closes upstream (MS-5)
        return inner

    try:
        try:
            if callbacks.started is not None:
                callbacks.started({"run_id": run.run_id, "step_id": p.step_id, "attempt": p.number,
                                   "attempt_id": aid})
        except Exception as exc:  # noqa: BLE001 - PageGone, or a write that failed or timed out
            if not isinstance(exc, PageGone):
                report_internal("/api/complete", exc)
            started_failed = True
        if not started_failed:
            try:
                model_info = None
                if p.snapshot.kind == "lmstudio" and p.task.reasoning == "off":
                    model_info, lookup = model_call.lmstudio_model_info(p.snapshot.endpoint, p.snapshot.model)
                model_called = True
                result = model_call.call_model(p.snapshot, p.messages_sent, p.arguments, model_info=model_info,
                                               on_text=wrap(callbacks.text), on_reasoning=wrap(callbacks.reasoning),
                                               cancel=run.handle)
            except modelspell.Cancelled as exc:
                cancelled_exc = exc
            except Exception as exc:  # noqa: BLE001 - ModelSpellError or anything unexpected
                failure = exc
    except BaseException as exc:  # noqa: BLE001 - the end point and commit still happen below
        crash = exc
        if isinstance(exc, Exception):
            failure = exc

    before_end_point()
    end = store.end_point(aid)

    outcome, error, partial, sent = None, None, None, None
    cancel_reason, stop_mode, result_dict = None, None, None
    if started_failed:                                                       # rule 0
        outcome, cancel_reason, sent = "stopped", "page_closed", False
    elif result is not None:                                                 # rule 1
        outcome, result_dict = classify_outcome(result), result_to_dict(result)
    elif cancelled_exc is not None:                                          # rule 2
        outcome, sent = "stopped", cancelled_exc.sent
        if cancelled_exc.partial is not None:
            partial = result_to_dict(cancelled_exc.partial)
        if end.cancelled:
            cancel_reason, stop_mode = end.reason or runs.USER_STOP, "immediate"
        else:
            cancel_reason = "page_closed"
    elif failure is not None or crash is not None:
        error = _error_fields(failure if failure is not None else crash, p)
        if end.cancelled:                                                    # rule 3
            outcome, cancel_reason, stop_mode = "stopped", end.reason or runs.USER_STOP, "immediate"
        else:                                                                # rule 4
            outcome = "error"
    else:  # cannot happen: the call returned nothing and raised nothing
        outcome, error = "error", {"class": "InternalError", "status": None,
                                   "description": "InternalError: no outcome"}

    check, skipped, value = None, None, checks.NO_VALUE
    if p.plan is not None:
        if outcome in ("complete", "truncated"):
            try:
                check, value = checks.check_named_answer(p.plan.contracts(), result.text)
            except Exception as exc:  # noqa: BLE001 - never read as a pass
                report_internal("checks", exc)
                check = _failed_checks()
        else:
            skipped = "blank_answer" if outcome in BLANK_OUTCOMES else "not_completed"
    elif p.contract is not None:
        if outcome in ("complete", "truncated"):
            try:
                check, value = checks.check_answer_value(p.contract.parsed, result.text)
            except Exception as exc:  # noqa: BLE001 - never read as a pass
                report_internal("checks", exc)
                check = _failed_checks()
        else:
            skipped = "blank_answer" if outcome in BLANK_OUTCOMES else "not_completed"

    record = {**base, "ended_at": runs.utc_now(), "model_called": model_called}
    if lookup is not None:
        record["model_lookup"] = lookup
    record.update({"outcome": outcome, "result": result_dict, "partial": partial, "error": error,
                   "cancel_reason": cancel_reason, "stop_requested_at": end.stop_requested_at,
                   "stop_mode": stop_mode, "sent": sent, "check": check})
    if skipped is not None:
        record["check_skipped"] = skipped

    saving = bool(run.save_runs and sink is not None)
    status = store.commit(aid, record, saving)

    if callbacks.committed is not None:
        try:
            callbacks.committed(record, status)
        except Exception:  # noqa: BLE001 - the commit stands whatever the page did
            pass
    if saving:
        try:
            saved = sink.write_attempt(run, record)
        except Exception as exc:  # noqa: BLE001 - settle exactly once, whatever happened
            report_internal("history", exc)
            saved = {"save_status": "failed", "reason": "the run file could not be written (internal error)"}
        store.settle_save(aid, saved["save_status"], **{k: v for k, v in saved.items() if k != "save_status"})
    if crash is not None and not isinstance(crash, Exception):
        raise crash
    return record, value
