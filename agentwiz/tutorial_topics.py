"""The tutorial's topic registry (techspec 0.1.4, 14.1): plain, read-only data that the server, the page and the
tests all read. Nothing here runs anything.

Order is the tutorial's order, grouped by tab in tab order. `updated_in` is the AgentWiz version whose release last
changed a topic's demo; a release that changes what a topic shows or prepares sets it to its own version in the same
change, and nothing else moves it."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

# The example contract every topic that needs "fields" uses (saved into the tutorial's temporary workspace).
CONTRACT = '''{
  "format_version": 1, "format": "json", "strict": false,
  "description": "Tutorial exact-number fields", "schema": null,
  "checks": [{"type": "json_equals", "value": {
    "id": 9007199254740993, "ratio": 0.10000000000000000001
  }}]
}'''

ROLE = "fast"                 # the role the example model block uses
ROLE_PROFILE = "tutorial-json"  # the seeded profile it is mapped to
ROLE_MODEL = "json-repair"    # that profile's stand-in model
BLOCKS = ("extract_fields", "echo_numbers", "word_count", "wait_for_stop")


@dataclass(frozen=True)
class Topic:
    id: str              # stable; the topic's name in routes and tests
    title: str
    tip: str
    tab: str             # the target tab's fragment
    updated_in: str      # the AgentWiz version whose release last changed this topic's demo
    model: str           # the stand-in model the active settings use while this topic is shown
    seed_contract: bool  # re-save the example contract if it is missing
    page: Mapping = field(default_factory=dict)   # {"hook": "<tab>.<method>" or None, "args": [...], "actions": [...]}
    target: str | None = None                     # the data-test name of the control the topic highlights


def _page(hook=None, args=(), actions=()):
    return {"hook": hook, "args": list(args), "actions": [dict(a) for a in actions]}


TOPICS: tuple[Topic, ...] = (
    Topic("chat", "Chat", "Send the filled message to start a conversation. Copy beside the answer lets you reuse it. "
          "Stop is available while a response is running.", "chat", "0.1.3", "complete", False,
          _page("chat.tutorialPrepare", ["Hello! Please give a short tutorial response."]), "chat-send"),
    Topic("document", "Use a document", "Use example document to open a preview. Copy includes provenance; paste it "
          "into Chat to discuss the document.", "documents", "0.1.3", "complete", False,
          _page(None, [], [{"id": "example-document", "label": "Use example document"}]), None),
    Topic("tool", "Call a tool", "A local echo tool and its arguments are ready. Run calls it; Copy lets you use its "
          "result in a conversation.", "mcp-tools", "0.1.3", "complete", False,
          _page("mcp-tools.tutorialPrepare", ["tutorial", "echo"]), "mcp-run"),
    Topic("contract", "Define a contract", "A contract describes the response you want. Save the filled example to "
          "learn where reusable requirements live; Check can apply it to text.", "contracts", "0.1.3", "complete",
          False, _page("contracts.tutorialPrepare", [CONTRACT, '{"id":1,"ratio":0.5}']), "contracts-save"),
    Topic("repair", "Repair a response", "Send asks for structured data using the selected contract. Preview repair "
          "shows a follow-up request; Send repair continues the same run with feedback.", "checked-request",
          "0.1.3", "json-repair", True,
          _page("checked-request.tutorialPrepare", ["Extract the id and ratio as JSON."]), "checked-send"),
    Topic("model-block", "Run a model block",
          "A block is a saved step. This one asks a model (through the role fast) for the id and ratio in a report, "
          "checks the answer against the fields contract and repairs it once. Show the role opens the editor: "
          "filter by location and family, select one role, then review its purpose and assigned model profile. "
          "Add family offers your own domains; Save all changes retains every role. Local uses a server on this machine; "
          "unconfigured roles cannot run. Then Run, and compare the two attempts in the trace.", "workshop", "0.2.0", "complete", True,
          _page("workshop.tutorialPrepare",
                ["extract_fields", {"report": "Record 9007199254740993 has a ratio of 0.10000000000000000001."}],
                [{"id": "show-role", "label": "Show the role"}]), "ws-run-test"),
    Topic("tool-block", "Run a tool block",
          "This block calls the tutorial server's numbers tool with two exact numbers typed into the arguments. Run "
          "it: the output equals the inputs exactly, and the trace shows both fills as typed, with their "
          "provenance.", "workshop", "0.2.0", "complete", True,
          _page("workshop.tutorialPrepare",
                ["echo_numbers", {"id": "9007199254740993", "ratio": "0.10000000000000000001"}]), "ws-run-test"),
    Topic("script-block", "Run and stop a script block",
          "This block runs a small script that counts words. Run it for the count; then Try a script that waits, "
          "Run it and press Stop to see a script and its process group ended.", "workshop", "0.2.0", "complete", True,
          _page("workshop.tutorialPrepare", ["word_count", {"text": "The quick brown fox jumps over the lazy dog"}],
                [{"id": "wait-script", "label": "Try a script that waits"}]), "ws-run-test"),
    Topic("workshop-design", "Create a Workshop block", "Save the word counter and confirm its captured runtime. In Test, use New input and Run test for a one-off run, or select a prepared example. Add example keeps the current inputs in memory; Save example collection persists pending examples separately. Review correctness. Return to Design, change Count unit to Characters, Apply parameters and Save again. In Compare, choose the earlier baseline and compare the same examples; inspect both answers and totals.", "workshop", "0.2.0", "complete", False, _page("workshop.tutorialCreate"), "ws-save-revision"),
    *(Topic("assist-" + action, "Assist · " + action.replace("_", " "),
            "Open Assist, prepare the request and inspect its destination. Apply or discard the returned proposal.",
            "workshop", "0.2.0", "complete", False,
            _page("workshop.tutorialAssist", [action]), "ws-assist")
      for action in ("create", "improve", "describe", "suggest_examples", "explain_failure")),
)

_BY_ID = {t.id: t for t in TOPICS}


def public(topic: Topic) -> dict:
    """What the page and the tests read: id, title, tip, tab, updated_in, page, target."""
    return {"id": topic.id, "title": topic.title, "tip": topic.tip, "tab": topic.tab, "updated_in": topic.updated_in,
            "page": {"hook": topic.page.get("hook"), "args": list(topic.page.get("args", [])),
                     "actions": [dict(a) for a in topic.page.get("actions", [])]},
            "target": topic.target}


def by_id(topic_id: object) -> Topic:
    """The topic with this ID. Raises KeyError for an unknown or non-string ID."""
    if not isinstance(topic_id, str) or topic_id not in _BY_ID:
        raise KeyError(topic_id)
    return _BY_ID[topic_id]
