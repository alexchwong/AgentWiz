"""The addendum's agent document tools (3.3-3.5), their resolution and labels (3.1), the publication path and the
manager reservation (3.2, tests 3.6), and utility parity between a Tool step and agentic Chat (2.4). Driven through
HTTP against the deterministic fake model endpoint; orderings use hooks, never sleeps."""
from __future__ import annotations

import json
import threading

import pytest

from agentwiz import agent_documents as docs, agent_loop, agent_tools
from conftest import configure, failure_of, request, wait_until, write_block
from agent_helpers import final, run, sent_bodies, stage, start_conversation, text_turn, tool_call


@pytest.fixture
def agent(ui, server):
    configure(ui, server)
    return start_conversation(ui)


def trust_model(ui, server, model="m"):
    destination = {"kind": "openai_compat", "url": server.url + "/v1", "model": model, "provider": ""}
    grants = request(ui, "/api/workshop/destination-trust")
    request(ui, "/api/workshop/destination-trust", {"format_version": 1, "expected_revision": grants["revision"],
                                                     "destination": destination, "trusted": True})


def tool_results(server) -> list[dict]:
    """The tool envelopes the model received, in order."""
    messages = sent_bodies(server)[-1]["messages"]
    return [json.loads(m["content"]) for m in messages if m["role"] == "tool"]


def one_call(ui, server, conversation, name, arguments):
    server.reply(tool_call(name, arguments), text_turn("done"))
    events = run(ui, conversation, "go", [name])
    return tool_results(server)[-1], events


def documents(ui) -> list[dict]:
    return request(ui, "/api/agent/conversation")["conversation"]["documents"]


def staged_text(ui, conversation, ident) -> str:
    return ui.state.agent.conversation.read_text(ident)


# --- copy_to_draft (3.3) ----------------------------------------------------------------------------------------------
def test_copy_to_draft_copies_lines_verbatim_and_appends(ui, server, agent):
    _, source = stage(ui, agent, "one\r\ntwo\nthree\nfour")
    sid = source["document"]["id"]
    envelope, _ = one_call(ui, server, agent, "aw_copy_to_draft",
                           {"source_id": sid, "start_line": 2, "end_line": 3, "name": "part", "format": "md"})
    first = envelope["result"]
    assert first["lines_copied"] == 2 and staged_text(ui, agent, first["draft_id"]) == "two\nthree\n"
    envelope, _ = one_call(ui, server, agent, "aw_copy_to_draft",
                           {"source_id": sid, "start_line": 1, "end_line": 1, "name": "more", "format": "txt",
                            "append_to": first["draft_id"]})
    second = envelope["result"]
    assert staged_text(ui, agent, second["draft_id"]) == "two\nthree\none\n"
    assert staged_text(ui, agent, first["draft_id"]) == "two\nthree\n"            # nothing existing changes
    assert [d["kind"] for d in documents(ui)] == ["document", "draft", "draft"]


@pytest.mark.parametrize("arguments, code", [
    ({"start_line": 3, "end_line": 2}, "invalid_position"),
    ({"start_line": 1, "end_line": 9}, "invalid_position"),
])
def test_copy_to_draft_refusals_create_nothing(ui, server, agent, arguments, code):
    _, source = stage(ui, agent, "a\nb\n")
    envelope, _ = one_call(ui, server, agent, "aw_copy_to_draft",
                           {"source_id": source["document"]["id"], "name": "x", "format": "txt", **arguments})
    assert envelope["ok"] is False and envelope["error"]["code"] == code
    assert len(documents(ui)) == 1


def test_copy_to_draft_over_the_draft_limit_is_too_large_and_writes_no_file(ui, server, agent):
    _, source = stage(ui, agent, "x" * 150_000 + "\n" + "y" * 60_000 + "\n")
    envelope, _ = one_call(ui, server, agent, "aw_copy_to_draft",
                           {"source_id": source["document"]["id"], "start_line": 1, "end_line": 2, "name": "x",
                            "format": "txt"})
    assert envelope["error"]["code"] == "too_large"
    folder = ui.workspace.path / "agent_documents" / agent
    assert len(list(folder.iterdir())) == 1


# --- revise_draft (3.3) -----------------------------------------------------------------------------------------------
def test_revise_draft_applies_edits_in_order_from_a_document(ui, server, agent):
    _, source = stage(ui, agent, "Dear Sam,\nThe fee is 10.\nRegards\n", name="letter.md")
    envelope, _ = one_call(ui, server, agent, "aw_revise_draft", {
        "source_id": source["document"]["id"],
        "edits": [{"find": "Sam", "replace": "Alex"}, {"find": "Alex,", "replace": "Alex and team,"},
                  {"find": "e", "replace": "E", "expected_count": 7}]})
    result = envelope["result"]
    assert result["counts"] == [1, 1, 7]
    draft = next(d for d in documents(ui) if d["id"] == result["draft_id"])
    assert draft["format"] == "md" and draft["name"].startswith("letter")
    assert staged_text(ui, agent, result["draft_id"]) == "DEar AlEx and tEam,\nThE fEE is 10.\nREgards\n"


def test_an_edit_mismatch_refuses_the_whole_call(ui, server, agent):
    _, source = stage(ui, agent, "a a b\n")
    envelope, _ = one_call(ui, server, agent, "aw_revise_draft", {
        "source_id": source["document"]["id"], "edits": [{"find": "b", "replace": "c"}, {"find": "a", "replace": "z"}]})
    assert envelope["error"]["code"] == "edit_mismatch" and envelope["error"]["position"] == 2
    assert "a a b" not in envelope["error"]["message"]                      # never the input text
    assert len(documents(ui)) == 1


# --- labels (3.1) -----------------------------------------------------------------------------------------------------
def test_a_draft_from_a_local_only_source_is_labelled_and_promotes_before_reading(ui, server, agent):
    trust_model(ui, server)
    _, source = stage(ui, agent, "private\n", local_only=True)
    events = []
    server.reply(tool_call("aw_copy_to_draft", {"source_id": source["document"]["id"], "start_line": 1,
                                                "end_line": 1, "name": "copy", "format": "txt"}), text_turn("ok"))
    events = run(ui, agent, "copy", ["aw_copy_to_draft"])
    assert final(events)["stop_reason"] == "answered"
    draft = [d for d in documents(ui) if d["kind"] == "draft"][0]
    assert draft["local_only"] is True
    finished = [e for e in events if e["type"] == "action" and e.get("state") == "finished"]
    assert finished[0]["restricted"] is True


def test_an_untrusted_run_ends_before_reading_a_restricted_source(ui, server, agent, monkeypatch):
    _, source = stage(ui, agent, "private\n")
    item = ui.state.agent.conversation.item(source["document"]["id"])
    restricted = docs.Item(item.id, item.kind, item.name, item.format, item.lines, item.characters, item.size, True)
    monkeypatch.setattr(docs, "draft_inputs", lambda scope, tool, arguments: [restricted])
    read = []
    monkeypatch.setattr(docs.Conversation, "read_text", lambda self, ident: read.append(ident) or "")
    server.reply(tool_call("aw_copy_to_draft", {"source_id": item.id, "start_line": 1, "end_line": 1,
                                                "name": "copy", "format": "txt"}), text_turn("never"))
    events = run(ui, agent, "copy", ["aw_copy_to_draft"])
    assert final(events)["stop_reason"] == "restricted_result"
    assert read == [] and [d["kind"] for d in documents(ui)] == ["document"]
    assert len(sent_bodies(server)) == 1                                     # the result never reached the model


# --- compare_documents (3.4) ------------------------------------------------------------------------------------------
def test_compare_documents_returns_a_unified_diff(ui, server, agent):
    _, before = stage(ui, agent, "a\nb\nc\n")
    _, after = stage(ui, agent, "a\nB\nc\n", name="after.txt")
    envelope, _ = one_call(ui, server, agent, "aw_compare_documents",
                           {"before_id": before["document"]["id"], "after_id": after["document"]["id"],
                            "context_lines": 0})
    result = envelope["result"]
    assert result == {"changed": True, "diff": "--- before\n+++ after\n@@ -2 +2 @@\n-b\n+B",
                      "added_lines": 1, "removed_lines": 1}


# --- read_table (3.5) -------------------------------------------------------------------------------------------------
def test_read_table_pages_with_next_row_and_row_count_at_the_end(ui, server, agent):
    rows = "".join(f"{n},name {n}\n" for n in range(1, 6))
    _, table = stage(ui, agent, "id,name\n" + rows, name="t.csv")
    tid = table["document"]["id"]
    envelope, _ = one_call(ui, server, agent, "aw_read_table", {"document_id": tid, "max_rows": 2})
    first = envelope["result"]
    assert first["rows"] == [["1", "name 1"], ["2", "name 2"]] and first["next_row"] == 3
    assert first["eof"] is False and "row_count" not in first
    envelope, _ = one_call(ui, server, agent, "aw_read_table",
                           {"document_id": tid, "start_row": 4, "max_rows": 5, "columns": ["name"]})
    last = envelope["result"]
    assert last["columns"] == ["name"] and last["rows"] == [["name 4"], ["name 5"]]
    assert last["eof"] is True and last["row_count"] == 5 and last["next_row"] == 6
    envelope, _ = one_call(ui, server, agent, "aw_read_table",
                           {"document_id": tid, "start_row": 3, "max_rows": 3})
    assert envelope["result"]["eof"] is True and envelope["result"]["row_count"] == 5


def test_read_table_never_parses_past_the_lookahead(ui, server, agent):
    _, table = stage(ui, agent, "a\tb\n1\t2\n3\t4\n5\t6\nbroken\n", name="t.tsv")
    envelope, _ = one_call(ui, server, agent, "aw_read_table", {"document_id": table["document"]["id"], "max_rows": 2})
    assert envelope["ok"] is True and envelope["result"]["eof"] is False
    envelope, _ = one_call(ui, server, agent, "aw_read_table",
                           {"document_id": table["document"]["id"], "start_row": 3, "max_rows": 2})
    assert envelope["error"]["code"] == "invalid_csv" and envelope["error"]["position"] == 5


@pytest.mark.parametrize("text, name, arguments, code", [
    ("a,b\n1,2\n", "t.txt", {}, "not_a_table"),
    ("a,b\n1,2\n", "t.csv", {"start_row": 3}, "invalid_position"),
    ("a,b\n1,2\n", "t.csv", {"columns": ["c"]}, "invalid_arguments"),
    ("a,a\n1,2\n", "t.csv", {}, "invalid_csv"),
    ("a,b,c,d\n" + ",".join(["x" * 9000] * 4) + "\n", "t.csv", {}, "row_too_large"),
])
def test_read_table_refusals(ui, server, agent, text, name, arguments, code):
    _, table = stage(ui, agent, text, name=name)
    envelope, _ = one_call(ui, server, agent, "aw_read_table", {"document_id": table["document"]["id"], **arguments})
    assert envelope["ok"] is False and envelope["error"]["code"] == code


def test_the_four_tools_are_offered_only_with_staged_items(ui, agent):
    def offered():
        from conftest import get_state
        revision = get_state(ui)["revision"]
        return {c["name"] for c in request(ui, f"/api/agent/inventory?revision={revision}")["capabilities"]
                if c["kind"] == "builtin"}
    names = {"aw_copy_to_draft", "aw_revise_draft", "aw_compare_documents", "aw_read_table"}
    assert not names & offered() and "aw_create_draft" in offered()
    stage(ui, agent, "x\n")
    assert names <= offered()


# --- utility parity (2.4) ---------------------------------------------------------------------------------------------
def tool_block(name, utility, inputs):
    return {"format_version": 4, "name": name, "description": f"{utility} as a block",
            "inputs": {n: {"contract": c, "local_only": False} for n, c in inputs.items()},
            "outputs": {"out": {"contract": {"format": "json"}, "local_only": False}},
            "steps": [{"id": "call", "kind": "tool", "builtin": utility, "output": "out",
                       "arguments": {n: "{{inputs.%s}}" % n for n in inputs}}]}


@pytest.mark.parametrize("arguments", [
    {"text": "a-b-a", "find": "a", "replace": "x"},
    {"text": "a-b-a", "find": "a", "replace": "x", "expected_count": 3},
])
def test_a_tool_step_and_chat_return_the_same_result_or_code(ui, server, agent, arguments):
    from conftest import result_of, run_block
    block = tool_block("fr", "find_replace", {k: {"format": "json"} if k == "expected_count" else {"format": "text"}
                                              for k in arguments})
    write_block(ui, "fr", block)
    step = result_of(run_block(ui, "fr", {k: json.dumps(v) if k == "expected_count" else v
                                         for k, v in arguments.items()}))
    envelope, _ = one_call(ui, server, agent, "aw_find_replace", arguments)
    try:
        direct = {"ok": True, "result": agent_tools.run_utility("find_replace", arguments)}
    except agent_tools.ToolError as exc:
        direct = {"ok": False, "error": exc.view()}
    assert envelope == direct
    if direct["ok"]:
        assert step["outcome"] == "complete" and json.loads(step["outputs"]["out"]["text"]) == direct["result"]
    else:
        assert step["outcome"] != "complete" and direct["error"]["code"] in json.dumps(step)


def test_a_chat_result_over_its_limit_is_refused_with_the_remedy(ui, server, agent):
    envelope, _ = one_call(ui, server, agent, "aw_find_replace",
                           {"text": "a" * 40_000, "find": "a", "replace": "bb"})
    assert envelope["error"]["code"] == "too_large" and "Workshop or a workflow" in envelope["error"]["message"]
    assert agent_tools.run_utility("find_replace", {"text": "a" * 40_000, "find": "a", "replace": "bb"})["count"] \
        == 40_000                                                            # the Tool step limit is 1 MiB


# --- the publication path (3.2; tests 3.6) ----------------------------------------------------------------------------
def test_close_during_a_temporary_write_waits_and_the_commit_is_refused(ui, agent):
    conversation = ui.state.agent.conversation
    item = conversation.prepare_stage("late.txt", "txt", "late\n", False)
    closer = threading.Thread(target=conversation.close, daemon=True)
    closer.start()
    assert wait_until(lambda: conversation.closed)
    assert closer.is_alive()                                     # close waits for the pending write
    with pytest.raises(docs.StagingError) as exc:
        conversation.commit(item)
    conversation.end_write()
    closer.join(5)
    assert exc.value.code == "conversation_closed" and not closer.is_alive()
    assert not (ui.workspace.path / "agent_documents" / agent).exists()


def test_an_upload_is_refused_when_a_run_reserves_during_extraction(ui, server, agent):
    manager = ui.state.agent

    def extract(data, filename):
        from agentwiz import attachments
        document = attachments.extract(data, filename=filename)
        manager.reserved = agent_loop.Reservation(manager.conversation)         # a run reserved meanwhile
        return document, attachments.render(document)
    with pytest.raises(agent_loop.AgentError) as exc:
        manager.stage(agent, "a.txt", b"hello\n", False, extract=extract)
    manager.reserved = None
    assert exc.value.code == "run_active"
    assert documents(ui) == [] and list((ui.workspace.path / "agent_documents" / agent).iterdir()) == []


def test_idle_only_changes_are_refused_while_a_run_is_reserved_and_attach_verifies(ui, server, agent):
    manager = ui.state.agent
    from conftest import get_state
    body = {"conversation": agent, "revision": get_state(ui)["revision"], "message": "hi", "capabilities": []}
    reserved = manager.prepare_run(body, None)
    for change in (lambda: manager.start_conversation(),
                   lambda: manager.while_idle(agent, lambda: None),
                   lambda: manager.memory({"conversation": agent, "on": False})):
        with pytest.raises(agent_loop.AgentError) as exc:
            change()
        assert exc.value.code == "run_active"
    manager.abandon(reserved)
    manager.abandon(reserved)                                    # idempotent
    with pytest.raises(agent_loop.AgentError):
        manager.attach(reserved)                                 # the reservation is gone
    assert manager.reserved is None and manager.active is None
    manager.while_idle(agent, lambda: None)


def test_a_refused_attach_leaves_no_run_entry_or_writer(ui, server, agent, monkeypatch):
    manager = ui.state.agent
    from conftest import get_state
    body = {"conversation": agent, "revision": get_state(ui)["revision"], "message": "hi", "capabilities": []}
    reserved = manager.prepare_run(body, None)
    manager.cancel_all()                                         # shutdown between prepare_run and attach
    with pytest.raises(agent_loop.AgentError) as exc:
        reserved.run()
    assert exc.value.code == "shutting_down"
    assert sent_bodies(server) == [] and manager.reserved is None and manager.active is None
    assert ui.state.store.running_count() == 0


def test_lock_order_is_manager_then_conversation(ui, server, agent):
    manager, conversation = ui.state.agent, ui.state.agent.conversation
    held = threading.local()
    violations = []

    class Tracked:
        def __init__(self, lock, name):
            self.lock, self.name = lock, name

        def acquire(self, *args, **kwargs):
            stack = getattr(held, "stack", [])
            if self.name == "manager" and "conversation" in stack:
                violations.append(list(stack))
            got = self.lock.acquire(*args, **kwargs)
            if got:
                held.stack = [*stack, self.name]
            return got

        def release(self):
            self.lock.release()
            held.stack = [n for n in getattr(held, "stack", []) if n != self.name]

        __enter__ = acquire

        def __exit__(self, *exc):
            self.release()
    manager.lock = Tracked(manager.lock, "manager")
    tracked = Tracked(conversation.lock, "conversation")
    conversation.lock = tracked
    conversation.drained = threading.Condition(tracked)
    status, staged = stage(ui, agent, "hello\n")
    assert status == 200
    request(ui, "/api/agent/documents/update", {"format_version": 1, "conversation": agent, "id": staged["document"]["id"],
                                                "local_only": True})
    server.reply(text_turn("hi"))
    run(ui, agent, "hi")
    assert violations == []
