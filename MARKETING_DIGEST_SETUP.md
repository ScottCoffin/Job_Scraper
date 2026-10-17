# Daily marketing email digest — prepared locally, NOT activated

This is a local integration into **mdshah930/Job_Scraper**, based on `main` commit
`9189c98c7860c5aad9ab9705a9eb859751895057`. It reuses the fork's LinkedIn guest
search, JobSpy Indeed adapter, normalizers and URL identities. It is **not** a
replacement scraper, dashboard deployment, or evidence that email works live.
No push, PR, remote commit, workflow enablement, Pages deployment, account login,
real SMTP connection or email send was performed during preparation.

## What changed (exact filenames)

| File | Change |
|---|---|
| `config.json` | New complete marketing config, all levels; USA + explicit remote discovery |
| `marketing_digest.py` | New orchestration, conservative filtering, HTML/plain/CSV rendering, SMTP and sent ledger |
| `scrape_jobs.py` | Small opt-in adapter extensions: strict fresh-only collection, health/caps, remote LinkedIn query, raw title mode; fix empty exclusions and city/state-code handling |
| `.github/workflows/marketing_digest.yml` | New gated daily/manual workflow; read-only contents permission, serialized delivery, cache ledger |
| `requirements-marketing.txt` | Tested JobSpy version plus offline test dependencies |
| `tests/requirements-dev.txt` | Include digest test dependencies so inherited Tests CI still works |
| `tests/test_marketing_digest.py` | Labelled synthetic offline cases; mocked SMTP only |
| `tests/test_master_jobs_file_merge.py` | Freeze legacy tests' clock to their August fixture; fixes two pre-existing time-dependent failures |
| `.gitignore` | Ignore isolated environment, wheels, previews and state |
| `docs/MARKETING_DIGEST_SETUP.md` | This guide |

`config.example.json`, `requirements.txt`, existing workflows, `notify.py`, triage
and dashboard remain unchanged. The digest **never** reads `output/` or calls the
output saver (which can notify Pushover). Existing environmental output cannot
leak into the email. `config.json` remains ignored upstream; package/patch include
it explicitly. The digest reads this full config directly and fails closed if
missing/invalid, rather than deep-merging environmental defaults.

## Installation and activation order

1. **Review locally first.** Review the patch and changed-file ZIP. In a clean
   checkout at the base revision, use `git apply --check marketing-job-digest.patch`
   then `git apply marketing-job-digest.patch`, or extract the ZIP at repo root.
   If the fork has advanced, review conflicts; do not overwrite newer owner work.
   To later stage the ignored config locally: `git add -f config.json`.
   Do not stage `.digest/`, `.venv/`, emails, recipients, credentials or raw jobs.
2. **Prevent inherited automation conflicts before installation/activation.**
   Account owner: Actions → select workflow → `…` → Disable workflow (unless
   deliberately retained). Review every exact workflow listed below, especially
   the watchdog that dispatches other workflows. Do not run `scripts/setup.sh` or
   the old `Validate Setup` workflow for this digest: they prescribe unrelated
   Pages/data-commit setup. Leave `ENABLE_DATA_COMMITS` unset/false. Do not add
   Pushover, Anthropic, paid triage or scraping-proxy credentials.
3. **Obtain explicit GitHub write approval before uploading/pushing these files.**
   Nothing is installed on the remote fork yet. The owner can upload the reviewed
   files, or a planner can later apply them using an approved GitHub connection.
   Keep `ENABLE_MARKETING_EMAIL_DIGEST` and `MARKETING_EMAIL_SEND_APPROVED` unset
   or `false`. Actions may require owner enablement in a fork; do not globally
   enable inherited schedules without step 2. No Pages is needed. Only normal
   GitHub-hosted Actions on the default `main` branch is used.
4. **Run a preview:** Actions → **Daily Marketing Email Digest** → Run workflow →
   branch `main`, `dry_run=true`, `initialize_state=false`; optional lookback.
   SMTP secrets are not passed to this step. Download the short-lived
   `marketing-digest-preview-<run-id>` artifact. Inspect `digest.html`, `digest.txt`,
   `jobs.csv` and `health.json`: relevance, remote restrictions, freshness,
   source failures and caps. All-source failure is a failed run, not an empty
   success; inspect the sanitized `source_health` log. No artifact is published
   for a failed run. A synthetic local preview is **not** live coverage validation.
5. **Gmail is the selected provider.** The user selected Gmail after preparation
   started; sender and recipient addresses have not been supplied. Use the Gmail
   settings below. Choose a sender
   that account is permitted to use, and the personal recipient you want.
   Providers may need SMTP enabled, verified sender identity, an app password,
   or an account plan permitting SMTP. OAuth-only providers are not supported by
   this password/app-password implementation; choose an allowed SMTP method or
   request a separate reviewed OAuth extension. If Gmail App Passwords are unavailable,
   stop and review account eligibility; do not disable certificate checks.
6. **Owner adds settings in GitHub UI**, not chat: repository Settings → Secrets
   and variables → Actions → Variables / Secrets → New repository variable/secret.
   See exact names below. Never paste secrets or personal addresses into chat,
   config.json, a commit, a workflow input or a screenshot. Only the owner can
   authorize mailbox access/create app passwords and confirm recipient consent.
7. **Obtain explicit approval for the first live email.** Then set
   `MARKETING_EMAIL_SEND_APPROVED=true`. Run manually with `dry_run=false` and
   `initialize_state=true` **only for the first run**, or after consciously
   accepting possible duplicates following cache loss. Check the inbox/spam and
   sender status yourself; SMTP acceptance is not proof of inbox delivery. Verify
   cache-save confirmation in Actions. Later runs use `initialize_state=false`.
8. **Separately approve the daily schedule.** Only then set
   `ENABLE_MARKETING_EMAIL_DIGEST=true`. Proposed editable cron: **08:17 UTC daily**
   (`17 8 * * *`), not an inferred personal timezone preference. The job runs on
   GitHub-hosted Ubuntu without your computer. Turn either approval variable off
   to stop live mail; turning off the schedule variable still permits approved
   manual sends. Disable the workflow to stop all runs.

### Gmail setup (selected by the user)

Use **`smtp.gmail.com`**, port **`465`**, security **`ssl`**. Enable Google
**2-Step Verification** and create an **App Password** named `Job Digest` at
[Google App Passwords](https://myaccount.google.com/apppasswords). The regular
Google account password is not the SMTP password for this setup. Enter the app
password directly in GitHub, never in chat, files, commits, logs or screenshots.

Repository **Variables**:
- `SMTP_HOST` = `smtp.gmail.com`
- `SMTP_PORT` = `465`
- `SMTP_SECURITY` = `ssl`
- `MARKETING_LOOKBACK_HOURS` = `24` (optional; config already defaults to 24)

Repository **Secrets**:
- `SMTP_USERNAME`: full Gmail address of the sending account.
- `SMTP_PASSWORD`: that account's Google App Password.
- `MAIL_FROM`: the same sending Gmail address (simplest supported choice).
- `MAIL_TO`: your personal recipient address; it may be the same Gmail address.

Leave **`MARKETING_EMAIL_SEND_APPROVED`** and
**`ENABLE_MARKETING_EMAIL_DIGEST`** unset or `false` until their separate approvals.
The dry-run does not need SMTP secrets and will not send mail.

Google states that the App Passwords option can be unavailable for work/school
accounts, Advanced Protection, or 2-Step Verification set up only with security
keys. If unavailable, stop rather than substituting your normal password. Google
revokes app passwords when the main Google account password changes; update the
GitHub secret with a new app password if that happens. Gmail delivery and your
account's SMTP eligibility still need an approved live test; no connection was
made during preparation.

Official Google references retrieved and checked:
- [App passwords and prerequisites](https://support.google.com/accounts/answer/185833?hl=en)
- [Gmail SMTP hostname and SSL/STARTTLS ports](https://developers.google.com/workspace/gmail/imap/imap-smtp)

### Settings

Nonsecret repository **Variables**:

| Name | Value / meaning |
|---|---|
| `SMTP_HOST` | Provider's SMTP submission hostname; no URL scheme |
| `SMTP_PORT` | Provider's port; positive integer 1–65535 |
| `SMTP_SECURITY` | Exactly `ssl` or `starttls`; TLS certificates verified |
| `MARKETING_LOOKBACK_HOURS` | Optional positive finite hours; default from config = 24 |
| `MARKETING_EMAIL_SEND_APPROVED` | Unset/false until explicit live-email approval |
| `ENABLE_MARKETING_EMAIL_DIGEST` | Unset/false until explicit schedule approval |

Repository **Secrets** (names only):
`SMTP_USERNAME`, `SMTP_PASSWORD`, `MAIL_FROM`, `MAIL_TO`.
Sender/recipient are secrets for privacy, even though email addresses are not
passwords. One plain address per field; multiple recipients are intentionally
not supported. Example protocol choices only: SSL on port **465**, or STARTTLS
on **587**. For this user, the selected Gmail settings above use SSL on port **465**.
Username and password are only read in explicit send mode in your future runner.
Dry-run never connects to SMTP and never advances the sent ledger.

### Inherited workflows to disable/review

All remain unchanged locally; **the owner must explicitly address these** before
activation. Existing state of remote schedules was not queried or modified.

| File | Exact Actions name |
|---|---|
| `linkedin_watch.yml` | LinkedIn Watcher |
| `linkedin_watch_backup.yml` | Workflow Watchdog |
| `indeed_watch.yml` | Indeed Watcher |
| `glassdoor_watch.yml` | Glassdoor Watcher |
| `google_jobs_watch.yml` | Google Jobs Watcher |
| `ziprecruiter_watch.yml` | ZipRecruiter Watcher |
| `hiringcafe_watch.yml` | HiringCafe Watcher |
| `calcareers_watch.yml` | CalCareers Watcher |
| `csucareers_watch.yml` | CSU Careers Watcher |
| `localgov_watch.yml` | Local & State Gov Watcher (NEOGOV + CalOpps) |
| `usajobs_watch.yml` | USAJOBS Watcher |
| `scrape_jobs.yml` | Priority Employer Digest |
| `triage.yml` | Nightly Job Triage |
| `weekly_digest.yml` | Weekly Job Digest |
| `sync_upstream.yml` | Sync from upstream |

Also avoid manual **Test Pushover Notification** (`notify_test.yml`),
**LinkedIn Backfill (Parallel)** (`linkedin_backfill.yml`), **Clear Job Data**
(`clear_data.yml`), **Validate Setup** (`validate_setup.yml`), and **Triage Agent
Evals** (`evals.yml`) unless deliberately needed. The last also has push triggers.
The existing **Tests** workflow (`tests.yml`) is safe offline regression testing.

`sync_upstream.yml` contains unsupported `permissions: workflows: write` and uses
merge strategy `-X ours`; that does **not** prevent nonconflicting upstream changes
from altering this integration. It was not silently rewritten. Keep it disabled;
review future upstream updates locally and re-run tests before applying them.

## Search and matching configuration

Edit `config.json`:

- `keywords.include` / `keywords.exclude`: case-insensitive bounded phrases in
  **job title** for the digest. No generic sales/growth/content alone. No
  seniority or employer exclusions; internships through executives are included.
  Change these lists for your needs. `employers.priority` and `.exclude` are empty.
- `search_terms.linkedin` / `.indeed`: eight default broad queries cover marketing,
  SEO, paid media/search, social media, demand generation, brand and CRM.
  `marketing` covers growth/product/brand/content/performance/digital/email/
  lifecycle/operations marketing; `marketing_digest.query_family_reference`
  lists these editable query ideas (reference only, not executed separately).
- `marketing_digest.sources`: `['linkedin', 'indeed']` by default; remove one to
  turn it off. Other inherited adapters are deliberately not enabled here.
- LinkedIn searches US geoId `103644278` plus an explicit `f_WT=2` (remote) query
  with location `Worldwide` and **no country geoId**. Query-level remote evidence
  is retained; textual onsite/hybrid/remote-negation overrides it. This is an
  actual remote search, not just filtering US-only results. Guest endpoint global
  interpretation and completeness are **not live validated** and can change.
- Indeed uses `location='Remote'` separately for USA, UK and India country boards,
  plus US geographic search. These are bounded **coverage samples**, not personal
  country preferences or worldwide coverage. Add supported countries if useful.
  The pinned JobSpy implementation sends `location.where` and `dateOnIndeed`
  hour filters. Never combine `hours_old` with `is_remote`, `job_type` or
  `easy_apply` in this integration; it avoids the inherited compatibility warning.
- Final filter accepts US onsite/hybrid **or** explicitly remote postings outside
  the US. It preserves original location/restrictions and source remote evidence;
  it does not assert work authorization or worldwide eligibility. Hybrid abroad
  is not remote. Country names precede state checks, code matches need city/comma
  context; bare CA/IN and ambiguous Georgia alone are rejected. Sparse/ambiguous
  geography, unusual remote wording and title spelling can still miss roles.
- `max_results_per_query=20`, max 100, multiples of 10. Defaults: eight terms ×
  two LinkedIn geos × two requested ten-card pages = **32 page requests**, at most
  320 normalized candidates; eight terms × four Indeed geos = **32 JobSpy calls**,
  requesting up to 640 rows. These are configured bounds, **not measured yield**.
  LinkedIn page over-return is sliced to the requested cap. Indeed may internally
  paginate. Caps are reported, never presented as complete coverage.
- `source_timeout_seconds=420`: each source has a killable subprocess; timeout
  discards that incomplete source, marks it unavailable. LinkedIn strict mode
  uses 15-second HTTP timeouts, no retries/backoff and normal throttling. Overall
  workflow limit is 25 minutes. Default bounds can still be too broad/blocked on
  shared runners; tune queries/caps after observing a live dry-run. JobSpy may
  change behavior; update the pinned version only after testing.

## Freshness is conservative, not a timestamp guess

`marketing_digest.lookback_hours` defaults to **24**. Override order: manual
`lookback_hours` input → `MARKETING_LOOKBACK_HOURS` variable → config. Positive
finite values up to 8760 are accepted (48 and 72 tested). Both upstream query
windows and final filtering receive the override. Queries round **up** to whole
hours (Indeed) / seconds (LinkedIn); final comparison uses the exact requested
window with aware UTC times. The run window is anchored at collection start.

- A timestamp must have a timezone and lie inclusively within `[start, run time]`.
  Preserve its original offset in the digest. Reject future/invalid/naive dates.
- Date-only strings use a documented **UTC calendar convention**, not knowledge
  of source timezone or midnight posting. Accept only if that calendar day's
  start is within the window and the date is not future. The digest retains the
  date and labels **“date only; exact time unavailable; UTC calendar convention.”**
  At noon UTC with 24h lookback, today passes and yesterday is ambiguous/rejected.
  With 48h, yesterday passes but the previous day is still ambiguous/rejected.
  With a 1h window at noon even today's date-only record is ambiguous/rejected.
- No approximate section is enabled. Missing posting date is excluded; never
  substitute `first_seen`, `updated_at`, `dateFetched` or scrape time. LinkedIn
  card dates and JobSpy `date_posted` are the only inputs. JobSpy 1.3.0 exposes
  Indeed's `dateOnIndeed` as a **date**, not its raw timestamp; we cannot recover
  exact publication time or assert original employer publication. It is a board
  posting date. Exact timestamps *provided by the adapter* are preserved.

Consequences: today's/yesterday's sparse dates, late indexing, capped searches,
posts after collection start, blocked sources and scheduler gaps cause missed
jobs. A 24h daily window is **not lossless**. Consider 48/72h with sent dedupe if
recall matters more than a strict 24h list. No permanent-reliability promise.

## Health, privacy and delivery semantics

Successful sources with raw data but **no qualifying matches** produce a valid
zero-new-jobs digest. Empty/missing/blocked/unrecognized responses cannot prove
zero and are **unavailable**. Mixed runs carry a partial-coverage warning. A
source with some data and request errors is partial; JobSpy logged errors are
counted too. All unavailable → workflow fails, no email. Missing config, malformed
state, test failure or prerequisite failure → no email. Old output is never reused.

Every selected job appears in HTML/plain text, plus a full CSV attachment; no
silent list truncation. Salary appears only when provided; Indeed currency is
retained rather than assuming dollars. Titles/company/location are HTML escaped;
only safe HTTP(S) application URLs are allowed. CSV cells with formula-like prefixes
are escaped for spreadsheet safety. Original restrictions are shown as supplied;
check the linked posting for restrictions absent from board search cards.

Ledger: `.digest/state/sent.json`, versioned hashed URL/content identities and
acceptance times only, no addresses/job bodies. Exact normalized title/company/
location dedupes across boards; URL identities handle tracking changes. This may
merge identical-looking separate requisitions or miss changed text. No fuzzy
semantic match or exactly-once guarantee. All matching jobs are ledgered **only
after SMTP DATA acceptance**; failure before acceptance leaves jobs eligible.
If SMTP accepted but the response was lost, a retry may duplicate. A local state
write failure after acceptance emits a warning/failure; inspect before retrying.

`actions/cache/restore@v4` restores newest `marketing-sent-v1-` prefix. Each
successful live run saves `marketing-sent-v1-<run-id>-<attempt>`; a lookup verifies
the exact key exists. Missing/failed confirmation fails the workflow and warns
that SMTP already accepted. Runs are serialized, not canceled mid-send; GitHub
may replace older pending runs. Caches can be evicted, and older prefixes may be
restored after loss: duplicates remain possible. A totally missing cache blocks
scheduled/live sends until explicit manual initialization. Malformed state is
never silently replaced. Do not delete it to “fix” an error without reviewing loss.

**Cache/artifact is not a secret vault**, particularly on a public fork: workflows
and pull requests may read caches, and repo readers may access run artifacts.
Only nonsecret hashed identities are cached. Only dry-run public-job previews
are uploaded (3-day retention); no recipient, credentials, sent MIME email or raw
source descriptions. Live run output stays ephemeral on runner disk, not public
commits or Pages. Do not add sensitive annotations to preview jobs. Use a private
repository if digest content itself must be confidential.

GitHub schedules may be delayed or dropped, and public-repository schedules are
**automatically disabled after 60 days of repository inactivity**. Monitor Actions
and inbox; re-enable as owner if disabled. Compute/storage quotas, service terms
and availability apply; no unconditional “free forever” claim. Paid LLMs/proxies
are not required. Comply with job-board terms and rate limits; do not bypass blocks.

## Local checks (isolated environment)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-marketing.txt
.venv/bin/python -m pytest tests/ -q --ignore=tests/local
.venv/bin/python -m compileall -q scrape_jobs.py marketing_digest.py
# Real read-only scraping, only if you choose; still NO email:
.venv/bin/python marketing_digest.py --dry-run --lookback-hours 48
```

Do not run `--send` until owner approval. All preparation tests used synthetic
jobs and mocked SMTP. No live scraper or SMTP validation was claimed. See the
accompanying `VALIDATION.md` for actual counts, baseline findings and package checks.

### References inspected during preparation

- GitHub official [schedule event documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule):
  schedule delays/drops, default branch and 60-day public inactivity disable.
- Official [actions/cache README](https://github.com/actions/cache/blob/main/README.md):
  restore/save, lookup-only, cache-hit and eviction. Cache save can warn without
  failing, hence the explicit post-save key lookup.
- Fork source at the base revision above; installed `python-jobspy==1.3.0`,
  `jobspy.indeed.Indeed._build_query`, `_build_filters`, `_process_job` and Country
  definitions inspected locally. Live board behavior remains unverified.
