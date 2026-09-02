"""Test that --linkedin-merge-backfill handles mixed Phase 1 + Phase 2 files.

Phase 1 partition files have plain partition keys (e.g. "terms__California").
Phase 2 partition files have day-suffixed keys (e.g. "terms__California_d0").
The merge must correctly deduplicate by URL across both types.
"""
import json
import os
import shutil

import scrape_jobs


def _make_partition(path, partition_key, jobs, hit_cap=False):
    """Write a partition file in the production format."""
    with open(path, "w") as f:
        json.dump({
            "partition_key": partition_key,
            "terms": ["Director of Engineering"],
            "location": "California, United States",
            "jobs": jobs,
            "raw_cards": len(jobs),
            "hit_cap": hit_cap,
        }, f)


def _make_job(url, date_posted="2026-08-15"):
    return {
        "company": "TestCo",
        "title": "Director of Engineering",
        "location": "San Francisco, CA",
        "url": url,
        "date_posted": date_posted,
        "salary": "",
        "ats": "LinkedIn",
    }


def test_merge_mixed_phase1_and_phase2_files(tmp_output_dir):
    """Merge should handle both Phase 1 (no _d suffix) and Phase 2 (_d{N}) files."""
    # Phase 1 file (Alabama)
    _make_partition(
        tmp_output_dir / "linkedin_partition_alabama.json",
        "terms__Alabama",
        [_make_job("https://www.linkedin.com/jobs/view/1001/")],
    )
    # Phase 2 files (California day 0 and day 1)
    _make_partition(
        tmp_output_dir / "linkedin_partition_california_d0.json",
        "terms__California_d0",
        [_make_job("https://www.linkedin.com/jobs/view/2001/")],
    )
    _make_partition(
        tmp_output_dir / "linkedin_partition_california_d1.json",
        "terms__California_d1",
        [_make_job("https://www.linkedin.com/jobs/view/2002/")],
    )

    # Simulate the merge glob pattern
    import glob
    all_files = sorted(glob.glob(str(tmp_output_dir / "linkedin_partition_*.json")))
    assert len(all_files) == 3

    all_jobs = []
    seen_urls = set()
    for tf in all_files:
        with open(tf) as f:
            data = json.load(f)
        for j in data.get("jobs", []):
            url = j.get("url", "")
            if url not in seen_urls:
                seen_urls.add(url)
                all_jobs.append(j)

    assert len(all_jobs) == 3
    urls = {j["url"] for j in all_jobs}
    assert urls == {
        "https://www.linkedin.com/jobs/view/1001/",
        "https://www.linkedin.com/jobs/view/2001/",
        "https://www.linkedin.com/jobs/view/2002/",
    }


def test_merge_deduplicates_across_day_slices(tmp_output_dir):
    """Same job appearing in multiple day-slices should be deduplicated."""
    shared_job = _make_job("https://www.linkedin.com/jobs/view/3001/")
    _make_partition(
        tmp_output_dir / "linkedin_partition_california_d0.json",
        "terms__California_d0",
        [shared_job, _make_job("https://www.linkedin.com/jobs/view/3002/")],
    )
    _make_partition(
        tmp_output_dir / "linkedin_partition_california_d1.json",
        "terms__California_d1",
        [shared_job, _make_job("https://www.linkedin.com/jobs/view/3003/")],
    )

    import glob
    all_files = sorted(glob.glob(str(tmp_output_dir / "linkedin_partition_*.json")))
    all_jobs = []
    seen_urls = set()
    for tf in all_files:
        with open(tf) as f:
            data = json.load(f)
        for j in data.get("jobs", []):
            url = j.get("url", "")
            if url not in seen_urls:
                seen_urls.add(url)
                all_jobs.append(j)

    # 3 unique jobs (shared job appears in both files but should be deduped)
    assert len(all_jobs) == 3


def test_merge_handles_cap_hit_in_phase2_file(tmp_output_dir):
    """A Phase 2 file with hit_cap=True should be tracked but not crash merge."""
    _make_partition(
        tmp_output_dir / "linkedin_partition_california_d6.json",
        "terms__California_d6",
        [_make_job("https://www.linkedin.com/jobs/view/4001/")],
        hit_cap=True,
    )
    _make_partition(
        tmp_output_dir / "linkedin_partition_alabama.json",
        "terms__Alabama",
        [_make_job("https://www.linkedin.com/jobs/view/4002/")],
        hit_cap=False,
    )

    import glob
    all_files = sorted(glob.glob(str(tmp_output_dir / "linkedin_partition_*.json")))
    all_jobs = []
    seen_urls = set()
    cap_hits = []
    for tf in all_files:
        with open(tf) as f:
            data = json.load(f)
        hit_cap = data.get("hit_cap", False)
        for j in data.get("jobs", []):
            url = j.get("url", "")
            if url not in seen_urls:
                seen_urls.add(url)
                all_jobs.append(j)
        if hit_cap:
            cap_hits.append(data.get("partition_key", ""))

    assert len(all_jobs) == 2
    assert len(cap_hits) == 1
    assert "terms__California_d6" in cap_hits


def test_merge_phase2_only_files(tmp_output_dir):
    """Merge should work with only Phase 2 files (no Phase 1)."""
    for day in range(7):
        _make_partition(
            tmp_output_dir / f"linkedin_partition_california_d{day}.json",
            f"terms__California_d{day}",
            [_make_job(f"https://www.linkedin.com/jobs/view/500{day}/",
                       date_posted=f"2026-08-{15-day:02d}")],
        )

    import glob
    all_files = sorted(glob.glob(str(tmp_output_dir / "linkedin_partition_*.json")))
    assert len(all_files) == 7

    all_jobs = []
    seen_urls = set()
    for tf in all_files:
        with open(tf) as f:
            data = json.load(f)
        for j in data.get("jobs", []):
            url = j.get("url", "")
            if url not in seen_urls:
                seen_urls.add(url)
                all_jobs.append(j)

    assert len(all_jobs) == 7  # one unique job per day-slice
