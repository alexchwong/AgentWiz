"""The block loader and store: techspec 0.1.4, tests #90 and #91 (R6-11, R7-2, D78, P7, P11).

Through `blocks.BlockStore` on a workspace folder under `tmp_path`; the HTTP views are tested with the routes
in test_block_routes.py."""
import copy
import hashlib
import json
import os
from decimal import Decimal

import pytest

from agentwiz import blocks, contracts
from conftest import write_block

SENTINEL = "SENTINEL-VALUE-7731"


def model_block(name="b", **over):
    block = {"format_version": 1, "name": name, "description": "d",
             "inputs": {"text": {"contract": {"format": "text"}}},
             "outputs": {"out": {"contract": {"format": "text"}}},
             "steps": [{"id": "s", "kind": "model", "role": "fast", "system": {"file": "prompts/system.md"},
                        "prompt": {"file": "prompts/user.md"}, "output": "out"}]}
    block.update(over)
    return block


MODEL_FILES = {"prompts/system.md": "You are terse.", "prompts/user.md": "Say {{inputs.text}}"}


def tool_block(name="t"):
    return {"format_version": 1, "name": name, "inputs": {"n": {"contract": {"format": "text"}}},
            "outputs": {"out": {"contract": {"format": "json"}}},
            "steps": [{"id": "call", "kind": "tool", "server": "docs", "tool": "lookup",
                       "arguments": {"id": "{{inputs.n}}", "extra": ["a", "{{inputs.n}}"]}, "output": "out"}]}


def script_block(name="sc"):
    return {"format_version": 1, "name": name, "inputs": {"a": {"contract": {"format": "text"}}},
            "outputs": {"x": {"contract": {"format": "text"}}, "y": {"contract": {"format": "json"}}},
            "steps": [{"id": "run", "kind": "script", "file": "scripts/go.py", "timeout_s": 5}]}


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return root


@pytest.fixture
def store(ws):
    contract_store = contracts.ContractStore(ws)
    contract_store.rescan()
    return blocks.BlockStore(ws, contract_store)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def problem_of(store, name="b"):
    loaded = store.get(name)
    assert loaded is not None and loaded.problem is not None, "the block unexpectedly loaded"
    return loaded.problem


def issues_of(problem):
    return {(i["location"]): i["message"] for i in problem.issues}


# --- #90 -----------------------------------------------------------------------------------------------
def test_valid_model_tool_and_script_blocks_load_with_exact_hashes(ws, store):
    write_block(ws, "b", model_block(), MODEL_FILES)
    write_block(ws, "t", tool_block())
    write_block(ws, "sc", script_block(), {"scripts/go.py": "print('hi')\n"})
    for name, kind in (("b", "model"), ("t", "tool"), ("sc", "script")):
        loaded = store.get(name)
        assert loaded.problem is None and loaded.block is not None and loaded.kind == kind, loaded.problem
        folder = ws / "blocks" / name
        assert loaded.block.files["block.json"] == sha((folder / "block.json").read_bytes())
        for rel, digest in loaded.block.files.items():
            assert digest == sha((folder / rel).read_bytes())
    model = store.get("b").block
    assert set(model.files) == {"block.json", "prompts/system.md", "prompts/user.md"}
    assert model.step.system == "You are terse." and model.step.prompt == "Say {{inputs.text}}"
    assert model.step.max_repairs == 2 and model.budget.max_seconds == 1800
    assert set(model.texts) == {"block.json", "prompts/system.md", "prompts/user.md"}
    script = store.get("sc").block
    assert set(script.files) == {"block.json", "scripts/go.py"} and script.step.timeout_s == 5
    assert script.step.script_path == (ws / "blocks" / "sc" / "scripts" / "go.py")
    assert [d.name for d in script.outputs] == ["x", "y"]
    assert store.get("t").block.step.arguments["extra"] == ["a", "{{inputs.n}}"]


def test_a_prompt_file_loses_its_byte_order_mark_but_keeps_its_hash(ws, store):
    data = b"\xef\xbb\xbfSay {{inputs.text}}"
    write_block(ws, "b", model_block(), {**MODEL_FILES, "prompts/user.md": data})
    block = store.get("b").block
    assert block.step.prompt == "Say {{inputs.text}}" and block.files["prompts/user.md"] == sha(data)


def mutate(fn):
    def apply(block):
        fn(block)
        return block
    return apply


STEP = lambda b: b["steps"][0]  # noqa: E731

CASES = {
    "unknown top-level key": (lambda b: b.update(extra=SENTINEL), "invalid", "extra"),
    "unknown declaration key": (lambda b: b["inputs"]["text"].update(extra=SENTINEL), "invalid",
                                "inputs.text.extra"),
    "unknown step key": (lambda b: STEP(b).update(extra=SENTINEL), "invalid", "steps[0].extra"),
    "unknown budget key": (lambda b: b.update(budget={"max_seconds": 5, "extra": SENTINEL}), "invalid",
                           "budget.extra"),
    "max_attempts is unknown": (lambda b: STEP(b).update(repair={"max_attempts": 2}), "invalid",
                                "steps[0].repair.max_attempts"),
    "max_repairs 6": (lambda b: STEP(b).update(repair={"max_repairs": 6}), "invalid", "steps[0].repair.max_repairs"),
    "max_repairs -1": (lambda b: STEP(b).update(repair={"max_repairs": -1}), "invalid",
                       "steps[0].repair.max_repairs"),
    "max_repairs 1.5": (lambda b: STEP(b).update(repair={"max_repairs": Decimal("1.5")}), "invalid",
                        "steps[0].repair.max_repairs"),
    "max_repairs true": (lambda b: STEP(b).update(repair={"max_repairs": True}), "invalid",
                         "steps[0].repair.max_repairs"),
    "a second step": (lambda b: b["steps"].append(copy.deepcopy(STEP(b))), "invalid", "steps"),
    "kind block": (lambda b: STEP(b).update(kind="block"), "invalid", "steps[0].kind"),
    "reference with ..": (lambda b: STEP(b).update(prompt={"file": "prompts/../block.json"}), "invalid",
                          "steps[0].prompt.file"),
    "reference with a leading slash": (lambda b: STEP(b).update(prompt={"file": "/etc/passwd"}), "invalid",
                                       "steps[0].prompt.file"),
    "reference outside prompts": (lambda b: STEP(b).update(prompt={"file": "scripts/user.md"}), "invalid",
                                  "steps[0].prompt.file"),
    "name differs from the folder": (lambda b: b.update(name="other"), "invalid", "name"),
    "two outputs on a model block": (lambda b: b["outputs"].update(o2={"contract": {"format": "text"}}), "invalid",
                                     "outputs"),
    "undeclared step output": (lambda b: STEP(b).update(output="nope"), "invalid", "steps[0].output"),
    "input name not an identifier": (lambda b: b["inputs"].update({"1x": {"contract": {"format": "text"}}}),
                                     "invalid", "inputs"),
    "task decimal not exactly a float": (lambda b: STEP(b).update(task={"temperature": Decimal("0.1000000000000000055511151231257827")}),
                                         "invalid", "steps[0].task.temperature"),
    "task key not a task setting": (lambda b: STEP(b).update(task={"colour": 1}), "invalid", "steps[0].task.colour"),
    "invalid inline contract": (lambda b: b["outputs"]["out"].update(contract={"format": "xml"}), "invalid",
                                "outputs.out.contract.format"),
    "undeclared placeholder": (lambda b: STEP(b).update(prompt={"text": "{{inputs.zzz}}"}), "invalid",
                               "steps[0].prompt"),
    "steps placeholder": (lambda b: STEP(b).update(prompt={"text": "{{steps.a.b}}"}), "invalid", "steps[0].prompt"),
    "malformed placeholder": (lambda b: STEP(b).update(prompt={"text": "{{ inputs.text }}"}), "invalid",
                              "steps[0].prompt"),
    "format_version 2": (lambda b: b.update(format_version=2), "newer_format", "format_version"),
    "format_version true": (lambda b: b.update(format_version=True), "invalid", "format_version"),
    "a missing saved contract": (lambda b: b["outputs"]["out"].update(contract="no_such_contract"), "unusable",
                                 "outputs.out.contract"),
}


@pytest.mark.parametrize("label", list(CASES))
def test_each_rule_is_a_located_problem_with_no_value_in_the_message(ws, store, label):
    fn, code, location = CASES[label]
    block = model_block()
    fn(block)
    write_block(ws, "b", block, MODEL_FILES)
    problem = problem_of(store)
    assert problem.code == code, problem
    assert location in issues_of(problem), issues_of(problem)
    assert SENTINEL not in json.dumps(problem.view())
    assert problem.file.endswith(os.path.join("blocks", "b", "block.json"))


def test_a_script_not_ending_in_py_and_a_missing_prompt(ws, store):
    block = script_block()
    block["steps"][0]["file"] = "scripts/go.sh"
    write_block(ws, "sc", block, {"scripts/go.sh": "x"})
    assert "steps[0].file" in issues_of(problem_of(store, "sc"))
    write_block(ws, "b", model_block(), {"prompts/system.md": "s"})          # user.md is missing
    problem = problem_of(store)
    assert problem.code == "unreadable" and "steps[0].prompt.file" in issues_of(problem)


@pytest.mark.parametrize("where", ["file", "folder", "block"])
def test_a_symbolic_link_as_any_component_is_unreadable_and_never_read(ws, store, tmp_path, where):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "user.md").write_text("OUTSIDE-SECRET")
    folder = write_block(ws, "b", model_block(), {"prompts/system.md": "s"})
    if where == "file":
        os.symlink(outside / "user.md", folder / "prompts" / "user.md")
    elif where == "folder":
        (folder / "prompts" / "system.md").rename(outside / "system.md")
        (folder / "prompts").rmdir()
        os.symlink(outside, folder / "prompts")
    else:
        os.replace(folder / "block.json", outside / "block.json")
        os.symlink(outside / "block.json", folder / "block.json")
    loaded = store.get("b")
    assert loaded.problem is not None and loaded.problem.code == "unreadable"
    assert "OUTSIDE-SECRET" not in json.dumps([v.view() for v in loaded.views])
    assert all(v.text is None or "OUTSIDE-SECRET" not in v.text for v in loaded.views)


def test_an_oversized_prompt_file_is_unreadable_and_not_shown(ws, store, monkeypatch):
    monkeypatch.setattr(blocks, "FILE_LIMIT", 64)
    write_block(ws, "b", model_block(), {**MODEL_FILES, "prompts/user.md": "x" * 65})
    loaded = store.get("b")
    assert loaded.problem.code == "unreadable"
    view = {v.path: v for v in loaded.views}["prompts/user.md"]
    assert view.text is None and view.unavailable and "larger" in view.unavailable
    write_block(ws, "b", model_block(), {**MODEL_FILES, "prompts/user.md": "x" * 64})
    assert store.get("b").problem is None


def test_an_oversized_block_json_is_unreadable(ws, store, monkeypatch):
    monkeypatch.setattr(blocks, "BLOCK_FILE_LIMIT", 100)
    write_block(ws, "b", model_block(), MODEL_FILES)
    assert problem_of(store).code == "unreadable"


def test_invalid_json_and_bad_numbers_are_located_without_values(ws, store):
    write_block(ws, "b", '{"format_version": 1, ', MODEL_FILES)
    assert problem_of(store).code == "invalid"
    write_block(ws, "b", '{"format_version": 1, "format_version": 1}', MODEL_FILES)
    assert problem_of(store).code == "invalid"
    write_block(ws, "b", '{"format_version": NaN}', MODEL_FILES)
    assert problem_of(store).code == "invalid"


def test_every_issue_is_listed_up_to_the_cap_with_the_total(ws, store, monkeypatch):
    block = model_block()
    for i in range(7):
        block[f"extra{i}"] = i
    block["steps"][0]["also"] = 1
    write_block(ws, "b", block, MODEL_FILES)
    problem = problem_of(store)
    assert problem.total == 8 and len(problem.issues) == 8
    monkeypatch.setattr(blocks, "ISSUE_LIMIT", 3)
    store2 = blocks.BlockStore(ws, store._contracts)
    capped = problem_of(store2)
    assert len(capped.issues) == 3 and capped.total == 8


def test_an_exact_number_in_an_inline_contract_survives_and_unknown_fields_are_notes(ws, store):
    block = model_block()
    block["outputs"]["out"]["contract"] = json.loads('{"format": "json", "future": 1}')
    text = json.dumps(block).replace('"future": 1', '"future": 9007199254740993, "checks": [{"type": "json_equals", "value": 0.10000000000000000001}]')
    write_block(ws, "b", text, MODEL_FILES)
    loaded = store.get("b")
    assert loaded.problem is None
    copy_ = loaded.block.contracts["outputs"]["out"]
    assert copy_.name is None and "9007199254740993" in copy_.text and "0.10000000000000000001" in copy_.text
    assert any("future" in note for note in loaded.notes)


def test_a_saved_contract_named_before_it_exists_becomes_usable_when_saved(ws, store):
    block = model_block()
    block["outputs"]["out"]["contract"] = "later"
    write_block(ws, "b", block, MODEL_FILES)
    first = store.get("b")
    assert first.problem.code == "unusable"
    store._contracts.save("later", '{"format": "text"}', None, False)
    second = store.get("b")                                               # no restart
    assert second.problem is None and second.revision != first.revision
    copy_ = second.block.contracts["outputs"]["out"]
    assert copy_.name == "later" and copy_.revision == store._contracts.get("later").revision


# --- #91 -----------------------------------------------------------------------------------------------
def test_revisions_follow_referenced_files_only(ws, store):
    folder = write_block(ws, "b", model_block(), MODEL_FILES)
    first = store.get("b")
    assert store.get("b").revision == first.revision                       # untouched: same revision
    assert store.listing()["blocks"][0]["revision"] == first.revision
    (folder / "prompts" / "helper.md").write_text("unreferenced")          # added: no change
    (folder / "scripts").mkdir(exist_ok=True)
    (folder / "scripts" / "other.py").write_text("x = 1")
    assert store.get("b").revision == first.revision
    (folder / "prompts" / "helper.md").write_text("unreferenced, edited")
    assert store.get("b").revision == first.revision
    (folder / "prompts" / "user.md").write_text("Say it twice: {{inputs.text}} {{inputs.text}}")
    edited = store.get("b")
    assert edited.revision != first.revision and edited.block.step.prompt.startswith("Say it twice")


def test_a_revision_is_never_repeated_after_delete_and_recreate(ws, store):
    import shutil
    folder = write_block(ws, "b", model_block(), MODEL_FILES)
    seen = [store.get("b").revision]
    for _ in range(3):
        shutil.rmtree(folder)
        assert store.get("b") is None
        folder = write_block(ws, "b", model_block(), MODEL_FILES)
        seen.append(store.get("b").revision)
    assert len(set(seen)) == len(seen)


def test_saved_contract_changes_give_the_block_a_new_effective_revision(ws, store):
    block = model_block()
    block["outputs"]["out"]["contract"] = "c1"
    store._contracts.save("c1", '{"format": "text"}', None, False)
    write_block(ws, "b", block, MODEL_FILES)
    seen = [store.get("b").revision]
    entry = store._contracts.get("c1")
    store._contracts.save("c1", '{"format": "json"}', entry.revision, False)           # edited
    seen.append(store.get("b").revision)
    (ws / "contracts" / ("c1" + contracts.SUFFIX)).write_text("{damaged")             # damaged
    damaged = store.get("b")
    seen.append(damaged.revision)
    assert damaged.problem is not None and damaged.problem.code == "unusable"
    (ws / "contracts" / ("c1" + contracts.SUFFIX)).write_text('{"format_version": 1, "format": "text"}')   # repaired
    repaired = store.get("b")
    seen.append(repaired.revision)
    assert repaired.problem is None
    assert len(set(seen)) == len(seen), seen


def test_reload_notices_an_edit_that_kept_size_and_mtime(ws, store):
    folder = write_block(ws, "b", model_block(), {**MODEL_FILES, "prompts/user.md": "Say aaaa {{inputs.text}}"})
    path = folder / "prompts" / "user.md"
    first = store.get("b")
    st = os.stat(path)
    inode_before = st.st_ino
    path.write_bytes(b"Say bbbb {{inputs.text}}")                           # same size, same inode
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))                     # same modification time
    assert os.stat(path).st_ino == inode_before
    assert store.get("b").revision == first.revision                       # the cheap fingerprint cannot see it
    reloaded = store.get("b", reload=True)
    assert reloaded.revision != first.revision and "bbbb" in reloaded.block.step.prompt
    assert store.listing(reload=True)["blocks"][0]["revision"] == reloaded.revision


def test_a_linked_block_folder_is_listed_unreadable_and_its_target_not_read(ws, store, tmp_path):
    target = tmp_path / "elsewhere"
    write_block(target.parent, "elsewhere", model_block(name="linked"), MODEL_FILES)
    (ws / "blocks").mkdir(exist_ok=True)
    os.symlink(target.parent / "blocks" / "elsewhere", ws / "blocks" / "linked")
    row = store.listing()["blocks"][0]
    assert row["name"] == "linked" and row["problem"]["code"] == "unreadable"
    loaded = store.get("linked")
    assert loaded.block is None and loaded.views == ()
    assert "Say" not in json.dumps(row)


def test_a_folder_without_block_json_is_not_a_block(ws, store):
    (ws / "blocks" / "plain").mkdir(parents=True)
    write_block(ws, "real", model_block(name="real"), MODEL_FILES)
    listing = store.listing()
    assert [r["name"] for r in listing["blocks"]] == ["real"] and listing["total"] == 1
    assert store.get("plain") is None and store.get("missing") is None


def test_the_listing_reports_shown_and_total_and_unlisted_blocks_still_open(ws, store, monkeypatch):
    monkeypatch.setattr(blocks, "LIST_LIMIT", 2)
    monkeypatch.setattr(blocks, "CACHE_LIMIT", 2)
    for name in ("a", "b", "c", "d"):
        write_block(ws, name, model_block(name=name), MODEL_FILES)
    listing = store.listing()
    assert [r["name"] for r in listing["blocks"]] == ["a", "b"] and listing["shown"] == 2 and listing["total"] == 4
    far = store.get("d")                                                  # outside the listing, opened by name
    assert far.problem is None and far.block is not None
    for name in ("a", "b", "c"):                                          # evict "d" from the cache of two
        store.get(name)
    assert "d" not in store._cache
    again = store.get("d")
    assert again.revision == far.revision                                  # the revision survives eviction
    assert again.block.files == far.block.files


@pytest.mark.parametrize("name", ["", "..", "a/b", "a\\b", "../x", ".hidden", "x" * 200, "bad name", "a\x00b", 5, None])
def test_invalid_names_are_refused(ws, store, name):
    write_block(ws, "b", model_block(), MODEL_FILES)
    assert store.get(name) is None
