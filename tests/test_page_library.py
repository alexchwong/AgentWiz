"""The Add from library dialog (starter blocks addendum, 5.4) in headless Chromium: entries by group, statuses, the
role display, the two-step Review and Add, outcomes, Finish adding and the reopened review after a change. Elements
are found by data-test; waits use Playwright conditions on attributes, never sleeps."""
import pytest
from playwright.sync_api import expect

from agentwiz.workshop import WorkshopError
from conftest import request, save_profile, save_roles, write_block
from test_page import WAIT, browser, context, open_page, tid  # noqa: F401 - fixtures
from test_library import install


def open_library(page):
    tid(page, "tab-workshop").click()
    tid(page, "library-open").click()
    dialog = tid(page, "library-dialog")
    expect(dialog).to_have_attribute("data-state", "ready", timeout=WAIT)
    return dialog


def test_the_dialog_lists_entries_by_group_with_statuses_and_role(ui, server, open_page):
    write_block(ui, "Calculate", {"format_version": 4, "name": "Calculate", "inputs": {},
                                  "outputs": {"out": {"contract": {"format": "text"}, "local_only": False}},
                                  "steps": [{"id": "s", "kind": "tool", "builtin": "current_datetime",
                                             "output": "out", "arguments": {}}]})
    install(ui, {"key": "find_and_replace"})
    page = open_page(ui)
    dialog = open_library(page)
    for group in ("llm", "tool", "transform"):
        expect(tid(dialog, "library-group-" + group)).to_be_visible()
    expect(tid(dialog, "library-entry-summarise_document")).to_have_attribute("data-status", "available")
    expect(tid(dialog, "library-entry-find_and_replace")).to_have_attribute("data-status", "installed")
    expect(tid(dialog, "library-entry-calculate")).to_have_attribute("data-status", "key_in_use")
    expect(tid(dialog, "library-tick-calculate")).to_be_disabled()               # asks for another key first
    tid(dialog, "library-as-calculate").fill("calc_two")
    expect(tid(dialog, "library-tick-calculate")).to_be_enabled()
    expect(tid(dialog, "library-role-settings")).to_be_visible()                 # Needs model setup


def test_review_then_add_with_labels_destination_and_outcomes(ui, server, open_page):
    save_profile(ui, "p", model="m", kind="openai_compat", base_url=server.url + "/v1", provider="")
    save_roles(ui, {"fast": "p"})
    page = open_page(ui)
    dialog = open_library(page)
    tid(dialog, "library-tick-summarise_document").check()
    tid(dialog, "library-tick-check_identifier_map").check()
    tid(dialog, "library-review").click()
    labels = tid(dialog, "library-labels-check_identifier_map")
    expect(labels).to_be_visible()
    assert labels.locator("td").all_text_contents().count("Yes") == 4
    destination = tid(dialog, "library-destination-summarise_document")
    expect(destination).to_contain_text(server.url + "/v1")
    expect(destination).to_contain_text("openai_compat")
    tid(dialog, "library-add").click()
    expect(tid(dialog, "library-outcome-summarise_document")).to_have_attribute("data-outcome", "added")
    expect(tid(dialog, "library-outcome-check_identifier_map")).to_have_attribute("data-outcome", "added")
    expect(tid(dialog, "library-entry-summarise_document")).to_have_attribute("data-status", "installed")


def test_a_change_after_review_reopens_it(ui, server, open_page):
    save_profile(ui, "p", model="m", kind="openai_compat", base_url=server.url + "/v1", provider="")
    save_roles(ui, {"fast": "p"})
    page = open_page(ui)
    dialog = open_library(page)
    tid(dialog, "library-tick-classify_text").check()
    tid(dialog, "library-review").click()
    expect(tid(dialog, "library-destination-classify_text")).to_be_visible()
    save_profile(ui, "p", model="other", kind="openai_compat", base_url=server.url + "/v1", provider="")
    tid(dialog, "library-add").click()
    expect(tid(dialog, "library-outcome-classify_text")).to_have_attribute("data-outcome", "confirmation_required")
    expect(tid(dialog, "library-changed-classify_text")).to_be_visible()
    expect(tid(dialog, "library-destination-classify_text")).to_contain_text("other")
    tid(dialog, "library-add").click()
    expect(tid(dialog, "library-outcome-classify_text")).to_have_attribute("data-outcome", "added")


def test_finish_adding_an_incomplete_entry(ui, open_page, monkeypatch):
    service = ui.state.workshop
    real = service.save_examples
    monkeypatch.setattr(service, "save_examples", lambda *a, **k: (_ for _ in ()).throw(WorkshopError("busy", 503)))
    assert install(ui, {"key": "calculate"})[0]["outcome"] == "partial"
    monkeypatch.setattr(service, "save_examples", real)
    page = open_page(ui)
    dialog = open_library(page)
    expect(tid(dialog, "library-entry-calculate")).to_have_attribute("data-status", "incomplete")
    tid(dialog, "library-finish-calculate").click()
    expect(tid(dialog, "library-outcome-calculate")).to_have_attribute("data-outcome", "added")
    expect(tid(dialog, "library-entry-calculate")).to_have_attribute("data-status", "installed")


def test_design_shows_the_library_source(ui, open_page):
    install(ui, {"key": "calculate"})
    page = open_page(ui, "workshop/calculate/design")
    expect(tid(page, "ws-library-source")).to_contain_text("calculate")
