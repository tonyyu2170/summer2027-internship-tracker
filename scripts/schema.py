"""JSON-schema for one role row, plus a validator returning readable errors."""
from jsonschema import Draft202012Validator

_DATE = r"^\d{4}-\d{2}-\d{2}$"

ROW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "id", "company", "role", "location", "link", "date_posted", "term",
        "degree", "status", "sources", "date_added", "last_verified",
        "possible_duplicate_of",
    ],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "company": {"type": "string", "minLength": 1},
        "role": {"type": "string", "minLength": 1},
        "track": {"enum": ["Trading", "Research", "Development"]},
        "location": {"type": "string", "minLength": 1},
        "link": {"type": "string", "minLength": 1},
        "date_posted": {"type": "string", "pattern": _DATE},
        "date_estimated": {"type": "boolean"},
        "term": {"type": "string", "minLength": 1},
        "degree": {
            "type": "array", "minItems": 1,
            "items": {"enum": ["BS", "MS", "PhD"]},
        },
        "status": {"enum": ["open", "closed"]},
        "sources": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "date_added": {"type": "string", "pattern": _DATE},
        "last_verified": {"type": "string", "pattern": _DATE},
        "possible_duplicate_of": {"type": ["string", "null"]},
    },
}

_validator = Draft202012Validator(ROW_SCHEMA)


def validate_row(row: dict) -> list[str]:
    """Return a list of 'path: message' errors ([] if the row is valid)."""
    return [
        f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
        for e in _validator.iter_errors(row)
    ]
