# AgentWiz 0.2.2 existing-workspace fixture

Frozen. Produced by the published AgentWiz 0.2.2 wheel; never rewrite a file here. The upgrade tests (`tests/test_upgrade_v0_2_2.py`) copy it before opening it. Regenerate only with the command below, then review the whole diff.

- Command: `python scripts/make_upgrade_fixture.py --agentwiz-wheel agentwiz-0.2.2-py3-none-any.whl --output tests/fixtures/workspaces/v0_2_2`
- Wheel: `agentwiz-0.2.2-py3-none-any.whl`, SHA-256 `fad5de0c335c60f0bf77bbea06656df7a20d1e66011d62432b94c4cba44141da`
- Date: 2026-11-20

Contents: the managed protected model block `guarded`; the `private_summary_release` workflow with its blocks, roles and Release Gate and one saved run of it; one saved Checked request run with a failed attempt and its manual repair; the Python block `described`, saved through Describe it (`origin: llm_generated`); the unmanaged v3 host-script block `host_script`, written by hand, whose script writes a canary file if it ever runs; the profiles and roles they need. Trust grants name the generator's stand-in addresses, which no longer exist; the tests grant their own fakes.
