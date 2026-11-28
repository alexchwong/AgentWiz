"""Per-run execution record for Company work: operations, events and published progress updates.

The Recorder is the single authority for live execution state. The activity panel, the progress strip and the
representative's progress updates are all derived from it, so they cannot contradict one another. It performs no
I/O and takes no locks: callers hold ``Company.lock``.
"""
from __future__ import annotations

import time

from . import chat_history

STATES = ("queued", "running", "awaiting_approval", "complete", "failed", "blocked", "cancelled", "not_run")
TERMINAL = {"complete", "failed", "blocked", "cancelled", "not_run"}
MAX_OPERATIONS = 256
MAX_UPDATES = 64
MAX_EVENTS = 1024
LABEL = 48
TITLE = 40
WORDS = 50
CHARACTERS = 360
TOOL_UPDATES = 4
AGENT_LABELS = {"company/ceo": "CEO", "company/representative": "Representative"}
FALLBACK = "Progress recorded. See the activity panel for details."

REASON_TEXT = {
    "admission_busy": "another execution slot was busy",
    "allowance_reserved": "the remaining allowance is reserved",
    "approval_expired": "the approval request expired",
    "approval_invalid": "the approved request no longer matched",
    "attempts_exhausted": "the model-attempt limit was reached",
    "binding_changed": "the model settings changed during the run",
    "block_failed": "a reviewed block did not complete",
    "budget_bound_unavailable": "a token or cost cap cannot be verified",
    "budget_exceeded": "the time limit was reached",
    "call_unknown": "a step's outcome is uncertain",
    "capability_inventory_limit": "too many tools were offered",
    "checker_unsatisfied": "the checker did not pass the answer",
    "checklist_unsatisfied": "a checklist item was not met",
    "configuration_changed": "the tool configuration changed",
    "credential_in_company_text": "the text contained a credential",
    "credential_in_model_output": "a model output contained a credential",
    "deadline_exceeded": "the time limit was reached",
    "destination_refused": "the destination is not permitted",
    "evidence_write_failed": "run evidence could not be written",
    "external_outcome_uncertain": "a provider response was interrupted; outcome uncertain",
    "internal_error": "an internal error occurred",
    "invalid_checklist_evidence": "checklist evidence did not match the source",
    "invalid_model_output": "a model returned an invalid response",
    "invalid_quote": "a quotation did not match the brief",
    "invalid_report": "the CEO report failed validation",
    "invalid_workflow_check": "a workflow check is invalid",
    "invalid_workflow_evidence": "workflow evidence failed validation",
    "invalid_workflow_input": "the workflow inputs were invalid",
    "missing_credential": "no OpenRouter key is available",
    "model_error": "the model request failed",
    "output_limit": "a model response was too long",
    "pathway_unsupported": "the selected pathway cannot run this task",
    "process_ended": "AgentWiz closed before the run finished",
    "provider_rate_limit": "the provider rate limit was reached",
    "rejected": "the workflow could not start",
    "stale_revision": "settings changed before the run started",
    "stopped": "you stopped the run",
    "tool_call_refused": "a tool request was refused",
    "tool_denied": "you denied the tool request",
    "tool_failed": "a tool did not complete",
    "tool_result_limit": "a tool result was too large",
    "user_stop": "you stopped the run",
    "workflow_unsatisfied": "the workflow did not complete",
}


def reason_text(code):
    return REASON_TEXT.get(code) or (code or "unknown").replace("_", " ")


def clip(value, limit=LABEL):
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[:limit - 1] + "…"


def fits(text):
    return isinstance(text, str) and bool(text.strip()) and len(text.split()) <= WORDS and len(text) <= CHARACTERS


def plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def render(kind, data):
    """Runtime-authored update text. Every branch is bounded so that `fits` holds for maximal fillers."""
    title = clip(data.get("title"), TITLE)
    if kind == "brief_received":
        return {"A": "The CEO has received your brief and is answering it directly.",
                "B": "The CEO has received your brief and will work on it with reviewed tools."}.get(
            data.get("policy"), "The CEO has received your brief and is choosing how to approach it.")
    if kind == "plan":
        strategy = data.get("strategy")
        if strategy == "solo":
            return "The CEO will answer directly without delegating."
        if strategy == "tools":
            return "The CEO will use reviewed tools where they help."
        if strategy == "workflow":
            return f"The CEO chose the reviewed workflow “{title}”. It is starting now."
        if strategy == "specialists":
            return f"The CEO assigned the {title} structure. {clip(data.get('first'), TITLE)} starts first."
        return "The CEO has chosen an approach."
    if kind == "tool":
        return f"{title} returned a result ({clip(data.get('evidence_id'), 16)}). The CEO is reviewing it."
    if kind == "specialist":
        stage = clip(data.get("stage"), TITLE)
        if "passed" in data:
            findings = data.get("findings", 0)
            if data["passed"]:
                noted = f"; {plural(findings, 'finding')} noted" if findings else ""
                return f"{stage} passed the candidate answer{noted}. The CEO is preparing the report."
            return (f"{stage} did not pass the candidate answer ({plural(findings, 'finding')}). "
                    "The report cannot be marked complete.")
        quotes = plural(data.get("quotes", 0), "verified quotation")
        if data.get("next"):
            return f"{stage} proposed an answer with {quotes}. {clip(data['next'], TITLE)} is reviewing it next."
        return f"{stage} proposed an answer with {quotes}. The CEO is preparing the report."
    if kind == "workflow":
        total, complete = data.get("total", 0), data.get("complete", 0)
        if data.get("outcome") == "complete":
            conditions = data.get("conditions", 0)
            open_ = f"; {plural(conditions, 'open condition')}" if conditions else ""
            return (f"The workflow “{title}” finished all {plural(total, 'step')}{open_}. "
                    "The CEO is preparing the report.")
        node = clip(data.get("node") or "a step", TITLE)
        return (f"The workflow “{title}” stopped at {node} ({reason_text(data.get('reason'))}) after "
                f"{complete} of {plural(total, 'step')}. The CEO is reporting on what was completed.")
    if kind == "approval":
        return (f"The CEO needs your approval for one read-only {title} call to {clip(data.get('host'), TITLE)}. "
                "Review the exact request below.")
    if kind == "final":
        outcome, status = data.get("outcome"), data.get("report_status")
        done = plural(data.get("completed", 0), "operation")
        if outcome == "stopped":
            return f"Stopped at your request. {done} completed; {data.get('not_run', 0)} not run. No report was produced."
        if status == "needs_clarification":
            return "The CEO needs more information before it can finish. Its question is below."
        if status == "complete":
            return "The CEO report is ready. Status: complete."
        if status:
            return (f"The CEO report is ready. Status: {status.replace('_', ' ')}; "
                    f"{plural(data.get('open_conditions', 0), 'open condition')}.")
        return f"The run ended without a report: {reason_text(data.get('reason'))}. {done} completed."
    return FALLBACK


KIND_OF_EVENT = {"run_started": "brief_received", "plan_decided": "plan", "tool_result": "tool",
                 "specialist_result": "specialist", "workflow_result": "workflow",
                 "approval_requested": "approval", "run_finished": "final"}


class Recorder:
    def __init__(self, started=None):
        self.started = time.monotonic() if started is None else started
        self.operations = {}
        self.events = []
        self.updates = []
        self.truncated = False
        self.accounted = set()
        self.tool_updates = 0
        self.closed = False

    def now(self):
        return round((time.monotonic() - self.started) * 1000)

    # --- operations --------------------------------------------------------------------------------------------
    def _new(self, kind, path, activity, parent, requested_model, state):
        if len(self.operations) >= MAX_OPERATIONS:
            self.truncated = True
            return None
        ident = f"op-{len(self.operations) + 1}"
        self.operations[ident] = {
            "id": ident, "parent": parent if parent in self.operations else None, "kind": kind,
            "path": [{"tier": row["tier"], "id": clip(row["id"], 128), "label": clip(row["label"])} for row in path],
            "activity": clip(activity, 60), "state": state, "requested_model": requested_model, "served": None,
            "started_ms": self.now() if state != "queued" else None, "ended_ms": None, "usage": None,
            "code": None, "detail": None, "calls": 0}
        return ident

    def start_op(self, kind, path, activity, *, parent=None, requested_model=None, state="running"):
        ident = self._new(kind, path, activity, parent, requested_model, state)
        if ident:
            self.event("op_started", op=ident)
        return ident

    def queue_op(self, kind, path, activity, *, parent=None):
        return self._new(kind, path, activity, parent, None, "queued")

    def run_op(self, ident, activity=None, requested_model=None):
        op = self.operations.get(ident)
        if op is None or op["state"] in TERMINAL:
            return
        op["state"] = "running"
        op["started_ms"] = self.now()
        if activity:
            op["activity"] = clip(activity, 60)
        if requested_model:
            op["requested_model"] = requested_model
        self.event("op_started", op=ident)

    def set_state(self, ident, state):
        op = self.operations.get(ident)
        if op is not None and op["state"] not in TERMINAL:
            op["state"] = state

    def finish_op(self, ident, state, *, code=None, detail=None):
        op = self.operations.get(ident)
        if op is None or op["state"] in TERMINAL:
            return
        op["state"] = state
        if op["started_ms"] is None and state != "not_run":
            op["started_ms"] = self.now()
        op["ended_ms"] = self.now() if state != "not_run" else None
        op["code"] = code
        if detail:
            op["detail"] = {**(op["detail"] or {}), **detail}
        self.event("op_finished", op=ident, state=state)

    def set_detail(self, ident, detail):
        op = self.operations.get(ident)
        if op is not None:
            op["detail"] = {**(op["detail"] or {}), **detail}

    def model_call(self, ident, usage, served=None):
        """Attribute one attempted model call to an operation."""
        op = self.operations.get(ident)
        if op is None:
            return
        op["calls"] += 1
        op["usage"] = chat_history.merge_usage(op["usage"], usage)
        if served is not None:
            op["served"] = served

    def path_of(self, ident):
        op = self.operations.get(ident)
        return list(op["path"]) if op else []

    def depth(self, ident):
        depth, seen = 0, set()
        while ident in self.operations and ident not in seen:
            seen.add(ident)
            ident = self.operations[ident]["parent"]
            depth += 1
        return depth

    # --- events and updates -----------------------------------------------------------------------------------
    def event(self, kind, *, op=None, reportable=False, **data):
        if len(self.events) >= MAX_EVENTS:
            self.truncated = True
            if not reportable:
                return None
            # Reportable events are never dropped: they must be accounted for.
            self.events = [row for row in self.events if row["reportable"]]
        seq = (self.events[-1]["seq"] if self.events else 0) + 1
        self.events.append({"seq": seq, "at_ms": self.now(), "type": kind, "op": op,
                            "reportable": reportable, "data": data})
        return seq

    def pending(self):
        return [row for row in self.events if row["reportable"] and row["seq"] not in self.accounted]

    def publish_pending(self, final=False):
        """Publish one update covering every unaccounted reportable event. Returns True when published."""
        pending = self.pending()
        if not pending:
            return False
        held = [row for row in pending if row["type"] == "tool_result"] if self.tool_updates >= TOOL_UPDATES else []
        spoken = [row for row in pending if row not in held]
        if not spoken and not final:
            return False
        if len(self.updates) >= MAX_UPDATES - 1 and not final:
            self.truncated = True
            return False
        prefix = ""
        if len(self.updates) >= MAX_UPDATES - 1 and final:
            skipped = len(pending) - 1
            prefix = f"Plus {plural(skipped, 'earlier event')} recorded in the activity panel. " if skipped else ""
        elif held:
            prefix = f"Plus {len(held)} further tool result(s). "
        event = (spoken or held)[-1]
        kind = KIND_OF_EVENT.get(event["type"], "final")
        source = "run_record"
        text = render(kind, event["data"])
        ceo = event["data"].get("ceo_update")
        if kind == "plan" and ceo is not None:
            if fits(ceo) and fits(prefix + ceo):
                text, source = ceo, "ceo"
            else:
                event["data"]["ceo_update_rejected"] = True
        text = prefix + text
        if not fits(text):
            text = FALLBACK
        if kind == "tool":
            self.tool_updates += 1
        if len(self.updates) >= MAX_UPDATES:
            self.updates.pop()
        self.updates.append({"seq": len(self.updates) + 1, "at_ms": self.now(), "source": source, "kind": kind,
                             "text": text, "accounts_for": [row["seq"] for row in pending]})
        self.accounted.update(row["seq"] for row in pending)
        return True

    def close(self, outcome, reason=None):
        if self.closed:
            return
        self.closed = True
        for ident, op in self.operations.items():
            if op["state"] == "queued":
                self.finish_op(ident, "not_run", code=reason)
            elif op["state"] not in TERMINAL:
                self.finish_op(ident, "cancelled" if outcome == "stopped" else "failed", code=reason)

    # --- views --------------------------------------------------------------------------------------------------
    def counts(self):
        result = {state: 0 for state in ("running", "complete", "queued", "failed", "not_run", "cancelled")}
        for op in self.operations.values():
            state = {"awaiting_approval": "running", "blocked": "failed"}.get(op["state"], op["state"])
            result[state] = result.get(state, 0) + 1
        return result

    def current(self):
        running = [op for op in self.operations.values() if op["state"] in ("running", "awaiting_approval")]
        if not running:
            return None
        return max(running, key=lambda op: (self.depth(op["id"]), int(op["id"][3:])))

    def state(self, finished):
        op = self.current()
        if op is None:
            return "finished" if finished else "starting"
        if op["state"] == "awaiting_approval":
            return "awaiting_approval"
        if op["kind"] == "ceo_plan":
            return "planning"
        if op["kind"] == "ceo_report":
            return "reporting"
        if op["kind"] == "specialist" and any(row["id"] == "company/checker" for row in op["path"]):
            return "checking"
        return "executing"

    def view(self, *, finished=False, attempts=None):
        current = self.current()
        operations = [{**{key: value for key, value in op.items() if key != "calls"},
                       "reason_text": reason_text(op["code"]) if op["code"] else None}
                      for op in self.operations.values()]
        summary = {"state": self.state(finished), "counts": self.counts(), "elapsed_ms": self.now(),
                   "truncated": self.truncated, "model_attempts": attempts,
                   "current": None if current is None else {
                       "op": current["id"], "activity": current["activity"],
                       "path": " / ".join(row["label"] for row in current["path"]),
                       "model": (current["requested_model"] or {}).get("model"), "served": current["served"]}}
        return {"operations": operations, "updates": [dict(row) for row in self.updates], "summary": summary,
                "events_total": len(self.events)}


def agent_path(catalogue, stage):
    """Path tier for a catalogue entrypoint's agent, labelled from the compiled component title."""
    ref = catalogue.manifest["entrypoints"][stage]["agent"]["ref"]
    try:
        title = catalogue.reference({"ref": ref}, "agent")["component"]["title"]
    except (KeyError, ValueError):
        title = ref["id"]
    return {"tier": "agent", "id": ref["id"], "label": AGENT_LABELS.get(ref["id"], title)}
