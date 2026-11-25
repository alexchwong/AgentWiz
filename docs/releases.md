# Release operations

For the repository owner or a maintainer explicitly authorized to release. This document is
not permission for a coding agent to commit, push, merge, tag or publish. Agent requirements
and documentation placement are governed by `../DEVEL.md`.

## Validation and optional review

The offline CI matrix, release tooling checks, and installed-wheel smoke apply to each candidate.
See the [0.2.5 foundation gates](#agentwiz-025-release-gates) for the current release. The [0.2.4 release gates](#agentwiz-024-release-gates) record that release’s live checks. The historical
[0.2.3 installed-wheel gate](#023-installed-wheel-gate) was credential-free. The 0.2.2 live
qualifications are recorded in [the decision history](decisions.md#18-november-2026--022-release-close-out-disposition).

Subjective UI review is optional. Use the built-in sandbox tutorial to check readability, spacing,
guidance and discoverability. Objective regression checks belong in automation. Report WSL2
verification separately because the hosted matrix covers Linux and macOS.

## Installed-wheel smoke

Run from the repository root in the activated checkout `.venv`:

```bash
python scripts/build_release.py --smoke
```

The script builds committed `HEAD`. To check uncommitted edits without creating a commit, export
`HEAD` with `git archive` into a temporary directory, overlay the changed and new files, and remove
deleted paths there. Apply the release builder's `build_wheel`, `release_checks` and `smoke` functions
to that tree. Keep that output separate from earlier builds. Before publishing, run the normal
command against the intended committed tree.

Prepare hatchling and a wheelhouse containing the hash-checked pinned modelSpell and scriptSpell
wheels and all runtime dependencies for an offline smoke.

```bash
python scripts/build_release.py --smoke --no-isolation --modelspell-wheel ../agentwiz-prep/wheels/modelspell-0.4.0-py3-none-any.whl --scriptspell-wheel ../agentwiz-prep/wheels/scriptspell-0.3.0-py3-none-any.whl --dependency-wheels ../agentwiz-prep/wheels
```

`--modelspell-wheel` and `--scriptspell-wheel` each refuse a file whose name is not the pinned URL's file name
or whose sha256 is not the pin; the smoke then installs the wheels with `pip install --no-index --no-deps` and runs
`pip check`. Prepare `jsonschema>=4.26,<5`, `PyYAML>=6,<7`, `pydantic-monty==1.5.0` and
`pypdfium2>=5.13,<6` with their transitive wheels in the directory supplied with
`--dependency-wheels`; they are installed with `--no-index --find-links`. Without that option
these runtime dependencies are installed from the package index. Without a local modelSpell
wheel pip fetches the pinned URL. A warm pip cache
can hide a missing prerequisite, so it is not preparation.

## Branches and releases

Work happens on `devel`; `main` only receives merges from `devel`. On pushes to `devel` and on
pull requests not targeting `main`, `tests.yml` runs the offline and installed-wheel UI checks
(Ubuntu and macOS × Python 3.10 and 3.13), plus one installed PDF wheel smoke job
(Ubuntu x86-64, Python 3.13).

For PRs targeting `main`, `release-pr.yml` runs the same checks with the full 16-job PDF matrix
on the proposed merge, including after each push to the PR branch. Once the check has appeared,
configure `main` branch protection to require **PDF release gate** from GitHub Actions. Replace any
old required checks that no longer report on these PRs; this gate requires both the PDF matrix and
the other tests. Do not apply this requirement to `devel`.

`release.yml` runs on every push to `main`. It reruns the tests with the full PDF matrix, then,
from the commit that passed them (`github.sha`, never a re-read of `main`):

- **If `vX.Y.Z` is not yet released:** builds the wheel from that commit, runs the release checks
  RC1–RC3 on it, installs it into a fresh venv with `--no-index` (the pinned modelSpell
  and scriptSpell wheels are still fetched) and runs the smoke check, then creates release `vX.Y.Z` tagged at that commit, with
  the wheel, the NEWS section as notes and a pinned install line with the sha256. A failed check
  stops the job before anything is published.
- **If `vX.Y.Z` already exists:** fails if a path covered by the shipped-file guard changed since the tag; otherwise skips
  building and publishing the wheel.
- **Finally, once the release is there** (new, or unchanged with the guard passing): re-attaches
  `scripts/install.sh` to `vX.Y.Z` with `--clobber`, so `releases/latest/download/install.sh`
  always serves the current installer without a new release.

The workflow also accepts manual dispatch and uses the selected ref's event SHA. The current
workflow can publish from a non-main ref. For release recovery or installer re-attachment,
dispatch only on `main` while its head is the authorised merge commit. If `main` has advanced,
prefer re-running the authorised workflow run for the original commit where available.

If a post-merge test fails and needs a code fix, fix it on `devel` and open a new PR into `main`.
Each push to that open PR runs all 16 PDF jobs again before another merge. To reproduce a PDF
failure before opening the PR, manually run **Tests** on `devel` and select the failing runner and
Python version, or leave both at `all` for the full matrix. This diagnostic runs only the PDF checks
and cannot publish or substitute for the PR gate. GitHub requires the dispatch-enabled workflow
to be on the default branch before manual dispatch is available.

**Shipped-file rule.** [DEVEL.md](../DEVEL.md#documentation-and-release-preparation) defines
the policy and shipped inputs, including the force-included `config/model-servers.ini.template`.
The existing `build_release.py --changed-since <tag>` guard compares committed `HEAD` with the
candidate version's existing tag over `agentwiz/**`, `pyproject.toml`, `README.md` and `LICENSE`.
It exits 1 when those paths differ and performs no build. Review template-only changes against
the version policy because the guard omits the template source. README supplies wheel metadata.

**Release checks.** `build_release.py` runs these on every build (not with `--changed-since`),
after building and before any smoke test, reading only the archived `HEAD` tree and the wheel.
They are release tooling under the owner's exception to T2, not tests:

| Check | Passes when |
| --- | --- |
| RC1 | `NEWS.md` has exactly one line `## <version>`; its section has at least one `- ` bullet, each opening bullet line of at most 20 words. Review the entire bullet, including continuation lines, against the policy. |
| RC2 | The wheel is `agentwiz-<version>-py3-none-any.whl`; every member is under `agentwiz/` or `agentwiz-<version>.dist-info/`; `agentwiz/ui/index.html`, `agentwiz/model-servers.ini.template`, `agentwiz/pdf_reader.py`, `agentwiz/pdf_worker.py`, `agentwiz/tutorial_support/pdf_fixtures.py`, `agentwiz/library/index.json` and every file of each library entry it lists are present; the `.dist-info` members are exactly `METADATA`, `WHEEL`, `RECORD`, `entry_points.txt`, `licenses/LICENSE`. |
| RC3 | `METADATA` has `Name: AgentWiz`, `Version: <version>`, `License-Expression: MIT`, the required `jsonschema>=4.26,<5` and `pypdfium2>=5.13,<6`, and exactly two other runtime dependencies: the modelSpell and scriptSpell direct URLs, identical to the pinned release-wheel URLs and sha256 in `pyproject.toml`. |

Each prints `RCn ok` or `RCn FAILED: <reason>`; all three run; on any failure nothing is copied to
`--output-dir` and the script exits non-zero. Whether NEWS bullets are user-visible stays a human
judgement.

### Release checklist

1. On `devel`, check the exact target tag and releases with credentials that can see drafts
   and prereleases. Resolve any matching tag or release before preparing a new one. For shipped
   changes, use a version newer than the published version they change and its NEWS section.
   Keep the same version for further edits while it is unreleased. Commit and push when authorized.
2. Confirm the offline CI matrix passes on Ubuntu and macOS, Python 3.10 and 3.13. A Linux-only
   local pass covers Linux. Check the installed-wheel gate and all 16 PDF jobs in the PR into `main`.
3. Run `python scripts/build_release.py --smoke` on the intended committed tree and require
   RC1–RC3, `pip check` and S1–S26 to pass. Finish the version-specific checks below and require
   the pull request checks to pass on the proposed merge. Rerun checks affected by any change
   to the candidate. Merging into `main` can publish the release automatically.
4. Merge `devel` into `main` when authorized. The release workflow reruns tests and checks
   before publishing. Verify that the tag points to the tested merge commit and that the release has
   the wheel and installer. Compare the wheel's SHA-256 with the install line in the release notes.

NEWS authoring and version-bump requirements are defined in `../DEVEL.md`.

S15 in the installed smoke creates a captured
Workshop script starter, publishes it through a bound plan, runs it through the
normal coordinator, checks the returned count and verifies duplicate Start identity.
This exercises packaged starter runtime and licence resources without requiring a model backend.
Use the installed-wheel gate below for the browser and workspace upgrade tests.

Release 0.2.1 adds S16 and S17. S16 checks the installed Workflows browser asset, publishes a
captured v4 transform workflow, preflights it, and verifies its final output through
the installed server. S17 runs a Release Gate workflow, explicitly submits its token-bound
human decision through the ordinary API and verifies the released output. No live backend
or automatic approval is used.

Release 0.2.2 makes S13 a standalone Python block run and adds S18–S22 against a scripted stub (`agent-two-step`,
`agent-docs`, `agent-python` and a non-streaming `author` model): S18 a two-step agentic run, S19 search, read and
a downloaded draft over a staged document, S20 the S13 Python run, S21 Python Describe it (examples, then Generate and
check), and S22 an agentic run that calls a saved Python block.

The 0.2.2 starter blocks addendum adds S23–S25. S23 adds `csv_to_rows`, `sort_and_dedupe_rows` and
`rows_to_table` through the library installer (plan, then add with the plan's confirmations) and runs them as a
workflow. S24 extracts the packaged PDF fixtures (`agentwiz.tutorial_support.pdf_fixtures`) on both upload routes,
including the partial-coverage fixture, through the installed PDF worker. S25 is a scripted agent run
(`agent-draft`) that copies a passage to a draft with `copy_to_draft`, then revises it with `revise_draft`.
RC2 also requires the PDF reader and worker and every library entry listed by `agentwiz/library/index.json`; RC3
requires `pypdfium2>=5.13,<6`, which the smoke's dependency preload and the offline wheelhouse must include.

## AgentWiz 0.2.5 release gates

Record pass, fail, blocked or not run against the exact candidate and environment. Require
all eight foundation checks before owner sign-off.

| Gate | Required evidence |
| --- | --- |
| C1 | Valid and malformed packages, standalone roots, nested captures, exact pins and deterministic workflow outputs. |
| C2 | Private library and explicit all-Public Pool promotion, exact revision reuse, setup refusal, clone and reset. Verify that export and Pool promotion refuse workflow definitions carrying Local only constant values. |
| C3 | Stale review refusal, atomic publish, interruption recovery and idempotent retry. |
| C4 | Inert skill and protected-source regressions plus a passing synthetic OpenRouter proposal from [DEVEL.md](../DEVEL.md#025-foundations-checks). Retain the JSON with its tested commit. The owner confirmed the live pass recorded in the [0.2.5 decision history](decisions.md#025-implementation-evidence). |
| C5 | Scripted worker tests for pinned child workflows, isolated artifacts, trust, stale roles, budget, busy, Stop and effect refusal. |
| C6 | Offline regressions for Local only, the Release Gate, split origins, revision protections and webSpell eligibility. The native Gate check exercises zero sends before approval, one scoped send after approval and refusal to replay it. |
| C7 | Source and installed-wheel CI, all 16 binary PDF jobs, release builder smoke on the committed candidate and the published 0.2.4 workspace upgrade check using the candidate wheel. Record WSL2 PDF verification separately. |
| C8 | Review release notes, Workshop guide, README, developer commands and decision record for consistent foundation scope and prototype disclosure. Check the [tutorial requirements](../DEVEL.md#ui-and-tutorial-rules) for new functions. |

The installed wheel retains `privacy_summary_local_v1` and `privacy_summary_cloud_v1` with
**Unqualified** disclosure. They are unsuitable for production private summarisation or private
Cloud use. Retain their offline Gate and workflow regressions. The 0.2.5 foundations gate requires
neither a private-summary OpenRouter round trip nor GPT-OSS-20B 4-bit verification in LM Studio.
Need-to-Know is a 0.2.6 feasibility experiment contingent on evidence.

See [DEVEL.md](../DEVEL.md#025-foundations-checks) for the skill, upgrade and installed PDF
commands. Check the installed version of both templates for the **Unqualified** disclosure.

### Historical 0.2.2 qualification (starter blocks addendum, Section 7)

For 0.2.2, the owner accepted either OpenRouter or LM Studio for live qualification of the
shipped catalogue and Agentic Chat. The evaluation procedure below records that release's criteria.
Choose a profile explicitly and use synthetic fixtures. Record the route, model, provider pin
where applicable and effective settings. Provider API and tool replay diagnostics belong in
modelSpell's live suite.

1. **Model-block qualification.** Run `scripts/library_eval.py --profile NAME` on the chosen profile. Round 1
   is one trial of the dev set. Run full qualification (three trials of ten cases, `--trials 3 --set all`) for
   ready blocks. Fill in context, reasoning, temperature and token budget. For a local installation also record
   the model file, quantisation and server version. Each block needs at
   least 29/30 contract executions and 24/30 correct, every critical case correct on all three trials, the hard
   invariants in every run and the rubric cases passing the owner's review. Mark `owner_mark` true or false for
   each rubric and run `scripts/library_eval.py --review REPORT` to refresh the verdict without model calls.
   Alternatively, use the grouped `--export-review` / `--apply-review` flow in DEVEL.md; all trial outputs
   remain visible and no mark is inferred automatically.
   Keep passing evidence for every shipped model block. Remove any block still below the bar after three revision
   rounds from `index.json` and its shipping references. Record deferrals in [the decision history](decisions.md).
2. **Agent selection.** Run `scripts/agent_eval.py --cohorts tests/agent_eval/cohorts.json` in a disposable
   workspace prepared automatically by selecting `--route openrouter` or `--route lmstudio`. The runner
   selects its evaluation profile and binds `fast`; no UI setup is required. Use Automatic routing or an
   explicit tool-capable OpenRouter pin. For intentional attached-server diagnostics, supply `--url` and
   prepare matching Chat/`fast` settings in that disposable workspace. Record the three rates and the five-scenario gate verdict. A passing scenario needs a
   finished answer, successful expected tool calls in order, valid arguments and the expected result. The README
   claims library support in Chat only once this gate passes on the target orchestrator.
3. **PDF wheel matrix.** The Tests workflow's `pdf-wheels` job builds and installs the candidate wheel on
   Linux x86-64/arm64 and macOS arm64/x86-64 with Python 3.10–3.13. It requires a binary pypdfium2
   distribution, checks installed licence notices and extracts packaged fixtures through the real worker.
   Require all matrix jobs green. WSL2 remains outside hosted CI; if claiming WSL2 PDF support, run
   `python scripts/check_pdf_install.py` in that environment and retain its platform/version result.
   A target without a usable wheel needs its support claim resolved before release; this automation
   does not silently convert PDF to an optional extra or waive a platform failure.
4. **PDF on macOS.** Record the owner's decision on shipping without a worker address-space limit.
   See the [0.2.2 close-out disposition](decisions.md#18-november-2026--022-release-close-out-disposition).

The Local only `deidentify_text` starter is deferred from the 0.2.2 catalog and wheel. Its source and cases are
available for future qualification. To qualify it in a source checkout, restore its index entry temporarily and
run the same block evaluation on a trusted local profile, such as LM Studio or a loopback llama-server
(`--route openai_compat`). Restore shipping metadata only after it passes. The catalog deferral keeps a local
model run off the OpenRouter release path.

## 0.2.3 installed-wheel gate

- **Smoke.** S11 now runs a v4 model block pinning the saved contract through `/api/blocks/run` (complete, check
  `pass`). S26 sends previously valid requests to the retired routes and requires their router's not-found body,
  and the `checked_request` source refused 400, with nothing reaching the model stub.
- **Installed command.** `python scripts/check_installed_ui.py --expect-version X.Y.Z --workflow-outcome
  {f-a,f-b,removal}`. Both arguments are required (exit 2 without). It refuses a non-site-packages import or another
  version, checks the installed `workflows.js` shape (exit 3 on a mismatch; `--shape-only` stops there), runs its
  module list with `--junitxml`, and requires every `REQUIRED` row to match exactly one passed JUnit case.
- **Setting.** `scripts/installed_gate.json` holds the Stage 6 outcome (`f-b` for 0.2.3). The `tests.yml` step reads
  the version from `agentwiz/_version.py` and the outcome from the setting, runs the command, then requires exit 3
  for each other outcome with `--shape-only`. `release.yml` reuses that workflow unchanged.
- **Published baseline.** Install the published 0.2.2 wheel, verify its SHA-256 against the release, then run
  `python scripts/check_installed_ui.py --expect-version 0.2.2 --workflow-outcome f-a` from the 0.2.3 checkout.
  This runs the 13 Stage 0 cases and eight original Workflow Design checks. The 0.2.3 command selects the full
  candidate modules and required cases, including the installed private Agentic Chat checks.
- **Offline CI.** The source suite has a 20-minute step limit. The installed step has a 10-minute limit,
  prints pytest setup and teardown, and dumps a blocked test's stacks after 120 seconds. A separate
  8-minute watchdog dumps stacks if the installed pytest session is still active, including between
  tests and during cleanup. The offline suite allows one macOS skip for the Linux-only PDF worker
  address-space test. The separate PDF wheel matrix still checks binary installation, licence notices
  and extraction through the installed worker on Linux and macOS.

## AgentWiz 0.2.4 release gates

Use the same candidate and synthetic fixtures for all checks. Retain the commit, route, model and
provider or local endpoint with each report. Owner walkthrough and review are optional. The
publication workflow runs offline checks, so record the live verdicts before merging to `main`.

| Gate | Evidence to retain |
| --- | --- |
| Offline | Hosted Ubuntu/macOS × Python 3.10/3.13 source and installed-wheel checks, PDF wheel matrix, and RC1–RC3 plus S1–S26 on the release candidate. |
| Live Chat | The same fixed cohorts, five end-to-end scenarios, and full default inventory on OpenRouter and LM Studio. Record the effective Chat binding. |
| Shipped Cloud catalogue | Reuse matching evidence from the [accepted 13-block baseline](decisions.md#prior-live-starter-block-evidence). Run only blocks without a matching passing report for the intended binding. |
| Managed webSpell | Install the pinned v0.2.1 bundle. Check all three operations on public fixtures. Capture a live Cloud Chat tool turn, a refused operation, and zero webSpell availability and dispatch in Local only mode. |

The Local only `deidentify_text` starter is not a 0.2.4 gate. It remains out of
`agentwiz/library/index.json` and the wheel. Retained LM Studio reports are reference evidence
only: neither a pass nor a failure blocks publication, permits inclusion, or requires a rerun.
A later release may replace it with improved identifier redaction. The evaluator's
`--deidentify-source` option remains available for optional source-checkout runs.

Catalogue evaluation is release evidence. Chat and Workshop use the runtime eligibility rules
recorded in the [0.2.4 specification decision](decisions.md#agentwiz-024-specification-v2).
The Chat composer selects task instructions, while model defaults and existing block role bindings
determine routing. Preserve the active Cloud policy and record the actual provider.

Run the [live release quickstart](../DEVEL.md#024-live-release-evidence) from the candidate
checkout. Use the intended release bindings when comparing evidence. For Chat against an
installed wheel, launch it in a disposable workspace with
`agentwiz --no-browser --session-file PATH` and add `--session-file PATH` to the evaluation
command. Configure the binding and trust before running it. The evaluator changes that
workspace, including its live Chat session. The library evaluator launches its own server
from the candidate source.

The full catalogue threshold is at least 29/30 contract executions and 24/30 correct outputs per
block, with every critical case correct in all three trials and all hard invariants satisfied.
Treat a report awaiting rubric review as pending. A direct MCP check and the webSpell service's own
CI are supporting evidence. The AgentWiz webSpell release test checks the live service and
exercises its Chat discovery, dispatch and approval paths.
