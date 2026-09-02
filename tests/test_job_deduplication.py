"""Test job deduplication logic: _same_job, _dedupe_master_jobs, _merge_duplicate_job."""
from scrape_jobs import (
    _same_job,
    _dedupe_master_jobs,
    _merge_duplicate_job,
    _job_identity,
)


def test_same_url_means_same_job():
    a = {"url": "https://linkedin.com/jobs/view/123/", "company": "A", "title": "T"}
    b = {"url": "https://linkedin.com/jobs/view/123/", "company": "B", "title": "S"}
    assert _same_job(a, b) is True


def test_different_urls_different_companies():
    a = {"url": "https://linkedin.com/jobs/view/123/", "company": "A", "title": "T"}
    b = {"url": "https://linkedin.com/jobs/view/456/", "company": "B", "title": "S"}
    assert _same_job(a, b) is False


def test_same_company_similar_title_overlapping_location():
    """Same company + similar title + overlapping location = same job."""
    a = {"url": "", "company": "Acme", "title": "Director of Engineering",
         "location": "San Francisco, CA"}
    b = {"url": "", "company": "Acme", "title": "Director of Engineering",
         "location": "San Francisco, CA"}
    assert _same_job(a, b) is True


def test_dedupe_by_url():
    """5 jobs with 2 duplicate URLs → 3 unique."""
    jobs = [
        {"url": "https://linkedin.com/jobs/view/1/", "company": "A", "title": "T1"},
        {"url": "https://linkedin.com/jobs/view/2/", "company": "B", "title": "T2"},
        {"url": "https://linkedin.com/jobs/view/1/", "company": "A", "title": "T1"},
        {"url": "https://linkedin.com/jobs/view/3/", "company": "C", "title": "T3"},
        {"url": "https://linkedin.com/jobs/view/2/", "company": "B", "title": "T2"},
    ]
    kept, merged, enriched = _dedupe_master_jobs(jobs)
    assert len(kept) == 3
    assert merged == 2


def test_merge_duplicate_copies_description():
    """_merge_duplicate_job copies description from incoming if existing is missing."""
    existing = {"url": "https://linkedin.com/jobs/view/1/", "company": "A",
                "title": "T", "description": ""}
    incoming = {"url": "https://linkedin.com/jobs/view/1/", "company": "A",
                "title": "T", "description": "JD text here"}
    enriched = _merge_duplicate_job(existing, incoming)
    assert enriched == 1
    assert existing["description"] == "JD text here"


def test_merge_does_not_overwrite_existing_description():
    """_merge_duplicate_job should NOT overwrite an existing description."""
    existing = {"url": "https://linkedin.com/jobs/view/1/", "company": "A",
                "title": "T", "description": "Original JD"}
    incoming = {"url": "https://linkedin.com/jobs/view/1/", "company": "A",
                "title": "T", "description": "New JD"}
    enriched = _merge_duplicate_job(existing, incoming)
    assert enriched == 0
    assert existing["description"] == "Original JD"


def test_merge_accumulates_duplicate_urls():
    """_merge_duplicate_job should track duplicate URLs."""
    existing = {"url": "https://linkedin.com/jobs/view/1/", "company": "A",
                "title": "T"}
    incoming = {"url": "https://linkedin.com/jobs/view/2/", "company": "A",
                "title": "T"}
    _merge_duplicate_job(existing, incoming)
    assert "https://linkedin.com/jobs/view/2/" in existing.get("duplicate_urls", [])


def test_job_identity_extracts_id():
    """_job_identity extracts a stable identity from a LinkedIn URL."""
    assert _job_identity("https://www.linkedin.com/jobs/view/12345/")
    assert _job_identity("https://linkedin.com/jobs/view/67890/")
