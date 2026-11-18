"""Reader additions (starter blocks addendum, 4.4): the four text formats, PDF extraction in a worker process, its
admission, body reception, cancellation and shutdown, and coverage. Real PDFs are the repository-built fixtures;
failing workers are fixture workers that replace the worker command. Orderings use files the fixture workers wait
for, hooks and bounded polling, never sleeps in the test itself (the trickling sender is the scenario)."""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import urllib.parse

import pytest

from agentwiz import attachments, pdf_reader
from agentwiz.tutorial_support import pdf_fixtures
from conftest import group_gone, request, upload, wait_until
from agent_helpers import final, run, sent_bodies, stage, start_conversation, text_turn, tool_call

AGENT_ROUTE = "/api/agent/documents"
DOCUMENTS_ROUTE = "/api/attachments/extract"


def raw(ui, route, data: bytes, filename: str, *, length: int | None = None, extra: dict | None = None,
        send_body: bool = True, timeout: float = 20.0):
    """One raw upload. Returns (status, body) or (None, None) when the connection closed without a response."""
    sock = socket.create_connection(ui.server_address[:2], timeout=timeout)
    try:
        headers = {"X-Session-Token": ui.token, "X-Filename": urllib.parse.quote(filename, safe=""),
                   "Content-Length": str(len(data) if length is None else length), **(extra or {})}
        sock.sendall((f"POST {route} HTTP/1.0\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items())
                      + "\r\n").encode("ascii") + (data if send_body else b""))
        return _response(sock)
    finally:
        sock.close()


def _response(sock):
    chunks = []
    while True:
        try:
            chunk = sock.recv(65536)
        except OSError:
            break
        if not chunk:
            break
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        return None, None
    return int(data.split(b" ", 2)[1]), json.loads(data.split(b"\r\n\r\n", 1)[1] or b"{}")


def agent_headers(conversation, local_only=False):
    return {"X-Conversation": conversation, "X-Local-Only": "true" if local_only else "false"}


def fixture_worker(monkeypatch, tmp_path, body: str):
    """Replace the worker with a fixture worker. It writes its pid to `pid`, so a test can check its group."""
    code = (f"import os, sys, time\nopen({str(tmp_path / 'pid')!r}, 'w').write(str(os.getpid()))\n"
            f"release = {str(tmp_path / 'release')!r}\n" + body)
    monkeypatch.setattr(pdf_reader, "WORKER_COMMAND", [sys.executable, "-I", "-c", code])
    return tmp_path


BLOCKING = "while not os.path.exists(release):\n    time.sleep(0.01)\nsys.stdout.write('{\"error\": \"x\"}')\n"


def worker_pid(folder) -> int:
    assert wait_until(lambda: (folder / "pid").exists() and (folder / "pid").read_text().strip())
    return int((folder / "pid").read_text())


def in_thread(fn):
    out = {}
    thread = threading.Thread(target=lambda: out.setdefault("result", fn()), daemon=True)
    thread.start()
    return thread, out


# --- text formats (4.1) ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("extension", ["tsv", "log", "yaml", "yml"])
def test_the_four_text_formats_on_both_routes(ui, server, extension):
    from conftest import configure
    configure(ui, server)
    text = "a\tb\nkey: [1, 2]\n"
    status, body = upload(ui, text.encode(), "f." + extension)
    assert status == 200 and body["format"] == extension and body["page_count"] is None
    assert body["insertion"]["text"].endswith(text) and body["coverage"] is None
    conversation = start_conversation(ui)
    status, body = stage(ui, conversation, text, name="f." + extension)
    assert status == 200 and body["document"]["format"] == extension and "coverage" not in body["document"]


# --- the fixtures (4.4) ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name, status, code, pages, with_text, warnings", [
    ("text", 200, None, 1, 1, []),
    ("multi_page", 200, None, 3, 3, []),
    ("partial", 200, None, 3, 2, [("no_text_layer", 2)]),
    ("no_text", 400, "no_extractable_text", None, None, None),
    ("encrypted", 400, "damaged_file", None, None, None),
    ("truncated", 400, "damaged_file", None, None, None),
    ("over_limit", 200, None, 301, 300, [("content_limited", 300)]),
])
def test_pdf_fixtures_on_the_documents_route(ui, name, status, code, pages, with_text, warnings):
    got, body = upload(ui, pdf_fixtures.FIXTURES[name](), name + ".pdf")
    assert got == status, body
    if code is not None:
        assert body["code"] == code
        return
    assert body["format"] == "pdf" and body["page_count"] == pages
    coverage = body["coverage"]
    assert (coverage["page_count"], coverage["pages_with_text"]) == (pages, with_text)
    assert [(w["code"], w["page"]) for w in coverage["warnings"]] == warnings
    assert coverage["pages_read"] == min(pages, 300)
    assert body["insertion"]["text"].count("--- page ") == with_text


def test_the_address_space_limit_admits_a_pdf_near_the_source_limit():
    data = pdf_fixtures.near_limit()
    assert len(data) <= pdf_reader.SOURCE_LIMIT
    document = attachments.extract(data, filename="big.pdf")
    assert document["page_count"] == 300 and len(document["blocks"]) == 300


def test_page_range_rendering():
    document = attachments.extract(pdf_fixtures.partial(), filename="p.pdf")
    whole = attachments.render(document)
    assert whole["included_pages"] == [1, 3] and whole["text"].startswith("--- page 1 ---\nFirst page text.")
    assert "--- page 2 ---" not in whole["text"]
    middle = attachments.render(document, (2, 3))
    assert middle["pages"] == [2, 3] and middle["included_pages"] == [3, 3]
    assert middle["text"] == "--- page 3 ---\nThird page text."
    empty = attachments.render(document, (2, 2))
    assert empty["text"] == "" and empty["included_pages"] is None and empty["notice"]
    for bad in ((0, 1), (3, 2), (1, 4)):
        with pytest.raises(attachments.AttachmentError) as exc:
            attachments.render(document, bad)
        assert exc.value.code == "invalid_page_range"


def test_a_pdf_over_16_mib_is_refused_before_its_body_on_both_routes(ui):
    from conftest import headers_only
    conversation = start_conversation(ui)
    for route, extra in ((DOCUMENTS_ROUTE, {}), (AGENT_ROUTE, agent_headers(conversation))):
        status, body = headers_only(ui, route, pdf_reader.SOURCE_LIMIT + 1, {"X-Filename": "big.pdf", **extra})
        assert status == 413 and body["code"] == "too_large"


# --- the worker (4.2) ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["timeout", "oversized", "crash"])
def test_worker_failures_are_extraction_failed_with_no_process_left(ui, monkeypatch, tmp_path, case):
    if case == "timeout":
        # Holds a small fixed amount of memory and never returns: killed at the deadline on every platform.
        monkeypatch.setattr(pdf_reader, "DEADLINE_S", 1.0)
        body = "held = bytearray(1 << 20)\nwhile True:\n    time.sleep(0.01)\n"
    elif case == "oversized":
        monkeypatch.setattr(pdf_reader, "STDOUT_LIMIT", 1000)
        body = "sys.stdout.write('x' * 100000)\nsys.stdout.flush()\nwhile True:\n    time.sleep(0.01)\n"
    else:
        body = "os.abort()\n"
    folder = fixture_worker(monkeypatch, tmp_path, body)
    status, reply = upload(ui, pdf_fixtures.text(), "f.pdf", timeout=20)
    assert status == 400 and reply["code"] == "extraction_failed"
    assert wait_until(lambda: group_gone(worker_pid(folder)))
    assert not ui.state.pdf.busy()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="RLIMIT_AS is enforced on Linux and WSL2 only; "
                    "elsewhere this worker would exhaust the test machine")
def test_a_runaway_allocation_is_stopped_by_the_address_space_limit(ui, monkeypatch, tmp_path):
    import resource
    body = ("import agentwiz.pdf_worker as w\nw.set_limits()\nheld = []\n"
            "while True:\n    held.append(bytearray(16 << 20))\n")
    folder = fixture_worker(monkeypatch, tmp_path, body)
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    status, reply = upload(ui, pdf_fixtures.text(), "f.pdf", timeout=20)
    assert status == 400 and reply["code"] == "extraction_failed"
    assert wait_until(lambda: group_gone(worker_pid(folder)))
    assert resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before < 64 << 10      # KiB: the parent is unaffected


# --- admission (4.2) ---------------------------------------------------------------------------------------------------
def test_admission_limits_concurrency_and_waiters_and_a_waiter_reads_no_body(ui, monkeypatch, tmp_path):
    ui.state.pdf = pdf_reader.Admission(slots=1, waiters=1, wait_s=0.5)
    folder = fixture_worker(monkeypatch, tmp_path, BLOCKING)
    data = pdf_fixtures.text()
    first, first_out = in_thread(lambda: upload(ui, data, "a.pdf", timeout=20))
    worker_pid(folder)
    # A waiter that never sends its body: it is refused after the wait, so it never tried to read it.
    waiter, waiter_out = in_thread(lambda: raw(ui, DOCUMENTS_ROUTE, data, "b.pdf", send_body=False))
    assert wait_until(lambda: ui.state.pdf.view()["waiting"] == 1)
    status, body = raw(ui, DOCUMENTS_ROUTE, data, "c.pdf")                    # the waiter limit is reached
    assert status == 503 and body["code"] == "extraction_busy"
    waiter.join(10)
    assert waiter_out["result"][0] == 503 and waiter_out["result"][1]["code"] == "extraction_busy"
    (folder / "release").write_text("")
    first.join(10)
    assert first_out["result"][0] == 400                                       # the fixture worker's malformed reply
    assert not ui.state.pdf.busy()


# --- shutdown (4.2) ----------------------------------------------------------------------------------------------------
def test_shutdown_kills_an_active_worker_and_refuses_a_queued_upload(ui, monkeypatch, tmp_path):
    ui.state.pdf = pdf_reader.Admission(slots=1, waiters=1, wait_s=30)
    folder = fixture_worker(monkeypatch, tmp_path, BLOCKING)
    data = pdf_fixtures.text()
    active, active_out = in_thread(lambda: raw(ui, DOCUMENTS_ROUTE, data, "a.pdf"))
    pid = worker_pid(folder)
    queued, queued_out = in_thread(lambda: raw(ui, DOCUMENTS_ROUTE, data, "b.pdf", send_body=False))
    assert wait_until(lambda: ui.state.pdf.view()["waiting"] == 1)
    ui.state.shutdown(grace_s=5)
    active.join(10)
    queued.join(10)
    assert active_out["result"][1]["code"] == "extraction_cancelled"
    assert queued_out["result"][1]["code"] == "shutting_down"
    assert group_gone(pid) and not ui.state.pdf.busy()


def test_shutdown_with_a_slot_granted_and_no_process_yet_kills_it_at_launch(ui, monkeypatch, tmp_path):
    fixture_worker(monkeypatch, tmp_path, BLOCKING)
    reached, go, launched = threading.Event(), threading.Event(), []
    real = pdf_reader.subprocess.Popen

    def held_popen(*args, **kwargs):
        reached.set()
        go.wait(10)
        launched.append(real(*args, **kwargs))
        return launched[-1]
    monkeypatch.setattr(pdf_reader.subprocess, "Popen", held_popen)
    thread, out = in_thread(lambda: raw(ui, DOCUMENTS_ROUTE, pdf_fixtures.text(), "a.pdf"))
    assert reached.wait(10)
    stopper, _ = in_thread(lambda: ui.state.shutdown(grace_s=5))
    assert wait_until(lambda: ui.state.pdf.view()["closed"])
    go.set()
    thread.join(10)
    stopper.join(10)
    assert out["result"][1]["code"] == "extraction_cancelled"
    # Killed as soon as its record was found cancelled, before it could run.
    assert launched and wait_until(lambda: group_gone(launched[0].pid)) and not ui.state.pdf.busy()
    assert not (tmp_path / "release").exists()


def test_shutdown_interrupts_a_trickling_pdf_body(ui):
    data = pdf_fixtures.text()
    sock = socket.create_connection(ui.server_address[:2], timeout=20)
    try:
        sock.sendall((f"POST {DOCUMENTS_ROUTE} HTTP/1.0\r\nX-Session-Token: {ui.token}\r\nX-Filename: a.pdf\r\n"
                      f"Content-Length: {len(data)}\r\n\r\n").encode("ascii") + data[:10])
        assert wait_until(lambda: ui.state.pdf.view()["running"] == 1)
        ui.state.shutdown(grace_s=5)
        status, body = _response(sock)
    finally:
        sock.close()
    assert status == 503 and body["code"] == "extraction_cancelled" and not ui.state.pdf.busy()


# --- body reception (4.2) ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("route", [DOCUMENTS_ROUTE, AGENT_ROUTE])
def test_a_trickling_sender_is_refused_at_the_body_deadline(ui, monkeypatch, route):
    import time
    from agentwiz.ui import server as server_module
    monkeypatch.setattr(server_module, "BODY_DEADLINE_S", 0.5)
    conversation = start_conversation(ui)
    extra = agent_headers(conversation) if route == AGENT_ROUTE else {}
    sock = socket.create_connection(ui.server_address[:2], timeout=20)
    done = threading.Event()

    def trickle():
        # One byte every 20 ms, far faster than the 30-second socket timeout, until the server gives up.
        for _ in range(500):
            if done.is_set():
                return
            try:
                sock.sendall(b"x")
            except OSError:
                return
            time.sleep(0.02)
    sock.sendall((f"POST {route} HTTP/1.0\r\nX-Session-Token: {ui.token}\r\nX-Filename: a.txt\r\n"
                  "Content-Length: 100000\r\n" + "".join(f"{k}: {v}\r\n" for k, v in extra.items())
                  + "\r\n").encode("ascii"))
    started = time.monotonic()
    sender = threading.Thread(target=trickle, daemon=True)
    sender.start()
    try:
        status, _ = _response(sock)               # the read-timeout outcome: closed without a response
        elapsed = time.monotonic() - started
    finally:
        done.set()
        sock.close()
        sender.join(5)
    assert status is None and 0.4 < elapsed < 5


# --- workspace operations (4.2) ------------------------------------------------------------------------------------
def test_a_workspace_switch_during_an_extraction_is_busy_and_the_extraction_completes(make_ui, monkeypatch, tmp_path):
    from agentwiz.workspace import open_workspace
    ui = make_ui()
    open_workspace(ui.workspace.path.parent, "second").close()
    folder = fixture_worker(monkeypatch, tmp_path, "while not os.path.exists(release):\n    time.sleep(0.01)\n"
                            "import agentwiz.pdf_worker as w, pypdfium2 as p, pypdfium2.raw\n"
                            "w._emit(w.extract(sys.stdin.buffer.read(), p))\n")
    thread, out = in_thread(lambda: upload(ui, pdf_fixtures.text(), "a.pdf", timeout=20))
    worker_pid(folder)
    from conftest import failure_of
    refused = failure_of(lambda: request(ui, "/api/workspaces/switch", {"format_version": 1, "name": "second"}))
    assert refused.status in (409, 503) and refused.body["code"] in ("workspace_busy", "busy")
    (folder / "release").write_text("")
    thread.join(20)
    assert out["result"][0] == 200 and out["result"][1]["page_count"] == 1
    assert ui.workspace.name != "second"


# --- staging (4.2) -------------------------------------------------------------------------------------------------
def test_coverage_reaches_the_staged_item_and_list_documents(ui, server):
    from conftest import configure
    configure(ui, server)
    conversation = start_conversation(ui)
    status, body = raw(ui, AGENT_ROUTE, pdf_fixtures.partial(), "p.pdf", extra=agent_headers(conversation))
    assert status == 200 and body["document"]["coverage"]["pages_with_text"] == 2
    listed = request(ui, "/api/agent/conversation")["conversation"]["documents"][0]
    assert listed["coverage"] == body["document"]["coverage"]
    server.reply(tool_call("aw_list_documents", {}), text_turn("ok"))
    run(ui, conversation, "list", ["aw_list_documents"])
    result = json.loads([m for m in sent_bodies(server)[-1]["messages"] if m["role"] == "tool"][-1]["content"])
    assert result["result"]["documents"][0]["coverage"]["warnings"] == [{"code": "no_text_layer", "page": 2}]


def test_a_pdf_without_text_is_refused_on_the_chat_route_and_nothing_is_staged(ui):
    conversation = start_conversation(ui)
    status, body = raw(ui, AGENT_ROUTE, pdf_fixtures.no_text(), "s.pdf", extra=agent_headers(conversation))
    assert status == 400 and body["code"] == "no_extractable_text"
    assert request(ui, "/api/agent/conversation")["conversation"]["documents"] == []


def test_nothing_is_staged_after_cancellation(ui, monkeypatch):
    conversation = start_conversation(ui)
    real = pdf_reader.check_result

    def cancelled_after_return(value):
        result = real(value)
        ui.state.pdf.close()                      # shutdown begins after the worker returned
        return result
    monkeypatch.setattr(pdf_reader, "check_result", cancelled_after_return)
    status, body = raw(ui, AGENT_ROUTE, pdf_fixtures.text(), "a.pdf", extra=agent_headers(conversation))
    assert status == 503 and body["code"] == "extraction_cancelled"
    assert request(ui, "/api/agent/conversation")["conversation"]["documents"] == []
    folder = ui.workspace.path / "agent_documents" / conversation
    assert sorted(p.name for p in folder.iterdir()) == []
