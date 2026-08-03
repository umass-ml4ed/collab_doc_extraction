"""Deterministic verification that supporting quotes exist in the source document.

Every quote the model emits is checked against the document text by exact string
containment. Nothing here calls an LLM and nothing is fuzzy: a quote either
matches under a named, reproducible normalization tier or it is unverified.

The tiers exist because pypdf's text extraction is lossy in known ways, not to
give the model latitude. They are tried in order and the first hit wins, so the
recorded tier is also a measure of how far the quote drifted from the source:

    exact       whitespace collapsed only — the quote is character-for-character
    normalized  + unicode punctuation and bullet glyphs folded, soft hyphens and
                line-break hyphenation undone (pypdf and transcription artifacts,
                not authorial differences)
    loose       + casefolded and stripped to alphanumerics — catches quotes whose
                punctuation the model rewrote; worth a human glance

A quote that matches no tier keeps its text and is marked HALLUCINATED rather
than dropped, so a reviewer can see what the model claimed and judge it.

The spec's supporting_quotes object is {text, location}; verification adds
`verbatim` and `repaired` alongside those, leaving the spec fields untouched.
"""

import re
import unicodedata

MATCH_TIERS = ("exact", "normalized", "loose")

# The `verbatim` value for a quote found nowhere in the document. An explicit
# mark rather than null: a consumer that forgets to check gets a conspicuous
# string, not a falsy blank that reads like "no data".
HALLUCINATED = "hallucinated"

# Model's known stitching artifact: joining distant passages into one "quote".
ELLIPSIS_SPLIT = re.compile(r"\s*(?:\.\.\.|…|\[\.\.\.\])\s*")

# Bullet and list-marker glyphs, all folded to one marker.
#
# Bullets are the dominant artifact in this corpus (one checklist carries 76 of
# them) and the model rarely reproduces the glyph it was shown: it typically
# emits a C0/C1 control character instead. Real extracted document text contains
# no control characters at all, so a control character in a quote is always
# transcription noise — folding it to the same marker as a literal bullet lets a
# bullet-list quote verify.
#
# Folding to a marker rather than deleting is deliberate. A quote that drops the
# bullets entirely still will not match at this tier and falls through to
# `loose`, which keeps `normalized` from quietly blurring into it.
BULLET = "•"
BULLET_GLYPHS = "●○◦▪▫■□‣⁃∙⋅·•"
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
BULLET_SPACING = re.compile(rf"\s*{BULLET}\s*")

# pypdf emits these for typographic characters in the PDF; the underlying
# document text is the ASCII equivalent, so folding them loses no meaning.
PUNCTUATION_FOLD = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
    "…": "...",
    " ": " ", " ": " ", " ": " ", "​": "",
    "­": "",  # soft hyphen
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
}

# "collabo- ration" -> "collaboration": undo hyphenation introduced by a line
# break in the PDF. Only fires between two word characters.
LINEBREAK_HYPHEN = re.compile(r"(\w)-\s+(\w)")

NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Shortest span accepted when repairing a stitched quote. Below this, a span
# matches by coincidence rather than by being the evidence it claims to be.
MIN_SPAN_WORDS = 3


def _collapse(text: str) -> str:
    return " ".join(text.split())


def normalize(text: str, tier: str) -> str:
    """Normalize text for comparison at the given tier. Deterministic and pure."""
    if tier == "exact":
        return _collapse(text)

    folded = unicodedata.normalize("NFKC", text)
    folded = CONTROL_CHARS.sub(BULLET, folded)
    folded = "".join(
        BULLET if ch in BULLET_GLYPHS else PUNCTUATION_FOLD.get(ch, ch) for ch in folded
    )
    folded = BULLET_SPACING.sub(f" {BULLET} ", folded)
    folded = _collapse(folded)
    folded = LINEBREAK_HYPHEN.sub(r"\1\2", folded)
    if tier == "normalized":
        return folded

    if tier == "loose":
        return _collapse(NON_ALNUM.sub(" ", folded.casefold()))

    raise ValueError(f"unknown match tier: {tier!r}")


class DocumentIndex:
    """Pre-normalized views of the source document, one per match tier.

    Built once per extraction so verifying N quotes stays O(N) rather than
    re-normalizing the whole document for every quote.
    """

    def __init__(self, document_text: str):
        self.views = {tier: normalize(document_text, tier) for tier in MATCH_TIERS}

    def match_tier(self, fragment: str) -> str | None:
        """Return the first tier whose view contains the fragment, else None."""
        if not fragment.strip():
            return None
        for tier in MATCH_TIERS:
            if normalize(fragment, tier) in self.views[tier]:
                return tier
        return None


def _split_stitched(text: str, index: DocumentIndex) -> list[tuple[str, str]] | None:
    """Split a stitched quote into spans that each verify on their own.

    Two stitching patterns show up, both from documents laid out as tables:

    1. An explicit "..." delimiter joining distant passages.
    2. No delimiter at all — the model prepends a table row's header to a cell's
       contents ("DP / Camera Operator" + "Prod: RECORDS AND MONITORS ALL
       VIDEO"). Both halves are real document text; only the join is invented.

    For (2) the quote is cut at the single word boundary where both sides verify
    independently. Each side must be at least MIN_SPAN_WORDS long, because a
    one- or two-word span matches almost any document by coincidence and is
    worthless as evidence either way.

    Returns [(text, tier), ...] if every span verifies, else None — a partial
    repair is not a repair, and keeping only the half that matched would
    misrepresent what the document says.

    Every fragment must verify, but fragments shorter than MIN_SPAN_WORDS are
    dropped rather than emitted: a bare role name ("Producer") matches almost
    any document by coincidence, so keeping it as a verified quote would inflate
    the pass rate with spans that are labels, not evidence.
    """
    fragments = [f for f in ELLIPSIS_SPLIT.split(text) if f.strip()]
    if len(fragments) >= 2:
        tiers = [index.match_tier(f) for f in fragments]
        if not all(tiers):
            return None
        spans = [
            (_collapse(f), tier)
            for f, tier in zip(fragments, tiers)
            if len(f.split()) >= MIN_SPAN_WORDS
        ]
        return spans or None

    words = _collapse(text).split()
    if len(words) < 2 * MIN_SPAN_WORDS:
        return None
    for cut in range(MIN_SPAN_WORDS, len(words) - MIN_SPAN_WORDS + 1):
        head, tail = " ".join(words[:cut]), " ".join(words[cut:])
        head_tier, tail_tier = index.match_tier(head), index.match_tier(tail)
        if head_tier and tail_tier:
            return [(head, head_tier), (tail, tail_tier)]
    return None


def verify_quotes(quotes: list[dict], index: DocumentIndex) -> list[dict]:
    """Return quotes annotated with `verbatim` and `repaired`.

    `verbatim` is the tier the quote matched at, or HALLUCINATED if it appears
    nowhere in the document — so the one field answers both "is this real?" and
    "how far did it drift?".

    Stitched quotes are split into one object per verifying span, each flagged
    `repaired` so a reviewer can tell a quote the model emitted cleanly from one
    the pipeline had to take apart. Quote text is replaced with its
    whitespace-collapsed form so downstream consumers get a single consistent
    spacing convention; the wording is never altered.
    """
    result = []
    for quote in quotes:
        text = quote["text"]

        tier = index.match_tier(text)
        if tier:
            result.append({**quote, "text": _collapse(text), "verbatim": tier, "repaired": False})
            continue

        spans = _split_stitched(text, index)
        if spans:
            result.extend(
                {**quote, "text": span, "verbatim": tier, "repaired": True} for span, tier in spans
            )
            continue

        result.append(
            {**quote, "text": _collapse(text), "verbatim": HALLUCINATED, "repaired": False}
        )
    return result


def verify_extraction(extracted: dict, document_text: str) -> dict:
    """Verify every quote in the extraction in place; return a summary.

    Also checks referential integrity of relationship role_ids, which is the
    other claim in the output that can be checked without a model.
    """
    index = DocumentIndex(document_text)

    all_quotes = []
    for role in extracted["roles"]:
        role["supporting_quotes"] = verify_quotes(role["supporting_quotes"], index)
        all_quotes.extend((f"role {role['id']}", q) for q in role["supporting_quotes"])
    for i, rel in enumerate(extracted["role_structure"]["relationships"]):
        rel["supporting_quotes"] = verify_quotes(rel["supporting_quotes"], index)
        all_quotes.extend((f"relationship {i}", q) for q in rel["supporting_quotes"])

    hallucinated = [(owner, q) for owner, q in all_quotes if q["verbatim"] == HALLUCINATED]

    role_ids = {r["id"] for r in extracted["roles"]}
    dangling = [
        f"relationship {i}: unknown role_id {rid!r}"
        for i, rel in enumerate(extracted["role_structure"]["relationships"])
        for rid in rel["role_ids"]
        if rid not in role_ids
    ]

    return {
        "total_quotes": len(all_quotes),
        "verbatim_quotes": len(all_quotes) - len(hallucinated),
        "hallucinated_quotes": len(hallucinated),
        "by_verbatim_tier": {
            tier: sum(1 for _, q in all_quotes if q["verbatim"] == tier) for tier in MATCH_TIERS
        },
        "repaired_quotes": sum(1 for _, q in all_quotes if q["repaired"]),
        "hallucinated_detail": [{"owner": owner, "text": q["text"]} for owner, q in hallucinated],
        "dangling_role_ids": dangling,
    }
