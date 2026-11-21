"""Opt-in workspace transcripts for AgentWiz Chat. Private turns are never written here."""
from __future__ import annotations

import re
from pathlib import Path

from . import blocks, history, jsonio, model_call, runs
from .workspace import write_atomic_bytes

PREFIX = "chat-history-"
LIMIT = 8 << 20
MAX_CHATS = 50
MAX_TURNS = 20
ID = re.compile(r"conv_[a-f0-9]{8}")


class HistoryError(Exception):
    pass


def model_choice(value):
    """Validate a source and model ID. A choice conveys no connection settings or authority."""
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {"kind", "model"}
            or value["kind"] not in model_call.KINDS or not isinstance(value["model"], str)
            or not 0 < len(value["model"]) <= 4096 or value["model"] != value["model"].strip()
            or not value["model"].isprintable()):
        raise ValueError("invalid model choice")
    if value["kind"] == "openrouter":
        parts = value["model"].split("/")
        if (len(parts) != 2
                or any(part in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.:-]+", part) for part in parts)):
            raise ValueError("invalid OpenRouter model ID")
    return dict(value)


def turn_snapshot(view):
    """Keep displayable turns without staged-file handles, raw tool payloads or route credentials."""
    return {
        "run_id": view["run_id"], "message": view["message"], "answer": view["answer"],
        "partial": view["partial"], "stop_reason": view["stop_reason"],
        "trace": [{"capability": action["capability"], "outcome": action["outcome"]}
                  for action in view["trace"]],
        "sources": view["sources"],
    }


def _turn_valid(turn):
    return (isinstance(turn, dict)
            and set(turn) == {"run_id", "message", "answer", "partial", "stop_reason", "trace", "sources"}
            and runs.valid_run_id(turn["run_id"]) and isinstance(turn["message"], str)
            and all(turn[field] is None or isinstance(turn[field], str)
                    for field in ("answer", "partial", "stop_reason"))
            and isinstance(turn["trace"], list)
            and all(isinstance(action, dict) and set(action) == {"capability", "outcome"}
                    and all(isinstance(value, str) for value in action.values()) for action in turn["trace"])
            and isinstance(turn["sources"], list) and all(isinstance(url, str) for url in turn["sources"]))


class History:
    """The manager serializes writes with conversation changes and executions."""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    @staticmethod
    def _name(ident):
        if not isinstance(ident, str) or not ID.fullmatch(ident):
            raise HistoryError("no_such_chat")
        return f"{PREFIX}{ident}.json"

    def contains(self, ident):
        path = self.workspace / self._name(ident)
        return path.exists() or path.is_symlink()

    def _ids(self):
        return [ident for path in self.workspace.glob(f"{PREFIX}*.json")
                if ID.fullmatch(ident := path.stem.removeprefix(PREFIX))]

    def _read(self, ident):
        result = blocks.read_file(self.workspace, self._name(ident), LIMIT)
        if result.fingerprint == ("missing",):
            return None
        if result.data is not None:
            try:
                row = jsonio.decode_file(result.data)
                if (set(row) == {"format_version", "id", "private", "ready", "updated", "title", "model", "turns"}
                        and type(row["format_version"]) is int and row["format_version"] == 1
                        and row["id"] == ident and row["private"] is False and type(row["ready"]) is bool
                        and isinstance(row["updated"], str) and isinstance(row["title"], str)
                        and isinstance(row["turns"], list) and 0 < len(row["turns"]) <= MAX_TURNS
                        and all(_turn_valid(turn) for turn in row["turns"])
                        and len({turn["run_id"] for turn in row["turns"]}) == len(row["turns"])):
                    model_choice(row["model"])
                    return row
            except (ValueError, RecursionError):
                pass
        raise HistoryError("chat_history_unavailable")

    def listing(self):
        rows = []
        for ident in self._ids()[:MAX_CHATS]:
            try:
                row = self._read(ident)
            except HistoryError:
                rows.append({"id": ident, "title": "Unavailable chat", "ready": False, "updated": ""})
                continue
            if row is not None:
                rows.append({key: row[key] for key in ("id", "title", "ready", "updated")})
        return sorted(rows, key=lambda row: row["updated"], reverse=True)

    def reopen(self, ident):
        row = self._read(ident)
        if row is None:
            raise HistoryError("no_such_chat")
        if not row["ready"]:
            raise HistoryError("chat_history_interrupted")
        return row

    def _write(self, row):
        data = jsonio.encode(row) + b"\n"
        while len(data) > LIMIT and len(row["turns"]) > 1:
            del row["turns"][0]
            data = jsonio.encode(row) + b"\n"
        if len(data) > LIMIT:
            raise HistoryError("chat_history_size")
        try:
            write_atomic_bytes(self.workspace, self._name(row["id"]), data)
        except OSError:
            raise HistoryError("chat_history_unavailable") from None

    def suspend(self, ident):
        row = self._read(ident)
        if row and row["ready"]:
            self._write({**row, "ready": False})

    def save(self, ident, turns, model, credentials):
        if not turns:
            return
        before = self._read(ident)
        if before is None and len(self._ids()) >= MAX_CHATS:
            raise HistoryError("chat_history_full")
        title = " ".join(turns[0]["message"].split())[:72] or "Chat"
        turns = turns[-MAX_TURNS:]
        content, _ = history.redact({"title": before["title"] if before else title, "model": model,
                                     "turns": turns}, credentials)
        try:
            model_choice(content["model"])
        except ValueError:
            content["model"] = None
        # Preserve app-owned identifiers even if a credential happens to match one.
        content["turns"] = [{**safe, "run_id": turn["run_id"]}
                            for safe, turn in zip(content["turns"], turns)]
        self._write({**content, "format_version": 1, "id": ident, "private": False, "ready": True,
                     "updated": runs.utc_now()})

    def delete(self, ident):
        try:
            (self.workspace / self._name(ident)).unlink(missing_ok=True)
        except OSError:
            raise HistoryError("chat_history_unavailable") from None
