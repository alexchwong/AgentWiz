"""R-0: routes retired in 0.2.3 refuse a request that was well formed for the 0.2.2 handler (spec v15, 9.2).

A retired route is recognised by its router's own not-found body, never by a status alone: the old handlers
answered 404 or 409 with their own codes (`no_such_attempt`, `not_repairable`, `draft_expired`). Every case also
shows that nothing was sent to the model server and no attempt was reserved.
"""
from conftest import (HTTPFailure, answer, complete, configure, contract_pin, get_state, record_of, request,
                      save_contract)

ROUTER_404 = {"error": "not found"}           # the /api/ router's own not-found body
WORKSHOP_404 = {"code": "missing"}            # the /api/workshop/ router's own not-found body

TEXT_CONTRACT = '{"format": "text", "contains": ["pong"]}'


def refused(ui, path, body):
    try:
        request(ui, path, body)
    except HTTPFailure as failure:
        return failure.status, failure.body
    raise AssertionError(f"{path} unexpectedly succeeded")


def attempts_held(ui):
    return len(ui.state.store._attempts)


def test_synthesis_inline_route_is_retired(ui, server):
    """Stage 1. The 0.2.2 handler took exactly these members and answered `draft_expired` for an unknown draft."""
    configure(ui, server)
    body = {"format_version": 1, "draft": {"key": "b", "definition_text": "{}", "files": {}, "metadata": {}},
            "proposal_id": "p" * 32, "side": "outputs", "port": "out", "definition_text": None}
    assert refused(ui, "/api/workshop/synthesis-inline", body) == (404, WORKSHOP_404)
    assert server.requests == []


def chat_attempt(ui, server):
    configure(ui, server)
    server.reply(answer("pong"))
    record = record_of(complete(ui, "ping"))
    assert len(server.requests) == 1
    return record


def test_checked_request_source_is_refused(ui, server):
    """Stage 3b. A Checked request with a real saved-contract pin is refused on the source rule; no run."""
    configure(ui, server)
    save_contract(ui, "c", TEXT_CONTRACT)
    name, revision, digest = contract_pin(ui, "c")
    before = attempts_held(ui)
    body = {"source": "checked_request", "prompt": "ping", "system": "", "revision": get_state(ui)["revision"],
            "contract": {"name": name, "revision": revision, "digest": digest}}
    status, failure = refused(ui, "/api/complete", body)
    assert status == 400 and "source" in failure["error"]
    assert server.requests == [] and attempts_held(ui) == before
    assert not (ui.workspace.path / "runs").exists() or not any((ui.workspace.path / "runs").iterdir())


def test_repair_routes_are_retired(ui, server):
    """Stage 3b. A real committed Chat attempt was `not_repairable` (409) on 0.2.2; now the router's 404."""
    record = chat_attempt(ui, server)
    before = attempts_held(ui)
    for path in ("/api/repair/preview", "/api/repair"):
        assert refused(ui, path, {"attempt_id": record["id"]}) == (404, ROUTER_404)
    assert len(server.requests) == 1 and attempts_held(ui) == before


def test_contract_instructions_route_is_retired(ui, server):
    """Stage 3b. The 0.2.2 handler rendered a real pin's instructions."""
    configure(ui, server)
    save_contract(ui, "c", TEXT_CONTRACT)
    name, revision, digest = contract_pin(ui, "c")
    body = {"format_version": 2, "name": name, "revision": revision, "digest": digest}
    assert refused(ui, "/api/contracts/instructions", body) == (404, ROUTER_404)
    assert server.requests == []
