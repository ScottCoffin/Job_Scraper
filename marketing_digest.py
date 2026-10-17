"""Daily email integration for the existing scraper. Dry-run by default.

No dashboard output, Pushover, triage, or previous scraped files are used.
Network scraping runs in bounded child processes; SMTP is a separate opt-in.
"""
import argparse
import csv
import hashlib
import html
import io
import json
import logging
import math
import os
from pathlib import Path
import re
import smtplib
import ssl
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urlsplit

import scrape_jobs as scraper

UTC = timezone.utc
ROOT = Path(__file__).resolve().parent
STATE_VERSION = 1
STATES = re.compile(r"\b(?:" + "|".join(scraper._US_STATE_NAMES) + r")\b", re.I)
STATE_CODES = ('AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN '
               'MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC')
CITY_STATE = re.compile(r",\s*(?:" + "|".join(STATE_CODES.split()) + r")\b")
REMOTE = re.compile(r"\b(?:remote|work from home|fully distributed)\b", re.I)
NOT_REMOTE = re.compile(
    r"\b(?:hybrid|onsite|on[ -]site|in[ -]office|non[ -]remote|not remote|no remote|"
    r"remote\s+(?:not|unavailable)|remote\s+(?:work\s+)?(?:is\s+)?not|"
    r"no work from home|not eligible for remote|not a remote)\b", re.I)


def positive_hours(value):
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError('lookback_hours must be a positive finite number') from None
    if isinstance(value, bool) or not math.isfinite(result) or not 0 < result <= 8760:
        raise ValueError('lookback_hours must be positive, finite and at most 8760')
    return result


def load_config(path, override=None):
    # Deliberately do not fall back to config.example.json on a missing/bad file.
    c = json.loads(Path(path).read_text(encoding='utf-8'))
    d = c['marketing_digest']
    d['lookback_hours'] = positive_hours(override if override not in (None, '') else d['lookback_hours'])
    if not d['sources'] or len(set(d['sources'])) != len(d['sources']) or not set(d['sources']) <= {'linkedin', 'indeed'}:
        raise ValueError('sources must be a nonempty unique list of linkedin / indeed')
    if d['date_only_policy'] != 'conservative_utc':
        raise ValueError('Only conservative_utc date-only policy is supported')
    for key, low, high in [('max_results_per_query', 10, 100), ('source_timeout_seconds', 30, 900)]:
        v = d[key]
        if type(v) is not int or not low <= v <= high:
            raise ValueError(f'{key} must be an integer in [{low}, {high}]')
    if d['max_results_per_query'] % 10:
        raise ValueError('max_results_per_query must be a multiple of 10')
    for key in ('include', 'exclude'):
        values = c['keywords'][key]
        if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
            raise ValueError(f'keywords.{key} must contain nonempty strings')
    if not c['keywords']['include']:
        raise ValueError('keywords.include cannot be empty')
    for source in d['sources']:
        terms, geos = c['search_terms'][source], c['locations'][source]
        if not terms or len(terms) > 24 or any(not isinstance(t, str) or not t.strip() for t in terms):
            raise ValueError('Provide 1–24 nonempty queries per source')
        if not geos or len(geos) > 8:
            raise ValueError('Provide 1–8 geographies per source')
        for geo in geos:
            if not isinstance(geo.get('location'), str) or not geo['location'].strip():
                raise ValueError('Every geography needs location text')
        if source == 'linkedin' and not any(g.get('remote') is True for g in geos):
            raise ValueError('LinkedIn needs an explicit remote geography')
        if source == 'indeed' and not any(g['location'].lower() == 'remote' for g in geos):
            raise ValueError('Indeed needs a Remote location query (without is_remote filter)')
    return c


def matches_title(title, config):
    def contains(terms):
        return any(re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', title, re.I) for term in terms)
    return contains(config['keywords']['include']) and not contains(config['keywords']['exclude'])


def location_allowed(job):
    loc = str(job.get('location') or '')
    evidence = ' '.join(str(job.get(k) or '') for k in ('title', 'location', 'work_arrangement'))
    evidence = re.sub(r'[-_]+', ' ', evidence)
    remote = not NOT_REMOTE.search(evidence) and (
        REMOTE.search(evidence) or job.get('is_remote') is True or job.get('remote_query') is True)
    if remote:
        return True
    if re.search(r'\b(?:united states(?: of america)?|usa)\b|(?:^|,\s*)US(?:$|[, (])', loc, re.I):
        return True
    # Do not treat a foreign hybrid job as remote, or country codes CA / IN as states.
    stripped = re.sub(r'\bnew mexico\b', '', loc.lower())
    if scraper._NON_US_COUNTRY_RE.search(stripped) or any(c in stripped for c in scraper.NON_US_COUNTRIES_MULTI):
        return False
    # Georgia alone is ambiguous (country/state); require an explicit US label or city+GA.
    state_text = re.sub(r'\bgeorgia\b', '', loc, flags=re.I)
    return bool(STATES.search(state_text) or CITY_STATE.search(loc))


def posting_precision(value, now, hours):
    """Return a display precision, or None. Never use first_seen / updated_at.

    Date-only intervals use UTC calendar days as a stated convention, not a
    fabricated timestamp. Accept only when the entire elapsed part of that
    calendar date is within the window. Future dates and naive times fail.
    """
    if now.tzinfo is None:
        raise ValueError('now must be timezone-aware')
    now = now.astimezone(UTC)
    start = now - timedelta(hours=hours)
    if not isinstance(value, str) or not value:
        return None
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            day = datetime.strptime(value, '%Y-%m-%d').replace(tzinfo=UTC)
            if start <= day <= now:
                return 'date only; exact time unavailable; UTC calendar convention'
            return None
        posted = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if posted.tzinfo is None:
            return None
        if start <= posted.astimezone(UTC) <= now:
            return 'timestamp with timezone'
    except (ValueError, OverflowError):
        pass
    return None


def safe_url(value):
    if not isinstance(value, str) or re.search(r'[\x00-\x20<>"\\]', value):
        return ''
    try:
        u = urlsplit(value)
        if u.scheme.lower() in ('http', 'https') and u.hostname and not u.username and not u.password:
            _ = u.port
            return value
    except ValueError:
        pass
    return ''


def identities(job):
    keys = []
    for key in ('url', 'direct_url'):
        if safe_url(job.get(key, '')):
            keys.append(scraper._job_identity(job[key]))
    # Conservative exact content identity bridges boards without fuzzy merging.
    values = [re.sub(r'\s+', ' ', str(job.get(k) or '').strip().casefold())
              for k in ('company', 'title', 'location')]
    if all(values) and values[0] != 'unknown':
        keys.append('content:' + '|'.join(values))
    return {hashlib.sha256(k.encode()).hexdigest() for k in keys}


def select_jobs(rows, config, now, sent):
    selected, seen = [], set(sent)
    counts = dict(raw=len(rows), filtered=0, duplicate_or_sent=0)
    for row in rows:
        precision = posting_precision(row.get('date_posted'), now, config['marketing_digest']['lookback_hours'])
        if (not matches_title(str(row.get('title') or ''), config) or not location_allowed(row)
                or not precision or not safe_url(row.get('url', ''))):
            counts['filtered'] += 1
            continue
        keys = identities(row)
        if keys & seen:
            # Retain aliases for duplicates in this digest, without resending sent jobs.
            for previous in selected:
                if keys & set(previous['_identities']):
                    previous['_identities'] = sorted(set(previous['_identities']) | keys)
                    previous['ats'] = ' / '.join(sorted(set(previous['ats'].split(' / ')) | {row['ats']}))
                    break
            seen.update(keys)
            counts['duplicate_or_sent'] += 1
            continue
        seen.update(keys)
        selected.append({**row, 'posted_precision': precision, '_identities': sorted(keys)})
    return selected, counts


def load_state(path, allow_missing=False):
    path = Path(path)
    if not path.exists():
        if allow_missing:
            return {'version': STATE_VERSION, 'sent': {}}
        raise ValueError('Sent state missing. Explicit initialization is required; cache may have been evicted.')
    state = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(state, dict) or state.get('version') != STATE_VERSION
            or not isinstance(state.get('sent'), dict)):
        raise ValueError('Malformed sent ledger; refusing to discard history')
    for key, value in state['sent'].items():
        if not re.fullmatch(r'[0-9a-f]{64}', key) or not isinstance(value, str):
            raise ValueError('Malformed ledger entry')
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('Malformed ledger timestamp')
    return state


def write_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, sort_keys=True), encoding='utf-8')
    tmp.replace(path)


def collect_source(source, config):
    d = config['marketing_digest']
    health = {}
    common = dict(terms=config['search_terms'][source], geos=config['locations'][source],
                  strict=True, health=health, filter_titles=False)
    if source == 'linkedin':
        rows, _ = scraper._linkedin_search(
            lookback_seconds=math.ceil(d['lookback_hours'] * 3600),
            max_results=d['max_results_per_query'], **common)
    else:
        # JobSpy sometimes logs errors and returns a partial/empty dataframe
        # instead of raising. Count those errors without recording server text.
        errors = []
        class ErrorCounter(logging.Handler):
            def emit(self, record):
                if record.levelno >= logging.ERROR:
                    errors.append(1)
        counter = ErrorCounter()
        logger = logging.getLogger('JobSpy:Indeed')
        logger.addHandler(counter)
        try:
            rows = scraper._scrape_jobspy_board(
                label='Indeed', site_name='indeed', hours_old=math.ceil(d['lookback_hours']),
                results_wanted=d['max_results_per_query'], prev_basename='unused_digest', **common)
        finally:
            logger.removeHandler(counter)
        health['failed'] += len(errors)
    status = 'unavailable' if not health['raw'] else ('partial' if health['failed'] else 'ok')
    return {'source': source, 'status': status, 'health': health, 'jobs': rows,
            'note': 'Empty/unrecognized responses are unavailable, not confirmed zero. Query caps may limit coverage.'}


def collect(config, config_path, output):
    results = []
    for source in config['marketing_digest']['sources']:
        dest = output / f'{source}.json'
        dest.unlink(missing_ok=True)
        try:
            subprocess.run([
                sys.executable, str(ROOT / 'marketing_digest.py'), '--config', str(config_path),
                '--lookback-hours', str(config['marketing_digest']['lookback_hours']),
                '--worker', source, '--worker-output', str(dest)],
                check=True, timeout=config['marketing_digest']['source_timeout_seconds'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            result = json.loads(dest.read_text())
        except (subprocess.SubprocessError, OSError, ValueError):
            result = {'source': source, 'status': 'unavailable', 'jobs': [],
                      'note': 'Source failed, exceeded timeout, or returned invalid data.'}
        results.append(result)
    return results


def validate_results(results, expected_sources):
    if not isinstance(results, list) or [r.get('source') for r in results] != expected_sources:
        raise ValueError('Source result set does not match configured sources')
    for r in results:
        if r.get('status') not in ('ok', 'partial', 'unavailable') or not isinstance(r.get('jobs'), list):
            raise ValueError('Malformed source result')
        if any(not isinstance(j, dict) for j in r['jobs']):
            raise ValueError('Malformed job row')
    if all(r['status'] == 'unavailable' for r in results):
        raise RuntimeError('All sources unavailable. No successful empty-jobs digest will be sent.')


def csv_text(jobs):
    out = io.StringIO()
    fields = ['title', 'company', 'location', 'work_arrangement', 'remote_query', 'is_remote',
              'date_posted', 'posted_precision', 'ats', 'url', 'salary']
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for j in jobs:
        # Avoid spreadsheet formula execution when opening the attachment.
        writer.writerow({k: ("'" + str(j.get(k, '')) if str(j.get(k, '')).lstrip().startswith(('=', '+', '-', '@'))
                            else j.get(k, '')) for k in fields})
    return out.getvalue()


def render(jobs, results, counts, now, hours, warnings):
    start = now - timedelta(hours=hours)
    lines = [f'Marketing jobs: {len(jobs)} new matching openings',
             f'Run window: {start.isoformat()} through {now.isoformat()} ({hours:g} hours).',
             'USA OR explicitly remote. Remote does NOT mean worldwide eligible; check each application.',
             'Date-only postings use UTC calendar dates; exact times and source timezone are unavailable.',
             'Conservative filtering misses ambiguous dates. Results are bounded, not exhaustive.',
             f'Candidates: {counts["raw"]}; excluded: {counts["filtered"]}; duplicate/already sent: {counts["duplicate_or_sent"]}.']
    lines += ['WARNING: ' + w for w in warnings]
    lines += [f'Source {r["source"]}: {r["status"]}; {r.get("health", {})}; {r.get("note", "")}' for r in results]
    if not jobs:
        lines.append('No new qualifying jobs in available source data (not a claim that no jobs exist).')
    text = '\n'.join(lines) + '\n'
    body = '<html><body><h1>Marketing job digest</h1>' + ''.join(f'<p>{html.escape(line)}</p>' for line in lines)
    for j in jobs:
        arrangement = j.get('work_arrangement') or ('Remote search result; verify eligibility' if j.get('remote_query') else
                      ('Remote flag from source; verify eligibility' if j.get('is_remote') else 'Arrangement not supplied'))
        detail = [j.get('title', ''), j.get('company', ''), j.get('location', ''), arrangement,
                  f'Posted: {j["date_posted"]} ({j["posted_precision"]})', f'Source: {j["ats"]}']
        if j.get('salary'):
            detail.append('Salary: ' + str(j['salary']))
        text += '\n' + '\n'.join(str(x) for x in detail) + '\n' + safe_url(j['url']) + '\n'
        body += '<section>' + ''.join(f'<p>{html.escape(str(x))}</p>' for x in detail)
        body += f'<a href="{html.escape(safe_url(j["url"]), quote=True)}">Apply / review restrictions</a></section>'
    return text, body + '</body></html>', csv_text(jobs)


def smtp_settings(env):
    """Read settings only in explicit --send mode on the user's future runner."""
    required = ['SMTP_HOST', 'SMTP_PORT', 'SMTP_SECURITY', 'SMTP_USERNAME', 'SMTP_PASSWORD', 'MAIL_FROM', 'MAIL_TO']
    if any(not env.get(k) for k in required):
        raise ValueError('Missing required SMTP settings; see setup guide for secret names')
    settings = {k: env[k] for k in required}
    if settings['SMTP_SECURITY'] not in ('ssl', 'starttls'):
        raise ValueError('SMTP_SECURITY must be ssl or starttls; plaintext is forbidden')
    settings['SMTP_PORT'] = int(settings['SMTP_PORT'])
    if not 1 <= settings['SMTP_PORT'] <= 65535:
        raise ValueError('Invalid SMTP port')
    for key in ('MAIL_FROM', 'MAIL_TO'):
        if not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', settings[key]):
            raise ValueError('Supply one plain email address per sender/recipient setting')
    return settings


def send_smtp(settings, text, body, attachment):
    message = EmailMessage()
    message['Subject'] = 'Daily marketing job digest'
    message['From'], message['To'] = settings['MAIL_FROM'], settings['MAIL_TO']
    message.set_content(text)
    message.add_alternative(body, subtype='html')
    message.add_attachment(attachment.encode('utf-8'), maintype='text', subtype='csv', filename='marketing-jobs.csv')
    context = ssl.create_default_context()
    if settings['SMTP_SECURITY'] == 'ssl':
        client = smtplib.SMTP_SSL(settings['SMTP_HOST'], settings['SMTP_PORT'], timeout=30, context=context)
    else:
        client = smtplib.SMTP(settings['SMTP_HOST'], settings['SMTP_PORT'], timeout=30)
    try:
        client.ehlo()
        if settings['SMTP_SECURITY'] == 'starttls':
            client.starttls(context=context)
            client.ehlo()
        client.login(settings['SMTP_USERNAME'], settings['SMTP_PASSWORD'])
        if client.send_message(message):
            raise RuntimeError('SMTP rejected a recipient; ledger unchanged')
    finally:
        # A QUIT failure after DATA acceptance must not turn acceptance into retry.
        try:
            client.quit()
        except (OSError, smtplib.SMTPException):
            client.close()


def run_digest(config, results, state_path, output, now, *, dry_run=True,
               initialize_state=False, settings=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'health.json').write_text(json.dumps(
        [{k: v for k, v in r.items() if k != 'jobs'} for r in results], indent=2))
    validate_results(results, config['marketing_digest']['sources'])
    missing = not Path(state_path).exists()
    state = load_state(state_path, allow_missing=dry_run or initialize_state)
    warnings = []
    if missing:
        warnings.append('No previous ledger; this is a preview or explicitly initialized run. Duplicates are possible after cache loss.')
    if any(r['status'] != 'ok' for r in results):
        warnings.append('Partial coverage: unavailable sources contribute no jobs; other sources may also have failed queries.')
    if any(r.get('health', {}).get('capped') for r in results):
        warnings.append('One or more query caps were reached; additional jobs may be missing.')
    rows = [j for r in results if r['status'] != 'unavailable' for j in r['jobs']]
    jobs, counts = select_jobs(rows, config, now, state['sent'])
    text, body, attachment = render(jobs, results, counts, now, config['marketing_digest']['lookback_hours'], warnings)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    for filename, content in [('digest.txt', text), ('digest.html', body), ('jobs.csv', attachment)]:
        (output / filename).write_text(content, encoding='utf-8')
    if not dry_run:
        if settings is None:
            raise ValueError('SMTP settings required for sending')
        # Verify writable ledger directory before SMTP without advancing sent records.
        Path(state_path).parent.mkdir(parents=True, exist_ok=True)
        probe = Path(state_path).with_suffix('.probe')
        probe.write_text('write-check')
        probe.unlink()
        send_smtp(settings, text, body, attachment)
        for j in jobs:
            for identity in j['_identities']:
                state['sent'][identity] = now.isoformat()
        try:
            write_state(state_path, state)
        except OSError as exc:
            raise RuntimeError('SMTP accepted but local state persistence failed; duplicates possible. Inspect before retrying.') from exc
    return {'dry_run': dry_run, 'jobs': len(jobs), 'counts': counts, 'warnings': warnings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(ROOT / 'config.json'))
    parser.add_argument('--lookback-hours')
    parser.add_argument('--state', default='.digest/state/sent.json')
    parser.add_argument('--output', default='.digest/preview')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--send', action='store_true')
    mode.add_argument('--dry-run', action='store_true')
    parser.add_argument('--initialize-state', action='store_true')
    parser.add_argument('--worker', choices=['linkedin', 'indeed'], help=argparse.SUPPRESS)
    parser.add_argument('--worker-output', help=argparse.SUPPRESS)
    args = parser.parse_args()
    config = load_config(args.config, args.lookback_hours)
    if args.worker:
        Path(args.worker_output).write_text(json.dumps(collect_source(args.worker, config)))
        return
    # State and SMTP configuration prerequisites fail before collection or delivery.
    load_state(args.state, allow_missing=not args.send or args.initialize_state)
    settings = smtp_settings(os.environ) if args.send else None
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    results = collect(config, Path(args.config).resolve(), output)
    print(json.dumps({'source_health': [{k: v for k, v in r.items() if k != 'jobs'} for r in results]}))
    print(json.dumps(run_digest(config, results, args.state, output, now,
                               dry_run=not args.send, initialize_state=args.initialize_state, settings=settings)))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # No SMTP/server exception text (may include private addresses) in CI logs.
        print(f'Digest failed ({type(error).__name__}); no success claimed. Check health/config/state and setup guide.', file=sys.stderr)
        if isinstance(error, RuntimeError) and 'SMTP accepted' in str(error):
            print('WARNING: SMTP accepted but state failed; duplicates possible.', file=sys.stderr)
        sys.exit(1)
