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

The run prints the roles it found, a `Quotes verified: N/M` tally, and writes the JSON.
Any quote it couldn't find in the source document is printed to stderr as an
`UNVERIFIED` line — see [Quote verification](#quote-verification).

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
        {
          "text": "verbatim text from the PDF",
          "location": "section or page, or null",
          "verified": true,                    // checked against the source, not model-asserted
          "match_tier": "exact",               // exact | normalized | loose | null
          "repaired": false                    // true if split out of a stitched quote
        }
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

Every supporting quote is checked against the source document by exact string
containment before the JSON is written (`verify.py`). No LLM is involved and nothing is
fuzzy — a quote either matches under a named normalization tier or it is marked
unverified. Each quote carries the result:

```jsonc
{ "text": "...", "location": "Page 2", "verified": true, "match_tier": "exact", "repaired": false }
```

The tiers exist because pypdf's extraction is lossy in known ways, not to give the model
latitude. They're tried in order, first hit wins, so the recorded tier also says how far
the quote drifted:

| `match_tier` | What it tolerates | Read it as |
| --- | --- | --- |
| `exact` | Whitespace collapsing only | Character-for-character verbatim |
| `normalized` | Unicode punctuation folded, soft hyphens and line-break hyphenation undone | Verbatim; the difference is a PDF artifact |
| `loose` | Case and all non-alphanumeric characters | Wording matches, punctuation was rewritten — worth a glance |
| `null` | — | **Unverified.** Not found in the document |

No tier tolerates different *wording*, so a paraphrase always lands in `null`.

**Stitched quotes** are the model's characteristic failure on table-shaped documents,
in two forms. Both get split into one quote object per span, each flagged
`"repaired": true`:

- An explicit `...` joining distant passages.
- No delimiter at all — a table row's header glued onto a cell's contents
  (`"DP / Camera Operator"` + `"Prod: RECORDS AND MONITORS ALL VIDEO"`). Both halves
  are real text; only the join is invented. The quote is cut at the one word boundary
  where both sides verify independently, each side at least 3 words.

If any span fails to verify, the whole quote is rejected rather than partly repaired —
half-real evidence would misrepresent the document.

Repair deliberately stops at a **two-way** split for the delimiter-less case. The
remaining failure mode is the model eliding an interior clause without an ellipsis
(source: `ACCOUNTED FOR; checks gear in/out; sets/adjusts lighting` → quoted:
`ACCOUNTED FOR; sets/adjusts lighting`), which implies an adjacency the document
doesn't have. Splitting into arbitrarily many spans would accept any reassembly of
document phrases and defeat the check, so these stay flagged for a human.

**Unverified quotes are kept, not dropped**, so a reviewer can see what the model
claimed and judge it. They're printed to stderr as `UNVERIFIED` lines and counted in
the `quote_verification` block:

```jsonc
"quote_verification": {
  "total_quotes": 29,
  "verified_quotes": 24,
  "unverified_quotes": 5,
  "by_match_tier": { "exact": 24, "normalized": 0, "loose": 0 },
  "repaired_quotes": 6,
  "unverified_detail": [ { "owner": "relationship 2", "text": "..." } ],
  "dangling_role_ids": []
}
```

(Real numbers from `Western Films Job Descriptions.pdf`, a heavily table-formatted
document — the hardest case in the corpus so far.)

`dangling_role_ids` catches the other checkable claim: a relationship referencing a
`role_id` that isn't in `roles`.

Pass `--strict` to exit non-zero when anything fails verification — for batch runs
where you want a bad extraction to stop the pipeline rather than land in `output/`.

```bash
uv run pytest    # 26 tests covering the tiers, stitch repair, and rejection cases
```

## Changing the prompt

`prompt_version` in the output is the template filename minus `.j2`. Once results have
been shared, don't edit a template in place — add `prompts/role_extraction_v2.j2` and
bump `PROMPT_VERSION` in `extract_roles.py`, so existing outputs stay traceable to the
prompt that produced them.

## Scope

V1 is single-document extraction against the schema above. Themes, temporal structure,
and content graphs are deliberately out of scope.
