"""The block definition routes and the state body's 0.1.4 keys: techspec 0.1.4, 15.2 and 15.3 (read side)."""
import pytest

from conftest import failure_of, get_state, request, write_block
from test_blocks import MODEL_FILES, model_block, script_block, tool_block

needs_checks = pytest.mark.needs_checks


def test_state_body_carries_roles_blocks_and_scripts(ui):
    state = get_state(ui)
    roles = state["roles"]
    assert {k: roles[k] for k in ("map", "revision", "read_only", "path", "problem")} == {
        "map": {}, "revision": 1, "read_only": False,
        "path": str(ui.workspace.path / "roles.json"), "problem": None}
    assert set(roles["definitions"]) == {"fast", "assistant", "reasoning"}
    assert roles["defaults"] == roles["definitions"]
    assert {u["role"] for u in roles["unresolved"]} == set(roles["definitions"])
    assert state["blocks"] == {"path": str(ui.workspace.path / "blocks"), "active_runs": 0}
    assert state["scripts"] == {"running": 0, "limit": 4}


def test_listing_and_detail(ui):
    write_block(ui, "b", model_block(description="Say it"), MODEL_FILES)
    write_block(ui, "t", tool_block())
    write_block(ui, "sc", script_block(), {"scripts/go.py": "print(1)\n"})
    write_block(ui, "broken", model_block(name="broken", extra=1), MODEL_FILES)
    listing = request(ui, "/api/blocks")
    rows = {r["name"]: r for r in listing["blocks"]}
    assert list(rows) == ["b", "broken", "sc", "t"] and listing["shown"] == listing["total"] == 4
    assert listing["path"] == str(ui.workspace.path / "blocks") and listing["active_runs"] == []
    assert rows["b"]["kind"] == "model" and rows["b"]["problem"] is None and rows["b"]["description"] == "Say it"
    assert rows["broken"]["problem"]["code"] == "invalid"
    assert request(ui, "/api/blocks?reload=1")["blocks"] == listing["blocks"]

    detail = request(ui, "/api/blocks/b")
    assert detail["name"] == "b" and detail["revision"] == rows["b"]["revision"] and detail["problem"] is None
    assert [f["path"] for f in detail["files"]] == ["block.json", "prompts/system.md", "prompts/user.md"]
    assert all(f["sha256"] and f["unavailable"] is None for f in detail["files"])
    assert detail["inputs"] == [{"name": "text", "description": "", "format": "text",
                                 "contract_text": detail["contracts"]["inputs"]["text"]["text"],
                                 "form": None, "form_notes": []}]
    assert detail["outputs"][0]["name"] == "out" and detail["outputs"][0]["format"] == "text"
    assert "description" not in detail["outputs"][0]
    assert detail["step"] == {"id": "s", "kind": "model", "role": "fast", "max_repairs": 2}
    assert detail["budget"] == {"max_seconds": 1800, "max_model_tokens": None}
    assert request(ui, "/api/blocks/t")["step"] == {"id": "call", "kind": "tool", "server": "docs", "tool": "lookup"}
    script = request(ui, "/api/blocks/sc")
    assert script["step"] == {"id": "run", "kind": "script", "file": "scripts/go.py", "timeout_s": 5}
    assert [f["path"] for f in script["files"]] == ["block.json", "scripts/go.py"]
    assert script["files"][1]["text"] is None or "print" in script["files"][1]["text"]


def test_a_block_with_a_problem_still_returns_the_files_it_can_read(ui):
    write_block(ui, "b", model_block(), {"prompts/system.md": "system text"})        # user.md is missing
    detail = request(ui, "/api/blocks/b")
    assert detail["problem"]["code"] == "unreadable"
    by_path = {f["path"]: f for f in detail["files"]}
    assert by_path["prompts/system.md"]["text"] == "system text"
    assert by_path["prompts/user.md"]["text"] is None and by_path["prompts/user.md"]["unavailable"]
    assert detail["step"] is None and detail["inputs"] == []


@needs_checks
def test_a_json_input_carries_its_form(ui):
    block = model_block()
    block["inputs"]["doc"] = {"description": "The doc", "contract": {
        "format": "json", "schema": {"type": "object", "properties": {"title": {"type": "string"}}}}}
    write_block(ui, "b", block, MODEL_FILES)
    inputs = {i["name"]: i for i in request(ui, "/api/blocks/b")["inputs"]}
    assert inputs["doc"]["format"] == "json" and inputs["doc"]["description"] == "The doc"
    assert isinstance(inputs["doc"]["form"], dict) and inputs["doc"]["form_notes"] == []
    assert inputs["text"]["form"] is None


@pytest.mark.parametrize("path", ["/api/blocks/bad%20name", "/api/blocks/..", "/api/blocks/a%2Fb", "/api/blocks/"])
def test_malformed_names_are_400(ui, path):
    assert failure_of(lambda: request(ui, path)).status in (400, 404)


def test_malformed_name_is_400_and_unknown_is_404(ui):
    assert failure_of(lambda: request(ui, "/api/blocks/bad%20name")).status == 400
    fail = failure_of(lambda: request(ui, "/api/blocks/nothing"))
    assert fail.status == 404 and fail.body["code"] == "no_such_block"


def test_an_edit_gives_a_new_revision_in_both_views(ui):
    folder = write_block(ui, "b", model_block(), MODEL_FILES)
    first = request(ui, "/api/blocks/b")["revision"]
    (folder / "prompts" / "user.md").write_text("changed {{inputs.text}}")
    second = request(ui, "/api/blocks/b")["revision"]
    assert second != first and request(ui, "/api/blocks")["blocks"][0]["revision"] == second
