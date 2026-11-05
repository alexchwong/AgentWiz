"""Saved contracts and the check route through HTTP: techspec 0.1.3 tests #64 and #65 (Part A, 7.8),
with the route-level halves of #62 and #63. Files are read with the exact reader (X1)."""
import json
import os
import stat
import threading
from decimal import Decimal

import pytest

from agentwiz import checks, jsonio
from agentwiz.workspace import open_workspace
from conftest import (JSON_CONTRACT, JSON_CONTRACT_NO_SCHEMA, TEXT_CONTRACT, HTTPFailure, configure, contract_files,
                      contract_item, failure_of, file_stat, get_state, mcp_state, request, save_contract, status_of,
                      write_ws_file)



def read_contract_file(ui, name):
    return jsonio.decode_file_exact((contract_files(ui) / f"{name}.json").read_bytes())


def edit_on_disk(ui, name, text):
    """What an editor does: change the file's content (and push its modification time on)."""
    path = contract_files(ui) / f"{name}.json"
    before = os.stat(path).st_mtime_ns
    path.write_bytes(text.encode("utf-8"))
    os.utime(path, ns=(before + 5_000_000_000, before + 5_000_000_000))


# 64 ---------------------------------------------------------------------------------------------
def test_contract_storage(ui):
    state = get_state(ui)
    assert state["contracts"]["items"] == [] and state["contracts"]["read_only"] is False
    assert state["contracts"]["path"] == str(ui.workspace.path / "contracts")
    assert state["contracts"]["checks"]["available"] is True and "jsonschema" in state["contracts"]["checks"]["detail"]
    assert not (ui.workspace.path / "contracts").exists()                      # created by the first save

    created = save_contract(ui, "fields_v1", JSON_CONTRACT)                     # revision null: create
    first = created["contract"]
    assert created["notes"] == [] and first["name"] == "fields_v1" and first["problem"] is None
    assert [c["name"] for c in created["contracts"]["items"]] == ["fields_v1"]
    assert contract_item(ui, "fields_v1") == {"name": "fields_v1", "revision": first["revision"], "format": "json",
                                              "description": "Fields pulled from a report", "problem": None}

    assert failure_of(lambda: save_contract(ui, "fields_v1", JSON_CONTRACT)).body["code"] == "exists"
    assert status_of(lambda: save_contract(ui, "fields_v1", JSON_CONTRACT)) == 409

    # Exact numbers go page -> server -> file -> page as values (R4-13).
    file_obj = read_contract_file(ui, "fields_v1")
    equals = next(c for c in file_obj["checks"] if c["type"] == "json_equals")["value"]
    assert equals == {"id": 9007199254740993, "ratio": Decimal("0.10000000000000000001")}
    assert str(equals["ratio"]) == "0.10000000000000000001" and isinstance(equals["id"], int)
    stored = request(ui, "/api/contracts/fields_v1")
    assert jsonio.decode_exact(stored["text"]) == file_obj and stored["text"] == first["text"]
    assert stored["revision"] == first["revision"] and stored["problem"] is None

    # The file's format: format_version first, indented, ASCII, newline at the end, mode 0600.
    raw = (contract_files(ui) / "fields_v1.json").read_bytes()
    assert raw.startswith(b'{\n  "format_version": 1,\n  "format": "json",') and raw.endswith(b"}\n") and raw.isascii()
    assert stat.S_IMODE(os.stat(contract_files(ui) / "fields_v1.json").st_mode) == 0o600
    assert sorted(p.name for p in contract_files(ui).iterdir()) == ["fields_v1.json"]      # no temporary files left

    # Update with the revision; a stale one is refused.
    updated = save_contract(ui, "fields_v1", JSON_CONTRACT.replace("Fields pulled", "Values pulled"), first["revision"])
    assert updated["contract"]["revision"] != first["revision"]
    assert contract_item(ui, "fields_v1")["description"] == "Values pulled from a report"
    stale = failure_of(lambda: save_contract(ui, "fields_v1", TEXT_CONTRACT, first["revision"]))
    assert (stale.status, stale.body["code"]) == (409, "stale_revision")
    assert failure_of(lambda: save_contract(ui, "nobody", TEXT_CONTRACT, 3)).body["code"] == "no_such_contract"
    assert read_contract_file(ui, "fields_v1")["description"] == "Values pulled from a report"

    # A save that changes nothing keeps the revision and the file (R4-25): even reformatted.
    current = updated["contract"]["revision"]
    before = file_stat(contract_files(ui) / "fields_v1.json")
    reformatted = JSON_CONTRACT.replace("Fields pulled", "Values pulled").replace("{\n  ", "{ ", 1)
    same = save_contract(ui, "fields_v1", reformatted, current)
    assert same["contract"]["revision"] == current
    assert file_stat(contract_files(ui) / "fields_v1.json") == before

    # Unknown top-level fields survive a save and are named in `notes`.
    kept = save_contract(ui, "fields_v1", '{"format": "text", "future": {"a": [1.50]}, "other": 1}', current)
    assert kept["notes"] == ['unknown top-level fields kept: "future", "other"']
    assert read_contract_file(ui, "fields_v1")["future"] == {"a": [Decimal("1.50")]}

    # Deleting and recreating never repeats a revision; a stale delete is refused.
    seen = {first["revision"], current, kept["contract"]["revision"]}
    assert failure_of(lambda: request(ui, "/api/contracts/delete", {"name": "fields_v1", "revision": current})
                      ).body["code"] == "stale_revision"
    request(ui, "/api/contracts/delete", {"name": "fields_v1", "revision": kept["contract"]["revision"]})
    assert contract_item(ui, "fields_v1") is None and not (contract_files(ui) / "fields_v1.json").exists()
    assert failure_of(lambda: request(ui, "/api/contracts/delete", {"name": "fields_v1", "revision": 1})
                      ).body["code"] == "no_such_contract"
    for _ in range(3):
        revision = save_contract(ui, "again", TEXT_CONTRACT)["contract"]["revision"]
        assert revision not in seen
        seen.add(revision)
        request(ui, "/api/contracts/delete", {"name": "again", "revision": revision})


def test_contract_changes_on_disk_are_noticed(ui):
    first = save_contract(ui, "watched", TEXT_CONTRACT)["contract"]
    assert contract_item(ui, "watched")["revision"] == first["revision"]
    edit_on_disk(ui, "watched", '{"format_version": 1, "format": "text", "description": "by hand"}')
    item = contract_item(ui, "watched")
    assert item["description"] == "by hand" and item["revision"] != first["revision"] and item["problem"] is None
    stale = failure_of(lambda: save_contract(ui, "watched", TEXT_CONTRACT, first["revision"]))
    assert (stale.status, stale.body["code"]) == (409, "stale_revision")
    assert request(ui, "/api/contracts/watched")["text"].count("by hand") == 1

    # A new file appears without a restart; a removed one disappears; names that are not contracts are ignored.
    (contract_files(ui) / "dropped_in.json").write_text('{"format_version": 1, "format": "text"}')
    (contract_files(ui) / "notes.txt").write_text("not a contract")
    (contract_files(ui) / ".hidden.json").write_text('{"format_version": 1, "format": "text"}')
    (contract_files(ui) / "bad name.json").write_text('{"format_version": 1, "format": "text"}')
    assert [c["name"] for c in get_state(ui)["contracts"]["items"]] == ["dropped_in", "watched"]
    (contract_files(ui) / "dropped_in.json").unlink()
    assert [c["name"] for c in get_state(ui)["contracts"]["items"]] == ["watched"]


def test_damaged_contracts_are_listed_never_written_and_repairable(ui):
    folder = contract_files(ui)
    folder.mkdir()
    cases = {"broken": ("{ not json", "invalid"),
             "no_version": ('{"format": "text"}', "invalid"),
             "bad_field": ('{"format_version": 1, "format": "text", "checks": [{"type": "nope"}]}', "invalid"),
             "toobig": ('{"format_version": 1, "format": "json", "checks": [{"type": "json_equals", "value": ' + "9" * 5000 + "}]}", "invalid"),
             "newer": ('{"format_version": 2, "format": "text"}', "newer_format"),
             "array": ("[1]", "invalid")}
    for name, (text, _code) in cases.items():
        (folder / f"{name}.json").write_text(text)
    (folder / "binary.json").write_bytes(b"\xff\xfe{")
    items = {c["name"]: c for c in get_state(ui)["contracts"]["items"]}
    assert set(items) == set(cases) | {"binary"}
    for name, (text, code) in cases.items():
        problem = items[name]["problem"]
        assert problem["code"] == code and problem["file"] == str(folder / f"{name}.json") and problem["message"]
        assert items[name]["format"] is None
    assert items["binary"]["problem"]["message"] == "the file is not UTF-8 text"
    assert "9999" not in json.dumps(items) and "nope" not in json.dumps(items)           # rules, never values
    assert items["bad_field"]["problem"]["message"].startswith("checks[0].type")

    opened = request(ui, "/api/contracts/broken")                                          # it can be opened
    assert opened["text"] == "{ not json" and opened["problem"]["code"] == "invalid"
    assert request(ui, "/api/contracts/binary")["text"] == "��{"                  # decoded with replacement
    before = {n: (folder / f"{n}.json").read_bytes() for n in cases}
    for name in cases:                                                                      # and is never written
        fail = failure_of(lambda: save_contract(ui, name, TEXT_CONTRACT, items[name]["revision"]))
        assert (fail.status, fail.body["code"]) == (409, "contract_unusable")
        assert fail.body["problem"]["code"] == cases[name][1] and "file" in fail.body["problem"]
        assert (folder / f"{name}.json").read_bytes() == before[name]

    edit_on_disk(ui, "broken", '{"format_version": 1, "format": "text"}')                  # repaired on disk: no restart
    repaired = contract_item(ui, "broken")
    assert repaired["problem"] is None and repaired["format"] == "text"
    assert save_contract(ui, "broken", TEXT_CONTRACT, repaired["revision"])["contract"]["problem"] is None
    request(ui, "/api/contracts/delete", {"name": "newer", "revision": items["newer"]["revision"]})   # damaged ones can be deleted
    assert contract_item(ui, "newer") is None and not (folder / "newer.json").exists()


def test_valid_replacement_never_overwrites_a_damaged_or_newer_file(ui):
    folder = contract_files(ui)
    folder.mkdir()
    for name, text in (("damaged", "{ nope"), ("future", '{"format_version": 2, "format": "text", "x": 1}')):
        (folder / f"{name}.json").write_text(text)
        item = contract_item(ui, name)
        bytes_before, stat_before = (folder / f"{name}.json").read_bytes(), file_stat(folder / f"{name}.json")
        fail = failure_of(lambda: save_contract(ui, name, TEXT_CONTRACT, item["revision"]))
        assert (fail.status, fail.body["code"]) == (409, "contract_unusable")
        assert (folder / f"{name}.json").read_bytes() == bytes_before
        assert file_stat(folder / f"{name}.json") == stat_before
        assert contract_item(ui, name)["revision"] == item["revision"]                      # nothing was touched
        edit_on_disk(ui, name, '{"format_version": 1, "format": "text"}')                  # once repaired...
        again = save_contract(ui, name, TEXT_CONTRACT, contract_item(ui, name)["revision"])  # ...the same save works
        assert read_contract_file(ui, name)["checks"][0]["value"] == "pong" and again["contract"]["problem"] is None


def test_contract_save_validation(ui):
    fail = failure_of(lambda: save_contract(ui, "x", '{"format": "text", "strict": true, "checks": [{"type": "nope"}]}'))
    assert (fail.status, fail.body["code"]) == (400, "contract_error")
    assert {i["location"] for i in fail.body["issues"]} == {"strict", "checks[0].type"}
    assert fail.body["issue_count"] == 2 and fail.body["error"].startswith("ContractError: ")
    assert failure_of(lambda: save_contract(ui, "x", "{ nope")).body["issues"][0]["location"] == "line 1, column 3"
    assert not contract_files(ui).exists()                                                  # nothing was written
    for bad_name in ("", ".x", "../x", "a/b", "a" * 65, "é", "with space"):
        assert status_of(lambda: save_contract(ui, bad_name, TEXT_CONTRACT)) == 400, bad_name
    for body in ({"text": TEXT_CONTRACT, "revision": None}, {"name": "x", "revision": None},
                 {"name": 5, "text": TEXT_CONTRACT, "revision": None}, {"name": "x", "text": 5, "revision": None},
                 {"name": "x", "text": TEXT_CONTRACT, "revision": "1"}, {"name": "x", "text": TEXT_CONTRACT, "revision": True}):
        assert status_of(lambda: request(ui, "/api/contracts/save", body)) == 400, body
    assert status_of(lambda: request(ui, "/api/contracts/delete", {"name": "x"})) == 400
    assert status_of(lambda: request(ui, "/api/contracts/delete", {"revision": 1})) == 400
    # A name that cannot exist is "no such contract", never a path.
    for name in ("nobody", "..", "a%2Fb", "", "x" * 100):
        assert status_of(lambda: request(ui, f"/api/contracts/{name}")) == 404, name
    assert status_of(lambda: request(ui, "/api/contracts/x", token=False)) == 403
    assert status_of(lambda: request(ui, "/api/contracts/save", {"name": "x", "text": TEXT_CONTRACT,
                                                                   "revision": None}, token=False)) == 403


def test_contract_routes_are_atomic_across_threads(ui):
    save_contract(ui, "race", TEXT_CONTRACT)
    current = contract_item(ui, "race")["revision"]
    texts = ['{"format": "text", "description": "one"}', '{"format": "text", "description": "two"}']
    barrier, results = threading.Barrier(2), {}

    def save(text):
        barrier.wait()
        results[text] = status_of(lambda: save_contract(ui, "race", text, current))

    threads = [threading.Thread(target=save, args=(t,)) for t in texts]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(results.values()) == [200, 409]
    winner = next(t for t, s in results.items() if s == 200)
    assert read_contract_file(ui, "race")["description"] == json.loads(winner)["description"]


def test_read_only_workspace_refuses_contract_changes(make_ui, tmp_path):
    root = tmp_path / "ro_ws"
    write_ws_file(root / "ro" / "workspace.json", "{ not json")
    workspace = open_workspace(root, "ro")
    make_ui.opened.append(workspace)
    ui = make_ui(workspace=workspace)
    assert get_state(ui)["contracts"]["read_only"] is True
    fail = failure_of(lambda: save_contract(ui, "x", TEXT_CONTRACT))
    assert (fail.status, fail.body["code"]) == (409, "read_only")
    assert not contract_files(ui).exists()
    (contract_files(ui)).mkdir()
    (contract_files(ui) / "there.json").write_text('{"format_version": 1, "format": "text"}')
    revision = contract_item(ui, "there")["revision"]                                       # it can still be listed
    assert request(ui, "/api/contracts/there")["problem"] is None
    fail = failure_of(lambda: request(ui, "/api/contracts/delete", {"name": "there", "revision": revision}))
    assert (fail.status, fail.body["code"]) == (409, "read_only")
    assert (contract_files(ui) / "there.json").exists()


def test_write_failure_is_reported(ui):
    contract_files(ui).parent.mkdir(parents=True, exist_ok=True)
    contract_files(ui).write_text("a file where the folder should be")
    fail = failure_of(lambda: save_contract(ui, "x", TEXT_CONTRACT))
    assert (fail.status, fail.body["code"]) == (500, "write_failed") and "could not write" in fail.body["error"]


# the route-level half of 62 ---------------------------------------------------------------------
def test_schema_rules_through_the_routes(ui):
    refused = '{"format": "json", "schema": {"$ref": "http://example.com/x.json"}}'
    fail = failure_of(lambda: save_contract(ui, "remote", refused))
    assert (fail.status, fail.body["code"]) == (400, "contract_error")
    assert fail.body["issues"][0]["location"] == 'schema["$ref"]'
    draft7 = ('{"format": "json", "schema": {"$ref": "#/$defs/x", "$defs": {"x": {"$schema": '
              '"http://json-schema.org/draft-07/schema#", "dependencies": {"a": ["b"]}}}}}')
    assert failure_of(lambda: save_contract(ui, "draft7", draft7)).body["code"] == "contract_error"
    assert not contract_files(ui).exists()
    # At load: the same text in a file is a problem, not a usable contract.
    contract_files(ui).mkdir()
    (contract_files(ui) / "draft7.json").write_text(draft7.replace('{"format"', '{"format_version": 1, "format"', 1))
    assert contract_item(ui, "draft7")["problem"]["code"] == "invalid"
    # In the Contracts tab's check.
    fail = failure_of(lambda: request(ui, "/api/contracts/check", {"contract_text": draft7, "text": "{}"}))
    assert (fail.status, fail.body["code"]) == (400, "contract_error")
    # A resource declaring 2020-12 is accepted everywhere.
    ok = draft7.replace("http://json-schema.org/draft-07/schema#", "https://json-schema.org/draft/2020-12/schema")
    ok = ok.replace(', "dependencies": {"a": ["b"]}', "")
    assert save_contract(ui, "fine", ok)["contract"]["problem"] is None
    assert request(ui, "/api/contracts/check", {"contract_text": ok, "text": "{}"})["check"]["overall"] == "pass"
    # An unresolvable local reference saves, and fails when applied.
    missing = '{"format": "json", "schema": {"$ref": "#/$defs/missing"}}'
    assert save_contract(ui, "dangling", missing)["contract"]["problem"] is None
    assert request(ui, "/api/contracts/check", {"contract_text": missing, "text": "{}"})["check"]["overall"] == "contract_error"


# the route-level half of 63 ---------------------------------------------------------------------
def test_schema_without_extra_through_the_routes(ui, monkeypatch):
    schema_text = '{"format": "json", "schema": {"type": "object", "required": ["x"]}}'
    monkeypatch.setattr(checks, "schema_support", lambda: (False, "jsonschema is not installed"))
    fail = failure_of(lambda: save_contract(ui, "needs_extra", schema_text))
    assert (fail.status, fail.body["code"]) == (400, "contract_error")
    assert fail.body['issues'][0]['location'] == 'schema'
    assert 'validation_unavailable' in fail.body['issues'][0]['message']
    assert 'repair the AgentWiz installation' in fail.body['issues'][0]['message']
    assert not contract_files(ui).exists()
    assert save_contract(ui, "plain", JSON_CONTRACT_NO_SCHEMA)["contract"]["problem"] is None   # no schema: no extra needed
    state = get_state(ui)["contracts"]["checks"]
    assert state["available"] is False and state["detail"] == "jsonschema is not installed"

    # A schema contract already in the workspace loads; its schema check is not_run, never pass.
    (contract_files(ui) / "has_schema.json").write_text(schema_text.replace('{"format"', '{"format_version": 1, "format"', 1))
    assert contract_item(ui, "has_schema")["problem"] is None
    result = request(ui, "/api/contracts/check", {"contract_text": schema_text, "text": '{"x": 1}'})["check"]
    assert result["overall"] == "incomplete"
    assert [e["result"] for e in result["checks"]] == ["pass", "not_run"]
    assert request(ui, "/api/contracts/check", {"contract_text": schema_text, "text": "nope"})["check"]["overall"] == "fail"


# 65 ---------------------------------------------------------------------------------------------
def test_contract_check_route(ui, server):
    configure(ui, server)
    state_before = get_state(ui)
    pasted = request(ui, "/api/contracts/check",
                     {"contract_text": JSON_CONTRACT_NO_SCHEMA, "text": '{"id": 9007199254740993, "ratio": 1.5}'})
    check = pasted["check"]
    assert check["overall"] == "fail"
    assert [(i["location"], i["check"]) for i in check["issues"]] == [("$.ratio", "checks[0]")]
    assert check["issues"][0]["message"] == "json_equals: expected 0.10000000000000000001; found 1.5"
    ok = request(ui, "/api/contracts/check",
                 {"contract_text": JSON_CONTRACT_NO_SCHEMA, "text": '{"id": 9007199254740993, "ratio": 0.10000000000000000001}'})
    assert ok["check"]["overall"] == "pass"
    assert request(ui, "/api/contracts/check", {"contract_text": TEXT_CONTRACT, "text": "pong"})["check"]["overall"] == "pass"
    assert server.requests == []                                                           # no backend was called
    assert get_state(ui)["revision"] == state_before["revision"] and not contract_files(ui).exists()   # nothing saved

    fail = failure_of(lambda: request(ui, "/api/contracts/check",
                                      {"contract_text": '{"format": "json", "checks": [{"type": "regex", "pattern": "(("}]}',
                                       "text": "x"}))
    assert (fail.status, fail.body["code"]) == (400, "contract_error")
    assert [i["location"] for i in fail.body["issues"]] == ["checks[0].pattern"]
    for body in ({"text": "x"}, {"contract_text": TEXT_CONTRACT}, {"contract_text": 1, "text": "x"},
                 {"contract_text": TEXT_CONTRACT, "text": 1}):
        assert status_of(lambda: request(ui, "/api/contracts/check", body)) == 400
    assert status_of(lambda: request(ui, "/api/contracts/check", {"contract_text": TEXT_CONTRACT, "text": "x"},
                                     token=False)) == 403
    huge = request(ui, "/api/contracts/check", {"contract_text": '{"format": "json"}', "text": "7" * 5000})["check"]
    assert huge["overall"] == "incomplete" and huge["checks"][0] == {"check": "parse", "result": "not_run", "issues": 1}


def test_mcp_routes_still_refuse_unreadable_numbers(ui):
    for text in ("1" * 5000, "1e9223372036854775808", "NaN"):
        fail = failure_of(lambda: request(ui, "/api/mcp/call", {"server": "s", "tool": "t", "revision": mcp_state(ui)["revision"],
                                                              "arguments_text": '{"n": %s}' % text}))
        assert (fail.status, fail.body["code"]) == (400, "bad_arguments"), text
    fail = failure_of(lambda: request(ui, "/api/mcp/call", {"server": "s", "tool": "t", "revision": mcp_state(ui)["revision"],
                                                          "arguments_text": '{"n": '}))
    assert (fail.status, fail.body["code"]) == (400, "bad_arguments") and "line 1, column" in fail.body["error"]
