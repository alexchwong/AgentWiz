"""Loopback browser authorities over a single workspace owner."""
from __future__ import annotations

import hmac
import json
import os
import secrets
import stat
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer

HERE = Path(__file__).parent
CHAT_GET = frozenset({"/api/agent/conversation", "/api/chat/settings", "/api/chat/history"})
CHAT_POST = frozenset({
    "/api/openrouter-key", "/api/chat/bindings", "/api/chat/tools", "/api/chat/webspell", "/api/chat/models",
    "/api/chat/history/save", "/api/chat/history/open", "/api/chat/history/delete",
    "/api/agent/conversation", "/api/agent/conversation/close", "/api/agent/documents",
    "/api/agent/documents/update", "/api/agent/inventory", "/api/agent/run", "/api/agent/stop", "/api/agent/approve",
})
WORKSHOP = {
    "GET": ("/api/workshop/", "/api/workspaces", "/api/state", "/api/tutorial",
            "/api/contracts", "/api/blocks", "/api/attempts/", "/api/block_runs/",
            "/api/library", "/api/runs", "/api/agent/capabilities"),
    "POST": ("/api/workshop/", "/api/workspaces/", "/api/library/", "/api/tutorial/",
             "/api/config", "/api/openrouter-key", "/api/models", "/api/providers", "/api/complete", "/api/complete/stop",
             "/api/history/switch", "/api/runs/delete", "/api/profiles/", "/api/task_settings/save",
             "/api/roles/save", "/api/blocks/run", "/api/blocks/stop", "/api/contracts/", "/api/mcp/",
             "/api/attachments/extract", "/api/agent/capabilities"),
    "PUT": ("/api/workshop/",),
    "DELETE": ("/api/workshop/comparisons/",),
}
WORKSHOP_COLLECTIONS = ("/api/contracts", "/api/blocks", "/api/runs", "/api/workspaces")


def permitted(surface, method, path):
    if surface == "chat":
        return ((method == "GET" and (path in CHAT_GET or
                 path.startswith(("/api/agent/runs/", "/api/agent/drafts/"))))
                or (method == "POST" and path in CHAT_POST))
    return any(path == route or (route.endswith("/") and path.startswith(route)) or
               (method == "GET" and route in WORKSHOP_COLLECTIONS and path.startswith(route + "/"))
               for route in WORKSHOP.get(method, ()))


class Authority:
    def __init__(self, surface):
        self.surface = surface
        self.lock = threading.Lock()
        self.rotate()

    def rotate(self):
        with self.lock:
            self.session = secrets.token_urlsafe(32)
            self.bootstraps = {}
            self.generation = secrets.token_hex(12)

    def allows(self, token):
        with self.lock:
            return hmac.compare_digest(token.encode("utf-8"), self.session.encode("ascii"))

    def launch_url(self, origin):
        with self.lock:
            now = time.monotonic()
            self.bootstraps = {key: stop for key, stop in self.bootstraps.items() if stop > now}
            token = secrets.token_urlsafe(32)
            self.bootstraps[token] = now + 120
        return f"{origin}/#bootstrap={token}"

    def exchange(self, key):
        if not isinstance(key, str):
            return None
        with self.lock:
            expiry = self.bootstraps.pop(key, 0)
            return self.session if expiry > time.monotonic() else None

    def entry(self, origin):
        with self.lock:
            return {"url": origin, "session": self.session, "generation": self.generation}


def origin(listener):
    return f"http://127.0.0.1:{listener.server_address[1]}"


class ChatListener(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, owner, handler):
        self.owner = owner
        self.authority = Authority("chat")
        self.index = (HERE / "chat.html").read_bytes()
        self.assets = {f"/assets/{name}": ((HERE / name).read_bytes(), ctype)
                       for name, ctype in (
                           ("chat.js", "text/javascript; charset=utf-8"),
                           ("chat-markdown.js", "text/javascript; charset=utf-8"),
                           ("chat.css", "text/css; charset=utf-8"),
                       )}
        super().__init__(("127.0.0.1", port), handler)

    def server_bind(self):
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def __getattr__(self, name):
        return getattr(self.owner, name)

    @property
    def url(self):
        return origin(self)


class SessionFile:
    def __init__(self, path):
        self.path = Path(path).absolute()
        self.identity = None
        if self.path.exists() or self.path.is_symlink():
            raise FileExistsError("session file already exists")

    def write(self, value):
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".agentwiz-session-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream)
            if self.identity is None:
                os.link(tmp, self.path)  # atomic creation, refuses any existing path
            elif self._owned():
                os.replace(tmp, self.path)
            else:
                raise OSError("session file was replaced")
            info = self.path.lstat()
            self.identity = (info.st_dev, info.st_ino)
        finally:
            Path(tmp).unlink(missing_ok=True)

    def _owned(self):
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return False
        return stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == self.identity

    def close(self):
        if self._owned():
            self.path.unlink(missing_ok=True)


def workshop_page(html):
    """Use the shared Workshop components through the Workshop origin only."""
    text = html.decode("utf-8")
    text = text.replace("<title>AgentWiz", "<title>AgentWorkshop")
    text = text.replace("</head>", "<style>#tab-chat,#panel-chat{display:none!important}</style></head>")
    marker = '<script>\nconst TOKEN = new URLSearchParams(location.search).get("t") || "";'
    replacement = '''<script>
(async () => {
const TOKEN = await window.agentWizSession();
if (!TOKEN) throw new Error("Open AgentWorkshop from the AgentWiz launcher.");'''
    text = text.replace(marker, '<script src="/assets/session.js"></script>\n' + replacement, 1)
    text = text.replace('script.src = "/assets/"+name+".js?t=" + encodeURIComponent(TOKEN);',
                        'script.src = "/assets/"+name+".js";', 1)
    text = text.replace('selectTab(fragFromHash(), { hash: false });',
                        'selectTab(fragFromHash() === "chat" ? "workshop" : fragFromHash(), { hash: false });', 1)
    end = '})();</script>'
    text = text.rsplit("</script>", 1)[0] + end + text.rsplit("</script>", 1)[1]
    return text.encode("utf-8")
