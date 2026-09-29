# Job Scraper — Claude Code Guide

## What this project is

GitHub Actions pipelines that scrape job boards (LinkedIn, Indeed, Glassdoor, ZipRecruiter, Google Jobs, HiringCafe, USAJOBS, CalCareers, NEOGOV/CalOpps) on a schedule, commit results to the repo, and serve them through a filterable triage dashboard (`triage.html`) hosted on GitHub Pages.

Designed to be forked. No server. No paid services required (AI triage is optional).

## Key files

| File | Purpose |
|------|---------|
| `config.json` | User's search config — keywords, locations, employers. Gitignored upstream. Copy from `config.example.json`. |
| `config.example.json` | Documented template. **Do not edit** — kept for upstream sync. |
| `scoring_profile.json` | AI triage calibration — fit/poor-fit terms. Gitignored upstream. Copy from `scoring_profile.example.json`. |
| `scrape_jobs.py` | Main scraper. Dispatched by all watcher workflows. |
| `triage_agent.py` | Claude API fit-scoring agent. Run by `triage.yml`. |
| `triage.html` | The dashboard. Pure client-side JS; reads `output/*.json` at page-load time. |
| `output/` | All scraped data (gitignored upstream). `all_jobs.json` = 14-day rolling master. |

## Workflow architecture

All 18 workflows live in `.github/workflows/`. Pattern:
- **Watcher workflows** (`*_watch.yml`, `scrape_jobs.yml`) run on cron, call `scrape_jobs.py`, then commit to `output/` when `vars.ENABLE_DATA_COMMITS == 'true'`.
- **Concurrency group** `job-scraper-commit-push` serializes all commits (prevents push conflicts).
- **`triage.yml`** scores new roles via Claude API nightly. Disabled by default — requires `ANTHROPIC_API_KEY`, `CANDIDATE_PROFILE`, `CANDIDATE_RESUME` secrets.
- **`sync_upstream.yml`** merges upstream into the fork weekly via `scripts/sync-upstream.sh`, keeping the fork's `output/` and config. Safer than GitHub's "Sync fork" button.

## Required GitHub configuration (for a fork to work)

| Item | Where to set | Required? |
|------|-------------|-----------|
| `ENABLE_DATA_COMMITS=true` | Settings → Secrets and variables → Actions → **Variables** tab | **Yes** |
| Workflow permissions: Read+write | Settings → Actions → General → Workflow permissions | **Yes** |
| GitHub Pages: main branch, / root | Settings → Pages | Yes (for dashboard) |
| `PUSHOVER_TOKEN` + `PUSHOVER_USER` secrets | Settings → Secrets | Optional |
| `SYNC_TOKEN` secret (fine-grained PAT: Contents + Workflows write) | Settings → Secrets | Optional — lets `sync_upstream.yml` push upstream workflow-file changes |
| `ANTHROPIC_API_KEY` secret | Settings → Secrets | Optional (AI triage only) |
| `CANDIDATE_PROFILE` + `CANDIDATE_RESUME` secrets | Settings → Secrets | Optional (AI triage only) |

## Automated setup

```bash
# One-command setup (requires gh CLI: https://cli.github.com)
bash scripts/setup.sh
```

Verify everything is configured:
**Actions → Validate Setup → Run workflow**

## Common development tasks

### Run a scraper locally
```bash
pip install -r requirements.txt        # only for Indeed/Glassdoor/ZipRecruiter/Google
python scrape_jobs.py --linkedin-only
python scrape_jobs.py --indeed-only
python scrape_jobs.py --hiringcafe
python scrape_jobs.py --usajobs
```

### Serve the dashboard locally
```bash
python -m http.server 8000
# Open http://localhost:8000/triage.html
```

### Run the triage agent locally
```bash
pip install anthropic
ANTHROPIC_API_KEY=sk-... \
CANDIDATE_PROFILE="..." \
CANDIDATE_RESUME="..." \
python triage_agent.py --limit 50
```

### Run evals for the triage agent
```bash
python eval_triage.py
```

### Add a new job source

1. Create `.github/workflows/SOURCENAME_watch.yml` (copy an existing simple watcher as template).
2. Add a corresponding `--sourcename` flag to `scrape_jobs.py`.
3. The watcher should write to `output/SOURCENAME_jobs.json` (and `.md`/`.html`).
4. `triage.html` discovers output files at runtime — no changes needed to the dashboard unless adding new fields.

## Secrets vs variables

GitHub Actions uses two distinct namespaces:
- **Secrets** (`secrets.NAME`): encrypted, write-only. Use for API keys, credentials.
- **Variables** (`vars.NAME`): plaintext, readable in logs. Use for feature flags and tuning knobs.

`ENABLE_DATA_COMMITS` is a **Variable** (not a secret) — a common source of confusion for new users.

## Personal data handling

`config.json`, `scoring_profile.json`, and `output/` are in `.gitignore` in the upstream repo. The `.gitattributes` file marks them `merge=ours` so `sync_upstream.yml` never overwrites your customizations.

`CANDIDATE_PROFILE` and `CANDIDATE_RESUME` live only in GitHub Actions Secrets — never written to disk or committed.
