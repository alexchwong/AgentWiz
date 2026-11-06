"""The tutorial: topic registry, sandbox, lifecycle and real guided UI actions (techspec 0.1.4, tests #112, #113;
0.1.3's isolation and failed-start tests, now by topic ID)."""
import os
import tempfile
import threading
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect

from agentwiz import jsonio, script_step, tutorial_topics
from agentwiz.workspace import WorkspaceInUse, open_workspace
from conftest import (failure_of, get_state, group_gone, iter_events, read_until, request, run_block, switch_history,
                      wait_until)
from test_page import browser, context, open_page, tid, has
from test_script_cleanup import kill_group

EXACT = '{"id":9007199254740993,"ratio":0.10000000000000000001}'
BLOCKS_SOURCE = Path(tutorial_topics.__file__).with_name("tutorial_support") / "blocks"


def snapshot(root: Path) -> dict:
    """Every file of a tree with what "unchanged" compares (inode, size, modification time)."""
    out = {}
    for path in sorted(root.rglob("*")):
        st = os.stat(path)
        out[str(path.relative_to(root))] = (st.st_ino, st.st_size, st.st_mtime_ns) if path.is_file() else "dir"
    return out


# --- 0.1.3, by topic ID ------------------------------------------------------------------------------------
def test_tutorial_isolation_and_cleanup(ui):
    before = get_state(ui)
    assert request(ui, "/api/tutorial")["active"] is False
    request(ui, "/api/tutorial/start", {})
    session = ui.tutorial
    child = session.child
    root = Path(session.folder.name)
    assert get_state(child)["workspace"]["name"] == "tutorial"
    assert get_state(child)["base_url"].startswith("http://127.0.0.1:")
    assert get_state(ui) == before
    info = request(child, "/api/tutorial/step", {"topic": "repair"})
    assert info["topic"] == "repair" and [t["id"] for t in info["topics"]] == [t.id for t in tutorial_topics.TOPICS]
    assert get_state(child)["model"] == "json-repair"
    resources = list(session.resources)
    session.close()
    assert not root.exists()
    assert all(not t.is_alive() and s.fileno() == -1 for s, t in resources)
    assert get_state(ui) == before
    request(ui, "/api/tutorial/start", {})
    assert session.child is not None


def test_tutorial_failed_start_cleans_up(ui, monkeypatch):
    from agentwiz.state import ProcessState

    def fail(*args, **kwargs):
        raise OSError("fixture startup failure")

    monkeypatch.setattr(ProcessState, "save_mcp_servers", fail)
    with pytest.raises(OSError):
        ui.tutorial.start()
    assert ui.tutorial.child is None
    assert ui.tutorial.resources == []
    assert ui.tutorial.folder is None


# --- #112 ---------------------------------------------------------------------------------------------------
def run_and_wait(page, outcome="complete"):
    tid(page, "blocks-run").click()
    has(page, "blocks-status", "data-state", "done")
    seen = tid(page, "blocks-status").get_attribute("data-outcome")
    if seen != outcome:                                             # say why, not only that
        raise AssertionError(f"outcome {seen!r}, expected {outcome!r}: " + tid(page, "blocks-result").text_content()[:3000])


def drive_chat(page):
    assert tid(page, "chat-prompt").input_value()
    tid(page, "chat-send").click()
    has(page, "chat-status", "data-outcome", "complete")


def drive_document(page):
    page.locator('[data-test="tutorial-action"][data-action="example-document"]').click()
    has(page, "documents-status", "data-state", "ready")


def drive_tool(page):
    tid(page, "mcp-run").click()
    has(page, "mcp-status", "data-outcome", "ok")


def drive_contract(page):
    expect(tid(page, "panel-contracts")).to_be_visible()                 # Workshop → Contracts (overhaul 15.1)
    has(page, "contracts-editor", "data-format", "structured")
    tid(page, "contracts-run-check").click()
    has(page, "contracts-check", "data-check", "pass")
    tid(page, "contracts-save").click()
    tid(tid(page, "ws-dialog"), "ws-confirm").click()
    has(page, "contracts-status", "data-state", "saved")
    tid(page, "tutorial-reset").click()
    has(page, "tutorial-status", "data-state", "ready")
    tid(page, "contracts-save").click()
    tid(tid(page, "ws-dialog"), "ws-confirm").click()
    has(page, "contracts-status", "data-state", "unchanged")             # the same definition appends nothing


def drive_repair(page):
    tid(page, "checked-send").click()
    page.locator('[data-test="checked-attempt"][data-check="fail"]').wait_for()
    tid(page, "checked-repair-preview-btn").click()
    tid(page, "checked-repair-send").click()
    page.locator('[data-test="checked-attempt"][data-check="pass"]').wait_for()


def drive_model_block(page):
    page.locator('[data-test="tutorial-action"][data-action="show-role"]').click()
    expect(tid(page, "roles-dialog")).to_be_visible()
    row = page.locator('[data-test="roles-row"][data-role="fast"]')
    expect(row).to_have_count(1)
    expect(row.locator('[data-key="profile"]')).to_have_value(tutorial_topics.ROLE_PROFILE)
    tid(page, "roles-close").click()
    expect(tid(page, "roles-dialog")).to_be_hidden()
    workshop_run(page)
    expect(tid(page, "ws-trial")).to_have_attribute("data-attempts", "2")
    expect(tid(page, "ws-output")).to_have_count(2)      # both named outputs come from the one accepted answer


def workshop_run(page):
    tid(page, "ws-run-test").click()
    tid(tid(page,"ws-dialog"),"ws-confirm").click()
    expect(tid(page, "ws-trial")).to_have_attribute("data-outcome", "complete")


def drive_tool_block(page):
    workshop_run(page)
    assert jsonio.decode_exact(tid(page, "ws-output").text_content()) == jsonio.decode_exact(EXACT)


def drive_script_block(page):
    workshop_run(page)
    assert tid(page, "ws-output").text_content() == "9"
    page.locator('[data-action="wait-script"]').click()
    expect(tid(page, "ws-list")).to_have_value("wait_for_stop")
    expect(tid(page, "ws-no-inputs")).to_be_visible()
    tid(page, "ws-run-test").click()
    tid(tid(page,"ws-dialog"),"ws-confirm").click()
    has(page, "ws-status", "data-state", "running")
    tid(page, "ws-stop").click()
    expect(tid(page, "ws-trial")).to_have_attribute("data-outcome", "stopped")


def drive_design(page):
    tid(page, "ws-save-revision").click()
    tid(tid(page,"ws-dialog"),"ws-confirm").click()
    has(page,"ws-status","data-state","saved")
    tid(page,"ws-test").click()
    tid(page,"ws-run-saved-examples").click()
    tid(tid(page,"ws-dialog"),"ws-confirm").click()
    expect(tid(page,"ws-trial")).to_have_count(2)
    expect(tid(page,"ws-trial").first).to_have_attribute("data-outcome","complete")
    tid(page,"ws-design").click()
    tid(page,"ws-choice-count-unit").select_option("characters")
    tid(page,"ws-apply-parameters").click()
    tid(page,"ws-save-revision").click()
    tid(tid(page,"ws-dialog"),"ws-confirm").click()
    has(page,"ws-status","data-state","saved")
    tid(page,"ws-compare").click()
    tid(page,"ws-choice-baseline-revision").select_option(index=1)
    tid(page,"ws-compare-versions").click()
    tid(tid(page,"ws-dialog"),"ws-confirm").click()
    expect(tid(page,"ws-trial")).to_have_count(4)
    expect(tid(page,"ws-trial").last).to_have_attribute("data-outcome","complete")


DRIVERS = {"chat": drive_chat, "document": drive_document, "tool": drive_tool, "contract": drive_contract,
           "repair": drive_repair, "model-block": drive_model_block, "tool-block": drive_tool_block,
           "script-block": drive_script_block, "workshop-design": drive_design}


def drive_assist(page):
    tid(page,"ws-assist").click()
    dialog=tid(page,"ws-dialog")
    tid(dialog,"ws-prepare-request").click()
    tid(dialog,"ws-confirm").click()
    if tid(page,"tutorial-box").get_attribute("data-topic") == "assist-describe":
        # Describe reviews a description of the saved managed fixture and saves only that description.
        expect(tid(dialog,"ws-description-proposal")).to_be_visible()
        tid(dialog,"ws-save-description").click()
        has(page,"ws-status","data-state","description_saved")
        has(page,"ws-standard-description","data-status","saved")
        return
    if tid(page,"tutorial-box").get_attribute("data-topic") == "assist-improve":
        tid(dialog,"ws-discard").click()
        has(page,"ws-status","data-state","proposal_discarded")
    else:
        tid(dialog,"ws-apply").click()
        has(page,"ws-status","data-state","proposal_applied")
        if tid(page,"tutorial-box").get_attribute("data-topic") == "assist-explain_failure":
            tid(dialog,"ws-close").click()


ASSIST_IDS = ["assist-"+a for a in ("create","improve","describe","suggest_examples","explain_failure")]
DRIVERS.update({i:drive_assist for i in ASSIST_IDS})


def test_tutorial_topics(ui, open_page):
    assert [t.id for t in tutorial_topics.TOPICS] == list(DRIVERS)       # every topic has a driver, in order
    page = open_page(ui)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    tid(page, "header-tutorial").click()
    has(page, "tutorial-box", "data-topic", tutorial_topics.TOPICS[0].id)
    assert page.locator('[data-test="tutorial-jump"] option').count() == len(tutorial_topics.TOPICS)
    for topic in tutorial_topics.TOPICS:
        tid(page, "tutorial-jump").select_option(topic.id)
        has(page, "tutorial-box", "data-topic", topic.id)
        has(page, "tutorial-box", "data-updated-in", topic.updated_in)
        has(page, "tutorial-status", "data-state", "ready")
        expect(page.locator("#tutorial-title")).to_contain_text(topic.updated_in)
        DRIVERS[topic.id](page)
    tid(page, "tutorial-next").click()
    has(page, "tutorial-status", "data-state", "finished")
    tid(page, "tutorial-exit").click()
    page.wait_for_url(ui.url)
    assert errors == []


def test_tutorial_topic_registry():
    topics = tutorial_topics.TOPICS
    assert [t.id for t in topics] == ["chat", "document", "tool", "contract", "repair", "model-block", "tool-block",
                                       "script-block", "workshop-design"] + ASSIST_IDS
    assert [t.updated_in for t in topics] == ["0.1.3"] * 3 + ["0.2.0"] * 11
    assert all(tutorial_topics.by_id(t.id) is t for t in topics)
    for bad in ("nope", None, 3, ["chat"]):
        with pytest.raises(KeyError):
            tutorial_topics.by_id(bad)
    assert set(tutorial_topics.public(topics[0])) == {"id", "title", "tip", "tab", "updated_in", "page", "target"}


# --- #113 ---------------------------------------------------------------------------------------------------
def started(ui):
    request(ui, "/api/tutorial/start", {})
    return ui.tutorial.child


def test_tutorial_workspace_administration_is_isolated(ui):
    child = started(ui)
    info = request(ui, "/api/workspaces")
    assert info["tutorial_active"] is True
    fail = failure_of(lambda: request(ui, "/api/workspaces/switch",
                                      {"format_version": 1, "name": ui.workspace.name}))
    assert fail.status == 409 and fail.body["code"] == "tutorial_active"
    assert get_state(child)["workspace"]["admin"] is False
    fail = failure_of(lambda: request(child, "/api/workspaces/delete",
                                      {"format_version": 1, "name": child.workspace.name,
                                       "confirm": child.workspace.name}))
    assert fail.status == 409 and fail.body["code"] == "workspace_admin_disabled"
    ui.tutorial.close()
    assert request(ui, "/api/workspaces")["tutorial_active"] is False


def test_tutorial_sandbox(ui, spawned, tmp_path):
    parent_files = snapshot(ui.workspace.path)
    child = started(ui)
    root = Path(ui.tutorial.folder.name)
    ws = child.workspace.path
    state = get_state(child)
    assert state["roles"]["map"] == {"fast": "tutorial-json"} and "tutorial-json" in state["profiles"]["names"]
    roles_text = (ws / "roles.json").read_text()
    assert "tutorial-json" in roles_text and "fast" in roles_text
    packaged = sorted(p.relative_to(BLOCKS_SOURCE) for p in BLOCKS_SOURCE.rglob("*") if p.is_file())
    assert {p.parts[0] for p in packaged} == set(tutorial_topics.BLOCKS)
    for rel in packaged:
        assert (ws / "blocks" / rel).read_bytes() == (BLOCKS_SOURCE / rel).read_bytes()
    assert sorted(b["name"] for b in request(child, "/api/blocks")["blocks"]) == sorted(tutorial_topics.BLOCKS)
    assert all(b["problem"] is None for b in request(child, "/api/blocks")["blocks"])
    assert snapshot(ui.workspace.path) == parent_files                     # the parent's workspace is untouched
    assert not (ui.workspace.path / "blocks").exists() and not (ui.workspace.path / "roles.json").exists()
    # topic errors and busy
    assert failure_of(lambda: request(child, "/api/tutorial/step", {"topic": "nope"})).status == 400
    assert failure_of(lambda: request(child, "/api/tutorial/step", {"step": 2})).status == 400
    switch_history(child, True)
    it = iter_events(run_block(child, "wait_for_stop", {}, raw=True))
    run_id = read_until(it, "started")[-1]["run_id"]
    read_until(it, "operation")
    assert wait_until(lambda: spawned)
    assert request(child, "/api/tutorial/step", {"topic": "chat"}) == {"busy": True}
    # Exit ends the group and removes the temporary folder, scratch folders included
    scratch = [p for p in root.rglob("scratch")]
    assert scratch, "the saved run has a scratch folder"
    ui.tutorial.close()
    assert wait_until(lambda: group_gone(spawned[0]))
    assert not root.exists() and ui.tutorial.child is None and ui.tutorial.pending is None
    list(it)
    assert snapshot(ui.workspace.path) == parent_files


@pytest.fixture
def retained_child(ui, spawned, monkeypatch):
    """A tutorial child whose waiting script cannot be ended (the double): its cleanup owner is retained."""
    monkeypatch.setattr(script_step, "KILL_GRACE_S", 0.1)
    monkeypatch.setattr(script_step, "RETAINED_POLL_S", 0.05)
    real_send = script_step.send_group
    child = started(ui)
    child.grace_s = 0.2                                                    # the bounded waits, lowered
    switch_history(child, True)
    monkeypatch.setattr(script_step, "send_group", lambda pgid, sig: None)
    it = iter_events(run_block(child, "wait_for_stop", {}, raw=True))
    read_until(it, "operation")
    assert wait_until(lambda: spawned)

    def release():
        monkeypatch.setattr(script_step, "send_group", real_send)
        kill_group(spawned[0])

    yield child, release, it
    monkeypatch.setattr(script_step, "send_group", real_send)
    kill_group(spawned[0])


def test_tutorial_bounded_exit_with_outstanding_cleanup(ui, retained_child, monkeypatch):
    child, release, it = retained_child
    session = ui.tutorial
    root, workspace_path = Path(session.folder.name), child.workspace.path
    cleaned = []
    real_cleanup = tempfile.TemporaryDirectory.cleanup
    monkeypatch.setattr(tempfile.TemporaryDirectory, "cleanup", lambda self: (cleaned.append(self.name), real_cleanup(self))[1])
    started_at = time.monotonic()
    session.close()                                                        # the real Exit path, through server_close
    assert time.monotonic() - started_at < 5
    assert child.socket.fileno() == -1                                     # the child's listening socket is closed
    assert session.child is None and session.pending is not None
    assert root.exists() and cleaned == []                                 # nothing deleted that a writer still owns
    with pytest.raises(WorkspaceInUse):                                    # the workspace lock is still held
        open_workspace(workspace_path.parent, workspace_path.name)
    get_state(ui)                                                          # the parent still answers requests
    fail = failure_of(lambda: request(ui, "/api/tutorial/start", {}))      # a second start is refused
    assert fail.status == 503 and fail.body["code"] == "busy"
    assert session.child is None and session.resources == [] and ui.state.scripts.in_flight == 0
    # the double reports the child gone: the next start reclaims the pending session and succeeds
    release()
    assert wait_until(lambda: child.state.scripts.in_flight == 0, 10)
    new = started(ui)
    assert new is not child and session.pending is None
    assert not root.exists() and root.name in [Path(c).name for c in cleaned]
    list(it)


def test_parent_shutdown_drains_the_pending_tutorial(ui, retained_child, monkeypatch):
    child, release, it = retained_child
    session = ui.tutorial
    root = Path(session.folder.name)
    session.close()
    assert session.pending is not None and root.exists()
    threading.Timer(0.5, release).start()
    ui.shutdown()
    assert ui.server_close() == []                                         # drains the pending child first
    assert session.pending is None and not root.exists()
    list(it)


def test_a_start_after_the_managers_closing_is_refused(ui):
    ui.tutorial.begin_shutdown()
    fail = failure_of(lambda: request(ui, "/api/tutorial/start", {}))
    assert fail.status == 503 and fail.body["code"] == "busy" and ui.tutorial.child is None


def test_tutorial_model_block_inherits_its_role_default_and_declares_two_outputs(ui):
    """The model-block topic demonstrates role defaults and named outputs, so the tutorial workspace must really
    carry both: the block sets no temperature of its own and the role's default reaches the prepared request."""
    request(ui, "/api/tutorial/start", {})
    child = ui.tutorial.child
    assert get_state(child)["roles"]["definitions"]["fast"]["task_defaults"] == {"temperature": 0}
    preview = request(child, "/api/workshop/preview", {"format_version": 1, "key": "extract_fields",
                                                       "inputs": {"report": "Record 1 has a ratio of 0.5."}})
    prepared = preview["prepared"]
    assert prepared["sources"]["temperature"] == "role_default"
    assert [o["name"] for o in prepared["outputs"]] == ["fields", "note"]
