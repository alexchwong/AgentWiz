# AgentWiz — Owner's CEO Pathway Trial Guide v1

**Purpose:** Compare whether different execution architectures produce useful answers, then see whether an unrestricted CEO selects a sensible path. This is an owner-directed product trial, **not** a compulsory live-provider release test, a statistical superiority claim, or a privacy qualification.

## First run

1. Launch `agentwiz`. Set your key with **OpenRouter key** or the `OPENROUTER_API_KEY` environment variable.
2. Select **CEO** beside the composer and choose an OpenRouter model with the model picker. Open
   **CEO options**, choose its **Intelligence class**, then select **Save class**.
3. Under **CEO options → Owner trials**, choose the pathway, select **Load example** to place T01 in
   **Direct brief**, then select **Create handoff**. The handoff card appears in the conversation with
   its exact text. To have the representative help instead, type the task in the composer; sending
   to the representative or to the CEO can send text to OpenRouter and incur charges. Loading the
   example is local.
4. Review **Exact text sent to the CEO (Cloud)** and select **Send to CEO**. Progress updates appear
   in the thread; expand **Activity** to see each operation, its caller path and served model, and
   **Diagnostics** for the selected pathway and accepted evidence. **Stop** in the composer cancels
   work in progress. A stopped or otherwise unfinished run keeps its record with the report unavailable.
5. Read the representative's delivery line and select **Download .md.txt** for the original report.
   Each further direct brief creates a new handoff and a new run.

The installed tutorial is available in AgentWorkshop under **Tutorial → CEO mode in Chat** and uses an
isolated scripted model. Ordinary AgentWiz **Local only** Chat remains available in **Chat** mode.

## How to use this guide

Use synthetic/public information only in AgentWiz 0.3.0. Run each task under these **development-only** policy locks, with the same model inventory and comparable whole-job limits. For cleaner comparisons, create a direct-brief handoff with the same text each time.

| Variant | Architecture policy | How it is enforced |
|---|---|---|
| **A — Solo** | The CEO answers directly. | The runtime offers a text result call. |
| **B — Tools/Blocks** | CEO may call reviewed tools and blocks, but may not delegate to specialists or launch a multi-step workflow. | Runtime restricts allowed actions. |
| **C — Reviewed workflow** | The CEO maps the brief to a pinned preset or chooses from the eligible catalogue. | The native workflow slot is acquired before CEO planning. The source stays bound to the exact brief. |
| **D — Specialist structure** | The CEO assigns the reader and then the checker. | A complete report requires the current candidate to pass the check. |
| **E — Unrestricted CEO** | The CEO chooses a feasible strategy from the run inventory. | The same permissions, budgets and Stop apply. Inspect the selected pathway in the trace. |

If a forced variant has no valid way to run a task, it must report **unsupported/blocked**, not quietly select a different architecture. Do not count an unavailable path as a failed answer. In 0.3.0, Variant D is limited to the specialist structures actually shipped; multi-team comparisons become available only in later releases.

For each task, **record the exact task, model binding, user-assigned intelligence class, architecture used, final Markdown report, cost/tokens, elapsed time, blockers, and your quality notes**. Assess substantive correctness, evidence support, uncertainty/limitations, and usefulness. If a path produces the same useful answer with fewer steps or resources, prefer the simpler path. After trying A–D, run E and consider whether its chosen method was appropriate. You can skip variants unlikely to add insight; no fixed run count is required.

## Worked comparison

Load T01, select A and submit the preview. Check the version and the exact supporting line in the report.
Start a new draft, load T01 again and select C. Inspect the two workflow node outcomes, the source quotation
and the checklist findings. Repeat with D if you want to compare a separate check of the candidate. Try E
with the same settings and judge whether its choice used a suitable amount of work.

Use B with T02 to look for a calculator action and compare the reported total with AUD 524.70. T05 is a
missing-evidence trial. Look for a question or an explicit open condition about the missing material.
A reviewed workflow or specialist may add little to straightforward arithmetic.

## Example tasks

These prompts are self-contained: copy everything inside a fenced block as the task. Replace no content unless you are deliberately testing a variant. No outside search or private material is needed.

### T01 — Simple factual lookup

**Purpose:** Identify whether a one-step answer is sufficient.

```text
From these synthetic release notes, answer which version introduced the Export Preview feature, citing the exact relevant line. Do not infer anything not in the notes.

- v1.2.0: Added project labels and profile switching.
- v1.3.0: Improved PDF import and preserved document identifiers.
- v1.4.0: Introduced Export Preview, disabled by default.
- v1.4.1: Fixed a crash in Export Preview.
```

**Look for:** Correct version, unambiguous citation, no needless delegation or invented caveats.

### T02 — Deterministic calculation

**Purpose:** Test whether tools/blocks add value when exact arithmetic matters.

```text
Prepare an invoice summary from this synthetic order, in AUD. Items: 3 licences at $120 each, 2 support units at $85 each. Apply a 10% discount to the combined pre-tax subtotal. Then apply 10% GST to the discounted amount. Show subtotal, discount, taxable amount, GST and final total. Do not round intermediate calculations.
```

**Look for:** Reproducible arithmetic, clear calculation trail and a final total of AUD 524.70. Use B with built-ins enabled to try the Calculate tool.

### T03 — Structured extraction with duplicate/conflicting entries

**Purpose:** Test when a reviewed workflow or checker helps.

```text
Make a deduplicated issue register from these synthetic notes. Keep the latest status by timestamp per ID, but report any conflicting owners separately rather than choosing an owner silently.

2026-01-02 09:00 | I-101 | owner Ada | Open | PDF conversion fails on file X
2026-01-02 09:10 | I-102 | owner Ben | Open | Model route missing
2026-01-02 11:30 | I-101 | owner Ada | Closed | Fix tested
2026-01-02 12:00 | I-103 | owner Chen | Open | Export path invalid
2026-01-02 13:00 | I-102 | owner Dana | In progress | Investigation ongoing
2026-01-02 13:05 | I-102 | owner Ben | In progress | Investigation ongoing

Report the final status for each ID, list evidence lines, and flag any unresolved owner conflict.
```

**Look for:** Full coverage, correct chronology, explicit owner conflict, traceable source support.

### T04 — Superseding document

**Purpose:** Test whether a complex path actually helps with revision precedence.

```text
Write a short, evidence-backed recommendation based only on the following synthetic supplier policy extracts.

Policy P1, issued 1 March: Supplier A must respond within 48 hours; Supplier B within 24 hours. Both require quarterly audits.
Addendum A1, issued 8 March: This addendum supersedes P1 only for Supplier A's response window. Supplier A must respond within 12 hours.
Note N1, issued 11 March: A draft memo says Supplier A is slower because it has a 48-hour response window. The note does not claim to amend P1 or A1.

Which supplier has the shorter required response window? Explain which document controls, cite the controlling words, and identify any stale conclusion.
```

**Look for:** Correct precedence, resistance to later-but-non-authoritative text, supported recommendation.

### T05 — Deliberately missing decisive evidence

**Purpose:** Test how well an architecture avoids fabricated completion.

```text
Decide whether a synthetic software release can be labelled 'fully verified'. Available evidence:
- Offline tests: passed 120/120.
- Installed browser UI smoke: passed.
- Package checksum: matches the reviewed build.
- Required external provider contract confirmation: no report supplied.
The project's release policy explicitly requires evidence of all four checks.

Write a Markdown release verdict, explain what is verified, identify missing evidence, and state the next step. Do not invent a provider check result.
```

**Look for:** Correct partial/blocked disposition and preservation of the unresolved condition in the CEO report.

### T06 — Log-based fault isolation

**Purpose:** Test whether delegation/checking improves a technical diagnosis.

```text
Diagnose this synthetic application fault. Do not claim you have run the code.

09:00:01 request POST /run 200
09:00:01 created job j17 with budget=6000 tokens
09:00:02 model returned JSON: {"status":"ok","items":3}
09:00:02 validator raised KeyError: 'evidence'
09:00:03 retry attempted, remaining budget=0
09:00:03 job ended: partial, no output artifact

The declared output schema requires: status (string), items (integer), evidence (array).

Explain the most direct cause, distinguish it from the budget exhaustion, recommend a minimal fix, and describe an offline regression test. Name anything the logs cannot establish.
```

**Look for:** Evidence-based causal chain, restrained conclusions, smallest plausible fix and test.

### T07 — Resource-constrained architectural decision

**Purpose:** Test whether complex synthesis is useful under competing constraints.

```text
Recommend one of these synthetic backup options for a small research team. Requirements: usable capacity at least 20 TB, cost no more than $4,000, maintenance should be simple, and expansion in two years is desirable.

A: $2,500; 18 TB usable; easy administration; no expansion.
B: $3,600; 24 TB usable; moderate administration; easy expansion.
C: $3,900; 40 TB usable; difficult administration; expansion possible.
D: $4,200; 36 TB usable; easy administration; easy expansion.

Deliver a Markdown decision memo with a clear recommendation, explicit elimination of ineligible choices, trade-offs and what new information could change the recommendation.
```

**Look for:** Constraint compliance, a defensible recommendation, transparent trade-offs; avoid arbitrary precision.

### T08 — Multi-perspective synthesis with contradictions

**Purpose:** Test the strongest supported specialist structure against simpler alternatives.

```text
Produce a concise Markdown risk assessment using these synthetic stakeholder notes. Separate established facts from disputed assertions.

Engineering: 'The migration can be completed in two weeks, provided API parity holds. API parity has not been verified.'
Operations: 'The cutover has only a four-hour maintenance window. Rollback automation is not tested.'
Security: 'External data export is prohibited until destination scoping passes the offline tests.'
Finance: 'The budget covers two weeks of staff time but not a third week.'
Product: 'We should announce a firm launch date now because customer demand is high.'

Recommend whether to announce a firm launch date, what evidence must be obtained first, and which risks remain unresolved. Do not invent feasibility evidence.
```

**Look for:** All perspectives represented, decisive blockers preserved, a coherent action-oriented synthesis.

### T09 — Research gap from a frozen corpus

**Purpose:** Test whether extraction, checking and synthesis offer value without live web searching.

```text
Identify justified research gaps using only these synthetic abstracts:

Paper A (2024): Tested software agent delegation with large hosted models on three programming tasks, but did not measure token cost.
Paper B (2025): Compared direct versus fixed workflows on six extraction tasks using small local models, including token cost.
Paper C (2025): Tested adaptive delegation using hosted models on the same extraction tasks and reported latency and accuracy.
Paper D (2026): Studied smaller quantised local models, direct and adaptive approaches, reporting accuracy but not latency or cost.
Paper E (2026): Benchmarked local models and fixed workflows on programming tasks, reporting accuracy and cost but no adaptive delegation.

State what has been studied, identify at most three specific untested combinations or measures supported by these descriptions, and explain why an absence from the supplied abstracts does not prove that no such work exists elsewhere.
```

**Look for:** Evidence boundary discipline, explicit counterexamples, justified rather than fabricated gaps.

## Owner's quick comparison worksheet

Copy one row per run. Do not feel obliged to execute every variant for every task.

| Brief ID | Requested | Actual | Model/provider | Class | Report and evidence quality | Open conditions | Elapsed | Tokens/USD and unknowns | Trade-offs |
|---|---|---|---|---|---|---|---|---|---|
| T01 | A | | | | | | | | |
| T01 | E | | | | | | | | |

After a few trials, write down patterns rather than a single global winner, for example: 'For simple extraction, solo usually sufficed; for conflicting amendments, the checked path was worth the overhead.' An unrestricted CEO decision should be judged against the feasible architectures *for that task*, not against a universal preference for teams or for solo execution.

## Troubleshooting

- Set the OpenRouter key and choose a supported Cloud model if the binding is missing or stale. Start a new draft after changing settings.
- Retry a busy native workflow once its slot is clear. Record an unavailable workflow as blocked in the worksheet.
- Review the exact arguments and destination shown for a read-only external tool call. Denial keeps that call from being sent.
- If the representative would use the reserved CEO allowance, submit the brief directly or start a draft with a larger allowance. If the run reaches its deadline or you select **Stop**, inspect the accepted findings and open conditions and submit a revised public brief under a new draft.
- Download reports you need before closing the workspace. A run using in-memory retention can lose its download when the app restarts.

## Limitations

This guide is intentionally subjective and small. It can inform product defaults but cannot prove general superiority. For cloud-only 0.3.0, use public/synthetic content only. User-assigned intelligence classes are planning hints rather than measured model capability. If a variant uses live inference during an owner trial, that is a voluntary product exercise, not a mandatory automated live test or an established qualification claim.


## Company preset catalogue

In **Owner trials**, select **C · Reviewed workflow**, then choose **Choose automatically** or pin a
preset by name. For a paired run, keep the brief, model and allowance the same and select **E**. The
workflow trace records the preset revision and each executed node.

| Preset | Example |
| --- | --- |
| Answer from supplied text | T01 |
| Compare versions and amendments | T04 |
| Check whether evidence is complete | T05 |
| Build an issue register | T03 |
| Profile a pasted CSV | CSV below |
| Screen options against hard constraints | T07 |
| Turn meeting notes into an action register | Meeting below |
| Triage an application fault from logs | T06 |
| Synthesize conflicting stakeholder accounts | T08 |
| Map evidence and gaps in supplied abstracts | T09 |

For a CSV trial paste the table below and ask to profile **Units**. Expect five rows, three
numbers, sum eight, one blank, one nonnumeric value, and one excess duplicate.

```csv
Item,Units
A,2
B,3
B,3
C,
D,unknown
```

For a meeting trial, paste these lines and ask for decisions and an action register. The dashboard
action should stay unassigned with an unstated due date.

```text
Decision: deploy to staging first.
Maya will prepare the staging checklist by Friday.
Action: verify the dashboard.
```

AgentWorkshop's **Workflows → Company presets** shows the pinned graph definitions and their revisions.
