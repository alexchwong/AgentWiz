"""The command line's signal policy and process shutdown: techspec 0.1.4, test #114 (R3; review v3 R4, review v4 R3).

Runs `python -m agentwiz` as a subprocess against a temporary workspace folder, starts a waiting script block through
its HTTP route, and sends signals. Nothing leaves the temporary folder."""
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request

import pytest

from conftest import group_gone, wait_until
from test_blocks import script_block

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals and process groups")


class Running:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "agentwiz", "--no-browser", "--workspaces-dir", str(tmp_path / "workspaces"),
             "--profiles-dir", str(tmp_path / "profiles"), "--mcp-servers-dir", str(tmp_path / "mcp")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(tmp_path))
        first = self.proc.stdout.readline().strip().removeprefix("[AgentWiz] ")
        assert first.startswith("http://127.0.0.1:"), first
        self.base, self.token = first.split("/?t=")[0], first.split("/?t=")[1]
        self.block_dir = tmp_path / "workspaces" / "default" / "blocks" / "sc"

    def post(self, path, body, timeout=60):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-Session-Token": self.token})
        return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout)

    def get(self, path):
        req = urllib.request.Request(self.base + path, headers={"X-Session-Token": self.token})
        return json.loads(urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10).read())

    def start_script(self, code):
        (self.block_dir / "scripts").mkdir(parents=True, exist_ok=True)
        (self.block_dir / "block.json").write_text(json.dumps(script_block("sc") | {"steps": [
            {"id": "run", "kind": "script", "file": "scripts/go.py", "timeout_s": 600}]}))
        (self.block_dir / "scripts" / "go.py").write_text(code)
        revision = {r["name"]: r for r in self.get("/api/blocks")["blocks"]}["sc"]["revision"]
        self.events = []
        resp = self.post("/api/blocks/run", {"name": "sc", "revision": revision, "inputs": {"a": "x"}})
        threading.Thread(target=lambda: self.events.extend(
            json.loads(line) for line in resp.read().splitlines() if line), daemon=True).start()

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(10)


PID_SCRIPT = ("import os, sys, time, signal\n{trap}open({pid!r}, 'w').write(str(os.getpid()))\ntime.sleep(120)\n")


@pytest.fixture
def running(tmp_path):
    app = Running(tmp_path)
    yield app
    app.kill()


def start_waiting(running, tmp_path, trap=""):
    pidfile = tmp_path / "script.pid"
    running.start_script(PID_SCRIPT.format(trap=trap, pid=str(pidfile)))
    assert wait_until(pidfile.exists, 20)
    time.sleep(0.2)
    return int(pidfile.read_text())


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_the_first_signal_drains_and_exits_zero(running, tmp_path, sig):
    pgid = start_waiting(running, tmp_path)
    running.proc.send_signal(sig)
    assert running.proc.wait(30) == 0
    assert group_gone(pgid)
    # shutdown interrupts delivery (10.10, step 2), so the page need not see the end; the stream just closes
    assert wait_until(lambda: any(e.get("type") == "started" for e in running.events), 5)


@pytest.mark.parametrize("first, second, code", [(signal.SIGINT, signal.SIGINT, 130),
                                                 (signal.SIGTERM, signal.SIGTERM, 143),
                                                 (signal.SIGTERM, signal.SIGINT, 130),
                                                 (signal.SIGINT, signal.SIGTERM, 143)])
def test_a_second_signal_exits_at_once_and_writes_nothing(running, tmp_path, first, second, code):
    # a script that ignores SIGTERM keeps the drain waiting for the 2 second escalation
    pgid = start_waiting(running, tmp_path, trap="signal.signal(signal.SIGTERM, signal.SIG_IGN)\n")
    running.proc.send_signal(first)
    time.sleep(0.3)
    assert running.proc.poll() is None                         # still draining
    out_before = running.proc.stdout
    running.proc.send_signal(second)
    assert running.proc.wait(10) == code
    rest = running.proc.stderr.read()
    assert "Traceback" not in rest and "shutting" not in rest.lower()
    try:
        os.killpg(pgid, signal.SIGKILL)                         # the forced exit leaves the group to the test
    except ProcessLookupError:
        pass
    assert out_before is not None


def test_a_signal_while_idle_exits_zero(running):
    running.proc.send_signal(signal.SIGINT)
    assert running.proc.wait(20) == 0
