# Role Extractor

Extracts **role structure** from curricular documents. One PDF in, one `roles.v1` JSON
object out: the student roles a document defines, what each is responsible for, how the
roles relate to each other, and verbatim quotes backing every claim.

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env    # then add your OpenAI API key
```

## Running it

```bash
# Extract roles from one PDF → output/<pdf stem>.roles.json
uv run python extract_roles.py "path/to/document.pdf"

# Tag the output with the team/teacher the document came from
uv run python extract_roles.py "path/to/document.pdf" --team-context western_films

# Write somewhere other than ./output
uv run python extract_roles.py "path/to/document.pdf" --output-dir results/

# Inspect the raw text the model will see (useful when output looks wrong)
uv run python pdf_to_text.py "path/to/document.pdf"
```

The run prints the roles it found and writes the JSON. Any quote it couldn't verify
against the source text is printed to stderr as a `WARNING:` line — see
[Quote verification](#quote-verification).

Input PDFs and extraction results are gitignored; this repo is the pipeline only.

## How it works

```
PDF ──pdf_to_text.py──> plain text ──prompts/*.j2──> prompt
                                                       │
                                        gpt-4.1-mini, structured outputs (schema.py)
                                                       │
                                                       v
                                            model JSON (roles, structure)
                                                       │
                                          repair + validate quotes, add provenance
                                                       │
                                                       v
                                          output/<name>.roles.json
```

| File | Role |
| --- | --- |
| `pdf_to_text.py` | PDF → text via pypdf, with `--- Page N ---` markers so the model can cite locations. |
| `prompts/role_extraction_v1.j2` | The extraction prompt (Jinja2). |
| `schema.py` | JSON Schema handed to OpenAI structured outputs, in strict mode. |
| `extract_roles.py` | Orchestrates the above and wraps the result in a provenance envelope. |

Runs use `temperature=0` and a fixed seed, so repeat extractions of the same document
are close to deterministic — though not guaranteed identical.

### Output shape

```jsonc
{
  "extraction_id": "ext_2026-07-16_a1b2c3d4",
  "source_document": {
    "filename": "Western Films Job Descriptions.pdf",
    "document_type": "project brief",
    "checksum": "sha256:..."
  },
  "extracted_at": "2026-07-16T18:10:03-07:00",
  "extractor": {
    "model": "gpt-4.1-mini",
    "schema_version": "roles.v1",
    "prompt_version": "role_extraction_v1"
  },
  "team_context": "western_films",

  "roles": [
    {
      "id": "director_of_photography",       // snake_case, stable within this document
      "name": "Director of Photography",
      "description": "1–2 sentence summary of the role.",
      "responsibilities": [
        { "summary": "Short label", "detail": "What it entails in practice." }
      ],
      "supporting_quotes": [
        { "text": "verbatim text from the PDF", "location": "section or page, or null" }
      ],
      "source": "explicit",                  // explicit | inferred
      "confidence": 0.95                     // 0.0–1.0
    }
  ],

  "role_structure": {
    "assignment_method": "teacher_assigned", // teacher_assigned | student_chosen | rotating | unspecified
    "relationships": [
      {
        "role_ids": ["director_of_photography", "editor"],
        "relationship_type": "handoff",      // peer | hierarchical | handoff | complementary
        "detail": "How these roles interact.",
        "supporting_quotes": [ /* ... */ ]
      }
    ],
    "confidence": 0.8
  },

  "extraction_notes": "Document-level flags, or null."
}
```

Relationship types: **peer** (equal standing), **hierarchical** (one oversees another),
**handoff** (one role's output feeds the next), **complementary** (different aspects of
one shared deliverable).

Everything from `roles` down is model-generated. Provenance — `extraction_id`,
`source_document`, `extracted_at`, `extractor`, `team_context` — is computed by the
pipeline, never by the LLM.

**A document with no roles is a valid result**, not a failure: `roles` and
`relationships` come back empty and `extraction_notes` says why.

### Quote verification

Every quote is checked against the source text before the JSON is written
(whitespace-normalized, because pypdf's extraction introduces irregular spacing).

The model's known failure mode is stitching distant passages together with `...` or
prepending the role name. `repair_quotes()` splits those into one quote object per span
that does verify. Anything still unverifiable is left in the output as-is and reported
as a `WARNING:` on stderr — so a warning means **check that quote by hand**, not that
the run failed.

## Changing the prompt

`prompt_version` in the output is the template filename minus `.j2`. Once results have
been shared, don't edit a template in place — add `prompts/role_extraction_v2.j2` and
bump `PROMPT_VERSION` in `extract_roles.py`, so existing outputs stay traceable to the
prompt that produced them.

## Scope

V1 is single-document extraction against the schema above. Themes, temporal structure,
and content graphs are deliberately out of scope.
