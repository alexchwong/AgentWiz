"""Live workspace switching, creation, reset, clone and delete over HTTP."""
from pathlib import Path

import pytest

from agentwiz.workspace import WorkspaceInUse, open_workspace
from conftest import get_state, request


def make_second(ui, name="second"):
    ws = open_workspace(ui.workspace.path.parent, name)
    ws.close()
    return ws.path


def test_switch_changes_state_and_releases_the_old_lock(make_ui):
    ui = make_ui()
    other = make_second(ui)
    listing = request(ui, "/api/workspaces")
    assert listing["current"] == ui.workspace.name
    assert {row["name"] for row in listing["workspaces"]} == {ui.workspace.name, "second"}
    old_name = ui.workspace.name
    result = request(ui, "/api/workspaces/switch", {"format_version": 1, "name": "second"})
    assert result["workspace"]["name"] == "second"
    assert get_state(ui)["workspace"]["name"] == "second"
    assert ui.workspace.path == other
    opened = open_workspace(other.parent, old_name)
    try:
        with pytest.raises(WorkspaceInUse):
            open_workspace(other.parent, "second")
    finally:
        opened.close()


def test_switch_is_idempotent_and_refuses_missing_or_invalid_names(make_ui):
    ui = make_ui()
    name = ui.workspace.name
    result = request(ui, "/api/workspaces/switch", {"format_version": 1, "name": name})
    assert result["workspace"]["name"] == name
    with pytest.raises(Exception) as refused:
        request(ui, "/api/workspaces/switch", {"format_version": 1, "name": "missing"})
    assert refused.value.body["code"] == "no_such_workspace"
    with pytest.raises(Exception) as refused:
        request(ui, "/api/workspaces/switch", {"format_version": 1, "name": "../x"})
    assert refused.value.body["code"] == "invalid_workspace_name"


def test_create_switches_to_a_new_workspace(make_ui):
    ui = make_ui()
    result = request(ui, "/api/workspaces/create", {"format_version": 1, "name": "created"})
    assert result["workspace"]["name"] == "created"
    assert get_state(ui)["workspace"]["created"] is True


def test_clone_then_switch_and_delete(make_ui):
    ui = make_ui()
    original = ui.workspace.name
    (ui.workspace.path / "blocks" / "marker").mkdir(parents=True)
    (ui.workspace.path / "blocks" / "marker" / "block.json").write_text("source")
    cloned = request(ui, "/api/workspaces/clone",
                     {"format_version": 1, "source": original, "target": "clone"})
    assert cloned["name"] == "clone"
    request(ui, "/api/workspaces/switch", {"format_version": 1, "name": "clone"})
    assert (ui.workspace.path / "blocks" / "marker" / "block.json").read_text() == "source"
    with pytest.raises(Exception) as refused:
        request(ui, "/api/workspaces/delete", {"format_version": 1, "name": "clone", "confirm": "clone"})
    assert refused.value.body["code"] == "workspace_is_current"
    request(ui, "/api/workspaces/switch", {"format_version": 1, "name": original})
    request(ui, "/api/workspaces/delete", {"format_version": 1, "name": "clone", "confirm": "clone"})
    assert not (ui.workspace.path.parent / "clone").exists()


def test_reset_current_keeps_the_workspace_and_clears_data(make_ui):
    ui = make_ui()
    (ui.workspace.path / "marker").write_text("old")
    result = request(ui, "/api/workspaces/reset", {"format_version": 1, "name": ui.workspace.name,
                                                    "confirm": ui.workspace.name})
    assert result["reset"] is True
    assert not (ui.workspace.path / "marker").exists()
    assert get_state(ui)["workspace"]["name"] == ui.workspace.name


def test_switch_refuses_an_in_use_target_and_an_active_writer(make_ui):
    ui = make_ui()
    other = open_workspace(ui.workspace.path.parent, "busy")
    try:
        with pytest.raises(Exception) as refused:
            request(ui, "/api/workspaces/switch", {"format_version": 1, "name": "busy"})
        assert refused.value.body["code"] == "workspace_in_use"
        assert get_state(ui)["workspace"]["name"] != "busy"
    finally:
        other.close()
    writer = ui.state.register_writer("test")
    try:
        with pytest.raises(Exception) as refused:
            request(ui, "/api/workspaces/switch", {"format_version": 1, "name": "busy"})
        assert refused.value.body["code"] == "workspace_busy"
        assert any(row["kind"] == "writer" for row in refused.value.body["active"])
    finally:
        writer.release()
        request(ui, "/api/workspaces/switch", {"format_version": 1, "name": "busy"})


def test_reset_requires_confirmation_and_refuses_current_delete(make_ui):
    ui = make_ui()
    with pytest.raises(Exception) as refused:
        request(ui, "/api/workspaces/reset", {"format_version": 1, "name": ui.workspace.name})
    assert refused.value.body["code"] == "confirmation_required"
    with pytest.raises(Exception) as refused:
        request(ui, "/api/workspaces/delete", {"format_version": 1, "name": ui.workspace.name,
                                                "confirm": ui.workspace.name})
    assert refused.value.body["code"] == "workspace_is_current"


def test_switch_failure_leaves_the_current_workspace_and_releases_the_target(ui, monkeypatch):
    other = make_second(ui, "unopenable")

    def fail(workspace):
        raise OSError("fixture")

    monkeypatch.setattr(ui, "_new_state", fail)
    with pytest.raises(Exception) as refused:
        request(ui, "/api/workspaces/switch", {"format_version": 1, "name": "unopenable"})
    assert refused.value.body["code"] == "workspace_unavailable"
    assert get_state(ui)["workspace"]["name"] != "unopenable"
    reopened = open_workspace(other.parent, "unopenable")
    reopened.close()


def test_reset_another_workspace_does_not_change_the_current_one(make_ui):
    ui = make_ui()
    other = make_second(ui, "other")
    (other / "marker").write_text("old")
    (ui.workspace.path / "current-marker").write_text("keep")
    result = request(ui, "/api/workspaces/reset", {"format_version": 1, "name": "other", "confirm": "other"})
    assert result["reset"] is True
    assert not (other / "marker").exists()
    assert (ui.workspace.path / "current-marker").read_text() == "keep"
    assert get_state(ui)["workspace"]["name"] != "other"


def test_reset_refuses_when_shared_defaults_are_inside_the_workspace(tmp_path, make_ui):
    workspace = open_workspace(tmp_path / "ws", "one")
    ui = make_ui(workspace=workspace, profiles_dir=workspace.path / "shared-defaults")
    with pytest.raises(Exception) as refused:
        request(ui, "/api/workspaces/reset", {"format_version": 1, "name": "one", "confirm": "one"})
    assert refused.value.body["code"] == "shared_data_inside_workspace"
    assert get_state(ui)["workspace"]["name"] == "one"


def test_reset_current_recovers_a_read_only_workspace(tmp_path, make_ui):
    root = tmp_path / "ws"
    path = root / "bad"
    path.mkdir(parents=True)
    (path / "workspace.json").write_text("{not json")
    workspace = open_workspace(root, "bad")
    ui = make_ui(workspace=workspace)
    assert get_state(ui)["workspace"]["read_only"] is True
    request(ui, "/api/workspaces/reset", {"format_version": 1, "name": "bad", "confirm": "bad"})
    assert get_state(ui)["workspace"]["read_only"] is False
    assert get_state(ui)["workspace"]["problems"] == []


def test_clone_accepts_a_non_current_source(make_ui):
    ui = make_ui()
    other = make_second(ui, "source")
    (other / "blocks").mkdir(exist_ok=True)
    (other / "blocks" / "marker").mkdir()
    (other / "blocks" / "marker" / "block.json").write_text("copied")
    result = request(ui, "/api/workspaces/clone",
                     {"format_version": 1, "source": "source", "target": "copy"})
    assert result["name"] == "copy"
    assert (Path(result["path"]) / "blocks" / "marker" / "block.json").read_text() == "copied"
    assert get_state(ui)["workspace"]["name"] != "copy"
