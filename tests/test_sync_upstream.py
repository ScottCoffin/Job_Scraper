"""Integration tests for scripts/sync-upstream.sh (run by sync_upstream.yml).

Each test builds a throwaway "upstream" repo, a fork that shares its history,
and a bare "origin" the fork pushes to, then runs the real script against them.
"""
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "sync-upstream.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "sync_upstream.yml"
SYNC_FILES = (".github/workflows/sync_upstream.yml", "scripts/sync-upstream.sh")


@pytest.fixture
def git_env(tmp_path, monkeypatch):
    """Isolate git from the developer's global/system config (identity, signing, hooks)."""
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(
        "[user]\n\tname = Test\n\temail = test@example.com\n"
        "[init]\n\tdefaultBranch = main\n"
        "[commit]\n\tgpgsign = false\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def write(repo, files):
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def commit(repo, msg, files=None, remove=()):
    write(repo, files or {})
    for rel in remove:
        git(repo, "rm", "-q", rel)
    git(repo, "add", "-A", "-f")
    git(repo, "commit", "-q", "-m", msg)


class Repos:
    """upstream (plain repo), origin (bare fork on "GitHub"), fork (local clone of origin)."""

    def __init__(self, root):
        self.root = root
        self.upstream = root / "upstream"
        self.origin = root / "origin.git"
        self.fork = root / "fork"

    def show(self, path, ref="main"):
        return git(self.origin, "show", f"{ref}:{path}")

    def exists(self, path, ref="main"):
        return subprocess.run(["git", "cat-file", "-e", f"{ref}:{path}"],
                              cwd=self.origin, capture_output=True).returncode == 0

    def origin_head(self):
        return git(self.origin, "rev-parse", "main")

    def fork_commit(self, msg, files=None, remove=()):
        """Commit to the fork and push it to origin, as a scraper run would."""
        commit(self.fork, msg, files, remove)
        git(self.fork, "push", "-q", "origin", "main")

    def sync(self):
        return subprocess.run(["bash", "scripts/sync-upstream.sh"], cwd=self.fork,
                              env={**os.environ, "UPSTREAM_URL": str(self.upstream)},
                              capture_output=True, text=True)


BASE_FILES = {
    "code.py": "base\n",
    "output/all_jobs.json": "base\n",
    "output/deltas/d1.json": "base\n",
    "output/workflow_runs.jsonl": "base\n",
    "output/fork_deletes.json": "base\n",
    "output/upstream_deletes.json": "base\n",
    "output/untouched_by_fork.json": "base\n",
}


@pytest.fixture
def repos(tmp_path, git_env):
    r = Repos(tmp_path)
    r.upstream.mkdir()
    git(r.upstream, "init", "-q")
    commit(r.upstream, "base", {
        **BASE_FILES,
        ".gitattributes": (REPO_ROOT / ".gitattributes").read_text(),
        "config.json": "{}\n",  # upstream once tracked it
        **{rel: (REPO_ROOT / rel).read_text() for rel in SYNC_FILES},
    })
    git(tmp_path, "clone", "-q", "--bare", str(r.upstream), str(r.origin))
    git(tmp_path, "clone", "-q", str(r.origin), str(r.fork))
    r.fork_commit("fork: personal config", {"config.json": '{"me": 1}\n'})
    return r


def test_output_stays_exactly_as_fork_has_it(repos):
    """Every kind of upstream output/ change is dropped; upstream code merges in."""
    repos.fork_commit("fork scrape", {
        "output/all_jobs.json": "fork\n",
        "output/deltas/d1.json": "fork\n",
        "output/workflow_runs.jsonl": "base\nfork run\n",
        "output/upstream_deletes.json": "fork\n",
    }, remove=["output/fork_deletes.json"])
    commit(repos.upstream, "upstream scrape + code", {
        "code.py": "upstream\n",
        "output/all_jobs.json": "upstream\n",              # both sides modified
        "output/deltas/d1.json": "upstream\n",
        "output/workflow_runs.jsonl": "base\nupstream run\n",
        "output/fork_deletes.json": "upstream\n",          # modify/delete
        "output/untouched_by_fork.json": "upstream\n",     # one-sided edit
        "output/deltas/new.json": "upstream\n",            # upstream addition
    }, remove=["output/upstream_deletes.json"])            # delete/modify
    fork_output = git(repos.origin, "rev-parse", "main:output")

    result = repos.sync()

    assert result.returncode == 0, result.stdout + result.stderr
    assert git(repos.origin, "rev-parse", "main:output") == fork_output
    assert repos.show("code.py") == "upstream"
    assert git(repos.origin, "rev-parse", "main^2") == git(repos.upstream, "rev-parse", "main")


def test_output_stays_fork_owned_across_repeated_syncs(repos):
    """After a first sync the merge base moves; later upstream output changes must still be dropped."""
    repos.fork_commit("fork scrape", {"output/all_jobs.json": "fork 1\n"})
    commit(repos.upstream, "upstream scrape 1", {"output/all_jobs.json": "upstream 1\n"})
    assert repos.sync().returncode == 0

    git(repos.fork, "pull", "-q", "--no-rebase", "origin", "main")
    repos.fork_commit("fork scrape 2", {"output/all_jobs.json": "fork 2\n"})
    commit(repos.upstream, "upstream scrape 2", {
        "output/all_jobs.json": "upstream 2\n",
        "output/untouched_by_fork.json": "upstream 2\n",
    })
    result = repos.sync()

    assert result.returncode == 0, result.stdout + result.stderr
    assert repos.show("output/all_jobs.json") == "fork 2"
    assert repos.show("output/untouched_by_fork.json") == "base"


def test_personal_config_survives_upstream_deleting_it(repos):
    """Upstream once tracked config.json then deleted it — the reason this sync merges, not rebases."""
    commit(repos.upstream, "upstream untracks config", remove=["config.json"])

    result = repos.sync()

    assert result.returncode == 0, result.stdout + result.stderr
    assert repos.show("config.json") == '{"me": 1}'


def test_upstream_edits_to_sync_machinery_are_not_merged(repos):
    """Upstream once added an invalid `workflows: write` permission that makes GitHub reject the workflow."""
    fork_versions = {rel: repos.show(rel) for rel in SYNC_FILES}
    # One-sided upstream edits: without the reset, these merge in cleanly
    commit(repos.upstream, "upstream edits sync", {
        rel: fork_versions[rel] + "\n# upstream edit\n" for rel in SYNC_FILES
    })

    result = repos.sync()

    assert result.returncode == 0, result.stdout + result.stderr
    assert {rel: repos.show(rel) for rel in SYNC_FILES} == fork_versions


def test_already_up_to_date_is_a_no_op(repos):
    before = repos.origin_head()

    result = repos.sync()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Already up to date" in result.stdout
    assert repos.origin_head() == before


def test_code_conflict_outside_output_stops_without_pushing(repos):
    repos.fork_commit("fork edits code", {"code.py": "fork\n"})
    commit(repos.upstream, "upstream deletes code", remove=["code.py"])
    before = repos.origin_head()

    result = repos.sync()

    assert result.returncode != 0
    assert "Unexpected conflicts outside output/" in result.stderr
    assert "code.py" in result.stdout
    assert repos.origin_head() == before


def test_refuses_to_run_with_uncommitted_changes(repos):
    """Resetting output/ would silently discard local edits."""
    commit(repos.upstream, "upstream code", {"code.py": "upstream\n"})
    (repos.fork / "output" / "all_jobs.json").write_text("unsaved\n")
    before = repos.origin_head()

    result = repos.sync()

    assert result.returncode != 0
    assert "uncommitted changes" in result.stderr
    assert (repos.fork / "output" / "all_jobs.json").read_text() == "unsaved\n"
    assert repos.origin_head() == before


def test_existing_upstream_remote_is_repointed(repos):
    """Local clones often already have an `upstream` remote; UPSTREAM_URL must win."""
    git(repos.fork, "remote", "add", "upstream", "https://example.invalid/stale.git")
    commit(repos.upstream, "upstream code", {"code.py": "upstream\n"})

    result = repos.sync()

    assert result.returncode == 0, result.stdout + result.stderr
    assert repos.show("code.py") == "upstream"


def test_workflow_permission_refusal_explains_sync_token(repos):
    """GitHub's default Actions token can never push .github/workflows/ changes."""
    hook = repos.origin / "hooks" / "pre-receive"
    hook.write_text(
        "#!/bin/sh\n"
        "echo 'refusing to allow a GitHub App to create or update workflow "
        "`.github/workflows/x.yml` without `workflows` permission' >&2\n"
        "exit 1\n"
    )
    hook.chmod(0o755)
    commit(repos.upstream, "upstream code", {"code.py": "upstream\n"})

    result = repos.sync()

    assert result.returncode != 0
    assert "SYNC_TOKEN" in result.stderr


def test_workflow_runs_the_script_safely():
    """Guard the workflow wiring the script depends on."""
    text = WORKFLOW.read_text()
    assert "bash scripts/sync-upstream.sh" in text
    assert "token: ${{ secrets.SYNC_TOKEN || github.token }}" in text
    # Commit workflows must share this group or they race on output/ (see CLAUDE.md rule 6)
    assert re.search(r"^concurrency:\n\s+group: job-scraper-commit-push$", text, re.M)
    # Not a real GITHUB_TOKEN scope; GitHub rejects the whole workflow file
    assert not re.search(r"^\s+workflows:", text, re.M)
