"""Functional reasons: sandbox isolation, lifecycle failure cleanup and real guided UI actions."""
from pathlib import Path

import pytest

from conftest import get_state, request
from test_page import browser, context, open_page, tid, has


def test_tutorial_isolation_recovery_and_cleanup(ui):
    before = get_state(ui)
    assert request(ui, "/api/tutorial")["active"] is False
    request(ui, "/api/tutorial/start", {})
    session = ui.tutorial
    child = session.child
    root = Path(session.folder.name)
    assert get_state(child)["workspace"]["name"] == "tutorial"
    assert get_state(child)["base_url"].startswith("http://127.0.0.1:")
    assert get_state(ui) == before
    assert request(child, "/api/tutorial/step", {"step": 24})["step"] == 24
    assert get_state(child)["model"] == "json-repair"
    request(child, "/api/tutorial/restart", {"action": "damage"})
    assert get_state(session.child)["profiles"]["read_only"] is True
    request(session.child, "/api/tutorial/restart", {"action": "restore"})
    assert get_state(session.child)["profiles"]["read_only"] is False
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


def test_tutorial_browser_preparation_and_repair(ui, open_page):
    page = open_page(ui)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    tid(page, "header-tutorial").click()
    has(page, "tutorial-box", "data-step", "0")
    for step in range(29):
        tid(page, "tutorial-jump").select_option(str(step))
        has(page, "tutorial-box", "data-step", str(step))
        has(page, "tutorial-status", "data-state", "ready")
        if step == 1:
            assert tid(page, "chat-prompt").input_value()
            tid(page, "chat-send").click()
            has(page, "chat-status", "data-outcome", "complete")
        if step == 10:
            tid(page, "chat-send").click()
            tid(page, "chat-stop").click()
            has(page, "chat-status", "data-outcome", "stopped")
        if step in (12, 13, 14):
            page.locator("#tutorial-actions button").click()
            has(page, "documents-status", "data-state", "error" if step == 13 else "ready")
        if step in (17, 18):
            tid(page, "mcp-run").click()
            has(page, "mcp-status", "data-outcome", "ok")
        if step == 21:
            tid(page, "contracts-save").click()
            has(page, "contracts-editor", "data-state", "saved")
            tid(page, "tutorial-reset").click()
            has(page, "tutorial-status", "data-state", "ready")
            tid(page, "contracts-save").click()
            has(page, "contracts-editor", "data-state", "saved")
        if step == 24:
            tid(page, "checked-send").click()
            page.locator('[data-test="checked-attempt"][data-check="fail"]').wait_for()
            tid(page, "checked-repair-preview-btn").click()
            tid(page, "checked-repair-send").click()
            page.locator('[data-test="checked-attempt"][data-check="pass"]').wait_for()
        if step == 26:
            tid(page, "chat-send").click()
            has(page, "chat-status", "data-save", "saved")
    tid(page, "tutorial-route").select_option("quick")
    has(page, "tutorial-box", "data-step", "1")
    has(page, "tutorial-status", "data-state", "ready")
    tid(page, "tutorial-next").click()
    has(page, "tutorial-box", "data-step", "9")
    tid(page, "tutorial-exit").click()
    page.wait_for_url(ui.url)
    assert errors == []
