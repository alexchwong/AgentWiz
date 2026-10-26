# Developer guide

This file is for repository maintenance. Usage is documented in `README.md`; the build reference
for 0.1.0 is *AgentWiz 0.1.0 — Technical Specification* (version 3), which takes precedence over
the release plan (*AgentWiz 0.1.x — Release Plan*, version 8) for 0.1.0. Agent operating rules are
in `.clinerules`. Nothing in this guide, `.clinerules`, `tests/`, `scripts/` or `.github/` ships in
the release wheel.

## Quick start

From the repository root:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'          # also downloads the pinned modelSpell wheel

python -m pytest                            # offline suite (live checks skip)
agentwiz --no-browser                       # run the UI from your checkout
python scripts/stub_backend.py              # scripted backend for the walkthrough
python scripts/build_release.py --smoke     # build and smoke-test the release wheel
```

## Contents

- [Repository layout](#repository-layout)
- [The modelSpell dependency](#the-modelspell-dependency)
- [UI provenance](#ui-provenance)
- [Concurrency and resource limits](#concurrency-and-resource-limits)
- [Error reporting](#error-reporting)
- [Tests](#tests)
- [Manual walkthrough](#manual-walkthrough)
- [Co-developing with modelSpell](#co-developing-with-modelspell)
- [Branches and releases](#branches-and-releases)
- [NEWS entries](#news-entries)
- [Implementation discretion](#implementation-discretion)

## Repository layout

| Path | Ships in wheel | Purpose |
| --- | --- | --- |
| `agentwiz/__init__.py` | yes | Re-exports `__version__`; no other imports, no side effects. |
| `agentwiz/_version.py` | yes | Only version source: exactly `__version__ = "X.Y.Z"`. |
| `agentwiz/__main__.py` | yes | Command line (`agentwiz`, `python -m agentwiz`). |
| `agentwiz/model_call.py` | yes | Settings and key validation, message building, `complete()` arguments, the call, outcome classification, error descriptions. No HTTP. |
| `agentwiz/state.py` | yes | Process state: settings, revision, keys, state lock, request budget, snapshots, key source. No HTTP. |
| `agentwiz/jsonio.py` | yes | The one JSON writer. Standard library only. |
| `agentwiz/ui/server.py` | yes | The HTTP server; the only module that knows HTTP. |
| `agentwiz/ui/index.html` | yes | The page. |
| `pyproject.toml`, `README.md`, `LICENSE` | metadata | Packaging, wheel description, MIT license. |
| `tests/` | no | Offline suite, opt-in live checks, `live_evidence.md`. |
| `scripts/build_release.py` | no | Builds the release wheel from `HEAD`; `--smoke`; `--changed-since`. |
| `scripts/smoke_installed.py` | no | Smoke check (S1–S7) against an installed wheel. |
| `scripts/stub_backend.py` | no | Scripted OpenAI-compatible backend for the walkthrough. |
| `scripts/install.sh` | no | Installer behind the README one-liner; re-attached to every release as `install.sh`. |
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
| Settings, keys, revision | The state lock (a non-reentrant `threading.Lock`), held only to compare revisions, take snapshots, build views and apply changes; never during a network call. A method holding it never calls another public method (views are built by `_view_locked()`). |
| What a page is showing | Every save and completion states the revision its page was showing; a stale one gets 409. A save that changes nothing keeps the revision. |
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

## Error reporting

The page and the terminal receive only text that cannot contain a credential:

| Exception | Page receives | Terminal |
| --- | --- | --- |
| AgentWiz's own request and settings errors (`SettingsError`, `BodyError`) | 400 `{"error": "<Class>: <message>"}`; messages name fields only | nothing |
| `ModelSpellError` | `describe_error(exc, base_url)`: 400 on non-streaming routes, an `error` event while streaming | nothing |
| Anything else | 500 `{"error": "InternalError: <Class>", "code": "internal"}`, or an `error` event `InternalError: <Class>` | `[AgentWiz] internal error in <route>: <Class>` and stack locations, never messages or values |

Never add a handler that formats an exception's message unless the exception is AgentWiz's own.
All JSON output goes through `agentwiz/jsonio.py`.

The installer suppresses Python, venv and pip diagnostics, which can include environment-specific
values, and reports its own fixed failure descriptions instead. Successful installs print the
installed versions and shell-quoted launch and activation commands.

## Tests

```bash
python -m pytest                    # offline suite (15 planned tests; table-driven cases)
python -m pytest tests/test_ui.py   # one area
python -m pytest -m live -s         # live checks
```

**Testing directive (owner, 25 October 2026).**

- **T1.** Use tests sparingly. A new test needs a functional reason, stated in its pull request.
- **T2.** Never test for contents in files: no assertions on page markup, branding, docs, NEWS,
  `.clinerules`, wheel members, metadata or help text. Message wording is not asserted either;
  machine-readable fields (`code`, `outcome`, the `<Class>:` prefix of an error) are.
- **T3.** Test functionality only: drive AgentWiz through HTTP, the command line, the public
  functions of `model_call`, `state` and `jsonio`, traffic at a fake backend, or captured output.

Orderings are forced with the fake backend's `hold`/`release`/`after_release` and `wait_holding`,
with `threading.Barrier`, and by wrapping `model_call.call_model`; waits are deadlines, never the
thing tested. The helpers parse every response and event strictly, so any `NaN` or `Infinity`
AgentWiz emitted fails the test that saw it.

**Live checks** (`tests/test_live.py`) skip unless their variables are set; CI sets none:

| Backend | Variables |
| --- | --- |
| LM Studio | `AGENTWIZ_LIVE_LMSTUDIO_URL` (e.g. `http://localhost:1234/v1`), `AGENTWIZ_LIVE_LMSTUDIO_MODEL` |
| OpenRouter | `OPENROUTER_API_KEY`, `AGENTWIZ_LIVE_OPENROUTER_MODEL` (`author/slug`) |
| llama-server | `AGENTWIZ_LIVE_LLAMASERVER_URL` (e.g. `http://127.0.0.1:8080/v1`), `AGENTWIZ_LIVE_LLAMASERVER_MODEL` |

Optional: `AGENTWIZ_LIVE_TIMEOUT` (seconds the test waits for each read from AgentWiz; default 600,
because AgentWiz sends nothing until the model's first visible token, and a model that is loading,
queued or reasoning silently can take minutes) and `AGENTWIZ_LIVE_OPENROUTER_PROVIDER` (the provider
slug to pin; default the first one listed, which your OpenRouter account settings may exclude, so
OpenRouter refuses the pinned request with HTTP 404). The OpenRouter check prints the provider
slugs it found and the one it pinned.

Each prints an `EVIDENCE …` line. Copy it into `tests/live_evidence.md` under the configuration,
with the five checks (install, launch and page, connection, discovery, streamed completion).
Missing evidence is written as "missing" and never counts as a pass. Keep the README platforms
table in step with the evidence matrix (it ships, so it changes only with a release).

## Manual walkthrough

The page's JavaScript is checked by hand (W1–W14 in the techspec, Section 13.6), with
`scripts/stub_backend.py` unless a step names a real backend:

```bash
python scripts/stub_backend.py            # terminal 1: http://127.0.0.1:8099/v1
agentwiz --port 8765                       # terminal 2
```

In the page choose OpenAI-compatible, base URL `http://127.0.0.1:8099/v1`, and pick the model that
scripts the behaviour you want (`complete`, `complete-reasoning`, `empty`, `reasoning-only`,
`truncated`, `truncated-no-answer`, `truncated-reasoning`, `error-500`, `slow`, `slow-first`,
`endless`, `hold`, `nonfinite`, `bigint`).

**W9 (budget).** Configure the `hold` model in the page first, then start eight held requests
(replace `<token>` with the printed token):

```bash
TOKEN=<token>; BASE=http://127.0.0.1:8765
REV=$(curl -s -H "X-Session-Token: $TOKEN" $BASE/api/state | python3 -c 'import json,sys; print(json.load(sys.stdin)["revision"])')
for i in $(seq 8); do
  curl -sN -H "X-Session-Token: $TOKEN" -H 'Content-Type: application/json' \
       -d "{\"prompt\":\"hi\",\"revision\":$REV}" $BASE/api/complete > /dev/null &
done
```

Send from the page (busy message), stop the curls with `kill %1 %2 %3 %4 %5 %6 %7 %8`, wait about
20 s for the stub's next tokens, and Send again.

Record the walkthrough in `tests/live_evidence.md` for each release candidate.

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

- **If `vX.Y.Z` is not yet released:** builds the wheel from that commit, installs it into a fresh
  venv with `--no-index` (the pinned modelSpell wheel is still fetched) and runs the smoke check,
  then creates release `vX.Y.Z` tagged at that commit, with the wheel, the NEWS section as notes
  and a pinned install line with the sha256.
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

### Release checklist

1. On `devel`, bump `agentwiz/_version.py` and add `## X.Y.Z` to `NEWS.md`.
2. Commit, then run `python scripts/build_release.py --smoke` (builds from the committed `HEAD`).
3. Complete the walkthrough and live records in `tests/live_evidence.md`; update the README
   platforms table to match.
4. **M1** (before merging): `NEWS.md` has a `## X.Y.Z` section matching `_version.py`, with
   user-visible bullets of at most 20 words.
5. Merge `devel` into `main` (for 0.1.0: create `main` from `devel`).
6. After the release workflow, on the **published** wheel:
   - **M2:** tag `vX.Y.Z` targets the commit that passed CI; the notes match the NEWS section.
     Download the wheel and compute its sha256 locally; it equals the install line's. Record the
     commit and sha256. In a fresh venv the install line gives a working `agentwiz --version`, and so
     does the README one-liner (`curl … | bash`) in an empty directory.
   - **M3:** `python -m zipfile -l <wheel>` lists only `agentwiz/**` (including
     `agentwiz/ui/index.html`) plus `METADATA`, `WHEEL`, `RECORD`, `entry_points.txt` and
     `licenses/LICENSE` under `agentwiz-X.Y.Z.dist-info/`.
   - **M4:** its `METADATA` has `Name: AgentWiz`, `Version: X.Y.Z`, `License-Expression: MIT` and
     the `Requires-Dist` line with the pinned modelSpell URL and sha256.
7. Record M1–M4 in `tests/live_evidence.md`.

## NEWS entries

User-visible changes only, under the version heading, newest first; one change per bullet, at most
20 words. Released sections are never edited.

## Implementation discretion

Routine implementation choices are made by the implementer and recorded here or in the pull
request. Any scope change, new dependency, or behavioural departure from the specification that it
does not already record is reported to the owner before merging.

Departures from the specification approved by the owner (25 October 2026), to be recorded in the
specification's Section 16.2 at its next revision:

- **Errors inside an accepted stream.** modelSpell gives errors reported after the backend answered
  HTTP 200 (OpenRouter error chunks, LM Studio `error` events, `response.failed`) the status 200.
  `describe_error` now reports a `BackendError` with status 200 as "the backend reported an error
  during the stream" instead of "a failure (HTTP 200)". Still built from class and status only.
- **Non-ASCII base URLs are refused.** `settings_from_body` (and so discovery) rejects a base URL
  containing any non-ASCII character with a `SettingsError` (400, nothing changes). Previously
  Save succeeded and Send failed inside Python's HTTP client with `InternalError:
  UnicodeEncodeError`. Covered by a case in test #5.

Choices recorded for 0.1.0:

- The page clears the model list with `replaceChildren()` rather than `innerHTML = ""`.
- `--port` values outside 0–65535 are reported like other bind failures (exit status 1).
- `build_release.py` falls back to plain extraction on Pythons without tarfile filters (3.10.0–3.10.11).
- Test #3(b) puts the CR/LF in the middle of the environment key, because modelSpell strips the
  ends; test #8's racing saves both change the model, so the winner really changes the revision.
