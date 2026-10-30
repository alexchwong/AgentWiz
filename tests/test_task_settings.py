"""Task settings through HTTP: techspec 0.1.2 tests #30-#35. Backends are the scripted fake in conftest."""
import json
import threading

import pytest

from conftest import (Reply, answer, complete, configure, failure_of, file_stat, get_state, lmstudio_models,
                      native_answer, request, responses_answer, write_ws_file)

JSON = {"Content-Type": "application/json"}


def lmstudio(ui, server):
    return configure(ui, server, kind="lmstudio")


# 30 --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind, reasoning, task_field, value, wire, replies", [
    ("openrouter", "", "max_tokens", 64, "max_tokens", lambda: [answer()]),
    ("openai_compat", "", "max_tokens", 64, "max_tokens", lambda: [answer()]),
    ("lmstudio", "off", "repetition_penalty", 1.1, "repeat_penalty",
     lambda: [lmstudio_models("m", reasoning=None), native_answer()]),
    ("lmstudio", "low", "max_tokens", 64, "max_output_tokens", lambda: [responses_answer()]),
])
def test_task_settings_reach_backend(ui, server, kind, reasoning, task_field, value, wire, replies):
    server.reply(*replies())
    configure(ui, server, kind=kind, **({"api_key": "k"} if kind == "openrouter" else {}))
    task = {"reasoning": reasoning} if reasoning else {}
    if task_field in ("max_tokens", "temperature"):
        task[task_field] = value
    else:
        task["advanced"] = {task_field: value}
    evs = complete(ui, "hi", task=task)
    end = evs[-1]
    assert end["type"] == "result", end
    sent = server.requests[-1]["body"]
    assert sent[wire] == value
    assert "temperature" not in sent and "top_p" not in sent and "seed" not in sent
    assert end["task"] == task


# 31 --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind, task", [
    ("lmstudio", {"reasoning": "low", "advanced": {"top_k": 40}}),
    ("openai_compat", {"reasoning": "low"}),
    ("lmstudio", {"reasoning": "off", "temperature": 1.5}),     # modelSpell's check_settings refuses
    ("openai_compat", {"advanced": {"seed": 2 ** 53}}),
    ("openai_compat", {"advanced": {"seed": 1.5}}),
    ("openai_compat", {"advanced": {"stop": ["ok", ""]}}),
    ("openai_compat", {"advanced": {"logit_bias": {}}}),
    ("openai_compat", {"temperature": True}),
])
def test_task_refusals_send_nothing(ui, server, kind, task):
    configure(ui, server, kind=kind)
    fail = failure_of(lambda: complete(ui, "hi", task=task))
    assert fail.status == 400 and fail.body["code"] == "task_setting"
    assert server.requests == []
    assert ui.state.budget.in_flight == 0


def test_blank_model_refused(ui, server):
    configure(ui, server, model="")
    fail = failure_of(lambda: complete(ui, "hi"))
    assert fail.status == 400 and fail.body["error"].startswith("SettingsError:")
    assert server.requests == []


# 32 --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("models, sent_reasoning, lookup", [
    (lambda: lmstudio_models("m", reasoning=None), None, "used"),
    (lambda: lmstudio_models("m", reasoning=["off", "on"]), "off", "used"),
    (lambda: Reply(status=500, body=b"{}", headers=JSON), "off", "failed"),
    (lambda: lmstudio_models("other", reasoning=None), "off", "not_listed"),
])
def test_lmstudio_off_model_lookup(ui, server, models, sent_reasoning, lookup):
    server.reply(models(), native_answer())
    lmstudio(ui, server)
    end = complete(ui, "hi", task={"reasoning": "off"})[-1]
    assert end["type"] == "result" and end["model_lookup"] == lookup
    chat = [r for r in server.requests if r["path"] == "/api/v1/chat"]
    assert len(chat) == 1
    assert chat[0]["body"].get("reasoning") == sent_reasoning


def test_lmstudio_off_failed_lookup_then_400(ui, server):
    secret = b'{"error": "BACKEND-BODY-TEXT reasoning off unsupported"}'
    server.reply(Reply(status=500, body=b"{}", headers=JSON), Reply(status=400, body=secret, headers=JSON))
    lmstudio(ui, server)
    evs = complete(ui, "hi", task={"reasoning": "off"})
    assert len(evs) == 1 and evs[0]["type"] == "error" and evs[0]["model_lookup"] == "failed"
    assert not any(b"BACKEND-BODY-TEXT" in body for body in ui.transcript)
    assert len([r for r in server.requests if r["path"] == "/api/v1/chat"]) == 1


# 33 --------------------------------------------------------------------------------------------
def test_openrouter_require_parameters(ui, server):
    server.reply(answer())
    configure(ui, server, kind="openrouter", api_key="k", provider="deepinfra/turbo")
    complete(ui, "hi", task={"temperature": 0.5})
    assert server.requests[-1]["body"]["provider"] == {"order": ["deepinfra/turbo"], "allow_fallbacks": False,
                                                       "require_parameters": True}
    server.reply(Reply(status=404, body=b'{"error": {"message": "No endpoints found"}}', headers=JSON))
    evs = complete(ui, "hi", task={"temperature": 0.5})
    assert len(evs) == 1 and evs[0]["type"] == "error" and evs[0]["hint"] == "openrouter_no_provider"
    server.reply(Reply(status=500, body=b"{}", headers=JSON))
    evs = complete(ui, "hi", task={"temperature": 0.5})
    assert evs[0]["type"] == "error" and "hint" not in evs[0]


# 34 --------------------------------------------------------------------------------------------
def test_task_settings_per_request(ui, server):
    server.reply(answer())
    configure(ui, server)
    saved = get_state(ui)["task"]
    barrier, results = threading.Barrier(2), {}

    def tab(temperature):
        barrier.wait()
        results[temperature] = complete(ui, "hi", task={"temperature": temperature})[-1]

    threads = [threading.Thread(target=tab, args=(t,)) for t in (0.1, 0.9)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(r["body"]["temperature"] for r in server.requests) == [0.1, 0.9]
    assert {t: r["task"]["temperature"] for t, r in results.items()} == {0.1: 0.1, 0.9: 0.9}
    assert get_state(ui)["task"] == saved


# 35 --------------------------------------------------------------------------------------------
def save_task(ui, task, revision=None):
    rev = get_state(ui)["task"]["revision"] if revision is None else revision
    return request(ui, "/api/task_settings/save", {"task": task, "revision": rev})


TASK = {"reasoning": "low", "temperature": 0.7, "max_tokens": None, "advanced": {"top_k": 40, "stop": ["END"]}}


def test_saved_task_settings(ui, server, restart, make_ui, tmp_path):
    config_rev = get_state(ui)["revision"]
    state = save_task(ui, TASK)
    assert state["task"]["saved"] == TASK and state["task"]["revision"] == 2
    assert state["revision"] == config_rev  # separate revisions
    path = ui.workspace.path / "task_settings.json"
    on_disk = file_stat(path)
    fail = failure_of(lambda: save_task(ui, {"temperature": 0.1}, revision=1))
    assert fail.status == 409 and fail.body["code"] == "stale_revision"
    assert file_stat(path) == on_disk
    assert save_task(ui, TASK)["task"]["revision"] == 2   # no change: no write, same revision
    assert file_stat(path) == on_disk

    # unknown fields survive a save and are not shown
    doc = json.loads(path.read_bytes())
    doc["future_top"] = {"x": 1}
    doc["advanced"]["future_sampler"] = 3
    path.write_text(json.dumps(doc))
    ui = restart(ui)
    state = get_state(ui)
    assert state["task"]["saved"] == TASK
    save_task(ui, {**TASK, "temperature": 0.2})
    doc = json.loads(path.read_bytes())
    assert doc["future_top"] == {"x": 1} and doc["advanced"]["future_sampler"] == 3
    assert doc["temperature"] == 0.2
    assert "future_sampler" not in get_state(ui)["task"]["saved"]["advanced"]


@pytest.mark.parametrize("text, code", [
    ('{"format_version": 1, "temperature": "hot"}', "invalid_field"),
    ('{"format_version": 2}', "newer_format"),
])
def test_saved_task_settings_read_only(make_ui, tmp_path, server, text, code):
    from agentwiz.workspace import open_workspace
    folder = tmp_path / "ws" / "w"
    write_ws_file(folder / "task_settings.json", text)
    before = file_stat(folder / "task_settings.json")
    ws = open_workspace(tmp_path / "ws", "w")
    make_ui.opened.append(ws)
    ui = make_ui(workspace=ws)
    state = get_state(ui)
    assert state["task"]["read_only"] is True
    assert state["task"]["saved"] == {"reasoning": "", "temperature": None, "max_tokens": None, "advanced": {}}
    assert ("task_settings.json", code) in [(p["file"], p["code"]) for p in state["workspace"]["problems"]]
    fail = failure_of(lambda: save_task(ui, {"temperature": 0.1}))
    assert fail.status == 409 and fail.body["code"] == "read_only"
    assert file_stat(folder / "task_settings.json") == before
    server.reply(answer("still works"))
    configure(ui, server)
    assert complete(ui, "hi", task={"temperature": 0.3})[-1]["result"]["text"] == "still works"


def test_task_settings_in_read_only_workspace(make_ui, tmp_path):
    from agentwiz.workspace import open_workspace
    folder = tmp_path / "ws" / "w"
    write_ws_file(folder / "workspace.json", "{not json")
    write_ws_file(folder / "task_settings.json", json.dumps({"format_version": 1, "temperature": 0.4}))
    ws = open_workspace(tmp_path / "ws", "w")
    make_ui.opened.append(ws)
    ui = make_ui(workspace=ws)
    state = get_state(ui)
    assert state["task"]["saved"]["temperature"] == 0.4 and state["task"]["read_only"] is True
    fail = failure_of(lambda: save_task(ui, {"temperature": 0.1}))
    assert fail.status == 409 and fail.body["code"] == "read_only"
