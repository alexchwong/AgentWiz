# Workshop components and private summaries

AgentWiz uses the hierarchy user → CEO → teams → agents → workflows → blocks. Component installation gives the owner pinned definitions to inspect and test. A team installs inactive.

Open Workshop → Workflows → **Component library** to review an import, open an installed workflow, export a component or add a Public entry to the shared Pool. An exact import can be repeated safely. Configure the Pool location with `--component-pool-dir`. Component access governs reuse. Public makes the reviewed interface eligible for sharing. A Private inline helper stays scoped to its owner. Local only values and protected definition contents are governed by separate disclosure checks. Exports omit examples by default. The export API supports an explicit list of eligible component/example IDs.

The [v1 package schema](component-package-v1.schema.json) describes the envelope, nesting and exact references. Native validators check block and workflow semantics. Try the [word-count example](examples/count_words.component.json) with `one two three`. It returns `3`. Use **Edit a copy** to make an editable private workflow with the reviewed snapshots. For a standalone block, **Edit a private copy** uses the same revision and privacy-label review as a regular Workshop save. Importing a standalone `SKILL.md` saves a protected private draft. **Draft agent from skill** reviews separately supplied Cloud-eligible text and uses the `propose_agent` OpenRouter role. A generated proposal remains private and has its own installation review.

## Private summary

Open `privacy_summary_local_v1` or `privacy_summary_cloud_v1` in the workflow picker. Assign `private_summary_local` to a trusted eligible local binding. For the latter template, assign `private_summary_cloud` to a manual OpenRouter profile with one provider. Enter the source and check readiness. The Cloud variant pauses after local discovery, redaction and assessment.

At the Gate, inspect the exact candidate, destination, known-original matches, detector findings and any coverage gaps. You can approve one request, abort, or request a local rerun. For a rerun, select a missed occurrence in the private source and choose an identifier class if a correction is needed. A new run needs its own review. Restore and the final text stay Local only. The assessment is advisory and coverage describes the checks that finished.

The templates display **Unqualified** pending live evidence for the exact profile and operating range. Use synthetic data for development checks. The saved roles and any trust grants remain workspace configuration.

For setup, credentials, model selection, and the offline and live evaluation commands, see [0.2.5 privacy workflow release evidence](../DEVEL.md#025-privacy-workflow-release-evidence).

The scripted test worker saves its producer identity with existing private workflow evidence when Save runs is enabled. The artifact handles are run-scoped and expire when the worker closes.
