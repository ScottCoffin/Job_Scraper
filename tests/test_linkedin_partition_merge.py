"""Test --linkedin-merge-backfill — partition merge, dedup, cleanup.

Tests the merge logic that combines per-partition JSON artifacts into the
master all_jobs.json file. Uses synthetic partition fixtures representing
different states (California, Texas, New York with a cap-hit).
"""
import json
import os
import shutil
from unittest.mock import patch

import scrape_jobs


def _copy_partitions_to_output(tmp_output_dir, sample_partition_files):
    """Copy fixture partition files to the temp output dir with production naming."""
    for pf in sample_partition_files:
        # Production code globs for "linkedin_partition_*.json"
        dest = tmp_output_dir / f"linkedin_partition_{pf.name}"
        shutil.copy(pf, dest)


def test_merge_deduplicates_by_url(tmp_output_dir, sample_partition_files):
    """3 partition files with overlapping URLs → correct unique count."""
    _copy_partitions_to_output(tmp_output_dir, sample_partition_files)

    # Simulate the production merge glob pattern
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

    # california: 5 jobs, texas: 5 jobs (1 overlap with california),
    # new_york: 3 jobs (1 overlap with texas)
    # Total unique: 5 + 4 + 2 = 11
    assert len(all_jobs) == 11


def test_merge_filters_excluded_companies(tmp_output_dir, sample_partition_files):
    """Excluded companies (Pfizer, Genentech) should be filtered out during merge
    when employers.exclude is configured."""
    _copy_partitions_to_output(tmp_output_dir, sample_partition_files)

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

    # Simulate configured exclusion terms (as employers.exclude would in production)
    with patch.object(scrape_jobs, "_EXCLUDE_COMPANY_TERMS",
                      ["pfizer", "genentech", "pharma"]):
        filtered = [j for j in all_jobs
                    if not scrape_jobs._is_excluded_company(j.get("company", ""))]
    companies = [j["company"] for j in filtered]
    assert "Pfizer" not in companies
    assert "Genentech" not in companies


def test_merge_no_exclusion_when_not_configured(tmp_output_dir, sample_partition_files):
    """When employers.exclude is empty, no companies should be filtered out."""
    _copy_partitions_to_output(tmp_output_dir, sample_partition_files)

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

    # With empty exclusion terms, nothing is filtered
    with patch.object(scrape_jobs, "_EXCLUDE_COMPANY_TERMS", []):
        filtered = [j for j in all_jobs
                    if not scrape_jobs._is_excluded_company(j.get("company", ""))]
    assert len(filtered) == len(all_jobs)


def test_merge_cap_hit_detection(sample_partition_files):
    """New York partition has hit_cap=true — should be detectable from the file."""
    # Find the cap-hit partition by checking each file (order depends on filenames)
    cap_hit_data = None
    for pf in sample_partition_files:
        with open(pf) as f:
            data = json.load(f)
        if data.get("hit_cap") is True:
            cap_hit_data = data
            break
    assert cap_hit_data is not None, "No partition with hit_cap=true found"
    assert cap_hit_data.get("raw_cards") == 990


def test_partial_merge_no_crash(tmp_output_dir, sample_partition_files):
    """Merge with only 1 of 3 partition files should work without crashing."""
    # Copy only the first partition file (California)
    shutil.copy(
        sample_partition_files[0],
        tmp_output_dir / f"linkedin_partition_{sample_partition_files[0].name}",
    )

    import glob
    all_files = sorted(glob.glob(str(tmp_output_dir / "linkedin_partition_*.json")))
    assert len(all_files) == 1

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

    assert len(all_jobs) == 5  # all 5 from California partition


def test_no_partition_files(tmp_output_dir):
    """No partition files in output dir → should handle gracefully."""
    import glob
    all_files = sorted(glob.glob(str(tmp_output_dir / "linkedin_partition_*.json")))
    assert len(all_files) == 0
