"""Install and operate AgentWiz's pinned, separate webSpell MCP service.

The service is managed with webSpell's own Docker Compose release stack. It is
never embedded in the AgentWiz process and never shares a workspace lock.
The shared MCP servers.json is the single source of truth for its loopback URL.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from . import webspell
from .mcp_library import Library, ServerConfig, build_raw

DEFAULT_URL = "http://127.0.0.1:8765/mcp"
ARCHIVE_URL = (
    "https://github.com/alexchwong/webSpell/releases/download/"
    f"v{webspell.VERSION}/webSpell-{webspell.VERSION}.zip"
)
DEFAULT_HOME = Path.home() / ".local" / "share" / "agentwiz" / "webspell" / ("v" + webspell.VERSION.replace(".", "_"))


class ServiceError(RuntimeError):
    """An actionable deployment or service configuration error."""


def _url_port(url: str) -> int:
    """Only allow an unauthenticated MCP service on the IPv4 loopback interface."""
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                or parsed.path != "/mcp" or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or parsed.netloc != f"127.0.0.1:{parsed.port}"):
            raise ValueError
        port = parsed.port
        if port is None or not 1024 <= port <= 65535:
            raise ValueError
        return port
    except (ValueError, TypeError) as exc:
        raise ServiceError(
            "webSpell's managed MCP URL must be http://127.0.0.1:PORT/mcp "
            "(PORT between 1024 and 65535); public binding is not supported"
        ) from exc


def ensure_server_entry(folder: Path) -> str:
    """Create webspell once without clobbering unrelated or existing MCP servers.

    This is safe to call on every AgentWiz launch. An existing URL is never
    overwritten and a damaged servers.json is never silently replaced.
    """
    library = Library(Path(folder))
    with library.locked():
        saved = library.read()
        if saved.problem:
            raise ServiceError(f"webSpell MCP configuration unavailable: {saved.problem.message}")
        current = saved.servers.get(webspell.SERVER)
        if current is not None:
            _url_port(current.url)
            if current.token_env is not None:
                raise ServiceError("managed webSpell must not have a token_env setting")
            return current.url
        entries = dict(saved.servers)
        entries[webspell.SERVER] = ServerConfig(url=DEFAULT_URL)
        library.write(build_raw(saved.raw, entries))
        return DEFAULT_URL


def configured_url(folder: Path) -> str:
    # The auto-registration happens on both startup and lifecycle commands.
    return ensure_server_entry(folder)


def _home() -> Path:
    raw = os.environ.get("AGENTWIZ_WEBSPELL_HOME", "").strip()
    return Path(raw).expanduser().resolve() if raw else DEFAULT_HOME


def _run(args: list[str], *, cwd: Path) -> None:
    try:
        subprocess.run(args, cwd=cwd, check=True,
                       env={**os.environ, "COMPOSE_PROJECT_NAME": "agentwiz-webspell"})
    except FileNotFoundError as exc:
        raise ServiceError(f"required command not found: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise ServiceError(f"command failed (exit {exc.returncode}): {' '.join(args)}") from exc


def _check_prerequisites() -> None:
    if shutil.which("docker") is None:
        raise ServiceError("Docker is required; install Docker and start its daemon")
    if shutil.which("openssl") is None:
        raise ServiceError("OpenSSL is required by webSpell setup.sh")
    try:
        subprocess.run(["docker", "compose", "version"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ServiceError("Docker Compose is required and must be running") from exc


def _download_verified(target: Path) -> None:
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(ARCHIVE_URL, timeout=60) as reply, target.open("wb") as out:
            while True:
                data = reply.read(1024 * 1024)
                if not data:
                    break
                digest.update(data)
                out.write(data)
    except (OSError, TimeoutError) as exc:
        raise ServiceError(f"cannot download pinned webSpell {webspell.VERSION} release: {exc}") from exc
    if digest.hexdigest() != webspell.ZIP_SHA256:
        raise ServiceError("downloaded webSpell release does not match pinned SHA-256")


def _unpack_verified(archive: Path, staging: Path) -> Path:
    """Reject traversal and symlinks before extracting the release archive."""
    try:
        with zipfile.ZipFile(archive) as z:
            files = z.infolist()
            if len(files) > 600 or sum(x.file_size for x in files) > 100 * 1024 * 1024:
                raise ServiceError("webSpell release archive exceeds safe extraction limits")
            for info in files:
                p = PurePosixPath(info.filename)
                if (p.is_absolute() or not p.parts or ".." in p.parts
                        or "\\" in info.filename or (info.external_attr >> 16) & 0o170000 == 0o120000):
                    raise ServiceError("unsafe webSpell release archive entry")
            z.extractall(staging)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ServiceError(f"invalid webSpell release archive: {exc}") from exc
    candidates = [staging] + [p for p in staging.iterdir() if p.is_dir()]
    matches = [p for p in candidates if (p / "setup.sh").is_file()
               and (p / "docker-compose.yml").is_file() and (p / ".env.example").is_file()]
    if len(matches) != 1:
        raise ServiceError("webSpell release archive has an unexpected layout")
    return matches[0]


def _update_env(home: Path, port: int) -> None:
    path = home / ".env"
    if path.exists():
        if not path.is_file() or path.is_symlink():
            raise ServiceError("existing webSpell .env is not a regular file")
        contents = path.read_text(encoding="utf-8")
    else:
        template = (home / ".env.example").read_text(encoding="utf-8")
        marker = "replace-with-a-random-secret"
        if marker not in template:
            raise ServiceError("webSpell secret placeholder missing from release template")
        contents = template.replace(marker, secrets.token_hex(32))
    replacement = f"MCP_HOST_PORT={port}"
    if re.search(r"(?m)^MCP_HOST_PORT=", contents):
        contents = re.sub(r"(?m)^MCP_HOST_PORT=.*$", replacement, contents)
    else:
        contents = contents.rstrip("\n") + "\n" + replacement + "\n"
    # This managed deployment is always loopback-only even when settings were edited.
    if re.search(r"(?m)^MCP_BIND_ADDRESS=", contents):
        contents = re.sub(r"(?m)^MCP_BIND_ADDRESS=.*$", "MCP_BIND_ADDRESS=127.0.0.1", contents)
    else:
        contents += "\nMCP_BIND_ADDRESS=127.0.0.1\n"
    # The managed server's observed MCP identity is pinned for AgentWiz's
    # existing schema-based allowlist. Do not accept a legacy renamed server.
    if re.search(r"(?m)^MCP_SERVER_NAME=", contents):
        contents = re.sub(r"(?m)^MCP_SERVER_NAME=.*$", "MCP_SERVER_NAME=webspell", contents)
    else:
        contents += "MCP_SERVER_NAME=webspell\n"
    if path.exists() and path.read_text(encoding="utf-8") == contents:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".webspell-env-", text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(contents)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _ensure_installation(home: Path) -> None:
    if not home.is_dir() or not (home / "setup.sh").is_file() or not (home / "docker-compose.yml").is_file():
        raise ServiceError("webSpell is not installed; run agentwiz-webspell install first")


def _verify_mcp(url: str) -> None:
    from .mcp_client import MCPClient, OpContext, Snapshot
    last = None
    # Compose reports containers started before the HTTP service is listening.
    for attempt in range(12):
        try:
            snap = Snapshot(webspell.SERVER, url, discovery_deadline_s=3)
            client = MCPClient()
            client.discover(OpContext(time.monotonic() + 4), snap)
            if not webspell.compatible(client, snap):
                raise ServiceError("webSpell endpoint has an unexpected identity or tool schemas")
            return
        except ServiceError:
            raise  # Wrong identity is not a startup delay.
        except Exception as exc:
            last = exc
            if attempt != 11:
                time.sleep(1)
    raise ServiceError(
        f"webSpell MCP unavailable at {url}; inspect 'agentwiz-webspell logs' "
        f"({type(last).__name__})"
    )


def execute(command: str, folder: Path, *, home: Path | None = None) -> None:
    """Manage a previously installed service without linking its lifetime to AgentWiz."""
    target = home or _home()
    url = configured_url(folder)
    port = _url_port(url)
    _check_prerequisites()
    if command == "install" and not target.is_dir():
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="webspell-", dir=target.parent) as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "release.zip"
            _download_verified(archive)
            extracted = _unpack_verified(archive, tmp_path / "unpack")
            # The permanent destination is published only after extraction checks.
            os.rename(extracted, target)
    _ensure_installation(target)
    if command in ("install", "start", "restart"):
        _update_env(target, port)
        if command == "install":
            _run(["bash", "setup.sh"], cwd=target)
        elif command == "start":
            _run(["docker", "compose", "up", "-d"], cwd=target)
        else:
            # Plain `docker compose restart` does not apply changed port mappings.
            _run(["docker", "compose", "up", "-d", "--force-recreate"], cwd=target)
        _verify_mcp(url)
        print(f"webSpell ready at {url}")
    elif command == "stop":
        _run(["docker", "compose", "stop"], cwd=target)
    elif command == "status":
        _run(["docker", "compose", "ps"], cwd=target)
        print(f"Configured MCP URL: {url} (use 'start' if containers are stopped)")
    elif command == "logs":
        _run(["docker", "compose", "logs", "--tail=80"], cwd=target)
    else:
        raise ServiceError(f"unknown service command: {command}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage the webSpell MCP service installed with AgentWiz")
    parser.add_argument("command", choices=("install", "start", "stop", "restart", "status", "logs"))
    parser.add_argument("--mcp-servers-dir", type=Path,
                        default=Path(os.environ.get("AGENTWIZ_MCP_SERVERS_DIR", "./mcp_servers")),
                        help="AgentWiz shared MCP settings directory (default: ./mcp_servers)")
    args = parser.parse_args(argv)
    try:
        execute(args.command, args.mcp_servers_dir)
        return 0
    except (ServiceError, OSError) as exc:
        print(f"webSpell: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
