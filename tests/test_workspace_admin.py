"""Filesystem workspace administration: clone, reset and delete without following links."""
import os
from pathlib import Path

import pytest

from agentwiz import profiles, workspace_admin
from agentwiz.workspace import open_workspace


def defaults_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "defaults"
    folder.mkdir()
    profiles.write_profiles(folder, {"format_version": 2, "profiles": {
        "p": {"kind": "openai_compat", "base_url": "http://127.0.0.1:1/v1", "model": "m", "provider": ""}}})
    return folder


def make_workspace(root: Path, name: str, defaults: Path, *, evidence: bool = False) -> Path:
    ws = open_workspace(root, name)
    try:
        path = ws.path
        profiles.seed_workspace(path / "model-profiles", profiles.Library(defaults))
        (path / "task_settings.json").write_text('{"format_version":1,"reasoning":"","temperature":null,"max_tokens":null,"advanced":{}}')
        (path / "roles.json").write_text('{"format_version":1,"roles":{}}')
        (path / "contracts").mkdir()
        (path / "contracts" / "text.json").write_text('{"format_version":1}')
        (path / "blocks" / "one").mkdir(parents=True)
        (path / "blocks" / "one" / "block.json").write_text('{"format_version":1}')
        (path / "workshop" / "examples").mkdir(parents=True)
        (path / "workshop" / "examples" / "one.json").write_text('{"format_version":1}')
        if evidence:
            (path / "runs" / "run").mkdir(parents=True)
            (path / "runs" / "run" / "run.json").write_text('{}')
            (path / "workshop" / "comparisons").mkdir(parents=True)
            (path / "workshop" / "comparisons" / "c.json").write_text('{}')
        return path
    finally:
        ws.close()


def test_list_excludes_links_and_staging(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "alpha").mkdir()
    (root / ".agentwiz-clone-x").mkdir()
    (root / "bad name").mkdir()
    (root / "link").symlink_to(root / "alpha", target_is_directory=True)
    result = workspace_admin.list_workspaces(root, "alpha")
    assert [row["name"] for row in result["workspaces"]] == ["alpha"]
    assert result["current"] == "alpha" and result["truncated"] is False

def test_clone_copies_configuration_and_is_independent(tmp_path):
    root, defaults = tmp_path / "ws", defaults_folder(tmp_path)
    source = make_workspace(root, "source", defaults)
    (source / "blocks" / "one" / "extra.txt").write_text("source")
    result = workspace_admin.clone_workspace(root, "source", "clone")
    target = Path(result["path"])
    assert result["files"] > 0 and target.is_dir()
    assert not (target / ".agentwiz.lock").exists()
    assert not (target / "runs").exists()
    assert not (target / "workshop" / "comparisons").exists()
    assert (target / "blocks" / "one" / "extra.txt").read_text() == "source"
    (target / "blocks" / "one" / "extra.txt").write_text("clone")
    assert (source / "blocks" / "one" / "extra.txt").read_text() == "source"


def test_clone_can_include_evidence(tmp_path):
    root, defaults = tmp_path / "ws", defaults_folder(tmp_path)
    make_workspace(root, "source", defaults, evidence=True)
    result = workspace_admin.clone_workspace(root, "source", "clone", include_evidence=True)
    target = Path(result["path"])
    assert (target / "runs" / "run" / "run.json").is_file()
    assert (target / "workshop" / "comparisons" / "c.json").is_file()
    assert result["evidence"] is True


def test_clone_refuses_symlink_and_cleans_staging(tmp_path):
    root, defaults = tmp_path / "ws", defaults_folder(tmp_path)
    source = make_workspace(root, "source", defaults)
    (source / "blocks" / "one" / "link").symlink_to(source / "blocks" / "one" / "block.json")
    with pytest.raises(workspace_admin.WorkspaceAdminError) as refused:
        workspace_admin.clone_workspace(root, "source", "clone")
    assert refused.value.code == "unsafe_entry"
    assert not (root / "clone").exists()
    assert not [p for p in root.iterdir() if p.name.startswith(".agentwiz-clone-")]


def test_clone_limits_are_bounded(tmp_path, monkeypatch):
    root, defaults = tmp_path / "ws", defaults_folder(tmp_path)
    make_workspace(root, "source", defaults)
    monkeypatch.setattr(workspace_admin, "CLONE_FILE_LIMIT", 1)
    with pytest.raises(workspace_admin.WorkspaceAdminError) as refused:
        workspace_admin.clone_workspace(root, "source", "clone")
    assert refused.value.code == "workspace_too_large"
    assert not (root / "clone").exists()


def test_reset_keeps_lock_and_makes_a_fresh_workspace(tmp_path):
    root, defaults = tmp_path / "ws", defaults_folder(tmp_path)
    path = make_workspace(root, "one", defaults, evidence=True)
    ws = open_workspace(root, "one")
    try:
        before = os.stat(path / ".agentwiz.lock").st_ino
        result = workspace_admin.reset_contents(path, defaults)
        assert result["reset"] is True
        assert os.stat(path / ".agentwiz.lock").st_ino == before
        assert not (path / "blocks").exists() and not (path / "runs").exists()
        assert not (path / "workshop").exists()
        assert (path / "model-profiles" / "profiles.json").is_file()
        assert '"save_runs": false' in (path / "workspace.json").read_text()
    finally:
        ws.close()


def test_reset_refuses_shared_data_inside_workspace(tmp_path):
    root, defaults = tmp_path / "ws", defaults_folder(tmp_path)
    path = make_workspace(root, "one", defaults)
    with pytest.raises(workspace_admin.WorkspaceAdminError) as refused:
        workspace_admin.reset_contents(path, path / "model-profiles", protected=[path / "model-profiles"])
    assert refused.value.code == "shared_data_inside_workspace"


def test_delete_refuses_current_and_removes_a_non_current_workspace(tmp_path):
    root, defaults = tmp_path / "ws", defaults_folder(tmp_path)
    make_workspace(root, "one", defaults)
    other = make_workspace(root, "two", defaults)
    with pytest.raises(workspace_admin.WorkspaceAdminError) as refused:
        workspace_admin.delete_workspace(root, "two", current="two")
    assert refused.value.code == "workspace_is_current"
    result = workspace_admin.delete_workspace(root, "two", current="one")
    assert result["cleanup"] == "complete" and not other.exists()


def test_delete_refuses_an_in_use_workspace_and_does_not_follow_links(tmp_path):
    root, defaults = tmp_path / "ws", defaults_folder(tmp_path)
    one = make_workspace(root, "one", defaults)
    two = make_workspace(root, "two", defaults)
    outside = tmp_path / "outside"
    outside.write_text("keep")
    (two / "link").symlink_to(outside)
    held = open_workspace(root, "two")
    try:
        with pytest.raises(workspace_admin.WorkspaceAdminError) as refused:
            workspace_admin.delete_workspace(root, "two", current="one")
        assert refused.value.code == "workspace_in_use" and refused.value.holder["pid"] == os.getpid()
        assert two.exists()
    finally:
        held.close()
    workspace_admin.delete_workspace(root, "two", current="one")
    assert not two.exists() and outside.read_text() == "keep"
    assert one.exists()


def test_launch_command_is_shell_quoted(tmp_path):
    root = tmp_path / "work spaces"
    root.mkdir()
    command = workspace_admin.launch_command(root, "one", tmp_path / "defaults", tmp_path / "mcp")
    assert "work spaces" in command
    assert "--workspace one" in command

