# Live and manual evidence

Evidence for each release (R0-5). Missing evidence is written as "missing" and never counts as a
pass. Copy `EVIDENCE` lines from `python -m pytest -m live -s` into the records below.

## Configuration matrix

| Configuration | Install | Launch and page | Connection | Discovery | Streamed completion | Status | Verified on |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Linux, LM Studio on the same machine | missing | missing | missing | missing | missing | awaiting evidence | |
| Linux, llama-server | missing | missing | missing | missing | missing | awaiting evidence | |
| macOS, LM Studio on the same machine | missing | missing | missing | missing | missing | awaiting evidence | |
| WSL2, LM Studio on Windows, mirrored networking | missing | missing | missing | missing | missing | awaiting evidence | |
| WSL2, LM Studio on Windows, NAT with "Serve on Local Network" | missing | missing | missing | missing | missing | awaiting evidence | |
| WSL2, LM Studio headless (llmster) inside WSL2 | missing | missing | missing | missing | missing | awaiting evidence | |
| OpenRouter (any one platform) | missing | missing | missing | missing | missing | awaiting evidence | |

## Records

<!-- Template:
### <date> — <configuration>
AgentWiz <v>, modelSpell <v>; backend <name and version>; model <id>;
checks: install ✓/✗/missing, launch ✓…, connection (incl. LM Studio auth if enabled) …, discovery …, streamed completion …;
outcome <outcome>, transport <transport>; notes.
EVIDENCE line: …
-->

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
