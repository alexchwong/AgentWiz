# Specification lineage and historical decisions

Historical context migrated from the developer guide. This is not a current build checklist
or an instruction to resume an old release. `../DEVEL.md` owns agent policy; `architecture.md`
describes behavior contracts and `releases.md` describes current release operations.
Historical choices may have been superseded; do not restore retired behavior from this record.

## Specification lineage

Usage is documented in `../README.md`; the build reference
for 0.1.4 is *AgentWiz 0.1.4 — Technical Specification* (version 5), which takes precedence over
release plan version 13; its Section 24 lists every departure, and `../HANDOVER.md` lists the ones found
while building it. The build reference for 0.1.3 was *AgentWiz 0.1.3 — Technical Specification* (version 4), which took precedence over
Section 8 of the release plan (*AgentWiz 0.1.x — Release Plan and 0.2 Design*, version 11) and over
Sections 11–12 of version 10.1, which the plan carries forward; its Section 22 lists every
departure. The build reference for 0.1.2 was *AgentWiz 0.1.2 — Technical Specification* (version
5.1), whose build process (D42, D44, D48–D51), error-text rule, MCP design and task settings stand
unless the 0.1.3 specification says otherwise. Its companion, *AgentWiz
attachment interface* (version 3, draft; `interface_version` 1), is kept as
`attachment_interface.md`. The plan carries everything that still binds from the 0.1.0 and
0.1.1 specifications (plan 2.9, 6.13), except that the owner has replaced the 0.1.1 specification's
manual evidence regime (see "Historical release-review directions").

## Historical release-review directions

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

## Historical build procedures

**Building 0.1.3.** Phases P, 0, A, B, D and E (specification 0.1.3, Section 21); the 0.1.2 rules
in this historical record applied unchanged. The gate in every phase is the whole offline suite on the prepared
interpreter with `.[dev,checks]` and Chromium installed; Phase E's also runs on Python 3.10 when it can
be provided, then the offline release smoke in `releases.md` on a throwaway local commit that is never pushed.
The hand-over is a zip of new and changed files, `DELETIONS.txt` and `HANDOVER.md`; the owner commits.

**Building 0.1.2 (D48–D51).** The build runs as phases (P, 0, A, B, C1, C2, D), each ending at a
checkpoint gate: `python -m pytest -q -m "not live"`, passing with no failures, errors or skips.
When a gate passes, the build continues without waiting; a gate is never passed by skipping,
weakening or deleting a test. Phase P prepares the environment (interpreters, the dev install,
`hatchling`, the hash-checked modelSpell wheel, the test certificate) and is the only phase that may
use the network beyond loopback. A check whose dependency the environment cannot provide is
**blocked**, never counted as passed. The historical Phase H manual tour is superseded by the
owner direction under "Historical release-review directions"; live checks remain optional.

**Offline Phase D.** With `hatchling` installed in the interpreter and a local copy of the pinned

## Building 0.1.4

The build order is specification Section 23, in gated phases (P prepare, 0 baseline, A definitions, B
runs of blocks, C the page, D the tutorial, E finish); every gate is `python -m pytest -q -m "not live"`
on Python 3.13 and 3.10 with no failures, errors or skips. The checkpoint log is in `HANDOVER.md`
(written as the build went; the original procedure referred to `BUILD_LOG_0.1.4.md`'s "NEXT ACTION"
for interrupted builds; that working log is not present in this checkout).
The final steps are, offline and on a throwaway local commit that is never pushed,
`python scripts/build_release.py --smoke --no-isolation --modelspell-wheel <the 0.3.0 wheel>`.

## Recorded implementation choices

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
  checks, release review) was withdrawn by the owner on 27 October 2026; see "Historical release-review directions".
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


## Historical NEWS exception

One owner-approved exception (D43): 0.1.2 moved the "Unreleased" macOS startup bullet under
`## 0.1.1`, because it shipped in the 0.1.1 wheel. This is not permission to edit other released sections.

## modelSpell upgrade error-text review

The error-text re-check was done for the move from 0.2.1 to 0.3.0
(specification 0.1.3, Section 3.2): `errors.py` adds only `Cancelled.sent` and a docstring and no
error class; the one new pre-send message, `ConfigError("cancel must be None or a CancelHandle")`,
names an argument; `Cancelled` messages are fixed; the watched transport's new failures
(`TimeoutError`, `OSError` from the non-blocking connect) are mapped by the existing handlers to
`RequestTimeout` and `ConnectError`, which are already fixed-description errors. Nothing moved
groups. (For 0.1.0 to 0.2.1 the same check, specification 0.1.2 Section 3.2, found that `_http.py`,
`errors.py`, `_sse.py` and `_discovery.py` were unchanged and every new error was a pre-send
`ConfigError` or `UnsupportedSetting` naming arguments, routes and rules, never a value.)

## Initial branch creation

The 0.1.0 procedure created `main` by merging `devel`, so the release workflow could not
publish an unfinished scaffold. This is historical context, not a current branch-creation task.
