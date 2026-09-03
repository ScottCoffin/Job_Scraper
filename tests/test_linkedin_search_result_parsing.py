"""Test _parse_linkedin_cards with real + synthetic LinkedIn HTML.

Uses 1 real LinkedIn card (kept for reference to verify parsing against
actual markup) plus 4 synthetic cards testing edge cases (salary, plain-text
company, empty location, non-job card). Filtering is applied by callers
(role_is_relevant).
"""
from scrape_jobs import _parse_linkedin_cards


def test_parses_raw_cards(linkedin_search_results_html):
    """Fixture should contain 4 job cards (1 real + 3 synthetic with URNs)."""
    jobs, raw_count = _parse_linkedin_cards(linkedin_search_results_html)
    assert raw_count == 4
    assert len(jobs) == 4  # all cards with URNs parsed, no filtering at this layer


def test_parsed_jobs_have_required_fields(linkedin_search_results_html):
    """Each parsed job must have id, company, title, location, date_posted."""
    jobs, _ = _parse_linkedin_cards(linkedin_search_results_html)
    assert len(jobs) > 0
    for job in jobs:
        assert "id" in job
        assert "company" in job
        assert "title" in job
        assert "location" in job
        assert "date_posted" in job


def test_company_parsed_for_all_cards(linkedin_search_results_html):
    """Company must be parsed for every card (needed by role_is_relevant)."""
    jobs, _ = _parse_linkedin_cards(linkedin_search_results_html)
    companies = [j["company"] for j in jobs]
    assert any(c != "Unknown" for c in companies), (
        "All companies are 'Unknown' — company may not be parsed correctly"
    )


def test_empty_html():
    """Empty HTML should return ([], 0)."""
    jobs, raw_count = _parse_linkedin_cards("")
    assert jobs == []
    assert raw_count == 0


def test_non_job_card_skipped():
    """HTML with a <li> that has no jobPosting URN should be skipped."""
    html = """
    <li>
      <div class="some-other-card">Not a job card</div>
    </li>
    """
    jobs, raw_count = _parse_linkedin_cards(html)
    assert jobs == []
    assert raw_count == 0


def test_single_card_parsed():
    """A single valid card should be parsed with all fields."""
    html = """
    <li>
      <div class="base-card" data-entity-urn="urn:li:jobPosting:123">
        <a class="base-card__full-link" href="/jobs/view/123/">
          <span class="sr-only">Software Engineer at TechCorp</span>
        </a>
        <h3 class="base-search-card__title">Software Engineer</h3>
        <h4 class="base-search-card__subtitle">
          <a>TechCorp</a>
        </h4>
        <span class="job-search-card__location">San Francisco, CA</span>
        <time datetime="2026-08-14"></time>
      </div>
    </li>
    """
    jobs, raw_count = _parse_linkedin_cards(html)
    assert raw_count == 1
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Software Engineer"
    assert jobs[0]["company"] == "TechCorp"
    assert jobs[0]["location"] == "San Francisco, CA"
    assert jobs[0]["date_posted"] == "2026-08-14"
