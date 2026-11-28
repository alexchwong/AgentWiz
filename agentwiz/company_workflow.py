"""Run the bundled pinned source graph using the native serial executor."""
from __future__ import annotations

import threading
import time
from contextlib import ExitStack

from . import agent_tools, chat_history, history, jsonio, outbound_policy, runs, workflow_runner, company_catalogue
from .workflow_definition import select
from .state import RoleCapture
from .workshop import WorkshopError


def acquire_workflow(state, work):
    from .company import Refusal
    workshop = state.workshop
    with workshop.lock:
        if workshop.active is not None:
            raise Refusal("admission_busy", 503)
        workshop.active = work.ident
        work.workflow = work.ident


def release_workflow(state, work):
    if work.workflow is None:
        return
    with state.workshop.lock:
        if state.workshop.active == work.workflow:
            state.workshop.active = None
        work.workflow = None


class CompanyWorkflow(workflow_runner.Job):
    def __init__(self, company, work):
        super().__init__(company.state)
        self.company = company
        self.work = work
        self.deadline = work.deadline
        self.reserve_calls = 0
        self.preset = None
        self.node_ops = {}
        self.node_labels = {}

    def parent_guard(self):
        from .company import Refusal
        try:
            if self.work.cancel.cancelled:
                raise Refusal("stopped")
            if time.monotonic() >= self.deadline:
                raise Refusal("deadline_exceeded")
            self.company.current(self.work.intake, self.work.binding)
        except Refusal as exc:
            raise outbound_policy.Refused(exc.code) from None

    def allow_attempt(self):
        from .company import Refusal
        with self.company.lock:
            try:
                self.company.reserve(self.work, self.reserve_calls)
            except Refusal as exc:
                self.work.conditions.append(exc.code)
                return False
            self.work.intake.attempts += 1
        return super().allow_attempt()

    def attempt_committed(self, record):
        with self.company.lock:
            result = record.get("result") or record.get("partial")
            self.work.usage = chat_history.merge_usage(self.work.usage, chat_history.usage_summary(result))
            routing = record.get("routing") or {}
            if routing.get("probe_usage") == "unreported":
                self.work.usage = chat_history.merge_usage(self.work.usage, chat_history.usage_summary(None))
            served = {"model": (result or {}).get("model") or None, "provider": (result or {}).get("provider") or None,
                      "selected_provider": routing.get("selected_slug")}
            self.work.trace.append({"stage": "workflow_model", "outcome": record.get("outcome"),
                                    "served_provider": served["provider"], "served_model": served["model"],
                                    "selected_provider": served["selected_provider"]})
            record_ = self.work.record
            parent = self.work.current_op
            if parent is not None and record_.operations.get(parent, {}).get("kind") == "workflow_node":
                outcome = record.get("outcome")
                op = record_.start_op("model_attempt", record_.path_of(parent), "Model attempt", parent=parent,
                                      requested_model=self.company.requested(self.work))
                record_.model_call(op, chat_history.usage_summary(result), served)
                record_.finish_op(op, "complete" if outcome in ("complete", None) else
                                  "cancelled" if self.work.cancel.cancelled else "failed",
                                  code=None if outcome in ("complete", None) else outcome)
            else:
                record_.model_call(parent, chat_history.usage_summary(result), served)

    def execute_node(self, graph, node, values):
        self.values = values
        self.reserve_calls = 1 + sum(graph.blocks[key].step.kind == "model"
                                     for key in graph.order[graph.order.index(node) + 1:])
        op = self.node_ops.get(node)
        if op is not None:
            with self.company.lock:
                self.work.record.run_op(op, "Running step")
                self.previous_op, self.work.current_op = self.work.current_op, op
        try:
            return super().execute_node(graph, node, values)
        except BaseException:
            self.finish_node(node, {"outcome": "failed"}, "internal_error")
            raise

    def finish_node(self, node, result, code=None):
        op = self.node_ops.get(node)
        if op is None:
            return
        with self.company.lock:
            outcome = result["outcome"]
            state = ("complete" if outcome == "complete" else "cancelled" if outcome == "stopped"
                     or self.work.cancel.cancelled else "failed")
            self.work.record.finish_op(op, state, code=code if state != "complete" else None)
            if self.work.current_op == op:
                self.work.current_op = getattr(self, "previous_op", None)

    def accept_node(self, graph, node, result):
        before = len(self.work.conditions)
        result = self.check_node(graph, node, result)
        added = self.work.conditions[before:]
        self.finish_node(node, result, added[-1] if added else result.get("outcome"))
        return result

    def check_node(self, graph, node, result):
        from .company import Refusal, quotations
        if result["outcome"] != "complete":
            return result
        raw = result["outputs"]
        _, unsafe = history.redact(raw, self.state.credentials())
        if unsafe:
            self.work.conditions.append("credential_in_model_output")
            return {**result, "outcome": "failed", "outputs": {}}
        values = {key: item["text"] if item["format"] == "text" else jsonio.decode_exact(item["text"])
                  for key, item in raw.items()}
        preset = self.preset
        evidence, conditions = {}, []
        def source(binding):
            identity = (binding["kind"], binding["port"]) if binding["kind"] == "input" else (
                binding["kind"], binding["node"], binding["port"])
            return select(self.values[identity], binding.get("path", []), node)

        try:
            for rule in preset["node_checks"]:
                if rule["node"] != node:
                    continue
                value = select(values[rule["port"]], rule.get("path", []), node)
                operation = rule["op"]
                excerpts = []
                if operation == "required_truth":
                    if value is not True:
                        conditions.append(rule["condition"])
                elif operation == "ambiguous_group":
                    if any(len(row[rule["field"]]) > 1 for row in value):
                        conditions.append(rule["condition"])
                elif operation == "contains_value":
                    if any(row[rule["field"]] == rule["value"] for row in value):
                        conditions.append(rule["condition"])
                elif operation == "quotes":
                    excerpts = value
                elif operation == "row_quotes":
                    excerpts = [row[rule["field"]] for row in value
                                if row[rule["field"]] is not None or not rule.get("nullable")]
                elif operation == "checklist":
                    candidate = source(rule["candidate"])
                    if ([row["item"] for row in value] != source(rule["criteria"])
                            or any((row["evidence"] and row["evidence"] not in candidate)
                                   or (row["met"] == "yes" and not row["evidence"]) for row in value)):
                        raise Refusal("invalid_checklist_evidence")
                    if any(row["met"] != "yes" for row in value):
                        conditions.append("checklist_unsatisfied")
                else:
                    raise Refusal("invalid_workflow_check")
                for item in quotations(self.work, excerpts, count=120):
                    evidence[f"{node}-quote-{len(evidence)+1}"] = item
            for export in preset["evidence_exports"]:
                if export["node"] == node:
                    value = select(values[export["port"]], export.get("path", []), node)
                    evidence[export["id"]] = {"value": value, "node": node, "port": export["port"],
                                               "workflow_digest": graph.digest}
        except (Refusal, WorkshopError):
            self.work.conditions.append("invalid_workflow_evidence")
            return {**result, "outcome": "failed", "outputs": {}}
        with self.company.lock:
            if self.work.cancel.cancelled:
                return {**result, "outcome": "stopped", "outputs": {}}
            self.work.evidence.update(evidence)
            self.work.conditions.extend(conditions)
        return result


def graph_for(company, work, stack, preset):
    from .company import Refusal
    state = company.state
    bundle = preset["bundle"]
    graph = workflow_runner.Graph(bundle, bundle["definition"]["name"], stack)
    if preset["uses_tools"] and not state.chat_preferences()["builtins"]:
        raise Refusal("pathway_unsupported")

    def binding(role):
        try:
            captured = company.current(work.intake, work.binding)
        except Refusal as exc:
            raise outbound_policy.Refused(exc.code) from None
        return RoleCapture(role, captured.profile_name, captured, {}, work.intake.revision)

    _, issues, _ = graph.destinations(state, state.workshop, model_resolver=binding)
    if issues:
        raise Refusal("pathway_unsupported")
    return graph


def execute_workflow(company, work, decision):
    preset = work.presets[decision["preset_id"]]
    path = company.ceo_path() + [{"tier": "workflow", "id": preset["id"], "label": preset["title"]}]
    with company.operation(work, "workflow", path, f"Running workflow {preset['title']}", model=False) as op:
        result, total, labels = run_workflow(company, work, decision, preset, path, op)
        if result["outcome"] != "complete":
            with company.lock:
                work.record.finish_op(op, "failed", code=work.conditions[-1] if work.conditions else result["outcome"])
    outcome = result["outcome"]
    nodes = result["nodes"]
    complete = sum(1 for row in nodes.values() if row.get("outcome") == "complete")
    failed = next((node for node, row in nodes.items() if row.get("outcome") not in ("complete", "not_run")), None)
    data = {"title": preset["title"], "outcome": outcome, "total": total, "complete": complete,
            "conditions": len(work.conditions)}
    if outcome != "complete":
        data.update(node=labels.get(failed, failed), reason=work.conditions[-1] if work.conditions else outcome)
    company.report_event(work, "workflow_result", **data)


def run_workflow(company, work, decision, preset, path, parent):
    from .company import Refusal
    work.phase = "workflow"
    job = CompanyWorkflow(company, work)
    job.preset = preset
    job.attempt_limit = preset["bundle"]["definition"].get("budget", {}).get("max_model_attempts", 32)
    work.deadline = min(work.deadline, time.monotonic() + preset["bundle"]["definition"].get(
        "budget", {}).get("max_seconds", 1800))
    job.deadline = work.deadline
    with ExitStack() as stack:
        graph = graph_for(company, work, stack, preset)
        with company.lock:
            for node in graph.order:
                label = graph.blocks[node].name or node
                job.node_labels[node] = label
                job.node_ops[node] = work.record.queue_op(
                    "workflow_node", path + [{"tier": "block", "id": node, "label": label}], "Waiting to run",
                    parent=parent)
        try:
            inputs = company_catalogue.bind(preset, work.brief, decision["parameters"])
        except (ValueError, agent_tools.ToolError):
            raise Refusal("invalid_workflow_input") from None
        try:
            graph.initial_values(inputs)
        except WorkshopError as exc:
            raise Refusal(exc.code) from None
        timer = threading.Timer(max(0, work.deadline - time.monotonic()), job.cancel, args=(runs.BUDGET_EXCEEDED,))
        timer.daemon = True
        with company.lock:
            company.reserve(work, preset["model_calls"])
            work.child = job
            timer.start()
        try:
            result = job.execute(graph, inputs, parent_budget=True)
        finally:
            timer.cancel()
            with company.lock:
                work.child = None
        with company.lock:
            work.trace.append({"stage": "workflow", "workflow_preset": preset["id"], "revision": preset["revision"],
                               "digest": graph.digest, "outcome": result["outcome"],
                               "nodes": {key: {**state, "block_snapshot": graph.definition["nodes"][key]["snapshot_id"]}
                                         for key, state in result["nodes"].items()}})
            if result["outcome"] != "complete":
                code = "workflow_unsatisfied"
                if result["outcome"] == "budget_exceeded":
                    code = "deadline_exceeded"
                elif result["outcome"] == "unknown":
                    code = "external_outcome_uncertain"
                work.conditions.append(code)
                if result["outcome"] == "stopped" or work.cancel.cancelled:
                    raise Refusal("stopped")
        return result, len(graph.order), dict(job.node_labels)
