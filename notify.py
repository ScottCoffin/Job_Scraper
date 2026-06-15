"""
Pushover push notifications for newly-scraped jobs.

Called by scrape_jobs.save_jobs_output() with each run's new_jobs. It is a
no-op unless BOTH PUSHOVER_TOKEN and PUSHOVER_USER env vars are set (so local
runs and forks without Pushover are unaffected). It dedupes against
notified.json so the same role is never pushed twice — across sources or runs.

By default every new (keyword-matched) role is pushed. To be more selective,
set NOTIFY_TERMS to a comma-separated list of words; only titles containing one
of them are pushed (e.g. NOTIFY_TERMS="staff,principal,senior").

Set up (GitHub → Settings → Secrets and variables → Actions):
  PUSHOVER_TOKEN   your Pushover application/API token
  PUSHOVER_USER    your Pushover user key
Optional Variable: NOTIFY_TERMS (comma-separated title filter).

Test:  PUSHOVER_TOKEN=… PUSHOVER_USER=… python notify.py --test
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
NOTIFIED_PATH = os.path.join(SCRIPT_DIR, "notified.json")
PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
MAX_PUSHES_PER_RUN = 8     # cap individual pings; the rest get one summary
NOTIFIED_KEEP = 600        # remember this many recent jobs to avoid repeats


def _terms() -> list:
    raw = os.environ.get("NOTIFY_TERMS", "") or ""
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


def is_relevant(job: dict) -> bool:
    terms = _terms()
    if not terms:
        return True  # default: notify on every new (already keyword-matched) role
    title = (job.get("title", "") or "").lower()
    return any(t in title for t in terms)


def _identity(job: dict) -> str:
    co = re.sub(r'[^a-z0-9]', '', (job.get("company", "") or "").lower())
    ti = re.sub(r'[^a-z0-9]', '', (job.get("title", "") or "").lower())
    return f"{co}|{ti}"


def _load_notified() -> dict:
    try:
        with open(NOTIFIED_PATH) as f:
            data = json.load(f)
            data.setdefault("ids", [])
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {"ids": []}


def _save_notified(data: dict):
    data["ids"] = data["ids"][-NOTIFIED_KEEP:]
    with open(NOTIFIED_PATH, "w") as f:
        json.dump(data, f)


def send_pushover(token: str, user: str, *, title: str, message: str,
                  url: str = "", url_title: str = "", priority: int = 0) -> bool:
    body = {"token": token, "user": user, "title": title[:250],
            "message": message[:1024], "priority": priority}
    if url:
        body["url"] = url
        body["url_title"] = url_title or "View posting"
    data = urllib.parse.urlencode(body).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(PUSHOVER_URL, data=data), timeout=15) as r:
            return 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "ignore")
        except Exception:
            detail = ""
        print(f"  ⚠️  Pushover HTTP {e.code}: {detail[:300]}")
        return False
    except Exception as e:
        print(f"  ⚠️  Pushover send failed: {e}")
        return False


def notify_new_jobs(new_jobs: list):
    """Push the not-yet-notified, relevant entries of new_jobs."""
    token = os.environ.get("PUSHOVER_TOKEN")
    user = os.environ.get("PUSHOVER_USER")
    if not token or not user:
        return  # notifications disabled — no creds

    notified = _load_notified()
    seen = set(notified["ids"])
    picks = []
    for job in new_jobs:
        ident = _identity(job)
        if ident in seen or not is_relevant(job):
            continue
        seen.add(ident)
        notified["ids"].append(ident)
        picks.append(job)

    if not picks:
        _save_notified(notified)
        return

    sent = 0
    for job in picks[:MAX_PUSHES_PER_RUN]:
        msg = f"{job.get('company', '?')} — {job.get('location', '')}"
        if job.get("salary"):
            msg += f"\n{job['salary']}"
        send_pushover(
            token, user,
            title=f"🆕 {job.get('title', 'New role')}",
            message=msg,
            url=job.get("url", ""), url_title="Open posting",
        )
        sent += 1

    extra = len(picks) - sent
    if extra > 0:
        send_pushover(token, user, title="🆕 More new roles",
                      message=f"+{extra} more new role(s) — open the dashboard.")
    print(f"  📲 Pushover: notified {sent} role(s)" + (f" (+{extra} summarized)" if extra else ""))
    _save_notified(notified)


def send_test() -> bool:
    """Send a single test push to verify the Pushover setup end-to-end."""
    token = os.environ.get("PUSHOVER_TOKEN")
    user = os.environ.get("PUSHOVER_USER")
    print(f"PUSHOVER_TOKEN: {'(set)' if token else '(MISSING)'}")
    print(f"PUSHOVER_USER:  {'(set)' if user else '(MISSING)'}")
    if not token or not user:
        print("\n❌ Both PUSHOVER_TOKEN and PUSHOVER_USER must be set.\n"
              "   • Locally:  PUSHOVER_TOKEN=… PUSHOVER_USER=… python notify.py --test\n"
              "   • On GitHub: add them as Actions secrets, then run the "
              "'Test Pushover Notification' workflow.")
        return False
    ok = send_pushover(
        token, user,
        title="🧪 Job_Scraper — test notification",
        message="Pushover is wired up correctly. You'll get a ping like this for "
                "each new role the scrapers find.",
        priority=0,
    )
    print("\n✅ Test notification sent — check your phone." if ok
          else "\n❌ Send failed (see the error above — usually a wrong token or user key).")
    return ok


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # let emoji print on Windows too
    except Exception:
        pass
    raise SystemExit(0 if send_test() else 1)
