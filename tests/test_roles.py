"""Roles through HTTP: techspec 0.1.4, test #89 (R6-1, P11)."""
import json

import pytest

from conftest import (failure_of, file_stat, get_state, request, save_profile, save_roles,
                      write_ws_file)

FIELDS = {"kind": "openai_compat", "base_url": "http://127.0.0.1:9/v1", "provider": ""}


def two_profiles(ui):
    save_profile(ui, "fast-p", model="small", **FIELDS)
    save_profile(ui, "big-p", model="large", **FIELDS)


def roles_file(ui):
    return ui.workspace.path / "roles.json"


def test_save_two_roles_and_survive_restart(ui, restart):
    two_profiles(ui)
    assert get_state(ui)["roles"]["map"] == {}
    state = save_roles(ui, {"fast": "fast-p", "reasoning": "big-p"})
    assert state["roles"]["map"] == {"fast": "fast-p", "reasoning": "big-p"}
    assert state["roles"]["revision"] == 2
    assert state["roles"]["problem"] is None and state["roles"]["unresolved"] == []
    assert json.loads(roles_file(ui).read_text()) == {"format_version": 1,
                                                      "roles": {"fast": "fast-p", "reasoning": "big-p"}}
    again = restart(ui)
    assert get_state(again)["roles"]["map"] == {"fast": "fast-p", "reasoning": "big-p"}


def test_stale_revision_writes_nothing(ui):
    two_profiles(ui)
    fail = failure_of(lambda: save_roles(ui, {"fast": "fast-p"}, revision=7))
    assert fail.status == 409 and fail.body["code"] == "stale_revision"
    assert not roles_file(ui).exists()
    assert get_state(ui)["roles"]["revision"] == 1


def test_role_naming_a_missing_profile_names_the_role(ui):
    two_profiles(ui)
    fail = failure_of(lambda: save_roles(ui, {"fast": "fast-p", "slow": "no-such-profile"}))
    assert fail.status == 400 and fail.body["code"] == "no_such_profile"
    assert "slow" in fail.body["error"] and "no-such-profile" not in fail.body["error"]
    assert not roles_file(ui).exists()


@pytest.mark.parametrize("body", [[], "x", {"bad name": "fast-p"}, {"fast": "bad name"}, {"fast": 3}, {"fast": None}])
def test_invalid_role_maps_are_400(ui, body):
    two_profiles(ui)
    fail = failure_of(lambda: save_roles(ui, body))
    assert fail.status == 400
    assert not roles_file(ui).exists()


def test_more_than_the_limit_is_refused(ui):
    two_profiles(ui)
    fail = failure_of(lambda: save_roles(ui, {f"r{i}": "fast-p" for i in range(101)}))
    assert fail.status == 400 and "100" in fail.body["error"]


def test_a_save_that_changes_nothing_keeps_revision_and_file(ui):
    two_profiles(ui)
    save_roles(ui, {"fast": "fast-p"})
    before, rev = file_stat(roles_file(ui)), get_state(ui)["roles"]["revision"]
    state = save_roles(ui, {"fast": "fast-p"})
    assert state["roles"]["revision"] == rev
    assert file_stat(roles_file(ui)) == before


def test_unknown_top_level_fields_survive_a_save(make_ui, tmp_path):
    from agentwiz.workspace import open_workspace
    root = tmp_path / "keep"
    ws = open_workspace(root, "w")
    make_ui.opened.append(ws)
    write_ws_file(ws.path / "roles.json", json.dumps({"format_version": 1, "roles": {}, "note": {"a": 1}}))
    ui = make_ui(workspace=ws)
    two_profiles(ui)
    save_roles(ui, {"fast": "fast-p"})
    assert json.loads(roles_file(ui).read_text()) == {"format_version": 1, "roles": {"fast": "fast-p"},
                                                      "note": {"a": 1}}


@pytest.mark.parametrize("text, code", [
    ("{not json", "invalid_json"),
    (json.dumps({"format_version": 1}), "invalid_field"),
    (json.dumps({"format_version": 1, "roles": {"fast": 5}}), "invalid_field"),
    (json.dumps({"format_version": 2, "roles": {}}), "newer_format"),
    (json.dumps({"format_version": True, "roles": {}}), "invalid_field"),
])
def test_a_damaged_file_is_a_problem_and_is_never_written(make_ui, tmp_path, text, code):
    from agentwiz.workspace import open_workspace
    ws = open_workspace(tmp_path / "bad", "w")
    make_ui.opened.append(ws)
    write_ws_file(ws.path / "roles.json", text)
    ui = make_ui(workspace=ws)
    state = get_state(ui)
    assert state["roles"]["read_only"] is True and state["roles"]["problem"]["code"] == code
    assert any(p["file"] == "roles.json" and p["code"] == code for p in state["workspace"]["problems"])
    save_profile(ui, "fast-p", model="small", **FIELDS)
    before = file_stat(roles_file(ui))
    fail = failure_of(lambda: save_roles(ui, {"fast": "fast-p"}))
    assert fail.status == 409 and fail.body["code"] == "read_only"
    assert file_stat(roles_file(ui)) == before


def test_deleting_a_mapped_profile_lists_the_role_as_unresolved(ui):
    two_profiles(ui)
    save_roles(ui, {"fast": "fast-p", "reasoning": "big-p"})
    rev = get_state(ui)["revision"]
    state = request(ui, "/api/profiles/delete", {"name": "big-p", "revision": rev})
    unresolved = state["roles"]["unresolved"]
    assert [u["role"] for u in unresolved] == ["reasoning"]
    assert "big-p" not in unresolved[0]["reason"]
    assert state["roles"]["map"]["reasoning"] == "big-p"          # the file is not rewritten behind the owner


def test_a_profile_without_a_model_is_unresolved(ui):
    save_profile(ui, "blank", model="", **FIELDS)
    save_roles(ui, {"fast": "blank"})
    assert get_state(ui)["roles"]["unresolved"] == [{"role": "fast", "reason": "role fast's profile has no model"}]


def test_a_read_only_workspace_refuses_the_save(make_ui, tmp_path):
    from agentwiz.workspace import open_workspace
    ws = open_workspace(tmp_path / "ro", "w")
    make_ui.opened.append(ws)
    ws.close()
    write_ws_file(ws.path / "workspace.json", "{broken")
    ws2 = open_workspace(tmp_path / "ro", "w")
    make_ui.opened.append(ws2)
    ui = make_ui(workspace=ws2)
    assert get_state(ui)["workspace"]["read_only"] is True
    fail = failure_of(lambda: save_roles(ui, {}))
    assert fail.status == 409 and fail.body["code"] == "read_only"
    assert not roles_file(ui).exists()


def test_a_role_save_changes_no_other_revision(ui):
    two_profiles(ui)
    before = get_state(ui)
    after = save_roles(ui, {"fast": "fast-p"})
    assert after["revision"] == before["revision"]
    assert after["task"]["revision"] == before["task"]["revision"]
    assert after["mcp"]["revision"] == before["mcp"]["revision"]
    assert after["contracts"] == before["contracts"]


def test_resolve_role_builds_the_profile_snapshot(ui):
    from agentwiz.state import RoleUnresolved
    two_profiles(ui)
    save_roles(ui, {"fast": "fast-p"})
    snap = ui.state.resolve_role("fast")
    assert snap.model == "small" and snap.profile_name == "fast-p" and snap.profile_modified is False
    with pytest.raises(RoleUnresolved) as undefined:
        ui.state.resolve_role("nope")
    assert str(undefined.value) == "role nope is not defined"
    request(ui, "/api/profiles/delete", {"name": "fast-p", "revision": get_state(ui)["revision"]})
    with pytest.raises(RoleUnresolved) as gone:
        ui.state.resolve_role("fast")
    assert str(gone.value) == "role fast names a profile that does not exist"
