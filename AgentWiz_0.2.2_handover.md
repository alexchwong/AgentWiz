# AgentWiz 0.2.2 — implementation handover

Built against `devel` at `b2a842b` ("preparation for 0.2.1 release"), following *AgentWiz 0.2.2 — Technical
Specification* v4. Nothing was committed or pushed. The release smoke ran in a throwaway clone (`/tmp/smoke`) that
had one local commit and had its remote removed.

## What was built

- **Agentic Chat.** Chat has an **Agentic** switch. The agent loop lives in `agent_loop.py`: it captures the run,
  preflights with `check_tool_request`, runs model turns through `complete_with_tools`, and validates every request
  against the captured definitions. MCP calls classified as changing state or unknown wait for an approval bound to
  the SHA-256 of the materialised arguments. Saved blocks run as child block runs through `runner`, using
  `FrozenSource.parent_guard` and `before_tool`. Labels are promoted as restricted material is read, and the
  `restricted_result` stop applies when the destination is not trusted. The deadline is a `runner.make_timer` timer.
- **Supporting modules:**
  - `agent_tools.py`: the seven built-in tools.
  - `agent_documents.py`: staged documents and drafts, labels and memory.
  - `agent_capabilities.py`: MCP classifications, the inventory and exclusions.
  - `prompts/agent_system.md`: the system prompt.
- **Python blocks (block format 4).** A `python` step carries `file` and `engine` and runs only through scriptSpell's
  public `run` (`python_step.py`).
  - Python runs have their own budget of 4 (`state.python`).
  - If cleanup cannot be confirmed, a restart-required latch closes the budget, and the Overview shows a notice.
  - Floats are rejected.
  - A v3 `script` step loads as unusable with `host_script_retired`.
  - An engine mismatch loads as unusable with `engine_changed`. Workflow snapshots load with `engine_check=False`
    and report the mismatch at readiness.
- **Built-in Tool steps.** A Tool step can carry `builtin` instead of a server: `current_datetime`, `calculate` or
  `date_arithmetic`. These run locally, take no MCP slot and need no approval.
- **Workshop.**
  - Starters are grouped as LLM, Tool, Python and Transform.
  - New starters: `blank_python` and `builtin_utility`.
  - Python **Describe it** (`python_authoring.py`, using `YamlAuthoring`) sends at most three requests.
  - Python source can be edited in Design.
  - Script generation, the wrapper and conversion are removed.
- **Tutorial.**
  - New topics: `agentic-chat` and `python-block`.
  - The `word_count` block is now a v4 Python block.
  - The stub backend serves a new `agent-demo` model.
- **Release tooling.**
  - RC3 requires exactly the two pinned direct-URL dependencies.
  - New `--scriptspell-wheel` option.
  - The smoke covers S1–S22.
  - New `scripts/agent_eval.py` for the owner's live route evaluation.
- **Docs.** Updated README, NEWS (a 0.2.2 section; bullets of at most 20 words), DEVEL, and `docs/architecture.md`,
  `decisions.md` and `releases.md`.

## Deviations from the spec, and why

1. **Host Python retired, although the 10.6 retirement gate was only partly met.**
   - The Linux part passes.
   - The macOS and WSL2 parts could not be run here.
   - I went ahead because the spec makes retirement part of 0.2.2, and keeping script steps would contradict the
     block format 4 design.
   - **Owner action:** run the gate on macOS and WSL2 before release.
2. **Prompt version is recorded as `scriptspell-<version>`.** scriptSpell 0.3.0 exposes no separate prompt version.
   The package version is the only stable identifier available.
3. **The model's `context_length` is not shown in the preflight panel.** No modelSpell public API reports it. An
   oversized request is refused by the backend, and the README says so.
4. **Draft lineage no longer blocks execution (spec 10.4).** I deleted the tests that asserted the old refusal (listed
   below).
5. **Inventory query parameters.** The spec named the inventory endpoint but not how its inputs are passed.
   `GET /api/agent/inventory` takes `revision`, `task`, `capabilities` and `limits` as query parameters. This matches
   the existing GET routes.
6. **Conversation IDs are `conv_` plus 8 hex characters.** This mirrors the spec's `doc_` and `draft_` ID shape.
7. **`X-Local-Only` defaults to true** when a draft download response would otherwise not set it. When the header's
   value is unknown, the safer reading wins.
8. **Draft download's conversation parameter is optional.** It defaults to the live conversation; without this, the
   page's simple link returned 404.
9. **The Python budget attribute is `state.python`, not a script name.** The old `script_step` registry is gone, so
   a script-named attribute would mislead.
10. **The utilities (`date_arithmetic` and the others) use flat argument schemas and return an `invalid_arguments`
    code.** The spec gave behaviour but not schemas. Flat schemas are the simplest form that `check_tool_request`
    accepts on every route.
11. **Calculator bounds.** Decimal Emax and Emin are set to ±999, and unary minus counts toward the depth limit. The
    spec required bounded evaluation without numbers. These limits stop exponent blow-up and deep `---…` chains.
12. **A built-in tool error is reported as the step failure `tool_error`, with the specific code in the operation.**
    This reuses the existing step failure vocabulary instead of adding new top-level failures.
13. **`before_tool` may return an `authorize` check, composed with the dispatch and parent-guard checks.** This is how
    the parent's approval reaches an MCP call made inside a child Tool block, without a second approval path.
14. **No Python-authoring tutorial stage.** The two topics (`agentic-chat`, `python-block`) cover the new functions
    (DEVEL's rule that every new function gets a topic). A stage that depends on authoring would need a scripted
    author model in the tutorial, which is unnecessary complexity.
15. **Some script-support UI code remains dormant in `index.html`.** It cannot be reached: the routes and starters
    are gone. I left it to keep the page diff reviewable.
    - **Owner decision:** remove it in a cleanup pass.
16. **One test calls a pure function directly.** The daylight-saving test calls `agent_tools.current_datetime`
    rather than going through a route. This extends T3: a clock-dependent edge case cannot be driven
    deterministically through HTTP.
17. **`mcp_client.observe` now keeps each tool's schema and description, read back with `observed_schema()`.** The
    agent must give the model the exact observed schema, and nothing else held it.

## Checks run

- **Platform:** Ubuntu 24.04.5 LTS, Linux 6.18 x86_64, Python 3.13.18.
- **Offline suite:** `python -m pytest -q -m "not live"` gives **1097 passed, 10 deselected** (268 s).
  `tests/test_agent.py` has 62 tests.
- **Release smoke:** `build_release.py --smoke --modelspell-wheel … --scriptspell-wheel … --dependency-wheels …` gives
  RC1–RC3 ok, `pip check` ok and smoke S1–S22 ok, in two consecutive runs. The wheel's sha256 is `2065482f…0328`.
- **Wheel pins:** the modelSpell 0.4.0 and scriptSpell 0.3.0 wheel hashes match the spec.
- **`git diff --check`:** clean.

## Observation worth knowing

The first smoke attempt ran while the full test suite was also running. In that attempt, one Python Describe it check
returned `cleanup_failed`. The cause is in scriptSpell's `_cleanup`, which allows about 1 s each for the thread join,
the reap, and confirming the process group is gone; on a heavily loaded machine those can run out. Once cleanup is
unconfirmed, scriptSpell refuses all further runs in the process, and AgentWiz shows the restart notice. This is the
latch working as designed, and with no other load the smoke passed both times. Even so, users on loaded machines could
hit it. That is a scriptSpell decision, not something AgentWiz should paper over.

## Not verified (blocked here)

- macOS and WSL2, including the 10.6 retirement gate.
- Python 3.10.
- Live checks (`-m live`).
- `scripts/agent_eval.py` against a real model.
- The CI matrix.

## Files

`AgentWiz_0.2.2_changed.zip` holds every modified and new file (54), at their repository paths.

**Flagged obsolete — delete:**

- `agentwiz/script_step.py`
- `agentwiz/tutorial_support/blocks/wait_for_stop/` (`block.json`, `scripts/wait.py`)
- `tests/test_scripts.py`
- `tests/test_script_cleanup.py`

**Obsolete tests removed from files that remain:**

- `test_workshop.py`:
  - `test_custom_script_support_versions_are_frozen`
  - `test_captured_runtime_upgrade_preserves_starter_controls`
  - `test_generated_source_requires_ack_and_compatibility_enforces_review`
- `test_workshop_descriptions.py`: `test_old_manual_script_keeps_its_provenance_under_a_generated_head`
- `test_phase1_outbound.py`: `test_generated_script_descendant_remains_refused_after_duplication`
- `test_workflows.py`: `test_legacy_starter_conversion_adds_script_free_generation`

`tutorial_support/blocks/word_count/scripts/count_words.py` was replaced in place; its content is now a Python block
`transform`.
