"""CEO mode inside the AgentWiz Chat page: composer, handoff, live run block, report fidelity and recovery."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from playwright.sync_api import expect

from agentwiz.tutorial_support import backend
from conftest import request
from test_company import company_client, create, settled  # noqa: F401
from test_page import browser  # noqa: F401

WAIT = 15000
BRIEF = ("From these synthetic release notes, answer which version introduced Export Preview.\n\n"
         "- v1.3.0: Improved PDF import.\n- v1.4.0: Introduced Export Preview, disabled by default.\n")


def tid(scope, name):
    return scope.locator(f'[data-test="{name}"]')


@pytest.fixture
def chat_page(company_client, browser):  # noqa: F811
    child, company = company_client
    chat = child.chat_listener
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(chat.authority.launch_url(chat.url))
    page.locator("#run").wait_for(state="attached")
    yield child, company, page
    page.close()
    assert errors == []


def scripted(monkeypatch, *, hold=None, report_text=None):
    """Representative proposes a brief with a structured handoff; the CEO answers directly (Policy E → solo)."""
    real = backend.company_reply
    calls = []

    def reply(messages):
        system = messages[0]["content"]
        calls.append(system)
        if "intake representative" in system:
            data = json.loads(messages[1]["content"])
            if "brief" in data["message"].lower():
                return {"kind": "brief_proposal", "reply": "Here is the brief I propose.", "brief": BRIEF,
                        "handoff": {"objective": "Identify the version that introduced Export Preview.",
                                    "constraints": ["Cite the exact line", "Use only the notes"],
                                    "deliverable": "The version number with its supporting line."}}
            return {"kind": "clarification", "reply": "Should I cite the exact line?"}
        if "planning object" in system:
            return {"strategy": "solo", "purpose": "Answer from the notes.", "conditions": [],
                    "status_update": "I will answer this directly from the supplied notes."}
        if hold is not None:
            hold.wait(20)
        result = real(messages)
        if report_text:
            result["markdown"] = report_text
        return result

    monkeypatch.setattr(backend, "company_reply", reply)
    return calls


def select_ceo(page):
    tid(page, "composer-mode-ceo").check()
    expect(tid(page, "ceo-notice")).to_be_visible()


def send(page, text):
    page.locator("#task").fill(text)
    page.locator("#run").click()


def test_composer_switches_to_cloud_ceo_mode(chat_page):
    child, _, page = chat_page
    expect(page.locator("#company-open")).to_have_count(0)
    expect(page.locator("#company")).to_have_count(0)
    select_ceo(page)
    expect(page.locator("#mode")).to_be_disabled()
    expect(page.locator("#mode")).to_have_value("cloud")
    expect(page.locator("#attach")).to_be_disabled()
    for control in ("#chat-task", "#reasoning", "#use-context"):
        expect(page.locator(control)).to_be_hidden()
    expect(page.locator("#task")).to_have_attribute("placeholder", "Describe the task for the CEO")
    page.locator('input[name="addressee"][value="chat"]').check()
    expect(page.locator("#mode")).to_be_enabled()
    expect(page.locator("#chat-task")).to_be_visible()


def test_representative_handoff_live_run_and_exact_report(chat_page, monkeypatch):
    child, company, page = chat_page
    report_text = ("Status: complete\n\n## Result\nExport Preview appeared in v1.4.0 ✓.\n\n## Evidence\n"
                   "The supplied notes identify v1.4.0.\n\n## Open conditions\nNone.\n")
    calls = scripted(monkeypatch, report_text=report_text)
    select_ceo(page)
    send(page, "Which version added export preview?")
    expect(tid(page, "ceo-representative")).to_contain_text("Should I cite the exact line?", timeout=WAIT)
    expect(tid(page, "ceo-user")).to_contain_text("To CEO")
    send(page, "Yes, please write the brief.")
    handoff = tid(page, "ceo-handoff")
    expect(handoff).to_contain_text("Identify the version that introduced Export Preview.", timeout=WAIT)
    expect(handoff).to_contain_text("Cite the exact line")
    expect(handoff).to_contain_text("Cloud · OpenRouter · tutorial/company")
    expect(handoff).to_contain_text("Typed text in this brief only")
    exact = tid(handoff, "ceo-exact-text")
    expect(exact).not_to_have_attribute("open", "")
    assert sum("intake representative" in call for call in calls) == 2
    exact.locator("summary").click()
    expect(exact.locator("pre")).to_contain_text("v1.4.0: Introduced Export Preview")
    tid(handoff, "ceo-send").click()
    run = tid(page, "ceo-run")
    report = tid(run, "ceo-report")
    expect(report).to_contain_text("v1.4.0 ✓", timeout=WAIT)
    expect(report).to_contain_text("original, unchanged")
    updates = tid(run, "ceo-update")
    expect(updates.first).to_contain_text("The CEO has received your brief")
    expect(run.locator('[data-test="ceo-update"][data-source="ceo"]')).to_contain_text("directly from the supplied notes")
    expect(tid(run, "ceo-delivery")).to_contain_text("The CEO report is ready. Status: complete.")
    expect(tid(handoff, "ceo-send")).to_have_count(0)
    tid(run, "ceo-activity").locator("summary").click()
    plan = run.locator('[data-test="ceo-op"][data-kind="ceo_plan"]')
    expect(plan).to_contain_text("CEO — Choosing an approach")
    expect(plan).to_contain_text("Model tutorial/company")
    assert page.get_by_text("strategy_inventory").count() == 0
    with page.expect_download() as downloaded:
        tid(run, "ceo-download").click()
    assert downloaded.value.suggested_filename.endswith(".md.txt")
    assert Path(downloaded.value.path()).read_bytes() == report_text.encode()
    # Refresh: the thread is rebuilt from the conversation and the report is still exact.
    page.reload()
    expect(tid(page, "ceo-report")).to_contain_text("v1.4.0 ✓", timeout=WAIT)
    expect(tid(page, "ceo-handoff")).to_contain_text("Sent · see the run below")
    # Follow-up after the report is representative commentary, not part of the report.
    select_ceo(page)
    send(page, "Thanks, what does that mean?")
    expect(page.locator('[data-test="ceo-representative"]').last).to_contain_text("Representative commentary",
                                                                                  timeout=WAIT)


def test_edited_exact_text_withdraws_summary_and_is_sent(chat_page, monkeypatch):
    child, company, page = chat_page
    sent = []
    real = backend.company_reply
    scripted(monkeypatch)
    proposed = backend.company_reply

    def capture(messages):
        if "Act as the CEO" in messages[0]["content"]:
            sent.append(json.loads(messages[1]["content"])["brief"])
        return proposed(messages)

    monkeypatch.setattr(backend, "company_reply", capture)
    select_ceo(page)
    send(page, "Please write the brief.")
    handoff = tid(page, "ceo-handoff")
    tid(handoff, "ceo-edit").click(timeout=WAIT)
    area = tid(handoff, "ceo-edit-text")
    expect(area).to_have_value(BRIEF.rstrip("\n") + "\n")
    area.fill(BRIEF + "Answer in one sentence.\n")
    tid(handoff, "ceo-edit-save").click()
    edited = tid(page, "ceo-handoff").last
    expect(edited).to_contain_text("Edited by you — summary withdrawn", timeout=WAIT)
    expect(tid(page, "ceo-handoff").first).to_contain_text("Superseded")
    tid(edited, "ceo-send").click()
    expect(tid(page, "ceo-report")).to_be_visible(timeout=WAIT)
    assert sent == [BRIEF + "Answer in one sentence.\n"]
    assert real is not None


def test_stop_from_composer_and_reload_during_run(chat_page, monkeypatch):
    child, company, page = chat_page
    hold = threading.Event()
    scripted(monkeypatch, hold=hold)
    try:
        select_ceo(page)
        send(page, "Please write the brief.")
        tid(page, "ceo-send").click(timeout=WAIT)
        strip = tid(page, "ceo-strip")
        expect(strip).to_contain_text("Writing report", timeout=WAIT)
        page.reload()
        expect(tid(page, "ceo-strip")).to_contain_text("Writing report", timeout=WAIT)
        page.locator("#stop").click()
        expect(tid(page, "ceo-strip")).to_contain_text("Stopped", timeout=WAIT)
        expect(tid(page, "ceo-delivery")).to_contain_text("Stopped at your request")
        expect(tid(page, "ceo-report")).to_have_count(0)
    finally:
        hold.set()


def test_private_conversation_disables_ceo(chat_page):
    child, company, page = chat_page
    conversation = company.conversation()
    with child.state.agent.lock:
        child.state.agent.conversation.private = True
    page.reload()
    radio = page.locator('input[name="addressee"][value="ceo"]')
    expect(radio).to_be_disabled(timeout=WAIT)
    assert conversation
