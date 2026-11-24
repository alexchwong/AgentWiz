"""Per-turn answer instructions for the Chat composer."""

TASKS = {
    "general": ("General", ""),
    "tldr": ("TL;DR", "Give the gist in up to three bullets. Keep any caveat that would change the takeaway."),
    "conclusion_first": ("Conclusion first", "Lead with the direct answer, then give the reasoning and material caveats."),
    "executive_summary": ("Executive summary",
                          "State the decision or recommendation, why it matters, the main trade-off, and the next action."),
    "deep_dive": ("Deep dive", "Explain how it works, the supporting evidence, edge cases, and remaining uncertainty."),
    "reverse_engineer": ("Reverse engineer",
                         "Infer how the system works from observable behaviour. Separate confirmed facts from hypotheses "
                         "and identify what would test each hypothesis."),
    "strongest_case": ("Strongest case for",
                       "Make the strongest evidence-based case in favour. Flag assumptions and evidence that would "
                       "materially weaken it."),
    "adversarial_critic": ("Adversarial critic",
                           "Identify consequential failure modes, missing evidence, and counterexamples. Rank objections "
                           "and suggest how to resolve them."),
    "compare_recommend": ("Compare and recommend",
                          "Compare options using the user's priorities. Recommend one and explain what would change that "
                          "recommendation."),
    "evidence_gaps": ("Find evidence gaps",
                      "Identify missing comparisons, underrepresented populations, weak methods, and contradictions in "
                      "the supplied evidence. State what would close each gap. Limit conclusions to that evidence set."),
}
