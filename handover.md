# AgentWiz 0.2.3 handover

**20 November 2026 · Australia/Sydney**
**Implementation base** `devel` at `bee9e1e4a9be04ae3328c1492dd2328bba8779b3`
**References** 0.2.3 technical specification v15 and plan v17.4

## Scope

Ship **F-b** for 0.2.3. Workflow Design retains the canvas and the List improvements. Stage 6b, W-3/W-4/W-6 and
the canvas-removal tutorial updates remain deferred. The release is **offline-only**. OpenRouter, LM Studio,
webSpell and the `deidentify_text` live qualification belong to 0.2.4.

## Follow-up fixes

- Workshop keeps current and retained selections distinct, including attached-run reopening and races between
  the preview and revision list. A current block’s problem no longer disables a valid retained revision. Compare
  preserves the selected arms and example choices after a stale refusal. Newly changed examples require reselection.
- Managed Python recovery uses the saved revision’s draft path. Unmanaged recovery preserves the protected Assist
  Apply lineage, bounds the raw captures and prunes expired references. Tested recovery after restart and workspace
  switch, saved-evidence hygiene, and ordinary purpose and Python source edits after recovery.
- Workshop views share in-flight compact attempt lookups by batch. R-2(b5) observes two polls after leaving the
  block, then reattaches and stops the original batch. R-7 verifies the compact response is under 1 KiB.
- W-1 uses the existing workflow’s direct Run → Design → List navigation and checks retained input. W-5 runs the
  actual amended workflow after rebind, Save and reopen. It checks the full outbound request for private canaries
  and verifies zero cloud requests before approval. The existing U-5 tests cover invalid, rejected and expired
  decisions. Retired-route checks use the original request shapes. D-1 checks the exact rendered PDF input.
- The installed command selects 21 baseline cases for the published 0.2.2 wheel and the full case set for 0.2.3.
  Candidate evidence includes private Agentic Chat draft labeling, a zero-send refusal and Stop. CI checks for
  unexpected skips and permits the documented macOS exception for the Linux-only PDF worker address-space test.

## Earlier local verification

- Published 0.2.2 wheel SHA-256 `fad5de0c335c60f0bf77bbea06656df7a20d1e66011d62432b94c4cba44141da`.
  Installed F-a check passed **21 of 21**.
- Candidate wheel SHA-256 `24995abd233bbb3a347aef741b8f91cfabc71c8d6e3c0537938b02a29a69c14f`.
  Built from a Git archive with the tracked edits overlaid in scratch. No commit was created. F-b shape passed,
  and F-a and removal both returned exit 3.
- Linux/Python 3.13.5 offline suite passed **1,448 tests**, with nine live cases deselected and zero skips.
- Linux/Python 3.13.5 installed F-b candidate passed **229 tests**, including **78 of 78** required checks.
- RC1–RC3, `pip check` and S1–S26 passed against the candidate wheel.

## Hosted CI repair on 71c25f4

[GitHub Actions run 44659019877](https://github.com/alexchwong/AgentWiz/actions/runs/44659019877)
had three different failure symptoms.

| Runner | Observed result | Change |
| --- | --- | --- |
| Ubuntu 3.13 | The source suite passed. The installed-wheel job stopped reporting progress after the first 72 tests and was cancelled roughly 80 minutes later. The old log has no per-test ID or stack for the stall. | Replace manually held Playwright routes in the parameter-preview and compact-attempt tests with bounded server-side holds that always release. Print each test ID, dump blocked thread stacks after 120 seconds, and bound each CI test step to 20 minutes. The original hosted hang did not reproduce locally. |
| macOS 3.13 | The retained-revision reload test found an empty input in the source suite. | Restore inputs before the refreshed form is rendered and keep its controls inert until Reload completes. Explicit revision tests wait for selection to finish. |
| macOS 3.10 | Installed tests failed on retained-revision input and stale Local only readiness. The report then marked all 78 required tests missing. | The same reload fix protects against early Run clicks. The installed launcher resolves its temporary root before invoking pytest, keeping JUnit test IDs relative to the copied test tree through symlinked temporary paths. |
| Ubuntu 3.10 | Passed. | Preserve the same offline and installed requirements. |

The old launcher reproduced malformed JUnit classnames with a symlinked temporary root on Linux.
A forced in-flight reload also reproduced the formerly active Run control. The revised regression confirms Run
is inert while Reload is waiting and that the refreshed Local only block refuses the cloud role with zero calls.
The original Ubuntu stall has no stack trace, so the affected browser fixture remains a possible cause.

- Linux/Python 3.13.5 passed **1,449 offline tests** with zero skips and nine live cases deselected.
- The 0.2.3 candidate passed **229 installed tests** and **78 of 78** required cases with a symlinked
  temporary directory. The published 0.2.2 wheel passed all **21** baseline cases with the same setup.
- The candidate passed RC1–RC3, `pip check`, S1–S26, and the F-b shape check. Both alternative shapes
  returned exit 3 as expected. Candidate wheel SHA-256
  `cbbc52929c13896818ff21f3542952d2e8d498ee7901cbc6b4d8621872752e6b`.

The patched hosted matrix still needs a run. No live testing is required for this repair.

## Historical departures and release follow-up

- The initial implementation bundled the stages into one commit. Its handover reported the Checked request
  ports passing before the old routes were removed. Historical stage-by-stage CI logs were unavailable during
  this follow-up.
- Retain the existing refusal bodies and codes. The headless stale code is `stale_revision`, the Workshop
  not-found code is `missing`, and a cloud destination for Local only input produces `destination_ineligible`.
  The existing Apply guard is `generated_script_disabled`. The unused `Writer.inherit` test caller was migrated
  with the original trim.
- Archive the actual hosted Ubuntu/macOS and Python 3.10/3.13 runs before release. The three failing jobs
  on `bee9e1e` are historical diagnostics. Their exact pytest failures were unavailable here. WSL2 PDF
  verification for this candidate is still separate.
- Remaining evidence gaps from the review are a separate R-7 case crossing the 32 MiB preview cap and a single
  browser case combining restricted Agentic Chat, reconnect and Stop. The installed checks cover the sub-cap
  Compare workload and private draft, refusal and Stop separately.
- **Files to delete in this delivery: none.** Keep this handover through release review and move the durable
  release evidence into the repository documentation before retiring it.
