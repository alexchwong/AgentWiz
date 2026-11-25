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

The release builder uses committed `HEAD`. For an uncommitted review, follow the temporary
export procedure in [release operations](docs/releases.md#installed-wheel-smoke). That section
also defines the offline smoke prerequisites.
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
unexpected skips. macOS allows only the Linux-specific PDF worker address-space test to skip.
Offline tests must run unattended. Report unavailable checks as blocked. Do not pass a gate by
skipping, weakening or deleting tests. The release criterion is the CI
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
- For live-check commands, see
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

## 0.2.5 foundations checks

Run from the repository root with the checkout `.venv` activated.

```bash
export OPENROUTER_API_KEY=sk-or-v1-blablabla
python scripts/check_skill_proposal.py --model qwen/qwen3.8-27b --report reports/skill-proposal-openrouter.json
```

Use a Cloud-eligible model with structured output support. The check sends one proposal request
with built-in synthetic text and opens the returned private agent in component review. Automatic
routing also performs discovery and synthetic probes. Require exit 0, `stage: draft_review`,
`verdict: passed` and at least one workflow in the private draft. Retain the JSON with the tested
commit. It records the model, destination, source digest and draft. Use `--model` to select
another eligible model.

```bash
python scripts/check_upgrade_024.py
```

This uses the published 0.2.4 wheel in an isolated baseline environment to create a disposable
workspace, then opens and runs the saved workflow with the candidate loaded by the active
interpreter. For installed-wheel evidence, run it with the candidate wheel's venv activated.
The check verifies output `3` and retention of its Local only label. Use `--baseline-wheel PATH`
if you already have the published wheel.

```bash
python -I scripts/check_pdf_install.py --require-installed
```

Run the PDF check in the candidate wheel's venv on the platform being verified. See the
[release gates](docs/releases.md#agentwiz-025-release-gates) for the full release procedure.

## Private-summary development diagnostics

These optional synthetic diagnostics support future privacy qualification. The 0.2.5 foundations
gate uses the existing offline regressions. The two retained templates are **Unqualified**.

Run from the checkout root after developer setup and activating `.venv`. Set the OpenRouter key
once in the same terminal. Replace the placeholder with your key, and keep it out of reports and Git.

```bash
export OPENROUTER_API_KEY=sk-or-v1-blablabla
```

Start the LM Studio API server with the GPT-OSS-20B 4-bit model loaded. Replace the example model IDs
with the exact IDs exposed by each backend. Use `--base-url` for a custom LM Studio URL
(default `http://127.0.0.1:1234/v1`) and export `AGENTWIZ_LIVE_LMSTUDIO_KEY` if LM Studio requires
authentication. Evaluators create report directories and use disposable workspaces with synthetic inputs.

### Offline checks

```bash
python scripts/privacy_workflow_eval.py --backend offline --set all --report reports/privacy-offline.json
python -m pytest -q -m 'not live'
```

The focused offline rerun covers privacy summaries, the release gate and workflows.

```bash
python -m pytest -q tests/test_private_summary_025.py tests/test_release_gate.py tests/test_workflows.py tests/test_core_v3.py
```

### OpenRouter detector development run

```bash
python scripts/privacy_workflow_eval.py --backend openrouter --model qwen/qwen3.8-27b --report reports/privacy-openrouter.json
```

The default is one trial on the development set. Add `--provider PROVIDER_SLUG` for a manual pin
or `--trials N` for up to ten trials. Requests are billable.

The evaluator writes progress and diagnostics to stderr. Use `--log-level summary` for run totals
only or `--log-level verbose` to see passes and all span decisions. Normal output includes lines
such as `[FAIL] case="dev-context" trial=1 stage="grade" code="severity_below_minimum"`.
The JSON report retains the diagnostic details at every log level. For older v1 reports, use a fresh
report path rather than `--resume`; rerunning is required to obtain the missing evidence.

### LM Studio held-out diagnostics and connected OpenRouter smoke

```bash
python scripts/privacy_workflow_eval.py --backend lmstudio --model gpt-oss-20b --set heldout --qualify --cloud-summary-model qwen/qwen3.8-27b --cloud-summary-provider PROVIDER_SLUG --report reports/privacy-lmstudio.json
```

Replace `PROVIDER_SLUG` with the OpenRouter provider for the connected summary call. The evaluator
creates both profiles. The summary model can differ from the detector used in the OpenRouter
development run. For a shorter live smoke, omit `--set heldout --qualify` and use a fresh report path.
To resume detector cases, add `--resume` with the same report path, build, model, provider and
schedule. Native workflow checks can rerun on resume.

### Collect the evidence

Keep the three JSON reports with the tested build. For LM Studio, also record the model artifact
filename and quantisation, LM Studio version and loaded context length. Share the reports and
those settings for review.

Exit `0` means the evaluated offline lane passed, `1` means a conclusive failure and `2` means
incomplete evidence. For live runs, `detector_verdict` covers the scheduled synthetic detector checks.
Overall live qualification stays incomplete without complete attribution and a measured supported
input range. Inspect `blocked`, `pending`, `connected_development` and `remaining_evidence`.

## 0.2.4 live release evidence

Use the OpenRouter key setup above and start LM Studio with the intended model loaded. For the
webSpell check, start managed webSpell. Run `agentwiz-webspell install` if needed and check it with
`agentwiz-webspell status`. Add `--mcp-url` to the webSpell command if it uses a custom port.

```bash
python scripts/agent_eval.py --route openrouter --model qwen/qwen3.8-27b --cohorts tests/agent_eval/cohorts.json --full-inventory --report reports/chat-cloud.json
python scripts/agent_eval.py --route lmstudio --model qwen3.8-27b --cohorts tests/agent_eval/cohorts.json --full-inventory --report reports/chat-local.json
python scripts/live_check.py --backend openrouter --model qwen/qwen3.8-27b --check webspell --junitxml reports/webspell-live.xml
```

For the 13 shipped model starters, use the [accepted baseline](docs/decisions.md#prior-live-starter-block-evidence)
where the retained report matches the candidate binding. For gaps, run the library evaluator below
with the intended model and routing. Repeat `--block` for several keys. Keep both Chat reports,
the webSpell result and any needed block reports with the candidate commit. See the
[release gates](docs/releases.md#agentwiz-024-release-gates) for acceptance.

## Live checks and evaluation quick starts

### Backend diagnostics

```bash
python scripts/live_check.py --backend openrouter --model qwen/qwen3.8-27b
python scripts/live_check.py --backend lmstudio --model qwen3.8-27b
python scripts/live_check.py --backend llamaserver --model qwen3.8-27b
```

Add `--check stop` for cancellation. For OpenRouter, use `--check manual --provider PROVIDER_SLUG`
for a pinned request or `--check block-repair` for the LV-1 repair diagnostic. A skip supplies no
evidence. Stop can skip if the request finishes too early. For a slow model, use `--timeout 1200`
for the basic diagnostics. This is seconds per read, with a default of 600. The llama-server
diagnostic uses `http://127.0.0.1:8080/v1` by default and expects an unauthenticated endpoint.

Direct pytest invocations still accept the existing `AGENTWIZ_LIVE_*` variables. The standalone
MCP test uses `AGENTWIZ_LIVE_MCP_URL`, `AGENTWIZ_LIVE_MCP_TOOL` and `AGENTWIZ_LIVE_MCP_ARGUMENTS`
(JSON text), with an optional `AGENTWIZ_LIVE_MCP_TOKEN`.

### Library qualification

```bash
python scripts/library_eval.py --route openrouter --model qwen/qwen3.8-27b --trials 3 --set all --report reports/library-openrouter.json
python scripts/library_eval.py --route lmstudio --model qwen3.8-27b --trials 3 --set all --report reports/library-lmstudio.json
```

Use `--block summarise_document` to qualify a single block in a fresh report. For prompt development
omit `--trials 3 --set all` to run one trial on the development set. Full qualification schedules
390 executions across the 13 shipped blocks, plus permitted repairs and provider probes. The
deferred `deidentify_text` starter is outside the 0.2.4 release gate. Automatic Balanced routing
can make up to four billable probes. Add `--provider PROVIDER_SLUG` to pin OpenRouter routing.

For Agentic Chat cohorts without full-inventory coverage, omit `--full-inventory` from the Chat
command above. The report records the cohort and five-scenario gate verdicts separately.
Attached Chat evaluations use `--session-file PATH --route ROUTE` with a disposable workspace
and take their model from the already configured Chat instance.

In disposable mode, command-line target options override the corresponding legacy environment
variables. `--profiles-dir` and `--config` are for explicit configuration and cannot be combined
with live target options or variables. OpenAI-compatible routes require an explicit profile.
See [disposable evaluation configuration](docs/architecture.md#disposable-evaluation-configuration)
for precedence and compatibility details.

### Review and diagnose reports

Set `owner_mark` to `true` or `false` after reviewing the rubric and trial outputs. Refresh scoring
without another model call.

```bash
python scripts/library_eval.py --review reports/library-openrouter.json
```

For a smaller review file, export the grouped rubric cases, edit only `owner_mark` fields, then
apply the marks. Use case-level `true` only if every trial passes. Use individual trial marks
for mixed results or leave null to preserve existing marks. Export a fresh file if the source
changes.

```bash
python scripts/library_eval.py --export-review reports/library-openrouter.json --report reports/library-rubric-review.json
python scripts/library_eval.py --apply-review reports/library-rubric-review.json --source-report reports/library-openrouter.json
```

Extract attempt outcomes, finish reasons and token counts from an existing report.

```bash
python scripts/summarize_library_eval.py reports/library-openrouter.json --repo . --include-excerpts --output reports/library-openrouter-diagnostics.json
```

Use fresh filenames for reruns and retain the full reports alongside targeted results. Library
and Chat checkpoint before and after cases. Exit codes are `0` passed, `1` failed, `2` blocked and
`3` incomplete or awaiting rubric review. Use `--case-delay 2` on the library evaluator to space
case starts without retrying requests.
