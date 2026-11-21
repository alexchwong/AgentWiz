"""Offline deployment checks; no Docker daemon, network or live MCP required."""
from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from agentwiz import webspell_service as svc
from agentwiz.mcp_library import Library, ServerConfig, build_raw


def test_first_launch_registers_without_clobbering_other_servers(tmp_path):
    folder = tmp_path / "mcp"
    library = Library(folder)
    with library.locked():
        cfg = {"my_mcp": ServerConfig("http://127.0.0.1:9123/mcp")}
        library.write(build_raw({"format_version": 1, "servers": {}}, cfg))
    url = svc.ensure_server_entry(folder)
    assert url == svc.DEFAULT_URL
    assert svc.ensure_server_entry(folder) == url
    entries = Library(folder).read().servers
    assert entries["my_mcp"].url == "http://127.0.0.1:9123/mcp"
    assert entries["webspell"].url == svc.DEFAULT_URL


def test_configured_custom_loopback_port_persists(tmp_path):
    folder = tmp_path / "mcp"
    library = Library(folder)
    with library.locked():
        library.write(build_raw({"format_version": 1, "servers": {}},
                                {"webspell": ServerConfig("http://127.0.0.1:9015/mcp")}))
    assert svc.ensure_server_entry(folder) == "http://127.0.0.1:9015/mcp"


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:8765/mcp", "http://0.0.0.0:8765/mcp",
    "http://127.0.0.1:80/mcp", "http://127.0.0.1:8765/other",
    "http://127.0.0.1:8765/mcp?x=1", "http://user@127.0.0.1:8765/mcp",
    "http://127.0.0.1:65536/mcp", "http://localhost:8765/mcp",
])
def test_invalid_managed_urls_fail_closed(url):
    with pytest.raises(svc.ServiceError):
        svc._url_port(url)


def test_update_env_preserves_secret_and_other_settings(tmp_path):
    (tmp_path / ".env.example").write_text("SEARXNG_SECRET=replace-with-a-random-secret\nMCP_HOST_PORT=8765\n")
    svc._update_env(tmp_path, 8766)
    env = (tmp_path / ".env").read_text()
    assert "replace-with-a-random-secret" not in env
    assert "MCP_HOST_PORT=8766" in env
    assert "MCP_BIND_ADDRESS=127.0.0.1" in env
    assert (tmp_path / ".env").stat().st_mode & 0o777 == 0o600
    svc._update_env(tmp_path, 9876)
    changed = (tmp_path / ".env").read_text()
    assert env.splitlines()[0] == changed.splitlines()[0]
    assert "MCP_HOST_PORT=9876" in changed


def test_bad_archive_refused(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("../outside", "not allowed")
    with pytest.raises(svc.ServiceError, match="unsafe"):
        svc._unpack_verified(archive, tmp_path / "unpack")
    assert not (tmp_path / "outside").exists()


def test_lifecycle_uses_compose_without_managing_agentwiz(tmp_path, monkeypatch):
    home = tmp_path / "installed"
    home.mkdir()
    for name in ("setup.sh", "docker-compose.yml"):
        (home / name).write_text("test")
    (home / ".env.example").write_text("SEARXNG_SECRET=replace-with-a-random-secret\n")
    calls, probes = [], []
    monkeypatch.setattr(svc, "_check_prerequisites", lambda: None)
    monkeypatch.setattr(svc, "_run", lambda argv, *, cwd: calls.append((argv, cwd)))
    monkeypatch.setattr(svc, "_verify_mcp", lambda url: probes.append(url))
    folder = tmp_path / "mcp"
    for action in ("install", "stop", "start", "restart", "status", "logs"):
        svc.execute(action, folder, home=home)
    assert calls == [
        (["bash", "setup.sh"], home),
        (["docker", "compose", "stop"], home),
        (["docker", "compose", "up", "-d"], home),
        (["docker", "compose", "up", "-d", "--force-recreate"], home),
        (["docker", "compose", "ps"], home),
        (["docker", "compose", "logs", "--tail=80"], home),
    ]
    assert probes == [svc.DEFAULT_URL] * 3
