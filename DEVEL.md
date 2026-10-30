# Developer guide

This file is for repository maintenance. Usage is documented in `README.md`; the build reference
for 0.1.2 is *AgentWiz 0.1.2 — Technical Specification* (version 5.1), which takes precedence over
Sections 8–10 of the release plan (*AgentWiz 0.1.x — Release Plan*, version 10.1) and the 0.1.2
parts of its other sections; its Section 19 lists every departure. Its companion, *AgentWiz
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
scripts/dev_setup.sh                        # ./.venv + editable install + pytest (downloads the pinned modelSpell wheel)
. .venv/bin/activate

python -m pytest -q -m "not live"          # the offline suite (the gate; live checks deselected)
agentwiz --no-browser                       # run the UI from your checkout (workspace: ./workspaces/default)
python scripts/stub_backend.py              # scripted backend for trying the UI without a model
python scripts/stub_mcp_server.py           # scripted MCP server (both protocol eras) for the Tools panel
python scripts/build_release.py --smoke     # build, check (RC1-RC3) and smoke-test the release wheel
```

`scripts/dev_setup.sh [--python PATH]` is the one developer setup command. It refuses to run outside
a checkout, creates `./.venv` if missing, and runs `python -m pip install -e '.[dev]'`; it also
converts a `.venv` made by the user installer (wheel only, no pytest). The user installer
(`scripts/install.sh`) refuses to run in a checkout unless `--dir` is given. `./workspaces/`, `./model_profiles/` and
`./mcp_servers/` are ignored by git in this checkout.

## Contents

- [Repository layout](#repository-layout)
- [The modelSpell dependency](#the-modelspell-dependency)
- [MCP protocol versions](#mcp-protocol-versions)
- [The attachment interface](#the-attachment-interface)
- [UI provenance](#ui-provenance)
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
| `agentwiz/model_call.py` | yes | Settings and key validation, message building, task arguments (`task_arguments`: route support, `require_parameters`, `check_settings`), the LM Studio model lookup, the call, outcome classification, error descriptions. No HTTP. |
| `agentwiz/task_settings.py` | yes | `TaskSettings`, its rules, and `task_settings.json`. No HTTP. |
| `agentwiz/state.py` | yes | Process state: settings, keys, profiles, saved task settings, the MCP library and tokens, the three revisions, state lock, request budget, snapshots, key and token sources. No HTTP. |
| `agentwiz/workspace.py` | yes | Workspace names, opening and locking (`flock`), reading and validating workspace.json, atomic writes. Called only from `state.py` under the state lock. No HTTP. Not imported by `import agentwiz`. |
| `agentwiz/profiles.py` | yes | Workspace profile copies and shared defaults (format version 2; reads 1): validation, preservation, atomic writes, fingerprints and permanent short-lived library lock. No HTTP. |
| `agentwiz/jsonio.py` | yes | The one JSON writer (`encode`, `encode_line`); the file writer and strict reader for AgentWiz's own files (`encode_file`, `decode_file`); the exact-number reader and writer for MCP traffic (`decode_exact`, `encode_exact`). Standard library only. |
| `agentwiz/attachments.py` | yes | The basic attachment reader (`.txt`, `.md`, `.csv`, `.json`) and `check_document`, implementing `docs/attachment_interface.md`. No HTTP. |
| `agentwiz/insertion.py` | yes | Provenance lines and preview sizes for files and tool results; `REQUEST_LIMIT`. No HTTP. |
| `agentwiz/mcp_library.py` | yes | The shared MCP server library (`servers.json`, `.servers.lock`): validation, fingerprints, atomic writes. No HTTP server code. |
| `agentwiz/mcp_client.py` | yes | Streamable HTTP MCP client, both eras, standard library only: probe and era cache, discovery entries, legacy sessions and the session lock, gates, reply reading with limits, `classify`. An HTTP *client*; knows nothing of AgentWiz's server. |
| `agentwiz/mcp_ops.py` | yes | Operations: worker threads, one commit per operation, abandonment and socket shutdown, capacity, maintenance (notices, `DELETE`s), retention. No HTTP server code. |
| `agentwiz/tool_forms.py` | yes | Form specifications from tool input schemas. No HTTP. |
| `agentwiz/ui/server.py` | yes | The HTTP server; the only module that knows HTTP. |
| `agentwiz/ui/index.html` | yes | The page. |
| `pyproject.toml`, `README.md`, `LICENSE` | metadata | Packaging, wheel description, MIT license. |
| `tests/` | no | Offline suite (`test_ui.py`, `test_profiles.py`, `test_workspace.py`, `test_jsonio.py`, `test_model_call.py`, `test_task_settings.py`, `test_attachments.py`, `test_mcp_client.py`, `test_mcp_ops.py`, `test_tool_forms.py`, `test_stub_mcp_server.py`), opt-in live checks (`test_live.py`), and the test-only TLS certificate (`tests/fixtures/tls/`). |
| `docs/attachment_interface.md` | no | The attachment interface (companion document, version 3, draft). |
| `scripts/build_release.py` | no | Builds the release wheel from `HEAD`, runs release checks RC1–RC3; `--smoke`; `--no-isolation`; `--modelspell-wheel` (offline smoke); `--changed-since`. |
| `scripts/smoke_installed.py` | no | Smoke check (S1–S10) against an installed wheel. |
| `scripts/dev_setup.sh` | no | Developer setup: `./.venv` with an editable install and pytest. |
| `scripts/stub_backend.py` | no | Scripted OpenAI-compatible backend for trying the UI without a model; logs the sampling fields each request carries. |
| `scripts/stub_mcp_server.py` | no | Scripted MCP server (`--era dual|modern|legacy`, `--token`) for trying the Tools panel; logs every request, disconnect, notice and `DELETE`. |
| `scripts/install.sh` | no | Installer behind the README one-liner; refuses to run in a checkout without `--dir`; re-attached to every release as `install.sh`. |
| `DEVEL.md`, `NEWS.md`, `.clinerules` | no | Maintenance guide, changelog, agent rules. |
| `.github/workflows/` | no | `tests.yml` (push, PR) and `release.yml` (push to `main`). |

Shipped code must run on Python 3.10: `from __future__ import annotations` for `X | None` hints,
and no 3.11-only modules or APIs (`tomllib`, `datetime.UTC`, exception groups).

## The modelSpell dependency

AgentWiz depends on the modelSpell **v0.2.1 release wheel**, pinned by URL and sha256 in
`pyproject.toml`. The error-text re-check below was done for the move from 0.1.0 to 0.2.1
(specification 0.1.2, Section 3.2): `_http.py`, `errors.py`, `_sse.py` and `_discovery.py` are
unchanged, and every new error is a pre-send `ConfigError` or `UnsupportedSetting` naming arguments,
routes and rules, never a value. Nothing moved groups. Task settings use `supported_settings()` and
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
| Model requests | At most 8 in progress (`RequestBudget`), counted from acceptance until the handler returns, including after the page stopped or closed; the ninth gets 503; nothing is queued. |
| Writes to a page | A 30-second socket timeout on every page connection, set in `Handler.setup()`. One writer per stream, on the handler's thread. |

**What frees a budget slot:** the model call completes or raises (including modelSpell's 900 s
per-read timeout); after the page has gone, a text or reasoning fragment whose write fails (usually
the first or second after the close, because TCP accepts the first write to a closed peer); or a
write to a page that has stopped reading times out after 30 s.

**What does not:** modelSpell 0.1.0 has no overall deadline, so a slot stays occupied while a
backend sends only keepalive comments, empty deltas or usage-only chunks, while it sends bytes more
often than the read timeout, or while DNS resolution hangs. Before the first token, Stop takes
effect only when output arrives. If all 8 slots stay occupied, restarting is the only recovery.
Discovery calls and connection threads are not budgeted.

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
tests must run unattended; live checks and the README tour are the owner's after handover.

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

**The page's JavaScript is not tested** (O1). Its check is README's "Trying it out" tour, which the
owner follows after a build and before any pull request (D50).

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

Loopback startup is tested with `socket.getfqdn` forced to fail: constructing a server and serving
authenticated state must not require hostname resolution. This guards against macOS resolver delays
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

| Any MCP server (`test_live_mcp`) | `AGENTWIZ_LIVE_MCP_URL`, `AGENTWIZ_LIVE_MCP_TOOL`, `AGENTWIZ_LIVE_MCP_ARGUMENTS` (the arguments as JSON text); optional `AGENTWIZ_LIVE_MCP_TOKEN`. Example, with web-retrieval-mcp running: `http://127.0.0.1:8765/mcp`, `search_web`, `{"query":"model context protocol"}` |

The model checks send task settings (`temperature`, `max_tokens`). All accept
`AGENTWIZ_LIVE_TIMEOUT` (seconds per read from AgentWiz, default 600: AgentWiz sends nothing until
the model's first visible token, so a loading or silently reasoning model can take minutes). The
live checks are human checks: they never run in a checkpoint gate, which deselects them with
`-m "not live"` (unsetting `AGENTWIZ_LIVE_*` alone would not be enough: the OpenRouter check reads
`OPENROUTER_API_KEY`).

**Building 0.1.2 (D48–D51).** The build runs as phases (P, 0, A, B, C1, C2, D), each ending at a
checkpoint gate: `python -m pytest -q -m "not live"`, passing with no failures, errors or skips.
When a gate passes, the build continues without waiting; a gate is never passed by skipping,
weakening or deleting a test. Phase P prepares the environment (interpreters, the dev install,
`hatchling`, the hash-checked modelSpell wheel, the test certificate) and is the only phase that may
use the network beyond loopback. A check whose dependency the environment cannot provide is
**blocked**, never counted as passed. Checks that need a person (the README tour, the live checks)
come last, as Phase H, before anything is committed or a pull request is opened.

**Offline Phase D.** With `hatchling` installed in the interpreter and a local copy of the pinned
modelSpell wheel:

```bash
python scripts/build_release.py --smoke --no-isolation --modelspell-wheel ../agentwiz-prep/wheels/modelspell-0.2.1-py3-none-any.whl
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
smoke test, which run before anything is published. The maintainer tries the UI using README's
"Trying it out" section. Live checks against real backends are optional.

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
2. **Phase H, before anything is committed to GitHub or a pull request opened** (D50): on a local
   working copy with the build applied, follow README's "Trying it out" against `stub_backend.py`
   and `stub_mcp_server.py`; optionally run the live checks and the web-retrieval-mcp example. A
   failure goes back to the implementer as a defect report.
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
- The page's preview for a tool result with both text and structured content toggles between them
  with one button; insertion records hold the provenance line, target, time, size, hash and source.

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
