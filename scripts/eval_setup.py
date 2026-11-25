"""Disposable configuration for owner-run evaluations; no personal defaults discovery."""
from __future__ import annotations

import configparser
import os
import threading
from argparse import ArgumentTypeError
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEEDS = ROOT / "tests" / "eval_profiles"


class SetupBlocked(Exception):
    """Invalid or unavailable evaluation configuration."""


def timeout_default():
    return float(os.environ.get("AGENTWIZ_LIVE_TIMEOUT", "").strip() or 600)


def nonempty(value):
    value = value.strip()
    if not value:
        raise ArgumentTypeError("value must be non-empty")
    return value


def add_target_options(parser):
    parser.add_argument("--model", type=nonempty, help="exact model ID for the selected backend")
    parser.add_argument("--provider", help="pin an OpenRouter provider")
    parser.add_argument("--base-url", type=nonempty, help="API base URL for a local backend")


def target_options(args):
    return {name: getattr(args, name) for name in ("model", "provider", "base_url")
            if getattr(args, name) is not None}


def materialize(workdir, profile, route=None, profiles_dir=None, config=None, *,
                model=None, provider=None, base_url=None, cloud_summary=None, routing_data_collection=None):
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
    if (profiles_dir is not None or config is not None) and (
            any(overrides.values()) or any(value is not None for value in (model, provider, base_url, cloud_summary))):
        raise SetupBlocked("external profile/config overrides cannot be combined with live target options or variables")
    if provider is not None and kind != "openrouter":
        raise SetupBlocked("--provider requires OpenRouter")
    if base_url is not None and kind == "openrouter":
        raise SetupBlocked("OpenRouter endpoint overrides require explicit server configuration")
    for field, value in (("MODEL", model), ("PROVIDER", provider)):
        if value is not None:
            overrides[field] = value
    raw = loaded.raw
    if profiles_dir is None and config is None:
        fields = {}
        if routing_data_collection is not None:
            fields["routing_data_collection"] = routing_data_collection
        if overrides["MODEL"]:
            fields["model"] = overrides["MODEL"]
        if overrides["PROVIDER"]:
            fields.update(provider=overrides["PROVIDER"], routing_mode="manual")
        selected = settings_from_body(selected, fields)
        raw = {**raw, "profiles": {**raw["profiles"], profile: profiles.profile_object(selected)}}
    if cloud_summary is not None:
        name, cloud_model, cloud_provider = cloud_summary
        seed = loaded.profiles["openrouter-eval"]
        settings = settings_from_body(seed, {"model": cloud_model, "provider": cloud_provider,
                                             "routing_mode": "manual"})
        raw["profiles"][name] = profiles.profile_object(settings)
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
    if base_url is not None:
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(config_path, encoding="utf-8")
        parser[kind]["base_url"] = base_url
        with config_path.open("w", encoding="utf-8") as stream:
            parser.write(stream)
    servers = server_config.load(config_path)
    # The explicit base URL already went through the normal server config validator.
    url_override = overrides["URL"] if base_url is None else ""
    if profiles_dir is None and config is None and (url_override or overrides["KEY"]):
        if kind == "openrouter":
            raise SetupBlocked("OpenRouter endpoint/key overrides require explicit server configuration; use OPENROUTER_API_KEY")
        from agentwiz.server_config import ServerSettings
        from modelspell import Endpoint, ModelSpellError
        from agentwiz.model_call import validate_key
        try:
            url = Endpoint(kind, url_override or servers[kind].base_url).base_url
            servers[kind] = ServerSettings(url, validate_key(overrides["KEY"]) if overrides["KEY"] else None)
        except (ModelSpellError, ValueError):
            raise SetupBlocked("invalid live server endpoint or credential") from None
    return destination, config_path, servers


@contextmanager
def session(workdir, profile, route=None, profiles_dir=None, config=None, *, split=False,
            model=None, provider=None, base_url=None, cloud_summary=None, routing_data_collection=None):
    from agentwiz.ui.server import Handler, UIServer
    from agentwiz.ui.surfaces import ChatListener
    from agentwiz.workspace import open_workspace

    folder, config_path, servers = materialize(
        workdir, profile, route, profiles_dir, config, model=model, provider=provider,
        base_url=base_url, cloud_summary=cloud_summary,
        routing_data_collection=routing_data_collection)
    workspace = open_workspace(workdir / "workspaces", "live-eval")
    ui = None
    thread = chat_thread = None
    try:
        ui = UIServer(0, workspace=workspace, profiles_dir=folder,
                      mcp_servers_dir=workdir / "mcp_servers", servers=servers, config_path=config_path,
                      production=split)
        thread = threading.Thread(target=ui.serve_forever, daemon=True)
        thread.start()
        if split:
            ui.chat_listener = ChatListener(0, ui, Handler)
            chat_thread = threading.Thread(target=ui.chat_listener.serve_forever, daemon=True)
            chat_thread.start()
        yield ui
    finally:
        if ui is not None:
            if chat_thread is not None:
                ui.chat_listener.shutdown()
                chat_thread.join()
                ui.chat_listener.server_close()
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
