"""Extract role structure (roles.v1 schema) from a single PDF.

Usage:
    uv run python extract_roles.py "data/raw/HTH - Western Films/Western Films Job Descriptions.pdf" \
        --team-context western_films
"""

import argparse
import hashlib
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from openai import OpenAI

from pdf_to_text import pdf_to_text
from schema import ROLE_EXTRACTION_RESPONSE

PROJECT_ROOT = Path(__file__).parent
MODEL = "gpt-4.1-mini"
SCHEMA_VERSION = "roles.v1"
PROMPT_VERSION = "role_extraction_v1"

SYSTEM_MESSAGE = (
    "You extract structured role information from curricular documents. "
    "You quote source text verbatim and never fabricate evidence."
)


def render_prompt(filename: str, document_text: str) -> str:
    env = Environment(
        loader=FileSystemLoader(PROJECT_ROOT / "prompts"),
        undefined=StrictUndefined,
    )
    template = env.get_template(f"{PROMPT_VERSION}.j2")
    return template.render(filename=filename, document_text=document_text)


ELLIPSIS_SPLIT = re.compile(r"\s*(?:\.\.\.|…)\s*")


def _verbatim_or_none(fragment: str, norm_doc: str) -> str | None:
    """Return the fragment (whitespace-normalized) if it appears verbatim in
    the document, trimming up to a few stitched-on leading words if needed."""
    words = fragment.split()
    for start in range(0, min(len(words), 8)):
        candidate = " ".join(words[start:])
        if candidate and candidate in norm_doc:
            return candidate
    return None


def repair_quotes(extracted: dict, document_text: str) -> None:
    """Normalize quote whitespace and repair the model's known failure mode:
    stitching distant passages together (with '...' or a prepended role name).

    Stitched quotes are split into one quote object per verbatim span. Quotes
    that still can't be verified are left untouched for validation to flag.
    Whitespace is normalized throughout because pypdf's text extraction
    introduces irregular spacing that isn't in the real document.
    """
    norm_doc = " ".join(document_text.split())

    def repair_list(quotes: list[dict]) -> list[dict]:
        repaired = []
        for q in quotes:
            normalized = " ".join(q["text"].split())
            if normalized in norm_doc:
                repaired.append({**q, "text": normalized})
                continue
            fragments = [f for f in ELLIPSIS_SPLIT.split(normalized) if f]
            fixed = [_verbatim_or_none(f, norm_doc) for f in fragments]
            if fixed and all(fixed):
                repaired.extend({**q, "text": t} for t in fixed)
            else:
                repaired.append(q)
        return repaired

    for role in extracted["roles"]:
        role["supporting_quotes"] = repair_list(role["supporting_quotes"])
    for rel in extracted["role_structure"]["relationships"]:
        rel["supporting_quotes"] = repair_list(rel["supporting_quotes"])


def validate_extraction(extracted: dict, document_text: str) -> list[str]:
    """Sanity-check model output: verbatim quotes and valid role_id references.

    Whitespace is normalized before quote matching because pypdf's extraction
    itself introduces irregular spacing.
    """
    warnings = []
    norm_doc = " ".join(document_text.split())

    def check_quotes(quotes, owner):
        for q in quotes:
            if " ".join(q["text"].split()) not in norm_doc:
                warnings.append(f"{owner}: quote not verbatim: {q['text'][:80]!r}")

    role_ids = {r["id"] for r in extracted["roles"]}
    for role in extracted["roles"]:
        check_quotes(role["supporting_quotes"], f"role {role['id']}")
    for i, rel in enumerate(extracted["role_structure"]["relationships"]):
        check_quotes(rel["supporting_quotes"], f"relationship {i}")
        for rid in rel["role_ids"]:
            if rid not in role_ids:
                warnings.append(f"relationship {i}: unknown role_id {rid!r}")
    return warnings


def extract_roles(pdf_path: Path, team_context: str | None) -> dict:
    document_text = pdf_to_text(pdf_path)
    prompt = render_prompt(pdf_path.name, document_text)

    client = OpenAI()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_MESSAGE},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_schema", "json_schema": ROLE_EXTRACTION_RESPONSE},
        temperature=0,
        seed=42,
    )
    extracted = json.loads(response.choices[0].message.content)

    repair_quotes(extracted, document_text)
    for warning in validate_extraction(extracted, document_text):
        print(f"WARNING: {warning}", file=sys.stderr)

    now = datetime.now(timezone.utc).astimezone()
    checksum = hashlib.sha256(pdf_path.read_bytes()).hexdigest()

    return {
        "extraction_id": f"ext_{now:%Y-%m-%d}_{uuid.uuid4().hex[:8]}",
        "source_document": {
            "filename": pdf_path.name,
            "document_type": extracted["document_type"],
            "checksum": f"sha256:{checksum}",
        },
        "extracted_at": now.isoformat(timespec="seconds"),
        "extractor": {
            "model": MODEL,
            "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
        },
        "team_context": team_context,
        "roles": extracted["roles"],
        "role_structure": extracted["role_structure"],
        "extraction_notes": extracted["extraction_notes"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract roles.v1 JSON from one PDF.")
    parser.add_argument("pdf", type=Path, help="Path to the input PDF")
    parser.add_argument("--team-context", default=None, help="Team/teacher this document belongs to")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "output")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")

    result = extract_roles(args.pdf, args.team_context)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / f"{args.pdf.stem}.roles.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")

    role_names = [r["name"] for r in result["roles"]]
    print(f"Wrote {out_path}")
    print(f"Roles ({len(role_names)}): {', '.join(role_names) or 'none'}")
    print(f"Relationships: {len(result['role_structure']['relationships'])}")


if __name__ == "__main__":
    main()
