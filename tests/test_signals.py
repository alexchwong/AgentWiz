"""The command line's signal policy and process shutdown: techspec 0.1.4, test #114 (R3; review v3 R4, review v4 R3).

Runs `python -m agentwiz` as a subprocess against a temporary workspace folder, starts a Python block through
its HTTP route, and sends signals. A test-only hook installed in the child keeps a run-history write waiting, so a
drain is still in progress when the second signal arrives (0.2.2: host scripts, which used to keep the drain
waiting, are retired). Nothing leaves the temporary folder."""
import json
import signal
import subprocess
import sys
import threading
import time
import urllib.request

import pytest

from conftest import python_block, wait_until

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals and process groups")

# Installs the hook, then runs the real command line exactly as `python -m agentwiz` would.
BOOT = ("import sys, threading\n"
        "from agentwiz import history\n"
        "if sys.argv[1] == 'hold-history':\n"
        "    history.before_write = lambda: threading.Event().wait(120)\n"
        "sys.argv = ['agentwiz', *sys.argv[2:]]\n"
        "from agentwiz.__main__ import main\n"
        "sys.exit(main())\n")


class Running:
    def __init__(self, tmp_path, hook="none"):
        self.tmp = tmp_path
        session = tmp_path / "ui-session"
        self.proc = subprocess.Popen(
            [sys.executable, "-c", BOOT, hook, "--no-browser", "--session-file", str(session), "--workspaces-dir", str(tmp_path / "workspaces"),
             "--profiles-dir", str(tmp_path / "profiles"), "--mcp-servers-dir", str(tmp_path / "mcp")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(tmp_path))
        first = self.proc.stdout.readline().strip().removeprefix("[AgentWiz] ")
        assert first.startswith("http://127.0.0.1:"), first
        workshop = json.loads(session.read_text())["workshop"]
        self.base, self.token = workshop["url"], workshop["session"]
        self.block_dir = tmp_path / "workspaces" / "default" / "blocks" / "py"

    def post(self, path, body, timeout=60):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-Session-Token": self.token})
        return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout)

    def get(self, path):
        req = urllib.request.Request(self.base + path, headers={"X-Session-Token": self.token})
        return json.loads(urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10).read())

    def start_python(self, code):
        (self.block_dir / "scripts").mkdir(parents=True, exist_ok=True)
        (self.block_dir / "block.json").write_text(json.dumps(python_block("py")))
        (self.block_dir / "scripts" / "go.py").write_text(code)
        revision = {r["name"]: r for r in self.get("/api/blocks")["blocks"]}["py"]["revision"]
        self.events = []
        resp = self.post("/api/blocks/run", {"name": "py", "revision": revision, "inputs": {"text": "x"}})

        def read():
            for line in resp:
                if line.strip():
                    self.events.append(json.loads(line))
        threading.Thread(target=read, daemon=True).start()

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(10)


LOOP = "def transform(inputs):\n    n = 0\n    while True:\n        n += 1\n"
QUICK = "def transform(inputs):\n    return {'out': 1}\n"


@pytest.fixture
def running(tmp_path):
    app = Running(tmp_path)
    yield app
    app.kill()


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_the_first_signal_drains_and_exits_zero(running, tmp_path, sig):
    running.start_python(LOOP)
    assert wait_until(lambda: running.get("/api/state")["python"]["running"] == 1, 20)
    running.proc.send_signal(sig)
    assert running.proc.wait(30) == 0
    # shutdown interrupts delivery (10.10, step 2), so the page need not see the end; the stream just closes
    assert wait_until(lambda: any(e.get("type") == "started" for e in running.events), 5)


@pytest.mark.parametrize("first, second, code", [(signal.SIGINT, signal.SIGINT, 130),
                                                 (signal.SIGTERM, signal.SIGTERM, 143),
                                                 (signal.SIGTERM, signal.SIGINT, 130),
                                                 (signal.SIGINT, signal.SIGTERM, 143)])
def test_a_second_signal_exits_at_once_and_writes_nothing(tmp_path, first, second, code):
    app = Running(tmp_path, hook="hold-history")
    try:
        state = app.get("/api/state")
        app.post("/api/history/switch", {"save_runs": True, "revision": state["history"]["revision"]}).read()
        app.start_python(QUICK)
        # the run's result is committed and delivered; its history write is held, so the drain waits
        assert wait_until(lambda: any(e.get("type") == "final" for e in app.events), 20)
        app.proc.send_signal(first)
        time.sleep(0.3)
        assert app.proc.poll() is None                             # still draining
        app.proc.send_signal(second)
        assert app.proc.wait(10) == code
        rest = app.proc.stderr.read()
        assert "Traceback" not in rest and "shutting" not in rest.lower()
    finally:
        app.kill()


def test_a_signal_while_idle_exits_zero(running):
    running.proc.send_signal(signal.SIGINT)
    assert running.proc.wait(20) == 0
