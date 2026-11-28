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
            self.work.trace.append({"stage": "workflow_model", "outcome": record.get("outcome"),
                                    "served_provider": (result or {}).get("provider"),
                                    "selected_provider": routing.get("selected_slug")})

    def execute_node(self, graph, node, values):
        self.values = values
        self.reserve_calls = 1 + sum(graph.blocks[key].step.kind == "model"
                                     for key in graph.order[graph.order.index(node) + 1:])
        return super().execute_node(graph, node, values)

    def accept_node(self, graph, node, result):
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
    from .company import Refusal
    work.phase = "workflow"
    job = CompanyWorkflow(company, work)
    preset = work.presets[decision["preset_id"]]
    job.preset = preset
    job.attempt_limit = preset["bundle"]["definition"].get("budget", {}).get("max_model_attempts", 32)
    work.deadline = min(work.deadline, time.monotonic() + preset["bundle"]["definition"].get(
        "budget", {}).get("max_seconds", 1800))
    job.deadline = work.deadline
    with ExitStack() as stack:
        graph = graph_for(company, work, stack, preset)
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
