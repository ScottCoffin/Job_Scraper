"""Test that sample job fixtures validate against the JSON schema.

Catches schema drift — if the schema or fixtures are updated independently,
this test fails rather than silently shipping an invalid contract.
"""
import json
from pathlib import Path

import pytest

SCHEMA_PATH = Path(__file__).parent.parent / "schema" / "jobs.schema.json"
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_schema():
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _try_jsonschema():
    try:
        import jsonschema
        return jsonschema
    except ImportError:
        return None


def test_schema_is_valid_json():
    """The schema file itself must be valid JSON."""
    schema = _load_schema()
    assert isinstance(schema, dict)
    assert "properties" in schema
    assert "required" in schema


def test_sample_all_jobs_validate():
    """Every job in sample_all_jobs.json must validate against the schema."""
    schema = _load_schema()
    sample = json.loads(
        (FIXTURES_DIR / "sample_all_jobs.json").read_text(encoding="utf-8")
    )
    jsonschema = _try_jsonschema()
    required = schema.get("required", [])

    for i, job in enumerate(sample["jobs"]):
        if jsonschema:
            jsonschema.validate(job, schema)
        else:
            # Fallback: check required fields exist
            for field in required:
                assert field in job, f"Job {i} missing required field: {field}"


def test_schema_required_fields_minimal():
    """Schema should require only the essential identity fields."""
    schema = _load_schema()
    required = schema["required"]
    # These are the minimum fields needed to identify a job
    assert "url" in required
    assert "title" in required
    assert "company" in required
    assert "ats" in required
    assert "first_seen" in required
