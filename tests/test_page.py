"""The page's JavaScript in headless Chromium: techspec 0.1.3 tests #80-#88 (Part F, 12).

Elements are found by `data-test` attributes, never by markup or visible text. Assertions are on values
(field contents, clipboard text, what the fakes received) and on the machine-readable states of Section 11.6,
never on wording or layout. Waiting uses Playwright's conditions on those attributes; nothing sleeps, and the
orderings that matter are forced with the fake backend's holds and the code's hooks.

If Playwright or its Chromium cannot be provided, these tests FAIL with the reason: the gate is blocked, never
passed, and never passed by skipping (D51)."""
import json
import threading

import pytest
from playwright.sync_api import expect

from agentwiz import history, jsonio
from agentwiz.ui import server as server_module
from conftest import (DONE, JSON_CONTRACT_NO_SCHEMA, Reply, answer, configure, delta, group_gone, iter_events,
                      json_answer, read_until, request, run_block, run_folders, save_contract, save_profile,
                      save_roles, simple_tool, text_result, upload, wait_until, add_server, write_block, get_state)
from test_runner import setup_model
from test_scripts import PREAMBLE, write_script

SSE = {"Content-Type": "text/event-stream"}
FAST = {"idle_timeout_s": 60, "call_deadline_s": 60, "discovery_deadline_s": 60}
FRAGS = ["chat", "documents", "mcp-tools", "contracts", "checked-request", "history", "blocks"]
WAIT = 20_000          # only bounds a failing test


# --- fixtures ------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def browser():
    """One Chromium for the module. A missing Playwright or Chromium fails every test with the reason."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        pytest.fail(f"Playwright is not installed ({exc}): run scripts/dev_setup.sh. The page checks cannot run (D51).")
    pw = sync_playwright().start()
    try:
        chromium = pw.chromium.launch()
    except Exception as exc:  # noqa: BLE001 - whatever Playwright says is the reason
        pw.stop()
        pytest.fail("Chromium could not be launched (" + str(exc).strip().splitlines()[0] +
                    "): run `python -m playwright install chromium`. The page checks cannot run (D51).")
    yield chromium
    chromium.close()
    pw.stop()


@pytest.fixture
def context(browser):
    ctx = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
    ctx.set_default_timeout(WAIT)
    yield ctx
    ctx.close()


@pytest.fixture
def open_page(context):
    """open_page(ui, fragment="") -> a page on a fresh server, with the state loaded."""
    def opener(ui, fragment=""):
        page = context.new_page()
        page.goto(ui.url + (("#" + fragment) if fragment else ""))
        page.wait_for_function("document.getElementById('version').textContent !== ''", timeout=WAIT)
        return page

    return opener


def tid(page, name):
    return page.locator(f'[data-test="{name}"]')


def has(page, name, attribute, value):
    expect(tid(page, name)).to_have_attribute(attribute, value, timeout=WAIT)


def clip(page):
    return page.evaluate("navigator.clipboard.readText()")


def copy_of(page, button):
    """Press a Copy button and return what reached the clipboard."""
    tid(page, button).click()
    has(page, button, "data-copy", "copied")
    return clip(page)


def say(page, tab, prompt):
    tid(page, f"{tab}-prompt").fill(prompt)
    tid(page, f"{tab}-send").click()


# 80 --------------------------------------------------------------------------------------------------
def test_page_tabs(ui, open_page):
    for fragment in FRAGS:
        page = open_page(ui, fragment)
        for other in FRAGS:
            has(page, f"tab-{other}", "aria-selected", "true" if other == fragment else "false")
            if other == fragment:
                expect(tid(page, f"panel-{other}")).to_be_visible()
            else:
                expect(tid(page, f"panel-{other}")).to_be_hidden()
        page.reload()
        page.wait_for_function("document.getElementById('version').textContent !== ''", timeout=WAIT)
        has(page, f"tab-{fragment}", "aria-selected", "true")        # a reload keeps the tab
    for fragment in ("", "nonsense", "history/extra"):
        page = open_page(ui, fragment)
        has(page, "tab-workshop", "aria-selected", "true")
    page = open_page(ui, "chat")
    tid(page, "tab-chat").focus()
    page.keyboard.press("ArrowRight")
    has(page, "tab-documents", "aria-selected", "true")
    page.keyboard.press("ArrowRight")
    has(page, "tab-mcp-tools", "aria-selected", "true")
    page.keyboard.press("ArrowLeft")
    has(page, "tab-documents", "aria-selected", "true")
    page.keyboard.press("End")
    has(page, "tab-checked-request", "aria-selected", "true")
    page.keyboard.press("Home")
    has(page, "tab-chat", "aria-selected", "true")
    assert page.evaluate("location.hash") == "#chat"
    page.keyboard.press("ArrowRight")
    assert page.evaluate("location.hash") == "#documents"            # the fragment follows the selection


# 81 --------------------------------------------------------------------------------------------------
def test_page_chat_send_and_copy(ui, server, open_page):
    text = "héllo 9007199254740993"
    server.reply(answer(text))
    configure(ui, server)
    page = open_page(ui, "chat")
    say(page, "chat", "hi")
    has(page, "chat-status", "data-state", "done")
    has(page, "chat-status", "data-outcome", "complete")
    assert tid(page, "chat-output").text_content() == text
    assert copy_of(page, "chat-copy-output") == text
    line = copy_of(page, "chat-copy-final")
    final = json.loads(line)
    assert final["type"] == "final" and final["record"]["result"]["text"] == text
    assert tid(page, "chat-status").get_attribute("data-attempt-id") == final["attempt_id"]
    assert line == tid(page, "chat-final").text_content()           # exactly the line received
    assert server.requests[0]["body"]["messages"][-1] == {"role": "user", "content": "hi"}


def test_page_chat_stop_before_headers(ui, server, open_page):
    server.reply(Reply(hold_headers=True))
    configure(ui, server)
    page = open_page(ui, "chat")
    say(page, "chat", "hi")
    assert server.reached.wait(10)
    has(page, "chat-status", "data-state", "running")
    tid(page, "chat-stop").click()
    has(page, "chat-status", "data-outcome", "stopped")
    has(page, "chat-status", "data-state", "done")
    assert server.client_closed.wait(10)
    record = json.loads(tid(page, "chat-final").text_content())["record"]
    assert record["stop_mode"] == "immediate" and record["cancel_reason"] == "user_stop"
    expect(tid(page, "chat-send")).to_be_enabled()


def test_page_chat_stop_before_started(ui, server, open_page, monkeypatch):
    """Stop pressed while `started` has not arrived is held, then sent the moment the attempt ID arrives."""
    server.reply(Reply(hold_headers=True))
    configure(ui, server)
    original = server_module.Handler.write_event
    entered, gate = threading.Event(), threading.Event()

    def held(self, event):
        if event.get("type") == "started" and not gate.is_set():
            entered.set()
            assert gate.wait(20)
        return original(self, event)

    monkeypatch.setattr(server_module.Handler, "write_event", held)
    page = open_page(ui, "chat")
    say(page, "chat", "hi")
    assert entered.wait(10)
    tid(page, "chat-stop").click()
    has(page, "chat-status", "data-state", "stopping")
    assert tid(page, "chat-status").get_attribute("data-attempt-id") is None     # no ID yet: the Stop is pending
    gate.set()
    has(page, "chat-status", "data-outcome", "stopped")
    record = json.loads(tid(page, "chat-final").text_content())["record"]
    assert record["cancel_reason"] == "user_stop"


def test_page_chat_lost_stream_polls(ui, server, open_page, monkeypatch):
    """The stream ends without `final`: the page asks the server and shows the committed outcome."""
    server.reply(Reply(body=[delta("one"), delta("two"), delta(finish="stop"), DONE], headers=SSE))
    configure(ui, server)
    original = server_module.Handler.write_event
    seen = {"text": 0, "broken": False}

    def flaky(self, event):
        if event.get("type") == "text":
            seen["text"] += 1
            if seen["text"] >= 2:
                seen["broken"] = True
        if seen["broken"]:
            raise OSError("the page cannot be written to")
        return original(self, event)

    monkeypatch.setattr(server_module.Handler, "write_event", flaky)
    page = open_page(ui, "chat")
    polls = []
    page.on("request", lambda r: polls.append(r.url) if "/api/attempts/" in r.url else None)
    say(page, "chat", "hi")
    has(page, "chat-status", "data-state", "done")
    has(page, "chat-status", "data-outcome", "stopped")             # the server committed it when the page vanished
    assert polls, "the page never asked the server for the outcome"
    record = json.loads(tid(page, "chat-final").text_content())["record"]
    assert record["cancel_reason"] == "page_closed"
    expect(tid(page, "chat-send")).to_be_enabled()


def test_page_chat_old_save_never_updates_a_new_request(ui, server, open_page, monkeypatch):
    """An earlier attempt's `save` event updates only that attempt (correlation by attempt ID)."""
    from conftest import switch_history
    switch_history(ui, True)
    server.reply(answer("first"), Reply(body=[delta("sec")], headers=SSE, hold=True,
                                        after_release=[delta("ond"), delta(finish="stop"), DONE]))
    configure(ui, server)
    hold = threading.Event()
    entered = threading.Event()

    def block():
        entered.set()
        assert hold.wait(20)

    monkeypatch.setattr(history, "before_write", block)
    page = open_page(ui, "chat")
    page.evaluate("""() => {
        const el = document.querySelector('[data-test="chat-status"]');
        window.__log = [];
        new MutationObserver(() => window.__log.push({state: el.dataset.state, attempt: el.dataset.attemptId || null,
                                                       save: el.dataset.save || null}))
          .observe(el, {attributes: true, childList: true, characterData: true, subtree: true});
    }""")
    try:
        say(page, "chat", "one")
        has(page, "chat-status", "data-state", "done")                   # committed, its save still pending
        has(page, "chat-status", "data-save", "pending")
        assert entered.wait(10)
        first = tid(page, "chat-status").get_attribute("data-attempt-id")
        tid(page, "chat-send").click()                                   # a second request in the same tab
        assert server.wait_holding(1)
        has(page, "chat-status", "data-state", "running")
        # Running is set before fetch; wait until the page has handled the new started event.
        page.wait_for_function("""first => {
            const id = document.querySelector('[data-test="chat-status"]').dataset.attemptId;
            return !!id && id !== first;
        }""", arg=first, timeout=WAIT)
        second = tid(page, "chat-status").get_attribute("data-attempt-id")
        assert second and second != first
        hold.set()                                                       # the first attempt's save now settles
        page.wait_for_function(                                          # its stream has ended (a resource entry exists)
            "performance.getEntriesByType('resource').filter(e => e.name.endsWith('/api/complete')).length >= 1",
            timeout=WAIT)
        assert wait_until(lambda: ui.state.store.save_detail(first)[0] == "saved")
        page.evaluate("0")                                               # the page has handled what it was sent
        server.release.set()
        has(page, "chat-status", "data-state", "done")
        has(page, "chat-status", "data-save", "saved")
        assert tid(page, "chat-status").get_attribute("data-attempt-id") == second
        log = page.evaluate("window.__log")
        for entry in log:
            if entry["attempt"] == second and entry["state"] == "running":
                assert entry["save"] is None, log                        # the first attempt's save never reached it
    finally:
        hold.set()
        server.release.set()


# 82 --------------------------------------------------------------------------------------------------
def test_page_hidden_tab_keeps_running(ui, server, open_page):
    server.reply(Reply(body=[delta("par")], headers=SSE, hold=True,
                       after_release=[delta("tial"), delta(finish="stop"), DONE]))
    configure(ui, server)
    page = open_page(ui, "chat")
    say(page, "chat", "hi")
    expect(tid(page, "chat-output")).to_have_text("par", timeout=WAIT)
    has(page, "tab-chat", "data-state", "running")
    tid(page, "tab-contracts").click()
    has(page, "tab-contracts", "aria-selected", "true")
    expect(tid(page, "panel-chat")).to_be_hidden()
    has(page, "tab-chat", "data-state", "running")                   # the label shows it while hidden
    server.release.set()
    has(page, "tab-chat", "data-state", "idle")
    tid(page, "ws-workbench").click()
    has(page, "chat-status", "data-outcome", "complete")
    expect(tid(page, "chat-output")).to_have_text("partial")


# 83 --------------------------------------------------------------------------------------------------
def test_page_documents_copy(ui, open_page, tmp_path):
    path = tmp_path / "résumé 年報.md"
    data = "# Title\n\nbody 9007199254740993\n".encode()
    path.write_bytes(data)
    status, body = upload(ui, data, path.name)
    expected = body["insertion"]["text"]
    page = open_page(ui, "documents")
    tid(page, "documents-file").set_input_files(str(path))
    has(page, "documents-preview", "data-state", "ready")
    assert tid(page, "documents-preview-text").text_content() == expected
    got = copy_of(page, "documents-preview-copy")
    assert got == expected
    assert got.split("\n", 1)[0] == "[file " + json.dumps(path.name, ensure_ascii=False) + "]"
    tid(page, "documents-preview-discard").click()
    has(page, "documents-preview", "data-state", "idle")


# 84 --------------------------------------------------------------------------------------------------
def test_page_mcp_tools(ui, make_fake_mcp, open_page):
    fake = make_fake_mcp("modern", tools=[
        simple_tool("echo", behave=lambda call: call.result(text_result("hi there"))),
        simple_tool("exact", behave=lambda call: call.result({"content": [{"type": "text", "text": "n"}],
                                                              "structuredContent": {"n": 9007199254740993},
                                                              "isError": False}))])
    add_server(ui, "s", fake.url, **FAST)
    page = open_page(ui, "mcp-tools")
    expect(tid(page, "mcp-server").locator("option")).to_have_count(1)
    tid(page, "mcp-load").click()
    expect(tid(page, "mcp-tool").locator("option")).to_have_count(2)
    tid(page, "mcp-tool").select_option("echo")
    tid(page, "mcp-run").click()
    has(page, "mcp-status", "data-state", "done")
    has(page, "mcp-status", "data-outcome", "ok")
    preview = tid(page, "mcp-preview-text").text_content()
    got = copy_of(page, "mcp-preview-copy")
    assert got == preview and got.startswith("[tool s.echo ") and got.endswith("\nhi there")
    tid(page, "mcp-tool").select_option("exact")
    tid(page, "mcp-run").click()
    has(page, "mcp-status", "data-outcome", "ok")
    expect(tid(page, "mcp-structured")).to_have_attribute("data-state", "ready", timeout=WAIT)
    structured = copy_of(page, "mcp-structured-copy")
    assert structured.startswith("[tool s.exact ") and structured.endswith('{"n":9007199254740993}')


# 85 --------------------------------------------------------------------------------------------------
def test_page_contracts(ui, open_page):
    page = open_page(ui, "contracts")
    tid(page, "contracts-new-json").click()
    tid(page, "contracts-name").fill("fields_v1")
    tid(page, "contracts-advanced").click()
    tid(page, "contracts-editor").fill(JSON_CONTRACT_NO_SCHEMA)
    tid(page, "contracts-save").click()
    has(page, "contracts-editor", "data-state", "saved")
    page.reload()
    page.wait_for_function("document.getElementById('version').textContent !== ''", timeout=WAIT)
    row = page.locator('[data-test="contracts-row"][data-name="fields_v1"]')
    expect(row).to_have_count(1)
    row.click()
    expect(tid(page, "contracts-name")).to_have_value("fields_v1")
    saved = jsonio.decode_exact(tid(page, "contracts-editor").input_value())
    wanted = jsonio.decode_exact(JSON_CONTRACT_NO_SCHEMA)
    assert saved["checks"] == wanted["checks"]                       # 9007199254740993 and 0.10000000000000000001, exactly
    assert saved["format"] == "json"
    tid(page, "contracts-check-text").fill('{"id": 9007199254740993, "ratio": 1.5}')
    tid(page, "contracts-run-check").click()
    has(page, "contracts-check", "data-check", "fail")
    copied = copy_of(page, "contracts-copy-check")
    assert "Overall: fail" in copied and "$.ratio" in copied
    tid(page, "contracts-check-text").fill('{"id": 9007199254740993, "ratio": 0.10000000000000000001}')
    tid(page, "contracts-run-check").click()
    has(page, "contracts-check", "data-check", "pass")


# 86 --------------------------------------------------------------------------------------------------
def test_page_checked_request_repair(ui, server, open_page):
    save_contract(ui, "c", JSON_CONTRACT_NO_SCHEMA)
    server.reply(json_answer('{"id": 9007199254740993, "ratio": 1.5}'),
                 json_answer('{"id": 9007199254740993, "ratio": 0.10000000000000000001}'))
    configure(ui, server)
    page = open_page(ui, "checked-request")
    tid(page, "checked-contract").select_option("c")
    say(page, "checked", "give the fields")
    has(page, "checked-status", "data-state", "done")
    has(page, "checked-check", "data-check", "fail")
    has(page, "checked-repair", "data-repair", "allowed")
    first = tid(page, "checked-status").get_attribute("data-attempt-id")
    tid(page, "checked-repair-preview-btn").click()
    preview = tid(page, "checked-repair-preview")
    expect(preview).to_contain_text("Problems found", timeout=WAIT)
    assert '"ratio": 1.5' in preview.text_content()                  # the failed answer goes back whole
    tid(page, "checked-repair-send").click()
    has(page, "checked-check", "data-check", "pass")
    second = tid(page, "checked-status").get_attribute("data-attempt-id")
    assert second != first and second.rsplit("-", 1)[0] == first.rsplit("-", 1)[0] and second.endswith("-2")
    attempts = page.locator('[data-test="checked-attempt"]')
    expect(attempts).to_have_count(2)
    assert [attempts.nth(i).get_attribute("data-repair-reason") for i in range(2)] == ["", "check_failed"]   # #86
    has(page, "checked-repair", "data-repair", "not_repairable")
    sent = server.requests[-1]["body"]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]


# 87 --------------------------------------------------------------------------------------------------
def test_page_history(ui, server, open_page):
    server.reply(answer("saved answer"))
    configure(ui, server)
    page = open_page(ui, "chat")
    if not tid(page, "header-save-runs").is_visible():
        tid(page, "ws-settings").click()
    tid(page, "header-save-runs").check()
    expect(tid(page, "header-saving")).to_be_visible()
    say(page, "chat", "keep this")
    has(page, "chat-status", "data-save", "saved")
    run_id = tid(page, "chat-status").get_attribute("data-attempt-id").rsplit("-", 1)[0]
    tid(page, "tab-history").click()
    row = page.locator(f'[data-test="history-row"][data-run-id="{run_id}"]')
    expect(row).to_have_count(1)
    row.click()
    expect(tid(page, "history-detail")).to_have_attribute("data-run-id", run_id)
    assert copy_of(page, "history-copy-answer") == "saved answer"
    assert len(run_folders(ui)) == 1
    tid(page, "history-delete").click()
    tid(page, "history-delete-confirm").click()
    expect(page.locator('[data-test="history-row"]')).to_have_count(0)
    assert run_folders(ui) == []


# 88 --------------------------------------------------------------------------------------------------
def test_page_stale_revision(ui, server, open_page):
    server.reply(answer("ok"))
    configure(ui, server)
    page = open_page(ui, "chat")
    configure(ui, server, model="other")                              # another tab, in effect
    say(page, "chat", "hi")
    has(page, "chat-status", "data-state", "stale")
    assert server.requests == []                                      # nothing was sent, nothing resent
    expect(page.locator("#summary")).to_contain_text("other", timeout=WAIT)     # the page reloaded the state
    expect(tid(page, "chat-prompt")).to_have_value("hi")             # its inputs are kept
    tid(page, "chat-send").click()
    has(page, "chat-status", "data-state", "done")
    assert server.requests[-1]["body"]["model"] == "other"


# extra: the script raises nothing on load or when every tab is opened -----------------------------------------
def test_page_workspaces_clone_switch_and_delete(ui, open_page):
    original = ui.workspace.name
    page = open_page(ui)
    tid(page, "header-workspaces").click()
    expect(tid(page, "workspaces-dialog")).to_be_visible()
    expect(page.locator('[data-test="workspaces-row"]')).to_have_count(1)
    has(page, "workspaces-dialog", "data-state", "idle")
    tid(page, "workspaces-name").fill("clone")
    tid(page, "workspaces-clone").click()
    expect(page.locator('[data-test="workspaces-row"][data-workspace="clone"]')).to_have_count(1)
    tid(page, "workspaces-selection").select_option("clone")
    tid(page, "workspaces-switch").click()
    expect(tid(page, "header-workspace")).to_have_attribute("data-workspace", "clone", timeout=WAIT)
    tid(page, "header-workspaces").click()
    tid(page, "workspaces-selection").select_option(original)
    tid(page, "workspaces-switch").click()
    expect(tid(page, "header-workspace")).to_have_attribute("data-workspace", original, timeout=WAIT)
    tid(page, "header-workspaces").click()
    tid(page, "workspaces-reset").click()
    tid(page, "workspaces-confirm-name").fill(original)
    tid(page, "ws-confirm").click()
    expect(tid(page, "header-workspace")).to_have_attribute("data-workspace", original, timeout=WAIT)
    tid(page, "header-workspaces").click()
    tid(page, "workspaces-selection").select_option("clone")
    tid(page, "workspaces-delete").click()
    tid(page, "workspaces-confirm-name").fill("clone")
    tid(page, "ws-confirm").click()
    expect(page.locator('[data-test="workspaces-row"][data-workspace="clone"]')).to_have_count(0)
    expect(page.locator('[data-test="workspaces-row"]')).to_have_count(1)


def test_page_raises_no_script_errors(ui, open_page, context):
    problems = []
    context.on("page", lambda p: (p.on("pageerror", lambda e: problems.append(str(e))),
                                  p.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)))
    page = open_page(ui, "chat")
    for fragment in FRAGS:
        if fragment == "blocks":
            tid(page, "tab-workshop").click()
        elif fragment != "history":
            tid(page, "ws-workbench").click()
        tid(page, f"tab-{fragment}").click()
        has(page, f"tab-{fragment}", "aria-selected", "true")
    tid(page, "tab-history").click()
    has(page, "history-count", "data-total", "0")
    assert problems == []


# --- Part G: the Blocks tab and the Roles dialog (techspec 0.1.4, tests #108-#111) --------------------------------
OBJ_BLOCK = {"format_version": 1, "name": "b", "description": "shows the exact numbers",
             "inputs": {"text": {"contract": {"format": "text"}}, "obj": {"contract": {"format": "json"}}},
             "outputs": {"out": {"contract": {"format": "json"}}},
             "steps": [{"id": "s", "kind": "model", "role": "fast", "system": {"file": "prompts/system.md"},
                        "prompt": {"file": "prompts/user.md"}, "response_mode": "named_json"}]}
OBJ_FILES = {"prompts/system.md": "You are terse.", "prompts/user.md": "Say {{inputs.text}} for {{inputs.obj.id}}"}
EXACT = '{"id":9007199254740993,"ratio":0.10000000000000000001}'


def profile_and_role(ui, server):
    save_profile(ui, "p", model="m", kind="openai_compat", base_url=server.url + "/v1", provider="")
    save_roles(ui, {"fast": "p"})


def input_of(page, name):
    return page.locator(f'[data-test="blocks-input"][data-input="{name}"]')


def pick(page, block):
    page.locator(f'[data-test="blocks-row"][data-block="{block}"]').click()
    expect(page.locator('[data-test="blocks-file"]').first).to_be_visible()


# 108 -----------------------------------------------------------------------------------------------------
def test_page_blocks(ui, server, open_page):
    profile_and_role(ui, server)
    write_block(ui, "b", OBJ_BLOCK, OBJ_FILES)
    server.reply(answer("not json at all"), json_answer('{"out":{"id": 9007199254740993, "ratio": 0.10000000000000000001}}'))
    page = open_page(ui, "blocks")
    row = page.locator('[data-test="blocks-row"][data-block="b"]')
    expect(row).to_have_count(1)
    assert row.get_attribute("data-problem") == ""
    pick(page, "b")
    assert sorted(f.get_attribute("data-path") for f in page.locator('[data-test="blocks-file"]').all()) == [
        "block.json", "prompts/system.md", "prompts/user.md"]
    assert input_of(page, "text").evaluate("e => e.tagName") == "TEXTAREA"                 # a text field
    assert input_of(page, "obj").evaluate("e => e.tagName") == "TEXTAREA"                  # a JSON editor
    expect(tid(page, "blocks-run")).to_be_enabled()
    input_of(page, "text").fill("hello")
    input_of(page, "obj").fill('{"id": 9007199254740993}')
    tid(page, "blocks-run").click()
    has(page, "blocks-status", "data-state", "done")
    has(page, "blocks-status", "data-outcome", "complete")
    attempts = page.locator('[data-test="blocks-attempt"]')
    expect(attempts).to_have_count(2)
    assert [a.get_attribute("data-check") for a in attempts.all()] == ["fail", "pass"]
    assert [a.get_attribute("data-repair-reason") for a in attempts.all()] == ["", "check_failed"]
    has(page, "blocks-trace", "data-attempts", "2")
    assert server.requests[0]["body"]["messages"][1]["content"] == "Say hello for 9007199254740993"   # exact number
    copied = copy_of(page, "blocks-copy-output")
    assert jsonio.decode_exact(copied) == jsonio.decode_exact(EXACT)
    # a run held at the backend: the Blocks tab says it is running while another tab is shown
    server.reply(Reply(hold_headers=True, body=[delta('{"id":1}'), delta(finish="stop"), DONE],
                       headers=SSE))
    tid(page, "blocks-run").click()
    has(page, "tab-blocks", "data-state", "running")
    has(page, "blocks-status", "data-state", "running")
    expect(tid(page, "blocks-run")).to_be_disabled()
    tid(page, "ws-workbench").click()
    has(page, "tab-blocks", "data-state", "running")
    has(page, "panel-blocks", "data-state", "running")
    server.release.set()
    tid(page, "tab-workshop").click()
    tid(page, "tab-blocks").click()
    has(page, "blocks-status", "data-state", "done")
    has(page, "tab-blocks", "data-state", "idle")


# 109 -----------------------------------------------------------------------------------------------------
def test_page_blocks_stop_and_reattach(ui, server, open_page, spawned):
    write_script(ui, PREAMBLE + "time.sleep(60)\n", timeout_s=30)
    page = open_page(ui, "blocks")
    pick(page, "sc")
    input_of(page, "a").fill("go")
    tid(page, "blocks-run").click()
    tid(tid(page,"ws-dialog"),"ws-use-selection").click()
    assert wait_until(lambda: spawned)
    has(page, "blocks-status", "data-state", "running")
    has(page, "blocks-operation", "data-kind", "script")
    tid(page, "blocks-stop").click()
    has(page, "blocks-status", "data-outcome", "stopped")
    assert wait_until(lambda: group_gone(spawned[0]))
    # a held model block run, reattached after a reload
    setup_model(ui, server)
    server.reply(Reply(hold_headers=True, body=[delta('{"out":"fine"}'), delta(finish="stop"), DONE], headers=SSE))
    it = iter_events(run_block(ui, "b", {"text": "x"}, raw=True))
    run_id = read_until(it, "started")[-1]["run_id"]
    page = open_page(ui, "blocks")
    active = page.locator('[data-test="blocks-active-run"]')
    expect(active).to_have_count(1)
    assert active.get_attribute("data-run-id") == run_id
    tid(page, "blocks-reattach").click()
    has(page, "blocks-status", "data-state", "polling")
    has(page, "blocks-status", "data-run-id", run_id)
    server.release.set()
    has(page, "blocks-status", "data-state", "done")
    has(page, "blocks-status", "data-outcome", "complete")
    list(it)


# 110 -----------------------------------------------------------------------------------------------------
def test_page_blocks_stale_and_problem(ui, server, open_page):
    profile_and_role(ui, server)
    saved = save_contract(ui, "tc", '{"format": "text"}')["contract"]
    block = {**OBJ_BLOCK, "inputs": {"text": {"contract": "tc"}, "obj": {"contract": {"format": "json"}}}}
    folder = write_block(ui, "b", block, OBJ_FILES)
    write_block(ui, "broken", "{ this is not json")
    server.reply(json_answer('{"out":'+EXACT+'}'))
    page = open_page(ui, "blocks")
    expect(page.locator('[data-test="blocks-row"][data-problem]:not([data-problem=""])')).to_have_count(1)
    pick(page, "b")
    input_of(page, "text").fill("hello")
    input_of(page, "obj").fill('{"id": 1}')
    (folder / "prompts" / "user.md").write_text("Edited: {{inputs.text}} {{inputs.obj.id}}")      # after it was shown
    tid(page, "blocks-run").click()
    has(page, "blocks-status", "data-state", "stale")
    assert server.requests == []                                                     # nothing was resent
    expect(input_of(page, "text")).to_have_value("hello")                           # the input texts are kept
    tid(page, "blocks-run").click()
    has(page, "blocks-status", "data-outcome", "complete")
    assert server.requests[0]["body"]["messages"][1]["content"] == "Edited: hello 1"
    # the saved contract an input uses changes: the old form is stale, the reloaded form reflects the new contract
    request(ui, "/api/contracts/save", {"name": "tc", "revision": saved["revision"],
                                        "text": '{"format": "text", "checks": [{"type": "contains", "value": "X"}]}'})
    tid(page, "blocks-run").click()
    has(page, "blocks-status", "data-state", "stale")
    assert len(server.requests) == 1
    assert "contains" in page.locator('[data-test="blocks-contract"][data-section="inputs"][data-name="text"]').text_content()
    tid(page, "blocks-run").click()
    has(page, "blocks-status", "data-outcome", "rejected")                          # "hello" lacks the X
    assert len(server.requests) == 1
    # a block with a problem can be read but not run
    page.locator('[data-test="blocks-row"][data-block="broken"]').click()
    expect(tid(page, "blocks-problem")).to_be_visible()
    expect(tid(page, "blocks-run")).to_be_disabled()


# 111 -----------------------------------------------------------------------------------------------------
def test_page_roles(ui, server, open_page):
    save_profile(ui, "p", model="m", kind="openai_compat", base_url=server.url + "/v1", provider="")
    page = open_page(ui, "chat")
    if not tid(page, "header-roles").is_visible():
        tid(page, "ws-settings").click()
    tid(page, "header-roles").click()
    has(page, "roles-dialog", "data-state", "idle")
    row = page.locator('[data-test="roles-row"][data-role="fast"]')
    expect(row).to_have_attribute("data-status", "unconfigured")
    row.locator('[data-key="profile"]').select_option("p")
    expect(row).to_have_attribute("data-status", "ready")
    tid(page, "roles-save").click()
    has(page, "roles-dialog", "data-state", "saved")
    assert get_state(ui)["roles"]["map"] == {"fast": "p"}
    page = open_page(ui, "chat")                                                    # reload: the mapping is shown
    if not tid(page, "header-roles").is_visible():
        tid(page, "ws-settings").click()
    tid(page, "header-roles").click()
    row = page.locator('[data-test="roles-row"][data-role="fast"]')
    expect(row).to_have_count(1)
    expect(row.locator('[data-key="profile"]')).to_have_value("p")
    # roles changed elsewhere: saving from the stale dialog is refused and the dialog shows what is saved
    save_roles(ui, {"fast": "p", "slow": "p"})
    tid(page, "roles-save").click()
    has(page, "roles-dialog", "data-state", "stale")
    expect(page.locator('[data-test="roles-row"]')).to_have_count(1)
    expect(tid(page, "roles-selection").locator('option')).to_have_count(4)
    assert get_state(ui)["roles"]["map"] == {"fast": "p", "slow": "p"}
    # the profile is deleted: its roles are unresolved
    request(ui, "/api/profiles/delete", {"name": "p", "revision": get_state(ui)["revision"]})
    page = open_page(ui, "chat")
    if not tid(page, "header-roles").is_visible():
        tid(page, "ws-settings").click()
    tid(page, "header-roles").click()
    row = page.locator('[data-test="roles-row"]')
    expect(row).to_have_attribute("data-unresolved", "true")
    tid(page, "roles-selection").select_option(label="slow")
    expect(row).to_have_attribute("data-role", "slow")
    expect(row).to_have_attribute("data-unresolved", "true")


@pytest.mark.parametrize("width", [390, 1280])
def test_workshop_design_save_compare_and_review(ui, open_page, width):
    if width==1280:
        from conftest import switch_history
        switch_history(ui,True)
    page = open_page(ui)
    page.set_viewport_size({"width":width,"height":850})
    tid(page,"ws-new-block").click()
    dialog=tid(page,"ws-dialog")
    tid(dialog,"ws-field-block-key").fill("counter")
    tid(dialog,"ws-choice-start-with").select_option("count_text")
    tid(dialog,"ws-create-draft").click()
    tid(page,"ws-save-revision").click()
    tid(dialog,"ws-confirm").click()
    has(page,"ws-status","data-state","saved")
    tid(page,"ws-test").click()
    tid(page,"ws-field-text").fill("one two")
    tid(page,"ws-run-test").click()
    tid(dialog,"ws-confirm").click()
    expect(tid(page,"ws-output")).to_have_text("2")
    tid(page,"ws-mark-correct").click()
    has(page,"ws-trial","data-correctness","correct")
    tid(page,"ws-save-as-example").click()
    tid(dialog,"ws-field-example-label").fill("Two words")
    tid(dialog,"ws-save").click()
    tid(page,"ws-save-example-collection").click()
    tid(page,"ws-compare").click()
    page.locator('[data-test="ws-example-choice"][data-example$="-1"]').uncheck()
    page.locator('[data-test="ws-example-choice"][data-example$="-2"]').uncheck()
    tid(page,"ws-compare-versions").click()
    tid(dialog,"ws-confirm").click()
    expect(tid(page,"ws-output")).to_have_count(2)
    page.reload()
    expect(tid(page,"ws-compare")).to_have_attribute("aria-pressed","true")
    if width==1280:
        tid(page,"ws-recent-runs-and-comparisons").click()
        record=page.locator('[data-test="ws-comparison-record"][data-arms="2"]')
        tid(record,"ws-open-comparison").click()
        expect(tid(page,"ws-output")).to_have_count(2)
        tid(page,"ws-recent-runs-and-comparisons").click()
        tid(record,"ws-delete-comparison").click()
        expect(record).to_have_count(0)
        tid(tid(page,"ws-dialog"),"ws-close").click()
        assert len(request(ui,"/api/runs")["runs"])==3


@pytest.mark.parametrize("assisted", [False, True])
def test_workshop_new_block_validation_and_roles(ui, server, open_page, assisted):
    # Both entry points must keep invalid input editable and then create the same draft.
    setup_model(ui, server)
    save_roles(ui, {"writer": "p"})
    page = open_page(ui)
    tid(page, "ws-new-block").click()
    dialog = tid(page, "ws-dialog")
    key = tid(dialog, "ws-field-block-key")
    role = tid(dialog, "ws-choice-model-role")
    expect(role).to_have_value("writer")
    expect(tid(dialog, "ws-choice-mcp-server")).to_be_hidden()
    create = tid(dialog, "ws-create-with-assistance" if assisted else "ws-create-draft")
    for invalid in ("", "My new block"):
        key.fill(invalid)
        create.click()
        has(dialog, "ws-create-error", "data-code", "invalid_key")
        expect(key).to_have_value(invalid)
    key.fill("new_writer")
    # A refused preview must leave the dialog and values available for correction.
    page.route("**/api/workshop/preview", lambda route: route.fulfill(
        status=409, content_type="application/json",
        body=json.dumps({"error": "Workshop request refused", "code": "stale"})), times=1)
    create.click()
    has(dialog, "ws-create-error", "data-code", "stale")
    expect(key).to_have_value("new_writer")
    create.click()
    if assisted:
        expect(tid(dialog, "ws-choice-assistant-role")).to_have_value("writer")
        tid(dialog, "ws-cancel").click()
    else:
        expect(dialog).to_have_count(0)
    expect(tid(page, "ws-field-role")).to_have_value("writer")
    expect(tid(page, "ws-field-instructions")).not_to_have_value("")


def test_roles_filter_profiles_and_preserve_unsaved_edits(ui, server, open_page):
    save_profile(ui, "local", model="m", kind="openai_compat", base_url=server.url + "/v1", provider="")
    save_profile(ui, "cloud", model="remote", kind="openrouter", base_url="", provider="")
    page = open_page(ui)
    tid(page, "ws-settings").click()
    tid(page, "header-roles").click()
    tid(page, "roles-selection").select_option(label="assistant")
    row = page.locator('[data-test="roles-row"][data-role="assistant"]')
    expect(row.locator('[data-key="profile"] option')).to_have_count(2)
    row.locator('[data-key="description"]').fill("My cloud helper")
    row.locator('[data-key="location"]').select_option("cloud")
    expect(row.locator('[data-key="profile"] option')).to_have_count(2)
    row.locator('[data-key="profile"]').select_option("cloud")
    tid(page, "roles-create-profile").click()
    expect(tid(page, "roles-dialog")).not_to_be_visible()
    page.keyboard.press("Escape")
    expect(tid(page, "roles-dialog")).to_be_visible()
    expect(row.locator('[data-key="description"]')).to_have_value("My cloud helper")
    expect(row.locator('[data-key="location"]')).to_have_value("cloud")
    expect(row.locator('[data-key="profile"]')).to_have_value("cloud")
    tid(page, "roles-defaults").click()
    expect(page.locator('[data-test="roles-row"]')).to_have_count(1)
    expect(tid(page, "roles-selection").locator('option')).to_have_count(3)
    tid(page, "roles-save").click()
    has(page, "roles-dialog", "data-state", "saved")
    saved = get_state(ui)["roles"]
    assert saved["map"] == {"assistant": "cloud"}
    assert saved["definitions"]["assistant"] == {"description": "My cloud helper", "location": "cloud", "family": "General"}


def test_roles_single_editor_filters_families_and_retains_other_edits(ui, open_page):
    initial = get_state(ui)["roles"]
    definitions = {**initial["definitions"], **{
        f"extra-{i}": {"description": "", "location": "local", "family": "General"} for i in range(30)}}
    request(ui, "/api/roles/save", {"roles": {}, "definitions": definitions, "revision": initial["revision"]})
    page = open_page(ui)
    tid(page, "ws-settings").click()
    tid(page, "header-roles").click()
    row = page.locator('[data-test="roles-row"]')
    expect(row).to_have_count(1)
    row.locator('[data-key="description"]').fill("Unsaved fast purpose")
    tid(page, "roles-default-temperature").fill("0")
    tid(page, "roles-default-reasoning").select_option("low")
    tid(page, "roles-selection").select_option(label="assistant")
    expect(row).to_have_count(1)
    row.locator('[data-key="location"]').select_option("cloud")
    tid(page, "roles-new-family").fill("Customer support")
    tid(page, "roles-add-family").click()
    expect(tid(page, "roles-family")).to_have_value("Customer support")
    tid(page, "roles-location-filter").select_option("local")
    expect(tid(page, "roles-selection").locator('option')).to_have_count(32)
    expect(row.locator('[data-key="description"]')).to_have_value("Unsaved fast purpose")
    tid(page, "roles-family-filter").select_option("Coding & engineering")
    expect(row).to_have_count(0)
    tid(page, "roles-location-filter").select_option("cloud")
    tid(page, "roles-family-filter").select_option("Customer support")
    expect(row).to_have_count(1)
    expect(row).to_have_attribute("data-role", "assistant")
    tid(page, "roles-add").click()
    row.locator('[data-key="role"]').fill("support")
    expect(tid(page, "roles-family")).to_have_value("Customer support")
    expect(row.locator('[data-key="location"]')).to_have_value("cloud")
    tid(page, "roles-save").click()
    has(page, "roles-dialog", "data-state", "saved")
    saved = get_state(ui)["roles"]["definitions"]
    assert saved["fast"]["description"] == "Unsaved fast purpose"
    assert saved["fast"]["task_defaults"] == {"temperature": 0, "reasoning": "low"}
    assert saved["assistant"]["family"] == saved["support"]["family"] == "Customer support"
    tid(page, "roles-close").click()
    tid(page, "header-roles").click()
    tid(page, "roles-location-filter").select_option("cloud")
    tid(page, "roles-family-filter").select_option("Customer support")
    expect(tid(page, "roles-selection").locator('option')).to_have_count(2)
    expect(row).to_have_count(1)


def test_workshop_new_tool_discovery(ui, make_fake_mcp, open_page):
    # Only advertised tools from the currently selected server can bind a new tool block.
    first = make_fake_mcp("modern", tools=[simple_tool("echo"), simple_tool("search")])
    empty = make_fake_mcp("modern")
    empty.tools.clear()
    add_server(ui, "first", first.url, **FAST)
    add_server(ui, "empty", empty.url, **FAST)
    page = open_page(ui)
    tid(page, "ws-new-block").click()
    dialog = tid(page, "ws-dialog")
    tid(dialog, "ws-field-block-key").fill("new_tool")
    tid(dialog, "ws-choice-start-with").select_option("call_tool")
    expect(tid(dialog, "ws-choice-model-role")).to_be_hidden()
    tid(dialog, "ws-create-draft").click()
    has(dialog, "ws-create-error", "data-code", "tool_required")
    server = tid(dialog, "ws-choice-mcp-server")
    tool = tid(dialog, "ws-choice-tool-name")
    server.select_option("first")
    has(dialog, "ws-tool-discovery", "data-state", "ready")
    expect(tool.locator("option")).to_have_count(2)
    tool.select_option("search")
    server.select_option("empty")
    has(dialog, "ws-tool-discovery", "data-state", "empty")
    expect(tool).to_be_disabled()
    expect(tool.locator("option")).to_have_count(0)
    empty.override("tools/list", lambda call: call.error(-32603, "discovery failed"))
    tid(dialog, "ws-refresh-tools").click()
    has(dialog, "ws-tool-discovery", "data-state", "error")
    tid(dialog, "ws-create-draft").click()
    has(dialog, "ws-create-error", "data-code", "tool_required")
    server.select_option("first")
    has(dialog, "ws-tool-discovery", "data-state", "ready")
    tool.select_option("search")
    tid(dialog, "ws-create-draft").click()
    expect(dialog).to_have_count(0)
    expect(tid(page, "ws-field-server")).to_have_value("first")
    expect(tid(page, "ws-field-tool-name")).to_have_value("search")
    assert not first.methods("tools/call") and not empty.methods("tools/call")


def test_workshop_extractor_revision_journey(ui, server, open_page):
    setup_model(ui,server)
    old=json.dumps({"product":"notebooks","quantity":300})
    revised=json.dumps({"product":"notebooks"})
    server.reply(answer('{"out":'+old+'}'),answer('{"out":'+old+'}'),answer('{"out":'+revised+'}'))
    page=open_page(ui)
    dialog=tid(page,"ws-dialog")
    tid(page,"ws-new-block").click()
    tid(dialog,"ws-field-block-key").fill("shop_extract")
    tid(dialog,"ws-choice-start-with").select_option("extract_fields")
    tid(dialog,"ws-create-draft").click()
    original=tid(page,"ws-field-instructions").input_value()
    tid(page,"ws-save-revision").click()
    tid(dialog,"ws-confirm").click()
    has(page,"ws-status","data-state","saved")
    tid(page,"ws-test").click()
    tid(page,"ws-save-example-collection").click()
    tid(page,"ws-choice-example").select_option("extract_fields-2")
    tid(page,"ws-run-test").click()
    tid(dialog,"ws-confirm").click()
    has(page,"ws-trial","data-correctness","incorrect")
    tid(page,"ws-design").click()
    instruction="Omit quantity when absent. Read {{inputs.text}}"
    tid(page,"ws-field-instructions").fill(instruction)
    tid(page,"ws-test").click()
    tid(page,"ws-preview-instructions").click()
    expect(dialog).to_contain_text("Omit quantity when absent. Read send some blue notebooks")
    tid(dialog,"ws-close").click()
    tid(page,"ws-design").click()
    tid(page,"ws-save-revision").click()
    tid(dialog,"ws-confirm").click()
    has(page,"ws-status","data-state","saved")
    tid(page,"ws-compare").click()
    tid(page,"ws-choice-baseline-revision").select_option(index=1)
    page.locator('[data-test="ws-example-choice"][data-example$="-1"]').uncheck()
    tid(page,"ws-compare-versions").click()
    tid(dialog,"ws-confirm").click()
    expect(tid(page,"ws-trial").nth(1)).to_have_attribute("data-correctness","correct")
    expect(tid(page,"ws-trial").nth(0)).to_have_attribute("data-correctness","incorrect")
    tid(page,"ws-design").click()
    tid(page,"ws-revision-actions").click()
    tid(dialog,"ws-choice-revision").select_option(index=1)
    tid(dialog,"ws-restore-to-draft").click()
    expect(tid(page,"ws-field-instructions")).to_have_value(original)
    assert len(server.requests)==3


@pytest.mark.needs_checks
def test_workshop_structured_inputs_keep_exact_literals_across_views_and_reload(ui, open_page):
    """The shared field form must not coerce long numbers or merge absence/null/empty values."""
    from test_workshop import send, plan_save
    p=send(ui,'preview',starter='select_fields',key='exact_form')
    draft=send(ui,'preview',draft=p['draft'],edit={'kind':'starter_parameters','parameters':{
        'mappings':[{'source_path':['data'],'destination':'payload','required':True}]}})['draft']
    definition=jsonio.decode_exact(draft['definition_text'])
    schema={'type':'object','properties':{'n':{'type':'integer'},'d':{'type':'number'},
        'yes':{'type':'boolean'},'nil':{'type':['string','null']},'empty':{'type':'string'},
        'missing':{'type':'string'}},'required':['n','d','yes','nil','empty'],'additionalProperties':False}
    definition['inputs']={'data':{'contract':{'format':'json','schema':schema}}}
    draft['definition_text']=jsonio.encode_exact(definition)
    plan_save(ui,draft)
    from test_workshop import http_method
    http_method(ui,'blocks/exact_form/examples','PUT',{'expected_revision':None,'examples':[
        {'id':'obsolete','version':1,'label':'Old shape','inputs':{'data':'{"n":1,"d":1,"yes":false,"nil":null,"empty":"","old":9007199254740993}'},
         'expectation':{'kind':'review','criterion':'Review'},'origin':'manual','verified':False}]})
    page=open_page(ui,'workshop/exact_form/test')
    tid(page,'ws-choice-example').select_option('obsolete')
    expect(tid(page,'ws-unmapped-fields')).to_have_count(1)
    tid(page,'ws-remove-unmapped-fields').click()
    expect(tid(page,'ws-unmapped-fields')).to_have_count(0)
    def node(path):
        return page.locator('[data-test="ws-input-node"][data-port="data"][data-path="'+path+'"]')
    tid(node('n'),'ws-field-value').fill('9007199254740993')
    tid(node('d'),'ws-field-value').fill('0.12345678901234567890123456789')
    tid(node('yes'),'ws-choice-value').select_option('false')
    tid(node('nil'),'ws-choice-nil').select_option('null')
    tid(node('missing'),'ws-choice-missing').select_option('omit')
    tid(page,'ws-design').click()
    tid(page,'ws-test').click()
    expect(tid(node('n'),'ws-field-value')).to_have_value('9007199254740993')
    tid(page,'ws-run-test').click()
    tid(tid(page,'ws-dialog'),'ws-confirm').click()
    expected=jsonio.decode_exact('{"payload":{"n":9007199254740993,"d":0.12345678901234567890123456789,"yes":false,"nil":null,"empty":""}}')
    assert jsonio.decode_exact(tid(page,'ws-output').text_content())==expected
    page.reload()
    expect(tid(page,'ws-trial')).to_have_attribute('data-outcome','complete')
    assert jsonio.decode_exact(tid(page,'ws-output').text_content())==expected


def test_workshop_invalid_design_keeps_inputs_and_located_save_error(ui, open_page):
    """A broken prompt reference must not masquerade as missing files or erase Test inputs."""
    page = open_page(ui)
    tid(page, 'ws-new-block').click()
    dialog = tid(page, 'ws-dialog')
    tid(dialog, 'ws-field-block-key').fill('invalid_design')
    tid(dialog, 'ws-choice-start-with').select_option('write_text')
    tid(dialog, 'ws-create-draft').click()
    tid(page, 'ws-field-field-name').first.fill('message')
    tid(page, 'ws-save-revision').click()
    has(page, 'ws-status', 'data-code', 'block_invalid')
    tid(page, 'ws-test').click()
    has(page, 'ws-validation', 'data-state', 'invalid')
    expect(tid(page, 'ws-field-message')).to_be_visible()
    tid(page, 'ws-field-message').fill('retained input')
    expect(tid(page, 'ws-run-test')).to_be_disabled()
    tid(page, 'ws-design').click()
    tid(page, 'ws-field-instructions').fill('Read {{inputs.message}}')
    tid(page, 'ws-field-field-name').last.fill('answer')
    tid(page, 'ws-test').click()
    expect(tid(page, 'ws-validation')).to_have_count(0)
    expect(tid(page, 'ws-field-message')).to_have_value('retained input')
    tid(page, 'ws-save-revision').click()
    tid(dialog, 'ws-confirm').click()
    has(page, 'ws-status', 'data-state', 'saved')
    from conftest import request
    saved = request(ui, '/api/workshop/blocks/invalid_design/revisions')
    draft = request(ui, '/api/workshop/blocks/invalid_design/revisions/' + saved['current'])['draft']
    definition = jsonio.decode_exact(draft['definition_text'])
    assert list(definition['outputs']) == ['answer']
    assert definition['steps'][0]['response_mode'] == 'named_json'


def test_workshop_one_off_and_pending_examples_survive_cancel_and_write_failure(ui, open_page):
    """One-off runs need no saved block; pending examples survive both refusal paths."""
    from conftest import request
    page = open_page(ui)
    tid(page, 'ws-new-block').click()
    dialog = tid(page, 'ws-dialog')
    tid(dialog, 'ws-field-block-key').fill('pending_examples')
    tid(dialog, 'ws-choice-start-with').select_option('count_text')
    tid(dialog, 'ws-create-draft').click()
    tid(page, 'ws-test').click()
    tid(page, 'ws-field-text').fill('one two three')
    tid(page, 'ws-run-test').click()
    tid(dialog, 'ws-confirm').click()
    expect(tid(page, 'ws-output')).to_have_text('3')
    assert request(ui, '/api/workshop/blocks/pending_examples/examples')['examples'] == []
    tid(page, 'ws-save-as-example').click()
    tid(dialog, 'ws-field-example-label').fill('Three words')
    tid(dialog, 'ws-save').click()
    has(page, 'ws-examples-state', 'data-state', 'unsaved')
    selected = tid(page, 'ws-choice-example').input_value()
    tid(page, 'ws-design').click()
    tid(page, 'ws-test').click()
    expect(tid(page, 'ws-choice-example')).to_have_value(selected)
    expect(tid(page, 'ws-field-text')).to_have_value('one two three')
    tid(page, 'ws-save-example-collection').click()
    tid(dialog, 'ws-cancel').click()
    has(page, 'ws-examples-state', 'data-state', 'unsaved')
    tid(page, 'ws-save-example-collection').click()
    tid(dialog, 'ws-save-revision-first').click()
    tid(dialog, 'ws-confirm').click()
    has(page, 'ws-examples-state', 'data-state', 'saved')
    tid(page, 'ws-edit-selected-example').click()
    tid(dialog, 'ws-field-label').fill('Edited words')
    tid(dialog, 'ws-save').click()
    has(page, 'ws-examples-state', 'data-state', 'unsaved')
    route = '**/api/workshop/blocks/pending_examples/examples'
    page.route(route, lambda r: r.fulfill(status=409, content_type='application/json', body='{"code":"read_only","error":"refused"}') if r.request.method == 'PUT' else r.continue_())
    tid(page, 'ws-save-example-collection').click()
    has(page, 'ws-status', 'data-code', 'read_only')
    has(page, 'ws-examples-state', 'data-state', 'unsaved')
    expect(tid(page, 'ws-choice-example')).to_have_value(selected)
    page.unroute(route)
    tid(page, 'ws-save-example-collection').click()
    has(page, 'ws-examples-state', 'data-state', 'saved')
    saved = request(ui, '/api/workshop/blocks/pending_examples/examples')
    example = next(e for e in saved['examples'] if e['id'] == selected)
    assert example['label'] == 'Edited words' and example['inputs'] == {'text': 'one two three'}
