# Developer guide

This file is for repository maintenance. Usage is documented in `README.md`; the build reference
for 0.1.1 is *AgentWiz 0.1.1 — Technical Specification* (version 3), which takes precedence over
Section 7 of the release plan (*AgentWiz 0.1.x — Release Plan*, version 8) and the 0.1.1 parts of
its other sections. The 0.1.0 specification is retired; what still binds from it is carried in the
0.1.1 specification's Appendix C, except that the owner has replaced the specification's manual
evidence regime (see "Checking a release"). Agent operating rules are in `.clinerules`. Nothing in
this guide, `.clinerules`, `tests/`, `scripts/` or
`.github/` ships in the release wheel.

## Quick start

From the repository root:

```bash
scripts/dev_setup.sh                        # ./.venv + editable install + pytest (downloads the pinned modelSpell wheel)
. .venv/bin/activate

python -m pytest                            # offline suite (live checks skip)
agentwiz --no-browser                       # run the UI from your checkout (workspace: ./workspaces/default)
python scripts/stub_backend.py              # scripted backend for trying the UI without a model
python scripts/build_release.py --smoke     # build, check (RC1-RC3) and smoke-test the release wheel
```

`scripts/dev_setup.sh [--python PATH]` is the one developer setup command. It refuses to run outside
a checkout, creates `./.venv` if missing, and runs `python -m pip install -e '.[dev]'`; it also
converts a `.venv` made by the user installer (wheel only, no pytest). The user installer
(`scripts/install.sh`) refuses to run in a checkout unless `--dir` is given. `./workspaces/` and `./model_profiles/` are
ignored by git in this checkout.

## Contents

- [Repository layout](#repository-layout)
- [The modelSpell dependency](#the-modelspell-dependency)
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
| `agentwiz/model_call.py` | yes | Settings and key validation, message building, `complete()` arguments, the call, outcome classification, error descriptions. No HTTP. |
| `agentwiz/state.py` | yes | Process state: settings, revision, keys, profiles, state lock, request budget, snapshots, key source. No HTTP. |
| `agentwiz/workspace.py` | yes | Workspace names, opening and locking (`flock`), reading and validating workspace.json, atomic writes. Called only from `state.py` under the state lock. No HTTP. Not imported by `import agentwiz`. |
| `agentwiz/profiles.py` | yes | Workspace profile copies and shared defaults: validation, preservation, atomic writes, fingerprints and permanent short-lived library lock. No HTTP. |
| `agentwiz/jsonio.py` | yes | The one JSON writer (`encode`, `encode_line`), and the file writer and strict reader for AgentWiz's own files (`encode_file`, `decode_file`). Standard library only. |
| `agentwiz/ui/server.py` | yes | The HTTP server; the only module that knows HTTP. |
| `agentwiz/ui/index.html` | yes | The page. |
| `pyproject.toml`, `README.md`, `LICENSE` | metadata | Packaging, wheel description, MIT license. |
| `tests/` | no | Offline suite (`test_ui.py`, `test_profiles.py`, `test_workspace.py`, `test_jsonio.py`, `test_model_call.py`), and opt-in live checks (`test_live.py`). |
| `scripts/build_release.py` | no | Builds the release wheel from `HEAD`, runs release checks RC1–RC3; `--smoke`; `--changed-since`. |
| `scripts/smoke_installed.py` | no | Smoke check (S1–S8) against an installed wheel. |
| `scripts/dev_setup.sh` | no | Developer setup: `./.venv` with an editable install and pytest. |
| `scripts/stub_backend.py` | no | Scripted OpenAI-compatible backend for trying the UI without a model. |
| `scripts/install.sh` | no | Installer behind the README one-liner; refuses to run in a checkout without `--dir`; re-attached to every release as `install.sh`. |
| `DEVEL.md`, `NEWS.md`, `.clinerules` | no | Maintenance guide, changelog, agent rules. |
| `.github/workflows/` | no | `tests.yml` (push, PR) and `release.yml` (push to `main`). |

Shipped code must run on Python 3.10: `from __future__ import annotations` for `X | None` hints,
and no 3.11-only modules or APIs (`tomllib`, `datetime.UTC`, exception groups).

## The modelSpell dependency

AgentWiz depends on the modelSpell **v0.1.0 release wheel**, pinned by URL and sha256 in
`pyproject.toml`. Adding any dependency, required or optional, needs the owner's approval.

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

## UI provenance

`agentwiz/ui/server.py`, `agentwiz/ui/index.html`, the test helpers in `tests/conftest.py` and
`scripts/build_release.py`/`smoke_installed.py` were copied from modelSpell at commit
`1759c6e3c28a637731434a73c46088048583e358` and adapted (each file names its source). Later changes to
modelSpell's copies are ported only by deliberate review, never synced automatically.

## Concurrency and resource limits

| Shared thing | Rule |
| --- | --- |
| Settings, keys, profiles, revision | The state lock (a non-reentrant `threading.Lock`), held to compare revisions, take snapshots, build views, apply changes, and read-validate-change-replace `profiles.json`; never during a network call. A method holding it never calls another public method (views are built by `_view_locked()`). One revision covers settings, keys and profiles. |
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
handles workspace.json, its lifetime lock, and the reusable atomic writer. Both libraries use
`{"format_version": 1, "profiles": {...}}`, preserve unknown fields and never receive session keys.
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
`conflicts` and `unresolved_active`. Conflicts compare the six validated ModelSettings fields,
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

```bash
python -m pytest                    # offline suite (planned: 20 tests; table-driven cases)
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
  functions of `model_call`, `state`, `workspace`, `profiles` and `jsonio`, traffic at a fake backend, or
  captured output.

Tests never write outside `tmp_path`: `make_ui` opens a workspace under `tmp_path` for every server
(including the live tests'), and a defaults library under `tmp_path`; command-line tests launch with their working directory there. "File left
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

All accept `AGENTWIZ_LIVE_TIMEOUT` (seconds per read from AgentWiz, default 600: AgentWiz sends
nothing until the model's first visible token, so a loading or silently reasoning model can take
minutes).

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
2. Run `python scripts/build_release.py --smoke` (builds from the committed `HEAD`, runs RC1–RC3,
   then the smoke test S1–S8).
3. Try the UI with README's "Trying it out".
4. Merge `devel` into `main`. The release workflow reruns the tests and RC1–RC3, then publishes.

## NEWS entries

User-visible changes only, under the version heading, newest first; one change per bullet, at most
20 words. Released sections are never edited.

## Implementation discretion

Routine implementation choices are made by the implementer and recorded here or in the pull
request. Any scope change, new dependency, or behavioural departure from the specification that it
does not already record is reported to the owner before merging.

Departures from the plan, including the two approved for 0.1.0 (errors inside an accepted stream
described without "HTTP 200"; non-ASCII base URLs refused), are recorded in the 0.1.1
specification's Section 16.2.

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
  `active` is uninterpreted metadata and preserved. Both files retain format_version 1.
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
