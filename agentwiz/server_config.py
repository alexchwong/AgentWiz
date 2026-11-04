"""Read fixed model-server settings without exporting them to the environment."""
from __future__ import annotations

import configparser
import importlib.resources
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from modelspell import Endpoint, ModelSpellError

from .model_call import SettingsError, validate_key


class ServerConfigError(ValueError):
    """A configuration problem whose message never includes file contents."""


@dataclass(frozen=True)
class ServerSettings:
    base_url: str
    api_key: str | None = field(default=None, repr=False)


def load(path: Path) -> dict[str, ServerSettings]:
    """Read the three server sections, rejecting unknown fields and unsafe URLs."""
    path = Path(path).absolute()
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8") as source:
            parser.read_file(source)
        kinds = {"lmstudio", "openrouter", "openai_compat"}
        if set(parser.sections()) != kinds or parser.defaults():
            raise ServerConfigError("expected lmstudio, openrouter and openai_compat sections")
        result = {}
        for kind in sorted(kinds):
            section = parser[kind]
            allowed = {"base_url"} if kind == "openrouter" else {"base_url", "api_key"}
            if set(section) - allowed or "base_url" not in section:
                raise ServerConfigError(f"invalid fields in [{kind}]")
            url = section["base_url"].strip()
            parts = urlsplit(url)
            if (not url.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in url) or
                    any(c in url for c in "@?#\\") or
                    not parts.hostname or parts.scheme not in ("http", "https")):
                raise ServerConfigError(f"invalid base_url in [{kind}]")
            try:
                endpoint = Endpoint(kind, url)
                # Force port validation even though modelSpell only validates the scheme.
                _ = parts.port
                key = section.get("api_key", "").strip()
                if key:
                    key = validate_key(key)
            except (ModelSpellError, SettingsError, ValueError):
                raise ServerConfigError(f"invalid server settings in [{kind}]") from None
            result[kind] = ServerSettings(endpoint.base_url, key or None)
        return result
    except (OSError, UnicodeError, configparser.Error, ServerConfigError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, ServerConfigError) else "cannot read or parse configuration"
        raise ServerConfigError(f"{path}: {reason}") from None


def template_bytes() -> bytes:
    """Read the packaged template, or the source template in an editable checkout."""
    resource = importlib.resources.files("agentwiz").joinpath("model-servers.ini.template")
    if resource.is_file():
        return resource.read_bytes()
    return (Path(__file__).resolve().parent.parent / "config" / "model-servers.ini.template").read_bytes()


def initialize(path: Path) -> None:
    """Publish a complete private file atomically, without replacing an existing file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        return
    temporary = None
    try:
        data = template_bytes()
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".model-servers-", delete=False) as target:
            temporary = Path(target.name)
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)