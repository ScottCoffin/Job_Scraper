"""A country the user targets in location_filter.terms must not be dropped as "non-US".

Runs scrape_jobs.py's location filter with different configs.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def kept(tmp_path, terms, locations):
    for name in ("scrape_jobs.py", "config.example.json"):
        shutil.copy(REPO_ROOT / name, tmp_path / name)
    (tmp_path / "config.json").write_text(json.dumps({"location_filter": {"terms": terms}}))
    code = ("import json, sys, scrape_jobs as s\n"
            "print(json.dumps([s.is_target_location(l) for l in json.loads(sys.argv[1])]))")
    out = subprocess.run([sys.executable, "-c", code, json.dumps(locations)], cwd=tmp_path,
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out.strip().splitlines()[-1])


def test_targeted_country_is_kept(tmp_path):
    assert kept(tmp_path, ["australia", "sydney"],
                ["Sydney, New South Wales, Australia", "Melbourne, Victoria, Australia"]) == [True, True]


def test_targeted_multi_word_country_is_kept(tmp_path):
    assert kept(tmp_path, ["london", "united kingdom"], ["London, England, United Kingdom"]) == [True]


def test_untargeted_countries_are_still_dropped(tmp_path):
    # ", ca" targets California; it must not let Canada (or anything else) through.
    assert kept(tmp_path, ["california", ", ca", "remote"],
                ["Toronto, Ontario, Canada", "Sydney, New South Wales, Australia",
                 "San Jose, CA", "Remote"]) == [False, False, True, True]
