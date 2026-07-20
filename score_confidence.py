"""EXPLORATORY: re-score an existing extraction's confidence with the evaluator panel.

Takes a roles.v1 JSON that extract_roles.py already produced and replaces each
role's self-reported confidence with a panel score. Separate from extraction on
purpose: iterating on lenses and thresholds is the point of this experiment, and
re-running extraction each time would be slow, costly, and would change the
extraction under you while you are trying to compare scoring approaches.

Usage:
    uv run python score_confidence.py output/Foo.roles.json --pdf "data/raw/.../Foo.pdf"
"""

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from confidence import score_extraction
from pdf_to_text import pdf_to_text

PROJECT_ROOT = Path(__file__).parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-score an extraction's confidence.")
    parser.add_argument("extraction", type=Path, help="Path to a .roles.json file")
    parser.add_argument(
        "--pdf", type=Path, required=True, help="Source PDF the extraction came from"
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Where to write the scored copy (default: alongside, .scored.json)",
    )
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")

    result = json.loads(args.extraction.read_text())
    if result["source_document"]["filename"] != args.pdf.name:
        print(
            f"WARNING: extraction is from {result['source_document']['filename']!r} "
            f"but --pdf is {args.pdf.name!r}"
        )

    summary = score_extraction(result, pdf_to_text(args.pdf))
    result["confidence_scoring"] = summary

    out = args.output or args.extraction.with_suffix("").with_suffix(".scored.json")
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")

    print(f"Wrote {out}\n")
    print(f"{'role':<44} {'self':>6} {'panel':>6} {'spread':>7}  review")
    for role in result["roles"]:
        panel = role["confidence_panel"]
        flag = "; ".join(panel["review_reasons"]) if panel["needs_review"] else ""
        print(
            f"{role['name'][:44]:<44} "
            f"{role['confidence_self_reported']:>6.2f} "
            f"{role['confidence']:>6.2f} "
            f"{panel['panel_spread']:>7.2f}  {flag}"
        )
    print(f"\nmean delta vs self-reported: {summary['mean_delta_vs_self_reported']:+.3f}")
    print(f"flagged for review: {len(summary['roles_needing_review'])}/{summary['roles_scored']}")


if __name__ == "__main__":
    main()
