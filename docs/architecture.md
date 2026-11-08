# Architecture and maintenance reference

This is the detailed technical reference, not the coding-agent entry point or a user guide.
`../DEVEL.md` owns agent directives and documentation placement. Agents changing an area must
consult its section here and preserve its behavior contracts unless a departure is approved.
Version labels identify when behavior was introduced; later replacement text takes precedence.
Release operations are in `releases.md`; historical decisions are in `decisions.md`.

## Checkout setup reference

`scripts/dev_setup.sh [--python PATH]` is the one developer setup command. It refuses to run outside
a checkout, creates `./.venv` if missing, runs `python -m pip install -e '.[dev]'` and then
`python -m playwright install chromium` (if the browser download fails it says so and how to retry;
the page checks will not run without it); it also converts a `.venv` made by the user installer
(wheel only, no pytest). The user installer
(`scripts/install.sh`) refuses to run in a checkout unless `--dir` is given. `./workspaces/`, `./model_profiles/` and
`./mcp_servers/` are ignored by git in this checkout.

## Checkout services

```bash
agentwiz --no-browser
python scripts/stub_backend.py
python scripts/stub_mcp_server.py
```

## Repository layout

Paths in this table are relative to the repository root.

| Path | Ships in wheel | Purpose |
| --- | --- | --- |
| `agentwiz/__init__.py` | yes | Re-exports `__version__`; no other imports, no side effects. |
| `agentwiz/_version.py` | yes | Only version source: exactly `__version__ = "X.Y.Z"`. |
| `agentwiz/__main__.py` | yes | Command line (`agentwiz`, `python -m agentwiz`); opens the workspace before binding; exit statuses 1, 2, 3. |
| `agentwiz/model_call.py` | yes | Settings and key validation, message building, task arguments (`task_arguments`: route support, `require_parameters`, `check_settings`), the LM Studio model lookup, the call (`call_model(..., cancel=)`, passed to `modelspell.complete`), outcome classification, error descriptions. No HTTP. |
| `agentwiz/checks.py` | yes | **0.1.3.** Contract parsing and validation, answer parsing, every check, results, the repair message. Pure functions that accept any value. `jsonschema` is imported lazily, only here. No HTTP. |
| `agentwiz/contracts.py` | yes | Saved contract containers (`format_version` 2) in `<workspace>/contracts/`: parsing with digest recomputation, re-scanning by fingerprint, exact pin resolution, file tokens, append/no-op/restore/family/delete and safe atomic publication. Takes no lock; `state.py` calls writers under a registered writer and the registry reservation. No HTTP. |
| `agentwiz/contract_schema.py` | yes | The authoritative user-contract recognizer, structural limits and the canonical contract digest. No storage, HTTP or validator. |
| `agentwiz/contract_service.py` | yes | Headless draft projection, targeted edits and sample checks. Never writes. |
| `agentwiz/contract_examples.py` | yes | Five illustrative starter definitions with synthetic valid, invalid and structurally-valid-but-wrong values. |
| `agentwiz/prompts/write_contract.md` | yes | Packaged synthesis prompt (`write_contract`, version 1). |
| `agentwiz/prompts/write_block_description.md` | yes | Packaged Describe/Refine prompt (`write_block_description`, version 1). |
| `agentwiz/workshop_descriptions.py` | yes | Revision descriptions: sidecar records, prose validation, projections, the guarded write helpers and the server-owned Describe/Refine context, schema and patch materialisation. No HTTP, no lock of its own. |
| `agentwiz/runs.py` | yes | **0.1.3.** `CancelToken`; runs; the attempt store: identity, records, end point, commit, summaries, retention, save status, deletion reservation. No HTTP. |
| `agentwiz/model_step.py` | yes | **0.1.3.** `run_attempt`, the headless model-step function, and the hook `before_end_point`. No HTTP. |
| `agentwiz/history.py` | yes | **0.1.3.** The run-history writer (run folders, redaction), listing, reading and deleting saved runs; `remove_tree`. No HTTP. |
| `agentwiz/task_settings.py` | yes | `TaskSettings`, its rules, and `task_settings.json`. No HTTP. |
| `agentwiz/state.py` | yes | Process state: settings, keys, profiles, saved task settings, the MCP library and tokens, contracts, the history switch, the five revisions, state lock, request budget, snapshots, key and token sources, every credential held (for redaction), `key_for(kind, base_url)` for repairs. No HTTP. |
| `agentwiz/workspace.py` | yes | Workspace names, opening and locking (`flock`), reading and validating workspace.json, atomic writes. Called only from `state.py` under the state lock. No HTTP. Not imported by `import agentwiz`. |
| `agentwiz/workspace_admin.py` | yes | Headless workspace administration: bounded listing, staged clone, reset and tombstone delete, with no-follow filesystem safety and limits. Called by `ui/server.py` during workspace lifecycle operations. |
| `agentwiz/profiles.py` | yes | Workspace profile copies and shared defaults (format version 2; reads 1): validation, preservation, atomic writes, fingerprints and permanent short-lived library lock. No HTTP. |
| `agentwiz/jsonio.py` | yes | The one JSON writer (`encode`, `encode_line`); the file writer and strict reader for AgentWiz's own files (`encode_file`, `decode_file`); the exact-number reader and writer (`decode_exact`, `encode_exact(value, indent=None)`, `encode_file_exact`, `decode_file_exact`; `JSONSyntax` and `ExactLimit` are its only errors besides other `ValueError`s) and `encode_record_file` for run files. Standard library only. |
| `agentwiz/attachments.py` | yes | The basic attachment reader (`.txt`, `.md`, `.csv`, `.json`) and `check_document`, implementing `docs/attachment_interface.md`. No HTTP. |
| `agentwiz/insertion.py` | yes | Provenance lines and preview sizes for files and tool results; `REQUEST_LIMIT`. The page now copies `preview.text` instead of inserting it. No HTTP. |
| `agentwiz/mcp_library.py` | yes | The shared MCP server library (`servers.json`, `.servers.lock`): validation, fingerprints, atomic writes. No HTTP server code. |
| `agentwiz/mcp_client.py` | yes | Streamable HTTP MCP client, both eras, standard library only: probe and era cache, discovery entries, legacy sessions and the session lock, gates, reply reading with limits, `classify`. An HTTP *client*; knows nothing of AgentWiz's server. |
| `agentwiz/mcp_ops.py` | yes | Operations: worker threads, one commit per operation, abandonment and socket shutdown, capacity, maintenance (notices, `DELETE`s), retention. No HTTP server code. |
| `agentwiz/tool_forms.py` | yes | Form specifications from tool input schemas. No HTTP. |
| `agentwiz/errors.py` | yes | **0.1.4.** `report_internal` (re-exported by `ui/server.py`) and `ShuttingDown`, the base of every "admission closed" error. |
| `agentwiz/roles.py` | yes | **0.1.4.** `roles.json`: parsing, reading, writing. Called from `state.py` under the state lock. No HTTP. |
| `agentwiz/templates.py` | yes | **0.1.4.** `{{inputs.x.y}}` scanning and filling (text and typed), with provenance. No HTTP. |
| `agentwiz/blocks.py` | yes | **0.1.4.** The block loader, problems, the block store (fingerprints, effective revisions, cache) and the `GET /api/blocks/<name>` view. Reads unmanaged executable v3 blocks and v3 generations selected by managed v2 heads. Workshop publication lives in workshop_assets. No HTTP. |
| `agentwiz/script_step.py` | yes | **0.1.4.** The script step: the single `selectors` supervisor owning the pipes, process-group handling, `Retained` cleanup owners. Hooks `spawn`, `after_spawn`. No HTTP. |
| `agentwiz/executor.py` | yes | **0.1.4.** `execute_step` for model, tool and script steps; token tally; step results. Called only by `runner.py`. No HTTP. |
| `agentwiz/runner.py` | yes | **0.1.4.** `run_block`: input checks, admission, preparation, the executor, the one finaliser, the observer, timers (`make_timer`, `before_run_end`). No HTTP. |
| `agentwiz/tutorial.py`, `agentwiz/tutorial_topics.py` | yes | The tutorial session manager (child UI, seeding, the pending session) and, **0.1.4**, the topic registry the server, page and tests read. |
| `agentwiz/tutorial_support/blocks/` | yes | **0.1.4.** The four example blocks (`extract_fields`, `echo_numbers`, `word_count`, `wait_for_stop`) copied byte for byte into the tutorial's temporary workspace. |
| `agentwiz/output_protocol.py` | yes | Renders named outputs, prompt placeholders and direct-value instructions from captured contracts. |
| `agentwiz/workshop.py`, `agentwiz/workshop_service.py` | yes | Workshop coordinator support, plans, tickets, batches and contract synthesis. |
| `agentwiz/workshop_assets.py` | yes | Managed generations, publication, retention, pins and captured assets. |
| `agentwiz/workshop_editor.py`, `agentwiz/workshop_assist.py` | yes | Draft editing, starters and reviewed assistance. |
| `agentwiz/workshop_license.txt` | yes | License resource included with generated Workshop starter assets. |
| `agentwiz/server_config.py`, `config/model-servers.ini.template` | yes | Loads server configuration. The template ships as `agentwiz/model-servers.ini.template`. |
| `agentwiz/ui/contracts.js` | yes | Shared contract editor and Workshop Contracts interface. |
| `agentwiz/ui/server.py` | yes | The HTTP server; the only module that knows HTTP. `Handler.write_event` is the one writer of stream events. |
| `agentwiz/ui/index.html` | yes | Workshop/Workbench/History, shared Settings dialogs; one framework-free file with no build step. |
| `pyproject.toml`, `README.md`, `LICENSE` | metadata | Packaging, wheel description, MIT license. |
| `tests/` | no | Offline suite (`test_ui.py`, `test_profiles.py`, `test_workspace.py`, `test_jsonio.py`, `test_model_call.py`, `test_task_settings.py`, `test_attachments.py`, `test_mcp_client.py`, `test_mcp_ops.py`, `test_tool_forms.py`, `test_stub_mcp_server.py`; 0.1.3: `test_checks.py`, `test_contracts.py`, `test_runs.py`, `test_model_step.py`, `test_attempts.py`, `test_history.py`, `test_page.py`, `test_live_judgement.py`; 0.1.4: `test_roles.py`, `test_templates.py`, `test_blocks.py`, `test_block_routes.py`, `test_block_tool.py`, `test_scripts.py`, `test_script_cleanup.py`, `test_runner.py`, `test_shutdown.py`, `test_signals.py`, `test_tutorial.py`; 0.2.0: `test_workspace_admin.py`, `test_workspace_lifecycle.py`, `test_named_outputs.py`, `test_contract_overhaul.py`, `test_workshop_descriptions.py`), opt-in live checks (`test_live.py`, with the pure judgement function `live_stop.py`), and the test-only TLS certificate (`tests/fixtures/tls/`). |
| `docs/attachment_interface.md` | no | The attachment interface (companion document, version 3, draft). |
| `scripts/build_release.py` | no | Builds the release wheel from `HEAD`, runs release checks RC1–RC3; `--smoke`; `--no-isolation`; `--modelspell-wheel` (offline smoke); `--changed-since`. |
| `scripts/replay_output.py` | no | Replays captured named-output evidence using the current checker. |
| `scripts/workshop_experiment.py` | no | Prepares Workshop fixtures and compares outcomes through the coordinator. |
| `scripts/smoke_installed.py` | no | Smoke check (S1–S15) against an installed wheel. |
| `scripts/dev_setup.sh` | no | Developer setup: `./.venv` with an editable install, pytest, `jsonschema`, Playwright and Chromium. |
| `scripts/stub_backend.py` | no | Scripted OpenAI-compatible backend for trying the UI without a model; logs the sampling fields and message roles each request carries. Models `json-pass`, `json-repair` and `slow-headers` serve the README tour. |
| `scripts/stub_mcp_server.py` | no | Scripted MCP server (`--era dual|modern|legacy`, `--token`) for trying the MCP tools tab; logs every request, disconnect, notice and `DELETE`. |
| `scripts/install.sh` | no | Installer behind the README one-liner; refuses to run in a checkout without `--dir`; installs required validation dependencies; re-attached to every release as `install.sh`. |
| `DEVEL.md`, `docs/`, `HANDOVER.md`, `NEWS.md`, `.clinerules` | no | Agent directives, technical/release/history references, task handover, changelog and agent workflow. |
| `.github/workflows/` | no | `tests.yml` (push, PR) and `release.yml` (push to `main`, manual dispatch). See [release operations](releases.md#branches-and-releases) for dispatch restrictions. |

## The modelSpell dependency

AgentWiz depends on the modelSpell **v0.3.0 release wheel**, pinned by URL and sha256 in
`pyproject.toml` (D74).
The public API AgentWiz uses includes `CancelHandle` (one per run; `cancel()` never blocks and may
be called from any thread any number of times) and `complete(..., cancel=)`; `Cancelled.sent` is
`False` when no byte of the request was written, `True` when it may have been, and `None` until
modelSpell has handled the exception.
A modelSpell defect found while building AgentWiz is fixed in modelSpell and released as 0.3.1;
AgentWiz takes it as a pin change. Task settings use `supported_settings()` and
`check_settings()`; AgentWiz keeps no support table of its own, and words its own refusal for a
setting a route does not send (modelSpell's suggests `extra`, which AgentWiz does not offer). Adding any dependency, required or optional, needs the owner's approval.

Boundary rules:

- Only modelSpell's public API (`modelspell.__all__` and `__version__`). No `modelspell._…`
  imports and no underscore attributes of modelSpell objects. The key source is re-derived from the
  public `Endpoint.api_key_env` field.
- No transport code for model traffic in AgentWiz, no retries, fallbacks, second client, or thread
  that abandons a running model call.

**Upgrading modelSpell** (new pin and hash) includes re-checking the two error groups that
`model_call.describe_error` relies on against the new source:

1. Errors raised **before any request is sent** (`ConfigError`, `UnsupportedSetting`,
   `UnsupportedOperation`, `AuthError` without a status). Their messages are shown as they are, so
   they must still come only from argument validation.
2. Errors raised **after a request is sent** (`AuthError` 401/403, `BackendError`,
   `ResponseFormatError`, `ConnectError`, `RequestTimeout`). These get fixed descriptions because
   their messages can embed backend text; modelSpell's redaction replaces only the literal key.

A class that starts carrying backend text moves to the fixed-description group. Also re-check the
exceptions modelSpell does not map: a key containing CR/LF raises a bare `ValueError` containing
the key; a key outside Latin-1, or a non-ASCII character in a base URL's path, raises
`UnicodeEncodeError`. Typed keys and base URLs are validated so these cannot arise from the UI;
an environment key still can, and the server's catch-all reports it as `InternalError: <Class>`.

## Fixed model-server configuration

`server_config.py` reads `config/model-servers.ini` relative to the launch directory, or the CLI's
`--config PATH`. It uses INI without interpolation, requiring `lmstudio`, `openrouter` and
`openai_compat` sections. Each has `base_url`; only local-compatible sections may have `api_key`.
Unknown fields, defaults, duplicate entries, unsafe URLs and invalid keys are rejected without
printing configuration values. No OpenRouter key is accepted in the file.

The tracked `config/model-servers.ini.template` is packaged as `agentwiz/model-servers.ini.template`.
Installer and startup initialization publish a complete mode-0600 copy with an atomic no-replace
link, preserving existing files. The configuration is read once per launch, never exported to the
environment, and passed as an immutable mapping to process state and workspace replacements.
The tutorial supplies its own isolated fake-server configuration.

Model settings and profiles select source, model and provider only. Settings, profile and discovery
HTTP routes reject connection overrides. Discovery, role locality and request snapshots resolve the
same configured endpoint. Historical records retain their actual destinations; repair preview and
dispatch reject captured destinations that differ from the current configuration.

`POST /api/openrouter-key` accepts configuration `revision` and write-only `api_key` (empty clears
the session override). State exposes `openrouter_key_source` independently of selected source:
`session`, `env:OPENROUTER_API_KEY` or `none`. The standalone header button has `data-state`
`present|missing`, with missing highlighted. Clearing a session key reveals the environment fallback.
Keys are never returned or persisted; local configuration keys and the OpenRouter environment key
participate in credential redaction. Workspace switch/reset clears session overrides, not fixed
configuration or the inherited environment fallback.

## MCP protocol versions

`mcp_client.py`, `mcp_ops.py` and `tool_forms.py` use the standard library only. Read MCP
messages with `jsonio.decode_exact` and write them with `jsonio.encode_exact` to preserve numbers.
A tool call whose outcome is `unknown` is never resent; only a legacy session-expiry rejection
is resent, once. Tool results and attachments are never inserted or sent without the user's action.

Recorded here as plan R2-7 requires (specification 0.1.2, Sections 3.3 and 9.3).

| Era | Versions | Shape |
| --- | --- | --- |
| Modern | `2026-07-28` | No handshake or session; every request carries `MCP-Protocol-Version`, `Mcp-Method` (and `Mcp-Name` for `tools/call`) and `params._meta` with the version, `clientCapabilities` (`{}`) and `clientInfo`. Closing the response stream is the cancellation. |
| Legacy | offers `2025-11-25`; accepts `2025-11-25`, `2025-06-18`, `2025-03-26` | `initialize` handshake, optional `Mcp-Session-Id`, `ping`, `notifications/cancelled`, `DELETE`. |

Not supported: `2024-11-05` (HTTP+SSE), and any version newer than `2026-07-28` offered without one
of the above.

**Detection.** `server/discover` with modern headers is the probe. A 200 result whose
`supportedVersions` includes `2026-07-28` is modern; one listing only legacy versions, or a 400
`-32022` whose `data.supported` lists one, is legacy; 401/403, redirects, transport failures,
`-32020`/`-32021` and version lists AgentWiz cannot speak are errors with no fallback; anything else
(another 4xx, `-32600` "Unsupported protocol version" or "Missing session ID", `-32601`, a body that
is not JSON-RPC) means legacy, and the client falls back to `initialize`. The era is cached per URL.
**Load tools** always re-probes. A call on a modern-cached server that gets a 400 with `-32600`, no
JSON-RPC error, or `-32022` clears the cache and ends `rejected` (`era_changed`), unsent again.

**SDKs probed on 30 October 2026:** FastMCP 4.1.4 / `mcp` 2.3.0 serve both eras on one endpoint
(routing by `MCP-Protocol-Version`); FastMCP 3.4.11 / `mcp` 1.30.0 serve only the handshake
revisions and answer the probe with 400 `-32600`, which falls back correctly.

**Probing a server by hand:**

```bash
# modern: expect supportedVersions including 2026-07-28
curl -s http://127.0.0.1:8765/mcp -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: server/discover' \
  -d '{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{},"io.modelcontextprotocol/clientInfo":{"name":"curl","version":"0"}}}}'
# legacy: expect protocolVersion in the result (and maybe an Mcp-Session-Id header)
curl -si http://127.0.0.1:8765/mcp -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

## The attachment interface

`attachment_interface.md` is a copy of the project's *AgentWiz attachment interface*
(version 3, draft, `interface_version` 1); the two are kept the same. Until docSpell's build starts
and freezes it (D35), the document and `agentwiz/attachments.py` change together, and
`interface_version` stays 1. `attachments.check_document` runs on every reader result before use,
so docSpell's results meet the same check from 0.1.4.

## UI provenance

`agentwiz/ui/server.py`, `agentwiz/ui/index.html`, the test helpers in `tests/conftest.py` and
`scripts/build_release.py`/`smoke_installed.py` were copied from modelSpell at commit
`1759c6e3c28a637731434a73c46088048583e358` and adapted (each file names its source). Later changes to
modelSpell's copies are ported only by deliberate review, never synced automatically.

## The page: one function per tab

The page is one file, `agentwiz/ui/index.html`, with markup, styles and script and no build step,
no framework. Its script has shared helpers (the API call with the session token, the NDJSON reader,
Copy, the task-panel component, the stale-revision handler, the request controller Chat and Checked
request use) and one section per tab. Independent Workbench utilities keep separate state. Workshop Design/Test/Compare share the explicitly
selected draft, inputs, examples and evidence. Every
server-provided string is inserted with `textContent` or `new Option`, never as markup.

The request controller correlates every asynchronous update with an attempt ID, keeps reading its
stream after `final` (a `save` event can follow), holds a Stop pressed before `started` as pending
and drops it if the request is refused, polls `/api/attempts/<id>` only after a stream is lost or a
save is still pending, and never polls or resends before `started` has arrived.

State attributes, which page checks assert: tab buttons `aria-selected` and `data-state`
(`idle`, `running`); a request status `data-state` (`idle`, `running`, `stopping`, `done`, `polling`,
`unknown`, `stale`) with `data-outcome`, `data-attempt-id`, `data-save`; check results `data-check`;
repair controls `data-repair`; tool status `data-state` and `data-outcome`; the document preview
`data-state`; the contract editor `data-state`; history rows `data-run-id`; Copy buttons `data-copy`
(`copied`, `refused`). `data-test` names are `<tab>-<element>`; tab buttons are `tab-<fragment>` and
panels `panel-<fragment>`. **0.1.4:** checked attempts carry `data-repair-reason`; the Blocks tab's
elements and attributes (`blocks-row` `data-block`/`data-problem`, `blocks-status` `data-state`
`idle|running|stopping|done|polling|stale` with `data-outcome`/`data-run-id`/`data-save`,
`blocks-attempt`, `blocks-trace`, `blocks-operation`, `blocks-output`) and the Roles dialog
(`roles-dialog` `data-state` `idle|saved|error|stale`, `roles-row` `data-role`/`data-unresolved`) are
listed in specification 13.1 and 13.2. A tab's preparation hook (`TABS.<tab>.tutorialPrepare`) fills
that tab's own inputs from a tutorial topic and moves no data between tabs. The Blocks tab never
starts a second load of a block while one is in flight (a keep-less load would discard the texts a
tutorial hook was asked to fill).

**0.2.1 layout (UI-AB v2).** Screens choose pane counts from content-width bands (Wide ≥ 1100 px, Medium
720–1099, Narrow < 720); viewport media queries approximate them, with `body.help-open` variants subtracting the
360 px help panel, because CSS containment would trap the fixed contract drawer. DOM, keyboard and visual order
follow each screen's action order. State attributes added for page checks: Design `ws-identity-bar`,
`ws-unsaved-state` `data-state`, `ws-upgrade-banner`, `ws-missing-reference` `data-ref`, `ws-protection-card`
`data-design` and `ws-protection-labels` `data-unset`, `ws-destination-line` `data-locality|eligible|trusted`;
the Create dialog's `ws-port-labels` `data-state`; port `port-chip` `data-kind` and `port-drawer` `data-state`
`open|closed` (one drawer at a time, anchored below `[data-drawer-anchor]`, reopened after a re-render of the same
block port); Test `ws-readiness` `data-state` `idle|checking|ready|refused` with `data-code`; Compare
`ws-example-pair` `data-current`; Workflow Run `wf-readiness-state` `data-state` `ready|blocked`, `wf-finding`
`data-code|severity`, `wf-run-status` `data-phase`, `wf-progress-node` `data-outcome`, `wf-node-detail`
`data-node`; trust `wf-trust-row` `data-eligible|trusted|used` and `wf-settings-status` `data-state|reason`;
History `history-filter` `aria-pressed`, `history-workflow-row`, `history-comparison-row`; header
`header-save-state` `data-state` `on|off|saving|failed`. A workflow finding's **Open source revision** stores
`window.agentwizWorkflowReturn` (workflow, node, run inputs, selected examples); Design's **Return to workflow**
calls `TABS.workflows.returnTo()`, which reloads the workflow and opens that node's replacement review.

## Page checks

`tests/test_page.py` drives the page in headless Chromium through Playwright for Python, which is
a developer-only dependency pinned in the `dev` extra (1.63.0: each Playwright release expects one
Chromium build, and the build environment provided this one; upgrading it is a deliberate change with
the page checks run again). Nothing under `agentwiz/` imports Playwright and the wheel never carries it.

- **Install.** `scripts/dev_setup.sh` runs `python -m playwright install chromium`. Where
  `PLAYWRIGHT_BROWSERS_PATH` already holds the matching build (the Claude cloud environment's
  `/opt/pw-browsers`), nothing is downloaded. CI runs `playwright install --with-deps chromium` on
  Ubuntu and `playwright install chromium` on macOS.
- **Run.** `python -m pytest tests/test_page.py`. One Chromium per test module, a fresh browser
  context per test with the clipboard permissions granted for the server's origin, and a fresh AgentWiz
  server from `make_ui` with the fake backend and `FakeMCP` of `conftest.py`.
- **Conventions.** Find elements by `data-test`; assert values (field contents, clipboard text, what
  the fakes received) and the state attributes in the page section; wait with Playwright's `expect(...)` conditions
  on those attributes, or on conditions in the page; never sleep, never assert wording or layout.
  Force orderings with the fakes' hold and release, `history.before_write`, `Handler.write_event` and
  the like.
- **Blocked, never skipped.** If Playwright or its Chromium cannot be provided, the page checks fail
  with that reason and the gate is blocked (D51); they are never passed by skipping.
- The in-UI tutorial is a required demo of each feature, checked in the gate (`tests/test_tutorial.py`),
  that the owner may also use for subjective review. Script-verifiable UI behaviour belongs in
  page checks, not in owner assignments; subjective review is described in `releases.md`.

## Runs and records

Every model request is a **run** with one step, `request`; the first send and each repair are numbered
**attempts**. `run_id` is 12 lowercase hex characters (random), the attempt ID `<run_id>-<n>`; routes
check IDs against `[0-9a-f]{12}-[1-9][0-9]{0,5}`. A record's layout is the 0.2 attempt record (0.1.4
and 0.2 add fields and never change these): see specification 0.1.3, Section 8.2. Records hold
plain JSON only (no `Decimal`; a contract travels as `{"name", "revision", "text"}`), never a key.

- **End point and commit.** `model_step.run_attempt` reaches its end point and its commit exactly
  once, whatever is raised: everything between creating the record and the end point sits in one
  `try`, and the end point, the checks and the commit follow it. The outcome is decided by the five
  rules of specification 8.5 (0: `started` failed; 1: `complete()` returned; 2: `Cancelled`; 3: another
  exception with the token cancelled; 4: any other exception). After the commit a record never changes.
- **The token.** `runs.CancelToken` has two halves, publish and dispatch. Stop's acceptance, its
  timestamp and the token's publication are one decision under the attempt store's lock (token lock
  inside it); stop actions run after every lock is released, each exactly once. Lock order: state lock,
  then store lock, then token lock. A Stop is addressed to an attempt: one that has already ended
  answers `already_finished` and cancels nothing, even while a later repair runs.
- **The modelSpell handle.** One `modelspell.CancelHandle` per run, registered with `on_cancel`, and
  passed as `cancel=` to every attempt including repairs. A cancelled handle stays cancelled, so a
  cancelled run takes no more attempts (409 `run_stopped`).
- **Retention.** At most `runs.RECORD_LIMIT = 100` committed records and `runs.RECORD_BYTES =
  268_435_456` bytes of them (by the length of `jsonio.encode(record)`), oldest dropped first;
  `runs.SUMMARY_LIMIT = 1000` summaries of settled attempts; run metadata is kept by lifecycle (an
  attempt running, a save pending, a deletion reserved or any record held) and holds no record
  objects; a monotonic attempt counter is never reset. Tests lower all three constants.
- **Run history.** `history.Writer` writes `runs/<UTC time>-<run_id>/run.json` and `request-<n>.json`,
  each once and redacted (credentials of at least 8 characters, longest first, in every string
  value), mode 0700 folders and 0600 files; it never recreates a folder. The switch is `save_runs` in
  `workspace.json`, read at run creation, with its own revision. `history.LIST_LIMIT = 200` (tests
  lower it). Deletion is a reservation under the store's lock (`deleting`, remembering the prior
  history state), a removal with no lock held through `history.remove_tree`, and a settlement that
  restores the prior state only if the tree is unchanged, else `history_incomplete`; an unknown
  failure counts as changed.
- **Hooks tests use** (all do nothing in shipped code): `model_step.before_end_point()` (a Stop between
  modelSpell's return and the end point), `history.before_write()` (an ordering between a commit and its
  attempt file, or removing the folder), `history.remove_tree()` (replaced to block, or to fail with
  `RemoveError(changed=False)` or after a partial removal with `changed=True`, so deletion cases work
  as root) and `Handler.write_event` (the one writer of stream events; made to raise `OSError` or
  `TimeoutError` or to block). Stop actions may themselves block, to hold dispatch after publication.

## Contracts and checks

### Contract overhaul (definitions, revisions, pins, synthesis and the shared editor)

`contract_schema` is the authoritative recognizer for the user vocabulary and owns the canonical
exact-number digest (`contract_digest`). `checks.parse_contract` and `contract_from_text` always apply user
policy: legacy `checks`, `strict`, inline versions, policy markers and rich schemas are refused with locations.
Source is one exact JSON definition, limited to 1 MiB before decoding. The fixed answer parser accepts whole
JSON or the existing single fenced block; it performs no extraction, coercion or quote repair. Text rules run on
decoded, unnormalized strings (`text.*` diagnostics); JSON value diagnostics redact actual values. Workshop
expectations call `checks.exact_equal` directly.

`checks.parse_internal_contract(obj, validator=None)` builds server-owned rich captures. `validator` is a
key of the hard-coded `checks.POST_VALIDATORS` (only `contract_synthesis_v1`), supplied by server code and
recorded in internal capture metadata; it is never read from a definition, draft or request.
`contract_synthesis_v1` validates the complete user definition the proposal would become
(`contract_schema.synthesis_definition`: subset schema, description and the 1 MiB definition limit) inside
acceptance, so an accepted proposal is always applicable and an oversized one uses the ordinary single repair.

- **Saved containers.** `<workspace>/contracts/<name>.json` holds `format_version` 2, the immutable `name`
  (file stem; casefold collisions refused), mutable `family`, `latest` and oldest-first `revisions`
  (`revision`, `label`, `created_at`, `origin`, `digest`, `contract`, optional minimal `basis`). Revisions
  are contiguous and never rewritten by AgentWiz; each stored digest is recomputed on load and a mismatch
  makes the file unusable (`digest_mismatch`). Version 1 files are listed as `migration_required` and never
  converted. Files over 32 MiB, links and non-regular files are listed as unusable and never written.
- **Operations.** Save creates (expected token null) or appends `latest+1`; a save equal to the latest
  digest is a no-op (`unchanged:true`). Restore appends a copy of an old revision. Family changes rewrite
  grouping only. Delete requires the file token and a complete raw reference scan of current unmanaged
  definitions and every retained managed generation (orphans and run records are not scanned); the scan fails
  closed: a managed head breaking the `head` invariants (shared `head_document`), a missing retained
  generation, a newer/unknown format or any definition whose port collections, declarations or contract members
  cannot all be enumerated is unreadable and refuses with `reference_scan_incomplete`. Used by carries
  `complete: false` in that case, so an empty item list is not evidence of non-use.
- **Tokens and writes.** `file_token` is `sha256:` of the complete file bytes; explicit get/save/delete and
  Checked request resolution read current bytes, while stat fingerprints only drive re-scans. Writers use a
  same-directory temporary file, file fsync, a recheck of the token, atomic replace and parent fsync. A failed
  parent fsync after replacement reports `publication:"durability_uncertain"`; an ambiguous replace is
  settled by rereading, never retried.
- **References.** Executable block definitions are `format_version` 3 (2 is the managed head; 1 is
  `migration_required`). A saved contract is referenced only by an exact `{"name","revision","digest"}`
  pin. All ports resolve against one `ContractStore.snapshot()`. A block's effective revision follows each
  pin's state (existence, usability, digest), never the container's latest or family.
- **Captures.** `model_step.ContractCopy` holds name/revision (null for inline), digest, exact text and the
  parsed contract with its policy. `from_record` verifies the digest and recorded policy and never resolves a
  live file: an old record (neither `digest` nor `policy`) is `migration_required`; a new-format snapshot must
  hold exactly name, revision, digest, text and policy (validator optional) with an inline (both null) or valid
  name/positive-integer revision identity, and anything else, like a changed (for example redacted) text, is
  `snapshot_incomplete`, so it cannot be repaired or re-executed.
- **Managed generations.** Workshop metadata keeps the `contracts` exact-text map for every port and adds
  `contract_pins` (referenced ports only) and `contracts_digest`; `validate_generation` checks them against
  the generation's executable definition. Historical previews read captured definitions through a private
  snapshot resolver; restored drafts keep their references, and the explicit `make_local_copy` edit replaces
  one reference with its captured definition.
- **Synthesis.** `contract_synthesize` is a Workshop service action: a server-built ephemeral internal block
  (role `write_contract`, packaged prompt, one closed wrapper output `proposal`, one repair, 1800 s budget)
  runs through the existing plan/ticket/batch/Stop machinery. Proposals live in the bounded proposal store
  with kind `contract` and a source-draft digest; Save with `proposal_id` derives `synthesized` or
  `derived_from_synthesis` and a minimal server-captured basis. A failed synthesis model attempt exposes
  its existing sanitized error as the batch row's `model_error` (`class`, `status`, `description`, optional
  `hint`); the Contracts pane displays that description without exposing raw backend exception text.

| Contract route (version 2 envelopes) | Behavior |
| --- | --- |
| GET `/api/contracts` | Listing with name/family/latest/latest_digest/problem, families, read_only, validation. |
| GET `/api/contracts/<name>[?revision=N]` | Identity, file_token, the selected numbered revision and exact `definition_text`, revision summaries, Used by. |
| POST `/api/contracts/project`, `/edit`, `/check` | Draft projection, targeted edit, Contract check; never write or dispatch. |
| POST `/api/contracts/save`, `/family`, `/restore`, `/delete` | Persistence with `expected_file_token`; refused in read-only workspaces. |
| POST `/api/contracts/synthesis/plan`, `/start`, `/apply` | Synthesis plan, ticketed start, draft-bound application (no write). |
| POST `/api/contracts/instructions` | `{name, revision, digest}` → the exact direct-value section `output_protocol.single_value_instructions` renders for that pin, resolved like a Checked request send (404/409 on a missing revision or changed digest). Never writes. |
| GET `/assets/contracts.js` | The packaged page component script; an explicit allowlist entry in `server.ASSETS`, session token required (header or same-origin `t` query), `text/javascript; charset=utf-8`. No other path under `/assets` is served. |

Checked requests send `contract:{name,revision,digest}`; a newer appended revision does not invalidate the
reviewed pin.

### The page's contract components (`agentwiz/ui/contracts.js`)

One framework-free script exposing `window.AgentWizContracts.create(deps)`; `deps` passes the page's
`api`, `h`, `copyControl`, `checkBox`, the Workshop review dialog, a `state()` getter and `changed()` (state
refresh). The page loads it from `/assets/contracts.js?t=<token>` and awaits it before selecting the first tab;
a load failure is shown in the banner and ports show that the editor is unavailable. Components:

- `editor` — the one readable/source editor (library, inline ports). It holds the last valid exact definition
  text and the server projection; every change is a targeted `/api/contracts/edit` (or `/project` for a fresh
  definition chosen by Change type or applied source), queued in order. `set()` starts a new epoch and drops
  queued edits against the replaced text; `replaceIf(expected, text)` replaces only if no edit intervened.
  Invalid source stays in the source buffer (`data-state="invalid"`, guided controls inert) and `text()` is
  null, so it can be neither saved nor applied to a port. Domain numbers are never parsed by the browser:
  bounds, rules and definitions travel as strings; fresh node/definition templates contain no numbers.
- `library` — Workshop → Contracts: listing (search, family filter), open/revision view, save review
  (`changes()` diff against the latest projection), stale refusal keeping the draft, Refresh (`replaceIf`
  keeps drafts), family save, restore, duplicate, delete with blockers/unreadable paths, Check sample,
  Used by and synthesis (plan → review dialog → ticket → start → batch poll/Stop → apply bound to the
  draft text the request used → explicit Apply draft; Save sends `proposal_id`).
- `picker` — contract and revision selectors for a concrete `{name, revision, digest}` pin; `latestOption`
  (Checked request) maps **Latest** to the concrete latest at the last read. Refresh never moves an explicit
  numbered selection.
- `port` — Inline | Saved contract for one block port. Changes reach the block draft through the existing
  `design_form` port `source`/`format` (an inline definition, or `format:"shared"` with the pin JSON). Make
  local copy uses the pinned revision when its digest still matches, otherwise the generation's captured
  definition when its digest matches the pin. Save inline as reusable creates a contract and changes the port
  only on explicit choice.

`workshop_editor.field_rows` projects only schemas the central recognizer accepts (no second allowlist);
`contract_view` is `contract_service.project` plus `source_text`. The former Workshop `contract-form` route
and the `property_form`, `contract` and `rename_field` draft edits were removed.

The validation engine details below cover server-owned internal schemas as well as the
shared exact-number machinery. User definitions go through the restricted recognizer first.
In particular, users cannot supply `$schema`, `$ref`, `$dynamicRef` or `pattern`.

- **Validation copy.** `jsonschema` picks its validator class from `$schema`, in `check_schema` and
  while following `$ref`, so extending its `integer` check does not reach every path. AgentWiz
  therefore validates a **copy** of the schema and of the value in which every whole-number decimal is
  a Python integer, with the stock Draft 2020-12 validator and `referencing.Registry()` (empty: nothing
  is ever fetched). The saved contract and every displayed value keep their original form.
- **Decimal context.** Every comparison and validation runs in a fresh `checks.exact_context()`:
  `prec=2100`, `ROUND_HALF_EVEN`, the full exponent range, and `InvalidOperation`, `DivisionByZero`,
  `Overflow`, `Underflow`, `Subnormal` and `Inexact` trapped (not `Rounded` or `Clamped`). A
  `decimal.DecimalException` makes only the check that needed it `not_run`; handlers are tried in the
  order: `DecimalException` (`not_run`), `ValidationError` (issues), anything else (`contract_error`).
  Never fall back to float arithmetic.
- **Validator installation.** `jsonschema>=4.26,<5` is required in every installation and CI job.
  `checks.schema_support()` remains a defensive probe for damaged or unsupported installations.
  Tests never skip schema validation because a dependency is missing.
- **Internal schema dialect.** Every `$schema` on an internal schema, at the root or reached from it, must be
  Draft 2020-12; `$ref` and `$dynamicRef` must start with `#`; a `$schema` key inside literal data
  (`const`, `enum`, `default`, `examples`) is data. An unresolved local target in an internal schema produces `contract_error` when applied.

## Contracts and repair

> **Contracts and repair.**
> 1. A contract decides every answer, first or repaired. Nothing upstream of the checks changes what counts as a pass.
> 2. The fixed JSON answer parser accepts the whole trimmed answer or a single fenced block. A new recovery rule needs replay evidence on saved answers and the owner's approval.
> 3. Generated output instructions include captured descriptions, format, Text rules and complete schemas, rendered by `output_protocol`; blocks also name their envelope outputs. Example data and saved contract names are not attached. The contract alone decides acceptance. (This replaces older rendering decisions; see `docs/decisions.md`.)
> 4. A truncated attempt never provides a block output.
> 5. Repairs reuse the attempt's captured settings. Nothing raises a token cap automatically.

Implementation: `checks.check_answer_value` returns the check result and the parsed value from one
decision; `model_step.run_attempt_with_value` is the only path a block attempt takes, repairs
included, so an accepted value is never re-derived by a second check. `checks.repair_message` has a
truncation branch; `repair_reason` (`null`, `check_failed`, `truncated`) is on every record and in the
repair preview. Only a `complete` attempt whose check `pass`es is accepted.

**Named envelope.** A block model step uses `response_mode: "named_json"`; `checks.check_named_answer` is
its only acceptance path (Workbench attempts keep `check_answer_value`). It parses the answer once through
`envelope_contract()` (one JSON value with duplicate keys refused, using the same answer parser as above),
requires the exact key *set* in any order, requires a JSON string for a text output, then checks each
already-parsed value with `check_value` against that output's own captured contract. It returns one
aggregate result with the bounded, located per-output results under `check["outputs"]`, and a value only when
the aggregate passes (`NO_VALUE` otherwise). `executor._publish` stages every output and commits them
together, so a failed, truncated or stopped attempt publishes none. A repair resends the whole envelope; its
message names failed output paths and rules, never interpolated values.

`model_step.OutputPlan` is the captured plan: each output's name, description and contract snapshot
(name/revision/digest/text/policy). It is built from the block at preparation, stored on the attempt as `output_plan`
(`mode`, `renderer`, `digest`, `outputs`) and rebuilt from the parent *record* for a repair, so a repair is
judged by what the reviewed attempt promised, never by the contracts as they are now. Records without
`output_plan` read as Workbench or historical attempts and are not re-validated; a plan without the snapshot
fields cannot be restored (`migration_required`). `scripts/replay_output.py`
replays a captured plan with the same checker and answers `unsupported_evidence` for a plan with another mode
or renderer, no outputs, a digest that does not match its contracts, or an unreadable contract.

**Generated instructions.** `output_protocol` is pure: no HTTP, storage or transport. `instructions(specs)`
renders the system section (marker lines `BEGIN`/`END`, the key rules, then one block per output) and
`describe(spec)` renders `{{output.NAME}}`, from the same `Spec`, so they cannot disagree. `RENDERER_VERSION`
names the wording and `digest(specs)` hashes the renderer, names, descriptions, canonical contract digests,
policies and the exact rendered requirements; both are
recorded with each attempt and with each reviewed Workshop candidate, and only replay reads the version back, to
refuse a renderer it does not know. The section is composed in
`runner.model_messages` and is never written to `block.json` or `prompts/*.md`. Prompt scanning
(`templates.scan_model_text`, `fill_model_text`) is context-scoped: only model system and user prompts accept
`{{output.NAME}}`; `scan_text`, `fill_text` and `fill_arguments` stay input-only. A filled text is never
rescanned.

## Blocks, roles and scripts

- **Loader.** `blocks.py` turns a folder into a `Block` or a problem (`unreadable`, `invalid`,
  `newer_format`, `unusable`; precedence `invalid` > `unreadable` > `unusable`, `newer_format` alone).
  Every issue is listed (at most 100). Files are read link-free and bounded
  (`blocks.BLOCK_FILE_LIMIT`, `FILE_LIMIT`); only the files `block.json` references are hashed or
  fingerprinted. The store lists the first `LIST_LIMIT` (200) blocks, caches `CACHE_LIMIT` (400), and
  keeps every name's revision in a small table so eviction never changes a revision. A revision comes
  from one process-wide counter and covers the block's own files **and** the state of each exact saved-contract
  pin it holds (an appended revision or family change does not change it). Executable `format_version` 1 is
  `migration_required`.
- **Admission.** `POST /api/blocks/run` checks, in order, body and size, the block at the revision the
  page showed (404 `no_such_block`, 409 `stale_revision`, 409 `block_unusable`), the `inputs` shape,
  then the runner: **inputs are checked before any slot is taken** (BD-03), then non-blocking
  admission (503 `busy` naming the budget), then preparation (role, fills, server, script hash).
- **One finaliser.** `runner._finalise` decides the outcome from explicit input, preparation and step
  results, commits the run, releases slots, delivers `final` (best effort) and saves `result.json`
  once. Delivery is an observer (`BlockDelivery`); no delivery failure affects a run.
- **Cancellation.** One token per run with a reason (first cancellation wins: `user_stop`,
  `budget_exceeded`). Each step kind has one dispatch gate (model: `reserve_attempt`; tool and
  script: the step lock). Cancellation is inspected before repair, exhaustion and the token allowance.
- **Script steps.** `script_step.py`: one `selectors` supervisor owns every pipe; the unbuffered pipe
  objects are the sole close owners of their descriptors (never `os.close` on a recorded number);
  every wait is bounded and every deadline is a `runner.make_timer` timer. A group whose cleanup
  cannot be confirmed is handed, with the process and pipes, to a `Retained` owner that keeps the
  script slot and a writer registration until the group is gone. Scripts need POSIX process groups.
- **Roles.** `roles.json` through `roles.py`, under the state lock, with its own revision; a save
  refuses a profile that does not exist. Resolution is live, at preparation (`state.resolve_role`).
  Version 2 retains the string assignment map and adds `definitions`, keyed by role, each containing
  `description` (at most 500 characters), `location` (`local|cloud`), and optional `family`
  (trimmed nonblank text, at most 64 characters; absent means General in the editor). Version 3 adds
  optional `task_defaults` with only `temperature` and `reasoning`, validated by
  `task_settings.parse_defaults` (zero is kept, a blank reasoning level means none, anything else is refused).
  Formats 1 and 2 are read unchanged; `roles.build_raw` always writes 3, keeps unknown top-level fields and
  retains existing definitions, so an assignment-only save never downgrades the file or drops a default; an older
  AgentWiz refuses the newer file rather than dropping behaviour. Unassigned definitions are
  allowed; every v2 and v3 assignment needs a definition. Missing files expose unconfigured `fast`,
  `assistant`, `reasoning` definitions in memory only. Version-1 assignments remain readable with
  no imposed location constraint until explicitly configured in the editor. Legacy API saves retain
  existing v2 policies for retained assignments; they cannot bypass location enforcement.
  `/api/state.roles` adds `definitions` and `defaults`; `profiles.details` exposes saved settings
  plus computed location, never credentials. `/api/roles/save` accepts optional `definitions`.
  Local requires `lmstudio|openai_compat` and a loopback IP or `localhost`; other endpoints are cloud,
  without DNS resolution. Both save and resolution check policy, including after profile edits.
  Frozen Workshop trial snapshots retain the existing reviewed-configuration contract.
  The editor filters profiles, preserves drafts across model settings, and reports `roles-row`
  `data-status` (`unconfigured|missing|incompatible|no-model|ready`). Saved names are read-only;
  removing a role warns without rewriting blocks. Workshop role selectors link directly to setup.
  The dialog keeps all drafts in memory but mounts at most one `roles-row`. Location and family
  dropdowns filter the role selector; browsing, empty matches and the profile-settings round trip
  retain edits. Save submits all drafts, not just filtered matches. Suggested families are UI hints;
  custom families are stored on role definitions and offered across locations. No empty-family
  registry is persisted. Adding a role inherits active filters; adding defaults never duplicates names.
- **Model step format and settings.** Executable definitions use v3. The model step keys are `id, kind, role,
  system, prompt, response_mode, task, task_resets, repair`. `response_mode` is required and only `"named_json"`
  is accepted; `output` on a model step is refused with a located issue (tool steps keep `output`); 1–50 outputs
  are declared from `outputs` alone. `task_resets` is a bounded list of `temperature` and/or `reasoning`; a name
  may not also appear in `task`, and an explicit `null` temperature or blank reasoning is normalised to a reset.
  The loader refuses executable v1 with `migration_required`. Format 2 identifies Workshop managed heads. `task_settings.resolve_effective(task, resets, role_defaults)` resolves each setting
  independently (step value, then reset, then role default, then backend default; zero is a value) and records
  the source of each in `Effective.sources`, without mutating a role or a `TaskSettings`.
- **One preparation path.** `runner.model_messages` fills the prompts, composes the generated section,
  resolves the effective settings and refuses a request over `insertion.REQUEST_LIMIT`; both dispatch
  (`_prepare_model`) and Workshop preview call it. `state.role_capture(role)` returns the destination snapshot,
  a detached copy of the role's defaults and the roles revision under one lock acquisition, and a started
  Workshop batch freezes it (`FrozenSource`), so editing a role afterwards changes neither the trials nor their
  repairs. `settings_sources` is recorded on each attempt.
- **Workshop projections.** `preview` returns `prepared` (`system`, `generated`, `system_sent`, `prompt`,
  `response_mode`, `task`, `sources`, `role_defaults`, per-output `name/description/format/schema`) or
  `prepared_issues`, `design.role_defaults`, and per-row `output_checks` and a bounded `raw_answer` on trials.
  The reviewed plan descriptor (and so `expected_plan`) covers the source digest, captured contracts, effective
  `settings`, `settings_source`, `role_defaults` and `output_plan` (`mode`, `renderer`, `digest`) per model
  candidate; plan identities are bound to a per-process salt, so they never outlive the process. `design_form`
  accepts `resets` and `customs`: a control marked custom with no value is refused as `block_invalid` at
  `steps[0].task.<name>`, never read as inherit or reset. `check_new_groups` runs when a draft is saved or
  started and refuses a guided group (an object closed to extra properties) with no field that the saved block
  did not already hold empty, naming it as `<side>.<port>.<path>`; preview and editing never refuse, and a
  schema outside the guided subset or a contract held by reference is not judged.
- **Shutdown.** `ProcessState.shutdown(grace_s, wait=)` closes the writer barrier and every admission
  gate, interrupts delivery, cancels admitted work, escalates, and drains the writer count;
  `UIServer.server_close(wait=)` calls it (and the tutorial's halves around it); `__main__.py` owns the
  one signal controller. `UIServer.grace_s` exists so the tutorial's bounded Exit can be lowered in tests.
- **Hooks tests use (all do nothing in shipped code):** `runner.make_timer`, `runner.before_run_end`,
  `script_step.spawn`, `script_step.after_spawn`, `state.before_register_writer`, `state.before_write`,
  and the `BlockDelivery` write boundary (`Handler.write_event`).
- **Constants tests lower:** `blocks.BLOCK_FILE_LIMIT`, `FILE_LIMIT`, `LIST_LIMIT`, `CACHE_LIMIT`;
  `script_step.POLL_S`, `RETAINED_POLL_S`, `OUTPUT_LIMIT`, `STDERR_TAIL`, `KILL_GRACE_S`;
  `runs.RESULT_LIMIT`, `RESULT_BYTES`; `state.SHUTDOWN_GRACE_S` (and `UIServer.grace_s`).
- **New error mappings.** 400 `block_body`; 404 `no_such_block`, `no_such_run`; 409 `stale_revision`,
  `block_unusable` (with the problem), `not_held` (410); 503 `busy` (with `budget` for admission);
  400 `no_such_profile` for a role save naming a missing profile. `RoleUnresolved` and `FillError`
  name the role or the placeholder, never a value; a rejected run records them as located issues.

## Tutorial implementation

Implementation (0.2.0): `agentwiz/tutorial_topics.py` holds `TOPICS`, `public()` and `by_id()`;
`/api/tutorial` returns the topic list and `/api/tutorial/step` takes `{"topic": <id>}` (400 for an
unknown ID, `{"busy": true}` while a model request, a tool call or a block run is running). A session
seeds its temporary workspace with the profile `tutorial-json`, `roles.json` (`fast` to that profile)
and the four example blocks, and creates (or reuses, when its revision 1 is the packaged definition) the
version 2 `fields` contract in family Tutorial, which the example blocks pin by name/revision/digest. Assistance topics select their packaged model
through a topic-local profile in that same temporary workspace. Exit closes the child through
`UIServer.server_close(wait=False)` once; an outstanding cleanup makes the session the manager's one
pending session (state, workspace and `TemporaryDirectory` kept until it drains); `Tutorial.shutdown_all()`
(built from `begin_shutdown()` and `finish()`) drains it at the parent's shutdown. `test_tutorial_topics`
requires a driver for every topic in the registry, so a new topic without one fails the gate.

## Concurrency and resource limits

**0.1.4 lock map (specification 16, which replaces the rows it supersedes).** The permitted nestings
are exactly: C1 state lock → attempt store lock → token lock; C2 step lock → reservation lock → `Ops`
internal lock; C3 step lock → `ProcessState.cleanups` lock, and step lock → writer-barrier lock
(`Writer.inherit`); C4 reservation lock → `Ops` internal lock; C5 operation lock → MCP client locks.
The reverse of a chain never happens; no step lock is held while taking the state, store, token or
operation lock; stop actions decide under the step lock and act after releasing it. A writer
registration is a count, not a lock. Nothing is held during contract checks, model or tool calls,
script execution, page writes, run-history writes or waiting in shutdown. The tutorial manager's lock
is outermost and is never held during a `wait=True` drain. Budgets: 8 model requests, 8 MCP workers
(abandoned ones count until they exit), 4 script processes (owned by the run, or by a cleanup owner).

| Shared thing | Rule |
| --- | --- |
| Settings, keys, profiles, revisions | The state lock (a non-reentrant `threading.Lock`), held to compare revisions, take snapshots, build views, apply changes, and read-validate-change-replace `profiles.json`, `task_settings.json` and `servers.json`; never during a network call (MCP work and the LM Studio model lookup included). A method holding it never calls another public method (views are built by `_view_locked()`). Three revisions: configuration (settings, keys, profiles), task settings, MCP (library and tokens); a change to one never makes another stale. |
| Saved task settings | Changed only by `state.py` through `task_settings.py`, under the state lock, with the task-settings revision; re-read before each change; written atomically. |
| The MCP library, across processes | `mcp_servers/.servers.lock`, a permanent file held with blocking `flock` only around each read-change-replace (lock order: state lock, then library lock); the file re-read under it; changes by other processes noticed by inode, modification time and size, and reloaded with an MCP revision increment before any revision comparison. |
| MCP tokens | In process memory, under the state lock, bound to a URL. A change increments the MCP revision. |
| Era cache and discovery entries | Per URL and per identity (URL plus token) in `mcp_client`, under its own lock, never held during network work. Entries hold header paths and not-offered reasons only; replaced by a complete discovery, removed with the era, on `-32020`, and when the identity stops being current. |
| Legacy sessions | One per identity, with a per-identity lock across (re)initialisation only. Borrowers (workers until they exit; notices, reserved at abandonment before the sockets are shut down) are counted per session. An obsolete or superseded session gets at most one `DELETE`, scheduled when its last borrower releases it or the library changes; at its gate one step checks eligibility and borrowers and marks it ending, so it is never borrowed again. |
| The session lock | One lock in `mcp_client` for current identities, session states, borrowers and each identity's session; never held during network work or while waiting for another lock. Order: state lock, then operation lock, then session lock; the initialisation lock may be held while taking it, never the reverse. |
| MCP workers | At most 8 (`mcp_ops.WORKER_LIMIT`), counting abandoned workers until they exit; a gate before every outbound message (before connecting and after connecting), legacy answers included; each operation's sockets (at most two) registered and withdrawn under the operation's lock, and all shut down on abandonment; `started`/`progress` writes bounded by the page timeout or the time left before the call deadline, never begun with no time left; the final event written once, after the commit, with the page timeout; a failed running write abandons the operation (`deadline` if past it, else `page_closed`); one commit per operation; Stop decides by the abandoned flag; the progress queue holds one event. |
| MCP maintenance | At most 4 threads (`MAINTENANCE_LIMIT`) for legacy cancellation notices and session `DELETE`s, counted until they exit; a 4.5-second network limit with a gate after connecting; at most one notice per operation. |
| Ended operations | The latest 200, each for at most 10 minutes. |
| Request bodies | At most 32 MiB for JSON routes (`insertion.REQUEST_LIMIT`) and 50 MiB for uploads (`ui.server.UPLOAD_LIMIT`), checked from `Content-Length` before reading. |
| MCP messages | At most 32 MiB each (`mcp_client.MESSAGE_LIMIT`), enforced while reading; at most 32 MiB in total per discovery (`DISCOVERY_LIMIT`) and 1,000 tools (`TOOL_LIMIT`), with no partial result kept. |
| LM Studio model lookup | One `list_models()` per LM Studio reasoning "off" request, inside that request's budget slot, 10-second timeout. |
| What a page is showing | Every save, profile operation and completion states the revision its page was showing; a stale one gets 409. An operation that changes nothing writes nothing and keeps the revision. |
| Workspace files within the process | Changed only by `state.py` through `workspace.py`, under the state lock; whole-file atomic replacement (temporary file in the same folder, `fsync`, `os.replace`); each change re-reads and validates the file first, so a file damaged by hand is never overwritten. A file with a problem is never written. |
| The workspace across processes | `.agentwiz.lock`, opened once at launch and held with `flock(LOCK_EX \| LOCK_NB)` for the life of the process; never replaced, renamed or deleted (a new file would have a new inode that a second process could lock); released by the kernel when the process ends for any reason. The descriptor is non-inheritable, so child processes never keep it. A second process exits with status 3. |
| Shared defaults across processes | `model_profiles/.profiles.lock`: a permanent file, opened separately per operation and held with blocking `flock(LOCK_EX)` only across read/change/replace. Lock order is state lock, then defaults lock. Never replaced or deleted. Workspace copies remain protected by their lifetime workspace lock. |
| Lock-file details | pid, host and start time, written in place once at launch; informative only, never used for ownership. |
| Model requests | At most 8 in progress (`RequestBudget`), repairs included, counted from acceptance until the handler returns, including after the page stopped or closed and a stopped request until it ends; the ninth gets 503; nothing is queued. |
| Writes to a page | A 30-second socket timeout on every page connection, set in `Handler.setup()`. One writer per stream (`Handler.write_event`), on the handler's thread; it bounds every event, `final` and `save` included. |
| The attempt store | One lock, held only to create an attempt, reach the end point, commit, apply retention, settle a save status, accept Stop and publish the token's cancellation, answer polling, admit repairs, and reserve and settle deletions. Never held during a model call, a page write, a model-answer check, a run-history write, a folder removal or a stop action. |
| Run cancellation tokens | One per run, with their own lock; `cancel()` idempotent; each stop action runs exactly once, outside every lock. Order: state lock, store lock, token lock; a token's lock is never held while taking another. |
| modelSpell handles | One per run, registered as the token's stop action; `cancel()` never blocks. |
| Attempt numbers | Reserved under the store's lock together with the repair checks, so a step's attempts form one chain. |
| Contracts | Changed only through `contracts.py`, called from `state.py` with a registered writer and the logical contract-registry reservation (a busy flag claimed under C6, which is released before any I/O; a competing registry writer gets 409 `busy`). Block publication takes the same reservation. No C1/C6 lock is held during validation or file work; the shared cache is refreshed briefly under C1 afterwards. Current bytes and token rechecked immediately before replacement. |
| The history switch | `workspace.json` changed under the state lock with the history revision, re-read first, written atomically. |
| Run folders | New files, each written once, without the state or store lock; the writer never recreates a folder; deletion reserved under the store's lock, files removed with no lock held, outcome settled under the store's lock. |
| Records in memory | 100 records and 256 MiB of full records, 1,000 settled summaries plus pending envelopes, run metadata while needed; uncommitted attempts never dropped. |
| Revisions | Configuration, task settings, MCP and history revisions; contracts use numbered revisions plus file tokens. A change to one never makes a page's view of another stale. |

**What frees a budget slot:** the model call completes or raises (including modelSpell's 900 s
per-read timeout); Stop, which cancels the run's modelSpell handle and ends the call at any stage,
normally within milliseconds; after the page has gone, a text or reasoning fragment whose write fails
(usually the first or second after the close, because TCP accepts the first write to a closed peer);
or a write to a page that has stopped reading times out after 30 s.

**What does not:** modelSpell has no overall deadline, so without a Stop a slot stays occupied while a
backend sends only keepalive comments, empty deltas or usage-only chunks, or while it sends bytes more
often than the read timeout. Stop's two exceptions: a host name still being resolved (cancellation
takes effect when resolution returns, before anything is sent) and the LM Studio model lookup (a
discovery call modelSpell does not cancel; it has its own 10-second timeout). Discovery calls and
connection threads are not budgeted.

**Supported workspace locations.** Local disks on Linux and macOS, and WSL2's own filesystem.
`/mnt/c` under WSL2 has not been tested. Network, shared and cloud-synced
folders are unsupported and not detected.


### Workspace copies and shared defaults (owner-approved revision)

`--profiles-dir PATH` defaults to `./model_profiles`, relative to launch directory. Its
`profiles.json` holds shared defaults; workspace copies live at
`<workspaces-dir>/<workspace>/model-profiles/profiles.json`. Only state.py initiates mutations,
under its state lock. `profiles.py` handles profile files and library locking; `workspace.py`
handles workspace.json, its lifetime lock, and the reusable atomic writer. Both libraries use
`{"format_version": 3, "profiles": {...}}` (`profiles.PROFILES_FORMAT_VERSION`; `workspace.json`
stays at 1), read versions 1 and 2 too, preserve unknown fields and never receive session keys. Every
write path drops `base_url`, `reasoning` and `max_tokens` from every profile through
`profiles.write_profiles`. Legacy URLs are migration information only; state warns about differences
from configured endpoints without using them for requests or equality.
The active selection is in workspace.json, never added to a profile library.

New workspaces import all valid defaults once, with no active selection. No migration of old
workspace-root profiles.json is attempted. If defaults are damaged, a new workspace starts with
an empty local library and reports the defaults problem. Restarts never reimport automatically.

`POST /api/profiles/update` takes `revision` and an optional `overwrite` array of names. It adds
missing defaults; differing same-name profiles are replaced only when listed. Identical settings
are left untouched. Replacing the active profile applies its imported settings. The page asks
about each conflict. `POST /api/profiles/save` optionally takes boolean `export_default` and
`overwrite_default`; replacing a different default without approval returns 409 `default_conflict`.
Approval uses the same revision; changes while the prompt is open cause 409 `stale_revision`.
There is no default deletion route. Normal local deletion leaves defaults alone.

State exposes local `profiles.path`, `defaults_path`, `default_names`, `defaults_read_only`,
`conflicts` and `unresolved_active`. Conflicts compare source, model and provider,
not unknown metadata or keys. Only differing copies display `name-(workspace-name)`; stored names
never change. Missing active references use default settings without making files read-only.

State views stat defaults using inode, nanosecond mtime and size, under the library lock; changed
files reload and increment the shared revision. Profile operations re-read defaults under that
lock before checking revision. External edits never automatically change workspace settings.
Invalid defaults recover when the file is repaired; damaged local libraries remain latched until
restart, preserving the previous local-file policy. Defaults problems name the absolute path.
All revision-sensitive settings saves and snapshots refresh defaults too.

Multi-file writes are not a transaction. Local profiles are written first, then workspace selection,
then optional export. A workspace-write failure leaves the successfully written local definitions
visible but does not commit typed settings/keys or selection. An export failure leaves the local
save and active selection in place. Errors identify the failing file; rollback is not promised.
A crash may leave a missing active reference, handled by the unresolved rule. Locks release on
all errors; neither permanent lock file is replaced or removed. Local filesystems only.

Functional coverage uses HTTP for copy-on-creation, explicit conflict choices, export confirmation,
independent restart selections, stale revisions, concurrent export retry, invalid defaults, and
key exclusion. Installed smoke verifies both default directories from a fresh launch. No new
runtime dependencies or release-tool changes are needed. For uncommitted work, transfer changes
into a throwaway clone and commit only there before running build_release.py --smoke, which uses
committed HEAD. Never commit the implementation checkout just to run this check.

## Workspace lifecycle

`ui/server.py` owns the current workspace after launch. This is a deliberate departure from the
earlier "server never opens or closes a workspace" rule: `UIServer.close_workspace()` closes whichever
workspace is current, and `__main__.py` calls it after a drained shutdown. A tutorial child server has
`workspace_admin_enabled == False`, so it cannot switch, reset, clone or delete its isolated workspace.

`workspace_admin.py` is headless and never follows symbolic links. Only names accepted by
`workspace.NAME_RE` directly under the resolved launch root are managed. Listing and actions never
accept an absolute path or `..`. Reset/delete refuse a workspace that contains the shared
`--profiles-dir` or `--mcp-servers-dir`, so a destructive action cannot erase shared configuration.

- **Request gate.** `WorkspaceGate` gives ordinary HTTP requests a shared slot. Switch/reset-current/
  clone-current take the exclusive slot after in-flight requests finish; new ordinary requests get
  503 `busy` during the short admin window. Destination construction happens before the exclusive
  slot, so a failed open does not disturb the current workspace. Active page attempts, block
  attempts/runs, MCP operations, scripts, writers and Workshop batches are refused with 409
  `workspace_busy` and bounded `active` counts; nothing is stopped implicitly.
- **Switch/create.** Open the destination workspace and construct its `ProcessState` first, then take
  the gate, shut down the old state with `wait=True`, close the old lock, and swap `state` and
  `workspace`. Re-entering the current name is idempotent. Create only adopts a folder this attempt
  created; a concurrent collision is `workspace_exists`. A target held by another process is
  `workspace_in_use` with holder fields (pid, host, started).
- **Clone.** Copies `workspace.json`, `model-profiles`, `task_settings.json`, `roles.json`,
  `contracts`, `blocks` (including managed `.workshop` revisions) and `workshop/examples`. Saved
  `runs` and `workshop/comparisons` are copied only with `include_evidence`. Lock files, staging
  folders, unknown top-level entries and symlinks are excluded or refused. The copy is staged under
  `.agentwiz-clone-<random>` and atomically renamed to the target. `CLONE_FILE_LIMIT` and
  `CLONE_BYTE_LIMIT` bound the copy; failures remove staging.
- **Reset.** Removes every workspace entry except the `.agentwiz.lock` inode, writes a fresh
  `workspace.json` (`save_runs: false`, `active_profile: null`) and seeds `model-profiles` from the
  shared defaults (empty on damaged defaults). Resetting the current workspace shuts its state down
  and constructs a fresh one; another workspace is locked and reset on disk. Reset is destructive and
  not transactional.
- **Delete.** The current workspace is refused. A non-current target is locked, renamed to
  `.agentwiz-delete-<random>`, unlocked, then removed without following links. If final removal fails,
  the response reports `cleanup: incomplete` with the tombstone path; the workspace name is already
  gone.

Routes are top-level, not Workshop routes, and all mutations are POST with integer
`format_version: 1`:

| Route | Purpose |
| --- | --- |
| `GET /api/workspaces` | Root, current name, bounded rows, admin/tutorial capability and limits. |
| `GET /api/workspaces/launch?name=...` | Shell-quoted separate-process launch command. |
| `POST /api/workspaces/switch` | `{name}`; idempotent for the current name. |
| `POST /api/workspaces/create` | `{name}`; create and switch. |
| `POST /api/workspaces/clone` | `{source, target, include_evidence}`. |
| `POST /api/workspaces/reset` | `{name, confirm}`; `confirm` must equal `name`. |
| `POST /api/workspaces/delete` | `{name, confirm}`; only a non-current workspace. |

Error codes are `invalid_workspace_name` (400), `confirmation_required` (400), `no_such_workspace`
(404), `workspace_exists`/`workspace_in_use`/`workspace_busy`/`workspace_is_current`/
`tutorial_active`/`workspace_admin_disabled`/`unsafe_entry`/`workspace_too_large`/
`shared_data_inside_workspace` (409), `workspace_io_error` (500) and `workspace_unavailable` (503).
The page hides workspace administration when `/api/state.workspace.admin` is false. A switch or
current reset reloads the page after the Workshop dirty-draft guard; clone/delete/reset-other refresh
the dialog list only. Session API keys and MCP tokens are not transferred: they are memory-only and
must be re-entered after a switch or reset. The page uses the existing `data-test` rules and does not
depend on wording or layout.

Functional coverage: `tests/test_workspace_admin.py` (filesystem safety/limits),
`tests/test_workspace_lifecycle.py` (HTTP lifecycle, lock release, active work, ids),
`tests/test_workspace.py` (CLI release after switch), `tests/test_tutorial.py` (isolation) and
`tests/test_page.py` (dialog workflow).

## Error reporting

The page and the terminal receive only text that cannot contain a credential:

| Exception | Page receives | Terminal |
| --- | --- | --- |
| AgentWiz's own request and settings errors (`SettingsError`, `BodyError`) | 400 `{"error": "<Class>: <message>"}`; messages name fields only | nothing |
| `TaskSettingError` | 400 `{"error": "TaskSettingError: <message>", "code": "task_setting"}`: AgentWiz's own text (naming fields, rules and routes) or a modelSpell pre-send message | nothing |
| `AttachmentError` | 400 `{"error": <reader's message>, "code": <code>}`; the message never contains file content | nothing |
| Upload refusals | 400 `bad_filename` (names the rule, never the name), 411 `length_required`, 413 `too_large` (`size`, `limit`) | nothing |
| MCP failures (`MCPError`) | AgentWiz's description (failure class, HTTP status or JSON-RPC code, server name, era) with `reason`, `http_status`, `jsonrpc_code`; `server_text` (the server's own message, cut at 2,000 characters, labelled "the server says:" in the page) only when no token is held for the operation's identity. A `tool_error` result is tool output and is shown as content. | nothing |
| `BadArguments` | 400 `{"code": "bad_arguments"}`, naming the rule broken, never the text | nothing |
| `ModelSpellError` | `describe_error(exc, base_url)`: 400 on non-streaming routes, an `error` event while streaming | nothing |
| Contract refusals (`contracts.StoreError`) | `{"format_version": 2, "code", "message", "issues"?, "issue_count"?, …}`: 400 `invalid`, `contract_invalid`, `unsupported_keyword`, `reference_invalid`, `too_large`, `migration_required`; 404 `no_such_contract`, `no_such_revision`; 409 `stale_contract` (with current `file_token`), `contract_exists`, `contract_unusable`, `digest_mismatch`, `contract_in_use` (with `blockers`), `reference_scan_incomplete` (with `unreadable`), `read_only`, `busy`, `proposal_stale`; 410 `proposal_expired`; 503 `validation_unavailable`. A file's problem is `{"file", "code", "message"}`. Messages name rules and locations, never values. | nothing |
| Run-file problems | `problems` of `GET /api/runs/<id>`: file, code (`unreadable`, `invalid`), location and rule, never values | nothing |
| New refusals | 404 `no_such_attempt`, `no_such_run`; 409 `run_stopped`, `not_latest`, `not_repairable`, `run_active`, `not_deletable`, `migration_required` / `snapshot_incomplete` (a capture that cannot be restored), `stale_revision` and `read_only` for the history switch; 410 `not_held`; 500 `delete_failed` with AgentWiz's text and the system's error text | nothing |
| Errors inside an attempt | The committed record's `error`: `{"class", "status", "description", "hint"}`, AgentWiz's own text from `describe_error`, never backend text | nothing (an unexpected exception: `[AgentWiz] internal error in /api/complete: <Class>`) |
| `ReadOnly` | 409 `{"error": "profiles are read-only: <file>: <problem>", "code": "read_only"}` (or "the workspace is read-only: …") | nothing |
| `NoSuchProfile` | 404 `{"error": "no such profile", "code": "no_such_profile"}`; profile names are not echoed | nothing |
| `WriteFailed` | 500 `{"error": "could not write <path>: <strerror>", "code": "write_failed"}` | nothing |
| Anything else | 500 `{"error": "InternalError: <Class>", "code": "internal"}`, or an `error` event `InternalError: <Class>` | `[AgentWiz] internal error in <route>: <Class>` and stack locations, never messages or values |

Never add a handler that formats an exception's message unless the exception is AgentWiz's own.
All JSON output goes through `agentwiz/jsonio.py`.

**Workspace problems** (`workspace.Problem`, shown in the terminal at launch, in `/api/state` and in
the page's banner) name the file, a JSON location and the rule broken, and at most an OS `strerror`;
never a value from the file and never an exception's message, so a secret pasted into a file by
hand is not echoed. Profile settings read from a file are validated by `settings_from_body`, whose
messages name fields only. `WorkspaceError` messages at launch are AgentWiz's text plus an OS
`strerror`.

The installer suppresses Python, venv and pip diagnostics, which can include environment-specific
values, and reports its own fixed failure descriptions instead. Successful installs print the
installed versions and shell-quoted launch and activation commands.

## Test infrastructure

Offline fakes are `TestServer` and `FakeMCP` in `tests/conftest.py`, or the checkout MCP stub
(test #54). Ordering hooks include `mcp_client.connect_socket`, `mcp_ops.before_final_write`,
`threading.Barrier` and `threading.Event`; the testing policy is in `../DEVEL.md`.

**Constants tests lower.** Per-server MCP limits (1 second); `mcp_ops.MAINTENANCE_NETWORK_S` and
`CANCEL_ANSWER_S`; the size limits `insertion.REQUEST_LIMIT`, `ui.server.UPLOAD_LIMIT`,
`mcp_client.MESSAGE_LIMIT`, `DISCOVERY_LIMIT` and `TOOL_LIMIT`, so no test builds a large payload. The
shipped values are the modules'; the header-only 413 cases check the shipped request and upload limits.

**The test certificate.** `tests/fixtures/tls/test_cert.pem` and `test_key.pem` are a self-signed
pair (RSA 2048, `CN=AgentWiz test only`, `subjectAltName` `IP:127.0.0.1, DNS:localhost`, `CA:FALSE`,
`serverAuth`, valid for 100 years) used only by the HTTPS cases: the fake server loads it, and the
`test_tls` fixture replaces `mcp_client.make_ssl_context()` with a context trusting only it, so
verification stays on. Nothing runs `openssl` at test time, and no HTTPS case skips. The key is a
public test fixture; a secret scanner may flag it. To regenerate, in `tests/fixtures/tls/`:

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 36500 -subj "/CN=AgentWiz test only" \
  -addext "subjectAltName=IP:127.0.0.1,DNS:localhost" -addext "basicConstraints=critical,CA:FALSE" \
  -addext "keyUsage=critical,digitalSignature,keyEncipherment" -addext "extendedKeyUsage=serverAuth" \
  -keyout test_key.pem -out test_cert.pem
```

**Constants tests lower, 0.1.3.** `runs.RECORD_LIMIT`, `runs.RECORD_BYTES`, `runs.SUMMARY_LIMIT` and
`history.LIST_LIMIT`; the fake backend's `Reply(hold_headers=True)` (read the request, set
`server.reached`, send nothing, not even the status line, until released or the client disconnects,
recorded in `client_closed`; ported from modelSpell 0.3.0's `tests/conftest.py`).

Tests never write outside `tmp_path`: `make_ui` opens a workspace under `tmp_path` for every server
(including the live tests'), and a defaults library and an MCP library under `tmp_path` (`mcp_dir=`
shares one library between two servers); command-line tests launch with their working directory there. "File left
untouched" is checked with `os.stat` (inode, size, modification time). Restarts are simulated
in-process with the `restart` fixture (shut the server down, close its workspace, reopen it, start a
new server).

Orderings are forced with the fake backend's `hold`/`release`/`after_release` and `wait_holding`,
with `threading.Barrier`, and by wrapping `model_call.call_model`; waits are deadlines, never the
thing tested. The helpers parse every response and event strictly, so any `NaN` or `Infinity`
AgentWiz emitted fails the test that saw it.

Loopback startup is tested with `socket.getfqdn` forced to fail: constructing the UI and stub MCP
servers, and serving authenticated UI state, must not require hostname resolution. This guards against macOS resolver delays
without timing assertions. CLI startup failures distinguish timeout, early exit and unexpected output;
the helper kills and reaps the child and joins its reader before closing streams. Diagnostics report
exit status and captured stderr size, not raw child output that might contain credentials.

## Installed tutorial lifecycle

**Implementation (0.2.0).** Topics come from `tutorial_topics.TOPICS` (fourteen:
the five unchanged utility topics annotated 0.1.3, three migrated block topics, one
authoring/testing/comparison topic and five assistance topics annotated 0.2.0);
see `../DEVEL.md`, "UI and tutorial rules". The gate drives every topic's main action in headless
Chromium (`tests/test_tutorial.py`, #112) and checks the sandbox and the bounded Exit (#113).

**Implementation (0.1.3, as carried forward).** `agentwiz/tutorial.py` supervises one disposable child UI per parent,
with separate temporary workspace, defaults and MCP library. `tutorial_support/backend.py` and
`tutorial_support/mcp.py` are packaged stand-ins; the scripts are thin checkout entry points to the
same code. All servers use loopback and dynamic ports. Authenticated `/api/tutorial` and
`/api/tutorial/{start,step,exit}` routes manage the session. The UI controller has explicit
per-tab preparation hooks (a narrow exception to tab independence, not inter-tab data transfer).
Five feature introductions (0.1.3) cover Chat, documents, tools, contracts and repair. There is no full/quick
route split, manual regression walkthrough, protocol-variant or damaged-file scenario, restart
control, acknowledgement record, pass count or release sign-off control. Only the current topic
is retained for the session. The browser heartbeats every minute; 30 minutes without a heartbeat
expires a session, checked every 30 seconds. Parent shutdown and Exit clean up servers and temporary
files; startup failures clean up partially started services.

`python -m pytest tests/test_tutorial.py` checks isolation/cleanup, failed-start cleanup and
preparation of the five learning workflows using actual scripted responses and repair. Functional
reasons: prevent modification of normal work, leaked services and broken auto-fill/actions.
Regression coverage remains in the existing page, profiles, attachment, MCP and contract tests;
no human is assigned their deterministic assertions. No assertions inspect repository file contents.

## Co-developing with modelSpell

To try AgentWiz against a local modelSpell checkout, install it over the pin in your `.venv`:

```bash
python -m pip install -e ../modelSpell
```

Never commit that change. Releases always use the pinned wheel; reinstall with
`python -m pip install -e '.[dev]' --force-reinstall` to return to it.

### Workshop services and interface

`ProcessState.workshop` is the public headless service. `workshop_assets.Assets`
owns publication, source capture, generation pins and the saved loader, `workshop_editor` owns typed
projections and versioned starter renderers, `workshop_assist` owns the generic closed proposal schemas
and compilation, and `workshop_descriptions` owns revision descriptions and the Describe request. HTTP handlers translate requests only. All execution uses
`runner.run_block(..., frozen=FrozenSource(...))`; existing callers retain live
resolution. A frozen source captures the destination, credentials, settings,
contracts, source bytes and save preference once. Script cleanup owners retain the
private capture through the existing Env ownership chain. Managed/captured scripts
use `python -B`. Scripts retain ordinary user-level filesystem/network access.

The framework-free page uses Workshop, Workbench and History, with Design/Test/Compare
sharing one in-memory draft. `#workshop/<key>/<view>` validates against the workspace;
no source content enters navigation state. Dirty replacement offers Save/Discard/Stay.
New-block creation validates the stable key before preview and keeps its dialog open on refusal.
Type-specific bindings use configured role/server choices; tool discovery uses the existing
`/api/mcp/tools` operation without tool dispatch. A generation guard discards discovery replies
after server/type changes. Empty or failed discovery cannot supply a tool binding. Unassigned
starter role definitions permit drafting before configuration, but execution still requires resolution.
The page plans every Workshop Save/Start/Apply and resolves lost Start replies via
its ticket before following the batch. Complete generated source is shown during
review. Existing block clients retain compatibility; the saved-block inspector uses
the ticketed Workshop coordinator while preserving its original trace/Stop hooks.

Design uses three unlabelled responsive columns: description/system/user prompts, input/output tables,
and identity/save/settings/advanced controls. The block selector is a compact row above the editor.
The historical revision selector shares that selection area. Design builds each column explicitly;
the right column orders display name, revision name with Save, model role, paired temperature/reasoning,
model advanced settings and standard description. Model roles preserve missing/unresolved bindings rather
than selecting a replacement. Blank temperature and the reasoning default option inherit; explicit values
override, and advanced bypass controls preserve backend-reset intent through the existing sampling/resets/
customs payload. Role changes refresh default hints without replacing the form. Additional repairs sits
below Outputs. Assist and Revision actions share navigation in every mode; save locks navigation and the
revision selector as well as the editor until publication/reload completes.
Each variable table row retains expandable contract/source controls; structured format selection exposes
its editor immediately. Destructive format replacement requires confirmation. Stable row identity is
separate from the edited name; pending form edits flush before contract operations.
The primary Save lives in Design, not the shared navigation. The block-revision dropdown is rendered by
the common Workshop path (Design, Test and Compare). It opens a clean saved selection through
Save/Discard/Stay, preserving same-block example/input state; the page takes the selected ID from the
preview's `selector`, never from the separately loaded revision list. A clean selection runs through its
explicit `{key, revision}` selector; any edit makes it a draft, labelled with its base revision and
**Unsaved**, which uses the draft path. A clean older selection disables Save until an edit or Revision
actions → Restore to draft (a saved projection loaded into draft mode). The page holds the last saved
projection and its durable ID separately from the mutable draft and clears it for a new block, a
duplicate or a workspace change (page reload). Selected/current block revision IDs are separate from the
example collection's revision. Restore still uses captured contracts and the existing save acknowledgement.
Compare and revision actions share numeric/name display labels; results and prepared previews name the
revision they ran. The Standard description panel, manual editor and Describe/Refine review update only
the description readout: they never mark the executable form dirty, reset inputs or examples, or change
the picker. Late Describe results for a selection that is no longer current are ignored. Model prose is
rendered as DOM text.

Five deterministic proposal models named `workshop-<action>` are packaged in the
isolated tutorial backend. `tutorial_support.workshop` supplies their closed responses; `workshop-describe`
returns the Describe envelope for the saved managed fixture `tutorial_described` (an `extract_fields`
starter published when the Describe topic is prepared), with `write_block_description` bound to the same
local stand-in. Other assistance topics keep their bindings.
The three block topics now prepare Workshop Test; one authoring topic and five
assistance topics bring the registry to fourteen. Their browser drivers run every
main action. Tutorial configuration never touches the owner's workspace or keys.

#### Wire conventions

Workshop POST/PUT envelopes use integer `format_version: 1`. The separate Contract API, including
synthesis entry points, uses v2 envelopes. Editable data numbers
and expected values travel as source strings. A draft is:

```json
{"key":"example","definition_text":"<exact v3 executable JSON>","files":{"scripts/main.py":"<UTF-8 source>"},"metadata":{"format_version":1,"origin":"manual","effects":"unknown","support":[]}}
```

Files may alternatively be `{encoding:"base64",text:"..."}` records for binary
support or lossless exports. Paths are relative, link-free and bounded. No draft
file may be undeclared: the executable's references plus selected support must
account for the complete set. The server constructs assistant-generated filenames.
`definition_text` remains the advanced escape hatch. Preview returns port contract
projections, exact source, facts, descriptions, freshness, setup issues and a filled
prompt/arguments when inputs are supplied. A saved preview (an explicit `{key, revision}`, or a bare
`{key}` of a managed block) is built from the generation's own captured contracts and adds `selector`,
`revision_number`, `revision_name`, `run_issues` (what live validation would refuse) and
`standard_description`. Port contracts are projected by the shared contract service; a
definition outside the user vocabulary is never projected.
Preview also returns `valid` and declared `input_ports`, and projects independently usable contracts
even when another part of the definition is invalid. Save/Start planning rejects invalid drafts with
409 `block_invalid` and bounded `issues` (location/rule), rather than misclassifying them as file
read failures. Design-form output renames update the step binding; input reference errors remain
located validation issues requiring a deliberate correction. Shared-contract choices submit an exact
`{name, revision, digest}` pin (400 `reference_invalid` otherwise), never a name alone or an inline
`format: shared` contract.

Test retains selected-example identity and typed inputs across views. Example additions/edits stay
in page memory with an explicit unsaved state until collection persistence succeeds. Saving a new
or edited block's collection offers the existing block Save review first; cancellation and write
refusals retain pending examples. Dirty replacement/unload protection includes pending examples.
Targeted contract edits operate on the original exact object and preserve opaque
constraints. Reference/shape-changing schemas refuse visual editing.

| Method and path under `/api/workshop` | Behavior |
| --- | --- |
| GET `starters` | Nine starter IDs, versions and two packaged examples each. |
| POST `render-value`, `input-value`, `answer-view` | Exact literal text to typed presentation, or typed input back to exact text; checked answers use the existing checker/parser for display. |
| POST `preview` | Accept draft, starter/key/binding, key/support, or a clean saved `{key, revision}` selection. No dispatch/publication. A model or tool starter may add `data_handling` (`private`, `not_private`, `per_port`): the draft is then v4 with every starter port `local_only` `true`, `false` or unset. Without it the draft stays v3; script and transform starters refuse it. A resolved model or tool design projection adds `destination_facts` `{locality, eligible, reason, trusted}`. |
| GET/PUT `blocks/<key>/retained-labels` | Retained-label review: `pairs` `[{revision, revision_number, revision_name, input, acknowledged}]` for every input a retained v4 revision labels Local only, `restricted_examples` and the record `revision`. PUT takes `expected_revision` and the complete `acknowledged` list of `{revision, input}`; each pair must be currently retained and labelled (`stale` otherwise). |
| POST `plans` | `{action,request}` returns `expected_plan`, effective descriptor and required confirmations. |
| POST `save` | Draft/source, `expected_identity`, expected plan and confirmations publish one revision. |
| GET `blocks/<key>/revisions[/<id>]` | Revision summaries or the saved preview of one retained revision. |
| PUT `blocks/<key>/revisions/<id>/description` | Accept a description proposal or save a manual description for that revision only. |
| GET `blocks/<key>/support` | Bounded root candidates and captured support set. |
| POST `blocks/<key>/support/preview` | Explicit `replace`/`remove` paths update a draft, retaining old revisions. |
| GET/PUT `blocks/<key>/examples` | Collection with revision or save using `expected_revision`. |
| GET `destination-trust-candidates` | One row per exact destination reachable from configuration: each resolved model role and each observed MCP tool, with `destination`, `exact`, `locality`, `eligible`, `reason`, `trusted` and `used_by` (`{kind: role|server, name}`), plus `unused_grants`. No discovery or network access. |
| POST `test` | One source (draft, key, or saved `{key, revision}`) and input-text map start a frozen normal run. |
| POST `batches` | One/two candidates and ordered exact examples start serial independent trials. |
| GET `batches` | Current and latest completed session batch IDs. |
| GET `batches/<id>` | Rows, child run IDs, evidence summaries and denominators; a Describe batch returns its allowlisted projection instead. |
| POST `batches/<id>/stop` | Cancel current child and remaining trials. |
| POST `assist` | One independently requested action using an assigned assistant role, or the server-owned Describe/Refine request. |
| POST `proposals/apply` | Proposal ID, matching draft and expected plan produce draft/examples only. |
| PUT `reviews/<run>` | Result-bound rating and optional durable review, with expected revision. |
| GET `comparisons[/<id>]`, DELETE `comparisons/<id>` | Saved comparison listing/read or independent deletion. |

`test` and `batches` plans add `restriction_sources` for display: `{code}` rows with `declared_label`,
`example_restriction` (`example`, `input`), `retained_label` (`input`, `revision_number`), `context_manual`,
`context_inherited` or `context_clear_pending`. They do not change enforcement. A protected refusal (for example
`destination_untrusted`) carries one issue with `restriction_sources` and, when known, `destination` with
`locality`, `eligible`, `reason` and `trusted`.

Start requests require a server-issued ticket. Plan a request before sending it.
Echo each required `{kind,digest}` acknowledgement, omitting `detail`. Repeat the
same ticket/body after a lost reply, or GET its status. Its operation ID is the batch
ID while active. An accepted duplicate resolves before rechecking source freshness.
No automatic restart/replay is performed. Configuration/context/save changes require
a new plan. Schema observations are evidence, excluded from the action binding.

A source selector uses `key` and optional durable `revision`; drafts use `draft`.
An explicit `{key, revision}` is a clean saved selection: it refuses `draft`, `starter`, `edit`,
`duplicate_key`, `display_name`, `support` and `metadata` (400, located), bypasses `Service.source()`
(no current-head provenance, no authoring mutation) and reads the generation through `Assets.saved`.
`preview` also accepts `duplicate_key`, `display_name`, or an `edit` operation on the draft and key-only paths.
`starter_parameters` regenerates a current version only after an actual edit.
`contract` identifies side/port/path and exact `set` values, or `source_text`.
The retired `description` edit and draft-carried `description_sections` answer 409 `describe_moved`.
Saved previews and Describe read captured contracts through the private snapshot resolver
(`Assets.snapshot`), never granted to a caller-supplied draft. Runs validate the same saved files against
the live registry and the revision's exact pins and never resolve to latest. Restore drafts keep their
original pins; publication resolves them against the live store again. `make_local_copy` (side/port) explicitly replaces one pin with the
generation's captured definition. Nothing is inlined silently.

Examples contain id/version/label, named input source texts, expectation,
origin/verified and optional held_out. An expectation is `whole_output` with a
`values` map, `fields` with path-segment `assertions`, or `review` with a criterion.
Assertions distinguish equals, absent, present and null. Edits invalidate
verification. Batch requests may supply a frozen example list, or
`example_selection:{key,revision,ids}` to bind current saved versions. Candidates
must expose the same interface and use identical frozen examples. The coordinator
alternates baseline/candidate order between examples.

For a review, context contains snapshot_id, example_id, example_version and
result_digest from the completed row. The store reserves the run against deletion
before durable writing. `review-<revision>.json` files are write-once. History reads
expose review versions/current review. Existing attempt/result files remain immutable.
Only complete accepted evidence is reviewable. Redacted replay evidence is refused.

#### Storage, locks and recovery

A v2 `blocks/<key>/block.json` contains only format_version/current/retained.
Executable v3 assets and v1 generation metadata live under
`.workshop/revisions/<128-bit-id>/`. Publication stages on the same filesystem,
validates with the existing loader and atomically replaces the head last. Asset
fingerprints cover length, path and exact bytes. The metadata records source and
contract snapshots, origin, support, starter version/runtime identity and source
acknowledgement. Retain newest 20. Pins protect loaded generations through real
execution/cleanup. Pruning happens after head publication, and a later save/startup
reaps generations whose pins have finished. Unknown/damaged heads are preserved.
Private validation/captures use temporary directories outside the workspace.

Generation metadata additionally records server-owned `revision_number` and optional `revision_name`
(at most 120 characters, no control characters, trimmed at publication). Publication allocates the next
per-block number under the existing logical reservation and writes it with the generation before replacing
the head. Submitted/copied numbers are not authoritative; IDs, head v2 schema and effective freshness
tokens are unchanged. Revision summaries and save responses expose the number and name additively.
Unnumbered retained histories project oldest-first numbers starting at 1 without read-time writes. The
first new generation anchors the baseline so pruning does not renumber survivors. Already-pruned lifetime
history cannot be recovered. The first confirmed Workshop save of a valid unmanaged v3 block captures the original as
revision 1 and the edited block as revision 2. New blocks and duplicates start at 1. Restore appends, never rewrites an old generation. The UI starts a new revision label blank;
Overwrite replaces the selected retained entry with a fresh immutable generation, preserving its number
and list position. It is refused with `new_revision_required` when the draft or the target revision is v4, or
the target has `context_local_only`: replacing such an entry would remove Local-only input labels from
`retained_labels` or a protected-design record without trace. Retired labels use the recorded acknowledgement
instead. The default changes only when its entry is replaced. The expected head identity and
review plan bind the target; old IDs leave retention while pinned readers finish against unchanged files.
Existing evidence retains its original ID and captured contents; replacement descriptions start empty.
Revision names may be changed on overwrite. Restore-as-new still appends. Retention remains 20.

C6 is the one Workshop leaf lock, shared with tickets. It protects reservations,
pins, batch state, proposal references and session reviews. Existing locks may
acquire C6. Code holding C6 must never acquire any other lock, perform I/O, call
state/store methods or callbacks, dispatch, or wait. Logical reservations/pins
survive release of the actual lock. Publication, examples, reviews and coordinators
register with the shutdown writer barrier. Shutdown closes Workshop admission,
marks the batch cancelled, releases C6 and cancels its child through the ordinary
run token. It prevents admission of the next child.

Saved comparisons write a manifest before dispatch. Before each child, write a
specific `dispatch_pending` row durably, then invoke the normal runner. Marker
failure prevents dispatch and ends the batch. Terminal evidence is collected before
advancing, so attempt-cache eviction cannot erase earlier metrics. Unresolved saved
markers read as interrupted/outcome unknown after restart and are never resent.
DELETE returns 409 while the coordinator owns any future write, including its final
write. Saved child runs have their independent deletion lifecycle. Run deletion
never recreates evidence through a comparison.

Support preview accepts the current draft to preserve unsaved entry/support edits.
Root replacements carry draft-only `root_sources` digests, checked under the logical
publication reservation before staging and again before head publication. They are
not persisted as future live dependencies. Duplicate and starter regeneration clear
these working-copy preconditions.

At most one batch is active and the latest completed session batch is retained.
At most 50 examples and two candidates are admitted. Output previews have a 32 MiB
budget and pre-dispatch manifest source snapshots have a 64 MiB budget. Incomplete
previews retain digests/metrics and disable rating. Save choice is frozen at Start.
Saving off and read-only mode use session evidence and private captures. Read-only
mode rejects explicit persistence. Known-credential redaction remains best effort.

MCP observations are exact-schema digests from ordinary successful discovery in
both protocol eras, bounded to 128 identities and TOOL_LIMIT tools each. No extra
discovery fills them. Configuration/credential changes stop a captured tool batch;
schema observation differences are reported conditions and do not enforce a remote
implementation. Model/backend internal conditions remain unknown.

#### Experiments and replay

D72's LM Studio/llama-server Stop and checked-request live checks now use the same
judgement as OpenRouter. They remain optional and require configured owner backends.
`python scripts/replay_output.py --fixture PATH` accepts exported evidence, or use
`--workspace PATH --attempt ID` for saved attempts. An explicit span or replacement
file is compared through the unchanged checker. It reports discarded text, exact
recovered value and both judgements. Redaction/incomplete evidence is unassessable.
Truncation remains an acceptance failure. The tool never sends network requests and
never installs a production recovery rule.

Experiment fixture submission uses scripts/workshop_experiment.py with --task label or --task extract. Offline --print-fixture writes no workspace data. Live submission uses the same ticket, plan and batch APIs. No execution engine or automatic parser promotion is provided by that client.

#### Operating revisions

`Assets.pin(key, revision=None)` is the shared current/historical pin: it validates the ID shape, registers
the pin under the leaf lock, re-reads the head and confirms retention before opening the generation,
rejects symbolic links on the path, and is released in `finally`. A no-longer-retained or malformed ID is
404; a retained ID without its folder is a damaged generation (409 `unusable`). Publication's reaper skips
pinned generations, so a reader either holds its pin or sees the ID leave retention. `Assets.saved` copies
every asset listed in the generation's metadata, verifies digests, rechecks retention before returning, and
refuses executable formats older than 3 with 400 `migration_required`. Once Start owns the capture copy, a
later prune cannot change the admitted run.

A saved run candidate binds `block_revision_id` and `block_revision_number` in the plan candidate (so two
byte-identical generations stay distinct), batch rows and `result.workshop`. Its draft carries no head
token, so a plan survives a default-pointer change; Restore and editing keep the optimistic
`base_digest`. Older records omit these additive fields. Shared comparison examples are checked against
each arm's input names: a mismatch is 400 `example_incompatible` naming the example, the revision and the
missing/extra ports.

#### Revision descriptions

One record per retained managed generation lives at `blocks/<key>/.workshop/descriptions/<id>.json`, outside
the immutable generation and every executable asset map. Version 1 is closed:
`{format_version, basis, origin, sections, port_summaries}`. `basis` is the exact JSON digest of
`{key, revision, asset_digest, contracts_digest}` from a verified executable-v3 generation; without both
digests there is no basis and the status is `unavailable`. `origin` is `manual` or `model`. All six
`workshop_editor.SECTIONS` are required and trimmed; `use` is nonblank; each section is at most 600
characters. `port_summaries` holds `inputs` and `outputs` objects keyed only by declared ports, each a
nonblank summary of at most 240 characters; an absent key shows the declared meaning. Prose rejects invalid
Unicode and control characters other than newline and tab. The encoded record is at most 16 KiB.

The projection (`standard_description` in a saved preview, and the PUT result) carries `status`
(`saved`, `legacy` for `workshop.json.description_sections` with no sidecar, `missing`, or `unavailable`
for no basis or an unreadable, linked, unknown-format, mismatched or invalid sidecar), `basis`, `token`
(SHA-256 of the exact sidecar bytes, `null` when absent), prose, executable `fallback` text, `issues` and
`writable`. Unreadable, linked and unknown-format records are never overwritten; a mismatched or invalid
version-1 record may be replaced with its token.

`PUT .../description` takes either `{proposal_id, expected_token}` or `{expected_token, basis, sections,
port_summaries}` (both closed). Under the workspace writer and the per-block reservation shared with
publication it rechecks retention (404), basis and token (409 `stale`), validates prose (400
`description_invalid`, located), refuses known credentials (409 `credential_in_asset`) and writes
atomically. A proposal for revision A never targets revision B (409 `proposal_target`). New publication
strips `description_sections`/`description_basis` and never inherits a sidecar. Publication and startup
recovery remove regular sidecars with recognised IDs for pruned generations after the head commits;
links and foreign names are skipped and a cleanup failure never fails publication.

Describe/Refine reuse `plans` (action `assist`) and `assist` with closed request shapes
(`action:"describe"`, `mode`, `key`, `revision`, and for Refine `proposal_id` and `feedback` of at most
2,000 characters). They are routed before generic capability enumeration and role selection. The server
resolves `write_block_description` (409 `role_unresolved` before any call), reads the revision through the
snapshot resolver and builds an allowlisted context: key, kind, purpose, exact port names, bounded declared
meanings, compact contract facts; for model blocks the role and decoded system/user prompt; for scripts the
main source and starter name; for tools the server, tool and binding structure with literal values replaced.
Each excerpt is at most 4,000 characters and the serialised context at most 16 KiB (optional constraints,
then meanings, then excerpts are shed; otherwise 400 `context_too_large`). Known credentials are redacted;
one remaining anywhere in the outbound context or response schema refuses dispatch with 409
`credential_in_context`. The ephemeral block uses the named-output path with a closed response schema
restricted to the captured ports and sections, a 2,048-token cap and one repair. `saving` is forced off, so
no run record or comparison manifest is written even with Save runs on.

After a complete call, `describe_complete` validates normalised prose, Refine patch semantics (missing keys
keep prior text, an empty string clears an optional section, `null` removes a port summary, an empty patch
needs a note), whole-record bytes and credentials. Failure is the terminal `description_invalid` outcome
with located issues and no proposal; an unchanged Refine is `unchanged` and keeps the prior proposal. A
success stores a `description` proposal (opaque ID, target key/ID/basis, base token, candidate, note,
changed labels) in the bounded proposal cache unless cancellation already won. `description_proposal`
reads only that cache, never run envelopes, batch retention or history, and expires proposals whose
revision is pruned (410). The generic Apply route refuses this kind. Public plans, confirmation errors and
batch reads of a Describe request use `describe_public`/`describe_public_batch`: target, destination with
locality, persistence, progress, outcome, bounded issues and the validated proposal; never context,
feedback, prior prose, raw answers, prepared requests or child run IDs. The full private descriptor still
binds the plan.


### Workflow-ready blocks and destination trust

Executable definitions v3 and v4 use the same contract and runner paths. V4 requires
explicit port locality labels and supports in-process transforms. Workshop draft references are
opaque, workspace-scoped and process-local. Open fresh drafts explicitly, and use
`POST /api/workshop/drafts/import` to open external bytes as a new draft. After restarting,
reopen saved revisions to obtain a current draft reference.

`GET /api/workshop/destination-trust` returns the current revision and non-secret grants.
The corresponding POST takes `format_version: 1`, `expected_revision`, the exact `destination`
and a boolean `trusted`. Model identities contain `kind`, `url`, `model` and `provider`.
MCP identities contain `kind: "mcp"`, `url`, `server` and `tool`.
Trust is persisted in `destination_trust.json`. The runner checks the current grant and
configuration before protected model calls and repairs and before MCP arguments are sent.
`POST destination-trust-preview` also returns `detail` (the ineligibility reason: `hostname`, `remote`,
`address`, `provider` or `proxied`) and `locality` (`local` for a loopback address or `localhost`, else `remote`).
`outbound_policy.ineligibility` reports the reason for display only; dispatch still uses `canonical_url` and
`decide`, so enforcement is unchanged.

Workshop Test restricts an input when any retained v4 revision labelled it Local only
(`retained_labels`). The owner may retire exact `(revision, input)` pairs: the acknowledgement record
`workshop/label_reviews/<key>.json` (`format_version` 1, `revision`, sorted `acknowledged`) is written under the
`label_reviews` reservation with the workspace writer registered. `retained_labels` ignores those pairs only; an
unreadable record acknowledges nothing. Saved-example restrictions are unchanged, so values captured under a label
stay restricted. A later revision labelling the input again creates a new pair. Clearing the list restores the
previous behaviour.


### Headless workflow engine

Workflow bundles live at `workflows/<name>.json`. The file token hashes the complete file.
The executable digest covers the definition and its immutable node snapshots. Layout and examples
can be updated without refreshing snapshots. The planner validates exact captures, orders all
nodes by dependency and lexical tie-break, and calculates locality before resolving destinations.
Jobs use the Workshop slot and the shared block runner. They run selected examples in order.

All paths below are relative to `/api/workshop`. POSTs require `format_version: 1`.

| Route | Request or result |
| --- | --- |
| `GET workflows` and `GET workflows/<name>` | List names or read exact definition text, file token, sources, layout and examples. |
| `GET workflow-sources/<key>` | Obtain the reviewed fingerprint of an unmanaged v4 block. |
| `POST plans` | Send `action` and `request`. Actions are `workflow_save`, `workflow_rebind`, `workflow_delete` and `workflow_run`. |
| `POST workflows/<name>/save` or `.../rebind` | Send the reviewed `request`, `expected_plan` and `confirmations`. The request contains `name`, `file_token`, `definition_text` and optional `selections`, `layout` and `examples`. Select new node sources by `{key, revision}` or `{key, fingerprint}`. |
| `POST workflows/<name>/delete` | Apply a reviewed deletion with `name` and `file_token`. |
| `POST workflows/<name>/run` | Apply a reviewed run using a Workshop ticket. Supply `name` and either exact input texts or example ID/version pairs. |
| `GET workflow-jobs/<id>` and `POST workflow-jobs/<id>/stop` | Poll the job or request Stop. Query `node` for a node trace. Batch trace keys use the zero-based example index followed by a colon and node ID. |
| `POST workflow-jobs/<id>/review` | Submit a judgement bound to the example ID/version and result and executable digests. |
| `GET workflow-mcp-confirmation` and `POST workflow-mcp-confirmation` | Read or change the workspace preference. POST takes `mode` and `expected_revision`. |
| `GET workflow-evidence`, `GET workflow-evidence/<id>`, `POST workflow-evidence/<id>/delete` | Browse or delete saved jobs. Query `record` to read a manifest-listed record. |

Saving creates a private job folder under `runs/workflows/<id>/`. The initial capture is staged,
then each node has a durable intent before dispatch. The manifest indexes node records, model
attempts and the result. A running manifest left by an interrupted process is shown as interrupted.
Saving off keeps the bounded job view in memory.


The Workflows browser view loads the form projection from the server. Contracts and
constants remain exact text in that projection. The editor submits an `editor`
object or existing clients can submit `definition_text`. The server decodes each
exact value separately. The form and canvas share node bindings and layout. The
canvas previews compatible direct connections and uses the same reviewed save
operation as the form. Layout has no effect on dispatch order.



### Restoration, explanations, design-time validation and saved-use trust (core v3)

**Identifier mappings.** `identifier_pairs.parse(value, profile=)` is the one mapping parser. The `scan` profile
keeps the Gate's behaviour (string or `{original, placeholder?}` entries; missing placeholders become the entry
index; duplicates kept). The `restore` profile accepts objects only, requires a placeholder fully matching the
declared syntax (`bracket_upper`: `\[[A-Z][A-Z0-9_]*\]`), collapses identical pairs and refuses one placeholder
mapped to different originals. Both bound 1,000 entries, originals of 1–4,096 and placeholders of at most 256
characters, and raise only `PairsError(index, message)`; the Gate maps it to `scan_invalid` at `gate.scan`, the
transform to `inputs.pairs` or `inputs.pairs[i]`.

**`restore_placeholders` (operation version 1).** Inputs `text` (Text) and `pairs` (JSON); parameters exactly
`unknown` (`fail`|`keep`) and `placeholder_syntax` (`bracket_upper`). One streaming `finditer` pass; inserted text
is never rescanned; unknown/absent detection uses input tokens only. The JSON-escaped output length (jsonio's
ASCII encoding, quotes included) is accumulated in 256-character chunks before each append, so an oversized value
is never built; `transforms.execute`'s final check remains. Trace (`fills`) is one object with bounded
`replacements`, `unknown` and `absent` lists (200 each, `truncated` flags). The starter exists only as a v4
transform (`TRANSFORM_ONLY_IDS`), every port Local only.

**`text` expectations.** `{kind: "text", assertions: [...]}` with 1–50 assertions: `includes`/`excludes` take
`output`, `operation`, `values` (1–50 strings of 1–4,096) and `case` (`sensitive`|`insensitive`, `casefold`);
`excludes_pattern` takes `output`, `operation` and a named `pattern`. Outputs must be declared Text outputs;
a JSON output is `example_incompatible`. Judgement follows the existing rules (unverified → `unknown`, missing
output → `incorrect`).

**Explanation projection.** `Graph.explain(example=None)` describes, never decides: `values[id]` (dotted source
identity) reports `restricted` from `Graph.restricted` with causes `workflow_io`, `declared_label` (a labelled
output, or the labelled receiving port when every receiver is labelled), `consumer_label` (fan-out: a labelled
receiver restricts unlabelled siblings), `example_restriction` (example basis only), `upstream` (a chain of at
most 8 hops through bindings) and, for unrestricted Gate outputs, `released`. `nodes[n]` reports `protected` from
`Graph.protected_nodes` with `restricted_input`, `context_manual`/`context_inherited` and `release_assessment`
(a Gate's local assessment). At most 10 causes each, with `more`. Selecting a basis replaces the previous one.
`GET workflows/<name>` adds `restriction` (base) and `effective` beside each declared `local_only` in
`node_ports`; readiness adds `restriction` per example plan.

**Design-time validation.** `POST workflow-validate` takes `name`, `file_token` (null for new), `generation`,
`editor`, optional `selections`, advisory `rebind`, `layout` and `example_id`. It shares
`Workflows.candidate(base, edits, stack, *, replacements)` with Save and Rebind (Save passes no replacements,
Rebind its selected existing nodes) and builds the Graph in lenient mode: located binding issues are collected; a
cycle or unknown source fills `unplannable`, sets `available: false` and omits `restriction` and `destinations`
entirely. Planned graphs add Gate findings (as readiness) to `structural`, `gates`, and `destinations` from
`State.destination_config()` and `outbound_policy.facts` only. It never writes, publishes or authorises a Rebind.
No capture cache; captures close before responding; the response is capped at 256 KiB (`truncated`). Strict
planning for Save, readiness and execution is unchanged.

**Configuration-only projection.** `State.destination_config()` reads roles, profiles and server base URLs and
the already-loaded MCP library under the state lock: no `Endpoint`, no credential, token or session-key read, no
`view()` and no MCP refresh. It may lag an MCP library edit until the next ordinary refresh.

**Saved-use inventory.** `outbound_policy.saved_uses(state)` lists configured roles, blocks' current default
revisions and up to 50 saved workflows (opened with `Workflows.open_saved`, one graph per workflow, captures
released before the next). `requires_trust` is set when a saved workflow dispatches from a protected node under its
base restrictions or any one example's (Gate assessments always). `destination-trust-candidates` merges these rows
with the authenticated enumeration and adds `requires_trust`, `remedy`, `observed` (MCP; `unknown` when only the
inventory knows it), `used_by_truncated`, `trust_gaps` (eligible, untrusted, required) and `scan`
(`complete`, `workflows`, `workflows_skipped`, `reasons`). It runs when Trusted destinations is opened.

**Remedies and catalogue.** Protected refusals (Workshop Test/Compare and assistance), readiness issues and the
run-time `destination_changed` issue carry `remedy: {action, target?} | null`; readiness destination issues also
carry `facts`. Actions: `grant`, `fix_address`, `fix_proxy`, `choose_local_role`, `add_release_gate` (workflow
readiness only, when the node is protected solely by upstream restriction), `configure_role`, `configure_server`,
`refresh_readiness`, `repair_trust_settings`, `explain`. `protected_host_script` is now a located issue with
`explain`. `messages.py` holds the scoped catalogue (`CODES`, `explain(item)` with a generic fallback); `GET
messages` serves its templates and the page fills them with `textContent`, keeping the code in a details line.

**Reference workflows.** `tutorial_support/private_summary.py` holds the synthetic documents, the deterministic
redaction and summary used by the packaged stand-in models `private-deid` and `private-summary`, the block and
workflow definitions and their ten `text`-checked examples. The tutorial topics `private-summary-local` and
`private-summary-release` and the tests prepare the same fixture.

### Release Gate boundary

`release_gate.py` owns built-in Gate validation, local identifier matching and the fixed,
versioned assessment definition. `workflow_definition.py` accepts bundle versions 1 and 2;
a Gate row has `gate` and `bindings`, never `snapshot_id`. Saves containing Gates write version 2;
ordinary workflows continue to write version 1. Gate contracts are exact-text fields in the
editor projection, as are workflow input/output contracts. Assessment prompt, version and
contract participate in Gate executable identity and saved capture evidence. The internal
assessment is a captured v4 model block using the existing runner, validation and repair path.

A Gate reads `value` and optional restricted `scan`; its sole output is `released`. Planning
allows that output to be unrestricted while runtime creates it only after an explicit decision.
No source/sibling identity is relabelled. Existing source-level reverse marking and ordinary
all-output inheritance apply after the boundary. Scan sources must already be restricted.
Unprotected candidate ancestors are errors, except dispatches whose variable lineage already
passes through earlier Gates. Traversal continues on other branches. Downstream review scope
includes indirect dispatch consumers and later Gates' local assessments, stopping at those
Gates' outputs. Captured destination guards remain authoritative before actual sends.

The Gate's own optional assessment is always protected and requires a literal-loopback grant.
It uses a separate role, fixed instructions and a JSON findings contract, with one repair.
Assessment data, findings and traces remain restricted; only the candidate becomes released.
Invalid assessment responses allow an acknowledged manual decision, but Stop, budget exhaustion,
unknown send outcomes, evidence failure and destination refusals do not become advisory passes.

Jobs remain `outcome: running` and expose phases `assessing_release`, `awaiting_release` and
`deciding_release`. A Condition shares the job lock; Stop notifies it and performs child Stop
outside the lock. A generation-bound execution timer prevents a cancelled callback from firing
against a human pause. Remaining execution time resumes after the decision; review waits have
a separate monotonic timeout. Evidence writes and model calls occur outside the job lock.
The Workshop slot and registered writer remain owned until normal job cleanup.

Additional authenticated Workshop routes:

| Route | Behaviour |
| --- | --- |
| `GET workflow-active` | Returns operation ID, name and batch flag for the active workflow, or an empty object. |
| `POST workflow-validate` | Advisory design-time validation of an unsaved draft (see core v3 above). |
| `GET messages` | The explanation catalogue's templates. |
| `POST workflow-jobs/<id>/release` | Sends token, payload/executable/scope/checks digests, decision (`release` or `reject`) and optional boolean acknowledgements. |

The `release` object in a waiting job contains the immutable candidate, destination scope,
checks, expiry and a fresh token. Tokens are run/example/node specific, never saved, and never
accepted across pauses. Validation and one-shot decision selection are atomic under the job lock.
Exact duplicate submissions are idempotent; conflicting/stale/expired submissions return
`409 release_stale`. Missing required acknowledgement returns `409 release_acknowledgement_required`.
The worker persists a selected decision before exposing output when saving is enabled; a
write failure prevents downstream execution. A successful submission response acknowledges
selection, not completion of subsequent execution. Stop still prevents later dispatch.

Review/decision records use the existing private evidence store only when Save runs is enabled.
Compact decision records contain hit offsets and placeholder/index labels, not copied originals,
JSON paths potentially containing originals, assessment prose or approval tokens. Restricted
review records retain paths and optional assessment traces subject to existing redaction/limits.
A process restart shows incomplete evidence as interrupted and cannot resume approval.

Identifier checking accepts up to 1000 bounded nonempty originals and 1000 hits, with a
16 MiB character-comparison work limit. Exceeding a limit or malformed input fails visibly.
Unicode case-fold expansions map back to original code-point offsets. JSON property names and
string values are checked with paths; numeric/null/boolean values are preserved. JSON release
review uses deterministic exact serialisation, not a claim about downstream HTTP byte layout.
