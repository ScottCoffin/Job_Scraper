"""Test _slug helper for partition key generation."""
import pytest
from scrape_jobs import _slug


@pytest.mark.parametrize("input,expected", [
    ("Director of Engineering", "director_of_engineering"),
    ("VP of Engineering", "vp_of_engineering"),
    ("Director, Core Runtime & Platform Engineering",
     "director_core_runtime_platform_engineering"),
    ("Remote", "remote"),
    ("United States", "united_states"),
    ("  Multiple   Spaces  ", "multiple_spaces"),
    ("CamelCase", "camelcase"),
    ("with-dashes", "with_dashes"),
    ("special!@#chars", "special_chars"),
    ("", ""),
])
def test_slug(input, expected):
    assert _slug(input) == expected
