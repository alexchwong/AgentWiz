"""Disposable configuration for owner-run evaluations; no personal defaults discovery."""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEEDS = ROOT / "tests" / "eval_profiles"


class SetupBlocked(Exception):
    """Invalid or unavailable evaluation configuration."""


def timeout_default():
    return float(os.environ.get("AGENTWIZ_LIVE_TIMEOUT", "").strip() or 600)


def materialize(workdir, profile, route=None, profiles_dir=None, config=None):
    from agentwiz import profiles, server_config
    from agentwiz.model_call import settings_from_body

    source = Path(profiles_dir) if profiles_dir is not None else SEEDS
    loaded = profiles.read_profiles(source)
    if loaded.problem or profile not in loaded.profiles:
        raise SetupBlocked(f"evaluation profile {profile!r} unavailable in {source / 'profiles.json'}")
    selected = loaded.profiles[profile]
    if route is not None and selected.kind != route:
        raise SetupBlocked("evaluation profile does not match the asserted route")
    kind = selected.kind
    prefix = "AGENTWIZ_LIVE_" + kind.upper()
    overrides = {field: os.environ.get(prefix + "_" + field, "").strip()
                 for field in ("MODEL", "URL", "KEY", "PROVIDER")}
    if (profiles_dir is not None or config is not None) and any(overrides.values()):
        raise SetupBlocked("external profile/config overrides cannot be combined with live target variables")
    raw = loaded.raw
    if profiles_dir is None and config is None:
        fields = {}
        if overrides["MODEL"]:
            fields["model"] = overrides["MODEL"]
        if overrides["PROVIDER"]:
            fields.update(provider=overrides["PROVIDER"], routing_mode="manual")
        selected = settings_from_body(selected, fields)
        raw = {**raw, "profiles": {**raw["profiles"], profile: profiles.profile_object(selected)}}
    destination = workdir / "profiles"
    destination.mkdir(parents=True, exist_ok=True)
    profiles.write_profiles(destination, raw)
    if config is not None:
        config_path = Path(config).resolve()
        if not config_path.is_file():
            raise SetupBlocked("explicit evaluation server configuration does not exist")
    else:
        config_path = workdir / "config" / "model-servers.ini"
        server_config.initialize(config_path)
    servers = server_config.load(config_path)
    if profiles_dir is None and config is None and (overrides["URL"] or overrides["KEY"]):
        if kind == "openrouter":
            raise SetupBlocked("OpenRouter endpoint/key overrides require explicit server configuration; use OPENROUTER_API_KEY")
        from agentwiz.server_config import ServerSettings
        from modelspell import Endpoint, ModelSpellError
        from agentwiz.model_call import validate_key
        try:
            url = Endpoint(kind, overrides["URL"] or servers[kind].base_url).base_url
            servers[kind] = ServerSettings(url, validate_key(overrides["KEY"]) if overrides["KEY"] else None)
        except (ModelSpellError, ValueError):
            raise SetupBlocked("invalid live server endpoint or credential") from None
    return destination, config_path, servers


@contextmanager
def session(workdir, profile, route=None, profiles_dir=None, config=None):
    from agentwiz.ui.server import UIServer
    from agentwiz.workspace import open_workspace

    folder, config_path, servers = materialize(workdir, profile, route, profiles_dir, config)
    workspace = open_workspace(workdir / "workspaces", "live-eval")
    ui = None
    thread = None
    try:
        ui = UIServer(0, workspace=workspace, profiles_dir=folder,
                      mcp_servers_dir=workdir / "mcp_servers", servers=servers, config_path=config_path)
        thread = threading.Thread(target=ui.serve_forever, daemon=True)
        thread.start()
        yield ui
    finally:
        if ui is not None:
            if thread is not None:
                ui.shutdown()
                thread.join()
            ui.server_close()
            ui.close_workspace()
        workspace.close()


def select_chat(client, profile):
    state = client.json("/api/state")
    state = client.json("/api/profiles/select", {"name": profile, "revision": state["revision"]})
    roles = state["roles"]
    definitions = dict(roles["definitions"])
    definition = definitions.get("fast") or roles["defaults"]["fast"]
    definitions["fast"] = {**definition, "location": state["profiles"]["details"][profile]["location"]}
    client.json("/api/roles/save", {"roles": {**roles["map"], "fast": profile},
                                   "definitions": definitions, "revision": roles["revision"]})
