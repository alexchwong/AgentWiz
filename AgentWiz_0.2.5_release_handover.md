# AgentWiz 0.2.5 release handover

25 November 2026 · Australia/Sydney

**Release decision pending.** The live synthetic OpenRouter skill proposal passed with `qwen/qwen3.8-27b`, and the owner confirmed that the WSL2 PDF install check passed. Verify the intended candidate and proposed merge as described below. The ZIP contains 23 repository files and this handover. Apply the repository files at the root of a clean AgentWiz checkout at `devel` commit `ecde101b9e692a2349a7ab785d10548e7e12fd9f`. There are no deletions. Keep this handover with release evidence.

## Exactly what remains locally

1. **After the owner commits the candidate**, use the activated checkout environment to run the normal release builder against the intended commit. Require RC1–RC3, `pip check` and S1–S26. The normal builder archives committed `HEAD`. This implementation used the documented export-and-overlay procedure for the uncommitted candidate.

   ```bash
   python scripts/build_release.py --smoke
   ```

2. **In GitHub**, verify that `v0.2.5` has no conflicting tag or release, then require the offline and installed-wheel CI jobs on Ubuntu and macOS with Python 3.10 and 3.13, and the full PDF wheel matrix, on the exact candidate and proposed merge. Review the release gates in `docs/releases.md` before merging. The owner handles the PR and publication. Merging into `main` can publish automatically.

If repository files change before release, rerun the applicable checks on the resulting tree. The standard source checkpoint and 0.2.4 migration command are:

```bash
python -m pytest -q -m "not live"
python scripts/check_upgrade_024.py
```

The migration check uses a digest-verified copy of the published 0.2.4 wheel to create a disposable workspace. It reopens and runs the saved workflow with the candidate. It also accepts `--baseline-wheel PATH`.

## Candidate evidence

- Base commit `ecde101b9e692a2349a7ab785d10548e7e12fd9f`
- Candidate source manifest SHA-256 `01a93e541651bbce8cb308094921f6d284a388de6ffd2efc9493133439a7dc67`
- Tested wheel SHA-256 `2dd412a61a392eb5496b86399db1d3f323a6086013eab33dca2f63c09ab2eb6e`
- Linux x86-64, CPython 3.12.14, Playwright 1.63.0, matching Chromium headless shell 153.0.8010.12, modelSpell 0.4.0, scriptSpell 0.3.0 and pypdfium2 5.15.2.

The table names the commands and evidence behind each disposition. The output excerpts below record the local results.

| Gate | Status on this candidate | Check and evidence |
| --- | --- | --- |
| C1 · Components | **Pass** | `test_component_foundations.py` and `test_private_summary_025.py` cover malformed and cyclic definitions, standalone Block/Workflow/Agent installs, inactive nested teams, pinned built-ins and exact deterministic outputs. See the source and installed test results below. |
| C2 · Library | **Pass** | Source and installed tests cover all-Public promotion, private dependency refusal, clone/reset, Pool separation, exact reuse and new revisions. The Playwright Pool reuse case verifies review and install in the UI. |
| C3 · Atomicity | **Pass** | `test_publication_is_recoverable_on_both_sides_of_atomic_write` injected interruptions before publication and after the atomic write. The complete capture was visible after publication, and retry reused it. A stale plan and its altered input were refused. |
| C4 · Skill | **Pass** | Inert import, protected-source refusal and a scripted OpenRouter proposal passed offline. The [live OpenRouter report](reports/skill-proposal-openrouter.json) records AgentWiz `0.2.5`, model `qwen/qwen3.8-27b`, stage `draft_review`, verdict `passed`, no issues and one workflow in the draft. Keep the report with the tested tree. |
| C5 · Worker | **Pass** | Scripted tests cover two pinned workflows, separate artifact handles, stale and cross-scope access, a missing binding, busy admission, a changed role, an exhausted model budget, Stop, and refusal of portable unknown effects. |
| C6 · Security | **Pass** | Offline suite plus `privacy_workflow_eval.py --backend offline --set all`. The native exact-send test recorded zero sends before approval, one scoped send after approval and no replay. Source regressions cover Local only, origin split, revision protection and webSpell eligibility. |
| C7 · Packaging | **Pass locally; WSL2 PDF pass confirmed by owner. Hosted CI pending.** | Exact-tree release builder smoke, source suite and installed-wheel gate. The local installed PDF check passed five fixtures and licence notices, and the installed 0.2.4 upgrade check returned 3 with the Local only output label preserved. The owner confirmed that `python scripts/check_pdf_install.py` passed on WSL2; no WSL2 output is reproduced here. |
| C8 · Documentation | **Pass** | README, DEVEL, NEWS, Workshop guide, release gates, architecture and decision history agree with closure spec v1 and Plan v17.8. Both templates were also opened in the installed Workshop browser and displayed `Unqualified`. |

### Local output excerpts

```text
Source suite: 1502 passed, 10 deselected in 279.23s (0:04:39)
Installed-wheel gate: 251 passed in 175.19s (0:02:55)
RC1 ok
RC2 ok
RC3 ok
pip check ok
smoke ok: AgentWiz 0.2.5 (modelSpell 0.4.0)
PDF install ok: Linux x86_64 3.12.14 pypdfium2=5.15.2 licence_notices=37
0.2.4 workspace upgrade passed
privacy_summary_local_v1 Unqualified in installed Workshop
privacy_summary_cloud_v1 Unqualified in installed Workshop
```

## Scope and compatibility

- Existing saved workflow captures retain their stored pins. New component revisions hash normalized declaration metadata as well as the definition. The compiler recognizes unambiguous legacy dependency pins. Legacy Shared Pool records with Private children are excluded from Pool reuse. Public promotion still requires separate review. Installed packages with a missing exact dependency stay inert until a new reviewed import resolves it.
- The tutorial test now waits for the current word-count output before making the same exact result assertion. An earlier installed run had read the preceding Tool example's successful output while the current run was settling.
- The evaluator has a detector-specific verdict backed by the complete synthetic schedule and grading controls. An offline pass or detector-specific live pass supplies no overall privacy qualification. Full provider/model attribution and the measured operating range remain missing.
- The two native private-summary definitions, transforms, prompts, Gate and workflow runner files were verified byte-identical against the base. The existing Unqualified disclosure remains visible in the wheel. Neither template is ready for production private summarisation or private Cloud use. No live private-summary OpenRouter or GPT-OSS-20B 4-bit LM Studio qualification was performed or is required for these foundations.
- Need-to-Know remains a 0.2.6 feasibility experiment. No dependencies or release pins changed. Nothing was committed, pushed or published.
