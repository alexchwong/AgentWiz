# AgentWiz 0.2.0 close-out

Documentation cleanup against `devel` at `e937d268069a0c7815fc525f16e7c37ae6c0bb16`, following close-out specification v2. The worktree was clean at the start. Keep the release version at 0.2.0. At preflight, `main` and the latest published release, `v0.1.4`, pointed to `e0b91d26b72d2e96d85828356e4c88cf05eb2f18`. The exact `v0.2.0` tag was absent and the authenticated release list showed no matching draft, prerelease or published release.

## Delivery

- `README.md` and `docs/architecture.md` now describe executable v3 blocks, managed heads, exact contracts, named model outputs and revision descriptions. README includes a runnable synthetic example and separates checkout installation from published releases.
- `DEVEL.md` and `docs/releases.md` clarify version policy, wheel inputs and release procedure. `docs/decisions.md` preserves the historical decisions and links to their pinned sources.
- `NEWS.md` corrects and shortens the affected 0.2.0 bullets. `HANDOVER.md` now covers the current close-out.
- Delete `MANIFEST_phase2.txt`, `contract_overhaul_handover.md` and `contract_overhaul_tech_specs.md`. The archive's `DELETIONS.txt` lists these paths for application and is outside the repository changes.

The owner previously confirmed block design and testing, model-assisted custom JSON contract synthesis and a working identifier-tally block using that contract. The checks below used synthetic data and local stubs.

## Verification

Checks ran on 6 November 2026 on Linux x86_64 with Python 3.12.14, Playwright 1.63.0 and Chrome Headless Shell 153.0.8010.12. The Playwright CDN download failed. The exact pinned browser build was recovered from the Chrome for Testing distribution and its version and launch were verified.

| Gate | Result |
| --- | --- |
| G1 · Scope and examples | Passed. Clean diff check, expected paths only and no active links to obsolete working-tree files. Model, tool and script definitions loaded. Contract examples, multiline Workshop Request preview and positive and negative named-envelope cases passed. All 57 unreleased NEWS bullets meet the whole-bullet limit (maximum 19 words). Released NEWS sections match the baseline byte for byte. |
| G2 · Offline checkout | Passed. `python -m pytest -q -m "not live"` reported 935 passed, 10 deselected, zero failures, errors or skips. |
| G3 · Release build and smoke | Passed. `python scripts/build_release.py --smoke` passed RC1–RC3 and S1–S15 from disposable local commit `13fbc09f403424e1f94ada94b17fb69d45b93c10`. |
| G4 · Installed page and tutorial | Passed. 55 page and tutorial tests, zero failures, errors or skips. The isolated interpreter imported AgentWiz from the installed wheel in site-packages. |
| G5 · Candidate CI and release workflow | Pending authorised push and merge. The Linux/macOS × Python 3.10/3.13 matrix remains required. |

The implementation checkout has no new commit. The sole commit used for smoke is in the disposable clone. A Git tree comparison of that commit and the delivery, excluding only this handover, returned 0. The common tree with the handover excluded is `cf78345b70889461472f0a6b60cf2aa9372d8c54`.

## Release follow-ups

Apply the three deletions with the delivered files. Repeat the exact-tag and draft-aware release preflight before the authorised merge and require candidate CI.

The v2 specification leaves three release tooling fixes separate. The shipped-file guard omits the source template, RC1 ignores words on NEWS continuation lines and manual release dispatch can publish from a non-main ref. The documentation records the corresponding review and operator rules.

## 0.2.1 candidate

Cumulative changes are based on `devel` at `cc44074bbe01b99478566a37fea9a19aa3bd8552`.
The package version is 0.2.1. The checkout is uncommitted.

### Delivered by phase

1. V4 ports, transform starters, workspace-scoped destination trust, protected dispatch,
   draft lineage, and reviewed block upgrades.
2. Exact workflow bundles and immutable captures, graph planning, serial execution,
   example batches, MCP confirmation, and workflow evidence.
3. Workflow forms, run preflight and traces, examples, trust settings, History, and the
   isolated tutorial. The form-only journey passed before the canvas was added.
4. Canvas dragging and connections using the same form state and reviewed save,
   keyboard positioning, release version and notes, and installed workflow smoke S16.

### Verification

- Phase 3 workflow and tutorial checkpoint passed 14 tests on Linux with Chromium.
- Final focused workflow page tests passed five tests, including drag, form-only save
  and execution, exact values, destination trust, and rebind against a missing source.
- The Linux Python 3.12 offline suite passed 963 tests with ten live tests deselected.
- A wheel built from an isolated uncommitted source snapshot passed RC1–RC3,
  `pip check`, and installed smoke S1–S16. This used the release builder's existing
  build and smoke functions and the hash-checked pinned modelSpell wheel.
- Six focused browser and tutorial tests passed against the installed package in an
  isolated Python 3.12 environment outside the checkout.
- The Linux Python 3.10.22 and 3.13.17 offline suites each passed 963 tests with
  ten live tests deselected. The supplementary browser test for both MCP
  confirmation modes and Stop passed separately on all three Python versions.

### Release follow-up

Run the macOS Python 3.10 and 3.13 CI lanes and the WSL2 verification before sign-off.
Use the normal release command on the intended committed candidate.

### Obsolete files

No deletions are required for this delivery. The deletion list in the historical
0.2.0 close-out above is unrelated to this archive.

### 0.2.1 implementation audit

Reviewed the four-phase candidate against the v3 specification. The audit fixes
cover workflow Start replay after a dropped response, cancellation before example
output publication, reasons for skipped nodes and examples, and saved evidence.
Start recovery reuses the reviewed request and ticket while the page is open.
Saved runs recheck captured source bytes for newly known credentials and make the
executable capture available from History.

The Linux offline matrix passed 968 tests per interpreter on Python 3.10.22,
3.12.14 and 3.13.17, with ten live tests deselected. After exposing the capture
in History, the 15 workflow tests passed again on all three interpreters.
The final wheel passed RC1–RC3, `pip check`, smoke S1–S16 and nine focused
browser and tutorial tests. The macOS CI lanes and WSL2 verification remain
pending.

Apply the audit ZIP over the phases 1–4 ZIP. The audit requires no deletions.
The implementation checkout remains at the original devel commit.
