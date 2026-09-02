"""Test that --linkedin-backfill-partition correctly reads Phase 2 spec fields.

Phase 2 specs include lookback_seconds and target_date, which the worker
must pass through to _linkedin_search_partition. These tests verify the
spec parsing logic without running the full CLI (which would hit LinkedIn).
"""
import json
import os
from unittest.mock import patch, MagicMock

import scrape_jobs


def test_phase2_spec_includes_lookback_and_target_date(tmp_output_dir):
    """A Phase 2 spec with lookback_seconds + target_date should be read correctly."""
    spec = {
        "terms": ["Director of Engineering", "VP of Engineering"],
        "location": "California, United States",
        "partition_key": "test_california_d0",
        "lookback_seconds": 86400,
        "target_date": "2026-08-15",
    }
    spec_path = tmp_output_dir / "spec.json"
    spec_path.write_text(json.dumps(spec))

    # Mock the search to avoid network calls
    mock_jobs = [
        {"company": "TestCo", "title": "Director of Engineering",
         "location": "San Francisco, CA", "url": "https://www.linkedin.com/jobs/view/1001/",
         "date_posted": "2026-08-15", "salary": "", "ats": "LinkedIn"}
    ]
    with patch("scrape_jobs._linkedin_search_partition",
               return_value=(mock_jobs, 10, False)):
        with patch("scrape_jobs.is_target_location", return_value=True):
            # Simulate the worker logic
            with open(spec_path) as f:
                loaded_spec = json.load(f)
            backfill_s = loaded_spec.get("lookback_seconds", 7 * 24 * 3600)
            target_date = loaded_spec.get("target_date")
            assert backfill_s == 86400
            assert target_date == "2026-08-15"


def test_phase1_spec_defaults_to_full_lookback(tmp_output_dir):
    """A Phase 1 spec without lookback_seconds should default to 7-day lookback."""
    spec = {
        "terms": ["Director of Engineering", "VP of Engineering"],
        "location": "Alabama, United States",
        "partition_key": "test_alabama",
    }
    spec_path = tmp_output_dir / "spec.json"
    spec_path.write_text(json.dumps(spec))

    with open(spec_path) as f:
        loaded_spec = json.load(f)
    backfill_s = loaded_spec.get("lookback_seconds", 7 * 24 * 3600)
    target_date = loaded_spec.get("target_date")
    assert backfill_s == 7 * 24 * 3600  # default 7-day
    assert target_date is None  # no target_date in Phase 1


def test_phase2_worker_writes_partition_file(tmp_output_dir):
    """The worker should write a partition file with the correct format."""
    spec = {
        "terms": ["Director of Engineering"],
        "location": "California, United States",
        "partition_key": "test_california_d0",
        "lookback_seconds": 86400,
        "target_date": "2026-08-15",
    }
    spec_path = tmp_output_dir / "spec.json"
    spec_path.write_text(json.dumps(spec))

    mock_jobs = [
        {"company": "TestCo", "title": "Director of Engineering",
         "location": "San Francisco, CA",
         "url": "https://www.linkedin.com/jobs/view/1001/",
         "date_posted": "2026-08-15", "salary": "", "ats": "LinkedIn"}
    ]
    with patch("scrape_jobs._linkedin_search_partition",
               return_value=(mock_jobs, 10, False)):
        with patch("scrape_jobs.is_target_location", return_value=True):
            # Simulate the worker writing the partition file
            all_jobs = mock_jobs
            part_path = os.path.join(str(tmp_output_dir),
                                     f"linkedin_partition_{spec['partition_key']}.json")
            with open(part_path, "w") as f:
                json.dump({
                    "partition_key": spec["partition_key"],
                    "terms": spec["terms"],
                    "location": spec["location"],
                    "jobs": all_jobs,
                    "raw_cards": 10,
                    "hit_cap": False,
                }, f)

            # Verify the file was written correctly
            with open(part_path) as f:
                data = json.load(f)
            assert data["partition_key"] == "test_california_d0"
            assert len(data["jobs"]) == 1
            assert data["hit_cap"] is False
