# AgentWiz 0.2.2 starter blocks addendum — implementation handover

Built against `devel` at `cb87707` ("completed 0.2.2 handover process"), following *AgentWiz 0.2.2 — Starter
Blocks Technical Specification* v4. Nothing was committed to or pushed to GitHub. The release smoke ran in a
throwaway local clone (`/tmp/rc`) that had one local commit and no remote.

## What was built

The work follows the stages in Section 8 of the spec. Stages A–F and H are done. Stage G is the owner's
qualification and cohort runs, and it is not done (see "Owner actions").

- **Utility registry (2).** `agent_tools.REGISTRY` holds 14 utilities: the 3 existing ones and 11 new ones
  (`find_replace`, `extract_section`, `outline`, `compare_texts`, `combine_texts`, `parse_csv`, `rows_to_table`,
  `sort_dedupe_rows`, `column_stats`, `scan_identifiers`, `check_identifier_map`).
  - `UTILITY_TOOLS`, `SCHEMAS`, `DESCRIPTIONS` and `blocks.BUILTIN_TOOLS` are all derived from the registry.
  - `run_utility(name, arguments, *, result_limit, check)` keeps its call shape.
  - Every utility checks the encoded size of its arguments and computes its output size before allocating.
  - Long loops call `check` every 1,000 steps.
  - The error codes are fixed, and messages never quote input.
- **Publication path (3.2).** Drafts are published in four steps on the conversation. The `Manager` reservation is
  verified on `attach` and abandoned idempotently. `while_idle` covers staging, memory and labels. `close()` waits
  for pending writes.
- **PDF (4).** `pdf_reader` (parent) and `pdf_worker` (child; the only module that imports pypdfium2).
  - Admission is `state.pdf`.
  - Bodies are read in chunks (64 KiB, 60 s).
  - Coverage reaches the Documents preview, staged items and `list_documents`.
  - New text formats: `.tsv`, `.log`, `.yaml`, `.yml`.
- **Document tools (3).** `copy_to_draft`, `revise_draft`, `compare_documents` and `read_table`. Restricted material
  read for a draft promotes the run before the read.
- **Library (5).** 30 entries under `agentwiz/library/`, plus `index.json`.
  - `library_install.Installer` (`state.block_library`) provides the listing, plan, add (binding digest and settings
    hold), Finish adding and provenance.
  - `workshop_service` gains `only_revision` and `save(..., provenance=)`.
  - The Workshop dialog is **Add from library**, and the Design view shows the library source.
- **Tutorial (5.4).** New topic `block-library`. It adds `find_and_replace` and `calculate` through the installer,
  tests `find_and_replace`, and runs `calculate` from agentic Chat.
  - The `agent-demo` stub calls `block_calculate_<h8>` when that tool is offered. It finds the block result inside
    the JSON text.
- **Workflows (6).** The CSV→sort→table wiring, the model→table wiring and the de-identification chain all run as
  offline workflow tests (`tests/test_library_workflows.py`).
- **Evaluation (7).** See "Evaluation scripts" below.
- **Smoke.** Adds S23 (library add, then a workflow), S24 (packaged PDF fixtures on both routes, including partial
  coverage) and S25 (scripted `copy_to_draft`, then `revise_draft`).
- **Docs.**
  - README: Block library, utilities, document tools, PDF and the worker boundary, new formats, limitations.
  - NEWS: 7 new bullets, at most 16 words each.
  - `docs/architecture.md`, `docs/attachment_interface.md` (Sections 1 and 8) and `docs/releases.md` (RC2, RC3,
    S23–S25 and the owner sign-off).

### Evaluation scripts

- `tests/library_eval/<key>.json` holds 10 cases for each of the 14 model blocks.
- `tests/library_eval/checks.py` holds the invariants. Hard invariants: `classify_text`, `answer_from_source` and
  `deidentify_text`.
- `scripts/library_eval.py` is the owner-run qualification runner.
- `scripts/agent_eval.py --cohorts tests/agent_eval/cohorts.json` holds 6 cohorts of at most 8 capabilities each, plus
  the 5-scenario Chat gate.

## Measurements

- **Reference machine:** Linux 6.18 x86_64, Intel Xeon @ 2.10 GHz, Python 3.13.18.
- **Utility worst cases** (`scripts/utility_stress.py`, idle). All are under the 1 s target:

  | Utility | Time (s) |
  | --- | --- |
  | `check_identifier_map` | 0.52 |
  | `outline` | 0.17 |
  | `scan_identifiers` | 0.12 |
  | `parse_csv` | 0.04 |
  | `rows_to_table` | 0.02 |
  | `column_stats` | 0.02 |
  | `sort_dedupe_rows` | 0.02 |
  | `combine_texts` | 0.01 |
  | `find_replace` | 0.01 |
  | `compare_texts` | 0.002 |
  | `extract_section` | 0.002 |

  - `compare_texts` keeps its diff work limit of 2,000,000 line pairs. Adversarial repeated-line inputs at that size
    took at most 0.04 s. difflib's popular-line heuristic keeps them cheap.
  - `scan_identifiers` and `check_identifier_map` needed changes to reach the target: a 7-character minimum on the
    phone and long-number patterns, and a precomputed token set for the collision check.
- **`RLIMIT_AS` = 128 MiB.** The spec's provisional value was 1 GiB. 128 MiB is the smallest value under which every
  fixture extracts, including `near_limit` (300 pages, about 15.9 MiB).
  - On Python 3.13, the worker needed about 126 MiB for `near_limit`.
  - On Python 3.11 and 3.12 it needed at most 110 MiB.
  - With pypdfium2 5.15.2 (the newest in range), `near_limit` also extracts at 112 MiB in-process.
  - The headroom on 3.13 is small. If a later pypdfium2 or Python grows, `near_limit` will start failing with
    `extraction_failed`. That fixture's test catches it.
- **pypdfium2:**
  - Tested version: 5.13.0, which bundles PDFium 153.0.7999.0 (origin `pdfium-binaries`).
  - Its licence notices are present in the installed wheel's `.dist-info/licenses/`: `LICENSES/` with Apache-2.0,
    BSD-3-Clause and CC-BY-4.0, and `data/linux_x64/BUILD_LICENSES/` with PDFium and its third-party notices.
  - Only Linux x86-64 was tested.

## Deviations from the spec, and why

1. **The installer is `state.block_library`, not `state.library`.** `state.library` is already the shared profiles
   library (`pf.Library`), and renaming that would touch every profile path.
2. **`deidentify_text` and `table_from_text` use an open object item schema** for their pair and row items: no
   `required` list and `additionalProperties: true`. The contract dialect refuses `required` on object items inside
   arrays. The runner invariants in `checks.py` cover the shape that the contract cannot express.
3. **`library_eval.py` has options the spec did not list:**
   - `--route` (default `lmstudio`): the target route that the profile must use.
   - `--profiles-dir` and `--config`: where the owner's shared profiles and server configuration are.
   - `--report`: where the report is written.

   In the disposable workspace it also records the profile's destination as trusted, but only when that destination
   is on this machine. Without this, `deidentify_text` (Local only ports) cannot run. Nothing outside the temporary
   folder is touched.
4. **`context_rejected` is inferred.** The block result carries `model_error` but not the server's reason. The runner
   reads each attempt's error from the run store and classifies the run as `context_rejected` when that error
   mentions "context". Any other failure that is not `truncated` is reported as `contract_failed`, with the failure
   code as the reason.
5. **The report leaves some target fields to the owner.** The model file, quantisation, context length, LM Studio
   version, reasoning setting, temperature and token budget are not visible to AgentWiz, so they appear as `null`
   under `target.owner_fill`.
6. **Cohort cases use `expected_capabilities`** (all must appear in the trace; an empty list means no tool) rather
   than a single first capability. Several cohort tasks need two tools (read, then the block), so "correct choice"
   cannot be judged from the first call alone.
7. **The README says library use from Chat is untested.** The 7.2 gate has not run on a target orchestrator, so the
   README cannot yet claim Chat support (Section 7.2).
8. **`split_lines` moved** from `agent_documents` to `agent_tools`, and `agent_documents` imports it. The utilities
   need it, and `agent_tools` must not import `agent_documents`.
9. **The tutorial resolves the block capability by name prefix** (`block_calculate_`) in the page and the stub, as
   5.4 says. `agent_eval.py` resolves library capabilities by block identity instead, which also works for renamed
   installs.

## Checks run

- **Offline suite:** `python -m pytest -q -m "not live"` gives **1322 passed, 10 deselected**. The baseline was 1100.
  New test files:
  - `test_utilities.py`
  - `test_pdf.py`
  - `test_agent_document_tools.py`
  - `test_library.py`
  - `test_library_workflows.py`
  - `test_library_eval.py`
  - `test_page_library.py`
  - plus the `block-library` topic in `test_tutorial.py`.
- **Release smoke:** `build_release.py --smoke --no-isolation --modelspell-wheel … --scriptspell-wheel …
  --dependency-wheels …` gives RC1–RC3 ok, `pip check` ok and S1–S25 ok, in two consecutive runs.
  - The wheelhouse had `pypdfium2==5.13.0` and `pydantic-monty==1.5.0`.
  - The wheel's sha256 is `c4f9a5c2e0aebd8846cad34e067f42167770b9ae89d67bdde584882c34a62d2c`.
  - The two pinned wheels' hashes match `pyproject.toml`.
- **`git diff --check`:** clean.

## Owner actions (before the release merge to `main`)

1. **Qualification (7.1).** Use an OpenRouter or LM Studio profile as described in the [release instructions](docs/releases.md).
   Run the dev set once, revise prompts, then run `--trials 3 --set all` for the ready blocks. Mark the rubric cases
   and refresh the verdict with `--review`.
   - Remove any block still below the bar after three rounds from `index.json`, together with its cases, tutorial and
     README references.
   - Each model block's description currently states the context it needs (for example 32,768 tokens) but names no
     tested model, because none has been tested yet. Section 10 requires the tested model and context, so add them
     from the report.
2. **Deferred `deidentify_text`.** The Local only model block is out of the 0.2.2 catalog and wheel pending qualification
   on a trusted local profile. Its `max_tokens` is 12,288, and a long input near its maximum may not fit a 32,768-token
   context. If a future qualification shows `context_rejected` or `truncated` on the long case, lower the input maximum
   or raise the role's context.
3. **Agent cohorts and the Chat gate (7.2).** Run `python scripts/agent_eval.py --url … --cohorts
   tests/agent_eval/cohorts.json --report …` against a disposable workspace. Only if all five gate scenarios pass,
   change the README's Block library "Quality" paragraph to claim Chat support.
4. **Wheel matrix (4.3).** Check that pypdfium2 installs and extracts the fixtures on Linux arm64, macOS arm64 and
   x86-64, WSL2 and Python 3.10–3.12. On a target without a usable wheel, PDF moves to an `agentwiz[pdf]` extra.
5. **PDF on macOS.** Decide Section 9, item 1: macOS has no memory containment for the PDF worker.
6. **Outstanding from the baseline handover:** the macOS and WSL2 checks (including the host-Python retirement
   gate), Python 3.10, live checks and the live agent evaluation.

## Not verified (blocked here)

- macOS, WSL2, Linux arm64 and Python 3.10–3.12 for the new code. RLIMIT_AS was measured on 3.11 and 3.12, but the
  suite did not run there.
- Any run of `library_eval.py` or `agent_eval.py --cohorts` against a real model. Both were exercised only against the
  scripted stub.
- The CI matrix.

## Files

`AgentWiz_0.2.2_starter_blocks_changed.zip` holds every modified and new file at its repository path: 26 modified
and 120 new, plus this handover.

**Flagged obsolete — delete:** none. No file of the baseline is made obsolete by this addendum, and no test was
removed. `tests/test_attachments.py` changed one case from `.pdf` (now supported) to `.docx` (still unsupported).

The generator scripts that wrote `agentwiz/library/` and `tests/library_eval/` were scaffolding and are not
included. The packaged JSON is the source of truth.
