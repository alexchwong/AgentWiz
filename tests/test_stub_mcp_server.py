"""The README tour's stub MCP server: techspec 0.1.2 test #54 (D50). Started as a subprocess with --port 0;
its address is read from its first line."""
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from conftest import add_server, load_tools, mcp_call

STUB = Path(__file__).resolve().parent.parent / "scripts" / "stub_mcp_server.py"


@pytest.mark.parametrize("era_flag, era, version", [(None, "modern", "2026-07-28"),
                                                    ("legacy", "legacy", "2025-11-25")])
def test_stub_mcp_server(ui, era_flag, era, version):
    args = [sys.executable, str(STUB), "--port", "0"] + (["--era", era_flag] if era_flag else [])
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        lines = []
        reader = threading.Thread(target=lambda: lines.append(proc.stdout.readline()), daemon=True)
        reader.start()
        reader.join(15)
        assert lines and lines[0].startswith("stub MCP server at http://127.0.0.1:")
        url = lines[0].rsplit(" ", 1)[1].strip()
        add_server(ui, "stub", url, idle_timeout_s=10, call_deadline_s=30, discovery_deadline_s=10)
        tools = load_tools(ui, "stub")
        assert (tools["era"], tools["protocol_version"]) == (era, version)
        numbers = next(t for t in tools["tools"] if t["name"] == "numbers")
        defaults = {f["name"]: f["default"] for f in numbers["form"]["fields"]}
        assert defaults == {"id": "9007199254740993", "ratio": "0.10000000000000000001"}
        end = mcp_call(ui, "stub", "numbers", '{"id":9007199254740993,"ratio":0.10000000000000000001}')[-1]
        assert end["outcome"] == "ok"
        assert end["result"]["structured_text"] == '{"id":9007199254740993,"ratio":0.10000000000000000001}'
    finally:
        proc.terminate()
        proc.wait(10)
