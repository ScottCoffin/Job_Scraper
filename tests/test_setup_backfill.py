"""setup.sh's backfill trigger must wait for the run it started, one watcher at a time.

Runs the real run_and_wait() from scripts/setup.sh against a fake `gh` that
simulates GitHub's run list: a scheduled run of the same workflow can start at
the same moment, and a freshly dispatched run takes a few polls to appear.
"""
import json
import os
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

FAKE_GH = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys
    state_path = os.environ["FAKE_GH_STATE"]
    state = json.load(open(state_path))
    args = sys.argv[1:]

    def save():
        json.dump(state, open(state_path, "w"))

    def opt(name, default=None):
        return args[args.index(name) + 1] if name in args else default

    if args[:2] == ["workflow", "run"]:
        wf = args[2]
        if wf in state["disabled"]:
            sys.exit(1)
        # A scheduled run of the same workflow starts at the same moment (and is newer),
        # while our dispatched run only shows up after a few polls.
        state["runs"].append({"id": 300, "wf": wf, "event": "schedule", "visible_after": 0})
        state["runs"].append({"id": 200, "wf": wf, "event": "workflow_dispatch",
                              "visible_after": state["polls"] + 3})
        save()
    elif args[:2] == ["run", "list"]:
        state["polls"] += 1
        save()
        runs = [r for r in state["runs"]
                if r["wf"] == opt("-w") and state["polls"] > r["visible_after"]
                and opt("--event", r["event"]) == r["event"]]
        runs.sort(key=lambda r: r["id"], reverse=True)  # newest first, like gh
        runs = runs[:int(opt("-L", "20"))]
        query = opt("-q")
        if query == ".[].databaseId":
            print("\\n".join(str(r["id"]) for r in runs))
        elif query == ".[0].databaseId // 0":
            print(runs[0]["id"] if runs else 0)
        else:
            sys.exit(f"fake gh: unsupported -q {query!r}")
    elif args[:2] == ["run", "watch"]:
        state["watched"].append(int(args[2]))
        save()
    else:
        sys.exit(f"fake gh: unsupported {args}")
''')


def run_and_wait(tmp_path, workflow, runs=(), disabled=()):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "sleep").write_text("#!/bin/sh\n")  # don't actually wait between polls
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"runs": list(runs), "disabled": list(disabled),
                                 "polls": 0, "watched": []}))
    script = textwrap.dedent(f'''\
        set -euo pipefail
        ok() {{ echo "ok $*"; }}; warn() {{ echo "warn $*"; }}; info() {{ echo "info $*"; }}
        eval "$(sed -n '/^run_and_wait() {{/,/^}}/p' '{REPO_ROOT / "scripts" / "setup.sh"}')"
        run_and_wait {workflow} --field backfill=true
    ''')
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60,
                            env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
                                 "FAKE_GH_STATE": str(state)})
    return result, json.loads(state.read_text())


def test_waits_for_its_own_run_not_a_scheduled_one(tmp_path):
    earlier = [{"id": 100, "wf": "indeed_watch.yml", "event": "workflow_dispatch", "visible_after": 0}]

    result, state = run_and_wait(tmp_path, "indeed_watch.yml", runs=earlier)

    assert result.returncode == 0, result.stderr
    assert state["watched"] == [200]
    assert "indeed_watch.yml finished" in result.stdout


def test_disabled_workflow_is_skipped(tmp_path):
    result, state = run_and_wait(tmp_path, "indeed_watch.yml", disabled=["indeed_watch.yml"])

    assert result.returncode == 0, result.stderr
    assert state["watched"] == []
    assert "Skipped: indeed_watch.yml" in result.stdout
