"""
Bay Area MLE/DS Job Scraper
Three pipelines (see __main__): LinkedIn guest-endpoint watcher, Indeed via
python-jobspy, and a curated-biotech sweep (direct Greenhouse/Workday probes +
allowlist-filtered LinkedIn). Each writes {basename}.{json,md,html} digests and
accumulates into all_jobs.json for the nightly triage agent and the dashboard.
"""

import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen, Request, build_opener, HTTPCookieProcessor
from urllib.error import URLError

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

KEYWORDS = [
    # ---- ML / AI ----
    "machine learning engineer", "ml engineer", "mle",
    "machine learning infra", "ml platform", "ai platform",
    "ai engineer", "ai/ml engineer",
    "mlops", "research engineer",
    "llm engineer", "generative ai", "genai engineer", "prompt engineer",
    "deep learning", "reinforcement learning",
    "computer vision", "nlp engineer",
    # ---- Applied / AI / ML scientist ----
    "applied scientist", "ai scientist", "ml scientist",
    # ---- Data science ----
    "data scientist", "data science",
    # ---- Software engineering (broad) ----
    "software engineer", "software developer",
    "backend engineer", "back-end engineer", "backend developer",
    "frontend engineer", "front-end engineer", "frontend developer",
    "full stack engineer", "full-stack engineer", "fullstack engineer",
    "mobile engineer", "ios engineer", "android engineer",
    # ---- Platform / infra / ops ----
    "platform engineer",
    "infrastructure engineer", "infra engineer",
    "systems engineer", "distributed systems",
    "cloud engineer",
    "devops engineer", "devops",
    "site reliability engineer",
    "security engineer",
    # ---- Data engineering ----
    "data engineer", "data engineering",
    "analytics engineer",
    "data platform", "data infrastructure",
    "etl engineer", "etl developer",
    # ---- Robotics / perception ----
    "robotics engineer", "perception engineer",
    # ---- Computational / informatics (biotech) ----
    "computational scientist", "computational biologist",
    "bioinformatics scientist", "bioinformatics engineer",
    "cheminformatics",
]

# Seconds to wait between API probes — keeps us polite
REQUEST_DELAY = 0.3

# Biotech digest should only contain reliably fresh roles.
FRESH_JOB_LOOKBACK = timedelta(hours=24)

# Senior-track and executive titles are excluded everywhere: the candidate
# targets early-to-mid IC roles. Covers IC senior tracks (staff/principal/
# distinguished/founding) and management/exec tiers (director/VP/chief/head of).
EXCLUDED_SENIORITY_RE = re.compile(
    r'\b(staff|principal|distinguished|founding|director|vice president|s?vp|chief|head of)\b',
    re.IGNORECASE)

# Multi-word phrases keep substring semantics; single-word keywords ("mle",
# "devops") are word-bounded so they can't match inside a word ("Hamlet").
_KEYWORD_RE = re.compile(
    "|".join(
        re.escape(k) if " " in k else rf"\b{re.escape(k)}\b"
        for k in KEYWORDS
    ),
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch(url):
    req = Request(url, headers=HEADERS)
    try:
        with urlopen(req, timeout=15) as r:
            return r.read().decode("utf-8", errors="ignore")
    except (URLError, TimeoutError, OSError) as e:
        print(f"  ⚠️  Could not fetch {url}: {e}")
        return ""


def is_mle_role(title: str) -> bool:
    if EXCLUDED_SENIORITY_RE.search(title):
        return False
    return bool(_KEYWORD_RE.search(title))


BAY_AREA_LOCATIONS = [
    "bay area",
    "san francisco", "south san francisco", "daly city",
    "oakland", "berkeley", "alameda", "emeryville", "richmond",
    "palo alto", "mountain view", "menlo park", "sunnyvale",
    "santa clara", "san jose", "cupertino", "los altos", "los gatos",
    "san mateo", "foster city", "redwood city", "brisbane", "millbrae",
    "san bruno", "burlingame", "belmont",
    "fremont", "hayward", "union city", "newark", "milpitas",
    "concord", "walnut creek", "pleasanton", "dublin", "san ramon",
    "danville", "livermore",
    "novato", "san rafael", "mill valley", "sausalito",
    "vacaville",
]


def is_bay_area(location: str) -> bool:
    if not location:
        return False
    loc = location.lower()
    return any(city in loc for city in BAY_AREA_LOCATIONS)


def extract_location(job: dict) -> str:
    loc = job.get("jobLocation", {})
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    addr = loc.get("address", {})
    if isinstance(addr, dict):
        city = addr.get("addressLocality", "")
        state = addr.get("addressRegion", "")
        return f"{city}, {state}".strip(", ")
    return str(addr)


def _parse_posted_at(value: str, *, now: datetime | None = None) -> datetime | None:
    """
    Parse ATS posting dates into UTC datetimes.

    Some ATS APIs return exact ISO dates/datetimes, while Workday often returns
    relative strings like "Posted Today" or "Posted 3 hours ago".
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    raw = (value or "").strip()
    if not raw:
        return None

    text = re.sub(r'\s+', ' ', raw).strip().lower()
    text = text.removeprefix("posted ").strip()

    if text in {"today", "just posted", "just now"}:
        return now

    relative_m = re.search(
        r'(\d+)\s*(minutes?|mins?|hours?|hrs?)\b(?:\s*ago)?',
        text,
    )
    if relative_m:
        amount = int(relative_m.group(1))
        unit = relative_m.group(2)
        if unit.startswith(("minute", "min")):
            return now - timedelta(minutes=amount)
        return now - timedelta(hours=amount)

    iso_value = raw.replace("Z", "+00:00")
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', iso_value):
            parsed = datetime.strptime(iso_value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            parsed = datetime.fromisoformat(iso_value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            else:
                parsed = parsed.astimezone(timezone.utc)
        return parsed
    except ValueError:
        return None


def is_recent_posting(job: dict, *, now: datetime | None = None) -> bool:
    posted_at = _parse_posted_at(job.get("date_posted", ""), now=now)
    if posted_at is None:
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    return timedelta(0) <= now - posted_at <= FRESH_JOB_LOOKBACK


# ---------------------------------------------------------------------------
# Curated Bay Area biotechs — direct ATS probes (Greenhouse / Workday)
# ---------------------------------------------------------------------------

# Each entry must include: name, ats, fallback_location, and the ATS-specific id
# - greenhouse: "slug" (used in boards-api.greenhouse.io/v1/boards/{slug}/jobs)
# - workday:    "url"  (full /wday/cxs/{tenant}/{site}/jobs endpoint)
CURATED_BIOTECHS = [
    # ---- Greenhouse (confirmed via probes) ----
    {"name": "10x Genomics",         "ats": "greenhouse", "slug": "10xgenomics",       "fallback_location": "Pleasanton, CA"},
    {"name": "Twist Bioscience",     "ats": "greenhouse", "slug": "twistbioscience",   "fallback_location": "South San Francisco, CA"},
    {"name": "Maze Therapeutics",    "ats": "greenhouse", "slug": "mazetherapeutics",  "fallback_location": "South San Francisco, CA"},
    {"name": "Freenome",             "ats": "greenhouse", "slug": "freenome",          "fallback_location": "South San Francisco, CA"},
    {"name": "Cytokinetics",         "ats": "greenhouse", "slug": "cytokinetics",      "fallback_location": "South San Francisco, CA"},
    {"name": "Natera",               "ats": "greenhouse", "slug": "natera",            "fallback_location": "San Carlos, CA"},
    {"name": "Inceptive",            "ats": "greenhouse", "slug": "inceptive",         "fallback_location": "Palo Alto, CA"},
    {"name": "Atomwise",             "ats": "greenhouse", "slug": "atomwise",          "fallback_location": "San Francisco, CA"},
    {"name": "Profluent",            "ats": "greenhouse", "slug": "profluent",         "fallback_location": "Berkeley, CA"},
    {"name": "Eikon Therapeutics",   "ats": "greenhouse", "slug": "eikontherapeutics", "fallback_location": "South San Francisco, CA"},
    {"name": "Altos Labs",           "ats": "greenhouse", "slug": "altoslabs",         "fallback_location": "Redwood City, CA"},
    {"name": "Arc Institute",        "ats": "greenhouse", "slug": "arcinstitute",      "fallback_location": "Palo Alto, CA"},
    {"name": "Caribou Biosciences",  "ats": "greenhouse", "slug": "caribou",           "fallback_location": "Berkeley, CA"},
    {"name": "Octant Bio",           "ats": "greenhouse", "slug": "octantbio",         "fallback_location": "Emeryville, CA"},
    # ---- Workday (confirmed) ----
    {"name": "Gilead Sciences",      "ats": "workday",
     "url": "https://gilead.wd1.myworkdayjobs.com/wday/cxs/gilead/gileadcareers/jobs",
     "fallback_location": "Foster City, CA"},
]


def probe_curated_greenhouse(entry: dict) -> list:
    time.sleep(REQUEST_DELAY)
    url = f"https://boards-api.greenhouse.io/v1/boards/{entry['slug']}/jobs?content=true"
    raw = fetch(url)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    jobs = []
    for job in data.get("jobs", []):
        title = job.get("title", "")
        if not is_mle_role(title):
            continue
        loc = (job.get("location") or {}).get("name", "") or entry["fallback_location"]
        jobs.append({
            "company": entry["name"],
            "title": title,
            "location": loc,
            "url": job.get("absolute_url", f"https://boards.greenhouse.io/{entry['slug']}"),
            "date_posted": (job.get("updated_at") or "")[:10],
            "ats": "Greenhouse",
        })
    return jobs


WORKDAY_SEARCH_TERMS = [
    "machine learning",
    "data scientist",
    "applied scientist",
    "computational biology",
    "bioinformatics",
    "AI engineer",
]


def probe_curated_workday(entry: dict) -> list:
    """
    Workday's /jobs endpoint sometimes 400s on empty searchText, so we hit it
    once per term and dedupe by externalPath.
    """
    domain_m = re.match(r'https://([^/]+)', entry["url"])
    domain = domain_m.group(1) if domain_m else ""
    site_m = re.search(r'/wday/cxs/[^/]+/([^/]+)/jobs', entry["url"])
    site = site_m.group(1) if site_m else ""

    seen: dict[str, dict] = {}
    for term in WORKDAY_SEARCH_TERMS:
        time.sleep(REQUEST_DELAY)
        body = json.dumps({"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": term}).encode()
        try:
            req = Request(
                entry["url"],
                data=body,
                headers={**HEADERS, "Content-Type": "application/json", "Accept": "application/json"},
            )
            with urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8", errors="ignore"))
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            print(f"  ⚠️  Workday {entry['name']} ({term!r}): {e}")
            continue

        for posting in data.get("jobPostings", []):
            ext_path = posting.get("externalPath", "")
            if ext_path in seen:
                continue
            title = posting.get("title", "")
            if not is_mle_role(title):
                continue
            public_url = f"https://{domain}/{site}{ext_path}" if ext_path else entry["url"]
            loc = posting.get("locationsText", "") or entry["fallback_location"]
            # Workday summarizes multi-location roles as "N Locations" — assume HQ
            if re.match(r'^\d+ Locations?$', loc):
                loc = entry["fallback_location"]
            seen[ext_path] = {
                "company": entry["name"],
                "title": title,
                "location": loc,
                "url": public_url,
                "date_posted": posting.get("postedOn") or "",
                "ats": "Workday",
            }
    return list(seen.values())


def scrape_curated_biotechs() -> list:
    print(f"🔬 Scraping {len(CURATED_BIOTECHS)} curated Bay Area biotechs...")
    all_jobs: list = []
    for entry in CURATED_BIOTECHS:
        if entry["ats"] == "greenhouse":
            jobs = probe_curated_greenhouse(entry)
        elif entry["ats"] == "workday":
            jobs = probe_curated_workday(entry)
        else:
            print(f"  ⚠️  Unknown ATS for {entry['name']}: {entry['ats']}")
            continue
        if jobs:
            print(f"  ✅ {entry['name']}: {len(jobs)} role(s)")
            all_jobs.extend(jobs)
    return all_jobs


# ---------------------------------------------------------------------------
# Genentech — custom Phenom ATS, kept as standalone
# ---------------------------------------------------------------------------

def scrape_genentech():
    print("🔍 Scraping Genentech...")
    url = (
        "https://careers.gene.com/us/en/search-results"
        "?keywords=machine+learning+engineer&category=Data+Science+%26+AI%2FML"
    )
    html = fetch(url)
    jobs = []

    matches = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
    for match in matches:
        try:
            data = json.loads(match)
            items = (
                data if isinstance(data, list)
                else data.get("itemListElement", []) if data.get("@type") == "ItemList"
                else [data]
            )
            for item in items:
                job = item.get("item", item)
                title = job.get("title", job.get("name", ""))
                if title and is_mle_role(title):
                    jobs.append({
                        "company": "Genentech",
                        "title": title,
                        "location": extract_location(job),
                        "url": job.get("url", "https://careers.gene.com/us/en/c/data-science-ai-ml-jobs"),
                        "date_posted": job.get("datePosted", ""),
                        "ats": "Phenom",
                    })
        except json.JSONDecodeError:
            continue

    if not jobs:
        title_matches = re.findall(r'data-ph-at-job-title-text="([^"]+)"', html)
        link_matches = re.findall(r'href="(/us/en/job/[^"]+)"', html)
        for i, title in enumerate(title_matches):
            if is_mle_role(title):
                link = link_matches[i] if i < len(link_matches) else ""
                jobs.append({
                    "company": "Genentech",
                    "title": title,
                    "location": "South San Francisco, CA",
                    "url": f"https://careers.gene.com{link}" if link else "https://careers.gene.com/us/en/c/data-science-ai-ml-jobs",
                    "date_posted": "",
                    "ats": "Phenom",
                })

    print(f"  ✅ Found {len(jobs)} MLE role(s) at Genentech")
    return jobs


# ---------------------------------------------------------------------------
# LinkedIn — public guest endpoint, bucketed by recency (broad US-wide net)
# ---------------------------------------------------------------------------

LINKEDIN_SEARCH_TERMS = [
    # ML / AI / DS
    "machine learning engineer",
    "data scientist",
    "applied scientist",
    "AI engineer",
    "MLOps engineer",
    # Software engineering
    "software engineer",
    "backend engineer",
    "frontend engineer",
    "full stack engineer",
    "mobile engineer",
    # Platform / infra / ops
    "platform engineer",
    "devops engineer",
    "site reliability engineer",
    "infrastructure engineer",
    "security engineer",
    # Data engineering
    "data engineer",
    "analytics engineer",
    # Biotech / informatics
    "computational biologist",
    "bioinformatics",
    "cheminformatics",
]

LINKEDIN_LOOKBACK_SECONDS = 3600          # 1h — every-2h watcher only surfaces the freshest hour
LINKEDIN_BIOTECH_LOOKBACK_SECONDS = 86400 # 24h — biotech is a daily 8pm PT digest

# Biotech allowlist used by the LinkedIn-side filter. Broader than CURATED_BIOTECHS
# (which only covers the 15 companies with direct Greenhouse/Workday probes) because
# the public LinkedIn endpoint surfaces a wider universe of biotech employers.
# Match is case-insensitive on alphanum-stripped names with bidirectional substring
# matching, so "Genentech" matches "Genentech, Inc." and vice versa. Avoid names
# shorter than ~6 chars to limit incidental substring collisions.
BIOTECH_COMPANY_NAMES = [
    # Direct-scrape biotechs (kept aligned with CURATED_BIOTECHS)
    "10x Genomics", "Twist Bioscience", "Maze Therapeutics", "Freenome",
    "Cytokinetics", "Natera", "Inceptive", "Atomwise", "Profluent",
    "Eikon Therapeutics", "Altos Labs", "Arc Institute", "Caribou Biosciences",
    "Octant Bio", "Gilead Sciences",
    # Big pharma / biotech with Bay Area MLE hiring
    "Genentech", "AbbVie", "Amgen", "BioMarin", "Vertex Pharmaceuticals",
    "Bristol Myers Squibb", "Regeneron", "Pfizer",
    # Sequencing / genomics platforms
    "Illumina", "Pacific Biosciences", "PacBio", "Element Biosciences",
    "Ultima Genomics", "Singular Genomics",
    # Clinical genomics / diagnostics
    "GRAIL", "Guardant Health", "Invitae", "Color Health", "Tempus AI",
    "Foundation Medicine", "Veracyte", "Personalis", "Karius",
    "Adaptive Biotechnologies",
    # ML-driven drug discovery
    "Recursion Pharmaceuticals", "Insitro", "Schrodinger", "Schrödinger",
    "Relay Therapeutics", "Generate Biomedicines", "Isomorphic Labs",
    "AbCellera", "Iambic Therapeutics", "Lila Sciences",
    # Cell / gene therapy
    "Sana Biotechnology", "Allogene Therapeutics", "Cellares",
    "Beam Therapeutics", "Editas Medicine", "Intellia Therapeutics",
    "CRISPR Therapeutics",
    # Bay Area biotech & life-sci research
    "Verily Life Sciences", "Calico Life Sciences", "Synthego",
    "Buck Institute", "Chan Zuckerberg Biohub", "Chan Zuckerberg Initiative",
]

BIOTECH_COMPANY_ALLOWLIST = frozenset(
    re.sub(r'[^a-z0-9]', '', n.lower()) for n in BIOTECH_COMPANY_NAMES
)


def _is_biotech_company(name: str) -> bool:
    norm = re.sub(r'[^a-z0-9]', '', (name or "").lower())
    if not norm:
        return False
    return any(b in norm or norm in b for b in BIOTECH_COMPANY_ALLOWLIST)


def _parse_linkedin_cards(html: str) -> tuple[list[dict], int]:
    """Returns (keyword-matched cards, raw card count on the page). The raw
    count lets callers distinguish 'page full of non-matching roles' (keep
    paginating) from 'no results at all' (stop)."""
    import html as html_mod
    cards = re.split(r'<li[^>]*>', html)[1:]
    parsed = []
    raw_count = 0
    for card in cards:
        urn = re.search(r'data-entity-urn="urn:li:jobPosting:(\d+)"', card)
        if not urn:
            continue
        raw_count += 1
        title_m = re.search(r'base-search-card__title[^>]*>\s*([^<]+)', card)
        company_m = re.search(
            r'base-search-card__subtitle[^>]*>.*?<a[^>]*>\s*([^<]+)\s*</a>',
            card, re.DOTALL,
        ) or re.search(r'base-search-card__subtitle[^>]*>\s*([^<]+)', card)
        location_m = re.search(r'job-search-card__location[^>]*>\s*([^<]+)', card)
        time_m = re.search(r'<time[^>]*datetime="([^"]+)"', card)

        title = html_mod.unescape(title_m.group(1).strip()) if title_m else ""
        if not title or not is_mle_role(title):
            continue
        company = (
            html_mod.unescape(re.sub(r'\s+', ' ', company_m.group(1).strip()))
            if company_m else "Unknown"
        )
        location = html_mod.unescape(
            (location_m.group(1).strip() if location_m else "")
        ).replace("\n", " ")
        parsed.append({
            "id": urn.group(1),
            "company": company,
            "title": title,
            "location": location,
            "date_posted": time_m.group(1) if time_m else "",
        })
    return parsed, raw_count


def _linkedin_search(terms: list[str], lookback_seconds: int) -> tuple[list[dict], int]:
    """
    Per-term, paginated LinkedIn guest-endpoint search. Dedupes by job ID and
    sorts by recency. Used by both the general MLE/DS watcher and the biotech
    allowlist-filtered scrape.

    Returns (jobs, total_raw_cards). total_raw_cards == 0 across every term
    means LinkedIn gave us no data at all — the callers' block guard.
    """
    jobs_by_id: dict[str, dict] = {}
    total_raw_cards = 0
    for term in terms:
        for start in range(0, 75, 25):
            time.sleep(REQUEST_DELAY)
            url = (
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
                f"?keywords={urllib.parse.quote(term)}"
                "&location=San%20Francisco%20Bay%20Area"
                "&geoId=90000084"
                f"&f_TPR=r{lookback_seconds}"
                f"&start={start}"
            )
            html = fetch(url)
            if not html.strip():
                break
            parsed, raw_count = _parse_linkedin_cards(html)
            total_raw_cards += raw_count
            # Break on a truly empty page, NOT on "no keyword matches" — a page
            # of 25 off-target roles must not end pagination for the term.
            if not raw_count:
                break
            for p in parsed:
                if p["id"] in jobs_by_id:
                    continue
                jobs_by_id[p["id"]] = {
                    "company": p["company"],
                    "title": p["title"],
                    "location": p["location"],
                    "url": f"https://www.linkedin.com/jobs/view/{p['id']}/",
                    "date_posted": p["date_posted"],
                    "ats": "LinkedIn",
                }

    jobs = list(jobs_by_id.values())
    jobs.sort(key=lambda j: -_iso_to_ts(j.get("date_posted", "")))
    return jobs, total_raw_cards


def scrape_linkedin_recent() -> list:
    print(f"🔎 Scraping LinkedIn (last {LINKEDIN_LOOKBACK_SECONDS // 3600}h)...")
    jobs, raw_cards = _linkedin_search(LINKEDIN_SEARCH_TERMS, LINKEDIN_LOOKBACK_SECONDS)
    # Block guard (mirrors Indeed's): zero raw cards across every term means
    # LinkedIn gave us nothing — rate-limited or blocked, not a quiet hour.
    # Reuse the previous results so we don't clobber the dedupe baseline.
    if raw_cards == 0:
        prev = _load_prev_jobs(os.path.join(SCRIPT_DIR, "linkedin_jobs.json"))
        print(f"  ⛔ LinkedIn returned 0 cards across all terms (likely blocked); "
              f"preserving previous {len(prev)} result(s)")
        return prev
    print(f"  ✅ LinkedIn: {len(jobs)} role(s)")
    _enrich_linkedin_salaries(jobs)
    return jobs


def scrape_linkedin_biotech() -> list:
    """
    Last 24h on LinkedIn, filtered to companies on the biotech allowlist.
    LinkedIn's f_I industry filter is silently ignored on the public guest
    endpoint, so we use general MLE/DS keywords + a company allowlist.
    """
    print(f"🧬 Scraping LinkedIn biotech allowlist (last {LINKEDIN_BIOTECH_LOOKBACK_SECONDS // 3600}h)...")
    raw, raw_cards = _linkedin_search(LINKEDIN_SEARCH_TERMS, LINKEDIN_BIOTECH_LOOKBACK_SECONDS)
    if raw_cards == 0:
        # Blocked run: contribute nothing rather than nuke the digest baseline;
        # the direct ATS probes in --biotech-only still supply fresh roles.
        print("  ⛔ LinkedIn returned 0 cards across all terms (likely blocked); "
              "skipping LinkedIn for this digest")
        return []
    jobs = [j for j in raw if _is_biotech_company(j["company"])]
    print(f"  ✅ Biotech LinkedIn: {len(jobs)} role(s) (from {len(raw)} total)")
    return jobs


# ---------------------------------------------------------------------------
# Indeed — via python-jobspy (Indeed's RSS feeds + Publisher API were both
# deprecated in 2026, and indeed.com sits behind Cloudflare top-tier bot
# protection. JobSpy uses Indeed's mobile-app API internally — no proxies
# required, no documented rate limit.)
# ---------------------------------------------------------------------------

INDEED_LOOKBACK_HOURS = 24  # Indeed posting dates are ~day-resolution, so a 1h window
# returns almost nothing; the hourly watcher's cross-run dedupe trims the overlap.

# jobspy returns the full JD (markdown) for Indeed rows. We keep a trimmed copy
# in indeed_jobs.json (bounded: 24h window) so the nightly triage agent can
# judge Indeed roles from the actual description instead of the title alone.
# _merge_into_all_jobs strips it so the dashboard's master stays lean.
INDEED_JD_MAX_CHARS = 6000


def scrape_indeed_recent() -> list:
    """Indeed MLE/DS roles posted in the last INDEED_LOOKBACK_HOURS, SF Bay Area."""
    print(f"🟦 Scraping Indeed (last {INDEED_LOOKBACK_HOURS}h)...")
    try:
        from jobspy import scrape_jobs as jobspy_scrape
    except ImportError:
        print("  ⚠️  python-jobspy not installed; skipping Indeed")
        return []

    jobs_by_id: dict[str, dict] = {}
    ok_terms = 0
    errored_terms = 0
    raw_rows = 0
    for term in LINKEDIN_SEARCH_TERMS:
        time.sleep(REQUEST_DELAY)  # throttle: 20 back-to-back calls invite blocking on CI IPs
        try:
            # JobSpy Indeed gotcha: hours_old / is_remote / job_type / easy_apply
            # are mutually exclusive — only one may be set, or the time filter
            # silently breaks. Keep hours_old; do not add the others.
            df = jobspy_scrape(
                site_name=["indeed"],
                search_term=term,
                location="San Francisco, CA",
                distance=50,
                results_wanted=50,
                hours_old=INDEED_LOOKBACK_HOURS,
                country_indeed="USA",
            )
        except Exception as e:
            errored_terms += 1
            print(f"  ⚠️  Indeed ({term!r}): {e}")
            continue
        ok_terms += 1
        if df is None or df.empty:
            continue
        raw_rows += len(df)
        df.columns = [c.lower() for c in df.columns]
        df = df.fillna("")
        for _, row in df.iterrows():
            title = str(row.get("title", "") or "")
            if not is_mle_role(title):
                continue
            url = str(row.get("job_url", "") or "")
            ident = _job_identity(url)
            if ident in jobs_by_id:
                continue
            loc = str(row.get("location", "") or "")
            if not loc:
                city = str(row.get("city", "") or "")
                state = str(row.get("state", "") or "")
                loc = ", ".join(p for p in [city, state] if p)
            jobs_by_id[ident] = {
                "company": str(row.get("company", "") or "Unknown"),
                "title": title,
                "location": loc,
                "url": url,
                "date_posted": str(row.get("date_posted", "") or ""),
                "description": str(row.get("description", "") or "")[:INDEED_JD_MAX_CHARS],
                "salary": format_salary(
                    row.get("min_amount", ""),
                    row.get("max_amount", ""),
                    row.get("interval", ""),
                ),
                "ats": "Indeed",
            }
    jobs = list(jobs_by_id.values())
    print(
        f"  📊 Indeed: {len(LINKEDIN_SEARCH_TERMS)} terms → "
        f"{ok_terms} ok / {errored_terms} errored · {raw_rows} raw, {len(jobs)} matched"
    )

    # Block guard: zero rows pulled across every term means Indeed gave us no data
    # — a hard block (calls raised) or a soft block (empty frames). This is NOT the
    # same as "rows returned but none matched our keywords" (raw_rows > 0, jobs == []),
    # which is a legitimate empty result. On a no-data run, reuse the previous results
    # so we don't clobber the dedupe baseline (and the dashboard's Indeed column) with
    # an empty file; save_indeed_results() then reports 0 new (all already seen).
    if raw_rows == 0:
        prev = _load_prev_jobs(os.path.join(SCRIPT_DIR, "indeed_jobs.json"))
        print(
            f"  ⛔ Indeed returned 0 rows across all terms (likely blocked); "
            f"preserving previous {len(prev)} result(s)"
        )
        return prev

    return jobs


def format_salary(min_amount, max_amount, interval) -> str:
    """
    Display string for jobspy's Indeed pay fields, e.g. "$150k–$190k/yr" or
    "$62.50/hr". Returns "" when neither bound is present.
    """
    def _num(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if f > 0 else None

    def _fmt(n):
        if n >= 10000:
            return f"${round(n / 1000)}k"
        if n == int(n):
            return f"${int(n)}"
        return f"${n:.2f}"

    lo, hi = _num(min_amount), _num(max_amount)
    if lo is None and hi is None:
        return ""
    suffix = {"yearly": "/yr", "hourly": "/hr", "monthly": "/mo",
              "weekly": "/wk", "daily": "/day"}.get(str(interval or "").lower(), "")
    if lo is not None and hi is not None and lo != hi:
        return f"{_fmt(lo)}–{_fmt(hi)}{suffix}"
    return f"{_fmt(lo if lo is not None else hi)}{suffix}"


def _iso_to_ts(iso: str) -> float:
    if not iso:
        return 0.0
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return 0.0


def _job_identity(url: str) -> str:
    """
    Stable identity string for a posting URL, used to dedupe across runs.

    LinkedIn → numeric posting ID (LinkedIn appends tracking params that vary
    run-to-run). Indeed → the `jk=` token (Indeed appends `indpubnum` and other
    tracking that varies). Other ATS (Greenhouse, Workday, Phenom) → URL with
    query string and trailing slash stripped.
    """
    if not url:
        return ""
    m = re.search(r'/jobs/view/(\d+)', url)
    if m:
        return f"linkedin:{m.group(1)}"
    m = re.search(r'[?&]jk=([a-zA-Z0-9]+)', url)
    if m:
        return f"indeed:{m.group(1)}"
    return url.split("?")[0].rstrip("/")


def _load_prev_jobs(json_path: str) -> list[dict]:
    """Read the `jobs` list from a previously-saved jobs JSON (empty if missing)."""
    try:
        with open(json_path) as f:
            return json.load(f).get("jobs", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _load_prev_ids(json_path: str) -> set[str]:
    """Read previously-saved jobs JSON and return the set of job identities."""
    ids = set()
    for j in _load_prev_jobs(json_path):
        i = _job_identity(j.get("url", ""))
        if i:
            ids.add(i)
    return ids


ALL_JOBS_PRUNE_DAYS = 14


def _merge_into_all_jobs(new_jobs: list) -> int:
    """
    Maintain all_jobs.json — a cumulative, URL-deduped master of every role the
    scrapers surface, each stamped with first_seen. The per-source JSONs are
    rolling windows that overwrite every run (LinkedIn keeps only ~1h), so this
    master is what the triage agent and the dashboard's Rank tab read to see
    everything from the last ALL_JOBS_PRUNE_DAYS days. Returns count added.
    """
    path = os.path.join(SCRIPT_DIR, "all_jobs.json")
    try:
        with open(path) as f:
            master = json.load(f).get("jobs", [])
    except (FileNotFoundError, json.JSONDecodeError):
        master = []

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    by_url = {j.get("url"): j for j in master if j.get("url")}
    added = 0
    for j in new_jobs:
        url = j.get("url")
        if url and url not in by_url:  # first writer wins on first_seen
            # Drop the JD text: the dashboard fetches this whole file on every
            # load; the triage agent reads descriptions from indeed_jobs.json.
            entry = {k: v for k, v in j.items() if k != "description"}
            entry["first_seen"] = stamp
            by_url[url] = entry
            added += 1

    cutoff = (now - timedelta(days=ALL_JOBS_PRUNE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    kept = [j for j in by_url.values() if j.get("first_seen", stamp) >= cutoff]
    kept.sort(key=lambda j: j.get("first_seen", ""), reverse=True)

    with open(path, "w") as f:
        # Compact separators: the dashboard downloads this file on every load.
        json.dump({"updated_at": now.strftime("%Y-%m-%d %H:%M UTC"), "jobs": kept},
                  f, separators=(",", ":"))
    print(f"🗂  all_jobs.json: +{added} new, {len(kept)} total (last {ALL_JOBS_PRUNE_DAYS}d)")
    return added


def save_jobs_output(jobs: list, *, basename: str, title: str, subtitle: str,
                     accent: str, empty_message: str, window_label: str):
    """
    Save jobs to {basename}.{json,md,html}. Dedupes against the previous JSON at
    the same path so each email surfaces only postings new to this run.
    """
    json_path = os.path.join(SCRIPT_DIR, f"{basename}.json")
    md_path = os.path.join(SCRIPT_DIR, f"{basename}.md")
    html_path = os.path.join(SCRIPT_DIR, f"{basename}.html")

    prev_ids = _load_prev_ids(json_path)
    new_jobs = [j for j in jobs if _job_identity(j.get("url", "")) not in prev_ids]

    # Accumulate into the cumulative master. Guarded: a bug here must never
    # break the scrape/commit path that the digests and dashboard depend on.
    try:
        _merge_into_all_jobs(new_jobs)
    except Exception as e:
        print(f"  ⚠️  all_jobs.json accumulator failed (non-fatal): {e}")

    # Push new roles to Pushover (no-op without PUSHOVER_TOKEN/USER env vars).
    try:
        import notify
        notify.notify_new_jobs(new_jobs)
    except Exception as e:
        print(f"  ⚠️  Pushover notify failed (non-fatal): {e}")

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    output = {
        "scraped_at": timestamp,
        "total": len(jobs),
        "new_count": len(new_jobs),
        "jobs": jobs,
        "new_jobs": new_jobs,
    }
    with open(json_path, "w") as f:
        json.dump(output, f, indent=2)

    lines = [
        f"# {title}",
        f"*Last updated: {timestamp}*\n",
        f"**{len(new_jobs)} new role(s)** since last run · {len(jobs)} total in {window_label}\n",
    ]
    if not new_jobs:
        lines.append(empty_message)
    else:
        for job in new_jobs:
            lines.append(f"### [{job['title']}]({job['url']}) — {job['company']}")
            lines.append(f"- 📍 **Location:** {job['location'] or 'Not specified'}")
            if job.get("salary"):
                lines.append(f"- 💰 **Salary:** {job['salary']}")
            if job.get("date_posted"):
                lines.append(f"- 🕒 **Posted:** {job['date_posted']}")
            lines.append("")
    with open(md_path, "w") as f:
        f.write("\n".join(lines))

    with open(html_path, "w") as f:
        f.write(_render_jobs_html(
            title=title,
            subtitle=subtitle,
            timestamp=timestamp,
            jobs=new_jobs,
            empty_message=empty_message,
            accent=accent,
        ))
    print(f"📄 Saved {basename}.json/.md/.html ({len(new_jobs)} new of {len(jobs)} total)")


def save_linkedin_results(jobs: list):
    save_jobs_output(
        jobs,
        basename="linkedin_jobs",
        title="🔥 LinkedIn — Engineering / ML / DS Roles (SF Bay Area)",
        subtitle=f"SF Bay Area · last {LINKEDIN_LOOKBACK_SECONDS // 3600}h",
        accent="#3b82f6",
        empty_message="No new roles since the last run.",
        window_label=f"last {LINKEDIN_LOOKBACK_SECONDS // 3600}h",
    )


def save_indeed_results(jobs: list):
    save_jobs_output(
        jobs,
        basename="indeed_jobs",
        title="🟦 Indeed — Engineering / ML / DS Roles (SF Bay Area)",
        subtitle=f"SF Bay Area · last {INDEED_LOOKBACK_HOURS}h",
        accent="#2557a7",
        empty_message="No new roles since the last run.",
        window_label=f"last {INDEED_LOOKBACK_HOURS}h",
    )


def save_biotech_linkedin_results(jobs: list):
    save_jobs_output(
        jobs,
        basename="jobs",
        title="🧬 Biotech LinkedIn — MLE / DS Roles",
        subtitle=f"SF Bay Area biotech allowlist · last {LINKEDIN_BIOTECH_LOOKBACK_SECONDS // 3600}h",
        accent="#2ea04f",
        empty_message="No new biotech roles since the last run.",
        window_label=f"last {LINKEDIN_BIOTECH_LOOKBACK_SECONDS // 3600}h",
    )


def _render_jobs_html(*, title: str, subtitle: str, timestamp: str,
                      jobs: list, empty_message: str, accent: str) -> str:
    import html as html_mod

    if not jobs:
        body = f'<div class="empty">{html_mod.escape(empty_message)}</div>'
    else:
        cards = []
        for j in jobs:
            salary = (
                f'<span class="meta-item">💰 {html_mod.escape(j["salary"])}</span>'
                if j.get("salary") else ""
            )
            posted = (
                f'<span class="meta-item">🕒 Posted {html_mod.escape(j["date_posted"])}</span>'
                if j.get("date_posted") else ""
            )
            ats_tag = (
                f'<span class="ats">{html_mod.escape(j["ats"])}</span>'
                if j.get("ats") else ""
            )
            cards.append(
                f'<div class="job">'
                f'<div class="title"><a href="{html_mod.escape(j["url"])}">'
                f'{html_mod.escape(j["title"])}</a></div>'
                f'<div class="company">{html_mod.escape(j["company"])} {ats_tag}</div>'
                f'<div class="meta">'
                f'<span class="meta-item">📍 {html_mod.escape(j["location"] or "Not specified")}</span>'
                f'{salary}'
                f'{posted}'
                f'</div></div>'
            )
        body = "\n".join(cards)

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  max-width: 720px; margin: 24px auto; padding: 0 16px; color: #1a1a1a; background: #fff; line-height: 1.5; }}
h1 {{ font-size: 22px; margin: 0 0 4px 0; }}
.subtitle {{ color: #666; font-size: 14px; margin-bottom: 16px; }}
.summary {{ background: #f4f6fb; padding: 12px 16px; border-left: 4px solid {accent};
  margin: 16px 0; border-radius: 4px; font-size: 14px; }}
.summary strong {{ font-size: 18px; color: {accent}; }}
.job {{ background: #fafafa; border: 1px solid #e8e8e8; border-radius: 8px;
  padding: 14px 18px; margin-bottom: 10px; }}
.title {{ font-size: 16px; font-weight: 600; margin-bottom: 4px; }}
.title a {{ color: #0a66c2; text-decoration: none; }}
.title a:hover {{ text-decoration: underline; }}
.company {{ color: #444; font-weight: 500; margin-bottom: 8px; font-size: 14px; }}
.ats {{ display: inline-block; background: #eaf3fb; color: #0a66c2; font-size: 11px;
  padding: 1px 8px; border-radius: 10px; font-weight: 500; margin-left: 6px; vertical-align: middle; }}
.meta {{ font-size: 13px; color: #666; }}
.meta-item {{ margin-right: 14px; }}
.empty {{ color: #999; font-style: italic; padding: 28px; text-align: center;
  background: #fafafa; border-radius: 8px; border: 1px dashed #ddd; }}
.foot {{ margin-top: 28px; padding-top: 12px; border-top: 1px solid #eee;
  color: #888; font-size: 12px; text-align: center; }}
.foot a {{ color: #0a66c2; }}
</style></head>
<body>
<h1>{title}</h1>
<div class="subtitle">{subtitle}</div>
<div class="summary"><strong>{len(jobs)}</strong> role(s) &nbsp;·&nbsp; scraped {timestamp}</div>
{body}
<div class="foot">Auto-generated by <a href="https://github.com/ernestod1998/Job_Scraper">Job_Scraper</a></div>
</body></html>"""


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def save_results(jobs: list):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    output = {"scraped_at": timestamp, "total": len(jobs), "jobs": jobs}
    with open(os.path.join(SCRIPT_DIR, "jobs.json"), "w") as f:
        json.dump(output, f, indent=2)

    lines = [
        "# 🧬 Fresh Biotech MLE Job Listings (SF Bay Area)",
        f"*Last updated: {timestamp}*\n",
        f"**{len(jobs)} role(s) posted in the last 24 hours**\n",
    ]

    for company in sorted(set(j["company"] for j in jobs)):
        company_jobs = [j for j in jobs if j["company"] == company]
        lines.append(f"## {company} ({len(company_jobs)} role(s))\n")
        for job in company_jobs:
            lines.append(f"### [{job['title']}]({job['url']})")
            lines.append(f"- 📍 **Location:** {job['location'] or 'Not specified'}")
            if job.get("date_posted"):
                lines.append(f"- 📅 **Posted:** {job['date_posted']}")
            lines.append("")

    with open(os.path.join(SCRIPT_DIR, "jobs.md"), "w") as f:
        f.write("\n".join(lines))

    with open(os.path.join(SCRIPT_DIR, "jobs.html"), "w") as f:
        f.write(_render_jobs_html(
            title="🧬 Fresh Biotech MLE Job Listings",
            subtitle="SF Bay Area · posted in the last 24 hours",
            timestamp=timestamp,
            jobs=jobs,
            empty_message="No biotech roles posted in the last 24 hours.",
            accent="#2ea04f",
        ))

    print(f"\n📄 Saved jobs.json/.md/.html ({len(jobs)} total roles)")


# ===========================================================================
# Salary backfill + extra sources (USAJOBS / GovernmentJobs / CalCareers /
# CalOpps). These reuse the repo's existing keyword gate (is_mle_role) and
# location predicate (is_bay_area), so they follow whatever KEYWORDS /
# BAY_AREA_LOCATIONS the maintainer sets — no domain-specific terms are
# hardcoded here. Heavier per-term sources share GOV_SEARCH_TERMS (a slice of
# the LinkedIn list) to keep request counts sane; widen it if you like.
# ===========================================================================

GOV_SEARCH_TERMS = LINKEDIN_SEARCH_TERMS[:8]


# ---- LinkedIn salary backfill ---------------------------------------------
# LinkedIn search-result cards omit pay, but the public guest *posting* page
# carries a `compensation__salary` block when the employer provided it. Fetch
# it only for jobs still missing salary, capped per run to bound runtime.
LINKEDIN_SALARY_FETCH_CAP = 120


def _linkedin_posting_salary(job_id: str) -> str:
    import html as html_mod
    page = fetch(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}")
    if not page:
        return ""
    anchor = re.search(r'compensation__salary', page)
    if not anchor:
        return ""
    window = page[anchor.start():anchor.start() + 400]
    amt = re.search(r'\$[\d][^<]{0,60}', window)
    return re.sub(r'\s+', ' ', html_mod.unescape(amt.group(0))).strip() if amt else ""


def _enrich_linkedin_salaries(jobs: list) -> int:
    """Backfill salary on LinkedIn jobs from their posting pages. Bounded by
    LINKEDIN_SALARY_FETCH_CAP; never raises."""
    filled = fetched = 0
    for job in jobs:
        if fetched >= LINKEDIN_SALARY_FETCH_CAP:
            break
        if job.get("salary") or job.get("ats") != "LinkedIn":
            continue
        m = re.search(r'/jobs/view/(\d+)', job.get("url", ""))
        if not m:
            continue
        time.sleep(REQUEST_DELAY)
        fetched += 1
        try:
            sal = _linkedin_posting_salary(m.group(1))
        except (URLError, TimeoutError, OSError):
            continue
        if sal:
            job["salary"] = sal
            filled += 1
    if fetched:
        print(f"  💰 LinkedIn salary backfill: {filled}/{fetched} posting(s) had pay")
    return filled


# ---- Shared cookie-jar opener (for ASP.NET session sources) ---------------
def _session_opener():
    jar = http.cookiejar.CookieJar()
    return build_opener(HTTPCookieProcessor(jar))


def _hidden_inputs(html: str) -> dict:
    """All <input type=hidden> name/value pairs (ASP.NET viewstate etc.)."""
    fields = {}
    for tag in re.findall(r'<input\b[^>]*type=["\']hidden["\'][^>]*>', html, re.I):
        n = re.search(r'\bname=["\']([^"\']+)["\']', tag)
        v = re.search(r'\bvalue=["\']([^"\']*)["\']', tag)
        if n:
            fields[n.group(1)] = (v.group(1) if v else "")
    return fields


# ---- USAJOBS — federal jobs (no API key) ----------------------------------
USAJOBS_RESULTS_URL = "https://www.usajobs.gov/Search/Results?hp=public&s=startdate&sd=desc&p=1"
USAJOBS_SEARCH_URL = "https://www.usajobs.gov/Search/ExecuteSearch"
USAJOBS_RESULTS_PER_PAGE = 50


def _usajobs_date(date_display: str) -> str:
    m = re.search(r'(\d{2})/(\d{2})/(\d{4})', date_display or "")
    return f"{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else ""


def scrape_usajobs_recent() -> list:
    """Federal roles from usajobs.gov via the public website search (no API key).
    Seeds a session on the Results page, then POSTs each keyword to
    /Search/ExecuteSearch and keeps titles passing is_mle_role(). Returns salary."""
    print("🇺🇸 Scraping USAJOBS (federal jobs)...")
    jobs_by_url: dict[str, dict] = {}
    headers = {
        **HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://www.usajobs.gov",
        "Referer": USAJOBS_RESULTS_URL,
    }
    try:
        opener = _session_opener()
        opener.open(Request(USAJOBS_RESULTS_URL, headers=HEADERS), timeout=25).read()
        for term in GOV_SEARCH_TERMS:
            time.sleep(REQUEST_DELAY)
            body = json.dumps({
                "Keyword": term, "HiringPath": ["public"],
                "SortField": "startdate", "SortDirection": "desc",
                "Page": "1", "ResultsPerPage": USAJOBS_RESULTS_PER_PAGE,
            }).encode()
            try:
                payload = json.loads(opener.open(
                    Request(USAJOBS_SEARCH_URL, data=body, headers=headers),
                    timeout=25).read().decode("utf-8", "ignore"))
            except (URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
                print(f"  ⚠️  USAJOBS ({term!r}): {e}")
                continue
            for job in payload.get("Jobs", []):
                title = (job.get("Title") or "").strip()
                if not is_mle_role(title):
                    continue
                uri = (job.get("PositionURI") or "").replace(":443", "")
                if not uri and job.get("DocumentID"):
                    uri = f"https://www.usajobs.gov/job/{job['DocumentID']}"
                if not uri or uri in jobs_by_url:
                    continue
                jobs_by_url[uri] = {
                    "company": (job.get("Agency") or job.get("Department") or "Federal Government").strip(),
                    "title": title,
                    "location": (job.get("LocationName") or "").strip(),
                    "url": uri,
                    "date_posted": _usajobs_date(job.get("DateDisplay", "")),
                    "salary": (job.get("SalaryDisplay") or "").strip(),
                    "ats": "USAJOBS",
                }
    except (URLError, TimeoutError, OSError, ValueError) as e:
        print(f"  ⛔ USAJOBS unreachable ({e}); preserving previous results")
        return _load_prev_jobs(os.path.join(SCRIPT_DIR, "usajobs_jobs.json"))
    jobs = list(jobs_by_url.values())
    print(f"  ✅ USAJOBS: {len(jobs)} federal role(s)")
    return jobs if jobs else _load_prev_jobs(os.path.join(SCRIPT_DIR, "usajobs_jobs.json"))


def save_usajobs_results(jobs: list):
    save_jobs_output(
        jobs, basename="usajobs_jobs",
        title="🇺🇸 USAJOBS — Federal Roles",
        subtitle="usajobs.gov · federal agencies",
        accent="#1d4ed8",
        empty_message="No new federal roles since the last run.",
        window_label="current USAJOBS postings",
    )


# ---- GovernmentJobs.com / NEOGOV — state & local government ----------------
GOVERNMENTJOBS_BASE = "https://www.governmentjobs.com"
GOVERNMENTJOBS_DAYS = 21
GOVERNMENTJOBS_PAGES = 2


def scrape_governmentjobs_recent() -> list:
    """State/local-government roles via governmentjobs.com, filtered to the
    repo's locations with is_bay_area()."""
    print("🏛  Scraping GovernmentJobs/NEOGOV (state & local gov)...")
    import html as html_mod
    item_re = re.compile(r'<li[^>]*class=["\'][^"\']*\bjob-item\b[^"\']*["\'][^>]*>([\s\S]*?)</li>', re.I)
    link_re = re.compile(r'<a[^>]*class=["\'][^"\']*\bjob-details-link\b[^"\']*["\'][^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>', re.I)
    org_re = re.compile(r'<div[^>]*class=["\'][^"\']*\bjob-organization\b[^"\']*["\'][^>]*>([\s\S]*?)</div>', re.I)
    loc_re = re.compile(r'<span[^>]*class=["\'][^"\']*\bjob-location\b[^"\']*["\'][^>]*>([\s\S]*?)</span>', re.I)

    def _clean(s):
        return re.sub(r'\s+', ' ', html_mod.unescape(re.sub(r'<[^>]+>', ' ', s or ''))).strip()

    jobs_by_url: dict[str, dict] = {}
    raw_items = 0
    for term in GOV_SEARCH_TERMS:
        for page in range(1, GOVERNMENTJOBS_PAGES + 1):
            time.sleep(REQUEST_DELAY)
            url = (f"{GOVERNMENTJOBS_BASE}/jobs?keyword={urllib.parse.quote(term)}"
                   f"&daysposted={GOVERNMENTJOBS_DAYS}&isFiltered=true&page={page}")
            items = item_re.findall(fetch(url))
            raw_items += len(items)
            if not items:
                break
            for it in items:
                lk = link_re.search(it)
                if not lk:
                    continue
                title = _clean(lk.group(2))
                if not is_mle_role(title):
                    continue
                loc_m = loc_re.search(it)
                location = _clean(loc_m.group(1)) if loc_m else ""
                if not is_bay_area(location):
                    continue
                href = re.sub(r'\s+', '', lk.group(1))
                job_url = href if href.startswith("http") else GOVERNMENTJOBS_BASE + "/" + href.lstrip("/")
                if job_url in jobs_by_url:
                    continue
                org_m = org_re.search(it)
                sal_m = re.search(
                    r'\$[\d,]+(?:\.\d{2})?\s*-\s*\$[\d,]+(?:\.\d{2})?'
                    r'\s*(?:Annually|Monthly|Hourly|Biweekly|Bi-Weekly|Weekly|Daily)?',
                    _clean(it), re.I)
                jobs_by_url[job_url] = {
                    "company": _clean(org_m.group(1)) if org_m else "Government Agency",
                    "title": title, "location": location, "url": job_url,
                    "date_posted": "",
                    "salary": sal_m.group(0).strip() if sal_m else "",
                    "ats": "NEOGOV",
                }
    jobs = list(jobs_by_url.values())
    print(f"  ✅ NEOGOV: {len(jobs)} role(s) (from {raw_items} scanned)")
    if not jobs and raw_items == 0:
        return _load_prev_jobs(os.path.join(SCRIPT_DIR, "governmentjobs_jobs.json"))
    return jobs


def save_governmentjobs_results(jobs: list):
    save_jobs_output(
        jobs, basename="governmentjobs_jobs",
        title="🏛 NEOGOV — State & Local Government Roles",
        subtitle="governmentjobs.com",
        accent="#0e7490",
        empty_message="No new state/local-gov roles since the last run.",
        window_label="recent GovernmentJobs postings",
    )


# ---- CalOpps — California local agencies -----------------------------------
CALOPPS_LIST_URL = "https://www.calopps.org/job-search-list"
CALOPPS_MAX_PAGES = 10


def _calopps_company(href: str) -> str:
    m = re.match(r'/?([^/]+)/', href or "")
    return m.group(1).replace('-', ' ').title() if m else "California Agency"


def scrape_calopps_recent() -> list:
    """California local-agency roles from calopps.org (CA-only board)."""
    print("🏛  Scraping CalOpps (California local agencies)...")
    import html as html_mod
    row_re = re.compile(r'<tr[^>]*>([\s\S]*?)</tr>', re.I)
    cell_re = re.compile(r'<td[^>]*>([\s\S]*?)</td>', re.I)
    link_re = re.compile(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>', re.I)

    def _clean(s):
        return re.sub(r'\s+', ' ', html_mod.unescape(re.sub(r'<[^>]+>', ' ', s or ''))).strip()

    jobs_by_url: dict[str, dict] = {}
    scanned = 0
    for page in range(CALOPPS_MAX_PAGES):
        time.sleep(REQUEST_DELAY)
        url = CALOPPS_LIST_URL + (f"?page={page}" if page else "")
        rows = [r for r in row_re.findall(fetch(url)) if "views-field-label" in r.lower()]
        if not rows:
            break
        for r in rows:
            cells = cell_re.findall(r)
            if len(cells) < 5:
                continue
            lk = link_re.search(cells[0])
            if not lk:
                continue
            scanned += 1
            title = _clean(lk.group(2))
            if not is_mle_role(title):
                continue
            href = html_mod.unescape(lk.group(1).strip())
            job_url = href if href.startswith("http") else "https://www.calopps.org" + ("" if href.startswith("/") else "/") + href
            if job_url in jobs_by_url:
                continue
            jobs_by_url[job_url] = {
                "company": _calopps_company(href), "title": title,
                "location": _clean(cells[1]) or "California", "url": job_url,
                "date_posted": "", "salary": "", "ats": "CalOpps",
            }
    jobs = list(jobs_by_url.values())
    for job in jobs:  # salary is on the posting page (few matches → cheap)
        time.sleep(REQUEST_DELAY)
        try:
            ph = fetch(job["url"])
        except (URLError, TimeoutError, OSError):
            continue
        sm = re.search(
            r'Salary\s*(\$[\d,]+(?:\.\d{2})?\s*-\s*\$[\d,]+(?:\.\d{2})?'
            r'\s*(?:Monthly|Annually|Hourly|Biweekly|Bi-Weekly|Weekly|Daily)?)',
            re.sub(r'<[^>]+>', ' ', ph), re.I)
        if sm:
            job["salary"] = re.sub(r'\s+', ' ', sm.group(1)).strip()
    print(f"  ✅ CalOpps: {len(jobs)} role(s) (from {scanned} scanned)")
    if not jobs and scanned == 0:
        return _load_prev_jobs(os.path.join(SCRIPT_DIR, "calopps_jobs.json"))
    return jobs


def save_calopps_results(jobs: list):
    save_jobs_output(
        jobs, basename="calopps_jobs",
        title="🏛 CalOpps — California Local-Agency Roles",
        subtitle="calopps.org · CA cities, counties, special districts",
        accent="#15803d",
        empty_message="No new CalOpps roles since the last run.",
        window_label="recent CalOpps postings",
    )


# ---- CalCareers — California state civil service ---------------------------
CALCAREERS_SEARCH_URL = "https://calcareers.ca.gov/CalHRPublic/Search/JobSearchResults.aspx"
CALCAREERS_TIMEOUT = 30
CALCAREERS_CARD_RE = re.compile(
    r'Working Title:\s*</div>\s*<div class="col-xs-6 job-details">\s*<span[^>]*>(.*?)</span>'
    r'[\s\S]*?Job Control:\s*</div>\s*<div class="col-xs-6 job-details">\s*(\d+)\s*</div>'
    r'[\s\S]*?Department:\s*</div>\s*<div class="col-xs-6 job-details">\s*(.*?)\s*</div>'
    r'[\s\S]*?Location:\s*</div>\s*<div class="col-xs-6 job-details">\s*(.*?)\s*</div>'
    r'[\s\S]*?Publish Date:\s*</div>\s*<div class="col-xs-6 job-details">\s*<time[^>]*>\s*([^<]+)\s*</time>'
    r'[\s\S]*?href="(https://www\.calcareers\.ca\.gov/CalHrPublic/Jobs/JobPosting\.aspx\?JobControlId=\d+)"',
    re.I,
)


def _parse_calcareers_results(html: str) -> list[dict]:
    import html as html_mod

    def _clean(s):
        return re.sub(r'\s+', ' ', html_mod.unescape(re.sub(r'<[^>]+>', ' ', s or ''))).strip()

    jobs: list[dict] = []
    for m in CALCAREERS_CARD_RE.finditer(html):
        title, _jc, dept, location, pub_date, url = m.groups()
        date = ""
        dm = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', pub_date or "")
        if dm:
            date = f"{dm.group(3)}-{int(dm.group(1)):02d}-{int(dm.group(2)):02d}"
        card = html[m.start():m.end()]
        sal_m = re.search(r'Salary Range:\s*</div>\s*<div[^>]*>([\s\S]*?)</div>', card, re.I)
        salary = ""
        if sal_m:
            sm = re.search(
                r'\$[\d,]+(?:\.\d{2})?\s*-\s*\$[\d,]+(?:\.\d{2})?(?:\s*(?:per|/)\s*\w+)?',
                _clean(sal_m.group(1)))
            salary = sm.group(0).strip() if sm else ""
        jobs.append({
            "company": _clean(dept) or "State of California",
            "title": _clean(title), "location": _clean(location) or "California",
            "url": _clean(url), "date_posted": date, "salary": salary,
            "ats": "CalCareers",
        })
    return jobs


def _calcareers_payload(hidden: dict, event_target: str, keyword: str) -> dict:
    payload = dict(hidden)
    payload["__EVENTTARGET"] = event_target
    payload["__EVENTARGUMENT"] = ""
    payload["ctl00$cphMainContent$txtKeyword"] = keyword
    payload["ctl00$cphMainContent$hdnInit"] = "true"
    payload.setdefault("ctl00$cphMainContent$chkExactWordMatch", "")
    payload.setdefault("ctl00$hdnShowHeaderPadding", "1")
    payload.setdefault("ctl00$ucSessionTimeoutDialog$tmrCountdown", "1200")
    return payload


def scrape_calcareers_recent() -> list:
    """California state civil-service roles via the ASP.NET search postback.
    Fires the search with __EVENTTARGET=btnSearch + the keyword field, then
    parses the labeled result cards. Guarded — returns previous on any failure."""
    print("🏛  Scraping CalCareers (California state jobs)...")
    headers = {
        **HEADERS,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": CALCAREERS_SEARCH_URL,
    }
    jobs_by_url: dict[str, dict] = {}
    parsed_total = 0
    reached = False
    for term in GOV_SEARCH_TERMS:
        time.sleep(REQUEST_DELAY)
        try:
            opener = _session_opener()  # fresh session/viewstate per keyword
            seed = opener.open(Request(CALCAREERS_SEARCH_URL, headers=HEADERS),
                               timeout=CALCAREERS_TIMEOUT).read().decode("utf-8", "ignore")
            reached = True
            hidden = _hidden_inputs(seed)
            if not hidden:
                continue
            data = urllib.parse.urlencode(
                _calcareers_payload(hidden, "ctl00$cphMainContent$btnSearch", term)).encode()
            res_html = opener.open(Request(CALCAREERS_SEARCH_URL, data=data, headers=headers),
                                   timeout=CALCAREERS_TIMEOUT).read().decode("utf-8", "ignore")
        except (URLError, TimeoutError, OSError) as e:
            print(f"  ⚠️  CalCareers ({term!r}): {e}")
            continue
        for job in _parse_calcareers_results(res_html):
            parsed_total += 1
            if is_mle_role(job["title"]) and job["url"] not in jobs_by_url:
                jobs_by_url[job["url"]] = job
    jobs = list(jobs_by_url.values())
    print(f"  ✅ CalCareers: {len(jobs)} on-target role(s) (from {parsed_total} parsed)")
    if not jobs and (parsed_total == 0 or not reached):
        return _load_prev_jobs(os.path.join(SCRIPT_DIR, "calcareers_jobs.json"))
    return jobs


def save_calcareers_results(jobs: list):
    save_jobs_output(
        jobs, basename="calcareers_jobs",
        title="🏛 CalCareers — California State Roles",
        subtitle="calcareers.ca.gov · California state civil service",
        accent="#b45309",
        empty_message="No new CalCareers roles since the last run.",
        window_label="current CalCareers postings",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--indeed-only" in sys.argv:
        save_indeed_results(scrape_indeed_recent())
        sys.exit(0)

    if "--linkedin-only" in sys.argv:
        save_linkedin_results(scrape_linkedin_recent())
        sys.exit(0)

    if "--usajobs-only" in sys.argv:
        save_usajobs_results(scrape_usajobs_recent())
        sys.exit(0)

    if "--governmentjobs-only" in sys.argv:
        save_governmentjobs_results(scrape_governmentjobs_recent())
        sys.exit(0)

    if "--calopps-only" in sys.argv:
        save_calopps_results(scrape_calopps_recent())
        sys.exit(0)

    if "--calcareers-only" in sys.argv:
        save_calcareers_results(scrape_calcareers_recent())
        sys.exit(0)

    if "--biotech-only" in sys.argv:
        # Direct ATS gives a stable baseline (LinkedIn's 24h endpoint has been
        # flaky on GH Actions runners — see workflow_runs.jsonl). LinkedIn is
        # kept as a supplemental source for biotechs not in CURATED_BIOTECHS.
        # Cross-run dedupe via _load_prev_ids → save_biotech_linkedin_results
        # provides "new since last digest" semantics, so we skip the 24h
        # freshness filter (ATS updated_at is unreliable for that anyway).
        jobs = list(scrape_genentech())
        jobs.extend(scrape_curated_biotechs())
        jobs = [j for j in jobs if is_bay_area(j.get("location", ""))]
        jobs.extend(scrape_linkedin_biotech())

        seen: set[tuple[str, str]] = set()
        deduped: list[dict] = []
        for j in jobs:
            key = (j["company"].strip().lower(), j["title"].strip().lower())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(j)
        print(f"\n🧬 Combined biotech total: {len(deduped)} unique role(s) "
              f"(from {len(jobs)} across sources)")

        save_biotech_linkedin_results(deduped)
        sys.exit(0)

    # Legacy default: curated Greenhouse/Workday/Phenom sweep. Returned 0 roles
    # consistently because ATS updated_at dates rarely fall inside the 24h window.
    # CI now uses --biotech-only; this branch is kept for ad-hoc local runs.
    all_jobs = list(scrape_genentech())
    all_jobs.extend(scrape_curated_biotechs())

    before = len(all_jobs)
    all_jobs = [j for j in all_jobs if is_bay_area(j.get("location", ""))]
    print(f"\n📍 Bay Area filter: {before} → {len(all_jobs)} roles")

    before = len(all_jobs)
    all_jobs = [j for j in all_jobs if is_recent_posting(j)]
    print(f"🕒 Freshness filter (last 24h): {before} → {len(all_jobs)} roles")

    save_results(all_jobs)
