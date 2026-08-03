"""Extract role structure (roles.v1 schema) from a single PDF.

Usage:
    uv run python extract_roles.py "data/raw/HTH - Western Films/Western Films Job Descriptions.pdf" \
        --team-context western_films
"""

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from openai import OpenAI

from pdf_to_text import pdf_to_text
from schema import ROLE_EXTRACTION_RESPONSE
from verify import verify_extraction

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

    verification = verify_extraction(extracted, document_text)

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
        "quote_verification": verification,
        "roles": extracted["roles"],
        "role_structure": extracted["role_structure"],
        "extraction_notes": extracted["extraction_notes"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract roles.v1 JSON from one PDF.")
    parser.add_argument("pdf", type=Path, help="Path to the input PDF")
    parser.add_argument("--team-context", default=None, help="Team/teacher this document belongs to")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "output")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any quote fails verification or any role_id dangles.",
    )
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

    v = result["quote_verification"]
    tiers = ", ".join(f"{tier} {n}" for tier, n in v["by_verbatim_tier"].items() if n)
    print(f"Quotes verbatim: {v['verbatim_quotes']}/{v['total_quotes']}" + (f" ({tiers})" if tiers else ""))

    for item in v["hallucinated_detail"]:
        print(f"HALLUCINATED {item['owner']}: {item['text'][:100]!r}", file=sys.stderr)
    for problem in v["dangling_role_ids"]:
        print(f"DANGLING {problem}", file=sys.stderr)

    if args.strict and (v["hallucinated_quotes"] or v["dangling_role_ids"]):
        sys.exit(1)


if __name__ == "__main__":
    main()
