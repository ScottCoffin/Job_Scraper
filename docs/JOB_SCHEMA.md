# Job data contract

What `scrape_jobs.py` writes to `output/`, for anyone building a tool that reads it. The machine-validatable schemas are [`schema/jobs.schema.json`](../schema/jobs.schema.json) (one job record) and [`schema/delta.schema.json`](../schema/delta.schema.json) (one delta file).

## Delta files (incremental consumption)

LinkedIn runs write a small per-run delta file to `output/deltas/`, so downstream tools can fetch only what changed instead of re-downloading the full `all_jobs.json` on every run.

- **Delta file**: `output/deltas/<timestamp>_<source>.json` — `added` and `updated` arrays of full job records.
- **Manifest**: `output/deltas/index.jsonl` — append-only, one line per delta file.
- **Pruning**: delta files and manifest lines older than 30 days (`DELTA_PRUNE_DAYS`) are removed on each run. For older history, read `all_jobs.json`.
- **Opt-in per source**: only LinkedIn produces deltas today. Any source can opt in by passing `source="<name>"` to `save_jobs_output()` and staging `output/deltas/` in its workflow's commit step. Sources without it merge into `all_jobs.json` exactly as before.
- **Transport**: read `output/deltas/` from a checkout, or fetch it over HTTP from GitHub Pages or `raw.githubusercontent.com`.

### Delta file format

```json
{
  "run_at": "2026-08-31T14:00:00Z",
  "source": "linkedin",
  "added": [ {job record}, ... ],
  "updated": [ {job record}, ... ]
}
```

- `run_at` — UTC timestamp of the scraper run; matches the filename (with `:` replaced by `-`).
- `source` — the source that produced the run.
- `added` — jobs newly added to `all_jobs.json` this run (they got a new `first_seen`).
- `updated` — existing jobs enriched this run (gained a `description` or `salary` through a duplicate merge). Contains the full updated record.

A run with nothing added and nothing enriched writes no delta file and no manifest line.

### Manifest line format

```json
{"run_at": "2026-08-31T14:00:00Z", "file": "2026-08-31T14-00-00Z_linkedin.json", "source": "linkedin", "added": 5, "updated": 2}
```

The delta file is written before its manifest line. If a run dies in between, the orphaned file is simply never listed; those jobs are still in `all_jobs.json`.

### Consuming deltas

1. Fetch `output/deltas/index.jsonl`.
2. Track which `file` values you have already processed. (Not `run_at`: two runs finishing in the same second share a `run_at` and get separate files, the second with a `_2` suffix.)
3. For each line whose `file` you haven't processed, fetch `output/deltas/<file>`.
4. Upsert every job in `added` and `updated` into your store, keyed by `url` (both arrays hold full records).
5. Record the `file` as processed.
6. On a cold start, or if you have been away longer than 30 days, do one full sync from `output/all_jobs.json` first, then switch to deltas.

## Job record fields

Job records appear in `all_jobs.json`, the per-source files and delta files.

**Required fields** (always present):

| Field | Type | Description |
|-------|------|-------------|
| `url` | string | Canonical posting URL (http/https). Primary key. |
| `title` | string | Job title. |
| `company` | string | Employer name. |
| `ats` | string | Source label, e.g. `LinkedIn`, `Indeed`, `Glassdoor`, `ZipRecruiter`, `GoogleJobs`, `HiringCafe`, `CalCareers`, `CSUCareers`, `USAJOBS`, `NEOGOV`, `CalOpps`, `Greenhouse`, `Workday`, `Priority`. |
| `first_seen` | string | UTC timestamp added by the merge step. Present in `all_jobs.json` and delta files, not in per-source files. |

**Optional fields** (may be absent depending on source):

| Field | Type | Description | Sources that populate it |
|-------|------|-------------|------------------------|
| `location` | string | Location string. May be empty. | All sources |
| `date_posted` | string | ISO date or relative string. Format varies. | All sources, may be empty |
| `salary` | string | Raw salary string. May be empty. | Most sources |
| `description` | string | Job description (LinkedIn: ≤12k chars; JobSpy: ≤6k). **Absent for government boards.** | LinkedIn (detail-page fetch), Indeed/Glassdoor/ZipRecruiter, Google Jobs, HiringCafe, CSU Careers (short summary) |
| `direct_url` | string | Direct apply URL when different from `url`. | Some sources; backfilled during merge |
| `job_type` | string | Employment type (full-time, contract, etc.). | Some sources; backfilled during merge |
| `is_remote` | boolean | Remote signal. | JobSpy boards |
| `telework` | string | Board-specific telework label. | CalCareers, NEOGOV |
| `work_arrangement` | string | Normalized: `On-site`, `Remote`, `Hybrid`. | Sources where inferable |
| `salary_source` | string | Where salary was extracted from. | Some sources |
| `salary_currency` | string | Currency code (e.g. USD). | Some sources |
| `emails` | string | Contact emails, comma-separated. | JobSpy sources |
| `company_url` | string | Employer homepage URL. | Some sources |
| `duplicate_urls` | array[string] | Alternate URLs for the same job. | Added by merge step |

Records allow extra fields (`additionalProperties: true`), so downstream tools may add their own.
