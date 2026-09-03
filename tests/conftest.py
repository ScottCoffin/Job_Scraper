"""Shared fixtures for job-scraper tests."""
import json
import sys
from pathlib import Path

import pytest

# Make scrape_jobs importable
SCRAPER_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRAPER_DIR))

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolate_output_dir(tmp_path, monkeypatch):
    """Safeguard: redirect OUTPUT_DIR to a temp dir for every test.

    This prevents any test from accidentally modifying the real output/
    directory, even if it doesn't explicitly use the tmp_output_dir fixture.
    Tests that DO use tmp_output_dir get their own subdirectory; this just
    ensures the default is always a temp path, never the real output/.
    """
    import scrape_jobs
    monkeypatch.setattr(scrape_jobs, "OUTPUT_DIR", str(tmp_path / "output"))


@pytest.fixture
def fixtures_dir():
    """Path to test fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture
def linkedin_search_results_html():
    """Real LinkedIn search results HTML (10 cards) from California."""
    path = FIXTURES_DIR / "linkedin_search_results_california.html"
    return path.read_text(encoding="utf-8")


@pytest.fixture
def linkedin_job_posting_html():
    """Real LinkedIn job posting detail page HTML (trimmed to description section)."""
    path = FIXTURES_DIR / "linkedin_job_posting_detail.html"
    return path.read_text(encoding="utf-8")


@pytest.fixture
def sample_all_jobs():
    """Synthetic all_jobs.json with 10 jobs for merge tests."""
    path = FIXTURES_DIR / "sample_all_jobs.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def sample_partition_files():
    """List of 3 synthetic LinkedIn partition JSON file paths."""
    return sorted((FIXTURES_DIR / "sample_linkedin_partitions").glob("*.json"))


@pytest.fixture
def tmp_output_dir(tmp_path, monkeypatch):
    """Redirect OUTPUT_DIR to a temp directory for tests that write output files."""
    import scrape_jobs
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(scrape_jobs, "OUTPUT_DIR", str(output))
    return output
