"""JSON-schema for one opportunity row (program/research/competition), plus
a validator returning readable errors."""
from jsonschema import Draft202012Validator

_DATE = r"^\d{4}-\d{2}-\d{2}$"
_MONTH_OR_DATE = r"^\d{4}-\d{2}(-\d{2})?$"

OPPORTUNITY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "id", "name", "org", "kind", "category", "url", "apply_url",
        "status", "opens", "closes", "eligibility", "location", "cycle",
        "sources", "date_added", "last_checked", "notes",
    ],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "name": {"type": "string", "minLength": 1},
        "org": {"type": "string", "minLength": 1},
        "kind": {"enum": ["program", "research", "competition"]},
        "category": {
            "enum": ["swe", "quant", "data_science", "ai_ml", "hardware",
                     "actuarial", None],
        },
        "url": {"type": "string", "minLength": 1},
        "apply_url": {"type": ["string", "null"]},
        "status": {"enum": ["open", "upcoming", "closed", "unknown"]},
        "opens": {"type": ["string", "null"], "pattern": _MONTH_OR_DATE},
        "closes": {"type": ["string", "null"], "pattern": _MONTH_OR_DATE},
        "eligibility": {"type": "string", "minLength": 1},
        "location": {"type": ["string", "null"]},
        "cycle": {"type": ["string", "null"]},
        "sources": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "date_added": {"type": "string", "pattern": _DATE},
        "last_checked": {"type": "string", "pattern": _DATE},
        "notes": {"type": ["string", "null"]},
    },
}

_validator = Draft202012Validator(OPPORTUNITY_SCHEMA)


def validate_opportunity(row: dict) -> list[str]:
    """Return a list of 'path: message' errors ([] if the row is valid)."""
    return [
        f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
        for e in _validator.iter_errors(row)
    ]
