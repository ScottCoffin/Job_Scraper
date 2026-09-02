"""Test classify_work_arrangement — remote/onsite/hybrid label normalization."""
import pytest
from scrape_jobs import classify_work_arrangement, WORK_ARRANGEMENTS


@pytest.mark.parametrize("text,is_remote,expected", [
    ("Remote", None, WORK_ARRANGEMENTS["remote_in_state"]),
    ("Hybrid", None, WORK_ARRANGEMENTS["telecommute"]),
    ("On-site", None, WORK_ARRANGEMENTS["onsite"]),
    ("Onsite", None, WORK_ARRANGEMENTS["onsite"]),
    ("In office", None, WORK_ARRANGEMENTS["onsite"]),
    ("Work from home", None, WORK_ARRANGEMENTS["remote_in_state"]),
    ("Telecommute", None, WORK_ARRANGEMENTS["telecommute"]),
    ("Telework", None, WORK_ARRANGEMENTS["telecommute"]),
    ("Out of state", None, WORK_ARRANGEMENTS["remote_out_of_state"]),
    ("Remote out of state", None, WORK_ARRANGEMENTS["remote_out_of_state"]),
    ("In state", None, WORK_ARRANGEMENTS["remote_in_state"]),
    ("Remote in state", None, WORK_ARRANGEMENTS["remote_in_state"]),
    ("Long distance", None, WORK_ARRANGEMENTS["remote_in_state"]),
    ("Business location", None, WORK_ARRANGEMENTS["onsite"]),
    ("In person", None, WORK_ARRANGEMENTS["onsite"]),
    ("", True, WORK_ARRANGEMENTS["remote_in_state"]),
    ("", False, WORK_ARRANGEMENTS["onsite"]),
    ("", None, ""),
    (None, None, ""),
])
def test_classify_work_arrangement(text, is_remote, expected):
    assert classify_work_arrangement(text, is_remote=is_remote) == expected
