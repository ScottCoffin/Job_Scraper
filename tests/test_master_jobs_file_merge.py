"""Test _merge_into_all_jobs — master file merge with field preservation."""
import json
from scrape_jobs import _merge_into_all_jobs


def test_merge_adds_new_jobs(tmp_output_dir, sample_all_jobs):
    """Merging 3 new jobs (2 genuinely new, 1 duplicate) → added == 2."""
    # Write the sample to the temp output dir
    path = tmp_output_dir / "all_jobs.json"
    path.write_text(json.dumps(sample_all_jobs, separators=(",", ":")))

    new_jobs = [
        {"url": "https://www.linkedin.com/jobs/view/9900000001/",
         "company": "NewCo", "title": "Director of Engineering",
         "location": "SF, CA", "ats": "LinkedIn"},
        {"url": "https://www.linkedin.com/jobs/view/9900000002/",
         "company": "OtherCo", "title": "VP of Engineering",
         "location": "NYC, NY", "ats": "LinkedIn"},
        # Duplicate URL of existing job
        {"url": "https://www.linkedin.com/jobs/view/4400000001/",
         "company": "Acme Corp", "title": "Director of Engineering",
         "location": "San Francisco, CA", "ats": "LinkedIn",
         "description": "Updated description"},
    ]
    added = _merge_into_all_jobs(new_jobs)
    assert added == 2


def test_preserves_feasible_true_tag(tmp_output_dir, sample_all_jobs):
    """Existing feasible:true tag must be preserved when merging a duplicate."""
    path = tmp_output_dir / "all_jobs.json"
    path.write_text(json.dumps(sample_all_jobs, separators=(",", ":")))

    # Merge a job that duplicates an existing feasible:true job
    new_jobs = [
        {"url": "https://www.linkedin.com/jobs/view/4400000001/",
         "company": "Acme Corp", "title": "Director of Engineering",
         "location": "San Francisco, CA", "ats": "LinkedIn"},
    ]
    _merge_into_all_jobs(new_jobs)

    data = json.loads(path.read_text())
    job = next(j for j in data["jobs"] if j["url"] == "https://www.linkedin.com/jobs/view/4400000001/")
    assert job.get("feasible") is True
    assert job.get("feasibility") == "preferred"


def test_preserves_feasible_false_tag(tmp_output_dir, sample_all_jobs):
    """Existing feasible:false tag must be preserved when merging a duplicate."""
    path = tmp_output_dir / "all_jobs.json"
    path.write_text(json.dumps(sample_all_jobs, separators=(",", ":")))

    new_jobs = [
        {"url": "https://www.linkedin.com/jobs/view/4400000008/",
         "company": "SalesForce", "title": "Director of Sales",
         "location": "Chicago, IL", "ats": "LinkedIn"},
    ]
    _merge_into_all_jobs(new_jobs)

    data = json.loads(path.read_text())
    job = next(j for j in data["jobs"] if j["url"] == "https://www.linkedin.com/jobs/view/4400000008/")
    assert job.get("feasible") is False
    assert job.get("feasibility") == "no"


def test_sets_first_seen_on_new_jobs(tmp_output_dir, sample_all_jobs):
    """New jobs should get a first_seen timestamp."""
    path = tmp_output_dir / "all_jobs.json"
    path.write_text(json.dumps(sample_all_jobs, separators=(",", ":")))

    new_jobs = [
        {"url": "https://www.linkedin.com/jobs/view/9900000099/",
         "company": "NewCo", "title": "Director of Engineering",
         "location": "SF, CA", "ats": "LinkedIn"},
    ]
    _merge_into_all_jobs(new_jobs)

    data = json.loads(path.read_text())
    job = next(j for j in data["jobs"] if j["url"] == "https://www.linkedin.com/jobs/view/9900000099/")
    assert "first_seen" in job
    assert job["first_seen"]


def test_output_is_valid_json(tmp_output_dir, sample_all_jobs):
    """Output file should be valid JSON with updated_at and jobs keys."""
    path = tmp_output_dir / "all_jobs.json"
    path.write_text(json.dumps(sample_all_jobs, separators=(",", ":")))

    _merge_into_all_jobs([])

    data = json.loads(path.read_text())
    assert "updated_at" in data
    assert "jobs" in data
    assert isinstance(data["jobs"], list)


def test_empty_master_file(tmp_output_dir):
    """Merging into a non-existent master should create it."""
    new_jobs = [
        {"url": "https://www.linkedin.com/jobs/view/9900000001/",
         "company": "NewCo", "title": "Director of Engineering",
         "location": "SF, CA", "ats": "LinkedIn"},
    ]
    added = _merge_into_all_jobs(new_jobs)
    assert added == 1

    data = json.loads((tmp_output_dir / "all_jobs.json").read_text())
    assert len(data["jobs"]) == 1
