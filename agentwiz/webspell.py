"""AgentWiz's interface to the separately installed webSpell v0.2.0 service."""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

SERVER = "webspell"
VERSION = "0.2.0"
ZIP_SHA256 = "1ba52d2dc9f861fdd7e4b2bf4f4c16e6bad2135f1e11863698a4312281db2acf"
WHEEL_SHA256 = "b818da1a32b9c2c55f74f07c831d06100227dc44835566a90a789b85bd2e30cd"


def _nullable(schema):
    return {"anyOf": [schema, {"type": "null"}], "default": None}


def _schema(required, **properties):
    return {"type": "object", "additionalProperties": False, "properties": properties, "required": [required]}


_QUERY = {"type": "string", "maxLength": 1000}
_URL = {"type": "string", "maxLength": 4096}
_SEARCH = {
    "query": _QUERY,
    "recency": _nullable({"type": "string"}),
    "domains": _nullable({"items": {"type": "string"}, "type": "array", "minItems": 1, "maxItems": 5}),
}
SCHEMAS = {
    "web_search_results": _schema("query", **_SEARCH),
    "web_evidence_passages": _schema("query", **_SEARCH, max_passages=_nullable({"type": "integer"}),
                                    urls=_nullable({"items": _URL, "type": "array"})),
    "web_page_text": _schema("url", url=_URL, start={"type": "integer", "minimum": 0, "default": 0},
                            max_chars=_nullable({"type": "integer", "minimum": 1})),
}


def compatible(client, snapshot):
    if client.observed_server_name(snapshot.identity) != SERVER:
        return False
    if set(client.observed_tools(snapshot.identity)) != SCHEMAS.keys():
        return False
    for name, schema in SCHEMAS.items():
        observed = client.observed_schema(snapshot.identity, name)
        if observed is None or observed[1] != schema:
            return False
    return True


def public_url(value):
    if not isinstance(value, str) or not 0 < len(value) <= 4096 or re.search(r"[\s\\]", value):
        return False
    try:
        url = urlsplit(value)
        hostname = url.hostname
        if (url.scheme not in ("http", "https") or not hostname or url.username is not None
                or url.port not in (None, 80 if url.scheme == "http" else 443)):
            return False
        try:
            return ipaddress.ip_address(hostname).is_global
        except ValueError:
            return "." in hostname and not hostname.rstrip(".").endswith((".localhost", ".local", ".internal"))
    except ValueError:
        return False


def prompt_urls(text):
    return {url for url in re.findall(r"https?://[^\s<>\x22\x27]+", text)[:64] if public_url(url)}


def result_urls(value):
    """Only explicitly structured source records may authorise a subsequent exact read."""
    if not isinstance(value, dict):
        return set()
    urls = set()
    for field in ("hits", "sources", "results", "passages"):
        rows = value.get(field)
        if isinstance(rows, list):
            for row in rows[:64]:
                if isinstance(row, dict):
                    urls.update(url for url in (row.get("url"), row.get("final_url")) if public_url(url))
    return urls
