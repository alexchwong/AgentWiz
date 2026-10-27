"""The workspace lock and the command line: techspec 13.3 tests #19 and #24."""
import json
import os
import signal
import subprocess
import sys
import threading
import urllib.request

import pytest

from agentwiz.workspace import WorkspaceError, WorkspaceInUse, open_workspace
from conftest import file_stat


# 19 (lock, and invalid names below) ----------------------------------------------------------
def test_workspace_lock(tmp_path):
    root = tmp_path / "ws"
    a = open_workspace(root, "default")
    child = None
    try:
        before = file_stat(a.path / "workspace.json")
        with pytest.raises(WorkspaceInUse) as refused:
            open_workspace(root, "default")
        assert refused.value.holder["pid"] == os.getpid()
        assert file_stat(a.path / "workspace.json") == before

        os.truncate(a.path / ".agentwiz.lock", 0)  # details missing: the lock still refuses
        with pytest.raises(WorkspaceInUse) as refused:
            open_workspace(root, "default")
        assert refused.value.holder == {}

        # A child started while the lock is held does not keep it.
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], close_fds=False)
        a.close()
        assert child.poll() is None
        b = open_workspace(root, "default")
        b.close()
        assert (a.path / ".agentwiz.lock").exists()
    finally:
        a.close()
        if child is not None:
            child.kill()
            child.wait()


@pytest.mark.parametrize("name", ["", ".x", "../x", "a/b", "a" * 65, "é"])
def test_workspace_invalid_names(tmp_path, name):
    with pytest.raises(WorkspaceError):
        open_workspace(tmp_path / "ws", name)
    assert list(tmp_path.iterdir()) == []


# 24 --------------------------------------------------------------------------------------------
class Launch:
    """`python -m agentwiz --no-browser …` in a scratch directory; the first stdout line is read."""

    def __init__(self, cwd, *args):
        self.proc = subprocess.Popen([sys.executable, "-m", "agentwiz", "--no-browser", *args], cwd=cwd,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def address(self, timeout=15):
        lines = []
        reader = threading.Thread(target=lambda: lines.append(self.proc.stdout.readline()), daemon=True)
        reader.start()
        reader.join(timeout)
        assert lines and lines[0].startswith("[AgentWiz] http://127.0.0.1:"), f"no address: {lines}"
        return lines[0].split(" ", 1)[1].strip()

    def state(self, address):
        root, token = address.split("/?t=", 1)
        req = urllib.request.Request(root + "/api/state", headers={"X-Session-Token": token})
        return json.loads(urllib.request.build_opener().open(req, timeout=10).read())

    def exit_status(self, timeout=15):
        return self.proc.wait(timeout)

    def stop(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()
        for stream in (self.proc.stdout, self.proc.stderr):
            stream.close()


def test_cli_workspace(tmp_path):
    wsdir = str(tmp_path / "workspaces")
    launches = []

    def launch(*args):
        launches.append(Launch(tmp_path, "--workspaces-dir", wsdir, *args))
        return launches[-1]

    try:
        first = launch()
        first_address = first.address()
        assert first.state(first_address)["workspace"]["name"] == "default"
        assert (tmp_path / "model_profiles").is_dir()
        assert (tmp_path / "workspaces" / "default" / "model-profiles" / "profiles.json").is_file()

        second = launch()
        assert second.exit_status() == 3
        assert first.state(first_address)["workspace"]["name"] == "default"

        demo = launch("--workspace", "demo", "--profiles-dir", str(tmp_path / "custom-defaults"))
        state = demo.state(demo.address())
        assert state["workspace"]["name"] == "demo"
        assert state["profiles"]["defaults_path"] == str(tmp_path / "custom-defaults" / "profiles.json")

        bad = launch("--workspace", "../x")
        assert bad.exit_status() == 2
        assert not (tmp_path / "x").exists()

        first.proc.send_signal(signal.SIGKILL)
        first.proc.wait(10)
        third = launch()
        assert third.state(third.address())["workspace"]["created"] is False
    finally:
        for item in launches:
            item.stop()
    # Nothing else was created beside the two workspaces.
    assert sorted(p.name for p in (tmp_path / "workspaces").iterdir()) == ["default", "demo"]
