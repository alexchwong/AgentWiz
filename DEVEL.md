# Developer guide

This file is for repository maintenance. Usage is documented in `README.md`; the build reference
for 0.1.3 is *AgentWiz 0.1.3 — Technical Specification* (version 4), which takes precedence over
Section 8 of the release plan (*AgentWiz 0.1.x — Release Plan and 0.2 Design*, version 11) and over
Sections 11–12 of version 10.1, which the plan carries forward; its Section 22 lists every
departure. The build reference for 0.1.2 was *AgentWiz 0.1.2 — Technical Specification* (version
5.1), whose build process (D42, D44, D48–D51), error-text rule, MCP design and task settings stand
unless the 0.1.3 specification says otherwise. Its companion, *AgentWiz
attachment interface* (version 3, draft; `interface_version` 1), is kept as
`docs/attachment_interface.md`. The plan carries everything that still binds from the 0.1.0 and
0.1.1 specifications (plan 2.9, 6.13), except that the owner has replaced the 0.1.1 specification's
manual evidence regime (see "Checking a release"). This guide owns project maintenance rules;
`.clinerules` defines Cline's workflow for reviewing and refining Claude's handover. Nothing in
this guide, `.clinerules`, `tests/`, `scripts/` or
`.github/` ships in the release wheel.

Coding agents must not modify `.clinerules` unless the owner explicitly instructs them to do so.
General permission to implement, review, refactor or update documentation does not grant that permission.

## Quick start

Work only in the checkout's `.venv`, set up with `scripts/dev_setup.sh`, then activate it.
Use `python -m pytest`, never bare `pytest`. Never commit `.venv/` or a real API key.
Keep `README.md` for users, this guide for maintenance and `NEWS.md` for the changelog.
Update this guide in the same change when layout, commands, tests or release steps change.

From the repository root:

```bash
scripts/dev_setup.sh                        # ./.venv + editable install + pytest, jsonschema, Playwright and Chromium (downloads the pinned modelSpell wheel)
. .venv/bin/activate

python -m pytest -q -m "not live"          # the offline suite (the gate; live checks deselected)
python -m pytest tests/test_page.py         # the page checks alone (headless Chromium)
agentwiz --no-browser                       # run the UI from your checkout (workspace: ./workspaces/default)
python scripts/stub_backend.py              # scripted backend for trying the UI without a model
python scripts/stub_mcp_server.py           # scripted MCP server (both protocol eras) for the MCP tools tab
python scripts/build_release.py --smoke     # build, check (RC1-RC3) and smoke-test the release wheel
```

`scripts/dev_setup.sh [--python PATH]` is the one developer setup command. It refuses to run outside
a checkout, creates `./.venv` if missing, runs `python -m pip install -e '.[dev,checks]'` and then
`python -m playwright install chromium` (if the browser download fails it says so and how to retry;
the page checks will not run without it); it also converts a `.venv` made by the user installer
(wheel only, no pytest). The user installer
(`scripts/install.sh`) refuses to run in a checkout unless `--dir` is given. `./workspaces/`, `./model_profiles/` and
`./mcp_servers/` are ignored by git in this checkout.

## Contents

- [Repository layout](#repository-layout)
- [The modelSpell dependency](#the-modelspell-dependency)
- [MCP protocol versions](#mcp-protocol-versions)
- [The attachment interface](#the-attachment-interface)
- [UI provenance](#ui-provenance)
- [The page: one function per tab](#the-page-one-function-per-tab)
- [Page checks](#page-checks)
- [Runs and records](#runs-and-records)
- [Contracts and checks](#contracts-and-checks)
- [Concurrency and resource limits](#concurrency-and-resource-limits)
- [Error reporting](#error-reporting)
- [Tests](#tests)
- [Checking a release](#checking-a-release)
- [Co-developing with modelSpell](#co-developing-with-modelspell)
- [Branches and releases](#branches-and-releases)
- [NEWS entries](#news-entries)
- [Implementation discretion](#implementation-discretion)

## Repository layout

| Path | Ships in wheel | Purpose |
| --- | --- | --- |
| `agentwiz/__init__.py` | yes | Re-exports `__version__`; no other imports, no side effects. |
| `agentwiz/_version.py` | yes | Only version source: exactly `__version__ = "X.Y.Z"`. |
| `agentwiz/__main__.py` | yes | Command line (`agentwiz`, `python -m agentwiz`); opens the workspace before binding; exit statuses 1, 2, 3. |
| `agentwiz/model_call.py` | yes | Settings and key validation, message building, task arguments (`task_arguments`: route support, `require_parameters`, `check_settings`), the LM Studio model lookup, the call (`call_model(..., cancel=)`, passed to `modelspell.complete`), outcome classification, error descriptions. No HTTP. |
| `agentwiz/checks.py` | yes | **0.1.3.** Contract parsing and validation, answer parsing, every check, results, the repair message. Pure functions that accept any value. `jsonschema` is imported lazily, only here. No HTTP. |
| `agentwiz/contracts.py` | yes | **0.1.3.** Contract files in `<workspace>/contracts/`: reading, re-scanning by fingerprint, revisions, writing. Called from `state.py` under the state lock. No HTTP. |
| `agentwiz/runs.py` | yes | **0.1.3.** `CancelToken`; runs; the attempt store: identity, records, end point, commit, summaries, retention, save status, deletion reservation. No HTTP. |
| `agentwiz/model_step.py` | yes | **0.1.3.** `run_attempt`, the headless model-step function, and the hook `before_end_point`. No HTTP. |
| `agentwiz/history.py` | yes | **0.1.3.** The run-history writer (run folders, redaction), listing, reading and deleting saved runs; `remove_tree`. No HTTP. |
| `agentwiz/task_settings.py` | yes | `TaskSettings`, its rules, and `task_settings.json`. No HTTP. |
| `agentwiz/state.py` | yes | Process state: settings, keys, profiles, saved task settings, the MCP library and tokens, contracts, the history switch, the five revisions, state lock, request budget, snapshots, key and token sources, every credential held (for redaction), `key_for(kind, base_url)` for repairs. No HTTP. |
| `agentwiz/workspace.py` | yes | Workspace names, opening and locking (`flock`), reading and validating workspace.json, atomic writes. Called only from `state.py` under the state lock. No HTTP. Not imported by `import agentwiz`. |
| `agentwiz/profiles.py` | yes | Workspace profile copies and shared defaults (format version 2; reads 1): validation, preservation, atomic writes, fingerprints and permanent short-lived library lock. No HTTP. |
| `agentwiz/jsonio.py` | yes | The one JSON writer (`encode`, `encode_line`); the file writer and strict reader for AgentWiz's own files (`encode_file`, `decode_file`); the exact-number reader and writer (`decode_exact`, `encode_exact(value, indent=None)`, `encode_file_exact`, `decode_file_exact`; `JSONSyntax` and `ExactLimit` are its only errors besides other `ValueError`s) and `encode_record_file` for run files. Standard library only. |
| `agentwiz/attachments.py` | yes | The basic attachment reader (`.txt`, `.md`, `.csv`, `.json`) and `check_document`, implementing `docs/attachment_interface.md`. No HTTP. |
| `agentwiz/insertion.py` | yes | Provenance lines and preview sizes for files and tool results; `REQUEST_LIMIT`. The page now copies `preview.text` instead of inserting it. No HTTP. |
| `agentwiz/mcp_library.py` | yes | The shared MCP server library (`servers.json`, `.servers.lock`): validation, fingerprints, atomic writes. No HTTP server code. |
| `agentwiz/mcp_client.py` | yes | Streamable HTTP MCP client, both eras, standard library only: probe and era cache, discovery entries, legacy sessions and the session lock, gates, reply reading with limits, `classify`. An HTTP *client*; knows nothing of AgentWiz's server. |
| `agentwiz/mcp_ops.py` | yes | Operations: worker threads, one commit per operation, abandonment and socket shutdown, capacity, maintenance (notices, `DELETE`s), retention. No HTTP server code. |
| `agentwiz/tool_forms.py` | yes | Form specifications from tool input schemas. No HTTP. |
| `agentwiz/ui/server.py` | yes | The HTTP server; the only module that knows HTTP. `Handler.write_event` is the one writer of stream events. |
| `agentwiz/ui/index.html` | yes | The page: six tabs, header dialogs, one file with no build step. |
| `pyproject.toml`, `README.md`, `LICENSE` | metadata | Packaging, wheel description, MIT license. |
| `tests/` | no | Offline suite (`test_ui.py`, `test_profiles.py`, `test_workspace.py`, `test_jsonio.py`, `test_model_call.py`, `test_task_settings.py`, `test_attachments.py`, `test_mcp_client.py`, `test_mcp_ops.py`, `test_tool_forms.py`, `test_stub_mcp_server.py`; 0.1.3: `test_checks.py`, `test_contracts.py`, `test_runs.py`, `test_model_step.py`, `test_attempts.py`, `test_history.py`, `test_page.py`, `test_live_judgement.py`), opt-in live checks (`test_live.py`, with the pure judgement function `live_stop.py`), and the test-only TLS certificate (`tests/fixtures/tls/`). |
| `docs/attachment_interface.md` | no | The attachment interface (companion document, version 3, draft). |
| `scripts/build_release.py` | no | Builds the release wheel from `HEAD`, runs release checks RC1–RC3; `--smoke`; `--no-isolation`; `--modelspell-wheel` (offline smoke); `--changed-since`. |
| `scripts/smoke_installed.py` | no | Smoke check (S1–S12) against an installed wheel. |
| `scripts/dev_setup.sh` | no | Developer setup: `./.venv` with an editable install, pytest, `jsonschema`, Playwright and Chromium. |
| `scripts/stub_backend.py` | no | Scripted OpenAI-compatible backend for trying the UI without a model; logs the sampling fields and message roles each request carries. Models `json-pass`, `json-repair` and `slow-headers` serve the README tour. |
| `scripts/stub_mcp_server.py` | no | Scripted MCP server (`--era dual|modern|legacy`, `--token`) for trying the MCP tools tab; logs every request, disconnect, notice and `DELETE`. |
| `scripts/install.sh` | no | Installer behind the README one-liner; refuses to run in a checkout without `--dir`; `--with-checks` installs `AgentWiz[checks]`; re-attached to every release as `install.sh`. |
| `DEVEL.md`, `NEWS.md`, `.clinerules` | no | Maintenance guide, changelog, agent rules. |
| `.github/workflows/` | no | `tests.yml` (push, PR) and `release.yml` (push to `main`). |

Shipped code must run on Python 3.10: `from __future__ import annotations` for `X | None` hints,
and no 3.11-only modules or APIs (`tomllib`, `datetime.UTC`, exception groups).

## The modelSpell dependency

AgentWiz depends on the modelSpell **v0.3.0 release wheel**, pinned by URL and sha256 in
`pyproject.toml` (D74). The error-text re-check below was done for the move from 0.2.1 to 0.3.0
(specification 0.1.3, Section 3.2): `errors.py` adds only `Cancelled.sent` and a docstring and no
error class; the one new pre-send message, `ConfigError("cancel must be None or a CancelHandle")`,
names an argument; `Cancelled` messages are fixed; the watched transport's new failures
(`TimeoutError`, `OSError` from the non-blocking connect) are mapped by the existing handlers to
`RequestTimeout` and `ConnectError`, which are already fixed-description errors. Nothing moved
groups. (For 0.1.0 to 0.2.1 the same check, specification 0.1.2 Section 3.2, found that `_http.py`,
`errors.py`, `_sse.py` and `_discovery.py` were unchanged and every new error was a pre-send
`ConfigError` or `UnsupportedSetting` naming arguments, routes and rules, never a value.)
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

`docs/attachment_interface.md` is a copy of the project's *AgentWiz attachment interface*
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

> **The page: one function per tab.**
> 1. Each tab demonstrates one function from start to finish and works without visiting any other tab.
> 2. A release's new function gets a new tab. Changes to an existing tab only extend that tab's own function.
> 3. Tabs never pass data to each other in 0.1.x. The clipboard is the only bridge.
> 4. Configuration is not a function. Model profiles, MCP servers and roles are header dialogs shared by every tab. Saved definitions (contracts, blocks) are shared the same way.
> 5. At most seven top-level tabs. The eighth means grouping into nested tabs, at most two levels deep.
> 6. Integration in 0.2.x arrives as new tabs; existing tabs stay as a workbench.
> 7. Every output panel has a Copy button.
> 8. Page checks find elements by `data-test` attributes and assert values and states, never wording or layout.
>
> Development rules live in this file. Agents do not edit `.clinerules`.

The page is one file, `agentwiz/ui/index.html`, with markup, styles and script and no build step,
no framework. Its script has shared helpers (the API call with the session token, the NDJSON reader,
Copy, the task-panel component, the stale-revision handler, the request controller Chat and Checked
request use) and one section per tab. No tab reads or writes another tab's state. Every
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
panels `panel-<fragment>`.

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
  the fakes received) and the state attributes above; wait with Playwright's `expect(...)` conditions
  on those attributes, or on conditions in the page; never sleep, never assert wording or layout.
  Force orderings with the fakes' hold and release, `history.before_write`, `Handler.write_event` and
  the like.
- **Blocked, never skipped.** If Playwright or its Chromium cannot be provided, the page checks fail
  with that reason and the gate is blocked (D51); they are never passed by skipping.
- The in-UI tutorial is optional learning material. Script-verifiable UI behaviour belongs in
  page checks, not in owner assignments; subjective review is governed by "Checking a release".

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

- **File format.** `<workspace>/contracts/<name>.json`, `format_version` 1, written with
  `jsonio.encode_file_exact` (indented, ASCII, numbers digit for digit) through `workspace.write_atomic`.
  Unknown top-level fields are kept. Revisions come from one process-wide counter, so a deleted and
  recreated contract never repeats one. Files are re-scanned on `/api/state`, every contract route
  and when `/api/complete` names a contract (inode, mtime in nanoseconds and size).
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
- **The extra.** `jsonschema>=4.26,<5` is the optional extra `checks`; `checks.schema_support()`
  reports it, and a version outside the range counts as missing. Tests that need it carry the marker
  `needs_checks`, which skips with the reason "the checks extra is not installed" only where it is
  absent (CI's Python 3.10 jobs); the build's gates install it, so nothing skips there. The no-extra
  path is tested everywhere by making `schema_support()` report the extra missing.
- **One dialect.** Every `$schema` on an actual schema, at the root or reached from it, must be
  Draft 2020-12; `$ref` and `$dynamicRef` must start with `#`; a `$schema` key inside literal data
  (`const`, `enum`, `default`, `examples`) is data. A missing local target is not refused at save; it
  gives `contract_error` when applied.

## Concurrency and resource limits

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
| Contracts | Changed only through `contracts.py`, called from `state.py` under the state lock; re-read before each change; written atomically; external changes noticed by fingerprint; revisions from one process-wide counter. |
| The history switch | `workspace.json` changed under the state lock with the history revision, re-read first, written atomically. |
| Run folders | New files, each written once, without the state or store lock; the writer never recreates a folder; deletion reserved under the store's lock, files removed with no lock held, outcome settled under the store's lock. |
| Records in memory | 100 records and 256 MiB of full records, 1,000 settled summaries plus pending envelopes, run metadata while needed; uncommitted attempts never dropped. |
| Revisions | Five: configuration, task settings, MCP, one per contract, and history. A change to one never makes a page's view of another stale. |

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
handles workspace.json, its lifetime lock, and the reusable atomic writer. From 0.1.2 both libraries
use `{"format_version": 2, "profiles": {...}}` (`profiles.PROFILES_FORMAT_VERSION`; `workspace.json`
stays at 1), read version 1 too, preserve unknown fields and never receive session keys. Every write
path (local writes, seeding a new workspace, update from defaults, export) writes version 2 with the
retired `reasoning` and `max_tokens` dropped from every profile, through `profiles.write_profiles`.
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
`conflicts` and `unresolved_active`. Conflicts compare the four validated ModelSettings fields,
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
| `ContractError` and contract problems | 400 `{"code": "contract_error", "issues": [{"location", "message"}]}` on save and check; a contract file's problem is `{"file", "code" (`unreadable`, `invalid`, `newer_format`), "message"}`. Messages name rules and locations, never values. | nothing |
| Run-file problems | `problems` of `GET /api/runs/<id>`: file, code (`unreadable`, `invalid`), location and rule, never values | nothing |
| New refusals | 404 `no_such_contract`, `no_such_attempt`, `no_such_run`; 409 `exists`, `contract_unusable`, `run_stopped`, `not_latest`, `not_repairable`, `run_active`, `not_deletable` (and `stale_revision`, `read_only` for contracts and the history switch); 410 `not_held`; 500 `delete_failed` with AgentWiz's text and the system's error text | nothing |
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

## Tests

At each build checkpoint, run the offline gate below. Never pass it by skipping, weakening or
deleting a test. Report a check that could not run as blocked, never as passed. Never substitute
a stand-in for modelSpell or present a source-only check as the installed-wheel smoke. Offline
tests must run unattended. Live checks are optional; the tutorial is optional learning material.

```bash
python -m pytest -q -m "not live"   # offline suite (the gate)
python -m pytest tests/test_ui.py   # one area
python -m pytest -m live -s         # optional live checks (variables below)
```

**Testing directive (owner, 25 October 2026; T2 clarified 27 October 2026).**

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

**Deterministic and minimal (D49).** The offline suite never calls a language model, a hosted API or
a real MCP server. Every backend and MCP server is a scripted fake in the test process (`TestServer`,
`FakeMCP` in `conftest.py`) or the repository's own stub (`scripts/stub_mcp_server.py`, test #54), on
loopback. Orderings are forced with hooks, never with `sleep`: the fakes' hold and release, the
connection hook `mcp_client.connect_socket`, `mcp_ops.before_final_write`, `threading.Barrier` and
`threading.Event`. Each behaviour is asserted in one test.

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

**The page's JavaScript is tested** from 0.1.3 by `tests/test_page.py` (see "Page checks"), with the
fakes and without a model. Owner review is limited to subjective presentation, clarity and
usability; see "Checking a release" for the current owner disposition overriding the old D50 tour.

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

**Live checks** (`tests/test_live.py`) are optional: a quick end-to-end run against a backend you
have running. They skip unless their variables are set (CI sets none), run against a temporary
workspace, and print one summary line.

| Backend | Variables |
| --- | --- |
| LM Studio | `AGENTWIZ_LIVE_LMSTUDIO_URL` (e.g. `http://localhost:1234/v1`), `AGENTWIZ_LIVE_LMSTUDIO_MODEL`; optional `AGENTWIZ_LIVE_LMSTUDIO_KEY` when LM Studio's authentication is on |
| OpenRouter | `OPENROUTER_API_KEY`, `AGENTWIZ_LIVE_OPENROUTER_MODEL` (`author/slug`); optional `AGENTWIZ_LIVE_OPENROUTER_PROVIDER` (slug to pin; default the first listed) |
| llama-server | `AGENTWIZ_LIVE_LLAMASERVER_URL` (e.g. `http://127.0.0.1:8080/v1`), `AGENTWIZ_LIVE_LLAMASERVER_MODEL` |

| OpenRouter, 0.1.3 | `test_live_stop_openrouter` (a long padded prompt, then Stop after `started`; judged by the pure function in `tests/live_stop.py`, itself tested offline) and `test_live_checked_request_openrouter` (a JSON contract, then up to two repairs). LM Studio and llama-server variants skip with "blocked until AgentWiz 0.2.0 (D72)". |

| Any MCP server (`test_live_mcp`) | `AGENTWIZ_LIVE_MCP_URL`, `AGENTWIZ_LIVE_MCP_TOOL`, `AGENTWIZ_LIVE_MCP_ARGUMENTS` (the arguments as JSON text); optional `AGENTWIZ_LIVE_MCP_TOKEN`. Example, with web-retrieval-mcp running: `http://127.0.0.1:8765/mcp`, `search_web`, `{"query":"model context protocol"}` |

The model checks send task settings (`temperature`, `max_tokens`). All accept
`AGENTWIZ_LIVE_TIMEOUT` (seconds per read from AgentWiz, default 600: AgentWiz sends nothing until
the model's first visible token, so a loading or silently reasoning model can take minutes). The
live checks are human checks: they never run in a checkpoint gate, which deselects them with
`-m "not live"` (unsetting `AGENTWIZ_LIVE_*` alone would not be enough: the OpenRouter check reads
`OPENROUTER_API_KEY`).

**Building 0.1.3.** Phases P, 0, A, B, D and E (specification 0.1.3, Section 21); the 0.1.2 rules
below apply unchanged. The gate in every phase is the whole offline suite on the prepared
interpreter with `.[dev,checks]` and Chromium installed; Phase E's also runs on Python 3.10 when it can
be provided, then the offline release smoke below on a throwaway local commit that is never pushed.
The hand-over is a zip of new and changed files, `DELETIONS.txt` and `HANDOVER.md`; the owner commits.

**Building 0.1.2 (D48–D51).** The build runs as phases (P, 0, A, B, C1, C2, D), each ending at a
checkpoint gate: `python -m pytest -q -m "not live"`, passing with no failures, errors or skips.
When a gate passes, the build continues without waiting; a gate is never passed by skipping,
weakening or deleting a test. Phase P prepares the environment (interpreters, the dev install,
`hatchling`, the hash-checked modelSpell wheel, the test certificate) and is the only phase that may
use the network beyond loopback. A check whose dependency the environment cannot provide is
**blocked**, never counted as passed. The historical Phase H manual tour is superseded by the
owner direction under "Checking a release"; live checks remain optional.

**Offline Phase D.** With `hatchling` installed in the interpreter and a local copy of the pinned
modelSpell wheel:

```bash
python scripts/build_release.py --smoke --no-isolation --modelspell-wheel ../agentwiz-prep/wheels/modelspell-0.3.0-py3-none-any.whl
```

`--modelspell-wheel` refuses a file whose name is not the pinned URL's file name or whose sha256 is
not the pin; the smoke then installs both wheels with `pip install --no-index --no-deps` and runs
`pip check`. Without it the script behaves as in 0.1.1 (pip fetches the pinned URL). A warm pip cache
can hide a missing prerequisite, so it is not preparation.

## Checking a release

**Owner direction, 27 October 2026.** Releases are not gated on manually collected evidence. The
0.1.1 specification's runbook, evidence records, walkthrough steps (W1–W20), lock checks (L1, L2),
release review (M1–M4) and their definition-of-done items (C1–C5, D1–D5, F3) are withdrawn.
Bug-checking is automatic: the offline suite in CI, and `build_release.py`'s release checks and
smoke test, which run before anything is published. README's "Trying it out" section is optional
learning material. Owner review is limited as directed below; real-backend live checks are optional.

**Owner direction, 2 November 2026 — automate objective checks.** Anything that can be
verified by Python, HTTP or a scripted browser belongs in pytest, not in an owner checklist.
Being visible in a browser does not make a deterministic assertion a human UI check. This includes
expected pass/fail results, exact values, persistence, deletion, protocol negotiation, credentials,
cancellation, stale revisions, clipboard contents, enabled controls and keyboard/tab behaviour.
Use existing coverage; add focused functional tests only for actual gaps under T1–T3.

Owner UI review is limited to subjective presentation (readability, spacing, clipping/overlap),
clarity of guidance/errors/status, and discoverability/understandability of interactions. Any future
owner review must be supported inside the UI with tips, auto-filled inputs and managed sandbox
setup, never a manual setup runbook. Do not ask the owner to repeat script-verifiable checks.

**0.1.3 subjective UI review: Completed by owner direction.** The owner instructed that the
remaining subjective review be marked completed after removing objective owner checks. This is
an owner disposition, not a claim of individual observations or a new automated test result. The
old 29-step walkthrough is not a release gate. Offline tests, CI and release smoke requirements
remain in force; real-backend live checks remain optional.

Tutorial mode is an optional learning aid, not release sign-off. Its header button starts an
isolated session with persistent tips, auto-filled examples, real application controls and managed
local scripted services. Inputs are prepared on entry/reset, not continuously overwritten. Normal
workspaces and credentials stay untouched; Exit, failure and abandoned-session handling clean up
tutorial resources. It works in the installed application without a source checkout. Keep learning
examples current with relevant feature changes, without acknowledgement gates or pass/completion
accounting. No screenshots or evidence dossiers are required.

**Implementation (0.1.3).** `agentwiz/tutorial.py` supervises one disposable child UI per parent,
with separate temporary workspace, defaults and MCP library. `tutorial_support/backend.py` and
`tutorial_support/mcp.py` are packaged stand-ins; the scripts are thin checkout entry points to the
same code. All servers use loopback and dynamic ports. Authenticated `/api/tutorial` and
`/api/tutorial/{start,step,restart,exit}` routes manage the session. The UI controller has explicit
per-tab preparation hooks (a narrow exception to tab independence, not inter-tab data transfer).
The 29 learning topics map to the README optional reference. Only the current topic is retained
for the session; there are no acknowledgement records, pass counts or release sign-off controls.
The browser heartbeats every minute; 30 minutes without a heartbeat expires a session, checked
every 30 seconds. Parent shutdown and Exit also clean up the child servers and temporary files.
Startup failures clean up partially started services. Restart/damage/restore act only on the child.

`python -m pytest tests/test_tutorial.py` checks isolation/recovery/cleanup, failed-start cleanup,
and browser preparation across the route with an actual scripted response and repair. Functional
reasons: prevent modification of normal work, leaked services and broken auto-fill/actions. No
assertions inspect repository file contents. The README examples are optional learning material,
not owner assignments. Automated tests remain responsible for deterministic outcomes.

## Co-developing with modelSpell

To try AgentWiz against a local modelSpell checkout, install it over the pin in your `.venv`:

```bash
python -m pip install -e ../modelSpell
```

Never commit that change. Releases always use the pinned wheel; reinstall with
`python -m pip install -e '.[dev]' --force-reinstall` to return to it.

## Branches and releases

Work happens on `devel`; `main` only receives merges from `devel`. `main` is created by the 0.1.0
merge, so the release workflow cannot publish an unfinished scaffold. `tests.yml` runs on pushes to
both and on pull requests (Ubuntu and macOS × Python 3.10 and 3.13).

`release.yml` runs on every push to `main`. It reruns the tests, then, from the commit that
passed them (`github.sha`, never a re-read of `main`):

- **If `vX.Y.Z` is not yet released:** builds the wheel from that commit, runs the release checks
  RC1–RC3 on it, installs it into a fresh venv with `--no-index` (the pinned modelSpell wheel is
  still fetched) and runs the smoke check, then creates release `vX.Y.Z` tagged at that commit, with
  the wheel, the NEWS section as notes and a pinned install line with the sha256. A failed check
  stops the job before anything is published.
- **If `vX.Y.Z` already exists:** fails if any shipped file changed since the tag; otherwise skips
  building and publishing the wheel.
- **Finally, once the release is there** (new, or unchanged with the guard passing): re-attaches
  `scripts/install.sh` to `vX.Y.Z` with `--clobber`, so `releases/latest/download/install.sh`
  always serves the current installer without a new release.

**Shipped-file rule (authoritative).** Any change to `agentwiz/**` (Python, HTML, CSS or
JavaScript), `pyproject.toml`, `README.md` or `LICENSE` needs a version bump and a NEWS entry before
a merge into `main`. Changes to tests, scripts, workflows, `DEVEL.md` and `.clinerules` merge without
a release. The rule is enforced by `release.yml` (`build_release.py --changed-since <tag>`), so
`devel` may carry shipped-file edits without a bump; the run for that merge fails if the bump is
missing, and no release is published.

**Release checks.** `build_release.py` runs these on every build (not with `--changed-since`),
after building and before any smoke test, reading only the archived `HEAD` tree and the wheel.
They are release tooling under the owner's exception to T2, not tests:

| Check | Passes when |
| --- | --- |
| RC1 | `NEWS.md` has exactly one line `## <version>`; its section has at least one `- ` bullet, each of at most 20 words. |
| RC2 | The wheel is `agentwiz-<version>-py3-none-any.whl`; every member is under `agentwiz/` or `agentwiz-<version>.dist-info/`; `agentwiz/ui/index.html` is present; the `.dist-info` members are exactly `METADATA`, `WHEEL`, `RECORD`, `entry_points.txt`, `licenses/LICENSE`. |
| RC3 | `METADATA` has `Name: AgentWiz`, `Version: <version>`, `License-Expression: MIT`, and exactly one runtime `Requires-Dist`, `<name> @ <url>`, agreeing with the single direct-URL dependency in `pyproject.toml`: both names normalise to `modelspell` (Hatchling lowercases it), and the URLs are identical and pin a modelSpell release wheel with its sha256. |

Each prints `RCn ok` or `RCn FAILED: <reason>`; all three run; on any failure nothing is copied to
`--output-dir` and the script exits non-zero. Whether NEWS bullets are user-visible stays a human
judgement.

### Release checklist

1. On `devel`, bump `agentwiz/_version.py`, add `## X.Y.Z` to `NEWS.md` (user-visible bullets), and
   update `README.md`. Commit and push; CI runs the offline suite.
2. **Owner UI review:** only subjective presentation, clarity and usability belong here. The
   0.1.3 review is **Completed by owner direction**, as recorded under "Checking a release".
   No manual regression walkthrough or tutorial completion is required. Optional live checks and
   the web-retrieval-mcp example are not gates.
3. Commit to `devel` and push; CI runs the offline suite (Ubuntu and macOS, Python 3.10 and 3.13).
   This matrix is a release criterion: the build environment cannot run macOS.
4. Run `python scripts/build_release.py --smoke` (builds from the committed `HEAD`, runs RC1–RC3,
   then the smoke test S1–S10).
5. Merge `devel` into `main`. The release workflow reruns the tests and RC1–RC3, then publishes.

## NEWS entries

User-visible changes only, under the version heading, newest first; one change per bullet, at most
20 words. Released sections are never edited. (One owner-approved exception, D43: 0.1.2 moved the
"Unreleased" macOS startup bullet under `## 0.1.1`, because it shipped in the 0.1.1 wheel.)

## Implementation discretion

Routine implementation choices are made by the implementer and recorded here or in the pull
request. Any scope change, new dependency, or behavioural departure from the specification that it
does not already record is reported to the owner before merging.

Departures from the plan, including the two approved for 0.1.0 (errors inside an accepted stream
described without "HTTP 200"; non-ASCII base URLs refused), are recorded in the 0.1.1
specification's Section 16.2.

Choices recorded for 0.1.3 (specification 0.1.3, Section 22.2, plus the builder's):

- ID forms: 12-hex run IDs, `<run>-<n>` attempt IDs, step `request`; the record's field names,
  including `check_skipped`, `stop_mode` and `sent`.
- Polling: the first poll is immediate, then every 2 seconds; the history list limit is 200.
- The repair message's wording and instructions; schema issue messages are AgentWiz's own, built
  from the keyword and values (quoting the keyword's value as written in the contract, not the
  validation copy's integer form), cut at 200 characters; issue sort order.
- Contract problem codes `unreadable`, `invalid`, `newer_format`; a problem's `file` is the absolute
  path; unknown top-level fields are noted on save.
- A `strict` flag or a schema on a text contract is refused; a boolean schema needs the extra like
  any other; `length` accepts `null` for either bound.
- A `RecursionError` while checking a deeply nested value makes that check `not_run` ("nested too
  deeply") rather than an internal error; an `ExactLimit` is re-scanned for its location.
- The OpenRouter 404 hint is appended to the error's description as a sentence, and `hint` is
  `"openrouter_no_provider"`.
- `/api/complete`: `source` is required; a request with `source` `chat` that has any `contract` key is
  refused with 400, even `null`; a repair of an attempt while a later one is running is refused as
  `not_latest`.
- Page refusals show the tab's status as `idle` with `data-error` set to the code; MCP tool status can
  also be `stale`; the History tab refreshes its list when it is shown; a saved contract's editor is
  replaced with the server's stored text; delete confirmations for contracts and runs are inline
  (no browser dialog), while the Model settings dialog keeps its `confirm()` and `prompt()`; History's
  "Full record" Copy puts `JSON.stringify` of the parsed run file on the clipboard.
- `data-test` naming and state values (Section 11.6); the stub backend's models `json-pass`,
  `json-repair` and `slow-headers`; smoke steps S11 and S12; `install.sh --with-checks`.

Choices recorded for 0.1.2 (specification 0.1.2, Section 19.2, plus the builder's):

- Task-setting names are modelSpell's argument names everywhere: page, file, request and state.
- Three separate revisions: configuration, task settings and MCP.
- The LM Studio model lookup uses a 10-second discovery timeout; failures fall back to the string ID,
  reported in `model_lookup`.
- Upload errors and the MCP routes use machine `code` fields for every refusal.
- Operation IDs are random strings; JSON-RPC IDs and progress tokens are process-wide integers.
- Ended operations are kept for 10 minutes, at most 200.
- MCP server limit bounds: idle 1–600 s, call 1–3,600 s, discovery 1–600 s.
- The stub MCP server's tools and flags.
- Tool provenance lines quote a tool name only when it has characters outside `[A-Za-z0-9_.-]`.
- The 32 MiB JSON cap is checked from `Content-Length` before the route is looked up, so every POST
  except the upload route is capped.
- A "non-modern 400" that clears a modern-cached era is a 400 with no JSON-RPC error or with
  `-32600` (the handshake era's "Unsupported protocol version" / "Missing session ID"), or `-32022`.
  A 400 with another code (for example `-32602`) on a modern server is an ordinary `rejected`
  (`http_status`), not an era change.
- `mcp_client.classify` reports a discovery abandoned by Stop or its deadline with reason `stopped`
  or `deadline` (the `not_sent_*` reasons belong to the `tools/call` gate).
- A deadline a worker notices at a gate goes through the same abandonment as the handler's
  (commit, notice reservation, socket shutdown), so an operation is still committed once.
- MCP tokens typed in the dialog are validated like API keys (visible ASCII, no spaces) and keyed by
  the server's URL.
- The page's preview for a tool result with both text and structured content toggled between them
  with one button, and insertion records held the provenance line, target, time, size, hash and
  source. (0.1.3 replaced both: **Copy** and **Copy structured content**, and no records.)

Choices recorded for 0.1.1:

- Exit status 3 for "workspace in use"; an invalid `--workspace` is an argparse usage error (exit 2),
  through a converter in `__main__.py` (`valid_name` is a predicate and is not the argparse `type`).
- The address stays the first terminal line; the workspace line follows, marked `(created)` when
  `workspace.json` was created by this launch.
- Workspace files are indented JSON with a trailing newline, ASCII only.
- The URL rule refuses `@`, `?` and `#` anywhere in a base URL rather than parsing it.
- Temporary files are named `.<file>.<random>.tmp` and left in place if a crash interrupts a write.
- `UIServer` requires an opened workspace; whoever opens it closes it.
- `UIServer.server_bind` delegates to `TCPServer.server_bind`, preserving socket binding and the
  assigned port but using the numeric loopback address for `server_name`. It bypasses HTTPServer's
  reverse-DNS lookup, which can delay address output on macOS; no hostname is needed for this UI.
- The dialog's Save is now Apply; profile names are entered with `prompt()`; overwrite and delete
  are confirmed with `confirm()`.
- Operations that change nothing write nothing and keep the revision.
- `workspace.json` requires `created_by` and `save_runs`; `active_profile` is null or a valid name.
  Missing selection is treated as null. Profile libraries require `profiles`, not `active`; legacy
  `active` is uninterpreted metadata and preserved. Both files retained format_version 1 in 0.1.1
  (profile files moved to 2 in 0.1.2).
- Workspace profiles are read even when the workspace is read-only; local profile operations are
  refused. Invalid defaults disable only imports/exports, not local profiles or model requests.
- Invalid workspace names are also rejected by `open_workspace` itself (before creating anything).
- The manual evidence regime of the 0.1.1 specification (runbook, evidence file, walkthrough, lock
  checks, release review) was withdrawn by the owner on 27 October 2026; see "Checking a release".
  The live tests no longer print an evidence line.
- Test #19's invalid-name cases are a separate parametrised test, `test_workspace_invalid_names`,
  so the lock steps run once; test #21's session-damage case and #22's delete-active and
  unknown-field cases are separate tests too.

Choices recorded for 0.1.0:

- The page clears the model list with `replaceChildren()` rather than `innerHTML = ""`.
- `--port` values outside 0–65535 are reported like other bind failures (exit status 1).
- `build_release.py` falls back to plain extraction on Pythons without tarfile filters (3.10.0–3.10.11).
- Test #3(b) puts the CR/LF in the middle of the environment key, because modelSpell strips the
  ends; test #8's racing saves both change the model, so the winner really changes the revision.
