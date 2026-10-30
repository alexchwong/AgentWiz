"""Provenance lines and preview sizes, shared by files and tool results (techspec 0.1.2, Section 8.5).

One pipeline for everything inserted into a request: the provenance line, a newline, then the content.
The page appends exactly `text` to the chosen field when the user asks; nothing is inserted or sent
automatically. Standard library only; knows nothing about HTTP.
"""
from __future__ import annotations

import hashlib
import json
import math
import re

REQUEST_LIMIT = 33_554_432        # 32 MiB: the JSON request cap; tests may lower it
ARGUMENTS_SHOWN = 200
_TOOL_NAME = re.compile(r"[A-Za-z0-9_.-]+\Z")


def _json_string(value: str) -> str:
    """A JSON string: quotes, backslashes and control characters escaped, other characters kept."""
    return json.dumps(value, ensure_ascii=False)


def file_provenance(filename: str) -> str:
    return f"[file {_json_string(filename)}]"


def tool_provenance(server: str, tool: str, arguments_text: str, tool_error: bool) -> str:
    name = tool if _TOOL_NAME.match(tool) else _json_string(tool)
    args = arguments_text
    if len(args) > ARGUMENTS_SHOWN:
        args = f"{args[:ARGUMENTS_SHOWN]}… ({len(arguments_text) - ARGUMENTS_SHOWN} more characters)"
    return f"[tool {server}.{name} arguments={args}]" + (" (tool error)" if tool_error else "")


def preview(provenance: str, content: str) -> dict:
    """What is inserted and its sizes."""
    text = provenance + "\n" + content
    chars = len(text)
    request_bytes = len(json.dumps(text, ensure_ascii=False).encode("utf-8", "surrogatepass")) - 2
    return {"provenance": provenance, "text": text,
            "sha256": hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest(),
            "content_chars": len(content), "chars": chars, "estimated_tokens": math.ceil(chars / 4),
            "request_bytes": request_bytes, "too_large_to_send": request_bytes > REQUEST_LIMIT}
