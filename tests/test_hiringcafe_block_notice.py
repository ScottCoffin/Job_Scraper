"""Regression coverage for the documented HiringCafe upstream block."""

import urllib.error

import scrape_jobs as scraper


def test_all_403_run_explains_known_block_and_preserves_previous(monkeypatch, capsys):
    previous = [{"title": "Previously collected role"}]

    def blocked(_term, page=0):
        raise urllib.error.HTTPError(
            "https://hiringcafe.com/jobs/test",
            403,
            "Forbidden",
            {"Cf-Mitigated": "challenge"},
            None,
        )

    monkeypatch.setattr(scraper, "HIRINGCAFE_SEARCH_TERMS", ["test"])
    monkeypatch.setattr(scraper, "HIRINGCAFE_MAX_PAGES", 1)
    monkeypatch.setattr(scraper, "_hiringcafe_ssr_hits", blocked)
    monkeypatch.setattr(scraper, "_load_prev_jobs", lambda _path: previous)
    monkeypatch.setattr(scraper.time, "sleep", lambda _seconds: None)

    assert scraper.scrape_hiringcafe_recent() == previous
    output = capsys.readouterr().out
    assert "Known upstream block" in output
    assert "Cloudflare browser challenge" in output
    assert "preserving previous 1 result(s)" in output
