"""EXPLORATORY: confidence scored by a panel of independent evaluators.

Status: experiment, not settled design. See EXPERIMENT.md before building on it.

The extractor currently scores its own confidence, which asks one model call to
both make a claim and audit it. That number tends to compress into 0.9-0.95 and
carries little signal about which roles a human should actually look at.

Here, confidence is instead produced by a separate panel. Each evaluator sees
the role and the document through ONE lens, scores only that lens, and never
sees the other evaluators' verdicts — so agreement between them is evidence
rather than an echo. Evaluators are shown only quotes that passed verification
in verify.py, plus a count of how many failed; a role propped up by text that
isn't in the document should not be able to score well.

Disagreement between lenses is treated as a first-class signal. A role every
lens likes is safe; a role that splits the panel is exactly what a human should
review, and a mean would hide that.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from openai import OpenAI

from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
EVAL_MODEL = os.getenv("ROLE_EVAL_MODEL", "gpt-4.1-mini")
EVAL_PROMPT_VERSION = "confidence_eval_v1"

# Thresholds for flagging a role for human review. Deliberately explicit rather
# than tuned — nobody has looked at enough scored output to tune them yet.
LOW_LENS_SCORE = 0.5
HIGH_PANEL_SPREAD = 0.3

EVALUATOR_SYSTEM_MESSAGE = (
    "You audit structured extractions against their source document. "
    "You are rewarded for catching real problems, not for agreeing with the extractor."
)

LENSES = {
    "evidence": (
        "EVIDENCE. Do the verified quotes actually establish that this is a role — "
        "a position a student occupies — rather than a task, a deliverable, a phase "
        "of the project, or a piece of equipment? Ask whether the quotes support the "
        "specific description and responsibilities claimed, or whether they merely "
        "mention the name in passing. A role whose only evidence is its name appearing "
        "in a list is weakly evidenced."
    ),
    "distinctness": (
        "DISTINCTNESS. Is this a genuinely separate role, or is it a duplicate, an "
        "alias, or a sub-facet of another role the extractor already listed? Documents "
        "often name the same position several ways ('DP', 'Director of Photography', "
        "'camera'). Consider also the opposite error: whether this single entry has "
        "collapsed two roles the document actually treats as distinct."
    ),
    "student_role": (
        "STUDENT ROLE. Is this a position a STUDENT occupies in the assignment? The "
        "extraction is scoped to student roles only. Teachers, outside mentors, "
        "clients, judges, and audiences are out of scope unless students are asked to "
        "take on that position themselves. Named film-industry job titles are in scope "
        "when students are the ones filling them."
    ),
    "overreach": (
        "OVERREACH. Do the description and responsibilities stay within what the "
        "document actually says, or has the extractor filled in plausible detail from "
        "general knowledge of how such a role usually works? A 'Director of "
        "Photography' entry that describes standard industry duties the document never "
        "mentions is overreach, however accurate it may be about filmmaking."
    ),
}

LENS_VERDICT_SCHEMA = {
    "name": "lens_verdict",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "score": {
                "type": "number",
                "description": "0.0-1.0 confidence that the role is sound through this lens.",
            },
            "reasoning": {
                "type": "string",
                "description": "1-3 sentences justifying the score, citing the document.",
            },
            "concerns": {
                "type": ["string", "null"],
                "description": "Specific problem found, or null if none.",
            },
        },
        "required": ["score", "reasoning", "concerns"],
        "additionalProperties": False,
    },
}


def _render(role: dict, document_text: str, lens: str, other_roles: list[str]) -> str:
    env = Environment(
        loader=FileSystemLoader(PROJECT_ROOT / "prompts"), undefined=StrictUndefined
    )
    quotes = role["supporting_quotes"]
    return env.get_template(f"{EVAL_PROMPT_VERSION}.j2").render(
        lens_instruction=LENSES[lens],
        role=role,
        verified_quotes=[q for q in quotes if q.get("verified")],
        unverified_count=sum(1 for q in quotes if not q.get("verified")),
        other_roles=other_roles,
        document_text=document_text,
    )


def score_lens(role: dict, document_text: str, lens: str, other_roles: list[str]) -> dict:
    """Run one evaluator. Returns {lens, score, reasoning, concerns}."""
    client = OpenAI()
    response = client.chat.completions.create(
        model=EVAL_MODEL,
        messages=[
            {"role": "system", "content": EVALUATOR_SYSTEM_MESSAGE},
            {"role": "user", "content": _render(role, document_text, lens, other_roles)},
        ],
        response_format={"type": "json_schema", "json_schema": LENS_VERDICT_SCHEMA},
        temperature=0,
        seed=42,
    )
    return {"lens": lens, **json.loads(response.choices[0].message.content)}


NO_EVIDENCE_CAP = 0.5


def aggregate(verdicts: list[dict], verified_count: int, unverified_count: int) -> dict:
    """Combine lens verdicts into one confidence, preserving the disagreement.

    The panel score is the MEAN, but it is not the whole answer: `needs_review`
    fires when any single lens is alarmed or when the lenses disagree sharply.
    One lens at 0.2 among three at 0.9 averages to a comfortable 0.72 while
    describing a role that is probably wrong — the mean alone would bury that.

    A role with no verified quote at all is capped at NO_EVIDENCE_CAP regardless
    of how the panel voted. Evaluators reason from the document and can talk
    themselves into a role that is genuinely there; but an extraction offering no
    checkable evidence for it is not a finished result, and the score should say
    so rather than let eloquence substitute for a citation.
    """
    scores = [v["score"] for v in verdicts]
    mean = sum(scores) / len(scores)
    spread = max(scores) - min(scores)
    weakest = min(verdicts, key=lambda v: v["score"])

    reasons = []
    if weakest["score"] < LOW_LENS_SCORE:
        reasons.append(f"{weakest['lens']} lens scored {weakest['score']:.2f}")
    if spread > HIGH_PANEL_SPREAD:
        reasons.append(f"panel disagreed (spread {spread:.2f})")
    if unverified_count:
        reasons.append(f"{unverified_count} quote(s) failed verification")
    if not verified_count:
        reasons.append("no verified supporting quotes")

    confidence = min(mean, NO_EVIDENCE_CAP) if not verified_count else mean

    return {
        "confidence": round(confidence, 3),
        "panel_mean": round(mean, 3),
        "panel_spread": round(spread, 3),
        "needs_review": bool(reasons),
        "review_reasons": reasons,
        "lens_verdicts": verdicts,
    }


def score_role(role: dict, document_text: str, other_roles: list[str]) -> dict:
    """Run the full panel against one role, lenses concurrently."""
    with ThreadPoolExecutor(max_workers=len(LENSES)) as pool:
        verdicts = list(
            pool.map(lambda lens: score_lens(role, document_text, lens, other_roles), LENSES)
        )
    quotes = role["supporting_quotes"]
    return aggregate(
        verdicts,
        verified_count=sum(1 for q in quotes if q.get("verified")),
        unverified_count=sum(1 for q in quotes if not q.get("verified")),
    )


def score_extraction(result: dict, document_text: str) -> dict:
    """Score every role in an extraction. Mutates roles in place; returns a summary.

    The extractor's self-assessed score is preserved as `confidence_self_reported`
    so the two can be compared — the open question this experiment exists to answer.
    """
    roles = result["roles"]
    names = [r["name"] for r in roles]

    with ThreadPoolExecutor(max_workers=4) as pool:
        panels = list(
            pool.map(
                lambda r: score_role(r, document_text, [n for n in names if n != r["name"]]),
                roles,
            )
        )

    for role, panel in zip(roles, panels):
        role["confidence_self_reported"] = role["confidence"]
        role["confidence"] = panel["confidence"]
        role["confidence_panel"] = panel

    flagged = [r["name"] for r, p in zip(roles, panels) if p["needs_review"]]
    deltas = [
        r["confidence"] - r["confidence_self_reported"] for r in roles
    ]
    return {
        "evaluator": {
            "model": EVAL_MODEL,
            "prompt_version": EVAL_PROMPT_VERSION,
            "lenses": list(LENSES),
        },
        "roles_scored": len(roles),
        "roles_needing_review": flagged,
        "mean_delta_vs_self_reported": round(sum(deltas) / len(deltas), 3) if deltas else 0.0,
    }
