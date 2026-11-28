"""Read-only external company tools and single-use exact-argument review."""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from . import agent_capabilities, history, jsonio, outbound_policy, runner, runs, webspell
from .state import NoSuchServer


@dataclass(frozen=True)
class ReviewedTool:
    capability: agent_capabilities.Capability
    snapshot: object
    revision: str | None


def capture(state, preferences):
    revision, rows = state.agent.store.read()
    available, _ = agent_capabilities.mcp_candidates(state, rows, mode="cloud",
                                                      web_on=preferences["webspell"])
    result = {}
    for item in available:
        if item.effects != "read-only" or item.results_local_only or item.restricted_definition:
            continue
        try:
            snapshot = state.mcp_snapshot_for_block(item.server)
        except NoSuchServer:
            continue
        if snapshot.url == item.url:
            result[item.name] = ReviewedTool(item, snapshot, revision)
    return result


def still_authorised(service, work, tool):
    from .company import Refusal
    state = service.state
    cap, snapshot = tool.capability, tool.snapshot
    try:
        if work.cancel.cancelled or state.store.closed:
            raise Refusal("stopped")
        if time.monotonic() >= work.deadline:
            raise Refusal("deadline_exceeded")
        service.current(work.intake, work.binding)
        if state.mcp_snapshot_for_block(cap.server) != snapshot:
            raise Refusal("configuration_changed")
        revision, rows = state.agent.store.read()
        found = state.mcp_client.observed_schema(snapshot.identity, cap.tool)
        if revision != tool.revision or found is None or found[0] != cap.schema_digest:
            raise Refusal("configuration_changed")
        if cap.server == webspell.SERVER:
            if not state.chat_preferences()["webspell"] or not webspell.compatible(state.mcp_client, snapshot):
                raise Refusal("configuration_changed")
        else:
            row = state.agent.store.row(rows, snapshot.url, cap.server, cap.tool, cap.schema_digest)
            if (not row["enabled"] or not row["no_host_files"] or row["effects"] != "read-only"
                    or row["results_local_only"]):
                raise Refusal("configuration_changed")
    except NoSuchServer:
        raise outbound_policy.Refused("configuration_changed") from None
    except Refusal as exc:
        raise outbound_policy.Refused(exc.code) from None


def review(service, work, tool, arguments):
    from .company import Refusal
    cap = tool.capability
    content = jsonio.encode_exact(arguments)
    if cap.server == webspell.SERVER:
        urls = [arguments["url"]] if "url" in arguments else arguments.get("urls") or []
        if any(not webspell.public_url(url) or url not in work.web_urls for url in urls):
            raise Refusal("destination_refused")
    service.public_text(content, 32 << 10)
    pending = agent_capabilities.Approval(
        work.ident, secrets.token_hex(16), cap.name, cap.identity, cap.schema_digest, tool.revision,
        agent_capabilities.sha256(content), content,
        min(work.deadline, time.monotonic() + agent_capabilities.APPROVAL_WAIT_S))
    with service.lock:
        service.reserve(work)
        work.approval = pending
    try:
        with pending.condition:
            while pending.decision is None and not work.cancel.cancelled:
                left = pending.expires - time.monotonic()
                if left <= 0:
                    break
                pending.condition.wait(left)
        if work.cancel.cancelled:
            raise Refusal("stopped")
        if pending.decision != "approve":
            raise Refusal("tool_denied" if pending.decision == "deny" else "approval_expired")
        return pending
    finally:
        with service.lock:
            work.approval = None
            work.trace.append({"stage": "approval", "capability": cap.name, "decision": pending.decision,
                               "arguments_sha256": pending.arguments_sha256})


def send(service, work, tool, arguments):
    from .company import Refusal
    state = service.state
    try:
        still_authorised(service, work, tool)
    except outbound_policy.Refused as exc:
        raise Refusal(str(exc)) from None
    pending = review(service, work, tool, arguments)

    def authorize():
        still_authorised(service, work, tool)
        if (time.monotonic() >= pending.expires
                or not pending.valid_for(jsonio.encode_exact(arguments), tool.capability.schema_digest, tool.revision)):
            raise outbound_policy.Refused("approval_invalid")
        pending.used = True

    def execute(operation):
        operation.authorize = authorize
        operation.allow_call_retry = False
        return state.mcp_client.call(operation, tool.snapshot, tool.capability.tool, arguments)

    with service.lock:
        service.reserve(work)
        reservation = state.mcp_ops.reserve()
        if reservation is None:
            raise Refusal("admission_busy", 503)
        try:
            op = state.mcp_ops.start("call", tool.snapshot, execute, reservation=reservation)
        except BaseException:
            reservation.release()
            raise
        work.mcp_operation = op
        timer = runner.make_timer(max(0, work.deadline - time.monotonic()), lambda: op.abandon("deadline"))
    try:
        while True:
            event, result = op.wait(.1)
            if event == "final":
                break
    finally:
        timer.cancel()
        # The native operation can publish a terminal notice before its worker exits.
        while op.ended_at is None or not op.notice_done.is_set():
            time.sleep(.01)
        with service.lock:
            work.mcp_operation = None

    if work.cancel.cancelled:
        raise Refusal("stopped")
    if result["outcome"] == "unknown":
        raise Refusal("external_outcome_uncertain")
    if result["outcome"] != "ok":
        raise Refusal("tool_failed")
    value = result["value"].result
    content = value.get("content", [])
    output = {"text": "\n\n".join(item["text"] for item in content if item.get("type") == "text")}
    if "structuredContent" in value:
        output["structured"] = value["structuredContent"]
    _, has_secret = history.redact(output, state.credentials())
    if has_secret:
        raise Refusal("credential_in_model_output")
    if tool.capability.server == webspell.SERVER:
        work.web_urls.update(webspell.result_urls(output.get("structured")))
        if len(work.web_urls) > 256:
            work.web_urls.clear()
    return output


def decide(service, work, body):
    from .company import Refusal, fields, text
    fields(body, ("call_id", "arguments_sha256", "decision"))
    call_id, sha = text(body["call_id"], 256), text(body["arguments_sha256"], 64)
    if body["decision"] not in ("approve", "deny"):
        raise Refusal("invalid_decision", 400)
    with service.lock:
        pending = work.approval
        if pending is None or pending.call_id != call_id or pending.arguments_sha256 != sha:
            raise Refusal("not_pending")
        with pending.condition:
            if pending.decision is not None or time.monotonic() >= pending.expires or work.cancel.cancelled:
                raise Refusal("not_pending")
            pending.decided_at = runs.utc_now()
            pending.decision = body["decision"]
            pending.condition.notify_all()
    return {"decision": body["decision"]}
