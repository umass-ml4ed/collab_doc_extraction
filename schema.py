"""JSON Schema for the model-generated portion of the roles.v1 output.

Covers only what the LLM produces: document_type, roles, role_structure,
extraction_notes. Provenance fields (extraction_id, source_document,
extracted_at, extractor, team_context) are added by extract_roles.py.

Written for OpenAI structured outputs in strict mode: every field is
required and additionalProperties is false; optional fields are nullable
instead of omitted.
"""

SUPPORTING_QUOTE = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "Exact verbatim quote from the source document.",
        },
        "location": {
            "type": ["string", "null"],
            "description": "Section heading or page reference where the quote appears, if identifiable.",
        },
    },
    "required": ["text", "location"],
    "additionalProperties": False,
}

ROLE = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "description": "Stable identifier within this document, snake_case (e.g. 'director_of_photography').",
        },
        "name": {
            "type": "string",
            "description": "Display name of the role as it appears in the assignment.",
        },
        "description": {
            "type": "string",
            "description": "1-2 sentence summary of what the role is and its purpose in the assignment.",
        },
        "responsibilities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Short label for the responsibility (e.g. 'Historical contextualization').",
                    },
                    "detail": {
                        "type": "string",
                        "description": "1-2 sentence description of what this responsibility entails in practice.",
                    },
                },
                "required": ["summary", "detail"],
                "additionalProperties": False,
            },
        },
        "supporting_quotes": {
            "type": "array",
            "items": SUPPORTING_QUOTE,
            "description": "Verbatim evidence. At least one expected for explicit roles; closest supporting text for inferred roles.",
        },
        "source": {
            "type": "string",
            "enum": ["explicit", "inferred"],
            "description": "Whether the role was stated directly or inferred from differentiated expectations.",
        },
        "confidence": {
            "type": "number",
            "description": "0.0-1.0 confidence that this is a genuine, correctly-captured role.",
        },
    },
    "required": [
        "id",
        "name",
        "description",
        "responsibilities",
        "supporting_quotes",
        "source",
        "confidence",
    ],
    "additionalProperties": False,
}

ROLE_STRUCTURE = {
    "type": "object",
    "properties": {
        "assignment_method": {
            "type": "string",
            "enum": ["teacher_assigned", "student_chosen", "rotating", "unspecified"],
            "description": "How roles are distributed among students.",
        },
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "role_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "The id values of the roles involved in this relationship.",
                    },
                    "relationship_type": {
                        "type": "string",
                        "enum": ["peer", "hierarchical", "handoff", "complementary"],
                        "description": "peer: equal standing; hierarchical: one oversees another; handoff: one role's output feeds the next; complementary: different aspects of one shared deliverable.",
                    },
                    "detail": {
                        "type": "string",
                        "description": "1-2 sentences describing how these roles interact in this assignment.",
                    },
                    "supporting_quotes": {
                        "type": "array",
                        "items": SUPPORTING_QUOTE,
                    },
                },
                "required": ["role_ids", "relationship_type", "detail", "supporting_quotes"],
                "additionalProperties": False,
            },
        },
        "confidence": {
            "type": "number",
            "description": "0.0-1.0 confidence that the collaborative structure was correctly characterized.",
        },
    },
    "required": ["assignment_method", "relationships", "confidence"],
    "additionalProperties": False,
}

ROLE_EXTRACTION_RESPONSE = {
    "name": "role_extraction",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "document_type": {
                "type": ["string", "null"],
                "description": "Best-effort classification of the document (e.g. 'project brief', 'rubric', 'prompt').",
            },
            "roles": {
                "type": "array",
                "items": ROLE,
                "description": "Extracted roles. Empty if the document defines no student roles.",
            },
            "role_structure": ROLE_STRUCTURE,
            "extraction_notes": {
                "type": ["string", "null"],
                "description": "Optional document-level flags (e.g. 'roles implied but never named', 'no distinct student roles identified').",
            },
        },
        "required": ["document_type", "roles", "role_structure", "extraction_notes"],
        "additionalProperties": False,
    },
}
