"""EXPLORATORY: calibration harness for the confidence panel.

A panel that returns 1.0 for everything is indistinguishable from a panel that
works, if every role you show it is genuinely good. This harness plants roles
with known, specific defects into a real extraction and measures whether the
panel separates them from the real ones.

Each planted role targets one lens, so a failure localizes: if `duplicate_role`
scores high, the distinctness lens is broken, not the panel as a whole.

Usage:
    uv run python evaluate_panel.py output/Foo.roles.json --pdf "data/raw/.../Foo.pdf"
"""

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from confidence import LENSES, score_role
from pdf_to_text import pdf_to_text

PROJECT_ROOT = Path(__file__).parent


def quote(text, verified=True):
    return {
        "text": text,
        "location": None,
        "verified": verified,
        "match_tier": "exact" if verified else None,
        "repaired": False,
    }


def planted_roles(real: list[dict]) -> list[tuple[str, str, dict]]:
    """Build defective roles from a real extraction.

    Returns (label, lens_that_should_catch_it, role). Defects are derived from
    the actual document so they stay plausible — a straw man the panel rejects
    for the wrong reason teaches nothing.
    """
    first = real[0]
    cases = []

    # Should fail `student_role`: the teacher is explicitly out of scope, and the
    # document does say the teacher assigns roles, so the quote is real.
    cases.append((
        "teacher_as_role",
        "student_role",
        {
            "id": "teacher",
            "name": "Teacher",
            "description": "Assigns roles to students and oversees the project.",
            "responsibilities": [
                {"summary": "Role assignment", "detail": "Decides which student holds which role."}
            ],
            "supporting_quotes": [quote(q["text"]) for q in first["supporting_quotes"][:1]],
            "source": "explicit",
            "confidence": 0.9,
        },
    ))

    # Should fail `distinctness`: same position as an existing role, renamed,
    # sharing its evidence verbatim.
    cases.append((
        "duplicate_role",
        "distinctness",
        {
            "id": "camera_person",
            "name": "Camera Person",
            "description": first["description"],
            "responsibilities": first["responsibilities"][:2],
            "supporting_quotes": [quote(q["text"]) for q in first["supporting_quotes"][:2]],
            "source": "explicit",
            "confidence": 0.9,
        },
    ))

    # Should fail `evidence`: nothing in the document supports it, and its one
    # quote failed verification — so the panel sees zero verified evidence.
    cases.append((
        "fabricated_role",
        "evidence",
        {
            "id": "social_media_manager",
            "name": "Social Media Manager",
            "description": "Runs the production's social media presence and audience outreach.",
            "responsibilities": [
                {"summary": "Audience growth", "detail": "Posts behind-the-scenes content."}
            ],
            "supporting_quotes": [
                quote("The Social Media Manager builds the film's online audience.", verified=False)
            ],
            "source": "explicit",
            "confidence": 0.9,
        },
    ))

    # Should fail `overreach`: real role name, real quote, but responsibilities
    # imported from general industry knowledge the document never states.
    cases.append((
        "overreaching_role",
        "overreach",
        {
            "id": f"{first['id']}_overreach",
            "name": first["name"],
            "description": first["description"],
            "responsibilities": [
                {"summary": "Union compliance", "detail": "Ensures the shoot follows IATSE rules and files daily production reports with the guild."},
                {"summary": "Budget authority", "detail": "Approves department budgets and negotiates equipment rental contracts with vendors."},
                {"summary": "Insurance", "detail": "Secures production insurance and completion bonds before principal photography."},
            ],
            "supporting_quotes": [quote(q["text"]) for q in first["supporting_quotes"][:1]],
            "source": "explicit",
            "confidence": 0.9,
        },
    ))

    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate the confidence panel.")
    parser.add_argument("extraction", type=Path)
    parser.add_argument("--pdf", type=Path, required=True)
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")

    result = json.loads(args.extraction.read_text())
    document_text = pdf_to_text(args.pdf)
    real = result["roles"]
    names = [r["name"] for r in real]

    controls = [("REAL: " + r["name"], None, r) for r in real[:2]]
    cases = controls + planted_roles(real)

    lens_names = list(LENSES)
    header = f"{'case':<26} {'conf':>5} {'flag':>5}  " + "  ".join(f"{l[:9]:>9}" for l in lens_names)
    print(header)
    print("-" * len(header))

    caught = 0
    for label, target_lens, role in cases:
        panel = score_role(role, document_text, [n for n in names if n != role["name"]])
        by_lens = {v["lens"]: v["score"] for v in panel["lens_verdicts"]}
        cells = "  ".join(
            (f"{by_lens[l]:>8.2f}" + ("*" if l == target_lens else " ")) for l in lens_names
        )
        print(f"{label[:26]:<26} {panel['confidence']:>5.2f} {'YES' if panel['needs_review'] else '-':>5}  {cells}")

        if target_lens:
            if by_lens[target_lens] < 0.5:
                caught += 1
            else:
                for v in panel["lens_verdicts"]:
                    if v["lens"] == target_lens:
                        print(f"    MISSED by {target_lens}: {v['reasoning'][:150]}")

    planted = [c for c in cases if c[1]]
    print(f"\n* = lens expected to catch the defect")
    print(f"planted defects caught by their target lens: {caught}/{len(planted)}")


if __name__ == "__main__":
    main()
