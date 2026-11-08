"""The tutorial's topic registry (techspec 0.1.4, 14.1): plain, read-only data that the server, the page and the
tests all read. Nothing here runs anything.

Order is the tutorial's order, grouped by tab in tab order. `updated_in` is the AgentWiz version whose release last
changed a topic's demo; a release that changes what a topic shows or prepares sets it to its own version in the same
change, and nothing else moves it."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

# The example contract every topic that needs "fields" uses, saved into the tutorial's temporary workspace as
# revision 1 of the version 2 container `fields` in family Tutorial. It checks structure only: a wrong number of
# the right type passes it, and only an exact Workshop example expectation can reject it.
CONTRACT = '''{
  "format": "json",
  "description": "Tutorial exact-number fields",
  "schema": {
    "type": "object",
    "properties": {
      "id": {"type": "integer", "description": "The record id, copied exactly"},
      "ratio": {"type": "number", "description": "The ratio, copied exactly"}
    },
    "required": ["id", "ratio"],
    "additionalProperties": false
  }
}'''
CONTRACT_NAME = "fields"
CONTRACT_FAMILY = "Tutorial"
CONTRACT_LABEL = "Tutorial example"

ROLE = "fast"                 # the role the example model block uses
ROLE_PROFILE = "tutorial-json"  # the seeded profile it is mapped to
ROLE_MODEL = "block-fields-repair"    # that profile's stand-in model
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


def _private_document(index):
    from .tutorial_support.private_summary import DOCUMENTS
    return DOCUMENTS[index][0]


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
    Topic("contract", "Define a contract", "A contract describes the structure of the value you want. Save the filled "
          "example to learn where reusable requirements live; Check can apply it to text. A wrong number of the right "
          "type still passes: exact values belong in Workshop example expectations.", "contracts", "0.2.0", "complete",
          False, _page("contracts.tutorialPrepare", [CONTRACT, '{"id":1,"ratio":0.5}', "my-fields"]), "contracts-save"),
    Topic("repair", "Repair a response", "Send asks for structured data using the selected contract. Preview repair "
          "shows a follow-up request; Send repair continues the same run with feedback.", "checked-request",
          "0.2.0", "json-repair", True,
          _page("checked-request.tutorialPrepare", ["Extract the id and ratio as JSON."]), "checked-send"),
    Topic("model-block", "Run a model block",
          "A block is a saved step. This one asks a model (through the role fast) for the id and ratio in a report, "
          "plus a one-sentence note: two named outputs returned in one JSON object, each checked on its own, "
          "with a repair if either fails. Its system instructions are in Design, Request preview shows the exact "
          "messages including the generated output section, and its temperature is inherited from the role's "
          "default. Show the role opens the editor: "
          "filter by location and family, pick a role from the list, then review its purpose, assigned model profile and "
          "where it connects. Add family offers your own domains; Save role assignments keeps every role. Local uses a "
          "server on this machine; unconfigured roles cannot run. Then Run, and compare the two attempts in the trace.",
          "workshop", "0.2.1", "complete", True,
          _page("workshop.tutorialPrepare",
                ["extract_fields", {"report": "Record 9007199254740993 has a ratio of 0.10000000000000000001."}],
                [{"id": "show-role", "label": "Show the role"}]), "ws-run-test"),
    Topic("tool-block", "Run a tool block",
          "This block calls the tutorial server's numbers tool with two exact numbers typed into the arguments. Run "
          "it: the output equals the inputs exactly, and the trace shows both fills as typed, with their "
          "provenance.", "workshop", "0.2.0", "complete", True,
          _page("workshop.tutorialPrepare",
                ["echo_numbers", {"id": "9007199254740993", "ratio": "0.10000000000000000001"}]), "ws-run-test"),
    Topic("script-block", "Run a transform and stop a local model",
          "Run the built-in word counter. Then try the local model that waits. Run it and press Stop.",
          "workshop", "0.2.1", "complete", True,
          _page("workshop.tutorialPrepare", ["tutorial_words", {"text": "The quick brown fox jumps over the lazy dog"}],
                [{"id": "wait-script", "label": "Try a local model that waits"}]), "ws-run-test"),
    Topic("workshop-design", "Create a Workshop block",
          "Save the word counter and review its port labels. Run the prepared examples in Test. In Design, "
          "change the count unit and save a new revision. Compare both revisions using the same examples.",
          "workshop", "0.2.1", "complete", False, _page("workshop.tutorialCreate"), "ws-save-revision"),
    Topic("workflow", "Wire and run a workflow",
          "In Design, follow the input into Count words and Count characters, then follow both outputs into "
          "Format report. The last stage was upgraded from v3 to v4 and has a local-only input. "
          "In Run, check readiness for the prepared example, review the trusted local model and execution order, then "
          "run it and inspect the node trace. The report reads Words: 2; characters: 7.",
          "workflows", "0.2.1", "complete", False,
          _page("workflows.tutorial", ["tutorial_report", "one two", "two_words"],
                [{"id": "show-workflow-upgrade", "label": "Show the v3 model revision"}]), "wf-preflight"),
    Topic("release-gate", "Review a Release Gate",
          "Run the synthetic candidate. The trusted local model supplies advisory findings; inspect the value and destinations, "
          "acknowledge the concerns and choose Review and release. All tutorial services are local stand-ins; no real cloud is contacted.",
          "workflows", "0.2.1", "complete", False,
          _page("workflows.tutorial", ["tutorial_release", "[P1] attended on 7 November."]), "wf-preflight"),
    Topic("private-summary-local", "Summarise private text locally",
          "A local model replaces the synthetic names, emails and record numbers with placeholders, a second local "
          "model summarises the redacted text, and the built-in restore step puts the originals back. Check readiness: "
          "every destination is local and trusted. Run the prepared example and read its text checks. Passing these "
          "checks says nothing about real documents, and unlabelled workflows are not checked for private content.",
          "workflows", "0.2.1", "complete", False,
          _page("workflows.tutorial", ["private_summary_local", _private_document(0), "case_1", "document"]), "wf-preflight"),
    Topic("private-summary-release", "Release redacted text for a cloud summary",
          "The same redaction runs locally, then a Release Gate pauses the run: review the redacted value and the "
          "destinations it will reach, acknowledge the local assessment and approve. Only the approved value reaches "
          "the cloud stand-in; the restore step runs locally. Approval covers the reviewed value at the destinations "
          "shown, not forwarding inside a provider. All services here are local stand-ins.",
          "workflows", "0.2.1", "complete", False,
          _page("workflows.tutorial", ["private_summary_release", _private_document(0), "case_1", "document"]), "wf-preflight"),
    *(Topic("assist-" + action, "Assist · " + action.replace("_", " "),
            "Open Assist, choose describe and confirm the destination. Save, refine or discard the proposed "
            "description of this saved revision; the executable block does not change."
            if action == "describe" else
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

