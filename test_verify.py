"""Tests for deterministic quote verification.

The document fixtures deliberately contain the artifacts pypdf actually
produces — curly quotes, en dashes, line-break hyphenation — so the tiers are
tested against the failure modes they exist for.
"""

import pytest

from verify import (
    HALLUCINATED,
    MATCH_TIERS,
    DocumentIndex,
    normalize,
    verify_extraction,
    verify_quotes,
)

DOC = """--- Page 1 ---
Western Films Job Descriptions

The Director of Photography is responsible for the visual language of the film.
They frame every shot and hand the raw footage to the Editor at the end of
each shoot day.

The Editor assembles the footage into a coherent narrative. The teacher will
assign roles during the first week; students don’t choose their own.

Historical positioners must situate the film within its period – an era of
rapid collabo-
ration between studios.
"""


@pytest.fixture
def index():
    return DocumentIndex(DOC)


def quote(text):
    return {"text": text, "location": None}


def verify_one(text, index):
    """Verify a single quote and return its annotated form."""
    result = verify_quotes([quote(text)], index)
    assert len(result) == 1
    return result[0]


class TestExactTier:
    def test_contiguous_span_verifies(self, index):
        q = verify_one("frame every shot and hand the raw footage to the Editor", index)
        assert q["verbatim"] == "exact"

    def test_span_crossing_a_line_break_verifies(self, index):
        """Line breaks in the PDF are whitespace, not content."""
        q = verify_one("hand the raw footage to the Editor at the end of each shoot day", index)
        assert q["verbatim"] == "exact"

    def test_irregular_internal_spacing_verifies(self, index):
        q = verify_one("The   Editor    assembles the footage", index)
        assert q["verbatim"] == "exact"

    def test_text_is_stored_whitespace_collapsed(self, index):
        q = verify_one("The   Editor    assembles the footage", index)
        assert q["text"] == "The Editor assembles the footage"


class TestNormalizedTier:
    def test_curly_apostrophe_matches_straight(self, index):
        q = verify_one("students don't choose their own", index)  # U+2019
        assert q["verbatim"] == "normalized"

    def test_em_dash_matches_en_dash(self, index):
        q = verify_one("within its period — an era", index)
        assert q["verbatim"] == "normalized"

    def test_linebreak_hyphenation_is_undone(self, index):
        """pypdf leaves 'collabo- ration'; the real word is 'collaboration'."""
        q = verify_one("an era of rapid collaboration between studios", index)
        assert q["verbatim"] == "normalized"


class TestBulletFolding:
    """Bullets are the dominant artifact in the real corpus, and the model
    almost never reproduces the glyph it was shown."""

    BULLET_DOC = DocumentIndex(
        "Production Designer Pre Production: ● Costume ● Sizes of Actors "
        "● Source Pieces We Already Have Access To"
    )

    def test_control_char_bullet_matches_glyph_bullet(self):
        """The model emits \\x7f where the document has ●. This single mismatch
        was pushing every bullet-list quote down to the loose tier."""
        q = verify_one("\x7f Costume \x7f Sizes of Actors", self.BULLET_DOC)
        assert q["verbatim"] == "normalized"

    def test_assorted_bullet_glyphs_are_interchangeable(self):
        for glyph in "•○▪‣-":
            if glyph == "-":
                continue
            q = verify_one(f"{glyph} Costume {glyph} Sizes of Actors", self.BULLET_DOC)
            assert q["verbatim"] == "normalized", glyph

    def test_bullet_spacing_is_canonicalized(self):
        q = verify_one("●Costume    ●   Sizes of Actors", self.BULLET_DOC)
        assert q["verbatim"] == "normalized"

    def test_dropping_bullets_does_not_match_at_normalized(self):
        """Folding to a marker rather than deleting keeps `normalized` from
        blurring into `loose`: a quote that omits the list structure still has
        to fall through."""
        q = verify_one("Costume Sizes of Actors", self.BULLET_DOC)
        assert q["verbatim"] == "loose"

    def test_bullet_folding_still_rejects_wrong_wording(self):
        q = verify_one("● Costume ● Heights of Actors", self.BULLET_DOC)
        assert q["verbatim"] == HALLUCINATED

    def test_control_chars_do_not_rescue_mangled_words(self):
        """A control char folds to a bullet, but junk the model inserted into a
        word is still wrong wording."""
        q = verify_one("● 9Costume9 ● Sizes of Actors", self.BULLET_DOC)
        assert q["verbatim"] == HALLUCINATED


class TestLooseTier:
    def test_case_and_punctuation_drift_matches_loosely(self, index):
        q = verify_one("the director of photography is responsible", index)
        assert q["verbatim"] == "loose"


class TestRejection:
    def test_fabricated_quote_is_marked_hallucinated(self, index):
        q = verify_one("Students will present their work to a panel of judges.", index)
        assert q["verbatim"] == HALLUCINATED

    def test_hallucinated_quote_text_is_preserved_not_dropped(self, index):
        """A reviewer has to be able to see what the model claimed."""
        fabricated = "Students will present their work to a panel of judges."
        q = verify_one(fabricated, index)
        assert q["text"] == fabricated

    def test_plausible_paraphrase_is_rejected(self, index):
        """Loose tier folds punctuation and case, never wording."""
        q = verify_one("The Editor puts the footage together into a narrative", index)
        assert q["verbatim"] == HALLUCINATED

    def test_empty_quote_is_marked_hallucinated(self, index):
        assert verify_one("   ", index)["verbatim"] == HALLUCINATED


class TestStitchedQuotes:
    def test_ellipsis_stitched_quote_splits_into_verifying_spans(self, index):
        q = verify_quotes(
            [quote("The Editor assembles the footage ... assign roles during the first week")],
            index,
        )
        assert len(q) == 2
        assert all(part["verbatim"] != HALLUCINATED for part in q)
        assert q[0]["text"] == "The Editor assembles the footage"
        assert q[1]["text"] == "assign roles during the first week"

    def test_partial_stitch_repair_is_rejected_wholesale(self, index):
        """Half-real evidence is not evidence; keeping the real half would
        misrepresent a quote the model partly invented."""
        q = verify_quotes(
            [quote("The Editor assembles the footage ... and emails it to the principal")],
            index,
        )
        assert len(q) == 1 and q[0]["verbatim"] == HALLUCINATED

    def test_row_header_prepended_to_cell_is_split(self, index):
        """Table layout: the model glues a row header onto a cell's contents
        with no delimiter. Both halves are real; only the join is invented."""
        q = verify_quotes(
            [quote("The Editor assembles the footage The teacher will assign roles")],
            index,
        )
        assert len(q) == 2
        assert all(part["verbatim"] != HALLUCINATED and part["repaired"] for part in q)

    def test_trivially_short_fragment_is_dropped_not_counted(self, index):
        """A bare role name matches almost any document. Keeping it as a
        verified span would inflate the pass rate with labels, not evidence.

        The fragments here are distant in the document on purpose: when they
        happen to be adjacent, the loose tier absorbs the '...' as punctuation
        and the whole quote matches without ever reaching the split path.
        """
        q = verify_quotes([quote("The Editor ... assign roles during the first week")], index)
        assert len(q) == 1
        assert q[0]["text"] == "assign roles during the first week"

    def test_quote_of_only_short_fragments_is_hallucinated(self, index):
        q = verify_quotes([quote("The Editor ... the film")], index)
        assert len(q) == 1 and q[0]["verbatim"] == HALLUCINATED

    def test_short_fragment_that_fails_still_rejects_the_whole_quote(self, index):
        """Dropping short fragments must not become a way to smuggle in an
        invented one."""
        q = verify_quotes(
            [quote("assembles the footage into a coherent narrative ... on Mars")], index
        )
        assert len(q) == 1 and q[0]["verbatim"] == HALLUCINATED

    def test_split_requires_both_sides_to_verify(self, index):
        q = verify_quotes(
            [quote("The Editor assembles the footage and then posts it online")], index
        )
        assert len(q) == 1 and q[0]["verbatim"] == HALLUCINATED

    def test_short_spans_are_not_split(self, index):
        """A two-word span matches by coincidence, not by being evidence."""
        q = verify_quotes([quote("The Editor the film")], index)
        assert len(q) == 1 and q[0]["verbatim"] == HALLUCINATED

    def test_cleanly_matching_quote_is_not_marked_repaired(self, index):
        assert verify_one("The Editor assembles the footage", index)["repaired"] is False

    def test_stitch_metadata_is_carried_to_each_span(self, index):
        q = verify_quotes(
            [
                {
                    "text": "The Editor assembles the footage ... assign roles during the first week",
                    "location": "Page 1",
                }
            ],
            index,
        )
        assert [part["location"] for part in q] == ["Page 1", "Page 1"]


class TestSpecConformance:
    """The spec's supporting_quotes object is {text, location}. Verification is
    additive: it may annotate, never reshape."""

    SPEC_FIELDS = {"text", "location"}
    ADDED_FIELDS = {"verbatim", "repaired"}

    def test_spec_fields_survive_verification(self, index):
        q = verify_quotes([{"text": "The Editor assembles the footage", "location": "Page 1"}], index)
        assert self.SPEC_FIELDS <= set(q[0])
        assert q[0]["location"] == "Page 1"

    def test_only_expected_fields_are_added(self, index):
        q = verify_one("The Editor assembles the footage", index)
        assert set(q) == self.SPEC_FIELDS | self.ADDED_FIELDS

    def test_hallucinated_quotes_have_the_same_shape(self, index):
        """A consumer must not need a different code path for bad quotes."""
        q = verify_one("nothing like this appears anywhere", index)
        assert set(q) == self.SPEC_FIELDS | self.ADDED_FIELDS

    def test_verbatim_is_never_null(self, index):
        """The mark for 'not in the document' is the string HALLUCINATED, not
        null — a consumer that forgets to check sees something conspicuous."""
        for text in ("The Editor assembles the footage", "invented text", "   "):
            assert verify_one(text, index)["verbatim"] is not None

    def test_verbatim_is_always_a_known_value(self, index):
        allowed = set(MATCH_TIERS) | {HALLUCINATED}
        for text in ("The Editor assembles the footage", "invented text"):
            assert verify_one(text, index)["verbatim"] in allowed


class TestNormalizeIsPure:
    def test_tiers_are_idempotent(self):
        for tier in ("exact", "normalized", "loose"):
            once = normalize(DOC, tier)
            assert normalize(once, tier) == once

    def test_unknown_tier_raises(self):
        with pytest.raises(ValueError):
            normalize("text", "fuzzy")


class TestVerifyExtraction:
    def extraction(self, **overrides):
        base = {
            "document_type": "project brief",
            "roles": [
                {
                    "id": "editor",
                    "name": "Editor",
                    "description": "...",
                    "responsibilities": [],
                    "supporting_quotes": [quote("The Editor assembles the footage")],
                    "source": "explicit",
                    "confidence": 0.9,
                }
            ],
            "role_structure": {
                "assignment_method": "teacher_assigned",
                "relationships": [],
                "confidence": 0.8,
            },
            "extraction_notes": None,
        }
        base.update(overrides)
        return base

    def test_summary_counts_quotes(self):
        summary = verify_extraction(self.extraction(), DOC)
        assert summary["total_quotes"] == 1
        assert summary["verbatim_quotes"] == 1
        assert summary["hallucinated_quotes"] == 0
        assert summary["by_verbatim_tier"]["exact"] == 1

    def test_hallucinated_quote_is_reported_with_owner(self):
        extracted = self.extraction()
        extracted["roles"][0]["supporting_quotes"] = [quote("invented text")]
        summary = verify_extraction(extracted, DOC)
        assert summary["hallucinated_quotes"] == 1
        assert summary["hallucinated_detail"][0]["owner"] == "role editor"

    def test_dangling_relationship_role_id_is_flagged(self):
        extracted = self.extraction()
        extracted["role_structure"]["relationships"] = [
            {
                "role_ids": ["editor", "director_of_photography"],
                "relationship_type": "handoff",
                "detail": "...",
                "supporting_quotes": [],
            }
        ]
        summary = verify_extraction(extracted, DOC)
        assert len(summary["dangling_role_ids"]) == 1
        assert "director_of_photography" in summary["dangling_role_ids"][0]

    def test_annotations_are_written_back_into_the_extraction(self):
        extracted = self.extraction()
        verify_extraction(extracted, DOC)
        assert extracted["roles"][0]["supporting_quotes"][0]["verbatim"] == "exact"

    def test_roleless_document_verifies_cleanly(self):
        summary = verify_extraction(
            self.extraction(roles=[]), DOC
        )
        assert summary["total_quotes"] == 0
        assert summary["hallucinated_quotes"] == 0
