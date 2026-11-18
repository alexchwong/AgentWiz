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
- Use modelSpell for explicit bounded provider selection; do not add custom model-traffic transport,
  retries, fallbacks, a second client or a thread that
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
  hold locks during checks, model/tool calls, Python execution, page/history writes or shutdown waits.
- Keep non-blocking bounded admission and all resource limits. Abandoned workers and retained
  cleanup owners keep their slots until they exit. Validate block inputs before admission.
- Commit/finalize each attempt, operation and block run exactly once; committed records are
  immutable. Delivery is an observer, not run ownership. Keep save-after-final handling,
  attempt-correlated asynchronous updates, cancellation publication/dispatch and repair ordering.
- Run Python steps only through scriptSpell's public `run`; keep the four-run budget and the
  restart-required latch when cleanup cannot be confirmed. Restricted Python is not an OS sandbox;
  preserve that warning. Agentic Chat decides capabilities, validation, approval and labels in
  AgentWiz, never from model output.
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
- For live-check variables and commands, see
  [live checks and evaluation quick starts](#live-checks-and-evaluation-quick-starts).
  Live tests are optional and never part of the offline gate; deselect them with `-m "not live"`.

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

## Live checks and evaluation quick starts

The live pytest checks are optional. The [release procedures](docs/releases.md) define owner sign-off
for the library and Chat evaluations. Hosted offline CI exercises those runners against a scripted
backend and deselects live pytest checks with `-m "not live"`.
Run the Bash blocks from the repository root after developer setup. The basic checks use subshells;
the evaluation quickstarts keep settings in the terminal for subsequent commands. Where a command
uses `${VAR:-default}`, an existing non-empty value takes precedence. Start the local server and load
the selected model before running an LM Studio check. Each backend's basic
check makes one request; MCP and the Stop and checked-request tests are separate. Each check skips
unless its variables are set (CI sets none), runs against a temporary workspace and prints one summary
line; a skip because required variables are missing is not a successful check, so never record one as
a pass.

The two runners select the model differently:

| Runner | Model comes from |
| --- | --- |
| pytest live checks (`tests/test_live.py`) | the backend's `AGENTWIZ_LIVE_<BACKEND>_MODEL` |
| `scripts/library_eval.py`, `scripts/agent_eval.py` (disposable mode) | checked-in evaluation profiles, overridden by the same backend live variables |
| `scripts/agent_eval.py --url ...` | the attached server's selected Chat model; `--route` asserts the backend |

All checks accept `AGENTWIZ_LIVE_TIMEOUT`: seconds per read from AgentWiz, default 600, not a
whole-test deadline. AgentWiz sends nothing until the model's first visible token, so a loading or
silently reasoning model can take minutes; for a slower model add
`export AGENTWIZ_LIVE_TIMEOUT=1200` inside the chosen block before its command. The model checks send
task settings (`temperature`, `max_tokens`).

### LM Studio — Qwen3.8-27B

- `AGENTWIZ_LIVE_LMSTUDIO_URL` — the server's address, e.g. `http://localhost:1234/v1`.
- `AGENTWIZ_LIVE_LMSTUDIO_MODEL` — the model selector. Use the exact ID exposed by LM Studio's
  `/v1/models`, changing the example if your loaded Qwen3.8-27B has a different identifier.
- `AGENTWIZ_LIVE_LMSTUDIO_KEY` — optional, when LM Studio's authentication is on. Uncomment the
  hidden token prompt below, or export it beforehand.

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

The evaluator supplies `lmstudio-eval`; no UI profile setup is required. Use the same live variables above:

```bash
. .venv/bin/activate
python scripts/library_eval.py --route lmstudio --profile lmstudio-eval --trials 3 --set all --report reports/library-lmstudio.json
```

### OpenRouter — complete live-run quickstart

**OpenRouter alone can clear the shipped 0.2.2 library and Chat live evaluations.** No LM Studio,
local model, running AgentWiz UI or manually saved profile is required. Both scripts create and
clean up their own disposable workspace. Run from the repository root in Bash on Linux, macOS
or WSL2 after `scripts/dev_setup.sh`.

**1. Set up the current terminal once.** Run this in the same terminal used for the commands below;
it deliberately does not use a subshell, so the exports remain available. The hidden prompt keeps
the key out of shell history. Requests are real and billable.

```bash
. .venv/bin/activate
if [ -z "${OPENROUTER_API_KEY:-}" ]; then
  read -r -s -p 'OpenRouter API key: ' OPENROUTER_API_KEY
  printf '\n'
fi
export OPENROUTER_API_KEY
export AGENTWIZ_LIVE_OPENROUTER_MODEL="${AGENTWIZ_LIVE_OPENROUTER_MODEL:-qwen/qwen3.8-27b}"
export AGENTWIZ_LIVE_TIMEOUT="${AGENTWIZ_LIVE_TIMEOUT:-600}"
```

`OPENROUTER_API_KEY` is required. `_MODEL` is an `author/slug` model ID. Timeout is seconds per
read, not a whole-suite deadline. An existing non-empty environment value takes precedence.
By default `openrouter-eval` uses production Automatic Balanced selection, including up to four
potentially billable probes. Optionally pin a known tool-capable provider, with no fallback:

```bash
# Optional: replace the example with your chosen provider slug.
# export AGENTWIZ_LIVE_OPENROUTER_PROVIDER='near-ai/fp8'
# To return to Automatic selection:
# unset AGENTWIZ_LIVE_OPENROUTER_PROVIDER
```

Do not set `AGENTWIZ_LIVE_OPENROUTER_KEY` or `_URL`; use `OPENROUTER_API_KEY`. Do not combine backend
target variables with `--profiles-dir` or `--config`; those are explicit external-configuration modes.

**2. Run full library qualification.** This runs three trials of all ten cases for every shipped
model block. With the current catalog that is 390 scheduled executions, plus permitted repairs
and provider probes. The Local only `deidentify_text` block remains deferred.

```bash
python scripts/library_eval.py --route openrouter --profile openrouter-eval --trials 3 --set all --report reports/library-openrouter.json
```

For prompt development first, use `--trials 1 --set dev` and a separate report filename. To rerun
one changed block, add `--block summarise_document` (repeat `--block` to select several) and choose
a new filename. Separate reports are not automatically merged. A failed block still needs a fresh,
complete qualification after changes; rerunning only a lucky case does not qualify it.

**3. Review rubric outputs.** In the full JSON report, inspect each `rubric` and `review_outputs`,
then set its `owner_mark` to `true` or `false`. Refresh scores without any model calls:

```bash
python scripts/library_eval.py --review reports/library-openrouter.json
```

For a smaller owner-review file, export the rubric cases, grouped with all three trial outputs:

```bash
python scripts/library_eval.py --export-review reports/library-openrouter.json --report reports/library-rubric-review.json
```

Review every displayed trial. Set each case's `owner_mark` to `true` only if all its trial outputs
satisfy the rubric, or use individual trial `owner_mark` values for mixed results. Leave null to
preserve existing marks. The current full catalog has 18 grouped cases covering 54 trial outputs;
export and import make no model calls. Edit only those mark fields, then apply once:

```bash
python scripts/library_eval.py --apply-review reports/library-rubric-review.json --source-report reports/library-openrouter.json
```

Import validates the source hash and every evidence field, applies only explicit marks, and refreshes
qualification. It cannot approve missing outputs or bypass numerical/critical checks. A nonzero exit
can still mean marks were applied successfully while another block remains failed or pending. Export
a fresh file if the source changed or if you want to continue reviewing after a partial import.

**4. Run Agentic Chat cohorts and the five-scenario gate.** Use the same terminal and model exports:

```bash
python scripts/agent_eval.py --route openrouter --cohorts tests/agent_eval/cohorts.json --report reports/chat-openrouter.json
```

The report keeps `cohorts_verdict` and `gate.verdict` separate; overall success requires both.
The Chat support claim is governed specifically by the five-scenario gate. A finished answer alone
does not pass: expected tools must complete in order, arguments must be valid and results used.
The summary gate also checks that the block received the staged source text.

**Diagnose a library evaluation.** Extract blank-attempt outcomes, finish reasons,
token usage and answer/reasoning lengths from an existing full report without model calls:

```bash
python scripts/summarize_library_eval.py reports/library-openrouter.json --repo . --include-excerpts --output reports/library-openrouter-diagnostics.json
```

The summarizer is standard-library-only and also supports reports written before compact attempt
diagnostics were added. It does not change the original report or grant qualification. Share the
small diagnostics file and keep fixtures from the evaluated candidate when assessing coverage.

After a block changes, run its full qualification in a fresh report. This example schedules 30
executions for one block and uses the OpenRouter exports from step 1:

```bash
python scripts/library_eval.py --route openrouter --profile openrouter-eval --block summarise_document --trials 3 --set all --report reports/library-openrouter-summary-retest.json
```

`--case-delay 2` spaces case starts to reduce bursts; it does not retry requests, change provider,
clear account quota limits or turn 429 into a pass. The default remains zero delay. If 429 persists,
restore provider availability or account quota before repeating affected runs. Each evaluation uses
fresh disposable installs and reads the current library prompts. Installed blocks in personal
workspaces retain their saved prompts.

Keep the previous full report alongside targeted reports; targeted evidence does not qualify unselected
blocks. The summarizer will list the omitted blocks when assessing a targeted report against the full
catalog. Complete pending rubric reviews separately; `--review` refreshes marks, not prior machine scoring.

**Read reports and exit codes.** Library exits: 0 passed; 1 qualification failed; 2 blocked;
3 incomplete or rubric review pending. Cohort evaluation exits: 0 passed; 1 failed; 2 blocked;
3 incomplete. A nonzero exit does not mean the process crashed. Do not chain these commands with
`&&` if you want Chat to run despite pending library rubric review.

Both runners checkpoint atomically before and after cases. Ctrl-C retains completed evidence and
marks the report incomplete; rerun to a new filename (there is no automatic resume). Each report
records its active case, execution status and diagnostics. Never treat a partial report, a blocked
run, or a percentage excluding unjudged cases as qualification. Reports use synthetic inputs and
redacted model/tool diagnostics; do not add real documents or credentials to fixtures. Existing
report files are replaced by a new run, so use unique names to keep earlier evidence.

**Optional basic integration checks.** These are separate from library/Chat qualification. Select
only the three OpenRouter tests so missing local-backend variables do not create confusing skips:

```bash
python -m pytest -m live -s tests/test_live.py::test_live_openrouter tests/test_live.py::test_live_stop_openrouter tests/test_live.py::test_live_checked_request_openrouter
```

To explicitly exercise a Manual pin, set `AGENTWIZ_LIVE_OPENROUTER_PROVIDER` first and run
`python -m pytest -m live -s tests/test_live.py::test_live_openrouter_manual`.
The checked-request test reports whether the model met its contract; a pytest pass alone is not
library qualification. Stop can report an inconclusive skip when the request finishes too early.
A missing-variable, unsupported-variant or inconclusive skip is not a pass. Provider API/tool-replay diagnostics
belong in modelSpell; these checks exercise AgentWiz integration. When finished, remove the key
from the terminal with `unset OPENROUTER_API_KEY`.

### llama-server — Qwen3.8-27B

- `AGENTWIZ_LIVE_LLAMASERVER_URL` — the server's address, e.g. `http://127.0.0.1:8080/v1`.
- `AGENTWIZ_LIVE_LLAMASERVER_MODEL` — the model selector. Use the ID or alias exposed by your running
  server's `/v1/models`, changing the example to match its Qwen3.8-27B deployment.

This check has no llama-server API-key variable, so use a local endpoint that does not require
authentication. The eval scripts assert only the `openrouter`, `lmstudio` and `openai_compat` routes,
so this guide shows no eval block for it.

```bash
(
  . .venv/bin/activate || exit
  export AGENTWIZ_LIVE_LLAMASERVER_URL="${AGENTWIZ_LIVE_LLAMASERVER_URL:-http://127.0.0.1:8080/v1}"
  export AGENTWIZ_LIVE_LLAMASERVER_MODEL="${AGENTWIZ_LIVE_LLAMASERVER_MODEL:-qwen3.8-27b}"
  export AGENTWIZ_LIVE_TIMEOUT="${AGENTWIZ_LIVE_TIMEOUT:-600}"
  python -m pytest -m live -s tests/test_live.py::test_live_llamaserver
)
```

### MCP

`test_live_mcp` requires `AGENTWIZ_LIVE_MCP_URL`, `AGENTWIZ_LIVE_MCP_TOOL` and
`AGENTWIZ_LIVE_MCP_ARGUMENTS` (JSON text); `AGENTWIZ_LIVE_MCP_TOKEN` is optional. With
web-retrieval-mcp running, an example is `http://127.0.0.1:8765/mcp`, `search_web`,
`{"query":"model context protocol"}`.

### Stop and checked-request checks

- **OpenRouter:** `test_live_stop_openrouter` sends a long padded prompt, then Stop after `started`;
  judgment uses `tests/live_stop.py`, itself tested offline. `test_live_checked_request_openrouter`
  uses a JSON contract with up to two repairs. Both reuse the OpenRouter variables above.
- **Local backends:** `test_live_stop_local` and `test_live_checked_request_local` are each
  parametrized over LM Studio and llama-server. Set both backends' URL/model variables to run both
  cases, or select one with `-k lmstudio` or `-k llamaserver`. LM Studio's key is optional.

Select one check with `python -m pytest -m live -s tests/test_live.py::<test_name>` and its required
variables set. Never record a missing-variable or blocked-variant skip as a pass.

### Evaluation scripts and review

The scripts use the checked-in synthetic fixtures. For a dev-set trial, omit `--trials 3 --set all`.
After setting the rubric entries' `owner_mark` to `true` or `false`, run
`python scripts/library_eval.py --review reports/library-openrouter.json` (or use the LM Studio report path).
The block evaluator exits 0 on a pass, 1 on failure, 2 when blocked and 3 for an incomplete run or
pending rubric review.

Agentic Chat starts its own disposable server, selects the evaluation profile and binds `fast`
automatically. Run one command against the selected route; no UI setup or session token is needed.

```bash
python scripts/agent_eval.py --route openrouter --cohorts tests/agent_eval/cohorts.json --report reports/chat-openrouter.json
python scripts/agent_eval.py --route lmstudio --cohorts tests/agent_eval/cohorts.json --report reports/chat-lmstudio.json
```

Both evaluators copy checked-in profiles into temporary storage and create temporary server settings.
`--profiles-dir` and `--config` are explicit external overrides; do not combine them with backend
live target variables. `OPENROUTER_API_KEY` and the read timeout remain usable. OpenAI-compatible
routes require an explicit profile because the repository supplies no universal model ID.
`--timeout` overrides `AGENTWIZ_LIVE_TIMEOUT` for either evaluator.

For intentional attached-server diagnostics, `agent_eval.py --url 'http://127.0.0.1:PORT/?t=TOKEN'`
retains the existing Chat configuration and requires `fast` to match for cohorts. It installs entries
and creates evaluation data in that workspace: use a disposable one. Do not combine `--url` with
profile/configuration options. See the architecture reference for configuration precedence.

See [release operations](docs/releases.md#owner-sign-off-before-merging-to-main-starter-blocks-addendum-section-7)
for acceptance and [modelSpell developer checks](https://github.com/alexchwong/modelSpell/blob/v0.4.0/DEVEL.md#tests)
for provider API and tool round-trip diagnostics.
