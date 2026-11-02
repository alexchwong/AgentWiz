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
from conftest import (DONE, JSON_CONTRACT_NO_SCHEMA, Reply, answer, configure, delta, json_answer, run_folders,
                      save_contract, simple_tool, text_result, upload, wait_until, add_server)

SSE = {"Content-Type": "text/event-stream"}
FAST = {"idle_timeout_s": 60, "call_deadline_s": 60, "discovery_deadline_s": 60}
FRAGS = ["chat", "documents", "mcp-tools", "contracts", "checked-request", "history"]
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
        has(page, "tab-chat", "aria-selected", "true")                # unknown or empty selects Chat
    page = open_page(ui, "chat")
    tid(page, "tab-chat").focus()
    page.keyboard.press("ArrowRight")
    has(page, "tab-documents", "aria-selected", "true")
    page.keyboard.press("ArrowRight")
    has(page, "tab-mcp-tools", "aria-selected", "true")
    page.keyboard.press("ArrowLeft")
    has(page, "tab-documents", "aria-selected", "true")
    page.keyboard.press("End")
    has(page, "tab-history", "aria-selected", "true")
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
    say(page, "chat", "one")
    has(page, "chat-status", "data-state", "done")                   # committed, its save still pending
    has(page, "chat-status", "data-save", "pending")
    assert entered.wait(10)
    first = tid(page, "chat-status").get_attribute("data-attempt-id")
    tid(page, "chat-send").click()                                   # a second request in the same tab
    assert server.wait_holding(1)
    has(page, "chat-status", "data-state", "running")
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
    tid(page, "tab-chat").click()
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
    expect(page.locator('[data-test="checked-attempt"]')).to_have_count(2)
    has(page, "checked-repair", "data-repair", "not_repairable")
    sent = server.requests[-1]["body"]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]


# 87 --------------------------------------------------------------------------------------------------
def test_page_history(ui, server, open_page):
    server.reply(answer("saved answer"))
    configure(ui, server)
    page = open_page(ui, "chat")
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
def test_page_raises_no_script_errors(ui, open_page, context):
    problems = []
    context.on("page", lambda p: (p.on("pageerror", lambda e: problems.append(str(e))),
                                  p.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)))
    page = open_page(ui, "chat")
    for fragment in FRAGS:
        tid(page, f"tab-{fragment}").click()
        has(page, f"tab-{fragment}", "aria-selected", "true")
    tid(page, "tab-history").click()
    has(page, "history-count", "data-total", "0")
    assert problems == []
