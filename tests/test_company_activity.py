"""Company Recorder: bounded operations, progress-update templates, coalescing and the accounting invariant."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentwiz import company_activity as activity

LONG = "W" * 80


def maximal(kind):
    """Template fillers at their maximum: long titles, large counts and the longest reason phrase."""
    longest = max(activity.REASON_TEXT, key=lambda code: len(activity.REASON_TEXT[code]))
    base = {"title": LONG, "first": LONG, "stage": LONG, "next": LONG, "node": LONG, "host": LONG,
            "evidence_id": "tool-999999", "quotes": 999, "findings": 999, "total": 999, "complete": 999,
            "conditions": 999, "open_conditions": 999, "completed": 999, "not_run": 999, "reason": longest}
    return {
        "brief_received": [{**base, "policy": policy} for policy in "ABCDE"],
        "plan": [{**base, "strategy": strategy} for strategy in ("solo", "tools", "workflow", "specialists", "other")],
        "tool": [base],
        "specialist": [{**base}, {**base, "next": None}, {**base, "passed": True}, {**base, "passed": False},
                       {**base, "passed": True, "findings": 0}],
        "workflow": [{**base, "outcome": "complete"}, {**base, "outcome": "failed"}],
        "approval": [base],
        "final": [{**base, "outcome": outcome, "report_status": status} for outcome, status in
                  [("stopped", None), ("complete", "complete"), ("partial", "partial"), ("blocked", None),
                   ("needs_clarification", "needs_clarification"), ("failed", None)]],
    }[kind]


@pytest.mark.parametrize("kind", ["brief_received", "plan", "tool", "specialist", "workflow", "approval", "final"])
def test_every_template_fits_with_maximal_fillers_and_prefixes(kind):
    for data in maximal(kind):
        text = activity.render(kind, data)
        assert activity.fits(text), (kind, text)
        for prefix in ("Plus 999 further tool result(s). ", "Plus 999 earlier events recorded in the activity panel. "):
            assert activity.fits(prefix + text), (kind, prefix + text)


def test_fits_counts_words_and_characters():
    assert activity.fits(" ".join(["word"] * 50))
    assert not activity.fits(" ".join(["word"] * 51))
    assert not activity.fits("x" * 361)
    assert not activity.fits("   ")


def test_every_refusal_code_has_a_phrase():
    root = Path(activity.__file__).parent
    codes = set()
    for name in ("company.py", "company_tools.py", "company_workflow.py"):
        source = (root / name).read_text()
        codes.update(re.findall(r'Refusal\("([a-z_]+)"', source))
        codes.update(re.findall(r'conditions\.append\("([a-z_]+)"\)', source))
        codes.update(re.findall(r'code = "([a-z_]+)"', source))
    # Request-validation codes are returned to the caller before any run exists, so they never reach an update.
    request_only = {"invalid_fields", "invalid_text", "invalid_limit", "invalid_class", "invalid_policy",
                    "invalid_model_choice", "cloud_model_required", "binding_unavailable", "stale_workflow_preset",
                    "not_found", "company_busy", "intake_unavailable", "changed_duplicate", "handoff_unavailable",
                    "handoff_changed", "report_unavailable", "report_integrity", "read_only", "invalid_class_store",
                    "not_pending", "invalid_decision", "surface_refused", "invalid_content_type", "invalid_body",
                    "invalid_version"}
    missing = sorted(code for code in codes - request_only if code not in activity.REASON_TEXT)
    assert not missing


def test_operations_bounds_and_close():
    record = activity.Recorder()
    ids = [record.start_op("tool", [{"tier": "tool", "id": str(n), "label": str(n)}], "Running") for n in range(300)]
    assert ids[activity.MAX_OPERATIONS - 1] and ids[activity.MAX_OPERATIONS] is None and record.truncated
    queued = activity.Recorder()
    first = queued.start_op("ceo_plan", [], "Planning")
    later = queued.queue_op("specialist", [], "Waiting")
    queued.close("stopped", "user_stop")
    states = {op["id"]: op["state"] for op in queued.view()["operations"]}
    assert states == {first: "cancelled", later: "not_run"}
    failed = activity.Recorder()
    running = failed.start_op("ceo_report", [], "Writing")
    failed.close("failed", "invalid_report")
    assert failed.operations[running]["state"] == "failed" and failed.operations[running]["code"] == "invalid_report"


def test_tool_results_coalesce_after_four_and_every_event_is_accounted():
    record = activity.Recorder()
    record.event("run_started", reportable=True, policy="B")
    record.publish_pending()
    for n in range(7):
        record.event("tool_result", reportable=True, title="Calculate", evidence_id=f"tool-{n + 1}")
        record.publish_pending()
    record.event("run_finished", reportable=True, outcome="complete", report_status="complete")
    record.publish_pending(final=True)
    kinds = [row["kind"] for row in record.updates]
    assert kinds == ["brief_received", "tool", "tool", "tool", "tool", "final"]
    assert record.updates[-1]["text"].startswith("Plus 3 further tool result(s). ")
    accounted = [seq for row in record.updates for seq in row["accounts_for"]]
    reportable = [row["seq"] for row in record.events if row["reportable"]]
    assert sorted(accounted) == reportable and len(set(accounted)) == len(accounted)
    assert all(activity.fits(row["text"]) for row in record.updates)


def test_simultaneous_arrivals_share_one_update():
    record = activity.Recorder()
    record.event("workflow_result", reportable=True, title="Source", outcome="complete", total=2, complete=2)
    record.event("run_finished", reportable=True, outcome="partial", report_status="partial", open_conditions=1)
    assert record.publish_pending(final=True)
    assert len(record.updates) == 1 and record.updates[0]["accounts_for"] == [1, 2]
    assert not record.publish_pending()          # nothing new: no duplicate noise


def test_ceo_update_used_only_when_it_fits():
    record = activity.Recorder()
    record.event("plan_decided", reportable=True, strategy="solo", ceo_update="I will answer directly.")
    record.publish_pending()
    record.event("plan_decided", reportable=True, strategy="solo", ceo_update=" ".join(["long"] * 51))
    record.publish_pending()
    assert [(row["source"], row["text"]) for row in record.updates] == [
        ("ceo", "I will answer directly."), ("run_record", "The CEO will answer directly without delegating.")]
    assert record.events[-1]["data"]["ceo_update_rejected"]


def test_update_overflow_keeps_the_final_slot():
    record = activity.Recorder()
    for n in range(80):
        record.event("specialist_result", reportable=True, stage="Reader", quotes=n)
        record.publish_pending()
    record.event("run_finished", reportable=True, outcome="complete", report_status="complete")
    record.publish_pending(final=True)
    assert len(record.updates) == activity.MAX_UPDATES and record.updates[-1]["kind"] == "final"
    assert record.updates[-1]["text"].startswith("Plus ")
    assert not record.pending()


def test_state_follows_the_deepest_running_operation():
    record = activity.Recorder()
    assert record.state(False) == "starting"
    plan = record.start_op("ceo_plan", [], "Planning")
    assert record.state(False) == "planning"
    record.finish_op(plan, "complete")
    checker = record.start_op("specialist", [{"tier": "agent", "id": "company/checker", "label": "Checker"}], "Checking")
    assert record.state(False) == "checking"
    tool = record.start_op("tool", [], "Running", parent=checker)
    approval = record.start_op("approval", [], "Waiting", parent=tool, state="awaiting_approval")
    assert record.state(False) == "awaiting_approval"
    for ident in (approval, tool, checker):
        record.finish_op(ident, "complete")
    report = record.start_op("ceo_report", [], "Writing")
    assert record.state(False) == "reporting"
    record.finish_op(report, "complete")
    assert record.state(True) == "finished"
