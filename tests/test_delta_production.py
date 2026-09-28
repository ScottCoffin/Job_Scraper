"""Test delta file production from _merge_into_all_jobs.

Tests cover:
- Delta file is written when source is provided and jobs are added/enriched
- No delta file when source is None (backward compat for non-LinkedIn sources)
- No delta file when nothing changed (empty run)
- Delta envelope format (run_at, source, added, updated)
- Manifest (index.jsonl) append behavior
- Manifest pruning at DELTA_PRUNE_DAYS (30)
- Filename includes source to avoid collisions
"""
import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

from scrape_jobs import _merge_into_all_jobs, _write_delta, _prune_deltas, DELTA_PRUNE_DAYS


# ─── Helpers ──────────────────────────────────────────────────────────────

def _make_job(url, company="TestCo", title="Engineer", **extra):
    """Build a minimal valid job dict."""
    job = {"url": url, "company": company, "title": title, "ats": "LinkedIn"}
    job.update(extra)
    return job


def _read_manifest(output_dir):
    """Read the delta manifest as a list of dicts."""
    manifest = output_dir / "deltas" / "index.jsonl"
    if not manifest.exists():
        return []
    return [json.loads(line) for line in manifest.read_text().strip().split("\n") if line]


def _read_delta(output_dir, filename):
    """Read a delta file as a dict."""
    path = output_dir / "deltas" / filename
    return json.loads(path.read_text())


def _list_deltas(output_dir):
    """List delta .json files (excluding index.jsonl)."""
    d = output_dir / "deltas"
    if not d.exists():
        return []
    return sorted(f.name for f in d.glob("*.json"))


# ─── Delta production ─────────────────────────────────────────────────────

class TestDeltaProduction:
    """Delta files are produced when source is provided."""

    def test_no_delta_when_source_is_none(self, tmp_output_dir, sample_all_jobs):
        """source=None → no delta file, no manifest (backward compat)."""
        path = tmp_output_dir / "all_jobs.json"
        path.write_text(json.dumps(sample_all_jobs, separators=(",", ":")))

        new_jobs = [_make_job("https://example.com/jobs/999")]
        _merge_into_all_jobs(new_jobs, source=None)

        assert not (tmp_output_dir / "deltas").exists()

    def test_delta_written_when_source_provided(self, tmp_output_dir, sample_all_jobs):
        """source='linkedin' with new jobs → delta file + manifest line."""
        path = tmp_output_dir / "all_jobs.json"
        path.write_text(json.dumps(sample_all_jobs, separators=(",", ":")))

        new_jobs = [_make_job("https://example.com/jobs/999")]
        _merge_into_all_jobs(new_jobs, source="linkedin")

        deltas = _list_deltas(tmp_output_dir)
        assert len(deltas) == 1
        assert "linkedin" in deltas[0]

        manifest = _read_manifest(tmp_output_dir)
        assert len(manifest) == 1
        assert manifest[0]["source"] == "linkedin"
        assert manifest[0]["added"] == 1
        assert manifest[0]["updated"] == 0

    def test_no_delta_on_empty_run(self, tmp_output_dir, sample_all_jobs):
        """No new jobs and no enrichments → no delta file, no manifest line."""
        path = tmp_output_dir / "all_jobs.json"
        path.write_text(json.dumps(sample_all_jobs, separators=(",", ":")))

        _merge_into_all_jobs([], source="linkedin")

        assert _list_deltas(tmp_output_dir) == []
        assert _read_manifest(tmp_output_dir) == []

    def test_delta_envelope_format(self, tmp_output_dir, sample_all_jobs):
        """Delta file has exactly run_at, source, added, updated keys."""
        path = tmp_output_dir / "all_jobs.json"
        path.write_text(json.dumps(sample_all_jobs, separators=(",", ":")))

        _merge_into_all_jobs([_make_job("https://example.com/jobs/999")], source="linkedin")

        manifest = _read_manifest(tmp_output_dir)
        delta = _read_delta(tmp_output_dir, manifest[0]["file"])

        assert set(delta.keys()) == {"run_at", "source", "added", "updated"}
        assert delta["source"] == "linkedin"
        assert delta["run_at"]  # non-empty
        assert len(delta["added"]) == 1
        assert len(delta["updated"]) == 0

    def test_filename_includes_source(self, tmp_output_dir, sample_all_jobs):
        """Filename pattern: <timestamp>_<source>.json (no collisions across sources)."""
        path = tmp_output_dir / "all_jobs.json"
        path.write_text(json.dumps(sample_all_jobs, separators=(",", ":")))

        _merge_into_all_jobs([_make_job("https://example.com/jobs/999")], source="linkedin")

        manifest = _read_manifest(tmp_output_dir)
        filename = manifest[0]["file"]
        assert filename.endswith("_linkedin.json")
        # Timestamp part should be ISO-ish (colons replaced with dashes)
        ts_part = filename.replace("_linkedin.json", "")
        assert "T" in ts_part
        assert ":" not in ts_part  # colons are filesystem-unsafe


class TestDeltaAddedUpdated:
    """Jobs are correctly classified as added vs updated in the delta."""

    def test_new_job_in_added(self, tmp_output_dir, sample_all_jobs):
        """A genuinely new job appears in the 'added' array."""
        path = tmp_output_dir / "all_jobs.json"
        path.write_text(json.dumps(sample_all_jobs, separators=(",", ":")))

        new_job = _make_job("https://example.com/jobs/new1", company="FreshCo")
        _merge_into_all_jobs([new_job], source="linkedin")

        manifest = _read_manifest(tmp_output_dir)
        delta = _read_delta(tmp_output_dir, manifest[0]["file"])

        assert len(delta["added"]) == 1
        assert delta["added"][0]["url"] == "https://example.com/jobs/new1"
        assert "first_seen" in delta["added"][0]

    def test_enriched_job_in_updated(self, tmp_output_dir, sample_all_jobs):
        """An existing job that gains a description appears in 'updated'."""
        path = tmp_output_dir / "all_jobs.json"
        path.write_text(json.dumps(sample_all_jobs, separators=(",", ":")))

        # Job 4400000005 (StartupX) has empty description in the fixture.
        # Merge a duplicate that carries a description.
        enriched_job = _make_job(
            "https://www.linkedin.com/jobs/view/4400000005/",
            company="StartupX",
            title="Director of Platform Engineering",
            description="Now we have a description!",
        )
        _merge_into_all_jobs([enriched_job], source="linkedin")

        manifest = _read_manifest(tmp_output_dir)
        delta = _read_delta(tmp_output_dir, manifest[0]["file"])

        assert len(delta["added"]) == 0
        assert len(delta["updated"]) == 1
        assert delta["updated"][0]["url"] == "https://www.linkedin.com/jobs/view/4400000005/"
        assert delta["updated"][0]["description"] == "Now we have a description!"

    def test_duplicate_with_no_enrichment_not_in_delta(self, tmp_output_dir, sample_all_jobs):
        """A duplicate merge that doesn't add description/salary → not in delta."""
        path = tmp_output_dir / "all_jobs.json"
        path.write_text(json.dumps(sample_all_jobs, separators=(",", ":")))

        # Job 4400000001 (Acme Corp) already has a description.
        # Merge a duplicate without any new fields to backfill.
        dup_job = _make_job(
            "https://www.linkedin.com/jobs/view/4400000001/",
            company="Acme Corp",
            title="Director of Engineering",
        )
        _merge_into_all_jobs([dup_job], source="linkedin")

        manifest = _read_manifest(tmp_output_dir)
        # No delta should be written (nothing added, nothing enriched)
        assert manifest == []
        assert _list_deltas(tmp_output_dir) == []

    def test_mixed_added_and_updated(self, tmp_output_dir, sample_all_jobs):
        """A run with both new jobs and enrichments → both arrays populated."""
        path = tmp_output_dir / "all_jobs.json"
        path.write_text(json.dumps(sample_all_jobs, separators=(",", ":")))

        new_job = _make_job("https://example.com/jobs/new1", company="FreshCo")
        # Job 4400000006 (DevHub) has empty description
        enriched_job = _make_job(
            "https://www.linkedin.com/jobs/view/4400000006/",
            company="DevHub",
            title="Director of Infrastructure",
            description="Newly fetched description",
        )
        _merge_into_all_jobs([new_job, enriched_job], source="linkedin")

        manifest = _read_manifest(tmp_output_dir)
        delta = _read_delta(tmp_output_dir, manifest[0]["file"])

        assert len(delta["added"]) == 1
        assert len(delta["updated"]) == 1
        assert delta["added"][0]["url"] == "https://example.com/jobs/new1"
        assert delta["updated"][0]["url"] == "https://www.linkedin.com/jobs/view/4400000006/"


# ─── Manifest ─────────────────────────────────────────────────────────────

class TestManifest:
    """Manifest (index.jsonl) append behavior."""

    def test_two_runs_two_manifest_lines(self, tmp_output_dir, sample_all_jobs):
        """Sequential runs produce two manifest lines with correct counts."""
        path = tmp_output_dir / "all_jobs.json"
        path.write_text(json.dumps(sample_all_jobs, separators=(",", ":")))

        _merge_into_all_jobs([_make_job("https://example.com/jobs/1")], source="linkedin")
        _merge_into_all_jobs([_make_job("https://example.com/jobs/2")], source="linkedin")

        manifest = _read_manifest(tmp_output_dir)
        assert len(manifest) == 2
        assert manifest[0]["added"] == 1
        assert manifest[1]["added"] == 1
        assert manifest[0]["file"] != manifest[1]["file"]

    def test_manifest_line_fields(self, tmp_output_dir, sample_all_jobs):
        """Each manifest line has run_at, file, source, added, updated."""
        path = tmp_output_dir / "all_jobs.json"
        path.write_text(json.dumps(sample_all_jobs, separators=(",", ":")))

        _merge_into_all_jobs([_make_job("https://example.com/jobs/1")], source="linkedin")

        manifest = _read_manifest(tmp_output_dir)
        entry = manifest[0]
        assert set(entry.keys()) == {"run_at", "file", "source", "added", "updated"}
        assert entry["source"] == "linkedin"
        assert entry["file"].endswith(".json")
        assert isinstance(entry["added"], int)
        assert isinstance(entry["updated"], int)


# ─── Pruning ──────────────────────────────────────────────────────────────

class TestPruning:
    """Delta files and manifest lines older than DELTA_PRUNE_DAYS are pruned."""

    def test_prune_old_entries(self, tmp_output_dir):
        """Manifest entries older than DELTA_PRUNE_DAYS are removed + files deleted."""
        deltas_dir = tmp_output_dir / "deltas"
        deltas_dir.mkdir()
        manifest = deltas_dir / "index.jsonl"

        now = datetime.now(timezone.utc)
        old_date = now - timedelta(days=DELTA_PRUNE_DAYS + 5)
        old_iso = old_date.strftime("%Y-%m-%dT%H:%M:%SZ")
        old_filename = f"{old_iso.replace(':', '-')}_linkedin.json"

        # Write an old delta file + manifest line
        (deltas_dir / old_filename).write_text(json.dumps({"run_at": old_iso, "source": "linkedin", "added": [], "updated": []}))
        manifest.write_text(json.dumps({"run_at": old_iso, "file": old_filename, "source": "linkedin", "added": 0, "updated": 0}) + "\n")

        # Write a recent delta file + manifest line
        recent_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        recent_filename = f"{recent_iso.replace(':', '-')}_linkedin.json"
        (deltas_dir / recent_filename).write_text(json.dumps({"run_at": recent_iso, "source": "linkedin", "added": [], "updated": []}))
        with open(manifest, "a") as f:
            f.write(json.dumps({"run_at": recent_iso, "file": recent_filename, "source": "linkedin", "added": 0, "updated": 0}) + "\n")

        # Prune
        _prune_deltas(str(deltas_dir), recent_iso)

        remaining = _read_manifest(tmp_output_dir)
        remaining_files = _list_deltas(tmp_output_dir)

        assert len(remaining) == 1
        assert remaining[0]["run_at"] == recent_iso
        assert old_filename not in remaining_files
        assert recent_filename in remaining_files

    def test_prune_keeps_recent_entries(self, tmp_output_dir):
        """Entries within the prune window are kept."""
        deltas_dir = tmp_output_dir / "deltas"
        deltas_dir.mkdir()
        manifest = deltas_dir / "index.jsonl"

        now = datetime.now(timezone.utc)
        recent_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        recent_filename = f"{recent_iso.replace(':', '-')}_linkedin.json"

        (deltas_dir / recent_filename).write_text("{}")
        manifest.write_text(json.dumps({"run_at": recent_iso, "file": recent_filename, "source": "linkedin", "added": 1, "updated": 0}) + "\n")

        _prune_deltas(str(deltas_dir), recent_iso)

        remaining = _read_manifest(tmp_output_dir)
        assert len(remaining) == 1

    def test_prune_no_manifest(self, tmp_output_dir):
        """Pruning with no manifest file is a no-op (not an error)."""
        deltas_dir = tmp_output_dir / "deltas"
        # Don't create the directory or manifest
        _prune_deltas(str(deltas_dir), "2026-08-31T14:00:00Z")
        # Should not raise


# ─── _write_delta direct tests ────────────────────────────────────────────

class TestWriteDelta:
    """Direct tests of _write_delta helper."""

    def test_empty_delta_not_written(self, tmp_output_dir):
        """No added and no updated → no file, no manifest line."""
        _write_delta([], [], "linkedin", "2026-08-31T14:00:00Z")
        assert _list_deltas(tmp_output_dir) == []
        assert _read_manifest(tmp_output_dir) == []

    def test_delta_with_added_only(self, tmp_output_dir):
        """Added jobs, no updated → delta written with empty updated array."""
        job = _make_job("https://example.com/1")
        _write_delta([job], [], "linkedin", "2026-08-31T14:00:00Z")

        manifest = _read_manifest(tmp_output_dir)
        assert len(manifest) == 1
        assert manifest[0]["added"] == 1
        assert manifest[0]["updated"] == 0

        delta = _read_delta(tmp_output_dir, manifest[0]["file"])
        assert len(delta["added"]) == 1
        assert len(delta["updated"]) == 0

    def test_delta_with_updated_only(self, tmp_output_dir):
        """Updated jobs, no added → delta written with empty added array."""
        job = _make_job("https://example.com/1", description="enriched")
        _write_delta([], [job], "linkedin", "2026-08-31T14:00:00Z")

        manifest = _read_manifest(tmp_output_dir)
        assert manifest[0]["added"] == 0
        assert manifest[0]["updated"] == 1


class TestDeltaMatchesSchema:
    """The files the scraper writes must validate against schema/delta.schema.json."""

    def test_written_delta_validates(self, tmp_output_dir, sample_all_jobs):
        jsonschema = pytest.importorskip("jsonschema")
        referencing = pytest.importorskip("referencing")
        path = tmp_output_dir / "all_jobs.json"
        path.write_text(json.dumps(sample_all_jobs, separators=(",", ":")))
        existing = next(j for j in sample_all_jobs["jobs"] if not j.get("description"))
        _merge_into_all_jobs([
            _make_job("https://www.linkedin.com/jobs/view/9900000001/"),
            {**existing, "description": "Backfilled description"},
        ], source="linkedin")

        manifest = _read_manifest(tmp_output_dir)
        delta = _read_delta(tmp_output_dir, manifest[0]["file"])
        assert delta["added"] and delta["updated"]

        schema_dir = Path(__file__).parent.parent / "schema"
        jobs_schema = json.loads((schema_dir / "jobs.schema.json").read_text(encoding="utf-8"))
        delta_schema = json.loads((schema_dir / "delta.schema.json").read_text(encoding="utf-8"))
        registry = referencing.Registry().with_resource(
            "jobs.schema.json", referencing.Resource.from_contents(jobs_schema))
        jsonschema.Draft202012Validator(delta_schema, registry=registry).validate(delta)
