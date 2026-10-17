"""Offline tests. ALL jobs, dates, SMTP settings and endpoints below are synthetic.
No live scraping, credential environment reads, or email delivery is performed.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
import json
from pathlib import Path
import smtplib
import subprocess
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pandas as pd
import pytest
import yaml

import marketing_digest as m
import scrape_jobs as s

NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def config():
    return m.load_config(ROOT / 'config.json')


@pytest.fixture
def job():
    return dict(title='Marketing intern', company='Synthetic Company', location='Austin, TX',
                url='https://www.linkedin.com/jobs/view/123/', date_posted='2026-10-17T09:00:00Z',
                ats='LinkedIn', salary='')


@pytest.fixture(autouse=True)
def no_live_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('Live network is forbidden in synthetic tests')
    monkeypatch.setattr(s, 'urlopen', blocked)
    monkeypatch.setattr(m.smtplib, 'SMTP', blocked)
    monkeypatch.setattr(m.smtplib, 'SMTP_SSL', blocked)


def results(job=None, status='ok'):
    return [{'source': 'linkedin', 'status': status, 'jobs': [job] if job else [], 'health': {'raw': 1}},
            {'source': 'indeed', 'status': 'ok', 'jobs': [], 'health': {'raw': 1}}]


@pytest.mark.parametrize('title', ['Marketing Intern', 'VP Marketing', 'Growth Marketing Associate',
    'Product Marketing Manager', 'Brand Marketing Director', 'Content Marketing Writer',
    'Performance Marketing Lead', 'Digital Marketing Trainee', 'Email Marketing Specialist',
    'Lifecycle Marketing', 'CRM Marketing Analyst', 'SEO specialist', 'Paid Media Analyst',
    'Paid Search Intern', 'Social Media Manager', 'Demand Generation Director', 'Marketing Operations',
    'Brand Manager', 'CRM Manager'])
def test_marketing_positives(config, title):
    assert m.matches_title(title, config)


@pytest.mark.parametrize('title', ['Sales Associate', 'Growth Engineer', 'Content Moderator',
    'Toxicologist', 'Environmental Scientist', 'Clinical Research', 'SEOlogist', 'CRM Database Administrator'])
def test_off_field_negatives(config, title):
    assert not m.matches_title(title, config)


def test_configurable_exclusion(config):
    config['keywords']['exclude'] = ['director']
    assert not m.matches_title('Marketing Director', config)
    assert m.matches_title('Marketing Intern', config)
    assert not s._build_title_re([]).search('Marketing intern')
    assert s.role_is_relevant('Marketing Intern')
    assert not s._EXCLUDE_COMPANY_TERMS


@pytest.mark.parametrize('loc,remote,expected', [
    ('Austin, TX', None, True), ('Texas', None, True), ('Hybrid - Austin, TX', None, True),
    ('On-site, California', None, True), ('Indianapolis, IN', None, True), ('New Mexico', None, True),
    ('Washington, DC', None, True), ('United States', None, True), ('US', None, True),
    ('Toronto, Canada', None, False), ('Mumbai, India', None, False), ('CA', None, False),
    ('IN', None, False), ('IndianaTech, Canada', None, False), ('California, United Kingdom', None, False),
    ('Hybrid - Berlin, Germany', None, False), ('Hybrid - London, UK', True, False),
    ('Remote - UK residents only', None, True), ('Germany', True, True), ('Worldwide Remote', None, True),
    ('Remote - India only', None, True), ('Berlin, Germany - non-remote', None, False),
    ('Berlin, Germany - not remote', True, False), ('Remote not available - UK', None, False),
    ('No remote work - India', None, False), ('Remote work is not allowed - Germany', True, False),
    ('Work from home - Canada only', None, True), ('Georgia', None, False), ('', None, False),
])
def test_locations(loc, remote, expected):
    assert bool(m.location_allowed({'location': loc, 'is_remote': remote})) == expected


@pytest.mark.parametrize('state', s._US_STATE_NAMES)
def test_all_state_names(state):
    assert m.location_allowed({'location': f'City, {state}, United States'})


@pytest.mark.parametrize('code', m.STATE_CODES.split())
def test_state_abbreviation_boundaries(code):
    assert m.location_allowed({'location': f'City, {code}'})
    assert not m.location_allowed({'location': f'City, {code}XXXX'})


@pytest.mark.parametrize('value,hours,expected', [
    ('2026-10-16T12:00:00Z',24,True), ('2026-10-16T11:59:59Z',24,False),
    ('2026-10-15T12:00:00Z',48,True), ('2026-10-15T11:59:59Z',48,False),
    ('2026-10-16T17:30:00+05:30',24,True), ('2026-10-16T07:59:59-04:00',24,False),
    ('2026-10-17T12:00:01Z',24,False), ('2026-10-17T12:00:00',24,False),
    ('2026-10-17',24,True), ('2026-10-16',24,False), ('2026-10-16',48,True),
    ('2026-10-15',48,False), ('2026-10-18',72,False), ('2026-10-17',1,False),
    ('2026-10-15',72,True), ('2026-02-30',24,False), ('bad date',24,False),
    ('',24,False), (None,24,False), ('yesterday',24,False),
])
def test_posting_dates(value, hours, expected):
    assert bool(m.posting_precision(value, NOW, hours)) == expected


def test_date_only_no_fabricated_midnight(config, job):
    job['date_posted'] = '2026-10-17'
    selected, _ = m.select_jobs([job], config, NOW, {})
    assert selected[0]['date_posted'] == '2026-10-17'
    assert 'exact time unavailable' in selected[0]['posted_precision']
    assert 'T00:00' not in json.dumps(selected)


def test_never_first_seen_or_updated(config, job):
    job.pop('date_posted')
    for key in ('first_seen', 'updated_at', 'dateFetched', 'scraped_at'):
        job[key] = NOW.isoformat()
    assert m.select_jobs([job], config, NOW, {})[0] == []


@pytest.mark.parametrize('value', [0, -1, 'nan', 'inf', 'oops', True, 1000000])
def test_invalid_window(value):
    with pytest.raises(ValueError):
        m.positive_hours(value)


def test_exact_timestamp_preserved(config, job):
    job['date_posted'] = '2026-10-16T17:30:00+05:30'
    assert m.select_jobs([job], config, NOW, {})[0][0]['date_posted'] == job['date_posted']


def test_dedupe_within_and_across(config, job):
    other = {**job, 'url': job['url'] + '?tracking=x'}
    cross = {**job, 'url':'https://www.indeed.com/viewjob?jk=synthetic1', 'ats':'Indeed'}
    selected, count = m.select_jobs([job, other, cross], config, NOW, {})
    assert len(selected) == 1 and count['duplicate_or_sent'] == 2
    assert 'Indeed' in selected[0]['ats']
    assert m.select_jobs([cross], config, NOW, selected[0]['_identities'])[0] == []
    assert len(m.select_jobs([job, {**job,'url':'https://www.linkedin.com/jobs/view/124/',
                                 'title':'Product Marketing Lead'}],config,NOW,{})[0]) == 2


def test_missing_and_corrupt_state(tmp_path):
    path = tmp_path / 'sent.json'
    with pytest.raises(ValueError, match='missing'):
        m.load_state(path)
    assert m.load_state(path, True)['sent'] == {}
    for data in ('oops', '[]', '{"version":1,"sent":[]}', '{"version":1,"sent":{"bad":"bad"}}'):
        path.write_text(data)
        with pytest.raises(ValueError):
            m.load_state(path, True)
        assert path.read_text() == data


def test_dry_run_never_connects_or_advances(config, job, tmp_path, monkeypatch):
    monkeypatch.setattr(m, 'send_smtp', Mock(side_effect=AssertionError('NO SMTP')))
    path = tmp_path / 'sent.json'
    summary = m.run_digest(config, results(job), path, tmp_path / 'preview', NOW)
    assert summary['jobs'] == 1 and summary['dry_run']
    assert not path.exists()
    assert 'Marketing intern' in (tmp_path / 'preview/digest.html').read_text()
    m.send_smtp.assert_not_called()


def test_smtp_failure_does_not_advance(config, job, tmp_path, monkeypatch):
    path = tmp_path / 'sent.json'
    m.write_state(path, {'version':1,'sent':{}})
    before = path.read_bytes()
    monkeypatch.setattr(m, 'send_smtp', Mock(side_effect=smtplib.SMTPException('synthetic failure')))
    with pytest.raises(smtplib.SMTPException):
        m.run_digest(config, results(job), path, tmp_path, NOW, dry_run=False, settings={})
    assert path.read_bytes() == before


def test_success_marks_after_acceptance(config, job, tmp_path, monkeypatch):
    path = tmp_path / 'sent.json'
    def fake_send(*args):
        assert not path.exists()
    monkeypatch.setattr(m, 'send_smtp', fake_send)
    m.run_digest(config, results(job), path, tmp_path, NOW, dry_run=False, initialize_state=True, settings={})
    assert m.load_state(path)['sent']
    assert m.run_digest(config, results(job), path, tmp_path, NOW)['jobs'] == 0


def test_persistence_failure_warns(config, job, tmp_path, monkeypatch):
    monkeypatch.setattr(m, 'send_smtp', Mock())
    monkeypatch.setattr(m, 'write_state', Mock(side_effect=OSError('synthetic disk failure')))
    with pytest.raises(RuntimeError, match='SMTP accepted'):
        m.run_digest(config, results(job), tmp_path/'sent',tmp_path,NOW,
                     dry_run=False,initialize_state=True,settings={})
    m.send_smtp.assert_called_once()


def test_missing_state_prevents_send(config, job, tmp_path, monkeypatch):
    monkeypatch.setattr(m, 'send_smtp', Mock())
    with pytest.raises(ValueError):
        m.run_digest(config, results(job),tmp_path/'sent',tmp_path,NOW,dry_run=False,settings={})
    m.send_smtp.assert_not_called()


def test_all_failed_partial_and_genuine_zero(config, job, tmp_path, monkeypatch):
    unavailable = [{'source':s, 'status':'unavailable','jobs':[job]} for s in ['linkedin','indeed']]
    monkeypatch.setattr(m, 'send_smtp', Mock())
    with pytest.raises(RuntimeError, match='All sources'):
        m.run_digest(config, unavailable,tmp_path/'sent',tmp_path,NOW, dry_run=False, initialize_state=True, settings={})
    m.send_smtp.assert_not_called()
    partial = results(job)
    partial[1] = unavailable[1]
    result = m.run_digest(config,partial,tmp_path/'sent',tmp_path,NOW)
    assert result['jobs'] == 1
    assert any('Partial coverage' in w for w in result['warnings'])
    zero = m.run_digest(config,results({**job,'title':'Toxicologist'}),tmp_path/'sent',tmp_path,NOW)
    assert zero['jobs'] == 0
    assert 'No new qualifying jobs' in (tmp_path/'digest.txt').read_text()


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'data:text/html,x', '//example.invalid/path',
    'https://user:pass@example.invalid', 'https://example.invalid/"bad', 'https://example.invalid/\npath'])
def test_unsafe_urls(url):
    assert m.safe_url(url) == ''


def test_html_and_full_csv(config, job, tmp_path):
    job.update(title='Marketing <script>alert(1)</script>', company='<img src=x onerror=x>',
               location='Remote - UK residents only', salary='GBP 40000')
    many = [{**job,'url':f'https://www.linkedin.com/jobs/view/{i}/','company':f'{job["company"]} {i}'} for i in range(125)]
    m.run_digest(config,[{**r,'jobs':many if r['source']=='linkedin' else []} for r in results()],tmp_path/'sent',tmp_path,NOW)
    body=(tmp_path/'digest.html').read_text()
    assert '<script>' not in body and '<img src=x' not in body
    assert '&lt;script&gt;' in body and 'UK residents only' in body
    assert body.count('Apply / review restrictions') == 125
    assert len(list(__import__('csv').DictReader((tmp_path/'jobs.csv').open()))) == 125
    assert 'GBP 40000' in body
    assert "'=HYPERLINK" in m.csv_text([{**job,'title':'=HYPERLINK("x")'}])


def synthetic_card(title='Marketing Intern', location='Remote - UK only', ident='999'):
    return f'''<li><div data-entity-urn="urn:li:jobPosting:{ident}"><h3 class="base-search-card__title">{title}</h3>
    <h4 class="base-search-card__subtitle">Synthetic</h4><span class="job-search-card__location">{location}</span>
    <time datetime="2026-10-17T10:30:00+01:00"></time></div></li>'''


@pytest.mark.parametrize('hours',[24,48,72,1.5])
def test_overrides_reach_queries_and_remote_searches(config, hours, monkeypatch):
    config = m.load_config(ROOT/'config.json',hours)
    config['search_terms'] = {'linkedin':['marketing'], 'indeed':['marketing']}
    urls, calls = [], []
    monkeypatch.setattr(s.time, 'sleep', lambda _:None)
    monkeypatch.setattr(s, 'fetch', lambda url, **kw: urls.append(url) or synthetic_card())
    result = m.collect_source('linkedin',config)
    assert result['status'] == 'ok'
    query=[parse_qs(urlsplit(u).query) for u in urls]
    assert all(q['f_TPR'] == [f'r{int(hours*3600)}'] for q in query)
    assert any(q.get('f_WT') == ['2'] and q['location'] == ['Worldwide'] and 'geoId' not in q for q in query)
    assert any(q.get('geoId') == ['103644278'] for q in query)
    import jobspy
    def fake(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame([dict(title='Marketing intern',company='Synthetic',location='UK',
            date_posted='2026-10-17',job_url='https://www.indeed.com/viewjob?jk=synthetic',is_remote=True)])
    monkeypatch.setattr(jobspy,'scrape_jobs',fake)
    result=m.collect_source('indeed',config)
    assert result['status'] == 'ok' and result['jobs'][0]['is_remote'] is True
    assert all(c['hours_old'] == __import__('math').ceil(hours) for c in calls)
    assert all(not set(c) & {'is_remote','job_type','easy_apply'} for c in calls)
    assert {c['country_indeed'] for c in calls if c['location']=='Remote'} >= {'USA','UK','India'}
    assert all(c['proxies'] is None for c in calls)


def test_adapters_empty_dont_load_previous(config, monkeypatch):
    import jobspy
    monkeypatch.setattr(s.time,'sleep',lambda _:None)
    monkeypatch.setattr(s,'fetch',lambda *a,**kw:'<html>captcha</html>')
    monkeypatch.setattr(jobspy,'scrape_jobs',lambda **kw:pd.DataFrame())
    monkeypatch.setattr(s,'_load_prev_jobs',Mock(side_effect=AssertionError('NO stale fallback')))
    for source in ('linkedin','indeed'):
        r=m.collect_source(source,config)
        assert r['status']=='unavailable' and r['jobs']==[]
    s._load_prev_jobs.assert_not_called()


def test_raw_nonmatching_is_successful_zero(config, monkeypatch):
    monkeypatch.setattr(s.time,'sleep',lambda _:None)
    monkeypatch.setattr(s,'fetch',lambda *a,**kw:synthetic_card('Toxicologist'))
    r=m.collect_source('linkedin',config)
    assert r['status']=='ok'
    assert m.select_jobs(r['jobs'],config,NOW,{})[0]==[]


def test_query_failure_is_partial(config, monkeypatch):
    import jobspy
    monkeypatch.setattr(s.time,'sleep',lambda _:None)
    def fake(**kw):
        if kw['location']=='Remote': raise RuntimeError('synthetic blocked')
        return pd.DataFrame([dict(title='Marketing intern',company='Synthetic',location='USA',
            date_posted='2026-10-17',job_url='https://www.indeed.com/viewjob?jk=synthetic')])
    monkeypatch.setattr(jobspy,'scrape_jobs',fake)
    r=m.collect_source('indeed',config)
    assert r['status']=='partial' and r['health']['failed']>0


def test_worker_timeout_is_unavailable(config,tmp_path,monkeypatch):
    monkeypatch.setattr(m.subprocess,'run',Mock(side_effect=subprocess.TimeoutExpired('synthetic',1)))
    collected=m.collect(config,ROOT/'config.json',tmp_path)
    assert all(r['status']=='unavailable' for r in collected)


@pytest.mark.parametrize('security',['ssl','starttls'])
def test_smtp_tls_multipart_acceptance(security,monkeypatch):
    # Synthetic settings only; never read os.environ or connect.
    settings=m.smtp_settings(dict(SMTP_HOST='smtp.example.invalid',SMTP_PORT='465' if security=='ssl' else '587',
        SMTP_SECURITY=security,SMTP_USERNAME='synthetic-user',SMTP_PASSWORD='synthetic-test-only',
        MAIL_FROM='sender@example.invalid',MAIL_TO='recipient@example.invalid'))
    client=Mock()
    client.send_message.return_value={}
    constructor=Mock(return_value=client)
    monkeypatch.setattr(m.smtplib,'SMTP_SSL' if security=='ssl' else 'SMTP',constructor)
    m.send_smtp(settings,'plain','<p>HTML</p>','title\nSynthetic\n')
    if security=='starttls': client.starttls.assert_called_once()
    else: assert constructor.call_args.kwargs['context'].verify_mode == m.ssl.CERT_REQUIRED
    msg=client.send_message.call_args.args[0]
    parsed=BytesParser(policy=policy.default).parsebytes(msg.as_bytes())
    assert parsed.get_body(preferencelist=('plain',)).get_content().strip()=='plain'
    assert parsed.get_body(preferencelist=('html',)).get_content().strip()=='<p>HTML</p>'
    assert list(parsed.iter_attachments())[0].get_filename()=='marketing-jobs.csv'
    client.send_message.return_value={'recipient@example.invalid':(550,b'synthetic rejection')}
    with pytest.raises(RuntimeError): m.send_smtp(settings,'plain','html','csv')


def test_missing_config_fails_closed(tmp_path):
    with pytest.raises(FileNotFoundError): m.load_config(tmp_path/'missing')
    p=tmp_path/'bad.json'; p.write_text('{}')
    with pytest.raises(KeyError): m.load_config(p)


def test_workflow_gates_and_semantics():
    # BaseLoader uses YAML 1.2-like string keys so 'on' is not parsed as True.
    w=yaml.load((ROOT/'.github/workflows/marketing_digest.yml').read_text(),Loader=yaml.BaseLoader)
    assert w['on']['schedule'][0]['cron']=='17 8 * * *'
    assert w['on']['workflow_dispatch']['inputs']['dry_run']['default']=='true'
    assert w['permissions']=={'contents':'read'}
    assert w['concurrency']['cancel-in-progress']=='false'
    assert 'ENABLE_MARKETING_EMAIL_DIGEST' in w['jobs']['digest']['if']
    steps=w['jobs']['digest']['steps']
    preview=next(x for x in steps if x.get('name','').startswith('Dry-run'))
    assert 'SMTP_PASSWORD' not in preview.get('env',{})
    assert any(x.get('uses')=='actions/cache/save@v4' for x in steps)
    assert any(x.get('id')=='confirm' and x['with']['lookup-only']=='true' for x in steps)


def test_remote_negation_variants():
    for text in ('Not a remote role - Canada', 'No work-from-home - UK', 'Non-remote - India',
                 'Remote is not available - Germany'):
        assert not m.location_allowed({'location':text,'remote_query':True})


def test_remote_query_evidence_survives_duplicate(config,monkeypatch):
    config['search_terms']['linkedin']=['marketing']
    monkeypatch.setattr(s.time,'sleep',lambda _:None)
    monkeypatch.setattr(s,'fetch',lambda *a,**kw:synthetic_card(location='UK residents only'))
    r=m.collect_source('linkedin',config)
    assert r['jobs'][0]['remote_query'] is True
    assert m.location_allowed(r['jobs'][0])
    assert r['jobs'][0]['location']=='UK residents only'


def test_jobspy_logged_failure_not_success(config,monkeypatch):
    import jobspy
    import logging
    monkeypatch.setattr(s.time,'sleep',lambda _:None)
    def partial(**kw):
        logging.getLogger('JobSpy:Indeed').error('synthetic request failure after first page')
        return pd.DataFrame([dict(title='Marketing intern',job_url='https://www.indeed.com/viewjob?jk=synthetic')])
    monkeypatch.setattr(jobspy,'scrape_jobs',partial)
    assert m.collect_source('indeed',config)['status']=='partial'


def test_timestamp_from_jobspy_preserved(config,monkeypatch):
    import jobspy
    monkeypatch.setattr(s.time,'sleep',lambda _:None)
    monkeypatch.setattr(jobspy,'scrape_jobs',lambda **kw:pd.DataFrame([dict(
        title='Marketing intern',job_url='https://www.indeed.com/viewjob?jk=synthetic',
        date_posted='2026-10-17T10:30:00+01:00')]))
    assert m.collect_source('indeed',config)['jobs'][0]['date_posted']=='2026-10-17T10:30:00+01:00'


def test_main_dry_run_never_reads_environment(config,job,tmp_path,monkeypatch):
    # Exercise the CLI control path, not just rendering; fake collection only.
    monkeypatch.setattr(m,'collect',lambda *a: results({**job,'date_posted':(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()}))
    monkeypatch.setattr(m,'smtp_settings',Mock(side_effect=AssertionError('Dry-run must not read SMTP settings')))
    monkeypatch.setattr(m.sys,'argv',['marketing_digest.py','--dry-run','--config',str(ROOT/'config.json'),
                                    '--state',str(tmp_path/'sent'),'--output',str(tmp_path/'preview')])
    m.main()
    assert 'Marketing intern' in (tmp_path/'preview/digest.html').read_text()
    assert not (tmp_path/'sent').exists()


def test_collection_command_propagates_override(config,tmp_path,monkeypatch):
    config['marketing_digest']['lookback_hours']=72
    commands=[]
    def fake(cmd,**kw):
        commands.append((cmd,kw))
        source=cmd[cmd.index('--worker')+1]
        Path(cmd[cmd.index('--worker-output')+1]).write_text(json.dumps(
            {'source':source,'status':'ok','jobs':[]}))
    monkeypatch.setattr(m.subprocess,'run',fake)
    m.collect(config,ROOT/'config.json',tmp_path)
    assert all(cmd[cmd.index('--lookback-hours')+1]=='72' for cmd,kw in commands)
    assert all(kw['timeout']==420 for cmd,kw in commands)


def test_plaintext_smtp_forbidden():
    settings=dict(SMTP_HOST='smtp.example.invalid',SMTP_PORT='25',SMTP_SECURITY='none',
        SMTP_USERNAME='synthetic',SMTP_PASSWORD='synthetic-test-only',MAIL_FROM='from@example.invalid',MAIL_TO='to@example.invalid')
    with pytest.raises(ValueError,match='plaintext'):
        m.smtp_settings(settings)


def test_corrupt_state_prevents_send(config,job,tmp_path,monkeypatch):
    path=tmp_path/'sent'; path.write_text('{broken')
    sender=Mock(); monkeypatch.setattr(m,'send_smtp',sender)
    with pytest.raises(ValueError):
        m.run_digest(config,results(job),path,tmp_path,NOW,dry_run=False,initialize_state=True,settings={})
    sender.assert_not_called()
    assert path.read_text()=='{broken'


def test_source_toggles_and_invalid_config(config,tmp_path):
    config['marketing_digest']['sources']=['indeed']
    path=tmp_path/'config.json'; path.write_text(json.dumps(config))
    assert m.load_config(path)['marketing_digest']['sources']==['indeed']
    config['marketing_digest']['sources']=[]
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError): m.load_config(path)


def test_linkedin_overreturn_is_bounded(config,monkeypatch):
    config['search_terms']['linkedin']=['marketing']
    config['locations']['linkedin']=[{'name':'Remote','location':'Worldwide','remote':True}]
    config['marketing_digest']['max_results_per_query']=20
    monkeypatch.setattr(s.time,'sleep',lambda _:None)
    def fetch(url,**kwargs):
        start=int(parse_qs(urlsplit(url).query)['start'][0])
        return ''.join(synthetic_card(ident=str(start*100+i)) for i in range(25))
    monkeypatch.setattr(s,'fetch',fetch)
    r=m.collect_source('linkedin',config)
    assert len(r['jobs'])==20 and r['health']['capped']==1
