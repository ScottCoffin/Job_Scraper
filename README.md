# 🧪 Environmental / Toxicology Job Scraper — Dr. Scott Coffin

Three GitHub Actions pipelines that scrape **environmental toxicology, risk &
exposure assessment, environmental health, water quality, microplastics/PFAS &
emerging-contaminants, chemical safety / regulatory, and supporting data-science
roles** across **California**, commit the results to this repo, and surface them
in the [`triage.html`](#interactive-triage-dashboard--triagehtml) dashboard.

> Originally built by [Ernesto Diaz](https://github.com/ernestod1998) as a Bay
> Area ML-engineer scraper; retargeted here for Dr. Scott Coffin's field
> (environmental/regulatory toxicology). See his profile at
> [scottcoff.in](https://scottcoff.in).

## What It Does

### 1. Priority-employer digest — daily, last 24h
Hits LinkedIn's public guest endpoint for California env/tox roles posted in the
last 24 hours, then post-filters to a **priority-employer allowlist** derived
from `BIOTECH_COMPANY_NAMES` in `scrape_jobs.py` — environmental/tox consulting
firms (Ramboll, Exponent, Gradient, ToxStrategies, Tetra Tech, ICF, Integral,
Geosyntec…), research institutes & NGOs (SCCWRP, SFEI, Silent Spring, EDF, NRDC,
EWG, Health Effects Institute, RTI, Battelle…), agencies (US EPA, CalEPA/OEHHA,
State Water Board, CARB, DTSC, NIEHS…), water utilities, universities, and
product-safety teams in industry. Add to that list to expand coverage.

Output goes to `jobs.json`, `jobs.md`, and `jobs.html`. Each run dedupes against
the previously-committed `jobs.json`, so the output surfaces only postings new
since the last run.

> A direct-ATS probe path (`CURATED_BIOTECHS`) also exists but is **empty by
> default** — environmental/tox employers overwhelmingly use iCIMS/Taleo/
> SuccessFactors rather than the public Greenhouse/Workday JSON endpoints the
> original biotech version relied on. The LinkedIn + Indeed keyword watchers
> (which need no employer slug) are the primary sources.

### 2. LinkedIn watcher — hourly, last 1h
Hits LinkedIn's public guest endpoint for **California** roles posted in the last
hour across the env/tox search terms, dedupes by job ID, and sorts by recency.
Output goes to `linkedin_jobs.json`, `linkedin_jobs.md`, and `linkedin_jobs.html`.

Runs hourly at :17 PT (8am–8pm) via native GitHub cron, with the in-repo
watchdog (`linkedin_watch_backup.yml` at :33) re-dispatching missed slots. A
block guard preserves the previous results when LinkedIn returns zero cards
across every term (rate-limited run).

> ⚠️ Uses the unauthenticated public guest endpoint only — **never** signs in
> with a user account and does not use LinkedIn cookies, tokens, or credentials.

### 3. Indeed watcher — hourly, last 24h
Uses [`python-jobspy`](https://pypi.org/project/python-jobspy/) (Indeed's RSS and
Publisher API were deprecated in 2026 and the site sits behind Cloudflare;
JobSpy uses Indeed's mobile-app API internally). Searches California. Output goes
to `indeed_jobs.json`, `indeed_jobs.md`, and `indeed_jobs.html`, deduped against
the previous run. Runs at :47 PT, offset from LinkedIn's :17 slot.

## Keywords Matched

A title is included if it contains any of these (case-insensitive). Multi-word
phrases match as substrings; single tokens are word-bounded (so list full words).
Full list lives in `KEYWORDS` in `scrape_jobs.py`:

**Toxicology:** `toxicologist`, `toxicology`, `ecotoxicologist`, `environmental
toxicolog`, `regulatory toxicolog`, `computational toxicolog`, `aquatic
toxicolog`, `research toxicolog`

**Risk / exposure / hazard:** `risk assess`, `risk assessor`, `human health
risk`, `ecological risk`, `exposure scien`, `exposure assess`, `hazard assess`,
`dose-response`, `pharmacokinetic`, `toxicokinetic`

**Environmental science / health / chemistry:** `environmental scien`,
`environmental health`, `environmental chemist`, `environmental engineer`,
`environmental epidemiolog`, `public health`, `epidemiologist`

**Water / contaminants:** `water quality`, `water resources`, `drinking water`,
`microplastic`, `nanoplastic`, `pfas`, `emerging contaminant`, `contaminant`,
`pollution`, `remediation`

**Chemical safety / regulatory / stewardship:** `chemical safety`, `chemical
risk`, `chemical assess`, `chemical regulatory`, `product steward`

The list is deliberately **tight** for precision: generic titles (`research
scientist`, `senior scientist`, `data scientist`, `professor`, `regulatory
affairs`) are *not* matched on their own, because they pull in pharma / biotech /
tech bench roles. Environmental academic, data, and policy roles are still caught
via their qualified forms (`Environmental Data Scientist`, `Assistant Professor
of Environmental Health`, etc.).

**Excluded everywhere:**
- **Junior / training:** `intern`, `internship`, `co-op`, `trainee`,
  `apprentice`, `technician`, `research/lab/teaching assistant`, `undergraduate`,
  `postdoc`, `work-study`, `volunteer`, `fellowship`. (Unlike the original, which
  dropped *senior* titles — Dr. Coffin is a senior IC, so senior / principal /
  lead / director roles are **kept**.)
- **EHS / workplace-safety compliance:** `EHS`, `health & safety`, `occupational
  safety/health` — a distinct field from environmental-tox science. (Does *not*
  touch `Chemical Safety`, which is in-scope.)

## Geographic Scope

Searches **California**, **Portland & Bend, Oregon**, and **Australia**. Edit the
geo lists at the top of `scrape_jobs.py` to add/remove regions:

- **LinkedIn** — `LINKEDIN_GEOS`: each is a `location` + LinkedIn `geoId`
  (California `102095887`, Portland metro `90000079`, Australia `101452733`;
  Bend uses location text with no geoId). Find a region's geoId by probing the
  guest endpoint, or leave it blank for a city LinkedIn can resolve.
- **Indeed** — `INDEED_GEOS`: each is a `location` + `country` (USA → indeed.com,
  Australia → au.indeed.com). Uses a tighter `INDEED_SEARCH_TERMS` list since
  every term is searched in every geo.
- **CalCareers** is California-only (state civil service).
- The curated/legacy ATS path filters with `is_target_location()` /
  `TARGET_LOCATIONS`.

## Output Files

| File | Source | Description |
|---|---|---|
| `jobs.json` / `.md` / `.html` | Priority-employer digest | Allowlisted env/tox employer roles, last 24h, deduped against the previous run |
| `linkedin_jobs.json` / `.md` / `.html` | LinkedIn watcher | California roles posted in the last 1h, deduped |
| `indeed_jobs.json` / `.md` / `.html` | Indeed watcher | Indeed-sourced California roles, last 24h, deduped |
| `calcareers_jobs.json` / `.md` / `.html` | CalCareers watcher | California state civil-service roles (calcareers.ca.gov) |
| `usajobs_jobs.json` / `.md` / `.html` | USAJOBS watcher | Federal roles with salary (EPA, NOAA, USGS, FDA, NIEHS…) via usajobs.gov |
| `governmentjobs_jobs.json` / `.md` / `.html` | NEOGOV watcher | CA/OR state & local-gov roles (air & water districts, county env health) via governmentjobs.com |
| `calopps_jobs.json` / `.md` / `.html` | CalOpps watcher | California local-agency roles (cities, counties, special districts) via calopps.org |
| `all_jobs.json` | accumulator | Cumulative 14-day master (feeds the dashboard + triage) |
| `scores.json` | triage agent | Optional fit verdicts keyed by job URL |

### CalCareers (California state jobs)

`scrape_jobs.py --calcareers-only` scrapes [calcareers.ca.gov](https://calcareers.ca.gov)
— the CA state civil-service portal where OEHHA, DTSC, CARB, the Water Boards,
Fish & Wildlife, and Caltrans post scientist roles. CalCareers is an ASP.NET
WebForms site with **no public API**, so the scraper seeds a session and fires
the search postback (`__EVENTTARGET=ctl00$cphMainContent$btnSearch` with the
keyword field), then parses the labeled result cards. The working postback
method was adapted from the [OpenPostings](https://github.com/Masterjx9/OpenPostings)
`calcareers` module. Fully guarded; runs daily via `calcareers_watch.yml`.
Verified returning real roles (Water Board, Fish & Wildlife, OEHHA, DTSC…).

### USAJOBS (federal jobs)

`scrape_jobs.py --usajobs-only` scrapes [usajobs.gov](https://www.usajobs.gov) —
federal env/tox roles at EPA, NOAA, USGS, FDA, NIEHS, CDC, DOI, etc., **with
salary**. It uses the site's public search endpoint (`/Search/ExecuteSearch`),
so **no API key is required**: it seeds a session, then POSTs each keyword and
keeps titles that pass the env/tox filter. Runs daily via `usajobs_watch.yml`.
Federal roles are nationwide; use the dashboard's location filter/map to focus.

> Source identified from the [OpenPostings](https://github.com/Masterjx9/OpenPostings)
> project's catalog of 80+ ATS providers. OpenPostings is a self-hosted
> aggregator (not a hosted API), so rather than depend on it we query the
> official USAJOBS public endpoint directly.

### NEOGOV & CalOpps (state & local government)

Also added from the OpenPostings catalog — the boards that carry county/city
environmental roles LinkedIn and Indeed miss:

- **`--governmentjobs-only`** ([governmentjobs.com](https://www.governmentjobs.com) /
  NEOGOV) — state & local agencies nationwide; keyword-searched and **filtered to
  CA/OR**. Surfaces e.g. air-district Air Quality Specialists, county Hazardous
  Materials Specialists, water-district roles.
- **`--calopps-only`** ([calopps.org](https://www.calopps.org)) — California
  local agencies (cities, counties, special & water districts). CA-only board, so
  it's title-filtered only (e.g. Water Resources Specialist, Environmental Health
  Specialist).

Both are HTML scrapes (no API), fully guarded, and run daily via
`localgov_watch.yml`. Local-gov env roles are sparse, so yield is low but
high-signal — they catch the occasional perfect agency role that the big boards
don't list.

### Dashboard features

The `triage.html` cockpit adds, on top of the source/role/seniority/date filters:

- **★ Priority topics** — roles touching signature topics (microplastics,
  ecotoxicology, endocrine-disrupting chemicals, R/Shiny) get a gold ★ and a
  highlighted card; a toggle filters to just those. Edit `STAR_TERMS` in
  `triage.html` to change what's flagged.
- **Cross-source de-dup** — the same role cross-posted to LinkedIn and Indeed
  collapses into one card (matched on title + location + compatible company),
  showing both source badges; triage applies to all copies at once.
- **★ Best fit** view — ranks roles by match to Dr. Coffin's specializations
  (microplastics, ecotoxicology, risk assessment, exposure, QSAR, PFAS,
  drinking water, computational tox…). Weights live in `FIT_TERMS` in
  `triage.html`; every card shows a 0–100 fit chip.
- **🚫 Not relevant** button — hides a role *and* learns from it: titles sharing
  distinctive words with your "not relevant" marks are down-ranked in Best fit.
- **Salary slider** — harmonizes inconsistent pay formats (hourly, monthly,
  yearly, `$k` ranges, title-embedded) to an annual figure, then filters by a
  minimum, with an "include unlisted" toggle.
- **🗺 Map** view — Leaflet map of roles by California city (client-side
  geocoding, no API key); hover a dot for the location, click for the roles
  (top by fit). Remote/statewide roles cluster at the state center.

### Interactive triage dashboard — `triage.html`

A single-file dashboard hosted on GitHub Pages that merges the latest source
JSONs into one filterable cockpit: search; source / role / seniority filters
(roles classified as Toxicology, Risk/Exposure, Water, Contaminants,
Environmental Health, Environmental Science, Policy/Regulatory, Data Science,
Academic); save / applied / dismiss buttons persisted in localStorage; top-
companies and role-mix charts; and an "export saved as Claude prompt" action.

**View it (after enabling Pages — see Deployment):**
`https://scottcoffin.github.io/Job_Scraper/triage.html`

The dashboard fetches the JSON files from the same repo at view time, so it
always reflects the latest committed scrape. To run locally:
```bash
python -m http.server 8000
# then visit http://localhost:8000/triage.html
```
Opening from `file://` won't work — the dashboard needs same-origin HTTP to
`fetch()` the source JSONs.

## Setup

### Run manually

From the **Actions** tab → Run workflow:
- *Priority Employers Digest* → priority-employer LinkedIn allowlist, last 24h
- *LinkedIn Env/Tox Watcher* → general California LinkedIn, last 1h
- *Indeed Env/Tox Watcher* → California Indeed via python-jobspy, last 24h

Or locally:
```bash
python scrape_jobs.py --biotech-only     # priority-employer digest (allowlist)
python scrape_jobs.py --linkedin-only    # general env/tox LinkedIn, last 1h
python scrape_jobs.py --indeed-only      # general env/tox Indeed, last 24h
python scrape_jobs.py --calcareers-only  # California state jobs (calcareers.ca.gov)
python scrape_jobs.py --usajobs-only     # federal jobs (usajobs.gov, no API key)
python scrape_jobs.py --governmentjobs-only  # state/local gov (NEOGOV, CA/OR)
python scrape_jobs.py --calopps-only     # California local agencies (calopps.org)
```
The LinkedIn/priority pipelines use only the standard library. Indeed requires
`pip install -r requirements.txt` (single dep: `python-jobspy`).

### 📲 Phone notifications (Pushover)

Get a push to your phone the moment a **highly-relevant** new role appears. After
each scrape, `notify.py` pushes any new posting that either touches a priority
topic (microplastics, ecotoxicology, endocrine-disrupting chemicals, R/Shiny) or
scores ≥ `NOTIFY_MIN_FIT` (default 75) on the resume-fit model. It dedupes
against `notified.json`, so the same role is never pushed twice (across sources
or runs). Priority-topic hits ping at high priority.

To enable, add these in **Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `PUSHOVER_TOKEN` | Your Pushover **application/API token** (create an app at pushover.net) |
| `PUSHOVER_USER` | Your Pushover **user key** (top of your pushover.net dashboard) |

Optional **Variable** (not secret): `NOTIFY_MIN_FIT` — lower than 75 for more
(less selective) pings, higher for fewer. Without the two secrets, notifications
are simply off (everything else still works).

**Test it** (sends one push to your phone):
- **From GitHub (recommended):** Actions → **Test Pushover Notification** → *Run
  workflow*. Uses your Actions secrets, so it confirms the real setup. The run
  log prints whether the keys are set and the exact Pushover API response on
  failure (e.g. a bad token/user key).
- **Locally:**
  ```bash
  PUSHOVER_TOKEN=xxx PUSHOVER_USER=yyy python notify.py --test
  ```

### Optional: nightly fit-scoring agent (`triage.yml`)

`triage_agent.py` scores each new role against your profile with the Claude API.
It is **optional** and needs three repo secrets (**Settings → Secrets and
variables → Actions**):

| Secret | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `CANDIDATE_PROFILE` | Short profile text (your background/targets — kept out of the public repo) |
| `CANDIDATE_RESUME` | Resume / CV text (kept out of the public repo) |

Paste your CV text into `CANDIDATE_RESUME`. Without these secrets, leave
`triage.yml` and `evals.yml` disabled (Actions → ⋯ → Disable workflow) — the
scrapers and dashboard work fully without them; `scores.json` is optional.

> Note: `eval_triage.py` still contains the original ML-candidate golden cases.
> They only matter if you run the triage agent; rewrite them for your domain (or
> keep `evals.yml` disabled) once you've finalized your profile.

## Repo Structure

```
├── scrape_jobs.py                  # All scraping logic + KEYWORDS / search terms / allowlist
├── triage_agent.py                 # Optional nightly fit-scoring agent (Claude API)
├── eval_triage.py                  # Golden-case evals for the triage agent (legacy ML cases)
├── requirements.txt                # python-jobspy (Indeed only)
├── jobs.{json,md,html}             # Priority-employer digest (last 24h)
├── linkedin_jobs.{json,md,html}    # LinkedIn watcher (last 1h)
├── indeed_jobs.{json,md,html}      # Indeed watcher (last 24h)
├── all_jobs.json                   # Cumulative 14-day master
├── scores.json                     # Triage verdicts (optional)
├── triage.html                     # Interactive dashboard
└── .github/workflows/
    ├── scrape_jobs.yml             # Daily — priority-employer digest
    ├── linkedin_watch.yml          # Hourly :17 PT — general LinkedIn (last 1h)
    ├── indeed_watch.yml            # Hourly :47 PT — Indeed (last 24h)
    ├── calcareers_watch.yml        # Daily — CalCareers (California state jobs)
    ├── usajobs_watch.yml           # Daily — USAJOBS (federal jobs, no API key)
    ├── localgov_watch.yml          # Daily — NEOGOV + CalOpps (state & local gov)
    ├── linkedin_watch_backup.yml   # Watchdog :33 PT — re-dispatches missed runs
    ├── triage.yml                  # Nightly — optional fit scoring (needs secrets)
    └── evals.yml                   # Triage-agent evals (optional)
```

## Tuning the search

Everything you'd adjust lives near the top of `scrape_jobs.py`:
- `KEYWORDS` — title match terms.
- `EXCLUDED_SENIORITY_RE` — junior/student titles to drop.
- `LINKEDIN_SEARCH_TERMS` / `WORKDAY_SEARCH_TERMS` — queries sent to the boards.
- `BIOTECH_COMPANY_NAMES` — the priority-employer allowlist (for `jobs.json`).
- `TARGET_LOCATIONS` + the LinkedIn `geoId` + the Indeed `location`.
