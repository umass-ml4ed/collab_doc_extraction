# Experiment: agentic confidence scoring

**Status:** exploratory. Not merged, not settled. Branched off `verbatim-verification`
(PR #1) because the panel depends on knowing which quotes verified.

## The problem

The extractor scores its own confidence — one model call both makes a claim and audits
it. In practice that number compresses hard: every role in `Western Films Job
Descriptions.pdf` came back at **0.98**. A score that never varies can't do the job it
exists for, which is telling a human which roles to look at.

## The approach

Confidence comes from a separate panel of evaluators (`confidence.py`). Each sees the
role and the document through **one lens**, scores only that lens, and never sees the
others' verdicts — so agreement is evidence rather than an echo.

| lens | asks |
| --- | --- |
| `evidence` | Do the verified quotes establish a *role*, or a task/deliverable/phase? |
| `distinctness` | Is this separate from the other roles, or an alias or sub-facet? |
| `student_role` | Is this a position a *student* occupies? (spec scopes out teachers, mentors, audiences) |
| `overreach` | Do the responsibilities stay inside the document, or import industry knowledge? |

Evaluators see **only quotes that passed verification**, plus a count of how many
failed. A role propped up by text that isn't in the document shouldn't be able to score
well, and a role with no verified quote at all is capped at 0.5 regardless of the vote.

Aggregation keeps the disagreement rather than dissolving it: `confidence` is the mean,
but `needs_review` fires independently when any single lens drops below 0.5, when the
panel spread exceeds 0.3, or when quotes failed verification.

## Calibration

A panel that returns 1.0 for everything looks identical to a working panel if every role
you show it is good. `evaluate_panel.py` plants roles with known, specific defects —
each targeting one lens, each built from the real document so it stays plausible — and
checks whether the panel separates them.

```
uv run python evaluate_panel.py output/Foo.roles.json --pdf "data/raw/.../Foo.pdf"
```

Result on `Western Films Job Descriptions.pdf`:

```
case                        conf  flag   evidence  distinctn  student_r  overreach
----------------------------------------------------------------------------------
REAL: Producer              1.00     -      1.00       1.00       1.00       1.00
REAL: Director              1.00     -      1.00       1.00       1.00       1.00
teacher_as_role             0.10   YES      0.10       0.20       0.00*      0.10
duplicate_role              0.03   YES      0.00       0.10*      0.00       0.00
fabricated_role             0.03   YES      0.00*      0.10       0.00       0.00
overreaching_role           0.82   YES      1.00       1.00       1.00       0.30*

planted defects caught by their target lens: 4/4
```

## What this actually shows

**The panel discriminates.** 4/4 planted defects caught, real roles cleanly separated.
The 1.00s on real roles are a real judgment, not a rubber stamp — the same evaluators
score a fabricated role at 0.00.

**The lenses are not independent, and that matters.** A gross defect tanks *every* lens,
not just its target: the fabricated role scored ≤0.10 across the board. So the panel is
closer to four correlated votes than four independent ones, and most of the time it is
paying 4× for redundant signal.

**The one case where the panel earns its cost is the subtle one.** `overreaching_role`
is the only case with genuine lens separation — 1.00 / 1.00 / 1.00 / **0.30**. A real
role name, a real verified quote, responsibilities quietly imported from general
filmmaking knowledge. This is the failure mode a human reviewer would most likely miss,
and only one lens saw it.

**Which means the mean is the wrong headline number.** On that same case the mean is
**0.82** — comfortably high, and wrong. `needs_review` is what caught it. If this ships,
the flag is the product and the scalar is decoration; anyone consuming
`role["confidence"]` alone would be misled precisely in the case that matters most.

**Scores are barely calibrated.** Observed values cluster at 0.00, 0.10, 0.30, and 1.00.
The panel behaves like a near-binary classifier with an occasional middle verdict, so
treating its output as a continuous 0.0–1.0 confidence overstates what it knows.

## Open questions

- **Is the extractor's 0.98 wrong, or is this document just easy?** It's a job
  descriptions table — the friendliest input in the corpus. The panel agreeing (1.00)
  may mean both are right, not that the panel adds anything. Needs a messier document,
  ideally one where roles are inferred rather than named.
- **Do the correlated lenses justify their cost?** 4 lenses × N roles is 4N extra calls
  per document, ~32 for this one against 1 for the extraction itself. If `overreach` is
  the only lens contributing independent signal on real documents, a cheaper design is
  one evaluator plus one overreach specialist.
- **No ground truth.** "Planted defect" is my judgment, not a labeled set. Everything
  above measures whether the panel agrees with me about obvious cases.
- **The evaluators share the extractor's model and blind spots.** A defect `gpt-4.1-mini`
  can't see when writing, it likely can't see when auditing. Worth running the panel on
  a different model — `ROLE_EVAL_MODEL` env var exists for this.

## Not done

- Not wired into `extract_roles.py`. `score_confidence.py` re-scores an existing
  extraction instead, so lenses and thresholds can be iterated without re-extracting —
  and so this branch doesn't conflict with PR #1 while that's under review.
- No tests. The mechanism is a few API calls and a mean; the thing worth testing is
  calibration, which is what `evaluate_panel.py` does against real documents.
- Thresholds (0.5, 0.3) are stated, not tuned. Nobody has looked at enough scored output.

## Files

| file | |
| --- | --- |
| `confidence.py` | lenses, evaluator calls, aggregation |
| `prompts/confidence_eval_v1.j2` | evaluator prompt |
| `score_confidence.py` | re-score an existing `.roles.json` |
| `evaluate_panel.py` | plant defects, measure separation |
