"""Saved contract containers and the version 2 contract routes (overhaul spec 9, 11, 13; C07, C09, C10, C15,
C18-C20, C26). Files are read with the exact reader; races are forced with hooks and barriers, never sleeps."""
import json
import os
import shutil
import stat
import threading
from decimal import Decimal

import pytest

from agentwiz import contracts, jsonio
from agentwiz.contract_schema import contract_digest
from agentwiz.workspace import open_workspace
from conftest import (JSON_CONTRACT, JSON_CONTRACT_NO_SCHEMA, TEXT_CONTRACT, contract_detail, contract_files,
                      contract_item, failure_of, file_stat, get_state, mcp_state, request, save_contract, status_of,
                      write_block, write_ws_file)

EXACT = '{"format": "json", "schema": {"type": "number", "minimum": 0.10000000000000000001, "maximum": 9007199254740993}}'


def read_container(ui, name):
    return jsonio.decode_file_exact((contract_files(ui) / f"{name}.json").read_bytes())


def edit_on_disk(ui, name, data):
    """What an editor does: change the bytes (and push the modification time on)."""
    path = contract_files(ui) / f"{name}.json"
    before = os.stat(path).st_mtime_ns if path.exists() else 0
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    os.utime(path, ns=(before + 5_000_000_000, before + 5_000_000_000))


def container(name, family="General", definitions=(('{"format": "text"}', "first"),)):
    revisions = []
    for index, (text, label) in enumerate(definitions, 1):
        obj = jsonio.decode_exact(text)
        revisions.append({"revision": index, "label": label, "created_at": "2026-11-05T05:00:00Z",
                          "origin": "manual", "digest": contract_digest(obj), "contract": obj})
    return jsonio.encode_file_exact({"format_version": 2, "name": name, "family": family,
                                     "latest": len(revisions), "revisions": revisions})


# C09 ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", [TEXT_CONTRACT, JSON_CONTRACT, JSON_CONTRACT_NO_SCHEMA, EXACT,
                                  '{"format": "text", "exact": "", "min_length": 0}'])
def test_every_contract_kind_saves_and_reloads_exactly(ui, text):
    saved = save_contract(ui, "kind", text, label="Initial")
    assert (saved["revision"], saved["unchanged"], saved["publication"]) == (1, False, "committed")
    assert saved["digest"] == contract_digest(jsonio.decode_exact(text))
    detail = contract_detail(ui, "kind")
    assert jsonio.decode_exact(detail["definition_text"]) == jsonio.decode_exact(text)
    assert detail["digest"] == saved["digest"] and detail["file_token"] == saved["file_token"]
    assert detail["latest"] == detail["revision"] == 1 and detail["label"] == "Initial"
    raw = (contract_files(ui) / "kind.json").read_bytes()
    assert raw.startswith(b'{\n  "format_version": 2,') and raw.endswith(b"}\n") and raw.isascii()
    assert stat.S_IMODE(os.stat(contract_files(ui) / "kind.json").st_mode) == 0o600
    assert stat.S_IMODE(os.stat(contract_files(ui)).st_mode) == 0o700
    assert sorted(p.name for p in contract_files(ui).iterdir()) == ["kind.json"]          # no temporary files


def test_append_noop_restore_family_and_collisions(ui):
    first = save_contract(ui, "order_line", JSON_CONTRACT, family="Commercial", label="Initial definition")
    assert contract_item(ui, "order_line") == {
        "name": "order_line", "family": "Commercial", "latest": 1, "latest_digest": first["digest"], "format": "json",
        "kind": "json", "description": "Fields pulled from a report", "problem": None}
    assert failure_of(lambda: save_contract(ui, "order_line", JSON_CONTRACT, family="Commercial")).body["code"] \
        == "contract_exists"
    assert failure_of(lambda: save_contract(ui, "Order_Line", JSON_CONTRACT)).body["code"] == "contract_exists"

    # Equal digest is a no-op: even reformatted and with an equivalent numeric spelling.
    same = JSON_CONTRACT.replace('"maximum": 1', '"maximum": 1.0').replace("{\n  ", "{ ", 1)
    before = (contract_files(ui) / "order_line.json").read_bytes()
    noop = save_contract(ui, "order_line", same, first["file_token"], "Commercial", "ignored")
    assert noop["unchanged"] is True and noop["revision"] == 1 and noop["file_token"] == first["file_token"]
    assert (contract_files(ui) / "order_line.json").read_bytes() == before

    # A change appends, never mutates an earlier record.
    second = save_contract(ui, "order_line", JSON_CONTRACT.replace("Fields pulled", "Values pulled"),
                           first["file_token"], "Commercial", "Reworded")
    assert (second["revision"], second["unchanged"]) == (2, False)
    stored = read_container(ui, "order_line")
    assert stored["latest"] == 2 and [r["revision"] for r in stored["revisions"]] == [1, 2]
    assert jsonio.decode_file_exact(before)["revisions"][0] == stored["revisions"][0]
    stale = failure_of(lambda: save_contract(ui, "order_line", TEXT_CONTRACT, first["file_token"], "Commercial"))
    assert (stale.status, stale.body["code"], stale.body["file_token"]) == (409, "stale_contract", second["file_token"])
    bundled = failure_of(lambda: save_contract(ui, "order_line", TEXT_CONTRACT, second["file_token"], "Other"))
    assert (bundled.status, bundled.body["code"]) == (400, "invalid")                  # family is a separate save

    # Restore appends a copy; restoring the latest is unchanged.
    restored = request(ui, "/api/contracts/restore", {"format_version": 2, "name": "order_line",
                       "expected_file_token": second["file_token"], "revision": 1, "label": "Back"})
    assert (restored["revision"], restored["digest"]) == (3, first["digest"])
    again = request(ui, "/api/contracts/restore", {"format_version": 2, "name": "order_line",
                    "expected_file_token": restored["file_token"], "revision": 1, "label": "Again"})
    assert again["unchanged"] is True and again["revision"] == 3
    missing = failure_of(lambda: request(ui, "/api/contracts/restore", {"format_version": 2, "name": "order_line",
                         "expected_file_token": restored["file_token"], "revision": 9, "label": ""}))
    assert (missing.status, missing.body["code"]) == (404, "no_such_revision")

    # Family is grouping only: identities and digests are unchanged, the file token changes.
    moved = request(ui, "/api/contracts/family", {"format_version": 2, "name": "order_line",
                    "expected_file_token": restored["file_token"], "family": "Orders"})
    assert moved["unchanged"] is False and moved["file_token"] != restored["file_token"]
    assert [r["digest"] for r in moved["revisions"]] == [first["digest"], second["digest"], first["digest"]]
    assert get_state(ui)["contracts"]["families"] == ["General", "Orders"]
    assert contract_detail(ui, "order_line", 2)["digest"] == second["digest"]          # immutable numbered selection
    assert contract_detail(ui, "order_line")["revision"] == 3                          # latest at this read


def test_restart_keeps_identities_and_tokens_follow_bytes(ui):
    first = save_contract(ui, "keep", EXACT)
    second = save_contract(ui, "keep", TEXT_CONTRACT, first["file_token"])
    ui.state.contracts = contracts.ContractStore(ui.workspace.path)        # a fresh process cache
    detail = contract_detail(ui, "keep", 1)
    assert detail["digest"] == first["digest"] and "0.10000000000000000001" in detail["definition_text"]
    # Whitespace-only and history/family edits change the file token, never a digest.
    data = (contract_files(ui) / "keep.json").read_bytes()
    edit_on_disk(ui, "keep", data.replace(b'\n  "family"', b'\n    "family"'))
    reformatted = contract_detail(ui, "keep")
    assert reformatted["file_token"] != second["file_token"] and reformatted["digest"] == second["digest"]
    stale = failure_of(lambda: save_contract(ui, "keep", JSON_CONTRACT, second["file_token"]))
    assert (stale.status, stale.body["code"]) == (409, "stale_contract")
    # A valid hand-appended revision is accepted on the next read.
    stored = read_container(ui, "keep")
    obj = {"format": "text", "description": "by hand"}
    stored["revisions"].append({"revision": 3, "label": "hand", "created_at": "2026-11-05T06:00:00Z",
                                "origin": "manual", "digest": contract_digest(obj), "contract": obj})
    stored["latest"] = 3
    edit_on_disk(ui, "keep", jsonio.encode_file_exact(stored))
    assert contract_item(ui, "keep")["latest"] == 3 and contract_item(ui, "keep")["description"] == "by hand"


# C07, C10 -----------------------------------------------------------------------------------------
def test_damaged_old_and_newer_files_are_listed_never_written(ui):
    folder = contract_files(ui)
    folder.mkdir()
    good = container("x")
    changed_history = jsonio.decode_file_exact(container("tampered"))
    changed_history["revisions"][0]["contract"] = {"format": "text", "min_length": 1}           # digest now stale
    cases = {"broken": (b"{ not json", "invalid"),
             "old": (b'{"format_version": 1, "format": "text"}', "migration_required"),
             "newer": (b'{"format_version": 3, "name": "newer"}', "newer_format"),
             "duplicate": (good.replace(b'"family": "General"', b'"family": "General", "family": "General"'), "invalid"),
             "nonfinite": (good.replace(b'"latest": 1', b'"latest": NaN'), "invalid"),
             "misnamed": (good, "invalid"),
             "gap": (good.replace(b'"revision": 1', b'"revision": 2'), "invalid"),
             "rich": (container("rich", definitions=(('{"format": "json", "schema": {"type": "string", "pattern": "x"}}', ""),)), "invalid"),
             "tampered": (jsonio.encode_file_exact(changed_history), "digest_mismatch"),
             "badtime": (good.replace(b'05:00:00Z', b'25:00:00Z').replace(b'"x"', b'"badtime"'), "invalid")}
    for name, (data, _code) in cases.items():
        (folder / f"{name}.json").write_bytes(data)
    (folder / "binary.json").write_bytes(b"\xff\xfe{")
    items = {c["name"]: c for c in request(ui, "/api/contracts")["items"]}
    assert set(items) == set(cases) | {"binary"}
    for name, (_data, code) in cases.items():
        assert items[name]["problem"]["code"] == code, (name, items[name])
        assert items[name]["latest"] is None and items[name]["problem"]["file"] == str(folder / f"{name}.json")
    detail = contract_detail(ui, "broken")                                              # inspectable, no projection
    assert detail["problem"]["code"] == "invalid" and detail["revisions"] == [] and "definition_text" not in detail
    for name in cases:
        token = contract_detail(ui, name)["file_token"]
        before = (folder / f"{name}.json").read_bytes()
        fail = failure_of(lambda: save_contract(ui, name, TEXT_CONTRACT, token))
        assert (fail.status, fail.body["code"]) == (409, "contract_unusable") and fail.body["problem"]["code"]
        assert (folder / f"{name}.json").read_bytes() == before
    # A wholly malformed file is deleted only through the token check and reference scan.
    deleted = request(ui, "/api/contracts/delete", {"format_version": 2, "name": "broken",
                                                    "expected_file_token": contract_detail(ui, "broken")["file_token"]})
    assert deleted["deleted"] is True and not (folder / "broken.json").exists()


def test_oversized_and_invalid_source_are_refused_before_writing(ui):
    assert failure_of(lambda: save_contract(ui, "x", "{ nope")).body["issues"][0]["location"] == "line 1, column 3"
    rich = failure_of(lambda: save_contract(ui, "x", '{"format": "json", "schema": {"type": "string", "$ref": "#/x"}}'))
    assert (rich.status, rich.body["code"]) == (400, "unsupported_keyword")
    legacy = failure_of(lambda: save_contract(ui, "x", '{"format": "text", "strict": true}'))
    assert legacy.status == 400 and legacy.body["issues"][0]["location"] == "strict"
    big = failure_of(lambda: save_contract(ui, "x", '{"format": "text", "exact": "' + "a" * (1 << 20) + '"}'))
    assert (big.status, big.body["code"]) == (400, "too_large")
    dup = failure_of(lambda: save_contract(ui, "x", '{"format": "text", "format": "text"}'))
    assert dup.status == 400
    assert not contract_files(ui).exists()
    for bad in ("", ".x", "../x", "a/b", "a" * 65, "é", "with space"):
        assert status_of(lambda: save_contract(ui, bad, TEXT_CONTRACT)) == 400, bad


def test_container_limit_refuses_an_append_without_change(ui, monkeypatch):
    first = save_contract(ui, "limited", TEXT_CONTRACT)
    monkeypatch.setattr(contracts, "CONTAINER_LIMIT", len((contract_files(ui) / "limited.json").read_bytes()) + 10)
    before = (contract_files(ui) / "limited.json").read_bytes()
    fail = failure_of(lambda: save_contract(ui, "limited", JSON_CONTRACT, first["file_token"]))
    assert (fail.status, fail.body["code"]) == (400, "too_large")
    assert (contract_files(ui) / "limited.json").read_bytes() == before


# C26 ---------------------------------------------------------------------------------------------
def test_request_envelopes_are_strict(ui):
    base = {"format_version": 2, "name": "x", "family": "General", "expected_file_token": None,
            "definition_text": TEXT_CONTRACT, "label": ""}
    for body in ({**base, "format_version": 1}, {**base, "format_version": True}, {**base, "extra": 1},
                 {k: v for k, v in base.items() if k != "label"}, {**base, "expected_file_token": "latest"},
                 {**base, "name": 5}, {**base, "definition_text": 5}, {**base, "family": " padded"},
                 {**base, "label": "x" * 121}, {**base, "family": "a\nb"},
                 {"name": "x", "text": TEXT_CONTRACT, "revision": None}):
        assert status_of(lambda: request(ui, "/api/contracts/save", body)) == 400, body
    for route, body in (("/api/contracts/family", {"name": "x", "expected_file_token": None, "family": "F"}),
                        ("/api/contracts/restore", {"name": "x", "expected_file_token": "sha256:" + "0" * 64,
                                                    "revision": True, "label": ""}),
                        ("/api/contracts/delete", {"name": "x"}),
                        ("/api/contracts/check", {"definition_text": TEXT_CONTRACT, "value_text": "x", "x": 1})):
        assert status_of(lambda: request(ui, route, {"format_version": 2, **body})) == 400, route
    for name in ("nobody", "..", "a%2Fb", "x" * 100):
        fail = failure_of(lambda: request(ui, f"/api/contracts/{name}"))
        assert (fail.status, fail.body["code"]) == (404, "no_such_contract"), name
    save_contract(ui, "x", TEXT_CONTRACT)
    for query in ("revision=0", "revision=true", "revision=1&revision=2", "other=1"):
        assert status_of(lambda: request(ui, "/api/contracts/x?" + query)) == 400, query
    assert failure_of(lambda: request(ui, "/api/contracts/x?revision=2")).body["code"] == "no_such_revision"
    assert status_of(lambda: request(ui, "/api/contracts/x", token=False)) == 403
    assert status_of(lambda: request(ui, "/api/contracts", token=False)) == 403
    assert status_of(lambda: request(ui, "/api/contracts/save", {**base, "name": "y"}, token=False)) == 403
    listing = request(ui, "/api/contracts")
    assert listing["format_version"] == 2 and listing["read_only"] is False
    assert listing["validation"]["available"] is True


# C15 (deletion) -----------------------------------------------------------------------------------
def test_deletion_scans_references_and_refuses_incomplete_scans(ui):
    saved = save_contract(ui, "shared", TEXT_CONTRACT)
    pin = {"name": "shared", "revision": 1, "digest": saved["digest"]}
    block = {"format_version": 3, "name": "user", "inputs": {}, "outputs": {"out": {"contract": pin}},
             "steps": [{"id": "s", "kind": "script", "file": "scripts/go.py"}]}
    write_block(ui, "user", block, {"scripts/go.py": "print(1)"})
    newer = save_contract(ui, "shared", JSON_CONTRACT, saved["file_token"])
    used = contract_detail(ui, "shared")["used_by"]
    assert used["unreadable"] == []
    assert used["items"] == [{"block": "user", "generation": None, "current": True, "side": "outputs",
                              "port": "out", "revision": 1, "newer_available": True}]
    body = {"format_version": 2, "name": "shared", "expected_file_token": newer["file_token"]}
    refused = failure_of(lambda: request(ui, "/api/contracts/delete", body))
    assert (refused.status, refused.body["code"]) == (409, "contract_in_use")
    assert refused.body["blockers"][0]["block"] == "user" and (contract_files(ui) / "shared.json").exists()
    # A block definition that cannot be inspected makes absence of use unknown.
    (ui.workspace.path / "blocks" / "user" / "block.json").write_text("{ damaged")
    incomplete = failure_of(lambda: request(ui, "/api/contracts/delete", body))
    assert (incomplete.status, incomplete.body["code"]) == (409, "reference_scan_incomplete")
    assert incomplete.body["unreadable"] == [str(ui.workspace.path / "blocks" / "user" / "block.json")]
    # Historical run snapshots and unrelated blocks do not block deletion.
    write_block(ui, "user", {**block, "outputs": {"out": {"contract": {"format": "text"}}}}, {"scripts/go.py": "x"})
    deleted = request(ui, "/api/contracts/delete", body)
    assert deleted["deleted"] is True and contract_item(ui, "shared") is None
    stale = failure_of(lambda: request(ui, "/api/contracts/delete", body))
    assert (stale.status, stale.body["code"]) == (404, "no_such_contract")


_GEN_A, _GEN_B = "a" * 32, "b" * 32
_SCRIPT_BLOCK = {"format_version": 3, "name": "other", "inputs": {},
                 "outputs": {"out": {"contract": {"format": "text"}}},
                 "steps": [{"id": "s", "kind": "script", "file": "scripts/go.py"}]}


def _head(current, retained):
    return {"format_version": 2, "current": current, "retained": retained}


def _generation(folder, ident, definition):
    target = folder / ".workshop" / "revisions" / ident
    target.mkdir(parents=True, exist_ok=True)
    text = definition if isinstance(definition, str) else jsonio.encode_exact(definition)
    (target / "block.json").write_text(text)


# Each case leaves a block whose declarations cannot all be enumerated; none of them names the contract.
_UNINSPECTABLE = {
    "head_retained_empty": (_head(_GEN_A, []), {_GEN_A: _SCRIPT_BLOCK}),
    "head_current_not_first": (_head(_GEN_B, [_GEN_A, _GEN_B]), {_GEN_A: _SCRIPT_BLOCK, _GEN_B: _SCRIPT_BLOCK}),
    "head_retained_duplicate": (_head(_GEN_A, [_GEN_A, _GEN_A]), {_GEN_A: _SCRIPT_BLOCK}),
    "head_extra_member": ({**_head(_GEN_A, [_GEN_A]), "note": 1}, {_GEN_A: _SCRIPT_BLOCK}),
    "head_bad_identity": (_head("../x", ["../x"]), {}),
    "head_retained_too_many": (_head("0" * 32, [f"{i:032x}" for i in range(21)]),
                               {f"{i:032x}": _SCRIPT_BLOCK for i in range(21)}),
    "generation_array": (_head(_GEN_A, [_GEN_A]), {_GEN_A: "[1, 2]"}),
    "generation_missing": (_head(_GEN_A, [_GEN_A, _GEN_B]), {_GEN_A: _SCRIPT_BLOCK}),
    "generation_is_head": (_head(_GEN_A, [_GEN_A]), {_GEN_A: _head(_GEN_B, [_GEN_B])}),
    "inputs_not_object": ({**_SCRIPT_BLOCK, "inputs": []}, {}),
    "outputs_absent": ({k: v for k, v in _SCRIPT_BLOCK.items() if k != "outputs"}, {}),
    "declaration_not_object": ({**_SCRIPT_BLOCK, "inputs": {"x": "shared"}}, {}),
    "declaration_without_contract": ({**_SCRIPT_BLOCK, "inputs": {"x": {"description": "d"}}}, {}),
    "contract_not_object": ({**_SCRIPT_BLOCK, "inputs": {"x": {"contract": ["shared"]}}}, {}),
    "pin_name_unreadable": ({**_SCRIPT_BLOCK, "inputs": {"x": {"contract": {"name": 7, "revision": 1,
                                                                             "digest": "sha256:" + "0" * 64}}}}, {}),
    "newer_root_format": ({**_SCRIPT_BLOCK, "format_version": 4}, {}),
    "boolean_root_format": ({**_SCRIPT_BLOCK, "format_version": True}, {}),
    "root_array": ("[]", {}),
}


@pytest.mark.parametrize("case", list(_UNINSPECTABLE))
def test_deletion_refuses_when_a_definition_cannot_be_enumerated(ui, case):
    """R1: a structurally uninspectable head or definition makes the scan incomplete; deletion refuses with
    the contract's bytes and token unchanged, and Used by says the result is not exhaustive."""
    saved = save_contract(ui, "shared", TEXT_CONTRACT)
    root, generations = _UNINSPECTABLE[case]
    folder = write_block(ui, "other", root, {"scripts/go.py": "print(1)"})
    for ident, definition in generations.items():
        _generation(folder, ident, definition)
    path = contract_files(ui) / "shared.json"
    before = path.read_bytes()
    used = contract_detail(ui, "shared")["used_by"]
    assert used["items"] == [] and used["unreadable"] and used["complete"] is False
    body = {"format_version": 2, "name": "shared", "expected_file_token": saved["file_token"]}
    refused = failure_of(lambda: request(ui, "/api/contracts/delete", body))
    assert (refused.status, refused.body["code"]) == (409, "reference_scan_incomplete")
    assert all(p.startswith(str(folder)) for p in refused.body["unreadable"])
    assert path.read_bytes() == before and contract_detail(ui, "shared")["file_token"] == saved["file_token"]
    # Removing the damaged block establishes absence of use again; the positive path still deletes.
    shutil.rmtree(folder)
    assert contract_detail(ui, "shared")["used_by"] == {"items": [], "unreadable": [], "complete": True}
    assert request(ui, "/api/contracts/delete", body)["deleted"] is True


def test_reference_scan_reports_identifiable_pins_in_broken_blocks(tmp_path):
    """A broken definition is reported as uninspectable and still contributes the pins it plainly holds; a
    well-formed managed head is scanned current-once plus retained, with old string references counted."""
    from agentwiz.workshop_assets import reference_scan
    pin = {"name": "shared", "revision": 2, "digest": "sha256:" + "0" * 64}
    broken = {**_SCRIPT_BLOCK, "name": "broken", "inputs": {"a": {"contract": pin}, "b": "junk"}}
    write_block(tmp_path, "broken", broken)
    managed = write_block(tmp_path, "managed", _head(_GEN_A, [_GEN_A, _GEN_B]))
    _generation(managed, _GEN_A, {**_SCRIPT_BLOCK, "name": "managed"})
    _generation(managed, _GEN_B, {**_SCRIPT_BLOCK, "format_version": 1, "name": "managed",
                                  "outputs": {"out": {"contract": "shared"}}})
    blockers, unreadable = reference_scan(tmp_path / "blocks", "shared", latest=3)
    assert unreadable == [str(tmp_path / "blocks" / "broken" / "block.json")]
    assert [(b["block"], b["generation"], b["current"], b["port"], b["revision"], b["newer_available"])
            for b in blockers] == [("broken", None, True, "a", 2, True), ("managed", _GEN_B, False, "out", None, False)]
    # Linked generation folders are never followed.
    (managed / ".workshop" / "revisions" / _GEN_B / "block.json").unlink()
    (managed / ".workshop" / "revisions" / _GEN_B).rmdir()
    (managed / ".workshop" / "revisions" / _GEN_B).symlink_to(managed / ".workshop" / "revisions" / _GEN_A)
    _blockers, unreadable = reference_scan(tmp_path / "blocks", "shared")
    assert str(managed / ".workshop" / "revisions" / _GEN_B / "block.json") in unreadable


# C18, C19----------------------------------------------------------------------------------------
def test_two_tabs_append_without_a_lost_update(ui, monkeypatch):
    first = save_contract(ui, "race", TEXT_CONTRACT)
    entered, release = threading.Event(), threading.Event()
    original = contracts.before_contract_replace

    calls = []

    def hold(name):                       # runs in the server's handler thread: hold only the first writer
        calls.append(name)
        if len(calls) == 1:
            entered.set()
            assert release.wait(10)
        original(name)

    monkeypatch.setattr(contracts, "before_contract_replace", hold)
    results = {}

    def save(label, text):
        results[label] = failure_of(lambda: save_contract(ui, "race", text, first["file_token"])) \
            if label == "tab-b" else save_contract(ui, "race", text, first["file_token"])

    a = threading.Thread(target=save, args=("tab-a", '{"format": "text", "description": "one"}'), name="tab-a")
    a.start()
    assert entered.wait(10)
    save("tab-b", '{"format": "text", "description": "two"}')        # a competing registry writer: refused now
    assert (results["tab-b"].status, results["tab-b"].body["code"]) == (409, "busy")
    release.set()
    a.join(10)
    assert results["tab-a"]["revision"] == 2
    late = failure_of(lambda: save_contract(ui, "race", '{"format": "text", "description": "two"}',
                                            first["file_token"]))
    assert (late.status, late.body["code"]) == (409, "stale_contract")      # the stale tab must refresh and review
    stored = read_container(ui, "race")
    assert [r["contract"].get("description") for r in stored["revisions"]] == [None, "one"]


def test_faults_before_and_after_replacement(ui, monkeypatch):
    first = save_contract(ui, "fault", TEXT_CONTRACT)
    before = (contract_files(ui) / "fault.json").read_bytes()

    def broken(name):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(contracts, "before_contract_replace", broken)
    fail = failure_of(lambda: save_contract(ui, "fault", JSON_CONTRACT, first["file_token"]))
    assert (fail.status, fail.body["code"]) == (500, "write_failed")
    assert (contract_files(ui) / "fault.json").read_bytes() == before                 # the original is intact
    assert sorted(p.name for p in contract_files(ui).iterdir()) == ["fault.json"]
    monkeypatch.setattr(contracts, "before_contract_replace", lambda name: None)
    monkeypatch.setattr(contracts, "after_contract_replace", broken)
    uncertain = save_contract(ui, "fault", JSON_CONTRACT, first["file_token"])
    assert (uncertain["revision"], uncertain["publication"]) == (2, "durability_uncertain")
    monkeypatch.setattr(contracts, "after_contract_replace", lambda name: None)
    repeat = save_contract(ui, "fault", JSON_CONTRACT, uncertain["file_token"])       # no duplicate append
    assert repeat["unchanged"] is True and contract_item(ui, "fault")["latest"] == 2


# C20 ---------------------------------------------------------------------------------------------
def test_links_and_non_regular_files_are_refused(ui, tmp_path):
    folder = contract_files(ui)
    folder.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_bytes(container("linked"))
    (folder / "linked.json").symlink_to(outside)
    (folder / "dir.json").mkdir()
    items = {c["name"]: c for c in request(ui, "/api/contracts")["items"]}
    assert items["linked"]["problem"]["code"] == "unusable" and items["dir"]["problem"]["code"] == "unusable"
    fail = failure_of(lambda: save_contract(ui, "linked", TEXT_CONTRACT, None))
    assert fail.status == 409
    assert outside.read_bytes() == container("linked")
    # A linked contracts folder is never followed for writing.
    os.rename(folder, tmp_path / "real")
    os.symlink(tmp_path / "real", folder)
    fail = failure_of(lambda: save_contract(ui, "new", TEXT_CONTRACT))
    assert (fail.status, fail.body["code"]) == (409, "contract_unusable")
    assert not (tmp_path / "real" / "new.json").exists()


def test_read_only_workspace_allows_viewing_and_checking_only(make_ui, tmp_path):
    root = tmp_path / "ro_ws"
    write_ws_file(root / "ro" / "workspace.json", "{ not json")
    workspace = open_workspace(root, "ro")
    make_ui.opened.append(workspace)
    ui = make_ui(workspace=workspace)
    assert request(ui, "/api/contracts")["read_only"] is True
    fail = failure_of(lambda: save_contract(ui, "x", TEXT_CONTRACT))
    assert (fail.status, fail.body["code"]) == (409, "read_only")
    contract_files(ui).mkdir()
    (contract_files(ui) / "there.json").write_bytes(container("there"))
    detail = contract_detail(ui, "there")
    assert detail["problem"] is None and detail["read_only"] is True
    for route, body in (("/api/contracts/delete", {"name": "there", "expected_file_token": detail["file_token"]}),
                        ("/api/contracts/family", {"name": "there", "expected_file_token": detail["file_token"],
                                                   "family": "Other"}),
                        ("/api/contracts/restore", {"name": "there", "expected_file_token": detail["file_token"],
                                                    "revision": 1, "label": ""})):
        fail = failure_of(lambda: request(ui, route, {"format_version": 2, **body}))
        assert (fail.status, fail.body["code"]) == (409, "read_only"), route
    assert request(ui, "/api/contracts/check", {"format_version": 2, "definition_text": TEXT_CONTRACT,
                                                "value_text": "pong"})["check"]["overall"] == "pass"
    assert (contract_files(ui) / "there.json").exists()


def test_write_failure_is_reported(ui):
    contract_files(ui).parent.mkdir(parents=True, exist_ok=True)
    contract_files(ui).write_text("a file where the folder should be")
    fail = failure_of(lambda: save_contract(ui, "x", TEXT_CONTRACT))
    assert (fail.status, fail.body["code"]) == (409, "contract_unusable")


# the sample check (spec 13): no model call, save or acceptance --------------------------------------
def test_contract_check_route(ui, server):
    from conftest import configure
    configure(ui, server)
    def check(definition, value):
        return request(ui, "/api/contracts/check", {"format_version": 2, "definition_text": definition,
                                                    "value_text": value})
    result = check(JSON_CONTRACT, '{"id": 9007199254740993, "ratio": 1.5}')
    assert result["label"] == "Contract check" and result["check"]["overall"] == "fail"
    assert [(i["location"], i["check"]) for i in result["check"]["issues"]] == [("$.ratio", "schema")]
    assert "1.5" not in json.dumps(result["check"]["issues"])                       # values are redacted
    assert check(JSON_CONTRACT, '{"id": 9007199254740993, "ratio": 0.10000000000000000001}')["check"]["overall"] == "pass"
    assert check(TEXT_CONTRACT, '"pong"')["check"]["overall"] == "pass"              # Text: literal, not parsed
    assert check(JSON_CONTRACT_NO_SCHEMA, "7" * 5000)["check"]["overall"] == "incomplete"
    assert server.requests == [] and not contract_files(ui).exists()


def test_mcp_routes_still_refuse_unreadable_numbers(ui):
    for text in ("1" * 5000, "1e9223372036854775808", "NaN"):
        fail = failure_of(lambda: request(ui, "/api/mcp/call", {"server": "s", "tool": "t", "revision": mcp_state(ui)["revision"],
                                                              "arguments_text": '{"n": %s}' % text}))
        assert (fail.status, fail.body["code"]) == (400, "bad_arguments"), text
    fail = failure_of(lambda: request(ui, "/api/mcp/call", {"server": "s", "tool": "t", "revision": mcp_state(ui)["revision"],
                                                          "arguments_text": '{"n": '}))
    assert (fail.status, fail.body["code"]) == (400, "bad_arguments") and "line 1, column" in fail.body["error"]
