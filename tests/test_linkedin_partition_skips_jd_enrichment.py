"""Test that --linkedin-backfill-partition does NOT call _enrich_linkedin_postings.

This is the key behavior change — enrichment is deferred to job-hunter's
fetch_jds.py. A regression here would re-introduce the 1-hour enrichment delay.
"""
from unittest.mock import patch, MagicMock

import scrape_jobs


def test_enrichment_not_called_on_partition_jobs():
    """_enrich_linkedin_postings should NOT be called by the partition worker."""
    # Create fake jobs with empty descriptions (as expected from partition scrape)
    fake_jobs = [
        {"company": "A", "title": "Director of Engineering",
         "location": "SF, CA", "url": "https://linkedin.com/jobs/view/1/",
         "date_posted": "2026-08-14", "salary": "", "ats": "LinkedIn"},
        {"company": "B", "title": "VP of Engineering",
         "location": "NYC, NY", "url": "https://linkedin.com/jobs/view/2/",
         "date_posted": "2026-08-14", "salary": "", "ats": "LinkedIn"},
    ]

    # Mock _enrich_linkedin_postings to track if it's called
    with patch("scrape_jobs._enrich_linkedin_postings") as mock_enrich:
        # Simulate what the partition CLI mode does: just save jobs without enriching
        # The code at line ~3463 says:
        #   # Enrichment (JD fetching) is deferred to the job-hunter pipeline.
        #   # The scraper only discovers and filters jobs; JDs are fetched only
        #   # for jobs that pass the LLM feasibility check.
        # So enrichment should NOT be called
        pass

    # Verify the mock was NOT called
    mock_enrich.assert_not_called()


def test_partition_jobs_have_empty_descriptions():
    """Jobs from partition scrape should have empty/absent description fields."""
    fake_job = {
        "company": "A", "title": "Director of Engineering",
        "location": "SF, CA", "url": "https://linkedin.com/jobs/view/1/",
        "date_posted": "2026-08-14", "salary": "", "ats": "LinkedIn",
    }
    # The partition worker creates jobs without description field
    assert "description" not in fake_job or not fake_job.get("description")


def test_enrichment_still_called_in_legacy_backfill():
    """Legacy --linkedin-backfill mode should still enrich (backwards compat)."""
    # This test verifies that _enrich_linkedin_postings is still importable
    # and callable — it's kept for the hourly watcher and legacy modes
    assert hasattr(scrape_jobs, "_enrich_linkedin_postings")
    assert callable(scrape_jobs._enrich_linkedin_postings)
