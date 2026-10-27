# Live and manual checks — runbook

The one place to follow for every manual and live check: the live tests against real backends, the
platform configurations, the workspace lock checks, the stub walkthrough and the release review.
Results go in `tests/live_evidence.md` (Section 8 below says how).

Each release's specification names which steps that release requires; this runbook says how to do
them. For 0.1.1 the required evidence is: the walkthrough steps W1, W2, W4, W8 and W15–W20, and one
five-check record each for OpenRouter, LM Studio and llama-server, all on the **release-candidate
route**. L1, L2 and further platforms are optional coverage.

Keep this file in step with `tests/test_live.py`, the supported backends and
`scripts/stub_backend.py`: a change to any of them updates this file in the same change.

## Contents

1. [Which build is being checked](#1-which-build-is-being-checked)
2. [The five evidence checks](#2-the-five-evidence-checks)
3. [Per backend](#3-per-backend)
4. [Platform configurations](#4-platform-configurations)
5. [Workspace lock checks (L1, L2)](#5-workspace-lock-checks-l1-l2)
6. [Stub walkthrough (W1–W20)](#6-stub-walkthrough-w1w20)
7. [Release review (M1–M4)](#7-release-review-m1m4)
8. [Recording](#8-recording)

## 1. Which build is being checked

The tests and the build must come from the same commit: a release's tests construct the server the
way that release does, so they cannot exercise another release's wheel.

| Route | Use | Commands |
| --- | --- | --- |
| Development | Day-to-day checks of the working tree. **Not release evidence.** | `scripts/dev_setup.sh`, then `. .venv/bin/activate` and `python -m pytest -m live -s -k <backend>` from the checkout root. |
| Release candidate | Release evidence. | See below. |
| Published release | Re-checking a release after publication, for example on a new platform. | See below. |

**Release candidate.** In a clean checkout of the candidate commit (`git status --porcelain` prints
nothing):

```bash
python scripts/build_release.py --output-dir dist        # prints the wheel path and its sha256
python3 -m venv <cand>
<cand>/bin/python -m pip install dist/agentwiz-X.Y.Z-py3-none-any.whl pytest
cd tests && <cand>/bin/python -m pytest -m live -s -k <backend> .
```

**Published release.**

```bash
git worktree add <dir> vX.Y.Z                            # that release's tests
scripts/install.sh --version X.Y.Z --dir <venv>
<venv>/bin/python -m pip install pytest
cd <dir>/tests && <venv>/bin/python -m pytest -m live -s -k <backend> .
```

**Run from `tests/` on both installed routes.** From the checkout root, `python -m pytest` puts the
checkout first on `sys.path` and imports the checkout's `agentwiz/` instead of the installed wheel.
The `EVIDENCE` line records which was imported (`agentwiz from installed` or `agentwiz from
checkout`) and the commit of the tests that ran (`commit <sha>`, with `+dirty` for uncommitted
changes). A record must match its route: candidate and published records say `installed` and a
commit without `+dirty`.

`<env>` below is the environment of the route in use (`.venv`, `<cand>` or `<venv>`). On the
candidate and published routes, run from the `tests/` folder of the matching commit and end the
command with ` .`; on the development route, run from the checkout root.

## 2. The five evidence checks

A configuration record has five checks, all on the same build, in one session:

| Check | Pass when |
| --- | --- |
| Install | The build under test is installed into a fresh virtual environment by its route (Section 1), and `agentwiz --version` from that environment prints the AgentWiz and modelSpell versions the record names. |
| Launch and page | `agentwiz --workspaces-dir <scratch folder>` from that environment prints the address, and the page opens in a browser on the configuration's machine (a Windows browser for the WSL2 configurations) with both versions in the header. |
| Connection | With the configuration's networking (and, where LM Studio's authentication is enabled, its token entered as the key), **Load models** in the page succeeds. |
| Discovery | **Load models** lists the record's model (any model for llama-server); for OpenRouter, **Load providers** lists providers for it. The live test's discovery assertions pass. |
| Streamed completion | One Send from the page streams and ends with a classified outcome, not an error; the live test passes on the same build and prints the `EVIDENCE` line. |

Install and launch-and-page are manual only. Connection, discovery and streamed completion each have
two parts, **both on the same build**: the live test (the server side, through a test server) and
the page operation in the table (Load models, Load providers, one Send) in a browser on the
configuration's machine. A check passes only when both parts do. The page operations take a minute
per backend; do them in the same session as the live test.

A record with only the live test marks install, launch-and-page and the page parts of the other
three "missing", and the configuration is not "verified". The stub walkthrough does not stand in for
a page operation, because it runs against the stub, not the record's backend.

## 3. Per backend

All three tests accept `AGENTWIZ_LIVE_TIMEOUT` (seconds to wait for each read from AgentWiz,
default 600). AgentWiz forwards nothing until the model's first visible token, so a model that is
loading, queued or reasoning silently can take minutes. Each test runs against a temporary
workspace and never touches `./workspaces`.

| Backend | Prerequisites and start | Model ID | Command |
| --- | --- | --- | --- |
| LM Studio | LM Studio 0.4.0 or later (0.3.29 is modelSpell's minimum; Load models and reasoning "off" need 0.4.0). Start the server from the app's Developer page, or `lms server start`; load the model (`lms load <model>`) or let the first request load it. For the WSL2 configurations, use the base URL from Section 4. | `curl <base URL>/models` and use an `id` (`lms ls` also lists models). | `AGENTWIZ_LIVE_LMSTUDIO_URL=<base URL, e.g. http://localhost:1234/v1> AGENTWIZ_LIVE_LMSTUDIO_MODEL=<id> [AGENTWIZ_LIVE_LMSTUDIO_KEY=<token>] <env>/bin/python -m pytest -m live -s -k lmstudio` |
| OpenRouter | An API key with credit, and account privacy and provider settings that allow at least one provider for the model. | `author/slug` from openrouter.ai/models, or `curl https://openrouter.ai/api/v1/models`. | `OPENROUTER_API_KEY=<key> AGENTWIZ_LIVE_OPENROUTER_MODEL=<author/slug> [AGENTWIZ_LIVE_OPENROUTER_PROVIDER=<slug>] <env>/bin/python -m pytest -m live -s -k openrouter` |
| llama-server | A llama.cpp build with `llama-server` and a GGUF model: `llama-server -m <model.gguf> --port 8080`. | `curl http://127.0.0.1:8080/v1/models` and use the `id`. | `AGENTWIZ_LIVE_LLAMASERVER_URL=http://127.0.0.1:8080/v1 AGENTWIZ_LIVE_LLAMASERVER_MODEL=<id> <env>/bin/python -m pytest -m live -s -k llamaserver` |

Optional variables:

- `AGENTWIZ_LIVE_LMSTUDIO_KEY`: LM Studio's authentication token, needed when its authentication is
  on (always for "WSL2, LM Studio on Windows, NAT"). Passed as the key for the configuration and
  for discovery; never printed.
- `AGENTWIZ_LIVE_OPENROUTER_PROVIDER`: the provider slug to pin (default: the first one listed).
  The test prints the slugs it found and the one it pinned, with fallbacks off.

**A pass** prints `1 passed`, then the `EVIDENCE` line (for OpenRouter, also the provider slugs found
and the one pinned). Copy the `EVIDENCE` line into the record.

**Common failures:**

| Symptom | Meaning |
| --- | --- |
| Skipped | A required variable is unset; the skip message names them (`python -m pytest -m live -rs` lists the reasons). |
| `TimeoutError` | Nothing arrived within `AGENTWIZ_LIVE_TIMEOUT`; usually a model loading or queued. Raise the timeout. |
| `... is not among LM Studio's models` / `not in OpenRouter's model list` | Wrong model ID (OpenRouter needs `author/slug`). |
| OpenRouter error event, with the hint about `AGENTWIZ_LIVE_OPENROUTER_PROVIDER` | The pinned provider was refused, often HTTP 404 because the account's settings exclude it. Set the variable to another slug from the printed list. |
| `answered by ..., but ... is listed as ...` | The streamed provider did not match the pinned one: modelSpell's verification V1 failed. Record it and report it; do not retry until it passes. |
| `AuthError` from LM Studio | Authentication is enabled; set `AGENTWIZ_LIVE_LMSTUDIO_KEY`. |
| `ConnectError` | The backend is not reachable at that URL from this machine (for WSL2, check the networking setup in Section 4). |
| 400 mentioning `base_url must not contain` | The URL has `@`, `?` or `#`; credentials go in the key variable, not the URL. |

**The first OpenRouter record** is also the first check of modelSpell's verification V1, which was
never run for want of a key: pinning by endpoint `tag` routes the request, and the streamed
`provider` equals the listing's `provider_name`. Say so in that record's notes.

## 4. Platform configurations

| Configuration | Backend test to run |
| --- | --- |
| Linux, LM Studio on the same machine | lmstudio, `http://localhost:1234/v1` |
| Linux, llama-server | llamaserver |
| macOS, LM Studio on the same machine | lmstudio, `http://localhost:1234/v1` |
| WSL2, LM Studio on Windows, mirrored networking | lmstudio, `http://localhost:1234/v1` (or `http://127.0.0.1:1234/v1`) |
| WSL2, LM Studio on Windows, NAT with "Serve on Local Network" | lmstudio, `http://<Windows host address>:1234/v1`, with `AGENTWIZ_LIVE_LMSTUDIO_KEY` |
| WSL2, LM Studio headless (`llmster`) inside WSL2 | lmstudio, `http://localhost:1234/v1` |
| OpenRouter (any one platform) | openrouter |

Native Windows is not supported.

**WSL2 setup** (as in the README):

- **A. LM Studio desktop on Windows, AgentWiz inside WSL2.**
  - With **mirrored networking** (Windows 11 22H2 or later; `networkingMode=mirrored` under
    `[wsl2]` in `.wslconfig`), WSL2 reaches Windows services on `127.0.0.1`, so the default
    `http://localhost:1234/v1` works. Mirrored mode does not support the IPv6 loopback `::1`, so if
    `localhost` misbehaves, use `http://127.0.0.1:1234/v1`.
  - With the **default NAT networking**, find the Windows host's address with
    `ip route show | grep -i default | awk '{print $3}'` and turn on LM Studio's "Serve on Local
    Network". That exposes LM Studio to your local network, so enable LM Studio's authentication
    first and use its token as the key. A Windows firewall rule may be needed.
- **B. LM Studio headless (`llmster`) inside WSL2.** Install with
  `curl -fsSL https://lmstudio.ai/install.sh | bash` and start it with `lms server start`.
  `localhost` works directly. GPU access depends on WSL2's GPU passthrough, and models are stored
  separately from any Windows install.

In both setups a Windows browser can open the address AgentWiz prints, because WSL forwards
localhost from Windows. Opening the browser automatically from WSL2 often does nothing; copy the
printed address instead.

## 5. Workspace lock checks (L1, L2)

CI's macOS jobs cover the lock on macOS (tests #19 and #24). WSL2 cannot run in CI, so check it by
hand. Optional for 0.1.1; record "missing" when no WSL2 machine is available.

| Step | Where | Procedure | Pass when |
| --- | --- | --- | --- |
| L1 | WSL2, a folder on WSL2's own filesystem | `agentwiz --no-browser --workspaces-dir ~/aw-lock &`; `agentwiz --no-browser --workspaces-dir ~/aw-lock; echo $?`; `kill -9 %1`; `agentwiz --no-browser --workspaces-dir ~/aw-lock` | The second exits 3; after `kill -9`, the third starts. |
| L2 | WSL2, a folder under `/mnt/c` | The same with `--workspaces-dir /mnt/c/Users/<you>/aw-lock` | The same. If the first launch fails with the lock-support error, record that: `/mnt/c` is then unsupported for workspaces, and the README must say so. |

Until L2 passes, the README states that `/mnt/c` workspaces are unverified.

## 6. Stub walkthrough (W1–W20)

The page's JavaScript is checked by hand. Run with `scripts/stub_backend.py` unless a step names a
real backend, against the build of the route in use (the candidate route for release evidence):

```bash
python scripts/stub_backend.py                                  # terminal 1: http://127.0.0.1:8099/v1
agentwiz --port 8765 --workspaces-dir <scratch folder>          # terminal 2
```

In the page choose OpenAI-compatible, base URL `http://127.0.0.1:8099/v1`, and pick the model that
scripts the behaviour you want: `complete`, `complete-reasoning`, `empty`, `reasoning-only`,
`truncated`, `truncated-no-answer`, `truncated-reasoning`, `error-500`, `slow`, `slow-first`,
`endless`, `hold`, `nonfinite`, `bigint`.

**Required for 0.1.1:** W1, W2, W4, W8 and W15–W20. W3 was 0.1.0 only.

| Step | Procedure | Pass when |
| --- | --- | --- |
| W1 | `agentwiz`; then `agentwiz --no-browser --port 8765`; then Ctrl-C; then `agentwiz --version`. | The browser opens at the printed address; with `--no-browser` none opens and the address works when pasted; Ctrl-C exits cleanly; versions print. |
| W2 | Look at the page title, header, terminal lines and `agentwiz --help`. | All read AgentWiz; both versions in the header; terminal lines prefixed `[AgentWiz]`. |
| W3 | (0.1.0 only.) Run the modelSpell demo from a checkout at `1759c6e` (`python -m ui`) beside AgentWiz. Compare every panel, setting and interaction. | Everything present in both; the only differences are AgentWiz's documented adaptations. |
| W4 | Settings dialog: switch kinds; Load models on the stub; Load providers on OpenRouter (if a key is available); enter a key and **Apply**; Clear it and **Apply**, with and without `OPENROUTER_API_KEY` set; try a key with a space in it. | Provider row only for OpenRouter; reasoning row hidden for OpenAI-compatible; models listed; header key source changes `session` → `none` or `env OPENROUTER_API_KEY`; the key with a space is refused with a message that does not show it. |
| W5 | Send to `complete-reasoning`. | Reasoning and output fill incrementally; the Result panel shows the event as received; status line order: outcome, time, transport, finish, usage, cost, provider; missing values `?`. |
| W6 | Send to `empty`, `reasoning-only`, `truncated`, `truncated-no-answer`, `truncated-reasoning`, `error-500`; press Stop on `endless`. | Each of the seven outcomes shows its own label and style, including the reasoning suffix for `truncated-reasoning`; `error-500` shows the fixed `BackendError` description. |
| W7 | Send to `slow` and press Ctrl/Cmd+Enter repeatedly while it runs. | The stub logs exactly one request. |
| W8 | Two tabs, A and B. (a) In B, change the model and **Apply**; in A, Send. (b) Open A's dialog and change a field without applying it (for example `max_tokens`). In B, make a *different* real change (another model) and **Apply**. In A, **Apply**. | (a) A shows the stale-settings message on Send, its header reloads, and the stub logs nothing from A. (b) A's Apply shows the dialog message; A's dialog still shows A's unapplied edit; A's header shows B's latest settings; the settings are B's (A's edit was not applied). Case (b) needs a real change in B: an Apply that changes nothing keeps the revision, so A would not be stale. |
| W9 | Configure the `hold` model in the page, start 8 held requests with the loop below, then Send from the page; stop the curls with `kill %1 %2 %3 %4 %5 %6 %7 %8`; wait about 20 s; Send again. | The page shows the busy message; after the stub's next tokens the slots free and Send works. |
| W10 | Send to `endless`, press Stop. | The Stop message reads: "Stopped. AgentWiz closes the model connection when more output arrives, which can take a while; whether the backend stops generating is up to the backend."; the stub logs the disconnect within a few token intervals. |
| W11 | Send to `slow-first`, press Stop at once. | The page shows the Stop message and Send is enabled at once; the stub logs the disconnect only after its first tokens (about 20 s). |
| W12 | Send to `nonfinite`, then to `bigint`. | No page error. For `nonfinite`, the status line notes replaced values and the Result panel shows the `null`s and the list. For `bigint`, the Result panel shows `9007199254740993` digit for digit, and the status line shows `in=see Result`. |
| W13 | Send to `endless`; while it streams, stop AgentWiz with Ctrl-C in its terminal. | The page shows the interrupted-stream message in error style, keeps the partial output, enables Send, and resends nothing. |
| W14 | Send to `complete`; when the result shows, try Stop, then stop AgentWiz with Ctrl-C. | Stop is disabled once the result arrives; the page still shows `Complete` with its status line after AgentWiz stops. |
| W15 | In an empty directory: `agentwiz --no-browser`; Ctrl-C; launch again. Then `agentwiz --workspace 'bad/name'`. | The address is the first line, then the information line, then the workspace line with `(created)` on the first launch only. The header shows `workspace: default`, with the path on hover. The bad name gives a usage error, exit status 2, and creates nothing. |
| W16 | Configure the stub (`complete`) and **Save as profile…** `stub`. Configure OpenRouter (or LM Studio) with a key and **Save as profile…** `remote`. Switch between them with the select. Change the model and **Apply**. **Save profile**. Choose `(no profile)`. Select `stub`, then **Delete** it. | The dialog and header follow each selection; `remote` shows the key as set for its endpoint. After Apply the header says `(modified)`; after Save profile it does not. `(no profile)` shows `no profile` with settings unchanged. Delete asks first, and the header shows `no profile` afterwards. |
| W17 | Select `remote`; Ctrl-C; launch again. | `remote` is active with its settings; the key shows `none` (or the environment variable); after re-entering the key, Send works. |
| W18 | Two tabs, A and B, with at least two profiles. Three independent cases; before each, reload both tabs, open A's dialog, then make one change in B (**Save as profile…** a new name), so that A is stale for exactly that attempt. (a) In A, select a different profile. (b) In A, **Save profile**. (c) In A, **Delete** a profile. | In each case A shows the stale-settings message in its dialog, A's header and profile list refresh to B's state, and the profile list, active profile and settings are exactly what B left (A's attempt changed nothing). A's first attempt after the refresh is not stale; it is not part of this step. |
| W19 | With AgentWiz running, start a second `agentwiz` on the same workspace in another terminal; then `echo $?`. | The second prints that the workspace is in use, with the first's pid, host and start time, and exits with status 3. The first is unaffected. |
| W20 | Stop AgentWiz. Set a profile's `max_tokens` to `"64"` in `profiles.json`; launch. Restore the file without restarting and try the profile controls; then restart. Then set `workspace.json`'s `format_version` to `2`, and launch. Restore it afterwards. | First launch: a terminal warning and a page banner naming `profiles.json` and the field and saying to restart after repairing; profile controls disabled; Apply and Send work. After the repair, the controls stay disabled until the restart, then work. With `format_version` 2: the header says `(read-only)` and the banner names `workspace.json`. Neither file was rewritten (its modification time is unchanged: `stat -c %Y <file>`, or `stat -f %m <file>` on macOS). |

**W9's loop** (replace `<token>` with the printed token; AgentWiz on port 8765):

```bash
TOKEN=<token>; BASE=http://127.0.0.1:8765
REV=$(curl -s -H "X-Session-Token: $TOKEN" $BASE/api/state | python3 -c 'import json,sys; print(json.load(sys.stdin)["revision"])')
for i in $(seq 8); do
  curl -sN -H "X-Session-Token: $TOKEN" -H 'Content-Type: application/json' \
       -d "{\"prompt\":\"hi\",\"revision\":$REV}" $BASE/api/complete > /dev/null &
done
```

## 7. Release review (M1–M4)

Done by the maintainer for each release and recorded in `tests/live_evidence.md`.
`python scripts/build_release.py` runs the mechanical parts of M1, M3 and M4 as release checks
RC1–RC3 on every build, before anything is published. M1 is then only the judgement that the NEWS
bullets are user-visible; M2–M4 remain the second check, on the wheel actually published.

| Step | When | Check |
| --- | --- | --- |
| M1 | Before merging to `main` | `NEWS.md` has a `## X.Y.Z` section matching `_version.py`, with user-visible bullets of at most 20 words. (RC1 checks the section and the word counts.) |
| M2 | After the release workflow | Tag `vX.Y.Z` targets the commit that passed CI, and the notes match the NEWS section. Download the published wheel and compute its sha256 locally: it equals the install line's. Record the commit and the sha256. In a fresh virtual environment, the install line gives a working `agentwiz --version` reporting both versions, and so does the README one-liner (`curl … \| bash`) in an empty directory. |
| M3 | On the wheel downloaded in M2 | `python -m zipfile -l <wheel>`: only `agentwiz/**`, including `agentwiz/ui/index.html`, plus `METADATA`, `WHEEL`, `RECORD`, `entry_points.txt` and `licenses/LICENSE` under `agentwiz-X.Y.Z.dist-info/`. |
| M4 | On the wheel downloaded in M2 | Its `METADATA` has `Name: AgentWiz`, `Version: X.Y.Z`, `License-Expression: MIT`, and the `Requires-Dist` line with the pinned modelSpell URL and sha256 (Hatchling writes the name as `modelspell`). |

## 8. Recording

Copy this template into `tests/live_evidence.md` under "Records", one per configuration and
session, and update the configuration matrix:

```markdown
### <date> — <configuration> — AgentWiz <version>, commit <sha>
Route: candidate | published | development. Wheel sha256: <hex, for the candidate and published routes>.
modelSpell <version>; backend <name and version>; model <id>; agentwiz imported from <installed|checkout>.
Checks: install ✓/✗/missing; launch and page ✓/✗/missing; connection (incl. LM Studio auth if enabled) …; discovery …; streamed completion ….
Outcome <outcome>, transport <transport>; notes.
EVIDENCE line: …
```

Rules:

- A record covers exactly the build its fields name. Only candidate-route records for a release's
  candidate commit count towards that release; earlier records stay as history, under their own
  version and date, and never satisfy a later release's gate.
- A configuration is "verified" only when all five checks pass on the same build (Section 2).
- The candidate's wheel sha256 comes from `build_release.py`'s output; the test cannot know it.
  When the release is built from the documentation-only follow-up commit (README platforms table
  and these records), the published wheel's sha256 differs from the candidate's, because README is
  part of the wheel's metadata. M2 records the published sha256 separately; candidate records are
  never relabelled as if they had tested the published wheel.
- **Historical records** keep their original date and versions, and may be added only from notes
  made when the check was done. A check run now is recorded as what it is (a run of the version and
  commit tested now). A missing record is written "missing" and is not read as proof that a check
  failed or was skipped.
- Keep the README platforms table in step with the matrix. README ships in the wheel, so the table
  changes only with a release (in the documentation-only follow-up commit).
