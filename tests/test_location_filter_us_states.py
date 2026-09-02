"""Test is_target_location — all 50 US states accepted, international rejected.

This is a regression test for the confirmed bug where 35/50 states were
missing from the location filter, causing jobs to be dropped.
"""
import pytest
from scrape_jobs import is_target_location


# All 50 US states (by full name — what LinkedIn returns)
US_STATES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
    "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
    "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
    "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
    "West Virginia", "Wisconsin", "Wyoming",
]


# International locations that must be rejected
INTERNATIONAL = [
    "London, United Kingdom",
    "Cardiff, Wales",
    "Toronto, Canada",
    "Sydney, Australia",
    "Berlin, Germany",
    "Tokyo, Japan",
    "Mumbai, India",
    "Paris, France",
    "Amsterdam, Netherlands",
    "Singapore, Singapore",
    "Dublin, Ireland",
    "Tel Aviv, Israel",
]


@pytest.mark.parametrize("state", US_STATES)
def test_all_50_states_accepted(state):
    """Every US state must pass the location filter."""
    assert is_target_location(f"City, {state}, United States") is True, (
        f'"{state}" was rejected — location filter bug'
    )


def test_remote():
    assert is_target_location("Remote") is True


def test_remote_united_states():
    assert is_target_location("Remote, United States") is True


def test_hybrid():
    assert is_target_location("Hybrid - Austin, TX") is True


@pytest.mark.parametrize("location", INTERNATIONAL)
def test_international_rejected(location):
    """International locations must be rejected."""
    assert is_target_location(location) is False, (
        f'"{location}" was accepted — should be rejected as international'
    )


def test_empty():
    assert is_target_location("") is False


def test_none():
    assert is_target_location(None) is False  # type: ignore[arg-type]
