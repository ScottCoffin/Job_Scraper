"""Guards around the LinkedIn backfill: own search config, matrix size, empty Phase 2.

These run the real scrape_jobs.py in a temp directory with
--linkedin-emit-matrix, which builds the job list without any network calls.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
EXAMPLE = json.loads((REPO_ROOT / "config.example.json").read_text(encoding="utf-8"))
OWN_TERMS = ["Staff Data Engineer", "Analytics Engineer", "Data Platform Lead"]


@pytest.fixture
def scraper_dir(tmp_path):
    for name in ("scrape_jobs.py", "config.example.json"):
        shutil.copy(REPO_ROOT / name, tmp_path / name)
    (tmp_path / "output").mkdir()
    return tmp_path


def own_config(**partitions):
    cfg = json.loads(json.dumps(EXAMPLE))
    cfg["search_terms"]["linkedin"] = OWN_TERMS
    cfg["locations"]["linkedin_partitions"].update(partitions)
    return cfg


def emit(scraper_dir, *args, config=None, env=None):
    if config is not None:
        text = config if isinstance(config, str) else json.dumps(config)
        (scraper_dir / "config.json").write_text(text, encoding="utf-8")
    gh_output = scraper_dir / "gh_output"
    gh_output.write_text("")
    run_env = {**os.environ, "GITHUB_OUTPUT": str(gh_output), **(env or {})}
    result = subprocess.run(
        [sys.executable, "scrape_jobs.py", "--linkedin-emit-matrix", *args],
        cwd=scraper_dir, env=run_env, capture_output=True, text=True, timeout=60)
    m = re.search(r"^matrix=(.*)$", gh_output.read_text(), re.M)
    return result, (json.loads(m.group(1)) if m else None)


@pytest.mark.parametrize("config, reason", [
    (None, "config.json is missing or isn't valid JSON"),
    ("\n", "config.json is missing or isn't valid JSON"),  # empty CONFIG_JSON secret
    ({"keywords": {"include": ["data"]}}, "doesn't set search_terms.linkedin"),
])
def test_backfill_refuses_without_own_search(scraper_dir, config, reason):
    result, matrix = emit(scraper_dir, config=config)

    assert result.returncode != 0
    assert "Refusing to run a LinkedIn backfill" in result.stderr
    assert reason in result.stderr
    assert matrix is None
    assert not (scraper_dir / "output" / "linkedin_matrix.json").exists()


def test_backfill_runs_with_own_search(scraper_dir):
    result, matrix = emit(scraper_dir, config=own_config())

    assert result.returncode == 0, result.stderr
    # 3 terms -> 2 batches of <=2, x (US-wide + Remote)
    assert len(matrix) == 4
    assert {tuple(m["terms"]) for m in matrix} == {tuple(OWN_TERMS[:2]), tuple(OWN_TERMS[2:])}


def test_example_copied_into_config_json_is_used(scraper_dir):
    """Copying config.example.json to config.json is a deliberate choice, so it runs."""
    result, matrix = emit(scraper_dir, config=EXAMPLE)

    assert result.returncode == 0, result.stderr
    assert matrix and matrix[0]["terms"] == EXAMPLE["search_terms"]["linkedin"][:2]


def test_phase2_without_high_volume_locations_emits_empty_matrix(scraper_dir):
    """The workflow skips Phase 2's fan-out when this is exactly []."""
    result, matrix = emit(scraper_dir, "--phase", "high", config=own_config())

    assert result.returncode == 0, result.stderr
    assert matrix == []


def test_matrix_over_github_limit_stops_before_searching(scraper_dir):
    states = [{"name": f"S{i}", "location": f"State {i}, United States"} for i in range(200)]
    result, matrix = emit(scraper_dir, config=own_config(states=states))

    assert result.returncode != 0
    assert "GitHub allows at most 256" in result.stderr
    assert matrix is None


def _config_steps():
    import yaml
    wf = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "linkedin_backfill.yml").read_text())
    return [step["run"] for job in wf["jobs"].values() for step in job["steps"]
            if step.get("name") == "Write config.json from secret"]


@pytest.mark.parametrize("secret, expected", [
    ("", "committed"),              # secret not set: keep the committed config.json
    ('{"from": "secret"}', "secret"),  # secret set: it wins
])
def test_backfill_config_step_keeps_committed_config_unless_secret_set(tmp_path, secret, expected):
    steps = _config_steps()
    assert steps, "linkedin_backfill.yml has no 'Write config.json from secret' steps"
    for run in steps:
        config = tmp_path / "config.json"
        config.write_text('{"from": "committed"}')
        subprocess.run(["bash", "-eo", "pipefail", "-c", run], cwd=tmp_path, check=True,
                       env={**os.environ, "CONFIG_JSON": secret})
        assert json.loads(config.read_text())["from"] == expected
