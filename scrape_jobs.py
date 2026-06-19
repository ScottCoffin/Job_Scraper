"""
Environmental / Toxicology Job Scraper — tailored for Dr. Scott Coffin
(environmental toxicology, risk assessment, exposure science, water quality,
microplastics/PFAS, and supporting data science), California-wide.

Three pipelines (see __main__): a LinkedIn guest-endpoint watcher, Indeed via
python-jobspy, and a priority-employer sweep (allowlist-filtered LinkedIn +
optional direct Greenhouse/Workday probes). Each writes {basename}.{json,md,html}
digests and accumulates into all_jobs.json for the dashboard and triage agent.

Tune the search by editing KEYWORDS, LINKEDIN_SEARCH_TERMS,
BIOTECH_COMPANY_NAMES (the priority-employer allowlist), TARGET_LOCATIONS, and
the LinkedIn geoId / Indeed location.
"""

import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

# ---------------------------------------------------------------------------
# Config — ALL of a user's search settings live in config.json (edit it by hand
# or generate it from a CV; see docs/cv-to-config-prompt.md). Everything below
# falls back to a sensible value if config.json or a key is missing, so the
# scraper always runs.
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    for name in ("config.json", "config.example.json"):
        path = os.path.join(SCRIPT_DIR, name)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if name != "config.json":
                print(f"  ℹ️  config.json not found; using {name} (copy it to config.json and customize)")
            return data
        except FileNotFoundError:
            continue
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ⚠️  {name} not loaded ({e}); trying next")
    print("  ⚠️  No config file found; using built-in defaults")
    return {}


CONFIG = _load_config()


def _cfg(path: str, default):
    """Nested config lookup by dotted path; returns default if absent/empty."""
    cur = CONFIG
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur if cur not in (None, "", [], {}) else default


# Short field label + geo subtitle for the digest titles, from config.profile.
# "Environmental / Toxicology Job Tracker" → "Environmental / Toxicology".
PROFILE_LABEL = re.sub(
    r'\s*(job\s*tracker|tracker|jobs?)\s*$', '',
    str(_cfg("profile.title", "Job")), flags=re.I).strip() or "Job"
PROFILE_SUBTITLE = str(_cfg("profile.subtitle", "All locations"))

# Title keywords for Dr. Scott Coffin — environmental/regulatory toxicology,
# risk assessment, exposure science, water quality, and the data-science work
# that supports it. A title matches if it contains any of these (case-
# insensitive). Multi-word phrases match as substrings ("risk assess" hits
# "Risk Assessor" and "Risk Assessment Scientist"); single tokens are word-
# bounded, so list FULL words ("toxicologist", not the stem "toxicolog").
_KEYWORDS_DEFAULT = [
    # ---- Toxicology (core) ----  (fallback; real list is config.json → keywords.include)
    "toxicologist", "toxicology", "toxicological",
    "ecotoxicologist", "ecotoxicology", "ecotoxicolog",
    "environmental toxicolog", "regulatory toxicolog",
    "computational toxicolog", "predictive toxicolog",
    "aquatic toxicolog", "wildlife toxicolog", "research toxicolog",
    # ---- Risk / exposure / hazard assessment ----
    "risk assess", "risk assessor", "human health risk",
    "ecological risk", "exposure scien", "exposure assess",
    "exposure modeling", "exposure modelling", "hazard assess",
    "hazard identification", "dose-response", "dose response",
    "pharmacokinetic", "toxicokinetic", "pbpk",
    # ---- Environmental science / health / chemistry ----
    "environmental scien", "environmental health",
    "environmental chemist", "environmental chemistry",
    "environmental specialist", "environmental analyst",
    "environmental protection", "environmental epidemiolog",
    "environmental data", "environmental monitoring",
    "environmental assessment", "exposure epidemiolog",
    # ---- Water / contaminants ----
    "water quality", "drinking water", "watershed", "water resources",
    "aquatic scien", "limnolog",
    "microplastic", "microplastics", "nanoplastic", "nanomaterial",
    "pfas", "per- and polyfluoro", "emerging contaminant",
    "contaminant", "pollutant", "air pollution", "water pollution",
    "air quality", "remediation", "environmental remediation",
    # ---- Chemical safety / product stewardship / regulatory ----
    "chemical safety", "chemical risk", "chemical assess",
    "chemical regulatory", "product steward", "regulatory toxicolog",
    "hazardous materials", "hazardous waste", "environmental compliance",
    # ---- Ecotoxicology-adjacent ecology / sustainability ----
    "ecotoxicolog", "conservation toxicolog",
]
KEYWORDS = _cfg("keywords.include", _KEYWORDS_DEFAULT)
# NOTE: deliberately tight. Generic titles ("Research Scientist", "Senior
# Scientist", "Data Scientist", "Professor", "Regulatory Affairs") were removed
# because they pull in pharma/biotech/tech bench roles. Environmental academic,
# data, and policy roles are still caught via their qualified forms
# ("Environmental Data Scientist" → "environmental data", "Assistant Professor
# of Environmental Health" → "environmental health", etc.).

# Seconds to wait between API probes — keeps us polite
REQUEST_DELAY = 0.3

# Biotech digest should only contain reliably fresh roles.
FRESH_JOB_LOOKBACK = timedelta(hours=24)

# Titles containing any excluded term are dropped (config.json → keywords.exclude).
# Single tokens are word-bounded; multi-word phrases match as substrings.
def _build_title_re(terms: list) -> re.Pattern:
    return re.compile(
        "|".join(re.escape(t) if (" " in t or "&" in t) else rf"\b{re.escape(t)}\b" for t in terms),
        re.IGNORECASE,
    )


_EXCLUDE_DEFAULT = [
    "intern", "interns", "internship", "co-op", "coop", "trainee", "apprentice", "technician",
    "research assistant", "lab assistant", "teaching assistant", "undergraduate", "postdoc",
    "postdoctoral", "work-study", "volunteer", "fellowship", "engineer", "engineering",
    "ehs", "occupational safety", "occupational health", "health and safety", "health & safety",
]
EXCLUDED_SENIORITY_RE = _build_title_re(_cfg("keywords.exclude", _EXCLUDE_DEFAULT))

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
        print(f"  WARNING: Could not fetch {url}: {e}")
        return ""


def is_mle_role(title: str) -> bool:
    """True if a job title is on-target for Dr. Coffin (env/tox/risk/etc.) and
    not a junior/student posting. (Name kept for compatibility with the
    original pipeline; it now gates environmental-toxicology titles.)"""
    if EXCLUDED_SENIORITY_RE.search(title):
        return False
    return bool(_KEYWORD_RE.search(title))


# Geographic scope for the curated/legacy ATS path. Primary base is California,
# extended to Portland & Bend OR and Australia. (The LinkedIn and Indeed watchers
# geo-filter at the API level — see LINKEDIN_GEOS / INDEED_GEOS.)
TARGET_LOCATIONS = [
    "california", ", ca", "remote", "hybrid",
    # Sacramento region (home base)
    "sacramento", "davis", "west sacramento", "rancho cordova", "elk grove",
    "roseville", "folsom", "woodland",
    # SF Bay Area
    "bay area", "san francisco", "south san francisco", "oakland", "berkeley",
    "emeryville", "richmond", "palo alto", "mountain view", "menlo park",
    "sunnyvale", "santa clara", "san jose", "san mateo", "redwood city",
    "fremont", "hayward", "concord", "walnut creek", "pleasanton", "livermore",
    "novato", "san rafael", "vacaville",
    # Southern California (his PhD / collaborator base)
    "los angeles", "long beach", "irvine", "costa mesa", "san diego",
    "riverside", "pasadena", "santa monica", "torrance", "fountain valley",
    # Central Coast / Valley
    "santa barbara", "san luis obispo", "fresno", "monterey",
    # Oregon
    "portland, or", "portland, oregon", "bend, or", "bend, oregon", "oregon",
    "beaverton", "hillsboro", "eugene", "salem, or", "corvallis", "redmond, or",
    # Australia
    "australia", "sydney", "melbourne", "brisbane", "perth", "adelaide",
    "canberra", "new south wales", "victoria", "queensland",
    "western australia", "tasmania",
]


def is_target_location(location: str) -> bool:
    if not location:
        return False
    loc = location.lower()
    return any(place in loc for place in TARGET_LOCATIONS)


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
#
# NOTE: The original biotech employers were on public Greenhouse/Workday boards.
# Environmental / toxicology employers (Ramboll, Exponent, ToxStrategies, Tetra
# Tech, ICF, NGOs, etc.) overwhelmingly use iCIMS / Taleo / SuccessFactors,
# which have no clean public JSON endpoint — so this direct-ATS path is left
# EMPTY and the LinkedIn + Indeed keyword watchers (which need no slug) are the
# primary sources. To add a verified board here, confirm it returns JSON first:
#   curl https://boards-api.greenhouse.io/v1/boards/<slug>/jobs   # Greenhouse
# then add e.g.:
#   {"name": "Example Env Co", "ats": "greenhouse", "slug": "examplenv",
#    "fallback_location": "Sacramento, CA"},
CURATED_BIOTECHS: list[dict] = []


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
    "toxicologist",
    "environmental scientist",
    "risk assessment",
    "exposure scientist",
    "environmental health",
    "water quality",
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
    if not CURATED_BIOTECHS:
        return []
    print(f"🔬 Scraping {len(CURATED_BIOTECHS)} curated organizations (direct ATS)...")
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

LINKEDIN_SEARCH_TERMS = _cfg("search_terms.linkedin", [
    # Toxicology (core)
    "toxicologist",
    "environmental toxicologist",
    "ecotoxicologist",
    "regulatory toxicologist",
    "computational toxicology",
    # Risk / exposure
    "human health risk assessment",
    "ecological risk assessment",
    "exposure scientist",
    "exposure assessment",
    # Environmental science / health / chemistry
    "environmental scientist",
    "environmental health scientist",
    "environmental chemist",
    "environmental epidemiologist",
    # Water / contaminants
    "water quality scientist",
    "microplastics",
    "PFAS",
    "emerging contaminants",
    "air quality scientist",
    # Chemical safety / stewardship / regulatory
    "product stewardship",
    "chemical safety",
    "environmental data scientist",
])

LINKEDIN_LOOKBACK_SECONDS = 3600          # 1h — every-2h watcher only surfaces the freshest hour
LINKEDIN_BIOTECH_LOOKBACK_SECONDS = 86400 # 24h — biotech is a daily 8pm PT digest

# Geographies to search. geoId is LinkedIn's authoritative region filter; an
# empty geoId lets LinkedIn resolve the location text (verified to work for
# Bend). All confirmed by probing the guest endpoint. Add a region by finding
# its geoId (or leaving it blank for a city LinkedIn can resolve).
LINKEDIN_GEOS = _cfg("locations.linkedin", [
    {"name": "California",  "location": "California, United States",          "geoId": "102095887"},
    {"name": "Portland OR", "location": "Portland, Oregon Metropolitan Area", "geoId": "90000079"},
    {"name": "Bend OR",     "location": "Bend, Oregon, United States",        "geoId": ""},
    {"name": "Australia",   "location": "Australia",                          "geoId": "101452733"},
])

# Priority-employer allowlist used by the LinkedIn-side filter to build the
# daily "Priority Employers" digest (jobs.json). These are organizations whose
# postings are worth surfacing on their own even on a quiet day: environmental
# consulting, toxicology/risk firms, research institutes, NGOs, agencies, water
# utilities, universities, and product-safety teams in industry. Match is case-
# insensitive on alphanum-stripped names with bidirectional substring matching,
# so "Ramboll" matches "Ramboll US Corporation". Keep names ~6+ chars to limit
# incidental substring collisions (avoid bare acronyms like EPA/EWG/ERG/CARB).
BIOTECH_COMPANY_NAMES = _cfg("employers.priority", [
    # ---- Environmental / toxicology / risk consulting ----  (fallback; real list in config.json)
    "Ramboll", "Exponent", "Gradient", "ToxStrategies", "Cardno",
    "Stantec", "Tetra Tech", "Tetratech", "ICF International",
    "Abt Associates", "Abt Global", "Eastern Research Group",
    "Integral Consulting", "Geosyntec", "Arcadis", "AECOM",
    "Montrose Environmental", "Trinity Consultants", "GHD Group",
    "Environmental Resources Management", "SLR Consulting",
    "Wood Environment", "Sciome", "Cardno ChemRisk", "ChemRisk",
    # ---- Research institutes / nonprofits / NGOs ----
    "Southern California Coastal Water Research Project", "SCCWRP",
    "San Francisco Estuary Institute", "Silent Spring Institute",
    "Environmental Defense Fund", "Natural Resources Defense Council",
    "Environmental Working Group", "Ocean Conservancy", "Pew Charitable Trusts",
    "Health Effects Institute", "RTI International", "Battelle",
    "Green Science Policy Institute", "Defend Our Health",
    "Moore Institute", "Plastic Pollution Coalition", "5 Gyres",
    "ChemForward", "Cadmus Group",
    # ---- Government / agencies (as they appear on LinkedIn) ----
    "Environmental Protection Agency", "California Environmental Protection",
    "Office of Environmental Health Hazard", "State Water Resources Control",
    "California Air Resources Board", "Department of Toxic Substances Control",
    "National Institute of Environmental Health", "Geological Survey",
    "Centers for Disease Control", "Food and Drug Administration",
    "National Oceanic and Atmospheric",
    # ---- Water utilities / districts ----
    "East Bay Municipal Utility", "Metropolitan Water District",
    "Orange County Water District", "San Francisco Public Utilities",
    "Santa Clara Valley Water",
    # ---- Universities (research-scientist / faculty) ----
    "University of California", "Stanford University", "Oregon State University",
    "Duke University", "San Diego State University", "Arizona State University",
    # ---- Industry product-safety / stewardship / consumer & chemical ----
    "Procter & Gamble", "Unilever", "Colgate-Palmolive", "Johnson & Johnson",
    "Clorox", "Seventh Generation", "Patagonia",
    "Corteva", "Syngenta", "Dow Chemical", "BASF Corporation",
])

BIOTECH_COMPANY_ALLOWLIST = frozenset(
    re.sub(r'[^a-z0-9]', '', n.lower()) for n in BIOTECH_COMPANY_NAMES
)


def _is_biotech_company(name: str) -> bool:
    norm = re.sub(r'[^a-z0-9]', '', (name or "").lower())
    if not norm:
        return False
    return any(b in norm or norm in b for b in BIOTECH_COMPANY_ALLOWLIST)


# Pharma / drug-development companies. Dr. Coffin works in ENVIRONMENTAL
# toxicology, never pharmaceutical / preclinical drug-safety tox, so these are
# dropped everywhere even when the title (e.g. "Toxicologist", "Toxicology
# Director") would otherwise match. Agrochemical and chemical manufacturers
# (Corteva, Syngenta, Dow, BASF) are intentionally NOT here — their product-
# stewardship / chemical-risk roles are in-scope.
PHARMA_COMPANY_RE = re.compile(
    # ---- generic pharma / biotech / drug-development name signals (substring) ----
    r'pharmaceutic|pharma\b|therapeutic|biopharm|biotech|biologic|bioscience|'
    r'biosystem|genomics|gene therap|cell therap|immunotherap|\bvaccine|'
    r'\bmedicines\b|drug discovery|oncolog|biomedicine|nanomedicine'
    # ---- explicit pharma / biotech companies (word-bounded, length >= 5) ----
    r'|\b(?:'
    r'pfizer|merck|novartis|roche|abbvie|bristol[ -]?myers|sanofi|astrazeneca|'
    r'glaxosmithkline|takeda|boehringer|amgen|gilead|genentech|biogen|regeneron|'
    r'moderna|vertex|novo nordisk|viatris|bausch|alkermes|halozyme|galapagos|'
    r'insitro|recursion|cytokinetics|arcus|gritstone|sutro|nurix|rigel|corcept|'
    r'annexon|kodiak|coherus|vaxcyte|allakos|protagonist|kyverna|septerna|'
    r'sangamo|atara|allogene|intellia|editas|poseida|nkarta|tenaya|pliant|'
    r'rezolute|aldeyra|arcturus|caribou|chemocentryx|dynavax|geron|iovance|'
    r'karuna|mersana|mirati|nektar|prothena|revance|seagen|ultragenyx|zentalis|'
    r'exelixis|biomarin|alnylam|incyte|neurocrine|ionis|denali|acadia|adarx|'
    r'genmab|nuvation|exact sciences|revolution medicines|structure therapeutics|'
    r'relay therapeutics|beam therapeutics|sana biotechnology|fate therapeutics'
    r')\b',
    re.IGNORECASE,
)


# config.json → employers.exclude: drop roles from any company whose name
# contains one of these (case-insensitive substring). When set, it overrides the
# built-in PHARMA_COMPANY_RE; leave it [] to disable company exclusion entirely.
_EXCLUDE_COMPANY_TERMS = [str(t).lower() for t in _cfg("employers.exclude", [])]


def _is_pharma_company(name: str) -> bool:
    if _EXCLUDE_COMPANY_TERMS:
        low = (name or "").lower()
        return any(t in low for t in _EXCLUDE_COMPANY_TERMS)
    return bool(PHARMA_COMPANY_RE.search(name or ""))


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
        # LinkedIn shows pay on the card when the poster provides it.
        salary_m = re.search(r'job-search-card__salary-info[^>]*>\s*([^<]+)', card)

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
        salary = (
            re.sub(r'\s+', ' ', html_mod.unescape(salary_m.group(1).strip()))
            if salary_m else ""
        )
        parsed.append({
            "id": urn.group(1),
            "company": company,
            "title": title,
            "location": location,
            "date_posted": time_m.group(1) if time_m else "",
            "salary": salary,
        })
    return parsed, raw_count


def _linkedin_search(terms: list[str], lookback_seconds: int,
                     geos: list[dict] | None = None) -> tuple[list[dict], int]:
    """
    Per-geo, per-term, paginated LinkedIn guest-endpoint search. Dedupes by job
    ID across every geography and sorts by recency. Used by both the general
    watcher and the priority-employer scrape.

    Returns (jobs, total_raw_cards). total_raw_cards == 0 across everything means
    LinkedIn gave us no data at all — the callers' block guard.
    """
    if geos is None:
        geos = LINKEDIN_GEOS
    jobs_by_id: dict[str, dict] = {}
    total_raw_cards = 0
    for geo in geos:
        geo_param = f"&geoId={geo['geoId']}" if geo.get("geoId") else ""
        for term in terms:
            for start in range(0, 75, 25):
                time.sleep(REQUEST_DELAY)
                url = (
                    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
                    f"?keywords={urllib.parse.quote(term)}"
                    f"&location={urllib.parse.quote(geo['location'])}"
                    f"{geo_param}"
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
                        "salary": p.get("salary", ""),
                        "ats": "LinkedIn",
                    }

    jobs = list(jobs_by_id.values())
    jobs.sort(key=lambda j: -_iso_to_ts(j.get("date_posted", "")))
    return jobs, total_raw_cards


# LinkedIn search-result cards omit the full description and often omit pay, but
# the public guest *posting* page includes both. Fetch it only for jobs that need
# enrichment, capped per run to bound runtime.
LINKEDIN_SALARY_FETCH_CAP = 120
LINKEDIN_DESCRIPTION_MAX_CHARS = 12000


def _linkedin_description_from_page(page: str) -> str:
    import html as html_mod
    if not page:
        return ""
    block = ""
    m = re.search(
        r'<div[^>]+show-more-less-html__markup[^>]*>(.*?)</div>\s*</section>',
        page,
        re.I | re.S,
    )
    if m:
        block = m.group(1)
    else:
        m = re.search(r'<div[^>]+description__text[^>]*>(.*?)</section>', page, re.I | re.S)
        if m:
            block = m.group(1)
    if not block:
        return ""
    text = re.sub(r'(?i)<\s*br\s*/?\s*>', "\n", block)
    text = re.sub(r'(?i)</\s*(p|li|ul|ol|section|div|strong|h\d)\s*>', "\n", text)
    text = re.sub(r'<[^>]+>', " ", text)
    text = html_mod.unescape(text)
    text = re.sub(r'[ \t\r\f\v]+', ' ', text)
    text = re.sub(r'\n\s*\n+', '\n\n', text)
    text = re.sub(r' *\n *', '\n', text).strip()
    return text[:LINKEDIN_DESCRIPTION_MAX_CHARS]


def _linkedin_posting_details(job_id: str) -> tuple[str, str]:
    import html as html_mod
    page = fetch(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}")
    if not page:
        return "", ""
    description = _linkedin_description_from_page(page)
    # Primary: structured compensation block (employer-declared LinkedIn field).
    anchor = re.search(r'compensation__salary', page)
    if anchor:
        window = page[anchor.start():anchor.start() + 400]
        amt = re.search(r'\$[\d][^<]{0,60}', window)
        if amt:
            return re.sub(r'\s+', ' ', html_mod.unescape(amt.group(0))).strip(), description
    # Fallback: salary range embedded in description text.
    text = re.sub(r'\s+', ' ', html_mod.unescape(re.sub(r'<[^>]+>', ' ', page)))

    # Pattern 1: two-dollar-sign range with optional USD codes and en/em dashes.
    # Handles: "$130k to $176k", "$7,820 – $10,732", "$75,000 USD - $85,000 USD",
    #          "USD $200,000 - USD $300,000"
    sal_m = re.search(
        r'(?:USD\s*)?\$\s*[\d,]+(?:\.\d{2})?(?:\s*[kK])?(?:\s*USD)?\s*(?:to|[–—-])\s*(?:USD\s*)?\$\s*[\d,]+(?:\.\d{2})?(?:\s*[kK])?(?:\s*USD)?'
        r'(?:\s*(?:per\s+\w+|annually|hourly|monthly|/\w+))?',
        text, re.I,
    )
    if sal_m:
        return re.sub(r'\s+', ' ', sal_m.group(0)).strip(), description

    # Pattern 2: single leading $ with bare second number: "$110,000-130,000/year".
    # Require second number to be comma-formatted (NNN,NNN) to avoid false positives.
    sal_m = re.search(
        r'\$\s*[\d,]+(?:\.\d{2})?(?:\s*[kK])?\s*-\s*\d{2,3},\d{3}(?:\.\d{2})?(?:\s*[kK])?'
        r'(?:\s*(?:per\s+\w+|annually|hourly|monthly|/\w+))?',
        text, re.I,
    )
    if sal_m:
        return re.sub(r'\s+', ' ', sal_m.group(0)).strip(), description

    # Pattern 3: keyword-anchored plain number range (no $ sign).
    # Handles: "Compensation Range 199,000.00 - 243,000.00 | Compensation Type Annual Salary"
    kw_m = re.search(
        r'(?:compensation|salary)\s+(?:range|amount)[:\s]+([\d,]+(?:\.\d{2})?)\s*(?:to|-)\s*([\d,]+(?:\.\d{2})?)',
        text, re.I,
    )
    if kw_m:
        return f"${kw_m.group(1)} - ${kw_m.group(2)}", description

    # Pattern 4: "Minimum Salary: $156,115/year / Maximum Salary: $218,560/year"
    min_m = re.search(r'[Mm]inimum\s+[Ss]alary[:\s]+\$\s*([\d,]+(?:\.\d{2})?(?:\s*[kK])?)', text)
    max_m = re.search(r'[Mm]aximum\s+[Ss]alary[:\s]+\$\s*([\d,]+(?:\.\d{2})?(?:\s*[kK])?)', text)
    if min_m and max_m:
        interval_m = re.search(
            r'(?:per\s+\w+|annually|hourly|monthly|/(?:year|yr|hr|mo))',
            text[min_m.start():min_m.start() + 60], re.I,
        )
        interval = f"/{interval_m.group(0).lstrip('/')}" if interval_m else ""
        return f"${min_m.group(1)}{interval} - ${max_m.group(1)}{interval}", description

    return "", description


def _linkedin_posting_salary(job_id: str) -> str:
    salary, _ = _linkedin_posting_details(job_id)
    return salary


def _enrich_linkedin_postings(jobs: list) -> tuple[int, int]:
    """Backfill salary and description on LinkedIn jobs from posting pages.
    Returns (salary_filled, description_filled). Bounded and never raises."""
    salary_filled = desc_filled = fetched = 0
    for job in jobs:
        if fetched >= LINKEDIN_SALARY_FETCH_CAP:
            break
        if job.get("ats") != "LinkedIn":
            continue
        if job.get("salary") and job.get("description"):
            continue
        m = re.search(r'/jobs/view/(\d+)', job.get("url", ""))
        if not m:
            continue
        time.sleep(REQUEST_DELAY)
        fetched += 1
        try:
            sal, desc = _linkedin_posting_details(m.group(1))
        except (URLError, TimeoutError, OSError):
            continue
        if sal:
            job["salary"] = sal
            salary_filled += 1
        if desc:
            job["description"] = desc
            desc_filled += 1
    if fetched:
        print(
            "  LinkedIn posting backfill: "
            f"{salary_filled}/{fetched} had pay; {desc_filled}/{fetched} had descriptions"
        )
    return salary_filled, desc_filled

def scrape_linkedin_recent() -> list:
    print(f"🔎 Scraping LinkedIn (last {LINKEDIN_LOOKBACK_SECONDS // 3600}h)...")
    jobs, raw_cards = _linkedin_search(LINKEDIN_SEARCH_TERMS, LINKEDIN_LOOKBACK_SECONDS)
    # Block guard (mirrors Indeed's): zero raw cards across every term means
    # LinkedIn gave us nothing — rate-limited or blocked, not a quiet hour.
    # Reuse the previous results so we don't clobber the dedupe baseline.
    if raw_cards == 0:
        prev = _load_prev_jobs(os.path.join(OUTPUT_DIR, "linkedin_jobs.json"))
        print(f"  ⛔ LinkedIn returned 0 cards across all terms (likely blocked); "
              f"preserving previous {len(prev)} result(s)")
        return prev
    print(f"  ✅ LinkedIn: {len(jobs)} role(s)")
    _enrich_linkedin_postings(jobs)
    return jobs


def scrape_linkedin_biotech() -> list:
    """
    Last 24h on LinkedIn, filtered to the priority-employer allowlist (env/tox
    consulting, research institutes, agencies, NGOs, universities, product
    safety). LinkedIn's f_I industry filter is silently ignored on the public
    guest endpoint, so we use the env/tox keyword terms + a company allowlist.
    """
    print(f"🏛  Scraping LinkedIn priority employers (last {LINKEDIN_BIOTECH_LOOKBACK_SECONDS // 3600}h)...")
    raw, raw_cards = _linkedin_search(LINKEDIN_SEARCH_TERMS, LINKEDIN_BIOTECH_LOOKBACK_SECONDS)
    if raw_cards == 0:
        # Blocked run: contribute nothing rather than nuke the digest baseline.
        print("  ⛔ LinkedIn returned 0 cards across all terms (likely blocked); "
              "skipping LinkedIn for this digest")
        return []
    jobs = [j for j in raw if _is_biotech_company(j["company"])]
    print(f"  ✅ Priority employers: {len(jobs)} role(s) (from {len(raw)} total)")
    _enrich_linkedin_postings(jobs)
    return jobs


# ---------------------------------------------------------------------------
# Indeed — via python-jobspy (Indeed's RSS feeds + Publisher API were both
# deprecated in 2026, and indeed.com sits behind Cloudflare top-tier bot
# protection. JobSpy uses Indeed's mobile-app API internally — no proxies
# required, no documented rate limit.)
# ---------------------------------------------------------------------------

INDEED_LOOKBACK_HOURS = 24  # Indeed posting dates are ~day-resolution, so a 1h window
# returns almost nothing; the hourly watcher's cross-run dedupe trims the overlap.

# Indeed geographies. country sets the Indeed domain (USA → indeed.com,
# Australia → au.indeed.com). Searched per term, so we use a tighter term list
# than LinkedIn to keep the call count sane (terms × geos jobspy calls).
INDEED_GEOS = _cfg("locations.indeed", [
    {"location": "California",   "country": "USA"},
    {"location": "Portland, OR", "country": "USA"},
    {"location": "Bend, OR",     "country": "USA"},
    {"location": "Australia",    "country": "Australia"},
])
INDEED_SEARCH_TERMS = _cfg("search_terms.indeed", [
    "toxicologist", "environmental scientist", "risk assessment",
    "exposure scientist", "ecotoxicologist", "microplastics",
    "water quality scientist", "environmental health scientist",
])

# jobspy returns the full JD (markdown) for Indeed rows. We keep a trimmed copy
# in indeed_jobs.json and all_jobs.json so the dashboard, deterministic scorer,
# and optional triage agent can judge roles from the actual description instead
# of the title alone.
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
    for geo in INDEED_GEOS:
      for term in INDEED_SEARCH_TERMS:
        time.sleep(REQUEST_DELAY)  # throttle: back-to-back calls invite blocking on CI IPs
        try:
            # JobSpy Indeed gotcha: hours_old / is_remote / job_type / easy_apply
            # are mutually exclusive — only one may be set, or the time filter
            # silently breaks. Keep hours_old; do not add the others.
            df = jobspy_scrape(
                site_name=["indeed"],
                search_term=term,
                location=geo["location"],
                results_wanted=50,
                hours_old=INDEED_LOOKBACK_HOURS,
                country_indeed=geo["country"],
            )
        except Exception as e:
            errored_terms += 1
            print(f"  ⚠️  Indeed ({geo['location']} · {term!r}): {e}")
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
        f"  📊 Indeed: {len(INDEED_GEOS)}×{len(INDEED_SEARCH_TERMS)} queries → "
        f"{ok_terms} ok / {errored_terms} errored · {raw_rows} raw, {len(jobs)} matched"
    )

    # Block guard: zero rows pulled across every term means Indeed gave us no data
    # — a hard block (calls raised) or a soft block (empty frames). This is NOT the
    # same as "rows returned but none matched our keywords" (raw_rows > 0, jobs == []),
    # which is a legitimate empty result. On a no-data run, reuse the previous results
    # so we don't clobber the dedupe baseline (and the dashboard's Indeed column) with
    # an empty file; save_indeed_results() then reports 0 new (all already seen).
    if raw_rows == 0:
        prev = _load_prev_jobs(os.path.join(OUTPUT_DIR, "indeed_jobs.json"))
        print(
            f"  ⛔ Indeed returned 0 rows across all terms (likely blocked); "
            f"preserving previous {len(prev)} result(s)"
        )
        return prev

    return jobs


# ---------------------------------------------------------------------------
# CalCareers (California state civil-service jobs) — calcareers.ca.gov
#
# CalCareers is an ASP.NET WebForms portal (DevExpress) with NO public JSON
# API: search state lives in a server-side session keyed by ASP.NET_SessionId.
# So we (1) GET the results page to seed a session + capture the hidden
# __VIEWSTATE/__EVENTVALIDATION fields, (2) auto-discover the keyword text box
# and the submit control from the live HTML (the ctl00$... names aren't stable
# or documented), (3) POST the search, and (4) parse JobPosting links from the
# returned HTML. Everything is wrapped so any failure is non-fatal.
#
# NOTE: this path could NOT be verified from the dev network (the site sits
# behind a WAF that times out there); it is written to run on GitHub Actions'
# clean egress. If the first GH run logs 0 rows, the result-card parsing in
# _parse_calcareers_results() likely needs a selector tweak. CA state
# departments (OEHHA, DTSC, CARB, Caltrans, Water Boards) also surface via the
# LinkedIn priority-employer allowlist as a backstop.
# ---------------------------------------------------------------------------

CALCAREERS_BASE = "https://www.calcareers.ca.gov"
# Apex domain for the search postback (per the OpenPostings calcareers module).
CALCAREERS_SEARCH_URL = "https://calcareers.ca.gov/CalHRPublic/Search/JobSearchResults.aspx"
CALCAREERS_TIMEOUT = 30

# Broad CalCareers queries; titles are still gated by is_mle_role() afterward.
CALCAREERS_TERMS = [
    "toxicologist", "environmental scientist", "risk assessment",
    "exposure", "water quality", "microplastics", "hazard",
    "environmental health", "ecologist",
]


def _calcareers_opener():
    """A urllib opener with its own cookie jar so the ASP.NET session set on the
    seeding GET is sent back on the search POST."""
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _hidden_inputs(html: str) -> dict:
    """All <input type=hidden> name→value pairs (the ASP.NET viewstate set)."""
    fields = {}
    for tag in re.findall(r'<input\b[^>]*type=["\']hidden["\'][^>]*>', html, re.I):
        n = re.search(r'\bname=["\']([^"\']+)["\']', tag)
        v = re.search(r'\bvalue=["\']([^"\']*)["\']', tag)
        if n:
            fields[n.group(1)] = (v.group(1) if v else "")
    return fields


# CalCareers renders each result as labeled "col-xs-6 job-details" divs
# (Working Title / Job Control / Department / Location / Publish Date) followed
# by the posting link. Pattern adapted from the OpenPostings calcareers module.
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
        # The card carries a "Salary Range:" field (e.g. "$4418.00 - $9321.00",
        # usually monthly for CA state). Pull it from the matched card span.
        card = html[m.start():m.end()]
        sal_m = re.search(
            r'Salary Range:\s*</div>\s*<div[^>]*>([\s\S]*?)</div>', card, re.I)
        salary = ""
        if sal_m:
            sm = re.search(
                r'\$[\d,]+(?:\.\d{2})?\s*-\s*\$[\d,]+(?:\.\d{2})?(?:\s*(?:per|/)\s*\w+)?',
                _clean(sal_m.group(1)))
            salary = sm.group(0).strip() if sm else ""
        jobs.append({
            "company": _clean(dept) or "State of California",
            "title": _clean(title),
            "location": _clean(location) or "California",
            "url": _clean(url),
            "date_posted": date,
            "salary": salary,
            "ats": "CalCareers",
        })
    return jobs


def _calcareers_payload(hidden: dict, event_target: str, keyword: str) -> dict:
    """ASP.NET postback body that actually fires the search (the missing piece
    was __EVENTTARGET=btnSearch + the real keyword field name)."""
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
    """CalCareers env/tox roles via the ASP.NET search postback (method proven by
    the OpenPostings project). Fully guarded — returns previous results on any
    failure so a flaky run never nukes the dashboard's CalCareers column."""
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
    for term in CALCAREERS_TERMS:
        time.sleep(REQUEST_DELAY)
        try:
            opener = _calcareers_opener()  # fresh session/viewstate per keyword
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
        # No data — site unreachable or parser/search mismatch. Preserve the
        # previous column rather than blanking it.
        return _load_prev_jobs(os.path.join(OUTPUT_DIR, "calcareers_jobs.json"))
    return jobs


def save_calcareers_results(jobs: list):
    save_jobs_output(
        jobs,
        basename="calcareers_jobs",
        title=f"🏛 CalCareers — California State {PROFILE_LABEL} Roles",
        subtitle="calcareers.ca.gov · California state civil service",
        accent="#b45309",
        empty_message="No new CalCareers roles since the last run.",
        window_label="current CalCareers postings",
    )


# ---------------------------------------------------------------------------
# USAJOBS — federal jobs (EPA, NOAA, USGS, FDA, NIEHS, CDC, DOI, ...)
#
# Uses the public usajobs.gov website search (NO API key): GET the Results page
# to seed a session cookie, then POST /Search/ExecuteSearch per keyword. Returns
# federal env/tox roles WITH salary (SalaryDisplay). Verified working from a
# plain client. Source surfaced via the OpenPostings ATS catalog
# (https://github.com/Masterjx9/OpenPostings), which lists usajobs among 80+
# providers; we query the official public endpoint directly.
# ---------------------------------------------------------------------------

USAJOBS_RESULTS_URL = "https://www.usajobs.gov/Search/Results?hp=public&s=startdate&sd=desc&p=1"
USAJOBS_SEARCH_URL = "https://www.usajobs.gov/Search/ExecuteSearch"
USAJOBS_TERMS = [
    "toxicologist", "environmental protection specialist", "environmental health",
    "ecotoxicology", "exposure science", "risk assessment",
    "environmental scientist", "microplastics", "ecologist",
]
USAJOBS_RESULTS_PER_PAGE = 50


def _usajobs_date(date_display: str) -> str:
    """"Open 06/13/2026 to 06/27/2026" → "2026-06-13" (the open date)."""
    m = re.search(r'(\d{2})/(\d{2})/(\d{4})', date_display or "")
    return f"{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else ""


def scrape_usajobs_recent() -> list:
    """Federal env/tox roles from usajobs.gov (no API key). Guarded — returns the
    previous results on any failure so a flaky run never blanks the column."""
    print("🇺🇸 Scraping USAJOBS (federal env/tox roles)...")
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
        opener = _calcareers_opener()  # cookie jar — the POST needs the session
        opener.open(Request(USAJOBS_RESULTS_URL, headers=HEADERS), timeout=25).read()
        for term in USAJOBS_TERMS:
            time.sleep(REQUEST_DELAY)
            body = json.dumps({
                "Keyword": term, "HiringPath": ["public"],
                "SortField": "startdate", "SortDirection": "desc",
                "Page": "1", "ResultsPerPage": USAJOBS_RESULTS_PER_PAGE,
            }).encode()
            try:
                raw = opener.open(Request(USAJOBS_SEARCH_URL, data=body, headers=headers),
                                  timeout=25).read().decode("utf-8", "ignore")
                payload = json.loads(raw)
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
        return _load_prev_jobs(os.path.join(OUTPUT_DIR, "usajobs_jobs.json"))

    jobs = list(jobs_by_url.values())
    print(f"  ✅ USAJOBS: {len(jobs)} federal role(s)")
    if not jobs:
        return _load_prev_jobs(os.path.join(OUTPUT_DIR, "usajobs_jobs.json"))
    return jobs


def save_usajobs_results(jobs: list):
    save_jobs_output(
        jobs,
        basename="usajobs_jobs",
        title=f"🇺🇸 USAJOBS — Federal {PROFILE_LABEL} Roles",
        subtitle="usajobs.gov · federal agencies",
        accent="#1d4ed8",
        empty_message="No new federal roles since the last run.",
        window_label="current USAJOBS postings",
    )


# ---------------------------------------------------------------------------
# GovernmentJobs.com / NEOGOV — state, county & city agencies (air & water
# districts, county environmental health, etc.). HTML search; keyword-filterable.
# Post-filtered to CA/OR (the board is nationwide). Source from the OpenPostings
# ATS catalog (https://github.com/Masterjx9/OpenPostings).
# ---------------------------------------------------------------------------

GOVERNMENTJOBS_BASE = "https://www.governmentjobs.com"
GOVERNMENTJOBS_TERMS = [
    "toxicologist", "environmental scientist", "environmental health",
    "risk assessment", "water quality", "air quality",
    "hazardous materials", "environmental specialist",
]
GOVERNMENTJOBS_DAYS = 21
GOVERNMENTJOBS_PAGES = 2


def scrape_governmentjobs_recent() -> list:
    """State/local-gov env roles via governmentjobs.com, filtered to CA/OR."""
    print("🏛  Scraping GovernmentJobs/NEOGOV (state & local gov)...")
    item_re = re.compile(r'<li[^>]*class=["\'][^"\']*\bjob-item\b[^"\']*["\'][^>]*>([\s\S]*?)</li>', re.I)
    link_re = re.compile(r'<a[^>]*class=["\'][^"\']*\bjob-details-link\b[^"\']*["\'][^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>', re.I)
    org_re = re.compile(r'<div[^>]*class=["\'][^"\']*\bjob-organization\b[^"\']*["\'][^>]*>([\s\S]*?)</div>', re.I)
    loc_re = re.compile(r'<span[^>]*class=["\'][^"\']*\bjob-location\b[^"\']*["\'][^>]*>([\s\S]*?)</span>', re.I)
    import html as html_mod

    def _clean(s):
        return re.sub(r'\s+', ' ', html_mod.unescape(re.sub(r'<[^>]+>', ' ', s or ''))).strip()

    jobs_by_url: dict[str, dict] = {}
    raw_items = 0
    for term in GOVERNMENTJOBS_TERMS:
        for page in range(1, GOVERNMENTJOBS_PAGES + 1):
            time.sleep(REQUEST_DELAY)
            url = (f"{GOVERNMENTJOBS_BASE}/jobs?keyword={urllib.parse.quote(term)}"
                   f"&daysposted={GOVERNMENTJOBS_DAYS}&isFiltered=true&page={page}")
            page_html = fetch(url)
            items = item_re.findall(page_html)
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
                if not is_target_location(location):
                    continue   # board is nationwide — keep CA/OR only
                href = re.sub(r'\s+', '', lk.group(1))
                job_url = href if href.startswith("http") else GOVERNMENTJOBS_BASE + "/" + href.lstrip("/")
                if job_url in jobs_by_url:
                    continue
                org_m = org_re.search(it)
                # NEOGOV cards carry pay inline, e.g. "$67,296.24 - $100,098.72
                # Annually" (or Monthly/Hourly) — the dashboard annualizes it.
                sal_m = re.search(
                    r'\$[\d,]+(?:\.\d{2})?\s*-\s*\$[\d,]+(?:\.\d{2})?'
                    r'\s*(?:Annually|Monthly|Hourly|Biweekly|Bi-Weekly|Weekly|Daily)?',
                    _clean(it), re.I)
                jobs_by_url[job_url] = {
                    "company": _clean(org_m.group(1)) if org_m else "Government Agency",
                    "title": title,
                    "location": location,
                    "url": job_url,
                    "date_posted": "",
                    "salary": sal_m.group(0).strip() if sal_m else "",
                    "ats": "NEOGOV",
                }
    jobs = list(jobs_by_url.values())
    print(f"  ✅ NEOGOV: {len(jobs)} CA/OR role(s) (from {raw_items} scanned)")
    if not jobs and raw_items == 0:
        return _load_prev_jobs(os.path.join(OUTPUT_DIR, "governmentjobs_jobs.json"))
    return jobs


def save_governmentjobs_results(jobs: list):
    save_jobs_output(
        jobs,
        basename="governmentjobs_jobs",
        title=f"🏛 NEOGOV — State & Local Government {PROFILE_LABEL} Roles",
        subtitle="governmentjobs.com · state & local agencies",
        accent="#0e7490",
        empty_message="No new state/local-gov roles since the last run.",
        window_label="recent GovernmentJobs postings",
    )


# ---------------------------------------------------------------------------
# CalOpps — California local-agency jobs (cities, counties, special districts,
# water associations). HTML list; CA-only, so no geo filter — just title filter.
# Source from the OpenPostings ATS catalog.
# ---------------------------------------------------------------------------

CALOPPS_LIST_URL = "https://www.calopps.org/job-search-list"
CALOPPS_MAX_PAGES = 10


def _calopps_company(href: str) -> str:
    m = re.match(r'/?([^/]+)/', href or "")
    if not m:
        return "California Agency"
    return m.group(1).replace('-', ' ').title()


def scrape_calopps_recent() -> list:
    """California local-agency env/tox roles from calopps.org (CA-only board)."""
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
        page_html = fetch(url)
        rows = [r for r in row_re.findall(page_html) if "views-field-label" in r.lower()]
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
                "company": _calopps_company(href),
                "title": title,
                "location": _clean(cells[1]) or "California",
                "url": job_url,
                "date_posted": "",
                "salary": "",
                "ats": "CalOpps",
            }
    jobs = list(jobs_by_url.values())
    # Salary is on the posting page (e.g. "Salary $9,272.00-$11,275.00 Monthly"),
    # not the list — backfill it (few matches, so one fetch each is cheap).
    for job in jobs:
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
    print(f"  ✅ CalOpps: {len(jobs)} env/tox role(s) (from {scanned} scanned)")
    if not jobs and scanned == 0:
        return _load_prev_jobs(os.path.join(OUTPUT_DIR, "calopps_jobs.json"))
    return jobs


def save_calopps_results(jobs: list):
    save_jobs_output(
        jobs,
        basename="calopps_jobs",
        title=f"🏛 CalOpps — California Local-Agency {PROFILE_LABEL} Roles",
        subtitle="calopps.org · CA cities, counties, special & water districts",
        accent="#15803d",
        empty_message="No new CalOpps roles since the last run.",
        window_label="recent CalOpps postings",
    )


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
    path = os.path.join(OUTPUT_DIR, "all_jobs.json")
    try:
        with open(path) as f:
            master = json.load(f).get("jobs", [])
    except (FileNotFoundError, json.JSONDecodeError):
        master = []

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    by_url = {j.get("url"): j for j in master if j.get("url")}
    added = enriched = 0
    for j in new_jobs:
        url = j.get("url")
        if url and url not in by_url:  # first writer wins on first_seen
            entry = dict(j)
            entry["first_seen"] = stamp
            by_url[url] = entry
            added += 1
        elif url and url in by_url:
            existing = by_url[url]
            for key in ("description", "salary"):
                if j.get(key) and not existing.get(key):
                    existing[key] = j[key]
                    enriched += 1

    cutoff = (now - timedelta(days=ALL_JOBS_PRUNE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    kept = [j for j in by_url.values() if j.get("first_seen", stamp) >= cutoff]
    kept.sort(key=lambda j: j.get("first_seen", ""), reverse=True)

    with open(path, "w") as f:
        # Compact separators: the dashboard downloads this file on every load.
        json.dump({"updated_at": now.strftime("%Y-%m-%d %H:%M UTC"), "jobs": kept},
                  f, separators=(",", ":"))
    print(
        f"all_jobs.json: +{added} new, {enriched} enriched, "
        f"{len(kept)} total (last {ALL_JOBS_PRUNE_DAYS}d)"
    )
    return added


def save_jobs_output(jobs: list, *, basename: str, title: str, subtitle: str,
                     accent: str, empty_message: str, window_label: str):
    """
    Save jobs to {basename}.{json,md,html}. Dedupes against the previous JSON at
    the same path so each email surfaces only postings new to this run.
    """
    # Single chokepoint for the pharma exclusion: every source (LinkedIn,
    # Indeed, priority, CalCareers) funnels through here, so dropping pharma
    # companies once keeps all digests AND all_jobs.json clean.
    before = len(jobs)
    jobs = [j for j in jobs if not _is_pharma_company(j.get("company", ""))]
    if len(jobs) < before:
        print(f"  🚫 Dropped {before - len(jobs)} pharma role(s)")

    json_path = os.path.join(OUTPUT_DIR, f"{basename}.json")
    md_path = os.path.join(OUTPUT_DIR, f"{basename}.md")
    html_path = os.path.join(OUTPUT_DIR, f"{basename}.html")

    prev_ids = _load_prev_ids(json_path)
    new_jobs = [j for j in jobs if _job_identity(j.get("url", "")) not in prev_ids]

    # Accumulate into the cumulative master. Guarded: a bug here must never
    # break the scrape/commit path that the digests and dashboard depend on.
    try:
        # Merge the full current source window, not only brand-new notifications:
        # existing sparse LinkedIn records can gain salary/description later.
        _merge_into_all_jobs(jobs)
    except Exception as e:
        print(f"  ⚠️  all_jobs.json accumulator failed (non-fatal): {e}")

    # Push the highly-relevant new roles to Pushover (no-op without creds).
    try:
        import notify
        notify.notify_new_jobs(new_jobs, basename)
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
        title=f"🔥 LinkedIn — {PROFILE_LABEL} Roles",
        subtitle=f"{PROFILE_SUBTITLE} · last {LINKEDIN_LOOKBACK_SECONDS // 3600}h",
        accent="#3b82f6",
        empty_message="No new roles since the last run.",
        window_label=f"last {LINKEDIN_LOOKBACK_SECONDS // 3600}h",
    )


def save_indeed_results(jobs: list):
    save_jobs_output(
        jobs,
        basename="indeed_jobs",
        title=f"🟦 Indeed — {PROFILE_LABEL} Roles",
        subtitle=f"{PROFILE_SUBTITLE} · last {INDEED_LOOKBACK_HOURS}h",
        accent="#2557a7",
        empty_message="No new roles since the last run.",
        window_label=f"last {INDEED_LOOKBACK_HOURS}h",
    )


def save_biotech_linkedin_results(jobs: list):
    save_jobs_output(
        jobs,
        basename="jobs",
        title=f"🏛 Priority Employers — {PROFILE_LABEL} Roles",
        subtitle=f"{PROFILE_SUBTITLE} · priority-employer allowlist · last {LINKEDIN_BIOTECH_LOOKBACK_SECONDS // 3600}h",
        accent="#2ea04f",
        empty_message="No new priority-employer roles since the last run.",
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
<div class="foot">Auto-generated by <a href="https://github.com/{os.environ.get('GITHUB_REPOSITORY', 'ScottCoffin/Job_Scraper')}">Job_Scraper</a></div>
</body></html>"""


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def save_results(jobs: list):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    output = {"scraped_at": timestamp, "total": len(jobs), "jobs": jobs}
    with open(os.path.join(OUTPUT_DIR, "jobs.json"), "w") as f:
        json.dump(output, f, indent=2)

    lines = [
        "# 🏛 Fresh Environmental / Toxicology Job Listings (California)",
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

    with open(os.path.join(OUTPUT_DIR, "jobs.md"), "w") as f:
        f.write("\n".join(lines))

    with open(os.path.join(OUTPUT_DIR, "jobs.html"), "w") as f:
        f.write(_render_jobs_html(
            title=f"🏛 Fresh {PROFILE_LABEL} Job Listings",
            subtitle=f"{PROFILE_SUBTITLE} · posted in the last 24 hours",
            timestamp=timestamp,
            jobs=jobs,
            empty_message="No environmental/toxicology roles posted in the last 24 hours.",
            accent="#2ea04f",
        ))

    print(f"\n📄 Saved jobs.json/.md/.html ({len(jobs)} total roles)")


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

    if "--calcareers-only" in sys.argv:
        save_calcareers_results(scrape_calcareers_recent())
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

    if "--biotech-only" in sys.argv:
        # "Priority Employers" digest (flag name kept so the GitHub workflow
        # doesn't change). Source = the LinkedIn priority-employer allowlist,
        # plus any verified direct-ATS boards added to CURATED_BIOTECHS (empty
        # by default for env/tox employers — see that list's note). Cross-run
        # dedupe via _load_prev_ids → save_biotech_linkedin_results gives
        # "new since last digest" semantics.
        jobs = list(scrape_curated_biotechs())
        jobs = [j for j in jobs if is_target_location(j.get("location", ""))]
        jobs.extend(scrape_linkedin_biotech())

        seen: set[tuple[str, str]] = set()
        deduped: list[dict] = []
        for j in jobs:
            key = (j["company"].strip().lower(), j["title"].strip().lower())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(j)
        print(f"\n🏛  Combined priority-employer total: {len(deduped)} unique role(s) "
              f"(from {len(jobs)} across sources)")

        save_biotech_linkedin_results(deduped)
        sys.exit(0)

    # Legacy default: direct-ATS sweep (CURATED_BIOTECHS). Empty by default for
    # env/tox employers, so this prints 0; CI uses the three --*-only flags.
    all_jobs = list(scrape_curated_biotechs())

    before = len(all_jobs)
    all_jobs = [j for j in all_jobs if is_target_location(j.get("location", ""))]
    print(f"\n📍 Location filter (California): {before} → {len(all_jobs)} roles")

    before = len(all_jobs)
    all_jobs = [j for j in all_jobs if is_recent_posting(j)]
    print(f"🕒 Freshness filter (last 24h): {before} → {len(all_jobs)} roles")

    save_results(all_jobs)
