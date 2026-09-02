"""Shared fixtures for job-scraper tests."""
import json
import sys
from pathlib import Path

import pytest

# Make scrape_jobs importable
SCRAPER_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRAPER_DIR))

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir():
    """Path to test fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture
def sample_all_jobs():
    """Synthetic all_jobs.json with 10 jobs for merge tests."""
    path = FIXTURES_DIR / "sample_all_jobs.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def tmp_output_dir(tmp_path, monkeypatch):
    """Redirect OUTPUT_DIR to a temp directory and copy fixtures there."""
    import scrape_jobs
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(scrape_jobs, "OUTPUT_DIR", str(output))
    return output
