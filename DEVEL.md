# Coding-agent development and maintenance directives

Read this file before changing the repository. It owns agent workflow, maintenance policy and
**what belongs in each document**. `.clinerules` defines Cline's handover-review workflow;
do not modify it without explicit owner instruction. General implementation, review, refactor
or documentation permission does not grant that permission.

## Documentation ownership and placement

Use these destinations; do not turn this file back into a user manual, release runbook,
architecture encyclopedia or chronological build log.

| Document | Put here | Keep out |
| --- | --- | --- |
| `DEVEL.md` | Standing directives agents need to develop and maintain the repo; essential setup/check commands; the live-run quickstart exception. | User instructions, owner checklists/dispositions, detailed API/format inventories, historical decisions and build logs. |
| `README.md` | User installation, launch, configuration, feature use, tutorial guidance, supported platforms and limitations. | Agent policy, owner release procedures and regression checklists. |
| `docs/architecture.md` | Component map, detailed behavior contracts, API/events/state attributes, persistence/locking/resource reference, test infrastructure and checkout diagnostics. | General agent policy, release sign-off and historical build assignments. |
| `docs/attachment_interface.md` | The versioned reader/consumer interface contract and its coordinated change rules. | General maintenance instructions or unrelated product documentation. |
| `docs/releases.md` | Owner/authorized-maintainer release workflow, branch/publication mechanics, RC1–RC3 reference and installed-wheel smoke procedures. | Authority for an agent to publish, test-result history or feature usage. |
| `docs/decisions.md` | Specification lineage, dated owner dispositions, approved departures and historical implementation/build decisions. | New standing directives, current checklists or claims that old results validate current code. |
| `NEWS.md` | Concise user-visible changes, grouped by release. | Internal maintenance narratives, test logs and owner procedures. |

Before adding documentation, classify its audience and purpose and update the owning document.
Merge with existing coverage rather than copying it. A mixed paragraph must be split: retain the
agent obligation here and move explanation/procedure/history to its destination. A directive
issued by the owner still belongs here if it governs agent work; an owner disposition does not.
Only the live-run quickstart is an exception for extended operational examples in this file.

Keep this file concise and imperative. Add a directive only when it changes what an agent must
or must not do; link detailed reference instead of repeating it. Update affected documentation
in the same change as behavior, layout, commands, tests or release requirements. Do not add
historical inventories, implementation completion claims or release-phase instructions here.
Do not add repository-document-content tests to enforce this organization (T2).

For work in an area, read the relevant sections of [the architecture reference](docs/architecture.md) and preserve their
behavior contracts, including field/protocol/format and lifecycle details. Relocation is not
permission to weaken a requirement. Use current source and tests with the architecture reference.
Consult [the decision history](docs/decisions.md) for accepted departures and historical design
references. Verify the current candidate with actual check results; historical delivery reports
do not validate later changes. Raise conflicts explicitly.

## Workflow and scope

- Inspect the working tree and actual changes before editing; preserve existing uncommitted work.
  Treat handover completion and validation claims as claims to verify, not proof.
- Make the smallest justified correction against the agreed specification. Do not infer permission
  to continue a larger build, refactor unrelated areas or add features.
- Make routine implementation choices locally and record consequential decisions in the pull
  request or appropriate reference/handover, not as a growing decision log in this file.
- Report scope changes, new dependencies (required or optional) and unrecorded behavioral departures
  to the owner before merging; dependencies require approval.
- Do not commit, push, merge, tag or publish without instruction. Release instructions in
  `docs/releases.md` do not grant authorization.
- Finish the agreed scope, inspect the final diff and report corrections/reasons, checks actually
  passed/failed/blocked and remaining owner actions. Never weaken a check to obtain a pass.

## Environment and required commands

Work only in the checkout's `.venv`; use `scripts/dev_setup.sh [--python PATH]` for setup and
activate it. Use `python -m pytest`, never bare `pytest`. Never commit `.venv/` or a real API key.
Run from the repository root:

```bash
scripts/dev_setup.sh
. .venv/bin/activate
python -m pytest -q -m "not live"   # offline checkpoint gate
python -m pytest tests/test_page.py # focused page checks when relevant
python scripts/build_release.py --smoke # installed-wheel validation when required
```

Setup installs `.[dev]` (including required JSON Schema validation) and the matching Playwright Chromium. Missing Chromium blocks page
checks; do not skip them. Setup/service details and local modelSpell checkout instructions are
in `docs/architecture.md`. Never commit a local dependency override; releases use the pinned wheel.

The release builder uses committed `HEAD`, not uncommitted edits. To validate uncommitted work,
transfer it to a throwaway clone and commit only there; never commit this checkout just to run a
smoke check. For offline smoke prerequisites and commands, consult `docs/releases.md`.
Never present source-only checks as installed-wheel smoke or substitute a stand-in for modelSpell.

## Platform compatibility

Code, developer scripts and offline tests must work on Linux and macOS with Python 3.10 and 3.13,
as exercised by the CI matrix. Keep WSL2 support within the documented filesystem constraints;
native Windows support is not implied.

When changing process launch or cleanup, networking, environment handling, locale handling or
filesystem operations, inspect the relevant operating-system and standard-library behavior. A
Linux pass is not evidence of macOS compatibility. Prefer a shared implementation based on
supported APIs; use platform-specific handling only for an identified difference.

Distinguish behavior AgentWiz controls from behavior the operating system or child runtime
introduces. For example, loopback server startup must not require hostname resolution, and an
environment supplied to a subprocess need not remain unchanged after runtime initialization.
Check security-sensitive inputs at the boundary AgentWiz controls and retain end-to-end checks
of the required behavior. Any platform exception must be narrow, explained and must not permit
arbitrary inherited state.

Under T1–T3, use a focused behavioral regression check for an identified portability defect,
making the relevant dependency fail deterministically where practical. Do not hide portability
failures with broader allow-lists, platform skips or longer timeouts without establishing the
cause.

Run the relevant local checks and report the actual OS and interpreter used. If macOS cannot be
tested locally, report it as unverified locally; the existing CI matrix remains the release
criterion.

Shipped code must run on Python 3.10: use `from __future__ import annotations` for `X | None`
hints; do not use 3.11-only modules/APIs (`tomllib`, `datetime.UTC`, exception groups).

## Dependency and protocol boundaries

- Use the pinned modelSpell release wheel and sha256 in `pyproject.toml`. Fix modelSpell defects
  upstream and consume a released pin change, not vendored/private fixes.
- Use only modelSpell's public API (`__all__` and `__version__`); no private imports/attributes.
  Derive key sources from public `Endpoint.api_key_env`. Use `supported_settings()` and
  `check_settings()`; do not maintain a duplicate support table or offer an `extra` escape hatch.
- Do not add model-traffic transport code, retries, fallbacks, a second client or a thread that
  abandons a running model call. Preserve cancellation/sent-state semantics.
- On modelSpell upgrades, re-check error classes/messages against upstream source: only pre-send
  argument-validation messages may be exposed; errors that can contain backend text need fixed
  descriptions. Re-check unmapped exceptions and input validation. Details are in the reference.
- Preserve both supported MCP protocol eras and their detection, identity/session and dispatch
  rules in `docs/architecture.md`; do not retry/resend a tool call except the specified one-time
  legacy session-expiry rejection. Do not auto-insert or auto-send tool/attachment content.
- Keep `docs/attachment_interface.md` and the companion project contract synchronized with
  `agentwiz/attachments.py`; validate every reader result. While draft, keep interface version 1;
  freeze when docSpell's build starts and version subsequent changes as the contract requires.
- Preserve copied-source attribution. Port changes from modelSpell UI/tooling only by deliberate
  review, never automatic synchronization. Playwright remains dev-only; pin upgrades deliberately
  and rerun page checks. Shipped code must not import it.

## Architecture and safety invariants

- Preserve module responsibilities and the no-HTTP boundaries in the component map. Keep the page
  framework-free with no build step; keep `agentwiz/__init__.py` side-effect-free except its version
  re-export. `agentwiz/_version.py` is the sole version source.
- Use `agentwiz/jsonio.py` for all JSON output and preserve strict/exact-number and file-format
  contracts. Keep unknown fields, atomic-write behavior, revision freshness and workspace/library
  lock ownership as specified; do not invent migration or rollback guarantees.
- Preserve the exact permitted lock nestings, limits, cancellation gates and shutdown/cleanup
  ownership in the concurrency reference. Never reverse a lock chain. No step lock while taking
  state/store/token/operation locks; decide Stop under the lock, act after releasing it. Do not
  hold locks during checks, model/tool calls, script execution, page/history writes or shutdown waits.
- Keep non-blocking bounded admission and all resource limits. Abandoned workers and retained
  cleanup owners keep their slots until they exit. Validate block inputs before admission.
- Commit/finalize each attempt, operation and block run exactly once; committed records are
  immutable. Delivery is an observer, not run ownership. Keep save-after-final handling,
  attempt-correlated asynchronous updates, cancellation publication/dispatch and repair ordering.
- Supervise script pipes with a single close owner and bounded waits; never close a recorded raw
  descriptor number. Preserve process-group escalation, retained cleanup ownership and writer
  registrations until cleanup is confirmed. Scripts are not sandboxed; preserve that warning.
- Keep credentials in process memory, never profiles/records; preserve history redaction and
  permissions. Validate security-sensitive inputs at controlled boundaries. Insert server strings
  into the page with `textContent` or `new Option`, never markup.
- Never format an external exception's message in a handler. Expose only the sanctioned descriptions
  and narrowly allowed text in the error reference. File problems name paths/locations/rules,
  not values; unexpected errors expose class/stack locations, never messages, keys or raw child
  output. Preserve installer diagnostic suppression and shell-quoted success commands.

## UI and tutorial rules

1. Each tab demonstrates one function from start to finish and works without visiting any other tab.
2. Workshop authoring actions belong inside Design, Test and Compare. Group independent utilities in Workbench.
3. Explicitly selected Workshop drafts, examples and results may move within Workshop. Independent Workbench utilities use the clipboard.
4. Configuration is not a function. Model profiles, MCP servers and roles are header dialogs shared by every tab. Saved definitions (contracts, blocks) are shared the same way.
5. At most seven top-level tabs. The eighth means grouping into nested tabs, at most two levels deep.
6. Use Workshop, Workbench and History with at most two navigation levels.
7. Every output panel has a Copy button.
8. Page checks find elements by `data-test` attributes and assert values and states, never wording or layout.

Tutorial requirements:

1. Every function a release adds gets a tutorial topic, or a new stage in an existing topic. Configuration is shown inside the topic of the function that uses it.
2. A topic shows its feature's main path against the packaged stand-ins. Limits, refusals, races and edge cases belong in tests, not in the tutorial.
3. Each topic carries `updated_in`: the version that last changed what it shows or prepares. A release that changes a demo sets it in the same change; nothing else moves it.
4. Topics live in one registry. The server, the page and the tests read it; topics are addressed by ID.
5. The tutorial writes only into its own temporary workspace, and only packaged files and fixed examples. It never touches a user's workspace, profiles or credentials.
6. Preparation hooks fill one tab's inputs from a topic. They never move data between tabs.
7. The tutorial check prepares every topic and drives its main action to its expected state in the headless browser. It is part of the gate.
8. Walking the tutorial is optional for the owner. No acknowledgement, pass or completion is recorded.

Keep tutorial examples working in the installed application with packaged stand-ins and fixed
examples, isolated from user data/credentials. Prepare inputs on entry/reset, not continuously.
Clean up partial starts, Exit, abandonment and parent shutdown. Keep regressions/edge cases in
tests, not the learning tour. Do not add acknowledgement gates, pass counts or completion records.

## Tests

At each build checkpoint, run `python -m pytest -q -m "not live"` with no failures, errors or
skips. Offline tests must run unattended. Report unavailable checks as blocked, never passed;
do not pass a gate by skipping, weakening or deleting tests. The release criterion is the CI
matrix on Linux/macOS and Python 3.10/3.13; report the actual locally tested OS/interpreter and
anything unverified. Required release tooling checks and smoke remain separate from pytest.

- **T1.** Use tests sparingly. A new test needs a functional reason, stated in its pull request.
- **T2.** Never test for the contents of files in the repository: no assertions on page markup,
  branding, docs, README, NEWS, `.clinerules`, wheel members, metadata or help text. Message wording
  is not asserted either; machine-readable fields (`code`, `outcome`, the `<Class>:` prefix of an
  error) are. *Clarified:* tests may assert the contents of files that AgentWiz's own code creates
  when it runs, in the test's temporary folders (workspace files, the lock file). *Exception for
  release tooling:* `scripts/build_release.py` checks the NEWS section, the wheel's members and its
  metadata before publishing (RC1–RC3); these are release checks, not tests.
- **T3.** Test functionality only: drive AgentWiz through HTTP, the command line, the public
  functions of `model_call`, `state`, `workspace`, `profiles`, `jsonio`, `attachments`, `insertion`,
  `mcp_client` and `tool_forms`, traffic at a fake backend or fake MCP server, or captured output.

- Keep offline tests deterministic and minimal: no language model, hosted API or real MCP server;
  use loopback scripted fakes/stubs. Force orderings with hooks, barriers/events and hold/release,
  never sleeps. Assert each behavior once; lower limits rather than creating huge payloads.
- Tests write only under `tmp_path`, including workspaces, defaults/MCP libraries and CLI working
  directories. Keep strict event/response parsing, TLS verification with the public test fixture,
  restart/cleanup ownership and bounded waits. Test infrastructure details are in the reference.
- Page tests use `data-test` and behavioral state/value assertions, never wording/layout or script
  internals. Use Playwright conditions, not sleeps. Do not add a page test for each HTTP case.
- Objective checks, including visible browser behavior, belong in automation, using existing
  coverage or focused functional gap tests under T1–T3. Do not assign deterministic regression
  checks to the owner. Subjective review is optional and supported inside the UI, not by manual
  setup runbooks. Do not require screenshots, evidence dossiers or tutorial completion.
- For live-check variables and commands, see [Live pytest quick starts](#live-pytest-quick-starts).
  Live tests are optional and never part of the offline gate. Deselect with `-m "not live"`;
  clearing `AGENTWIZ_LIVE_*` alone does not exclude OpenRouter when its API key is set.

## Documentation and release preparation

Follow the placement table above and update owning documents. Before merging shipped changes
to `main`, use a version newer than the published version they change and a corresponding
NEWS section. Keep using that version and section for further edits while it remains unreleased.
Shipped inputs are `agentwiz/**`, `pyproject.toml`, `README.md`, `LICENSE` and
`config/model-servers.ini.template`. The template ships under `agentwiz/` in the wheel.
The current automated guard omits the template source, so apply the rule to template-only
changes during review. Non-shipped tests, scripts, workflows and maintenance docs do not
by themselves require a release. Follow [release operations](docs/releases.md) when authorized.

NEWS contains user-visible changes only, newest release first, one change per bullet and at most
20 words per whole bullet, including continuation lines. Never edit released sections. Keep internal maintenance changes out of NEWS.
Keep check reports factual and task-specific; do not copy historical passes into current
validation claims. Nothing in this guide, `docs/`, `.clinerules`, `tests/`, `scripts/` or `.github/`
ships in the release wheel.

## Live pytest quick starts

Run these Bash blocks from the repository root after developer setup. Each activates `.venv`
and runs only that backend's basic live check, not MCP, Stop or checked-request tests.
Each block supplies default environment settings with `${VAR:-default}`: an existing non-empty
value takes precedence. The subshell keeps activation and variables set here out of the parent
shell. Local servers must already be running with Qwen3.8-27B loaded; these commands do not download or start a model.

**LM Studio — Qwen3.8-27B**

Use the exact model ID exposed by LM Studio's `/v1/models`; change the example ID if your
loaded Qwen3.8-27B has a different identifier. If server authentication is on, uncomment the
hidden token prompt (or export `AGENTWIZ_LIVE_LMSTUDIO_KEY` beforehand).

```bash
(
  . .venv/bin/activate || exit
  export AGENTWIZ_LIVE_LMSTUDIO_URL="${AGENTWIZ_LIVE_LMSTUDIO_URL:-http://localhost:1234/v1}"
  export AGENTWIZ_LIVE_LMSTUDIO_MODEL="${AGENTWIZ_LIVE_LMSTUDIO_MODEL:-qwen3.8-27b}"
  export AGENTWIZ_LIVE_TIMEOUT="${AGENTWIZ_LIVE_TIMEOUT:-600}"
  # read -r -s -p 'LM Studio token: ' AGENTWIZ_LIVE_LMSTUDIO_KEY || exit; printf '\n'
  # export AGENTWIZ_LIVE_LMSTUDIO_KEY
  python -m pytest -m live -s tests/test_live.py::test_live_lmstudio
)
```

**OpenRouter — Qwen3.8-27B**

This uses OpenRouter's model slug `qwen/qwen3.8-27b` and sends a real, potentially billable
request. The hidden prompt is used only if `OPENROUTER_API_KEY` is not already set; never paste
a real key into this guide or a shell command. The basic check prints the available provider
slugs and pins the first by default, with fallbacks off. If account settings exclude it,
uncomment the provider override and replace its value with an allowed slug from that output.

```bash
(
  . .venv/bin/activate || exit
  if [ -z "${OPENROUTER_API_KEY:-}" ]; then
    read -r -s -p 'OpenRouter API key: ' OPENROUTER_API_KEY || exit
    printf '\n'
  fi
  export OPENROUTER_API_KEY
  export AGENTWIZ_LIVE_OPENROUTER_MODEL="${AGENTWIZ_LIVE_OPENROUTER_MODEL:-qwen/qwen3.8-27b}"
  export AGENTWIZ_LIVE_TIMEOUT="${AGENTWIZ_LIVE_TIMEOUT:-600}"
  # export AGENTWIZ_LIVE_OPENROUTER_PROVIDER='provider-slug-from-test-output'
  python -m pytest -m live -s tests/test_live.py::test_live_openrouter
)
```

**llama-server — Qwen3.8-27B**

Use the model ID or alias exposed by your running server's `/v1/models`; change the example
ID to match its Qwen3.8-27B deployment. This live check has no llama-server API-key variable,
so use a local endpoint that does not require authentication.

```bash
(
  . .venv/bin/activate || exit
  export AGENTWIZ_LIVE_LLAMASERVER_URL="${AGENTWIZ_LIVE_LLAMASERVER_URL:-http://127.0.0.1:8080/v1}"
  export AGENTWIZ_LIVE_LLAMASERVER_MODEL="${AGENTWIZ_LIVE_LLAMASERVER_MODEL:-qwen3.8-27b}"
  export AGENTWIZ_LIVE_TIMEOUT="${AGENTWIZ_LIVE_TIMEOUT:-600}"
  python -m pytest -m live -s tests/test_live.py::test_live_llamaserver
)
```

All three inherit `AGENTWIZ_LIVE_TIMEOUT`, whose default is 600 seconds per read, not a
whole-test deadline. For a model that takes longer to load or reason before its first visible
token, add `export AGENTWIZ_LIVE_TIMEOUT=1200` inside the chosen block before the pytest command.
A skip because required variables are missing is not a successful live check. These optional
checks do not replace the offline gate. Additional Stop and checked-request checks remain
separate for all three backends, as described below.

### Additional live checks and variables

**Live checks** (`tests/test_live.py`) are optional: a quick end-to-end run against a backend you
have running. They skip unless their variables are set (CI sets none), run against a temporary
workspace, and print one summary line.

| Backend | Variables |
| --- | --- |
| LM Studio | `AGENTWIZ_LIVE_LMSTUDIO_URL` (e.g. `http://localhost:1234/v1`), `AGENTWIZ_LIVE_LMSTUDIO_MODEL`; optional `AGENTWIZ_LIVE_LMSTUDIO_KEY` when LM Studio's authentication is on |
| OpenRouter | `OPENROUTER_API_KEY`, `AGENTWIZ_LIVE_OPENROUTER_MODEL` (`author/slug`); optional `AGENTWIZ_LIVE_OPENROUTER_PROVIDER` (slug to pin; default the first listed) |
| llama-server | `AGENTWIZ_LIVE_LLAMASERVER_URL` (e.g. `http://127.0.0.1:8080/v1`), `AGENTWIZ_LIVE_LLAMASERVER_MODEL` |

Additional checks:

- **OpenRouter:** `test_live_stop_openrouter` sends a long padded prompt, then Stop after
  `started`; judgment uses `tests/live_stop.py`, itself tested offline.
  `test_live_checked_request_openrouter` uses a JSON contract with up to two repairs.
  LM Studio and llama-server use `test_live_stop_local` and `test_live_checked_request_local`,
  each parametrized over both backends. Set both backends' URL/model variables to run both cases,
  or select one with `-k lmstudio` or `-k llamaserver`. LM Studio's key is optional.
- **MCP:** `test_live_mcp` requires `AGENTWIZ_LIVE_MCP_URL`, `AGENTWIZ_LIVE_MCP_TOOL` and
  `AGENTWIZ_LIVE_MCP_ARGUMENTS` (JSON text); `AGENTWIZ_LIVE_MCP_TOKEN` is optional.
  With web-retrieval-mcp running, an example is `http://127.0.0.1:8765/mcp`, `search_web`,
  `{"query":"model context protocol"}`.

The model checks send task settings (`temperature`, `max_tokens`). All accept
`AGENTWIZ_LIVE_TIMEOUT` (seconds per read from AgentWiz, default 600: AgentWiz sends nothing until
the model's first visible token, so a loading or silently reasoning model can take minutes). The
live checks are optional checks: they never run in a checkpoint gate, which deselects them with
`-m "not live"` (unsetting `AGENTWIZ_LIVE_*` alone would not be enough: the OpenRouter check reads
`OPENROUTER_API_KEY`).

The basic backend blocks above do not run MCP, Stop or checked-request checks. To select a
specific additional live check, use `python -m pytest -m live -s tests/test_live.py::<test_name>`
with the required variables set. Never record a missing-variable or blocked-variant skip as a pass.
