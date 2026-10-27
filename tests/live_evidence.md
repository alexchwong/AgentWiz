# Live and manual evidence

Evidence for each release (R0-5). How to take it: `tests/LIVE_CHECKS.md`. Missing evidence is
written as "missing" and never counts as a pass, nor as proof that a check failed or was skipped.
Copy `EVIDENCE` lines from the live tests into the records below. A record covers exactly the build
it names; only candidate-route records for a release's candidate commit count towards that release.

## Configuration matrix

| Configuration | Install | Launch and page | Connection | Discovery | Streamed completion | Status | Last verified with |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Linux, LM Studio on the same machine | missing | missing | missing | missing | missing | awaiting evidence | |
| Linux, llama-server | missing | missing | missing | missing | missing | awaiting evidence | |
| macOS, LM Studio on the same machine | missing | missing | missing | missing | missing | awaiting evidence | |
| WSL2, LM Studio on Windows, mirrored networking | missing | missing | missing | missing | missing | awaiting evidence | |
| WSL2, LM Studio on Windows, NAT with "Serve on Local Network" | missing | missing | missing | missing | missing | awaiting evidence | |
| WSL2, LM Studio headless (llmster) inside WSL2 | missing | missing | missing | missing | missing | awaiting evidence | |
| OpenRouter (any one platform) | missing | missing | missing | missing | missing | awaiting evidence | |

## Records

<!-- Template (tests/LIVE_CHECKS.md, Section 8):
### <date> — <configuration> — AgentWiz <version>, commit <sha>
Route: candidate | published | development. Wheel sha256: <hex, for the candidate and published routes>.
modelSpell <version>; backend <name and version>; model <id>; agentwiz imported from <installed|checkout>.
Checks: install ✓/✗/missing; launch and page ✓/✗/missing; connection (incl. LM Studio auth if enabled) …; discovery …; streamed completion ….
Outcome <outcome>, transport <transport>; notes.
EVIDENCE line: …
-->

No records yet. The first OpenRouter record also covers modelSpell's verification V1 (never run
before for want of a key): pinning by endpoint `tag` routes the request, and the streamed
`provider` equals the listing's `provider_name`.

## Manual walkthrough — 0.1.1

Date: missing. Candidate commit: missing. Wheel sha256: missing. Browser(s) and OS: missing.

| Step | Result | Notes |
| --- | --- | --- |
| W1 launch, --no-browser, Ctrl-C, --version | missing | |
| W2 branding, header versions, terminal prefix, --help | missing | |
| W4 settings dialog, discovery, keys, key with a space (Apply) | missing | |
| W8 two tabs: stale Send and stale Apply | missing | |
| W15 workspace line, (created), header, bad name | missing | |
| W16 profiles: save as, switch, modified, save, detach, delete | missing | |
| W17 active profile restored; key re-entered | missing | |
| W18 two tabs: stale select, save and delete | missing | |
| W19 second process on the same workspace | missing | |
| W20 damaged and newer files; recovery by restart | missing | |

## Workspace lock — 0.1.1

Date: missing. WSL2 distribution and Windows build: missing.

| Step | Result | Notes |
| --- | --- | --- |
| L1 WSL2 filesystem | missing | |
| L2 /mnt/c | missing | |

## Release review — 0.1.1

Date: missing. Tag: missing. Commit the tag targets: missing. sha256 of the published wheel: missing.

| Step | Result | Notes |
| --- | --- | --- |
| M1 NEWS bullets user-visible (RC1 passed on the candidate) | missing | |
| M2 tag, notes, sha256 and install line of the published wheel | missing | |
| M3 published wheel members | missing | |
| M4 published wheel METADATA | missing | |

## Manual walkthrough — 0.1.0

Date: missing. AgentWiz and modelSpell versions: missing. Browser(s) and OS: missing.

| Step | Result | Notes |
| --- | --- | --- |
| W1 launch, --no-browser, Ctrl-C, --version | missing | |
| W2 branding, header versions, terminal prefix, --help | missing | |
| W3 parity with the modelSpell demo at 1759c6e | missing | |
| W4 settings dialog, discovery, keys, key with a space | missing | |
| W5 complete-reasoning streaming and status line | missing | |
| W6 seven outcome labels and styles | missing | |
| W7 keyboard guard on slow | missing | |
| W8 two tabs: stale Send and stale Save | missing | |
| W9 budget with 8 curls on hold | missing | |
| W10 Stop on endless | missing | |
| W11 Stop on slow-first | missing | |
| W12 nonfinite and bigint | missing | |
| W13 AgentWiz stopped mid-stream | missing | |
| W14 outcome kept after result and after AgentWiz stops | missing | |

## Release review — 0.1.0

Date: missing. Tag: missing. Commit the tag targets: missing. sha256 of the published wheel: missing.

| Step | Result | Notes |
| --- | --- | --- |
| M1 NEWS section matches _version.py; bullets ≤ 20 words | missing | |
| M2 tag, notes, sha256 and install line of the published wheel | missing | |
| M3 published wheel members | missing | |
| M4 published wheel METADATA | missing | |
