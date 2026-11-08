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
from conftest import (DONE, JSON_CONTRACT, JSON_CONTRACT_NO_SCHEMA, Reply, answer, configure, delta, group_gone, iter_events,
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


def test_openrouter_key_dialog(ui, open_page, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    page = open_page(ui)
    has(page, "header-openrouter-key", "data-state", "missing")
    tid(page, "header-openrouter-key").click()
    tid(page, "openrouter-key-save").click()
    expect(tid(page, "openrouter-key-dialog")).to_be_visible()
    tid(page, "openrouter-key-input").fill("session-secret-123")
    tid(page, "openrouter-key-save").click()
    expect(tid(page, "openrouter-key-dialog")).not_to_be_visible()
    has(page, "header-openrouter-key", "data-state", "present")
    tid(page, "header-openrouter-key").click()
    expect(tid(page, "openrouter-key-input")).to_have_value("")
    tid(page, "openrouter-key-input").fill("discarded-secret")
    tid(page, "openrouter-key-cancel").click()
    tid(page, "header-openrouter-key").click()
    expect(tid(page, "openrouter-key-input")).to_have_value("")
    tid(page, "openrouter-key-clear").click()
    has(page, "header-openrouter-key", "data-state", "missing")
    monkeypatch.setenv("OPENROUTER_API_KEY", "environment-secret")
    page.reload()
    has(page, "header-openrouter-key", "data-state", "present")


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
    tid(page, "tab-workshop").click()                                # Contracts is a Workshop section (overhaul 15.1)
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


# 85 / overhaul C27-C29: Workshop → Contracts, the shared editor, ports and synthesis -----------------------------------
def editor_row(page, path, prefix="contracts"):
    return page.locator(f'[data-test="{prefix}-row"][data-path="{path}"]')


def row_detail(page, path, prefix="contracts"):
    """The selected row's controls; opened with More when they are not shown yet."""
    detail = page.locator(f'[data-test="{prefix}-row-detail"][data-path="{path}"]')
    if detail.count() == 0:
        tid(editor_row(page, path, prefix), f"{prefix}-row-more").click()
    expect(detail).to_have_count(1)
    return detail


def add_field(page, parent, name, kind, prefix="contracts"):
    detail = row_detail(page, parent, prefix)
    tid(detail, f"{prefix}-new-name").fill(name)
    tid(detail, f"{prefix}-new-type").select_option(kind)
    tid(detail, f"{prefix}-add-property").click()
    expect(editor_row(page, "/".join(x for x in (parent, name) if x), prefix)).to_have_count(1)


def change(locator, value):
    """Fill a control and commit it (the editor applies a targeted edit on change)."""
    locator.fill(value)
    locator.blur()


def save_contract_from_page(page, state="saved"):
    tid(page, "contracts-save").click()
    tid(tid(page, "ws-dialog"), "ws-confirm").click()
    has(page, "contracts-status", "data-state", state)


def open_port(card):
    """Open a port's contract drawer (it stays open while the block re-renders)."""
    if tid(card, "port-drawer").get_attribute("data-state") != "open":
        tid(card, "port-edit").click()
    has(card, "port-drawer", "data-state", "open")


def close_port(card):
    """Done closes the drawer; the settings column is reachable again."""
    if tid(card, "port-drawer").get_attribute("data-state") == "open":
        tid(card, "port-done").click()
    has(card, "port-drawer", "data-state", "closed")


def open_more(page):
    """Clone, Reset and Delete live in the Workspaces dialog's More actions."""
    more = tid(page, "workspaces-more")
    if more.get_attribute("open") is None:
        more.locator("summary").click()


def pick_labels(dialog, choice="not-private"):
    """Model and tool starters need an explicit port-label choice; other starters show none."""
    if tid(dialog, "ws-port-labels").is_visible():
        tid(dialog, "ws-port-label-" + choice).check()


def new_block(page, key, starter="write_text"):
    dialog = tid(page, "ws-dialog")
    tid(page, "ws-new-block").click()
    tid(dialog, "ws-field-block-key").fill(key)
    tid(dialog, "ws-choice-start-with").select_option(starter)
    pick_labels(dialog)
    tid(dialog, "ws-create-draft").click()


BIG = "9007199254740993"
FINE = "0.10000000000000000001"


def test_page_contract_library_builds_nested_exact_contracts_and_refuses_invalid_source(ui, open_page):
    """C27: a List of Group with a nullable Choice and exact bounds, built in the readable editor; advanced source
    passes the same recogniser; the saved definition keeps every literal exactly across reload and Check sample."""
    page = open_page(ui, "contracts")
    has(page, "tab-workshop", "aria-selected", "true")
    expect(tid(page, "panel-contracts")).to_be_visible()
    has(page, "contracts-editor", "data-format", "text")                          # a new draft starts as Text
    tid(page, "contracts-name").fill("order_lines")
    tid(page, "contracts-format").select_option("structured")
    has(page, "contracts-editor", "data-format", "structured")
    add_field(page, "", "lines", "List")
    tid(editor_row(page, "lines/[]"), "contracts-row-type").select_option("Group")    # a plain item type: no loss preview
    expect(editor_row(page, "lines/[]")).to_have_attribute("data-type", "Group")
    add_field(page, "lines/[]", "product", "Text")
    add_field(page, "lines/[]", "quantity", "Number")
    add_field(page, "lines/[]", "status", "Choice")
    tid(editor_row(page, "lines/[]/status"), "contracts-row-nullable").check()
    detail = row_detail(page, "lines/[]/status")
    editor = tid(page, "contracts-editor")
    digest = editor.get_attribute("data-digest")
    # Type the next choice before the earlier edit returns and rebuilds the editor.
    held_edits = []
    edit_url = "**/api/contracts/edit"
    page.route(edit_url, lambda route: held_edits.append(route), times=1)
    with page.expect_request(edit_url):
        change(tid(detail, "contracts-choice").first, "open")
    tid(detail, "contracts-new-choice").fill("closed")
    assert len(held_edits) == 1
    held_edits[0].continue_()
    expect(editor).not_to_have_attribute("data-digest", digest)
    detail = row_detail(page, "lines/[]/status")
    expect(tid(detail, "contracts-choice").first).to_have_value("open")
    expect(tid(detail, "contracts-new-choice")).to_have_value("closed")
    tid(detail, "contracts-add-choice").click()
    expect(tid(row_detail(page, "lines/[]/status"), "contracts-choice").nth(1)).to_have_value("closed")
    quantity = row_detail(page, "lines/[]/quantity")
    digest = editor.get_attribute("data-digest")
    change(quantity.locator('[data-test="contracts-bound"][data-keyword="minimum"]'), FINE)
    # Each accepted edit rebuilds the controls. Wait for that rebuild before editing the next bound.
    expect(editor).not_to_have_attribute("data-digest", digest)
    digest = editor.get_attribute("data-digest")
    change(quantity.locator('[data-test="contracts-bound"][data-keyword="maximum"]'), BIG)
    expect(editor).not_to_have_attribute("data-digest", digest)
    expect(quantity.locator('[data-test="contracts-bound"][data-keyword="maximum"]')).to_have_value(BIG)
    has(page, "contracts-editor", "data-state", "valid")

    # Advanced source is the same subset: an unsupported keyword stays in the buffer and cannot be saved.
    tid(page, "contracts-advanced").click()
    source = tid(page, "contracts-source").input_value()
    assert FINE in source and BIG in source
    tid(page, "contracts-source").fill(source.replace('"type":"number"', '"type":"number","multipleOf":2', 1))
    tid(page, "contracts-apply-source").click()
    has(page, "contracts-editor", "data-state", "invalid")
    expect(tid(page, "contracts-source-error")).to_have_count(1)
    tid(page, "contracts-save").click()
    has(page, "contracts-status", "data-state", "error")
    expect(tid(page, "contracts-guided-blocked")).to_have_count(1)
    tid(page, "contracts-discard-source").click()
    has(page, "contracts-editor", "data-state", "valid")
    save_contract_from_page(page)

    saved = jsonio.decode_exact(request(ui, "/api/contracts/order_lines")["definition_text"])
    item = saved["schema"]["properties"]["lines"]["items"]
    assert item["properties"]["status"] == {"type": ["string", "null"], "enum": ["open", "closed", None]}
    assert item["properties"]["quantity"]["minimum"] == jsonio.decode_exact(FINE)
    assert item["properties"]["quantity"]["maximum"] == int(BIG)
    assert item["required"] == ["product", "quantity", "status"] and item["additionalProperties"] is False
    page.reload()
    page.wait_for_function("document.getElementById('version').textContent !== ''", timeout=WAIT)
    page.locator('[data-test="contracts-row"][data-name="order_lines"]').click()
    bound = row_detail(page, "lines/[]/quantity").locator('[data-test="contracts-bound"][data-keyword="minimum"]')
    expect(bound).to_have_value(FINE)                                                 # exact, not a JavaScript number
    tid(page, "contracts-check-text").fill('{"lines":[{"product":"pen","quantity":' + BIG + ',"status":null}]}')
    tid(page, "contracts-run-check").click()
    has(page, "contracts-check", "data-check", "pass")
    tid(page, "contracts-check-text").fill('{"lines":[{"product":"pen","quantity":0.1,"status":"open"}]}')
    tid(page, "contracts-run-check").click()
    has(page, "contracts-check", "data-check", "fail")                                 # 0.1 < 0.10000000000000000001
    copied = copy_of(page, "contracts-copy-check")
    assert "Overall: fail" in copied


def test_page_saved_contract_pin_survives_appends_and_select_newer_is_explicit(ui, open_page):
    """C27: a port selects a saved revision by exact pin; appending a revision does not move it; Select newer is an
    unsaved block edit until the block is saved."""
    first = save_contract(ui, "label_choice", '{"format":"json","schema":{"type":"string","enum":["a","b"]}}')
    page = open_page(ui)
    new_block(page, "pinned")
    output = page.locator('[data-test="ws-port"][data-side="outputs"]')
    open_port(output)
    tid(output, "port-mode").select_option("saved")
    tid(output, "port-contract").select_option("label_choice")
    has(output, "port-pin", "data-revision", "1")
    definition = save_new_revision(page, ui, "pinned")
    assert definition["outputs"]["out"]["contract"] == {"name": "label_choice", "revision": 1, "digest": first["digest"]}
    second = save_contract(ui, "label_choice", '{"format":"json","schema":{"type":"string","enum":["a","b","c"]}}',
                           token=first["file_token"])
    close_port(output)
    tid(page, "ws-advanced-settings").click()
    tid(page, "ws-refresh-design-fields").click()
    open_port(output)
    has(output, "port-pin", "data-revision", "1")                                      # the pin did not move
    expect(tid(output, "port-newer")).to_be_visible()
    assert saved_definition(ui, "pinned")["outputs"]["out"]["contract"]["revision"] == 1
    tid(output, "port-select-newer").click()
    has(output, "port-pin", "data-revision", "2")
    assert saved_definition(ui, "pinned")["outputs"]["out"]["contract"]["revision"] == 1   # not saved yet
    definition = save_new_revision(page, ui, "pinned")
    assert definition["outputs"]["out"]["contract"] == {"name": "label_choice", "revision": 2, "digest": second["digest"]}
    # Used by shows the current pin; the library opens from the port.
    tid(output, "port-open").click()
    expect(tid(page, "panel-contracts")).to_be_visible()
    expect(tid(page, "contracts-name")).to_have_value("label_choice")
    row = page.locator('[data-test="contracts-used-row"][data-block="pinned"][data-current="true"]')
    expect(row).to_have_attribute("data-revision", "2")


def test_page_contract_stale_save_keeps_draft_restore_families_and_used_by_refusal(ui, open_page):
    """C28: a stale save keeps the draft; family filter and grouping; historical restore appends; deletion of a used
    contract is refused with its blockers."""
    first = save_contract(ui, "notes", '{"format":"text","min_length":1}', family="Clinical")
    save_contract(ui, "other", '{"format":"json"}', family="Commercial")
    page = open_page(ui, "contracts")
    tid(page, "contracts-family-filter").select_option("Clinical")
    expect(tid(page, "contracts-row")).to_have_count(1)
    tid(page, "contracts-family-filter").select_option("")
    tid(page, "contracts-search").fill("oth")
    expect(tid(page, "contracts-row")).to_have_count(1)
    tid(page, "contracts-search").fill("")
    page.locator('[data-test="contracts-row"][data-name="notes"]').click()
    expect(tid(page, "contracts-name")).to_have_value("notes")
    # Another tab appends revision 2 while this draft is open.
    save_contract(ui, "notes", '{"format":"text","min_length":2}', token=first["file_token"], family="Clinical")
    change(tid(page, "contracts-max-length"), "500")
    has(page, "panel-contracts", "data-dirty", "true")
    tid(page, "contracts-save").click()
    tid(tid(page, "ws-dialog"), "ws-confirm").click()
    has(page, "contracts-status", "data-state", "stale")
    expect(tid(page, "contracts-max-length")).to_have_value("500")                    # the draft is kept
    assert request(ui, "/api/contracts/notes")["latest"] == 2                          # nothing was resubmitted
    tid(page, "contracts-refresh").click()
    expect(tid(page, "contracts-max-length")).to_have_value("500")
    save_contract_from_page(page)
    detail = request(ui, "/api/contracts/notes")
    assert detail["latest"] == 3 and jsonio.decode_exact(detail["definition_text"]) == {"format": "text", "min_length": 1, "max_length": 500}
    # Grouping is a separate save and changes no revision.
    tid(page, "contracts-family").select_option("Commercial")
    tid(page, "contracts-save-family").click()
    has(page, "contracts-status", "data-state", "family_saved")
    assert request(ui, "/api/contracts/notes")["family"] == "Commercial" and request(ui, "/api/contracts/notes")["latest"] == 3
    # Viewing an older revision does not move latest; Restore appends a copy.
    tid(page, "contracts-revision").select_option("2")
    expect(tid(page, "contracts-basis-note")).not_to_be_empty()
    tid(page, "contracts-restore").click()
    tid(tid(page, "ws-dialog"), "ws-confirm").click()
    has(page, "contracts-status", "data-state", "restored")
    restored = request(ui, "/api/contracts/notes")
    assert restored["latest"] == 4 and restored["revisions"][3]["digest"] == restored["revisions"][1]["digest"]
    # A pinned block blocks deletion; nothing cascades.
    write_block(ui, "uses_notes", {"format_version": 3, "name": "uses_notes", "inputs": {
        "text": {"contract": {"name": "notes", "revision": 4, "digest": restored["digest"]}}},
        "outputs": {"out": {"contract": {"format": "text"}}},
        "steps": [{"id": "s", "kind": "model", "role": "fast", "prompt": {"text": "{{inputs.text}}"}, "response_mode": "named_json"}]}, {})
    tid(page, "contracts-refresh").click()
    expect(page.locator('[data-test="contracts-used-row"][data-block="uses_notes"]')).to_have_count(1)
    tid(page, "contracts-delete").click()
    tid(tid(page, "ws-dialog"), "ws-delete").click()
    has(page, "contracts-status", "data-code", "contract_in_use")
    expect(page.locator('[data-test="contracts-blocker"][data-block="uses_notes"]')).to_have_count(1)
    assert request(ui, "/api/contracts/notes")["latest"] == 4


def test_page_contract_synthesis_model_error(ui, server, open_page, monkeypatch):
    import modelspell
    from agentwiz import model_call
    from test_contract_overhaul import setup_role
    setup_role(ui, server)
    description = 'OpenRouter API key is not set'

    def missing_key(*args, **kwargs):
        raise modelspell.AuthError(description)

    monkeypatch.setattr(model_call, 'call_model', missing_key)
    page = open_page(ui, 'contracts')
    tid(page, 'contracts-format').select_option('any')
    tid(page, 'contracts-synth-task').fill('Classify a support message.')
    tid(page, 'contracts-synth-plan').click()
    tid(page, 'ws-dialog').get_by_role('button', name='Confirm', exact=True).click()
    has(page, 'contracts-synth-status', 'data-state', 'failed')
    has(page, 'contracts-synth-status', 'data-code', 'AuthError')
    expect(tid(page, 'contracts-synth-status')).to_have_text('AuthError: ' + description)
    assert not server.requests


def test_page_contract_synthesis_apply_save_and_inline_attachment_are_separate(ui, server, open_page):
    """C29: synthesis preview, Apply draft and Save are separate actions; Save inline as reusable does not change
    the port unless chosen; dirty navigation offers Save / Discard / Stay."""
    from test_contract_overhaul import GOOD, envelope, setup_role
    setup_role(ui, server)
    server.reply(envelope(GOOD))
    page = open_page(ui, "contracts")
    dialog = tid(page, "ws-dialog")
    tid(page, "contracts-name").fill("triage")
    expect(tid(page, "contracts-synth-plan")).to_be_disabled()                      # Text is authored by hand
    tid(page, "contracts-format").select_option("any")
    has(page, "contracts-editor", "data-format", "any")
    expect(tid(page, "contracts-any-json")).to_have_attribute("data-state", "weak")
    tid(page, "contracts-synth-task").fill("Classify a support message into a queue.")
    tid(page, "contracts-synth-temperature").fill("0")
    tid(page, "contracts-synth-reasoning").select_option("low")
    with page.expect_request(lambda r: r.url.endswith("/api/contracts/synthesis/plan")) as sent:
        tid(page, "contracts-synth-plan").click()
    planned = json.loads(sent.value.post_data)
    assert planned["settings_text"] == {"temperature": "0", "reasoning": "low"} and planned["role"] == "write_contract"
    assert planned["definition_text"] is not None and "max_tokens" not in planned
    has(page, "contracts-synth-status", "data-code", "task_setting")              # the stand-in is OpenAI-compatible
    expect(dialog).to_have_count(0)                                              # refused before any review or call
    tid(page, "contracts-synth-reasoning").select_option("")
    with page.expect_request(lambda r: r.url.endswith("/api/contracts/synthesis/plan")) as sent:
        tid(page, "contracts-synth-plan").click()
    assert json.loads(sent.value.post_data)["settings_text"] == {"temperature": "0"}
    expect(tid(dialog, "contracts-synth-review")).to_be_visible()
    tid(dialog, "ws-confirm").click()
    has(page, "contracts-synth-status", "data-state", "proposal")
    expect(tid(page, "contracts-synth-notes")).to_contain_text(GOOD["notes"][0])
    has(page, "contracts-editor", "data-format", "any")                               # nothing applied yet
    assert not (ui.workspace.path / "contracts" / "triage.json").exists()             # nothing saved
    tid(page, "contracts-synth-apply").click()
    has(page, "contracts-synth-status", "data-state", "applied")
    has(page, "contracts-editor", "data-format", "structured")
    assert not (ui.workspace.path / "contracts" / "triage.json").exists()
    save_contract_from_page(page)
    saved = request(ui, "/api/contracts/triage")
    assert saved["origin"] == "synthesized" and saved["basis"]["role"] == "write_contract"
    assert jsonio.decode_exact(saved["definition_text"])["schema"] == GOOD["schema"]

    # Dirty navigation: Stay keeps the draft, Discard leaves it.
    save_contract(ui, "second", '{"format":"text"}')
    tid(page, "contracts-refresh").click()
    has(page, "contracts-status", "data-state", "idle")
    change(tid(page, "contracts-description"), "Changed in this tab")
    has(page, "panel-contracts", "data-dirty", "true")
    page.locator('[data-test="contracts-row"][data-name="second"]').click()
    tid(dialog, "ws-stay").click()
    expect(tid(page, "contracts-name")).to_have_value("triage")
    expect(tid(page, "contracts-description")).to_have_value("Changed in this tab")
    page.locator('[data-test="contracts-row"][data-name="second"]').click()
    tid(dialog, "ws-discard").click()
    expect(tid(page, "contracts-name")).to_have_value("second")
    assert request(ui, "/api/contracts/triage")["latest"] == 1

    # Save inline as reusable: a new contract; the port changes only when chosen, and only the block save keeps it.
    tid(page, "ws-section-blocks").click()
    new_block(page, "attach")
    output = page.locator('[data-test="ws-port"][data-side="outputs"]')
    open_port(output)
    change(tid(output, "port-min-length"), "3")
    tid(output, "port-save-reusable").click()
    tid(dialog, "port-reusable-name").fill("reply_text")
    tid(dialog, "port-reusable-use").uncheck()
    tid(dialog, "ws-save-contract").click()
    has(output, "port-msg", "data-state", "saved_reusable")
    has(output, "port-card", "data-mode", "inline")
    assert jsonio.decode_exact(request(ui, "/api/contracts/reply_text")["definition_text"]) == {"format": "text", "min_length": 3}
    tid(output, "port-save-reusable").click()
    tid(dialog, "port-reusable-name").fill("reply_text_2")
    tid(dialog, "ws-save-contract").click()                       # publishing and using it take one dialog
    has(output, "port-card", "data-mode", "saved")
    has(output, "port-pin", "data-name", "reply_text_2")
    assert request(ui, "/api/blocks")["total"] == 0                                    # the block is not saved yet
    definition = save_new_revision(page, ui, "attach")
    assert definition["outputs"]["out"]["contract"]["name"] == "reply_text_2"


# 86 --------------------------------------------------------------------------------------------------
def test_page_checked_request_repair(ui, server, open_page):
    save_contract(ui, "c", JSON_CONTRACT)                       # ratio has maximum 1: a structural failure
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
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]      # the generated contract section
    assert "AgentWiz output protocol" in sent[0]["content"]
    assert server.requests[0]["body"]["messages"][0] == sent[0]                     # the repair reuses the capture


def test_page_checked_request_dispatches_the_reviewed_numbered_revision(ui, server, open_page):
    """Spec 13.1 / C17: Latest shows its concrete number; an explicit older revision is what is previewed and sent."""
    first = save_contract(ui, "c", JSON_CONTRACT)
    save_contract(ui, "c", JSON_CONTRACT_NO_SCHEMA, token=first["file_token"])            # revision 2: Any JSON
    server.reply(json_answer('{"id": 1, "ratio": 0.5}'))
    configure(ui, server)
    page = open_page(ui, "checked-request")
    tid(page, "checked-contract").select_option("c")
    expect(tid(page, "checked-revision")).to_have_value("latest")
    has(page, "checked-pin", "data-revision", "2")                                    # the concrete latest is shown
    tid(page, "checked-revision").select_option("1")
    has(page, "checked-pin", "data-revision", "1")
    has(page, "checked-pin", "data-digest", first["digest"])
    tid(page, "checked-contract-preview").click()
    preview = tid(page, "checked-contract-instructions")
    expect(preview).to_have_attribute("data-revision", "1")
    section = preview.text_content()
    assert "AgentWiz output protocol" in section and '"maximum":1' in section and "Any JSON" not in section
    say(page, "checked", "give the fields")
    has(page, "checked-status", "data-state", "done")
    system = server.requests[-1]["body"]["messages"][0]["content"]
    assert section.split("\n\n", 1)[1] in system                                      # exactly the previewed section


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
    open_more(page)
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
    open_more(page)
    tid(page, "workspaces-reset").click()
    tid(page, "workspaces-confirm-name").fill(original)
    tid(page, "ws-confirm").click()
    expect(tid(page, "header-workspace")).to_have_attribute("data-workspace", original, timeout=WAIT)
    tid(page, "header-workspaces").click()
    tid(page, "workspaces-selection").select_option("clone")
    open_more(page)
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
        if fragment in ("blocks", "contracts"):
            tid(page, "tab-workshop").click()
        elif fragment != "history":
            tid(page, "ws-workbench").click()
        tid(page, f"tab-{fragment}").click()
        has(page, f"tab-{fragment}", "aria-selected", "true")
    tid(page, "tab-history").click()
    has(page, "history-count", "data-total", "0")
    assert problems == []


# --- Part G: the Blocks tab and the Roles dialog (techspec 0.1.4, tests #108-#111) --------------------------------
OBJ_BLOCK = {"format_version": 3, "name": "b", "description": "shows the exact numbers",
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
    saved = save_contract(ui, "tc", '{"format": "text"}')
    pin = {"name": "tc", "revision": 1, "digest": saved["digest"]}
    block = {**OBJ_BLOCK, "inputs": {"text": {"contract": pin}, "obj": {"contract": {"format": "json"}}}}
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
    # a newer revision of the saved contract does not move the block's pin; an explicit new pin makes the shown
    # form stale, and the reloaded form reflects the newly pinned revision
    newer = save_contract(ui, "tc", '{"format": "text", "contains": ["X"]}', saved["file_token"])
    block["inputs"]["text"]["contract"] = {"name": "tc", "revision": newer["revision"], "digest": newer["digest"]}
    write_block(ui, "b", block, OBJ_FILES | {"prompts/user.md": "Edited: {{inputs.text}} {{inputs.obj.id}}"})
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
def test_page_roles_flags_missing_defaults(ui, server, open_page):
    from test_contract_overhaul import setup_role
    setup_role(ui, server)  # Existing workspace defines only write_contract.
    page = open_page(ui, 'chat')
    if not tid(page, 'header-roles').is_visible():
        tid(page, 'ws-settings').click()
    tid(page, 'header-roles').click()
    notice = tid(page, 'roles-missing-defaults')
    expect(notice).to_be_visible()
    expect(notice).to_have_attribute('data-missing', 'fast,assistant,reasoning,write_block_description')
    tid(page, 'roles-defaults').click()
    expect(notice).to_be_visible()  # Still absent from the saved workspace until Save.
    tid(page, 'roles-save').click()
    has(page, 'roles-dialog', 'data-state', 'saved')
    expect(notice).to_be_hidden()
    assert get_state(ui)['roles']['map'] == {'write_contract': 'p'}
    assert set(get_state(ui)['roles']['definitions']) == {'fast', 'assistant', 'reasoning', 'write_contract', 'write_block_description'}


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
    expect(tid(page, "roles-selection").locator('option')).to_have_count(6)    # five defaults (write_contract, write_block_description) + slow
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
    pick_labels(dialog)
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
    pick_labels(dialog)
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
    expect(tid(page, "ws-choice-model-role")).to_have_value("writer")
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
    expect(tid(page, "settings-dialog")).to_be_visible()                  # the same profile editor, over Roles
    page.keyboard.press("Escape")
    expect(tid(page, "settings-dialog")).not_to_be_visible()
    expect(tid(page, "roles-dialog")).to_be_visible()
    expect(row.locator('[data-key="description"]')).to_have_value("My cloud helper")
    expect(row.locator('[data-key="location"]')).to_have_value("cloud")
    expect(row.locator('[data-key="profile"]')).to_have_value("cloud")
    tid(page, "roles-defaults").click()
    expect(page.locator('[data-test="roles-row"]')).to_have_count(1)
    expect(tid(page, "roles-selection").locator('option')).to_have_count(5)
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
    expect(tid(page, "roles-selection").locator('option')).to_have_count(33)
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
    pick_labels(dialog)
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
    pick_labels(dialog)
    tid(dialog, "ws-create-draft").click()
    has(dialog, "ws-create-error", "data-code", "tool_required")
    server.select_option("first")
    has(dialog, "ws-tool-discovery", "data-state", "ready")
    tool.select_option("search")
    pick_labels(dialog)
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
    pick_labels(dialog)
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


def test_workshop_numbered_revision_selection_keeps_dirty_edits_and_names(ui, open_page):
    """Inline history must protect edits and restore without overwriting saved generations."""
    page = open_page(ui)
    dialog = tid(page, 'ws-dialog')
    tid(page, 'ws-new-block').click()
    tid(dialog, 'ws-field-block-key').fill('numbered')
    tid(dialog, 'ws-choice-start-with').select_option('write_text')
    pick_labels(dialog)
    tid(dialog, 'ws-create-draft').click()
    original = tid(page, 'ws-field-instructions').input_value()
    tid(page, 'ws-field-revision-name-optional-').fill('Initial')
    save_new_revision(page, ui, 'numbered')
    first = current_revision(ui, 'numbered')
    from conftest import request
    rows = request(ui, '/api/workshop/blocks/numbered/revisions')['revisions']
    assert rows[0]['revision_number'] == 1 and rows[0]['revision_name'] == 'Initial'
    expect(tid(page, 'ws-field-revision-name-optional-')).to_have_value('')
    tid(page, 'ws-field-instructions').fill('Second {{inputs.text}}')
    save_new_revision(page, ui, 'numbered')
    second = current_revision(ui, 'numbered')
    tid(page, 'ws-field-instructions').fill('Pending {{inputs.text}}')
    tid(page, 'ws-choice-block-revision').select_option(first)
    tid(dialog, 'ws-stay').click()
    expect(tid(page, 'ws-field-instructions')).to_have_value('Pending {{inputs.text}}')
    expect(tid(page, 'ws-choice-block-revision')).to_have_value(second)
    tid(page, 'ws-choice-block-revision').select_option(first)
    tid(dialog, 'ws-discard').click()
    expect(tid(page, 'ws-field-instructions')).to_have_value(original)
    assert current_revision(ui, 'numbered') == second
    expect(tid(page, 'ws-choice-block-revision')).to_have_value(first)
    tid(page, 'ws-field-revision-name-optional-').fill('Restored')
    save_new_revision(page, ui, 'numbered')
    rows = request(ui, '/api/workshop/blocks/numbered/revisions')['revisions']
    assert [r['revision_number'] for r in rows] == [3, 2, 1]
    assert rows[0]['revision_name'] == 'Restored'
    assert rows[-1]['id'] == first
    tid(page, 'ws-field-instructions').fill('Saved before switch {{inputs.text}}')
    tid(page, 'ws-choice-block-revision').select_option(first)
    tid(dialog, 'ws-save').click()
    tid(dialog, 'ws-confirm').click()
    expect(tid(page, 'ws-field-instructions')).to_have_value(original)
    rows = request(ui, '/api/workshop/blocks/numbered/revisions')['revisions']
    assert rows[0]['revision_number'] == 4
    saved = request(ui, '/api/workshop/blocks/numbered/revisions/' + rows[0]['id'])['draft']
    assert jsonio.decode_exact(saved['definition_text'])['steps'][0]['prompt']['text'] == 'Saved before switch {{inputs.text}}'


def test_workshop_overwrite_selected_revision(ui, open_page):
    """Overwrite stays available for standalone (v3) blocks, which the page no longer creates."""
    from conftest import request
    from test_workshop import plan_save, send
    plan_save(ui, send(ui, 'preview', starter='write_text', key='overwrite', role='fast')['draft'])
    page = open_page(ui)
    dialog = tid(page, 'ws-dialog')
    tid(page, 'ws-list').select_option('overwrite')
    has(page, 'ws-identity-bar', 'data-test', 'ws-identity-bar')
    first = current_revision(ui, 'overwrite')
    tid(page, 'ws-field-instructions').fill('Second {{inputs.text}}')
    save_new_revision(page, ui, 'overwrite')
    second = current_revision(ui, 'overwrite')
    has(page, 'ws-status', 'data-state', 'saved')
    tid(page, 'ws-choice-block-revision').select_option(first)
    if dialog.is_visible():
        tid(dialog, 'ws-discard').click()
    expect(tid(page, 'ws-save-revision')).to_be_disabled()
    tid(page, 'ws-field-instructions').fill('Replacement {{inputs.text}}')
    tid(page, 'ws-advanced-settings').click()
    tid(page, 'ws-overwrite-revision').click()
    tid(dialog, 'ws-cancel').click()
    expect(tid(page, 'ws-field-instructions')).to_have_value('Replacement {{inputs.text}}')
    tid(page, 'ws-overwrite-revision').click()
    tid(dialog, 'ws-confirm').click()
    expect(tid(page, 'ws-choice-block-revision')).not_to_have_value(first)
    expect(tid(page, 'ws-field-instructions')).to_have_value('Replacement {{inputs.text}}')
    rows = request(ui, '/api/workshop/blocks/overwrite/revisions')
    assert rows['current'] == second
    assert [r['revision_number'] for r in rows['revisions']] == [2, 1]
    expect(tid(page, 'ws-choice-block-revision')).to_have_value(rows['revisions'][1]['id'])


def test_workshop_variable_format_exposes_editor_and_confirms_data_loss(ui, open_page):
    """A fresh structured variable is editable immediately; cancelling format loss keeps its schema."""
    page = open_page(ui)
    dialog = tid(page, 'ws-dialog')
    tid(page, 'ws-new-block').click()
    tid(dialog, 'ws-field-block-key').fill('variables')
    tid(dialog, 'ws-choice-start-with').select_option('write_text')
    pick_labels(dialog)
    tid(dialog, 'ws-create-draft').click()
    output = page.locator('[data-test="ws-port"][data-side="outputs"]')
    open_port(output)
    tid(output, 'port-format').select_option('structured')               # a fresh closed Group, editable at once
    has(output, 'port-editor', 'data-format', 'structured')
    tid(output, 'port-new-name').fill('message')
    tid(output, 'port-add-property').click()
    expect(tid(output, 'port-row')).to_have_count(2)
    tid(output, 'port-format').select_option('text')
    tid(dialog, 'ws-cancel').click()                                     # the loss preview was declined
    expect(tid(output, 'port-format')).to_have_value('structured')
    definition = save_new_revision(page, ui, 'variables')
    assert 'message' in definition['outputs']['out']['contract']['schema']['properties']
    tid(output, 'port-format').select_option('text')
    expect(tid(dialog, 'port-change-before')).to_contain_text('message')
    tid(dialog, 'ws-change-type').click()
    has(output, 'port-editor', 'data-format', 'text')
    definition = save_new_revision(page, ui, 'variables')
    assert definition['outputs']['out']['contract'] == {'format': 'text'}


def test_workshop_invalid_design_keeps_inputs_and_located_save_error(ui, open_page):
    """A broken prompt reference must not masquerade as missing files or erase Test inputs."""
    page = open_page(ui)
    tid(page, 'ws-new-block').click()
    dialog = tid(page, 'ws-dialog')
    tid(dialog, 'ws-field-block-key').fill('invalid_design')
    tid(dialog, 'ws-choice-start-with').select_option('write_text')
    pick_labels(dialog)
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
    tid(page, 'ws-design').click()
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
    pick_labels(dialog)
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


def current_revision(ui, key):
    """The id of a block's current saved revision, or None while it has none."""
    try:
        return request(ui, f'/api/workshop/blocks/{key}/revisions')['current']
    except Exception:  # noqa: BLE001 - no such block yet
        return None


def saved_definition(ui, key):
    """The definition of a block's current saved revision, as the Workshop stored it."""
    draft = request(ui, f'/api/workshop/blocks/{key}/revisions/{current_revision(ui, key)}')['draft']
    return jsonio.decode_exact(draft['definition_text'])


def save_new_revision(page, ui, key):
    """Save from the page and wait for a *new* revision: the status already reads "saved" after an earlier save."""
    before = current_revision(ui, key)
    tid(page, 'ws-save-revision').click()
    tid(tid(page, 'ws-dialog'), 'ws-confirm').click()
    assert wait_until(lambda: current_revision(ui, key) not in (None, before))
    expect(tid(page, 'ws-choice-block-revision')).to_have_attribute('data-current', current_revision(ui, key))
    return saved_definition(ui, key)


def test_workshop_model_role_defaults_and_missing_binding_preserve_intent(ui, server, open_page):
    """Role changes update inherited settings without substituting missing bindings or losing explicit edits."""
    setup_model(ui, server)
    initial = get_state(ui)['roles']
    definitions = {
        'fast': {'description': '', 'location': 'local', 'task_defaults': {'temperature': 0, 'reasoning': 'low'}},
        'writer': {'description': '', 'location': 'local', 'task_defaults': {'temperature': 0.7, 'reasoning': 'high'}},
    }
    request(ui, '/api/roles/save', {'roles': {'fast': 'p', 'writer': 'p'},
                                  'definitions': definitions, 'revision': initial['revision']})
    page = open_page(ui)
    dialog = tid(page, 'ws-dialog')
    tid(page, 'ws-new-block').click()
    tid(dialog, 'ws-field-block-key').fill('role_defaults')
    tid(dialog, 'ws-choice-start-with').select_option('write_text')
    tid(dialog, 'ws-choice-model-role').select_option('fast')
    pick_labels(dialog)
    tid(dialog, 'ws-create-draft').click()
    role = tid(page, 'ws-choice-model-role')
    temperature = tid(page, 'ws-field-temperature')
    reasoning = tid(page, 'ws-choice-reasoning-level')
    expect(temperature).to_have_attribute('placeholder', '0')
    expect(reasoning).to_have_value('')
    tid(page, 'ws-field-instructions').fill('Keep {{inputs.text}}')
    role.select_option('writer')
    expect(temperature).to_have_attribute('placeholder', '0.7')
    temperature.fill('0')
    reasoning.select_option('low')
    role.select_option('fast')
    expect(temperature).to_have_value('0')
    expect(reasoning).to_have_value('low')
    tid(page, 'ws-save-revision').click()
    expect(tid(page, 'ws-actions')).to_have_attribute('inert', '')
    expect(tid(page, 'ws-revision-picker')).to_have_attribute('inert', '')
    tid(dialog, 'ws-confirm').click()
    assert wait_until(lambda: current_revision(ui, 'role_defaults') is not None)
    expect(tid(page, 'ws-choice-block-revision')).to_have_attribute('data-current', current_revision(ui, 'role_defaults'))
    step = saved_definition(ui, 'role_defaults')['steps'][0]
    expect(tid(page, 'ws-actions')).not_to_have_attribute('inert', '')
    assert step['role'] == 'fast' and step['task']['temperature'] == 0 and step['task']['reasoning'] == 'low'
    assert step['prompt']['text'] == 'Keep {{inputs.text}}'
    temperature.fill('')
    reasoning.select_option('')
    step = save_new_revision(page, ui, 'role_defaults')['steps'][0]
    assert 'temperature' not in step['task'] and 'reasoning' not in step['task']
    for view in ['test', 'compare', 'design']:
        tid(page, 'ws-' + view).click()
        tid(page, 'ws-assist').click()
        tid(dialog, 'ws-cancel').click()
        tid(page, 'ws-revision-actions').click()
        tid(dialog, 'ws-cancel').click()
    current = get_state(ui)['roles']
    request(ui, '/api/roles/save', {'roles': {'writer': 'p'}, 'definitions': {'writer': definitions['writer']},
                                  'revision': current['revision']})
    page.reload()
    expect(role).to_have_value('fast')
    expect(temperature).to_have_value('')
    role.select_option('writer')
    expect(temperature).to_have_attribute('placeholder', '0.7')


def test_workshop_design_controls_overrides_request_preview_cards_and_assistance(ui, server, open_page):
    """Design's compact controls preserve inheritance, overrides, resets and repairs, preview requests with Copy,
    edit structured outputs inside their own
    card (a group left empty is refused at save), and the assistance request carries its own sampling intent."""
    setup_model(ui, server)
    save_roles(ui, {'fast': 'p', 'assistant': 'p'})
    page = open_page(ui)
    dialog = tid(page, 'ws-dialog')
    tid(page, 'ws-new-block').click()
    tid(dialog, 'ws-field-block-key').fill('authoring')
    tid(dialog, 'ws-choice-start-with').select_option('extract_fields')
    pick_labels(dialog)
    tid(dialog, 'ws-create-draft').click()
    expect(tid(page, 'ws-field-system-instructions')).to_be_visible()

    value = tid(page, 'ws-field-temperature')
    expect(value).to_be_visible()
    value.fill('not-a-number')
    tid(page, 'ws-save-revision').click()
    has(page, 'ws-status', 'data-state', 'error')
    value.fill('0')
    step = save_new_revision(page, ui, 'authoring')['steps'][0]
    assert step['task']['temperature'] == 0 and 'task_resets' not in step      # zero is a value, not "unset"
    value.fill('')
    step = save_new_revision(page, ui, 'authoring')['steps'][0]
    assert 'temperature' not in step['task'] and 'task_resets' not in step
    tid(page, 'ws-advanced-settings').click()
    tid(page, 'ws-reset-temperature').check()
    expect(value).to_have_value('')
    step = save_new_revision(page, ui, 'authoring')['steps'][0]
    assert step['task_resets'] == ['temperature'] and 'temperature' not in step['task']
    expect(value).to_have_attribute('data-source', 'reset')
    expect(tid(page, 'ws-field-additional-repairs')).to_be_visible()
    tid(page, 'ws-field-additional-repairs').fill('0')
    step = save_new_revision(page, ui, 'authoring')['steps'][0]
    assert step['repair']['max_repairs'] == 0 and step['task_resets'] == ['temperature']

    tid(page, 'ws-test').click()
    tid(page, 'ws-field-text').fill('three notebooks')
    tid(page, 'ws-design').click()
    tid(page, 'ws-request-preview').click()
    system = tid(dialog, 'ws-request-system').text_content()
    assert '"out"' in system and system.rstrip().endswith('=== end output protocol ===')
    assert 'three notebooks' in tid(dialog, 'ws-request-prompt').text_content()
    tid(dialog, 'ws-request-copy-system').click()
    has(dialog, 'ws-request-copy-system', 'data-copy', 'copied')
    assert page.evaluate('navigator.clipboard.readText()') == system
    tid(dialog, 'ws-close').click()

    card = page.locator('[data-test="ws-port"][data-side="outputs"][data-port="out"]')
    open_port(card)
    expect(tid(card, 'port-editor')).to_have_count(1)                         # the output's own card edits its fields

    def add_property(parent, name, kind):
        row = card.locator(f'[data-test="port-row"][data-path="{parent}"]')
        if card.locator(f'[data-test="port-row-detail"][data-path="{parent}"]').count() == 0:
            tid(row, 'port-row-more').click()
        detail = card.locator(f'[data-test="port-row-detail"][data-path="{parent}"]')
        tid(detail, 'port-new-name').fill(name)
        tid(detail, 'port-new-type').select_option(kind)
        tid(detail, 'port-add-property').click()
        expect(card.locator('[data-test="port-row"][data-path="' + '/'.join(filter(None, [parent, name])) + '"]')).to_have_count(1)

    add_property('', 'extras', 'Group')
    before = current_revision(ui, 'authoring')
    tid(page, 'ws-save-revision').click()
    has(page, 'ws-status', 'data-code', 'block_invalid')                      # a new group with no field is refused
    expect(dialog).to_have_count(0)                                            # no review was offered, nothing was saved
    assert current_revision(ui, 'authoring') == before
    add_property('extras', 'note', 'Text')
    schema = save_new_revision(page, ui, 'authoring')['outputs']['out']['contract']['schema']
    assert list(schema['properties']['extras']['properties']) == ['note']

    tid(page, 'ws-assist').click()
    tid(dialog, 'ws-choice-assistant-temperature-source').select_option('custom')
    tid(dialog, 'ws-field-assistant-temperature').fill('0')
    tid(dialog, 'ws-choice-assistant-reasoning-source').select_option('reset')
    with page.expect_request(lambda r: r.url.endswith('/api/workshop/plans') and r.method == 'POST') as sent:
        tid(dialog, 'ws-prepare-request').click()
    body = json.loads(sent.value.post_data)['request']
    assert body['settings_text'] == {'temperature': '0'} and body['task_resets'] == ['reasoning']


def test_workshop_example_dialog_offers_an_input_the_example_lacks(ui, open_page):
    """An input added after an example was saved has no value in it; editing the example must offer it, not hide it."""
    page = open_page(ui)
    dialog = tid(page, 'ws-dialog')
    tid(page, 'ws-new-block').click()
    tid(dialog, 'ws-field-block-key').fill('late_port')
    tid(dialog, 'ws-choice-start-with').select_option('count_text')
    pick_labels(dialog)
    tid(dialog, 'ws-create-draft').click()
    tid(page, 'ws-test').click()
    tid(page, 'ws-field-text').fill('one two')
    tid(page, 'ws-save-as-example').click()
    tid(dialog, 'ws-save').click()
    selected = tid(page, 'ws-choice-example').input_value()
    tid(page, 'ws-design').click()
    tid(page, 'ws-add-input').click()
    tid(page, 'ws-field-field-name').nth(1).fill('extra')
    tid(page, 'ws-choice-local-only').nth(1).select_option('false')
    tid(page, 'ws-test').click()
    expect(tid(page, 'ws-choice-example')).to_have_value(selected)
    tid(page, 'ws-edit-selected-example').click()
    expect(tid(dialog, 'ws-field-text')).to_have_value('one two')
    expect(tid(dialog, 'ws-field-extra')).to_have_count(1)
    expect(tid(dialog, 'ws-field-extra')).to_have_value('')


# --- block_metadata_refactor spec v2, M08 --------------------------------------------------------------------
@pytest.mark.parametrize("width", [390, 1280])
def test_workshop_revision_selection_describe_refine_save_and_test(ui, server, open_page, width):
    from conftest import Reply, answer, request, save_profile, save_roles
    def send(route, **body):
        return request(ui, "/api/workshop/" + route, {"format_version": 1, **body})
    def publish(draft, expected=None):
        body = {"draft": draft, "expected_identity": expected}
        plan = send("plans", action="save", request=body)
        return send("save", **body, expected_plan=plan["expected_plan"],
                    confirmations=[{"kind": r["kind"], "digest": r["digest"]} for r in plan["required_confirmations"]])
    save_profile(ui, "p", model="m", kind="openai_compat", base_url=server.url + "/v1", provider="")
    save_roles(ui, {"write_block_description": "p"})
    draft = send("preview", starter="count_text", key="journey")["draft"]
    first = publish(draft)
    characters = send("preview", draft=draft, edit={"kind": "starter_parameters", "parameters": {"unit": "characters"}})["draft"]
    second = publish(characters, first["base_digest"])
    sections = {k: ("Counts words in the text." if k == "use" else "") for k in
                ("use", "exclusions", "effects", "limitations", "missing_data", "failures")}
    generated = {"format_version": 1, "action": "describe",
                 "payload": {"sections": sections, "port_summaries": {"inputs": {"text": "Text to count."}, "outputs": {}}}}
    server.reply(Reply(body=answer(jsonio.encode_exact({"proposal": generated})).body,
                       headers={"Content-Type": "text/event-stream"}, delay=1.0))
    page = open_page(ui)
    page.set_viewport_size({"width": width, "height": 850})
    tid(page, "ws-list").select_option("journey")
    picker = tid(page, "ws-choice-block-revision")
    expect(picker).to_have_attribute("data-selected", second["durable_id"])
    picker.select_option(first["durable_id"])                                 # a clean selection: no dialog
    expect(picker).to_have_attribute("data-selected", first["durable_id"])
    expect(picker).to_have_attribute("data-current", second["durable_id"])
    expect(tid(page, "ws-save-revision")).to_be_disabled()
    panel = tid(page, "ws-standard-description")
    expect(panel).to_have_attribute("data-status", "missing")
    expect(tid(panel, "ws-description-label")).to_contain_text("revision 1")
    dialog = tid(page, "ws-dialog")
    tid(panel, "ws-describe-block").click()
    expect(tid(dialog, "ws-describe-plan")).to_have_attribute("data-locality", "local")
    expect(tid(dialog, "ws-describe-remote")).to_have_count(0)
    tid(dialog, "ws-confirm").click()
    expect(tid(page, "ws-describe-stop")).to_be_visible()                       # Stop during the call
    expect(tid(dialog, "ws-description-proposal")).to_be_visible()
    expect(tid(dialog, "ws-proposed-use")).to_have_text("Counts words in the text.")
    refined = {"format_version": 1, "action": "describe",
               "payload": {"sections": {"limitations": "Splits on whitespace only."}}}
    server.reply(answer(jsonio.encode_exact({"proposal": refined})))
    tid(dialog, "ws-refine").click()
    tid(dialog, "ws-describe-feedback").fill("Mention how words are split.")
    tid(dialog, "ws-send-feedback").click()
    tid(dialog, "ws-confirm").click()
    expect(tid(dialog, "ws-description-changed")).to_contain_text("Limitations")
    expect(tid(dialog, "ws-proposed-limitations")).to_have_text("Splits on whitespace only.")
    tid(dialog, "ws-save-description").click()
    has(page, "ws-status", "data-state", "description_saved")
    expect(tid(page, "ws-dialog")).to_have_count(0)                             # no executable save dialog
    expect(panel).to_have_attribute("data-status", "saved")
    expect(picker).to_have_attribute("data-dirty", "false")
    expect(tid(page, "ws-save-revision")).to_be_disabled()
    assert "Unsaved" not in page.locator("h2").first.inner_text()
    assert len(server.requests) == 2
    # Manual editing needs no model: unassign the role, edit the newer revision.
    save_roles(ui, {})
    picker.select_option(second["durable_id"])
    expect(tid(page, "ws-standard-description")).to_have_attribute("data-status", "missing")
    tid(page, "ws-edit-description").click()
    tid(dialog, "ws-description-field-use").fill("Counts characters in the text.")
    tid(dialog, "ws-save-description").click()
    has(page, "ws-status", "data-state", "description_saved")
    expect(tid(page, "ws-standard-description")).to_have_attribute("data-status", "saved")
    assert len(server.requests) == 2
    # Reopen older and newer revisions; each keeps its own prose.
    picker.select_option(first["durable_id"])
    expect(tid(page, "ws-description-limitations")).to_contain_text("Splits on whitespace only.")
    picker.select_option(second["durable_id"])
    expect(tid(page, "ws-description-use")).to_contain_text("Counts characters in the text.")
    # Test the older revision through its explicit saved selector; the default stays put.
    held_previews = []
    preview_url = "**/api/workshop/preview"
    page.route(preview_url, lambda route: held_previews.append(route), times=1)
    with page.expect_request(preview_url):
        picker.select_option(first["durable_id"])
    try:
        # A slow revision load must block actions against the previous selection.
        assert tid(page, "ws-test").evaluate("button => Boolean(button.closest('[inert]'))")
    finally:
        held_previews[0].continue_()
    tid(page, "ws-test").click()
    expect(tid(page, "ws-run-source")).to_have_text("Runs use: Revision 1")
    expect(tid(page, "ws-run-source")).to_have_attribute("data-clean", "true")
    tid(page, "ws-field-text").fill("one two")
    tid(page, "ws-run-test").click()
    tid(dialog, "ws-confirm").click()
    expect(tid(page, "ws-output")).to_have_text("2")
    expect(tid(page, "ws-trial").locator("strong")).to_contain_text("revision 1")
    assert request(ui, "/api/workshop/blocks/journey/revisions")["current"] == second["durable_id"]
    # An edit makes the selection a draft: Describe is disabled until it is saved or discarded.
    tid(page, "ws-design").click()
    tid(page, "ws-field-purpose").fill("Edited purpose")
    expect(tid(page, "ws-describe-block")).to_be_disabled()
    expect(tid(page, "ws-save-revision")).to_be_enabled()
    expect(tid(page, "ws-choice-block-revision")).to_have_attribute("data-dirty", "true")


def test_workflow_form_only_edit_save_preflight_run_and_trace(ui, open_page):
    from test_workshop import plan_save, send
    from conftest import request
    draft = send(ui, "preview", key="counter", starter="count_text", starter_version=2)["draft"]
    plan_save(ui, draft)
    page = open_page(ui)
    page.get_by_role("tab", name="Workflows").click()
    tid(page, "wf-new").click()
    tid(page, "wf-workflow-name").fill("browser_flow")
    for side, name in (("input", "text"), ("output", "count")):
        tid(page, "wf-add-" + side).click()
        dialog = tid(page, "ws-dialog")
        tid(dialog, "wf-identifier").fill(name)
        tid(dialog, "ws-add").click()
    tid(page, "wf-add-node").click()
    dialog = tid(page, "ws-dialog")
    tid(dialog, "wf-block-picker").select_option("counter")
    expect(dialog).to_contain_text("Ready to capture")
    tid(dialog, "wf-node-identifier").fill("count_words")
    tid(dialog, "ws-add").click()
    page.get_by_label("Bind text").select_option(label="Input · text")
    page.get_by_label("Output source").select_option(label="count_words · out")
    # The count node returns a JSON number. Use the readable contract selector.
    page.locator('[data-test="wf-contract-chip"][data-port="count"]').click()
    page.locator('details[open] [data-test="wf-contract-format"]').select_option("any")
    expect(page.locator('details[open] [data-test="wf-contract-editor"]')).to_have_attribute("data-format", "any")
    tid(page, "wf-save").click()
    tid(tid(page, "ws-dialog"), "ws-confirm").click()
    expect(tid(page, "wf-status")).to_have_text("Workflow saved.")
    tid(page, "wf-run-view").click()
    tid(page, "wf-text").fill("one two")
    tid(page, "wf-preflight").click()
    expect(tid(page, "wf-review")).to_contain_text("Order: count_words")
    tid(page, "wf-run").click()
    expect(tid(page, "wf-trace")).to_contain_text("browser_flow · complete")
    expect(tid(page, "wf-trace").locator("pre")).to_have_text("2")
    tid(page, "wf-inspect-node").click()
    expect(tid(page, "wf-node-detail")).to_have_attribute("data-node", "count_words")    # inline, no nested dialog
    expect(tid(page, "ws-dialog")).to_have_count(0)
    assert request(ui, "/api/workshop/workflows")["workflows"] == ["browser_flow"]


def test_workflow_settings_reviews_trust_and_confirmation(ui, open_page):
    from conftest import request
    page = open_page(ui)
    tid(page, "ws-settings").click()
    tid(page, "header-workflow-settings").click()
    settings = page.get_by_role("dialog", name="Trusted destinations")
    tid(settings, "wf-mcp-confirmation").select_option("effectful_batches")
    tid(settings, "wf-save-mcp-confirmation").click()
    has(settings, "wf-settings-status", "data-state", "preference")
    tid(settings, "wf-trust-manual").locator("summary").click()             # manual entry is folded
    tid(settings, "wf-endpoint-url").fill("http://localhost:1234/v1")
    tid(settings, "wf-model").fill("local-model")
    has(settings, "wf-trust-manual-status", "data-reason", "hostname")       # live eligibility before submitting
    tid(settings, "wf-review-trust").click()
    has(settings, "wf-settings-status", "data-reason", "hostname")
    tid(settings, "wf-endpoint-url").fill("http://127.0.0.1:1234/v1")
    tid(settings, "wf-review-trust").click()
    confirm = page.get_by_role("dialog", name="Grant destination trust")
    expect(confirm).to_contain_text("http://127.0.0.1:1234/v1")
    tid(confirm, "ws-confirm").click()
    has(settings, "wf-settings-status", "data-state", "granted")
    assert request(ui, "/api/workshop/destination-trust")["grants"][0]["model"] == "local-model"
    expect(settings.locator('[data-test="wf-trust-row"][data-trusted="true"]')).to_have_count(1)
    tid(settings, "wf-revoke-trust").click()
    tid(page.get_by_role("dialog", name="Revoke destination trust"), "ws-confirm").click()
    has(settings, "wf-settings-status", "data-state", "revoked")
    assert request(ui, "/api/workshop/destination-trust")["grants"] == []


def test_workflow_form_round_trips_exact_constant(ui, open_page):
    from conftest import request
    page = open_page(ui)
    page.get_by_role("tab", name="Workflows").click()
    tid(page, "wf-new").click()
    tid(page, "wf-workflow-name").fill("exact_constant")
    for side, name in (("output", "answer"), ("constant", "value")):
        tid(page, "wf-add-" + side).click()
        dialog = tid(page, "ws-dialog")
        tid(dialog, "wf-identifier").fill(name)
        tid(dialog, "ws-add").click()
    page.get_by_label("Constant format").select_option("json")
    text = '{"big":9007199254740993,"ratio":0.10000000000000000001}'
    tid(page, "wf-constant-value").fill(text)
    page.get_by_label("Output source").select_option(label="Constant · value")
    page.locator('[data-test="wf-contract-chip"][data-port="answer"]').click()
    page.locator('details[open] [data-test="wf-contract-format"]').select_option("any")
    expect(page.locator('details[open] [data-test="wf-contract-editor"]')).to_have_attribute("data-format", "any")
    tid(page, "wf-save").click()
    tid(tid(page, "ws-dialog"), "ws-confirm").click()
    expect(tid(page, "wf-status")).to_have_text("Workflow saved.")
    tid(page, "wf-purpose").fill("Keep exact values across edits")
    tid(page, "wf-save").click()
    tid(tid(page, "ws-dialog"), "ws-confirm").click()
    expect(tid(page, "wf-status")).to_have_text("Workflow saved.")
    saved = request(ui, "/api/workshop/workflows/exact_constant")
    assert "9007199254740993" in saved["editor"]["constants"]["value"]["value"]
    assert "0.10000000000000000001" in saved["editor"]["constants"]["value"]["value"]
    tid(page, "wf-run-view").click()
    tid(page, "wf-preflight").click()
    tid(page, "wf-run").click()
    expect(tid(page, "wf-trace").locator("pre")).to_have_text(text)


def test_workflow_rebind_keeps_verified_expectation_and_snapshot_runs(ui, open_page):
    import shutil
    from conftest import request, switch_history
    from test_workshop import plan_save, send
    from test_workflows import publish_workflow, transform, port
    first = transform("counter", "count_text", {"unit":"whitespace_words"}, {"text":port()}, port("json"))
    example = {"id":"fixed", "version":1, "label":"Two words", "verified":True, "inputs":{"text":"one two"},
               "expectation":{"kind":"whole_output", "values":{"out":"2"}}}
    publish_workflow(ui, first, examples=[example])
    replacement = transform("characters", "count_text", {"unit":"characters"}, {"text":port()}, port("json"))
    plan_save(ui, replacement)
    switch_history(ui, True)
    page = open_page(ui)
    page.get_by_role("tab", name="Workflows").click()
    tid(page, "wf-picker").select_option("chain")
    tid(page, "wf-rebind-node").click()
    dialog = page.get_by_role("dialog", name="Choose replacement revision")
    tid(dialog, "wf-block-picker").select_option("characters")
    expect(dialog).to_contain_text("Ready to capture")
    tid(dialog, "ws-use-revision").click()
    tid(page, "wf-save").click()
    review = page.get_by_role("dialog", name="Review node rebind")
    expect(review).to_contain_text("counter → characters")
    tid(review, "ws-confirm").click()
    expect(tid(page, "wf-status")).to_have_text("Workflow saved.")
    shutil.rmtree(ui.state.workspace.path / "blocks" / "characters")
    tid(page, "wf-picker").select_option("")
    tid(page, "wf-picker").select_option("chain")
    expect(tid(page, "wf-source-only")).to_contain_text("Workflow snapshot")
    tid(page, "wf-run-view").click()
    page.get_by_label("Two words", exact=True).check()
    tid(page, "wf-preflight").click()
    tid(page, "wf-run").click()
    expect(tid(page, "wf-trace")).to_contain_text("Checks passed 1/1")
    expect(tid(page, "wf-trace")).to_contain_text("Incorrect 1")
    tid(page, "wf-examples").click()
    tid(page, "wf-edit-example").click()
    tid(page.get_by_role("dialog", name="Workflow example"), "wf-expected-out-(exact-json)").fill("7")
    tid(page.get_by_role("dialog", name="Workflow example"), "ws-keep-example").click()
    tid(page, "wf-save").click()
    tid(page.get_by_role("dialog", name="Review workflow save"), "ws-confirm").click()
    expect(tid(page, "wf-status")).to_have_text("Workflow saved.")
    row = request(ui, "/api/workshop/workflows/chain")["examples"][0]
    assert row["version"] == 2 and row["verified"] is False
    page.get_by_role("tab", name="History", exact=True).click()
    tid(page, "history-workflow-row").first.click()                    # workflow runs are in the History list
    expect(tid(page, "history-workflow-title")).to_contain_text("chain · complete")
    expect(tid(page, "ws-dialog")).to_have_count(0)


def test_workflow_canvas_drag_uses_form_bindings_and_persists_layout(ui, open_page):
    from conftest import request
    from test_workflows import edit, port, transform
    from test_workshop import plan_save
    from agentwiz.jsonio import encode_exact

    first = plan_save(ui, transform("counter", "count_text", {"unit": "whitespace_words"},
                                    {"text": port()}, port("json")))
    second = plan_save(ui, transform("reporter", "format_report", {"template": "Words: {{inputs.n}}"},
                                     {"n": port("json")}, port()))
    definition = {
        "name": "canvas_flow", "purpose": "", "inputs": {"text": port()}, "constants": {},
        "nodes": {"count": {"bindings": {"text": {"kind": "input", "port": "text"}}},
                  "report": {"bindings": {}}},
        "outputs": {"answer": {**port(), "binding": {"kind": "node", "node": "report", "port": "out"}}},
    }
    edit(ui, "save", {"name": "canvas_flow", "file_token": None, "definition_text": encode_exact(definition),
                      "selections": {"count": {"key": "counter", "revision": first["durable_id"]},
                                     "report": {"key": "reporter", "revision": second["durable_id"]}}})
    page = open_page(ui)
    page.get_by_role("tab", name="Workflows").click()
    tid(page, "wf-picker").select_option("canvas_flow")
    page.get_by_text("Canvas", exact=True).click()
    tid(page, "wf-canvas-output-count-out").drag_to(tid(page, "wf-canvas-input-report-n"))
    expect(page.get_by_label("Bind n · json")).to_have_value('{"kind":"node","node":"count","port":"out"}')
    handle = tid(page, "wf-canvas-move-count")
    handle.scroll_into_view_if_needed()
    bounds = handle.bounding_box()
    x, y = bounds["x"] + 20, bounds["y"] + 10
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 34, y + 22, steps=4)
    page.mouse.up()
    expect(tid(page, "wf-canvas-node-count")).to_have_css("left", "44px")
    tid(page, "wf-save").click()
    tid(page.get_by_role("dialog", name="Review workflow save"), "ws-confirm").click()
    expect(tid(page, "wf-status")).to_have_text("Workflow saved.")
    saved = request(ui, "/api/workshop/workflows/canvas_flow")
    assert saved["layout"]["count"] == {"x": 34, "y": 22}
    assert saved["editor"]["nodes"]["report"]["bindings"]["n"]["node"] == "count"
    tid(page, "wf-run-view").click()
    tid(page, "wf-text").fill("one two")
    tid(page, "wf-preflight").click()
    tid(page, "wf-run").click()
    expect(tid(page, "wf-trace").locator("pre")).to_have_text("Words: 2")


def test_workflow_preflight_confirmation_modes_and_stop(ui, open_page, make_fake_mcp):
    from conftest import add_server, simple_tool, wait_until
    from test_workflows import port, publish_workflow
    from agentwiz.jsonio import encode_exact

    fake = make_fake_mcp("modern", tools=[simple_tool("lookup", behave=lambda call: (call.start_sse(), call.hold()))])
    add_server(ui, "docs", fake.url)
    publish_workflow(ui, {"key": "lookup", "files": {}, "definition_text": encode_exact({
        "format_version": 4, "name": "lookup", "inputs": {"text": port()}, "outputs": {"out": port("json")},
        "steps": [{"id": "lookup", "kind": "tool", "server": "docs", "tool": "lookup",
                   "arguments": {"q": "{{inputs.text}}"}, "output": "out"}]})})
    page = open_page(ui)
    page.get_by_role("tab", name="Workflows").click()
    tid(page, "wf-picker").select_option("chain")
    tid(page, "wf-run-view").click()
    tid(page, "wf-text").fill("query")
    tid(page, "wf-preflight").click()
    expect(tid(page, "wf-review")).to_contain_text("Every MCP workflow run")
    tid(page, "wf-run").click()
    tid(page.get_by_role("dialog", name="Confirm MCP workflow execution"), "ws-cancel").click()
    assert not fake.methods("tools/call")
    tid(page, "wf-preflight-settings").click()
    settings = page.get_by_role("dialog", name="Trusted destinations")
    tid(settings, "wf-mcp-confirmation").select_option("effectful_batches")
    tid(settings, "wf-save-mcp-confirmation").click()
    expect(settings).to_contain_text("Confirmation preference saved.")
    tid(settings, "ws-close").click()
    tid(page, "wf-preflight").click()
    expect(tid(page, "wf-review")).to_contain_text("Only MCP batches")
    try:
        tid(page, "wf-run").click()
        wait_until(lambda: fake.holding > 0)
        tid(page, "wf-stop").click()
        expect(tid(page, "wf-trace")).to_contain_text("chain · unknown")
        assert len(fake.methods("tools/call")) == 1
    finally:
        fake.release.set()


@pytest.mark.parametrize("lose_lookup", [False, True])
def test_workflow_start_recovers_lost_response(ui, open_page, lose_lookup):
    from test_workflows import port, publish_workflow, transform

    publish_workflow(ui, transform("count", "count_text", {"unit":"characters"}, {"text":port()}, port("json")))
    page = open_page(ui)
    page.get_by_role("tab", name="Workflows").click()
    tid(page, "wf-picker").select_option("chain")
    tid(page, "wf-run-view").click()
    tid(page, "wf-text").fill("test")
    tid(page, "wf-preflight").click()
    bodies, operations = [], []

    def intercept(route):
        bodies.append(route.request.post_data_json)
        response = route.fetch()
        operations.append(response.json()["operation_id"])
        if len(bodies) == 1:
            route.abort()
        else:
            route.fulfill(response=response)

    page.route("**/api/workshop/workflows/chain/run", intercept)
    if lose_lookup:
        page.route("**/api/workshop/tickets/*", lambda route: route.abort(), times=1)
    tid(page, "wf-run").click()
    if lose_lookup:
        expect(tid(page, "wf-run")).to_have_text("Retry start")
        expect(tid(page, "wf-picker")).to_be_disabled()
        tid(page, "wf-run").click()
    expect(tid(page, "wf-trace")).to_contain_text("chain · complete")
    assert len(bodies) == (2 if lose_lookup else 1)
    assert all(body == bodies[0] for body in bodies)
    assert len(set(operations)) == 1



def test_release_gate_form_approval_and_browser_reconnect(ui, open_page):
    page = open_page(ui)
    errors = []
    page.on('pageerror', lambda exc: errors.append(str(exc)))
    page.get_by_role('tab', name='Workflows').click()
    tid(page, 'wf-new').click()
    tid(page, 'wf-workflow-name').fill('browser_gate')
    for side, name in [('input', 'text'), ('output', 'out')]:
        tid(page, 'wf-add-' + side).click()
        dialog = tid(page, 'ws-dialog')
        tid(dialog, 'wf-identifier').fill(name)
        tid(dialog, 'ws-add').click()
    page.get_by_role('checkbox', name='Local only', exact=True).first.check()
    tid(page, 'wf-add-gate').click()
    dialog = tid(page, 'ws-dialog')
    tid(dialog, 'wf-identifier').fill('review')
    tid(dialog, 'ws-add').click()
    page.get_by_label('Bind value').select_option(label='Input · text')
    page.get_by_label('Output source').select_option(label='review · released')
    tid(page, 'wf-save').click()
    tid(tid(page, 'ws-dialog'), 'ws-confirm').click()
    expect(tid(page, 'wf-save')).to_be_disabled()
    saved = request(ui, '/api/workshop/workflows/browser_gate')
    assert saved['editor']['nodes']['review']['gate']['kind'] == 'release'
    tid(page, 'wf-run-view').click()
    text = 'Reviewed candidate\n' * 100
    tid(page, 'wf-text').fill(text)
    tid(page, 'wf-preflight').click()
    tid(page, 'wf-run').click()
    expect(tid(page, 'wf-release-payload')).to_have_text(text.strip())
    # Reopening the page recovers the active pause through the authenticated endpoint.
    page.reload()
    page.get_by_role('tab', name='Workflows').click()
    expect(tid(page, 'wf-release-panel')).to_be_visible()
    expect(tid(page, 'wf-release-approve')).to_be_enabled()
    tid(page, 'wf-release-approve').click()
    expect(tid(page, 'wf-release-panel')).to_have_count(0)
    assert request(ui, '/api/workshop/workflow-active') == {}
    tid(page, 'wf-inspect-node').click()
    expect(tid(page, 'wf-review-payload')).to_have_text(text.strip())
    assert errors == []


# UI-AB v2 -------------------------------------------------------------------------------------------------------
def local_role(ui, server):
    from conftest import save_profile, save_roles
    save_profile(ui, "p", model="m", kind="openai_compat", base_url=server.url + "/v1", provider="")
    save_roles(ui, {"fast": "p"})


def test_create_requires_port_labels_and_insert_buttons_follow_renames(ui, server, open_page):
    """Create needs an explicit label choice and makes a workflow-ready block; Insert buttons and missing-reference
    checks follow port edits, and a single-candidate replacement changes the prompt only after confirmation."""
    local_role(ui, server)
    page = open_page(ui)
    dialog = tid(page, "ws-dialog")
    tid(page, "ws-new-block").click()
    tid(dialog, "ws-field-block-key").fill("labelled")
    tid(dialog, "ws-choice-start-with").select_option("write_text")
    tid(dialog, "ws-create-draft").click()
    has(dialog, "ws-create-error", "data-code", "labels_required")
    tid(dialog, "ws-port-label-private").check()
    tid(dialog, "ws-create-draft").click()
    expect(dialog).to_have_count(0)
    expect(tid(page, "ws-upgrade-banner")).to_have_count(0)                   # workflow-ready from creation
    expect(tid(page, "ws-choice-local-only")).to_have_count(2)
    has(page, "ws-protection-labels", "data-unset", "0")
    tid(page, "ws-field-field-name").first.fill("summary")
    expect(tid(page, "ws-insert-summary")).to_have_count(1)
    expect(tid(page, "ws-insert-text")).to_have_count(0)
    has(page, "ws-missing-reference", "data-ref", "inputs.text")
    tid(page, "ws-replace-reference").click()
    tid(dialog, "ws-cancel").click()
    expect(dialog).to_have_count(0)
    assert "{{inputs.text}}" in tid(page, "ws-field-instructions").input_value()       # nothing rewritten unasked
    tid(page, "ws-replace-reference").click()
    tid(dialog, "ws-replace").click()
    expect(tid(page, "ws-missing-reference")).to_have_count(0)
    definition = save_new_revision(page, ui, "labelled")
    assert "{{inputs.summary}}" in definition["steps"][0]["prompt"]["text"]
    assert definition["format_version"] == 4 and definition["inputs"]["summary"]["local_only"] is True


def test_standalone_block_shows_upgrade_banner(ui, open_page):
    from test_workshop import plan_save, send
    plan_save(ui, send(ui, "preview", starter="write_text", key="legacy", role="fast")["draft"])
    page = open_page(ui)
    tid(page, "ws-list").select_option("legacy")
    tid(page, "ws-upgrade-for-workflows").click()
    expect(tid(page, "ws-upgrade-banner")).to_have_count(0)
    expect(page.locator('[data-test="ws-choice-local-only"][data-state="unset"]')).to_have_count(2)


def test_test_readiness_grants_and_retires_retained_labels_in_place(ui, server, open_page):
    """A protected Test refusal is explained in place; Grant clears it, and retiring an earlier revision's label is
    an explicit, recorded action. Nothing is sent while refused."""
    from test_workshop import plan_save, send
    local_role(ui, server)
    first = plan_save(ui, send(ui, "preview", starter="write_text", key="kept", role="fast", data_handling="private")["draft"])
    selected = send(ui, "preview", key="kept", revision=first["durable_id"])["draft"]
    definition = jsonio.decode_exact(selected["definition_text"])
    definition["inputs"]["text"]["local_only"] = definition["outputs"]["out"]["local_only"] = False
    selected["definition_text"] = jsonio.encode_exact(definition)
    plan_save(ui, selected, selected["base_digest"])
    page = open_page(ui)
    dialog = tid(page, "ws-dialog")
    tid(page, "ws-list").select_option("kept")
    tid(page, "ws-test").click()
    tid(page, "ws-field-text").fill("value")
    tid(page, "ws-check-readiness").click()
    has(page, "ws-readiness", "data-code", "destination_untrusted")
    expect(page.locator('[data-test="ws-restriction-sources"] [data-code="retained_label"]')).to_have_count(1)
    tid(page, "ws-readiness-retained").click()
    expect(tid(dialog, "ws-label-pair")).to_have_count(1)
    tid(dialog, "ws-retire-these-labels").click()
    has(page, "ws-readiness", "data-state", "ready")                          # nothing restricts this run now
    assert request(ui, "/api/workshop/blocks/kept/retained-labels")["pairs"][0]["acknowledged"] is True
    tid(page, "ws-design").click()
    tid(page, "ws-choice-local-only").first.select_option("true")            # a current label needs trust
    tid(page, "ws-test").click()
    tid(page, "ws-check-readiness").click()
    has(page, "ws-readiness", "data-code", "destination_untrusted")
    tid(page, "ws-readiness-grant").click()
    tid(dialog, "ws-confirm").click()
    has(page, "ws-readiness", "data-state", "ready")
    assert not server.requests


def test_port_chip_shape_shortcut_and_done_keep_draft(ui, server, open_page):
    local_role(ui, server)
    page = open_page(ui)
    new_block(page, "shaped")
    output = page.locator('[data-test="ws-port"][data-side="outputs"]')
    has(output, "port-chip", "data-kind", "text")
    open_port(output)
    tid(output, "port-shortcut-list").click()
    dialog = tid(page, "ws-dialog")
    tid(dialog, "port-shortcut-name").nth(0).fill("placeholder")
    tid(dialog, "port-shortcut-name").nth(1).fill("original")
    tid(dialog, "ws-use-shape").click()
    has(output, "port-editor", "data-format", "structured")
    tid(output, "port-done").click()
    has(output, "port-drawer", "data-state", "closed")
    has(output, "port-chip", "data-kind", "structured")
    has(page, "ws-unsaved-state", "data-state", "unsaved")                  # Done closes; saving stays with the block
    schema = save_new_revision(page, ui, "shaped")["outputs"]["out"]["contract"]["schema"]
    assert schema == {"type": "array", "items": {"type": "object", "properties": {
        "placeholder": {"type": "string"}, "original": {"type": "string"}},
        "required": ["placeholder", "original"], "additionalProperties": False}}


def test_workflow_readiness_finding_grants_in_place(ui, server, open_page):
    from test_workflows import port, publish_workflow
    local_role(ui, server)
    draft = {"key": "writer", "files": {}, "definition_text": jsonio.encode_exact({
        "format_version": 4, "name": "writer", "inputs": {"text": port(restricted=True)}, "outputs": {"out": port()},
        "steps": [{"id": "write", "kind": "model", "role": "fast", "response_mode": "named_json",
                   "prompt": {"text": "{{inputs.text}}"}}]})}
    publish_workflow(ui, draft)
    page = open_page(ui)
    page.get_by_role("tab", name="Workflows").click()
    tid(page, "wf-picker").select_option("chain")
    tid(page, "wf-run-view").click()
    tid(page, "wf-text").fill("protected")
    tid(page, "wf-preflight").click()
    has(page, "wf-readiness-state", "data-state", "blocked")
    finding = page.locator('[data-test="wf-finding"][data-code="destination_untrusted"]')
    tid(finding, "wf-finding-grant").click()
    tid(tid(page, "ws-dialog"), "ws-confirm").click()
    has(page, "wf-readiness-state", "data-state", "ready")
    expect(tid(page, "wf-run")).to_be_enabled()
    assert not server.requests


# core v3 §5 -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("starter", ["write_text", "summarise", "extract_fields", "choose_label", "check_source",
                                     "blank_model", "call_tool", "blank_tool"])
def test_page_create_dialog_makes_v4_blocks_with_the_chosen_labels(ui, open_page, make_fake_mcp, starter):
    """The ordinary Create a block dialog never yields a v3 model or tool block: each starter becomes v4 and
    every starter port carries the chosen label."""
    tool_kind = starter in ("call_tool", "blank_tool")
    if tool_kind:
        fake = make_fake_mcp("modern", tools=[simple_tool("echo")])
        add_server(ui, "tools", fake.url, **FAST)
    page = open_page(ui)
    tid(page, "ws-new-block").click()
    dialog = tid(page, "ws-dialog")
    tid(dialog, "ws-field-block-key").fill("guarded")
    tid(dialog, "ws-choice-start-with").select_option(starter)
    if tool_kind:
        tid(dialog, "ws-choice-mcp-server").select_option("tools")
        has(dialog, "ws-tool-discovery", "data-state", "ready")
        tid(dialog, "ws-choice-tool-name").select_option("echo")
    tid(dialog, "ws-port-label-private").check()
    with page.expect_response(lambda r: r.url.endswith("/api/workshop/preview")) as caught:
        tid(dialog, "ws-create-draft").click()
    sent = caught.value.request.post_data_json
    assert sent["data_handling"] == "private"
    definition = jsonio.decode_exact(caught.value.json()["draft"]["definition_text"])
    assert definition["format_version"] == 4
    ports = [p for side in ("inputs", "outputs") for p in definition[side].values()]
    assert ports and all(p["local_only"] is True for p in ports)


# core v3 §7.4 journeys ---------------------------------------------------------------------------------
def open_workflow_run(page, name):
    page.get_by_role("tab", name="Workflows").click()
    tid(page, "wf-picker").select_option(name)
    tid(page, "wf-run-view").click()


def test_page_s2a_local_private_summary_runs_with_only_local_trusted_destinations(ui, open_page, make_server):
    from test_core_v3 import Backend, prepare
    local = Backend()
    try:
        prepare(ui, "local", local)
        page = open_page(ui)
        open_workflow_run(page, "private_summary_local")
        tid(page, "wf-document").fill("Jane Example reported fatigue. Record TEST-123.")
        tid(page, "wf-preflight").click()
        has(page, "wf-readiness-state", "data-state", "ready")
        plan = request(ui, "/api/workshop/plans", {"format_version": 1, "action": "workflow_run",
                       "request": {"name": "private_summary_local", "inputs": {"document": "x"}}})
        assert all(row["protected"] and row["trust_revision"] for row in plan["descriptor"]["destinations"].values())
        expect(tid(page, "wf-release-panel")).to_have_count(0)
        tid(page, "wf-run").click()
        expect(tid(page, "wf-trace")).to_contain_text("complete")
    finally:
        local.close()


def test_page_s2_release_private_summary_pauses_for_review_then_completes(ui, open_page):
    from test_core_v3 import Backend, prepare
    local, cloud = Backend(), Backend()
    try:
        prepare(ui, "release", local, cloud)
        plan = request(ui, "/api/workshop/plans", {"format_version": 1, "action": "workflow_run",
                       "request": {"name": "private_summary_release", "inputs": {"document": "x"}}})
        destinations = plan["descriptor"]["destinations"]
        assert destinations["cloud_summary"]["protected"] is False and destinations["cloud_summary"]["kind"] == "openrouter"
        assert all(row["protected"] for node, row in destinations.items() if node != "cloud_summary")
        page = open_page(ui)
        open_workflow_run(page, "private_summary_release")
        tid(page, "wf-document").fill("Jane Example reported fatigue. Record TEST-123.")
        tid(page, "wf-preflight").click()
        has(page, "wf-readiness-state", "data-state", "ready")
        tid(page, "wf-run").click()
        expect(tid(page, "wf-release-panel")).to_be_visible()
        assert cloud.requests == []
        page.get_by_role("checkbox", name="I have reviewed the AI concerns or assessment unavailability").check()
        tid(page, "wf-release-approve").click()
        expect(tid(page, "wf-release-panel")).to_have_count(0)
        expect(tid(page, "wf-trace")).to_contain_text("complete")
        assert len(cloud.requests) == 1
    finally:
        local.close()
        cloud.close()


def test_page_s3_refusal_offers_a_release_gate_without_contacting_the_cloud(ui, open_page, make_server):
    from test_core_v3 import cloud_chain
    local, cloud = make_server(), make_server()
    cloud_chain(ui, local, cloud, gate_node=False)
    page = open_page(ui)
    open_workflow_run(page, "chain")
    tid(page, "wf-document").fill("Jane Example")
    tid(page, "wf-preflight").click()
    has(page, "wf-readiness-state", "data-state", "blocked")
    finding = page.locator('[data-test="wf-finding"][data-remedy="add_release_gate"]')
    expect(finding).to_have_count(1)
    expect(tid(finding, "wf-finding-add-gate")).to_be_visible()
    expect(finding.locator('details [data-test="wf-finding-code"]')).to_have_count(1)
    assert local.requests == cloud.requests == []


def test_page_s6_saved_use_banner_reports_a_model_change(ui, open_page, server):
    from conftest import save_profile, save_roles
    from test_core_v3 import save_definition
    from test_release_gate import model_draft, node
    from test_workflows import port
    from test_workshop import plan_save, send
    save_profile(ui, "local", model="old", kind="openai_compat", base_url=server.url + "/v1", provider="")
    save_roles(ui, {"local": "local"})
    saved = plan_save(ui, model_draft("deid", "local", {"text": port()}, {"out": port()}))
    save_definition(ui, {"name": "model", "purpose": "", "inputs": {"text": port(restricted=True)}, "constants": {},
                         "nodes": {"m": {"bindings": {"text": {"kind": "input", "port": "text"}}}},
                         "outputs": {"out": {**port(), "binding": node("m")}}},
                    {"m": {"key": "deid", "revision": saved["durable_id"]}})
    grants = request(ui, "/api/workshop/destination-trust")
    send(ui, "destination-trust", expected_revision=grants["revision"], trusted=True,
         destination={"kind": "openai_compat", "url": server.url + "/v1", "model": "old", "provider": ""})
    save_profile(ui, "local", model="new", kind="openai_compat", provider="")
    page = open_page(ui)
    tid(page, "ws-settings").click()
    tid(page, "header-workflow-settings").click()
    settings = page.get_by_role("dialog", name="Trusted destinations")
    has(settings, "wf-saved-trust-gaps", "data-count", "1")
    has(settings, "wf-saved-trust-gaps", "data-complete", "true")
    expect(settings.locator('[data-test="wf-trust-row"][data-requires-trust="true"][data-trusted="false"]')).to_have_count(1)
    assert not server.requests


def test_page_restore_parameter_control_saves_its_choice(ui, open_page):
    page = open_page(ui)
    new_block(page, "restorer", "restore_placeholders")
    tid(page, "ws-restore-unknown").select_option("keep")
    tid(page, "ws-apply-parameters").click()
    expect(tid(page, "ws-restore-unknown")).to_have_value("keep")
    definition = save_new_revision(page, ui, "restorer")
    assert definition["steps"][0]["parameters"] == {"unknown": "keep", "placeholder_syntax": "bracket_upper"}


TEXT_CHECKS = {"kind": "text", "assertions": [
    {"output": "out", "operation": "excludes", "values": ["Jane\nExample", "TEST-1"], "case": "insensitive"},
    {"output": "out", "operation": "excludes_pattern", "pattern": "bracket_upper"}]}


def test_page_block_example_editor_preserves_text_checks(ui, open_page):
    from test_workshop import http_method, plan_save, send
    plan_save(ui, send(ui, "preview", starter="format_report", starter_version=2, key="checked")["draft"])
    http_method(ui, "blocks/checked/examples", "PUT", {"expected_revision": None, "examples": [
        {"id": "t", "version": 1, "label": "Before", "inputs": {"text": "x"}, "expectation": TEXT_CHECKS,
         "origin": "manual", "verified": True}]})
    page = open_page(ui, "workshop/checked/test")
    tid(page, "ws-choice-example").select_option("t")
    tid(page, "ws-edit-selected-example").click()
    dialog = tid(page, "ws-dialog")
    expect(tid(dialog, "ws-text-check")).to_have_count(2)
    tid(dialog, "ws-field-label").fill("After")
    tid(dialog, "ws-save").click()
    tid(page, "ws-save-example-collection").click()
    assert wait_until(lambda: request(ui, "/api/workshop/blocks/checked/examples")["examples"][0]["label"] == "After")
    assert request(ui, "/api/workshop/blocks/checked/examples")["examples"][0]["expectation"] == TEXT_CHECKS


def test_page_workflow_example_editor_preserves_text_checks(ui, open_page):
    from test_core_v3 import Backend, prepare
    local = Backend()
    try:
        prepare(ui, "local", local)
        before = request(ui, "/api/workshop/workflows/private_summary_local")["examples"][0]
        page = open_page(ui)
        page.get_by_role("tab", name="Workflows").click()
        tid(page, "wf-picker").select_option("private_summary_local")
        tid(page, "wf-examples").click()
        tid(page, "wf-edit-example").first.click()
        dialog = page.get_by_role("dialog", name="Workflow example")
        expect(tid(dialog, "wf-text-check")).to_have_count(3)
        tid(dialog, "wf-example-label").fill("Relabelled")
        tid(dialog, "ws-keep-example").click()
        tid(page, "wf-save").click()
        tid(page.get_by_role("dialog", name="Review workflow save"), "ws-confirm").click()
        expect(tid(page, "wf-status")).to_have_text("Workflow saved.")
        after = request(ui, "/api/workshop/workflows/private_summary_local")["examples"][0]
        assert after["label"] == "Relabelled" and after["expectation"] == before["expectation"]
    finally:
        local.close()
