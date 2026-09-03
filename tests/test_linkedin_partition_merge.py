"""Test _linkedin_merge_backfill_files — partition merge, dedup, cleanup.

Tests the real merge function that combines per-partition JSON artifacts
into a single deduplicated job list. Uses synthetic partition fixtures
representing different states (California, Texas, New York with a cap-hit).
"""
import json
import shutil
from unittest.mock import patch

import scrape_jobs


def _copy_partitions_to_output(tmp_output_dir, sample_partition_files):
    """Copy fixture partition files to the temp output dir with production naming."""
    for pf in sample_partition_files:
        dest = tmp_output_dir / f"linkedin_partition_{pf.name}"
        shutil.copy(pf, dest)


@patch.object(scrape_jobs, "_EXCLUDE_COMPANY_TERMS", [])
def test_merge_deduplicates_by_url(tmp_output_dir, sample_partition_files):
    """3 partition files with overlapping URLs → correct unique count."""
    _copy_partitions_to_output(tmp_output_dir, sample_partition_files)

    all_jobs, stats, cap_hits = scrape_jobs._linkedin_merge_backfill_files(
        str(tmp_output_dir))

    # california: 5 jobs, texas: 5 jobs (1 overlap with california),
    # new_york: 3 jobs (1 overlap with texas)
    # Total unique: 5 + 4 + 2 = 11
    assert len(all_jobs) == 11


def test_merge_filters_excluded_companies(tmp_output_dir, sample_partition_files):
    """Excluded companies should be filtered out during merge."""
    _copy_partitions_to_output(tmp_output_dir, sample_partition_files)

    with patch.object(scrape_jobs, "_EXCLUDE_COMPANY_TERMS",
                      ["pfizer", "genentech", "pharma"]):
        all_jobs, stats, cap_hits = scrape_jobs._linkedin_merge_backfill_files(
            str(tmp_output_dir))

    companies = [j["company"] for j in all_jobs]
    assert "Pfizer" not in companies
    assert "Genentech" not in companies


def test_merge_no_exclusion_when_not_configured(tmp_output_dir, sample_partition_files):
    """When employers.exclude is empty, no companies should be filtered out."""
    _copy_partitions_to_output(tmp_output_dir, sample_partition_files)

    with patch.object(scrape_jobs, "_EXCLUDE_COMPANY_TERMS", []):
        all_jobs, stats, cap_hits = scrape_jobs._linkedin_merge_backfill_files(
            str(tmp_output_dir))

    assert len(all_jobs) == 11


@patch.object(scrape_jobs, "_EXCLUDE_COMPANY_TERMS", [])
def test_merge_cap_hit_detection(tmp_output_dir, sample_partition_files):
    """New York partition has hit_cap=true — should be reported in cap_hits."""
    _copy_partitions_to_output(tmp_output_dir, sample_partition_files)

    all_jobs, stats, cap_hits = scrape_jobs._linkedin_merge_backfill_files(
        str(tmp_output_dir))

    assert len(cap_hits) == 1
    cap_hit_stat = next(s for s in stats if s["hit_cap"])
    assert cap_hit_stat["raw"] == 990


@patch.object(scrape_jobs, "_EXCLUDE_COMPANY_TERMS", [])
def test_partial_merge_no_crash(tmp_output_dir, sample_partition_files):
    """Merge with only 1 of 3 partition files should work without crashing."""
    shutil.copy(
        sample_partition_files[0],
        tmp_output_dir / f"linkedin_partition_{sample_partition_files[0].name}",
    )

    all_jobs, stats, cap_hits = scrape_jobs._linkedin_merge_backfill_files(
        str(tmp_output_dir))

    assert len(all_jobs) == 5  # all 5 from California partition


def test_no_partition_files(tmp_output_dir):
    """No partition files in output dir → empty result, no crash."""
    all_jobs, stats, cap_hits = scrape_jobs._linkedin_merge_backfill_files(
        str(tmp_output_dir))

    assert len(all_jobs) == 0
    assert len(stats) == 0
    assert len(cap_hits) == 0


def test_merge_handles_both_term_and_partition_files(tmp_output_dir):
    """linkedin_backfill_*.json (term files) and linkedin_partition_*.json
    should both be merged into a single result."""
    import json
    # Term file
    with open(tmp_output_dir / "linkedin_backfill_term1.json", "w") as f:
        json.dump({"term": "term1", "jobs": [
            {"url": "http://x/1", "company": "A", "title": "T", "ats": "LinkedIn"}
        ], "raw_cards": 1, "hit_cap": False}, f)
    # Partition file
    with open(tmp_output_dir / "linkedin_partition_p1.json", "w") as f:
        json.dump({"partition_key": "p1", "jobs": [
            {"url": "http://x/2", "company": "B", "title": "T", "ats": "LinkedIn"}
        ], "raw_cards": 1, "hit_cap": False}, f)

    all_jobs, stats, cap_hits = scrape_jobs._linkedin_merge_backfill_files(
        str(tmp_output_dir))

    assert len(all_jobs) == 2
    urls = {j["url"] for j in all_jobs}
    assert urls == {"http://x/1", "http://x/2"}
