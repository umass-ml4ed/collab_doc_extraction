"""Measure run-to-run consistency of role extraction on one document.

The spec accepts an LLM-based extractor "provided the output is consistent and
robust across runs". This runs the same document through the extractor N times
and reports which parts of the model output agree across runs, so that claim
can be checked with data instead of asserted. temperature=0 plus a fixed seed
make runs *close* to deterministic, but OpenAI does not guarantee it.

Usage:
    uv run python check_consistency.py "<path/to.pdf>" [--runs 3] [--team-context X]

Exits non-zero if the structural dimensions (roles, relationships,
assignment_method) disagree across runs. Free-text fields (descriptions,
extraction_notes) and confidences are reported but don't affect the verdict:
wording jitter is expected, structure drift is the problem.
"""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from extract_roles import PROJECT_ROOT, extract_roles


def role_signature(result: dict) -> tuple:
    return tuple(sorted((r["id"], r["name"], r["source"]) for r in result["roles"]))


def relationship_signature(result: dict) -> tuple:
    return tuple(
        sorted(
            (rel["relationship_type"], tuple(sorted(rel["role_ids"])))
            for rel in result["role_structure"]["relationships"]
        )
    )


# Dimensions that must agree across runs for the extraction to count as stable.
STRUCTURAL_DIMENSIONS = {
    "roles (id, name, source)": role_signature,
    "relationships (type, role_ids)": relationship_signature,
    "assignment_method": lambda r: r["role_structure"]["assignment_method"],
}


def describe(value) -> str:
    """Compact one-line rendering of a dimension value for the report."""
    if isinstance(value, tuple):
        return f"{len(value)} items: {list(value)}"
    return repr(value)


def confidence_spread(results: list[dict]) -> list[str]:
    """Per-role and structure confidence ranges across runs, matched by role id."""
    lines = []
    by_id: dict[str, list[float]] = {}
    for result in results:
        for role in result["roles"]:
            by_id.setdefault(role["id"], []).append(role["confidence"])
    for role_id, values in sorted(by_id.items()):
        note = "" if len(values) == len(results) else f" (in {len(values)}/{len(results)} runs)"
        lines.append(f"  {role_id}: {min(values):.2f}-{max(values):.2f}{note}")
    structure = [r["role_structure"]["confidence"] for r in results]
    lines.append(f"  role_structure: {min(structure):.2f}-{max(structure):.2f}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="Run extraction N times and compare outputs.")
    parser.add_argument("pdf", type=Path, help="Path to the input PDF")
    parser.add_argument("--runs", type=int, default=3, help="Number of extraction runs (default 3)")
    parser.add_argument("--team-context", default=None, help="Passed through to extract_roles")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")

    results = []
    for i in range(args.runs):
        print(f"run {i + 1}/{args.runs} ...", file=sys.stderr)
        results.append(extract_roles(args.pdf, args.team_context))

    print(f"\nConsistency over {args.runs} runs: {args.pdf.name}\n")
    stable = True
    for name, signature in STRUCTURAL_DIMENSIONS.items():
        values = [signature(r) for r in results]
        if len(set(values)) == 1:
            print(f"  STABLE    {name}")
        else:
            stable = False
            print(f"  UNSTABLE  {name}")
            for i, value in enumerate(values, start=1):
                print(f"            run {i}: {describe(value)}")

    print("\nConfidence ranges across runs:")
    print("\n".join(confidence_spread(results)))

    print(f"\nVERDICT: {'STABLE' if stable else 'UNSTABLE'} "
          f"({args.runs} runs, structural dimensions only)")
    if not stable:
        sys.exit(1)


if __name__ == "__main__":
    main()
