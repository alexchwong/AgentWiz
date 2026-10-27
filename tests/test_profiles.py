"""Profiles in a workspace through HTTP: techspec 13.3 tests #21-#23.

"Left untouched" is checked with os.stat (inode, size, mtime). Restarts are simulated in-process
with the `restart` fixture. Files AgentWiz wrote are read only for what no route can show: that keys
are absent and that unknown fields are kept (#22); they are parsed, never compared as text.
"""
import json
import os
import threading

import pytest

from agentwiz.workspace import open_workspace
from conftest import (answer, complete, configure, failure_of, file_stat, get_state, request, status_of,
                      write_ws_file)

KEY_A, KEY_B = "sk-SENTINEL-KEY-A-0001", "sk-SENTINEL-KEY-B-0002"


def profile_fields(backend, model, **extra):
    return {"kind": "openai_compat", "base_url": backend.url + "/v1", "model": model, "provider": "",
            "reasoning": "", "max_tokens": None, **extra}


def save(ui, name, revision=None, **fields):
    rev = get_state(ui)["revision"] if revision is None else revision
    return request(ui, "/api/profiles/save", {"name": name, "revision": rev, **fields})


def select(ui, name, revision=None):
    rev = get_state(ui)["revision"] if revision is None else revision
    return request(ui, "/api/profiles/select", {"name": name, "revision": rev})


def delete(ui, name, revision=None):
    rev = get_state(ui)["revision"] if revision is None else revision
    return request(ui, "/api/profiles/delete", {"name": name, "revision": rev})


def settings_of(state):
    return {k: state[k] for k in ("kind", "base_url", "model", "provider", "reasoning", "max_tokens")}


def open_ui(make_ui, tmp_path, name="w"):
    ws = open_workspace(tmp_path / "ws", name)
    make_ui.opened.append(ws)
    return make_ui(workspace=ws)


# 21 --------------------------------------------------------------------------------------------
VALID_PROFILE = {"kind": "lmstudio", "base_url": "", "model": "m", "provider": "", "reasoning": "",
                 "max_tokens": None}


def profiles_text(**overrides):
    doc = {"format_version": 1, "active": "p", "profiles": {"p": dict(VALID_PROFILE)}}
    doc.update(overrides)
    return json.dumps(doc)


def bad_profile(**fields):
    return profiles_text(profiles={"p": {**VALID_PROFILE, **fields}})


DAMAGED = [
    # (file, text, whole workspace read-only, code)
    ("workspace.json", "{not json", True, "invalid_json"),
    ("workspace.json", '{"format_version": 2, "created_by": "AgentWiz 9", "save_runs": false}', True,
     "newer_format"),
    ("workspace.json", '{"format_version": 1, "created_by": "AgentWiz 0.1.1", "save_runs": "false"}', True,
     "invalid_field"),
    ("profiles.json", '{"format_version": 1, "active": null, "active": null, "profiles": {}}', False,
     "invalid_json"),
    ("profiles.json", profiles_text()[:-1] + ', "extra": 0.10000000000000000001}', False, "invalid_json"),
    ("profiles.json", profiles_text(format_version=2), False, "newer_format"),
    ("profiles.json", profiles_text(active="missing"), False, "invalid_field"),
    ("profiles.json", bad_profile(max_tokens="64"), False, "invalid_field"),
    ("profiles.json", bad_profile(base_url="http://u:p@127.0.0.1:1/v1"), False, "invalid_field"),
]


@pytest.mark.parametrize("file, text, whole, code", DAMAGED)
def test_damaged_files_read_only(make_ui, tmp_path, server, file, text, whole, code):
    folder = tmp_path / "ws" / "w"
    write_ws_file(folder / file, text)
    damaged = file_stat(folder / file)
    ui = open_ui(make_ui, tmp_path)

    state = get_state(ui)
    assert state["workspace"]["read_only"] is whole
    assert state["profiles"]["read_only"] is True
    assert [(p["file"], p["code"]) for p in state["workspace"]["problems"]] == [(file, code)]
    assert "u:p" not in state["workspace"]["problems"][0]["message"]
    if file == "profiles.json":
        assert state["profiles"]["names"] == [] and state["profiles"]["active"] is None

    for attempt in (lambda: save(ui, "q", **profile_fields(server, "m")), lambda: select(ui, "p"),
                    lambda: select(ui, None), lambda: delete(ui, "p")):
        fail = failure_of(attempt)
        assert fail.status == 409 and fail.body["code"] == "read_only"
    assert file_stat(folder / file) == damaged
    if whole:
        assert not (folder / "profiles.json").exists()

    server.reply(answer("still works"))
    configure(ui, server)
    assert complete(ui, "hi")[-1]["result"]["text"] == "still works"


def test_damage_during_session_needs_restart(make_ui, tmp_path, server, restart):
    folder = tmp_path / "ws" / "w"
    good = profiles_text()
    write_ws_file(folder / "profiles.json", good)
    ui = open_ui(make_ui, tmp_path)
    assert get_state(ui)["profiles"]["read_only"] is False

    (folder / "profiles.json").write_text(bad_profile(max_tokens="64"))
    damaged = file_stat(folder / "profiles.json")
    rev = get_state(ui)["revision"]
    fail = failure_of(lambda: save(ui, "p", revision=rev, model="m2"))
    assert fail.status == 409 and fail.body["code"] == "read_only"
    state = get_state(ui)
    assert state["revision"] == rev + 1 and state["profiles"]["read_only"] is True
    assert file_stat(folder / "profiles.json") == damaged

    (folder / "profiles.json").write_text(good)  # repaired, but recovery is by restart
    assert failure_of(lambda: save(ui, "p", model="m2")).body["code"] == "read_only"

    ui = restart(ui)
    assert save(ui, "p", model="m2")["model"] == "m2"


# 22 --------------------------------------------------------------------------------------------
def test_profile_lifecycle_and_restart(make_ui, tmp_path, make_server, restart):
    a, b = make_server().reply(answer()), make_server().reply(answer())
    ui = open_ui(make_ui, tmp_path)
    folder = ui.workspace.path

    state = save(ui, "a", **profile_fields(a, "m-a"), api_key=KEY_A)
    assert state["profiles"]["active"] == "a" and state["key_source"] == "session"
    state = save(ui, "b", **profile_fields(b, "m-b"), api_key=KEY_B)
    assert state["profiles"]["names"] == ["a", "b"] and state["profiles"]["active"] == "b"

    state = select(ui, "a")
    assert state["model"] == "m-a" and state["key_source"] == "session"
    complete(ui, "hi")
    assert a.requests[-1]["body"]["model"] == "m-a"
    assert a.requests[-1]["headers"]["authorization"] == f"Bearer {KEY_A}"

    state = request(ui, "/api/config", {"revision": state["revision"], "model": "m-a2"})
    assert state["profiles"]["modified"] is True
    state = save(ui, "a")
    assert state["profiles"]["modified"] is False and state["model"] == "m-a2"

    state = delete(ui, "b")
    assert state["profiles"]["names"] == ["a"] and state["profiles"]["active"] == "a"
    before = settings_of(state)
    state = select(ui, None)
    assert state["profiles"]["active"] is None and settings_of(state) == before
    assert state["profiles"]["modified"] is False
    select(ui, "a")

    rev = get_state(ui)["revision"]
    for route, name in (("save", "../x"), ("select", ""), ("delete", "a/b")):
        assert status_of(lambda: request(ui, f"/api/profiles/{route}", {"name": name, "revision": rev})) == 400
    assert get_state(ui)["revision"] == rev

    ui = restart(ui)
    state = get_state(ui)
    assert state["profiles"]["names"] == ["a"] and state["profiles"]["active"] == "a"
    assert settings_of(state) == profile_fields(a, "m-a2") and state["key_source"] == "none"
    complete(ui, "hi")
    assert a.requests[-1]["body"]["model"] == "m-a2"
    assert "authorization" not in a.requests[-1]["headers"]

    for path in folder.rglob("*"):
        data = path.read_bytes()
        assert KEY_A.encode() not in data and KEY_B.encode() not in data, path.name


def test_delete_active_profile(make_ui, tmp_path, server):
    ui = open_ui(make_ui, tmp_path)
    before = settings_of(save(ui, "a", **profile_fields(server, "m-a")))
    state = delete(ui, "a")
    assert state["profiles"]["names"] == [] and state["profiles"]["active"] is None
    assert settings_of(state) == before
    fail = failure_of(lambda: select(ui, "a"))
    assert fail.status == 404 and fail.body["code"] == "no_such_profile"


def test_unknown_fields_kept(make_ui, tmp_path, server):
    folder = tmp_path / "ws" / "w"
    doc = {"format_version": 1, "active": "a", "future_top": {"x": [1, 2]},
           "profiles": {"a": {**profile_fields(server, "m-a"), "future_field": "kept"}}}
    write_ws_file(folder / "profiles.json", json.dumps(doc))
    ui = open_ui(make_ui, tmp_path)
    assert save(ui, "a", model="m-new")["model"] == "m-new"
    saved = json.loads((folder / "profiles.json").read_bytes())
    assert saved["future_top"] == {"x": [1, 2]}
    assert saved["profiles"]["a"]["future_field"] == "kept"
    assert saved["profiles"]["a"]["model"] == "m-new"


# 23 --------------------------------------------------------------------------------------------
def race(*calls):
    """Run the calls at the same moment; returns their HTTP statuses in order."""
    barrier, statuses = threading.Barrier(len(calls)), [None] * len(calls)

    def run(i, call):
        barrier.wait()
        statuses[i] = status_of(call)

    threads = [threading.Thread(target=run, args=(i, c)) for i, c in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    return statuses


def test_profile_conflicts(make_ui, tmp_path, server, restart, monkeypatch):
    ui = open_ui(make_ui, tmp_path)
    folder = ui.workspace.path
    save(ui, "p", **profile_fields(server, "m-p"))
    save(ui, "r", **profile_fields(server, "m-r"))

    # (a) stale revisions change nothing
    stale = get_state(ui)["revision"]
    request(ui, "/api/config", {"revision": stale, "model": "m-other"})
    current, on_disk = get_state(ui), file_stat(folder / "profiles.json")
    for attempt in (lambda: save(ui, "q", revision=stale), lambda: select(ui, "p", revision=stale),
                    lambda: delete(ui, "p", revision=stale)):
        fail = failure_of(attempt)
        assert fail.status == 409 and fail.body["code"] == "stale_revision"
    assert get_state(ui) == current and file_stat(folder / "profiles.json") == on_disk

    # (b) two saves of different new profiles with one revision: one wins, nothing is lost
    rev = get_state(ui)["revision"]
    statuses = race(lambda: save(ui, "n1", revision=rev, model="m-n1"),
                    lambda: save(ui, "n2", revision=rev, model="m-n2"))
    assert sorted(statuses) == [200, 409]
    winner = "n1" if statuses[0] == 200 else "n2"
    ui = restart(ui)
    state = get_state(ui)
    assert state["profiles"]["names"] == sorted(["p", "r", winner]) and state["profiles"]["active"] == winner

    # ... and a save racing a delete of the active profile leaves `active` consistent
    select(ui, "p")
    rev = get_state(ui)["revision"]
    statuses = race(lambda: save(ui, "q", revision=rev), lambda: delete(ui, "p", revision=rev))
    assert sorted(statuses) == [200, 409]
    ui = restart(ui)
    state = get_state(ui)
    if statuses[0] == 200:
        assert state["profiles"]["names"] == sorted(["p", "q", "r", winner]) and state["profiles"]["active"] == "q"
    else:
        assert state["profiles"]["names"] == sorted(["r", winner]) and state["profiles"]["active"] is None

    # (c) a failed write changes nothing, in memory or on disk
    before = get_state(ui)

    def refuse(src, dst):
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as patched:
        patched.setattr(os, "replace", refuse)
        fail = failure_of(lambda: save(ui, "z", model="m-z"))
    assert fail.status == 500 and fail.body["code"] == "write_failed"
    assert get_state(ui) == before
    assert not [p for p in folder.iterdir() if p.name.endswith(".tmp")]
    ui = restart(ui)
    state = get_state(ui)
    assert state["profiles"]["names"] == before["profiles"]["names"]
    assert state["profiles"]["active"] == before["profiles"]["active"]
