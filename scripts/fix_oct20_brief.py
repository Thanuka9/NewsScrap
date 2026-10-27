"""Final October 20 editorial correction: cross-day carry-forwards and real follow-up.

Overwrite scripts/fix_oct20_brief.py; run from News Scrap project root.
This is a one-day report-only correction (no scraping, ML, or classifier changes).
Precondition: the earlier October 20 correction has already been applied.
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path.cwd()
TODAY = '2026-10-20'
OUT = ROOT / 'data/processed/reports' / TODAY / 'window_1d'
YESTERDAY = ROOT / 'data/processed/reports/2026-10-19/window_1d'
AUDIT = OUT / 'stakeholder_brief_v2_audit.json'
MANIFEST = OUT / 'report_manifest.json'
REPORT = OUT / f'Daily Financial Sector News Brief {TODAY}.html'
ALIAS = OUT / 'supervisory_digest.html'
REVIEW = OUT / 'stakeholder_category_review.csv'
ARTICLES = OUT / f'Daily Financial Sector News Brief {TODAY} Articles.csv'
PAST_AUDIT = YESTERDAY / 'stakeholder_brief_v2_audit.json'
BASE = ROOT / 'scripts'

needed = [AUDIT, MANIFEST, REPORT, ALIAS, REVIEW, ARTICLES, PAST_AUDIT,
          BASE / 'build_financial_brief_v2.py', BASE / 'brief_semantic_guard.py']
missing = [str(x) for x in needed if not x.is_file()]
if missing:
    raise SystemExit('[STOP] Required existing project files not found:\n' + '\n'.join(missing))

sys.path.insert(0, str(BASE))
import build_financial_brief_v2 as builder
import brief_semantic_guard as guard

current = json.loads(AUDIT.read_text(encoding='utf-8-sig'))
past = json.loads(PAST_AUDIT.read_text(encoding='utf-8-sig'))
manifest = json.loads(MANIFEST.read_text(encoding='utf-8-sig'))
if current.get('coverage_dates') != [TODAY] or past.get('coverage_dates') != ['2026-10-19']:
    raise SystemExit('[STOP] Source audits are not the expected October 19/20 daily windows')
if manifest.get('publication_date') != TODAY or current.get('qa', {}).get('status') != 'PASS':
    raise SystemExit('[STOP] October 20 report must be the previously QA-approved correction')
rows = copy.deepcopy(current['audit'])
if not all(any(marker in r.get('headline', '') and r['section'] != 'EXCLUDE' for r in rows)
           for marker in ('Nearly a third of banks AI-free', 'Microfinance lenders to accept',
                          'Bank of China Colombo branch')):
    raise SystemExit('[STOP] Previous October 20 editorial correction not detected; apply that first')
if len({r['source_url'] for r in rows}) != len(rows):
    raise SystemExit('[STOP] Duplicate article URLs exist in current audit')

# Import only two explicitly reviewed October 19 stories that were NOT in its
# distributed brief. Never repeat a previously published event automatically.
past_events = {s['source_url'] for e in past.get('events', [])
               for s in (e.get('sources') or [])}

def import_yesterday(headline, section, category, importance, reason, key, summary):
    candidates = [r for r in past['audit'] if r['headline'] == headline]
    if len(candidates) != 1:
        raise RuntimeError('[STOP] Prior-day source not uniquely found: ' + headline)
    item = copy.deepcopy(candidates[0])
    if item['source_url'] in past_events:
        raise RuntimeError('[STOP] Already included in Oct 19 report: ' + headline)
    if item['source_url'] in {r['source_url'] for r in rows}:
        item = next(r for r in rows if r['source_url'] == item['source_url'])
    else:
        rows.append(item)
    item.update(section=section, category=category, importance=importance,
                reason=reason, event_key=key, summary=summary)
    return item

banc = import_yesterday(
    'Union Assurance renews bancassurance deal with Nations Trust Bank',
    'BANKING_SECTOR_DEVELOPMENTS', 'BANCASSURANCE_DISTRIBUTION', 'LOW',
    'PREVIOUSLY_UNREPORTED_BANK_INSURANCE_DISTRIBUTION_AGREEMENT',
    'NTB_UNION_ASSURANCE_BANCASSURANCE_RENEWAL_20261019',
    'Union Assurance renewed its bancassurance agreement with Nations Trust Bank '
    'on 14 October, continuing the insurer\'s access to the bank\'s distribution '
    'network for life-insurance referrals. Previously unreported 19 October coverage.'
)

PORT_KEY = 'COLOMBO_PORT_LOGISTICS_JV_RESET_AND_NEW_PPP_20261020'
PORT_SUMMARY = (
    'Following termination of the earlier China Merchants–SLPA–Access Engineering '
    'logistics-hub joint venture, SLPA announced on 20 October that it intends '
    'to pursue a replacement Port of Colombo logistics hub through a new '
    'competitively procured public-private partnership. The former venture\'s '
    'shareholders are expected to recover approximately US$29.8 million of '
    'their equity contributions. The replacement is proposed, not yet awarded; '
    'no specific bank credit exposure is established by these reports.'
)
port_prior = import_yesterday(
    'China Merchants, SLPA, Access scrap $ 392 m logistics hub',
    'MACRO_SYSTEMIC_CONTEXT', 'INFRASTRUCTURE_INVESTMENT_RISK', 'LOW',
    'PREVIOUSLY_UNREPORTED_PROJECT_TERMINATION_CONTEXT', PORT_KEY, PORT_SUMMARY
)

new_port_titles = (
    'Port of Colombo: SLPA to develop logistics hub, upending JV plans',
    'SLPA to develop new logistics hub at Port of Colombo',
    'SLPA to develop new logistics hub at Port of Colombo after earlier JV scrapped',
)
port_today = [r for r in rows if r.get('headline') in new_port_titles]
if len(port_today) != 3 or len({r['source_url'] for r in port_today}) != 3:
    raise RuntimeError('[STOP] Expected three distinct October 20 port follow-ups')
for item in port_today:
    item.update(section='MACRO_SYSTEMIC_CONTEXT',
                category='INFRASTRUCTURE_INVESTMENT_RISK',
                importance='LOW',
                reason='RELATED_FOLLOWUP_TO_PREVIOUSLY_UNREPORTED_PROJECT_TERMINATION',
                event_key=PORT_KEY, summary=PORT_SUMMARY)

# Rebuild source lineage with the production builder and persistent semantic QA.
retained = [r for r in rows if r['section'] != 'EXCLUDE']
events = guard.after_group(builder.group_events(retained), rows)
by_key = {e['event_key']: e for e in events if e.get('event_key')}
if not all(key in by_key for key in (PORT_KEY, banc['event_key'])):
    raise RuntimeError('[STOP] New events were dropped during grouping')

# Lead the multi-day story with the *new* development, not yesterday's news.
port_event = by_key[PORT_KEY]
lead_title = 'SLPA to develop new logistics hub at Port of Colombo after earlier JV scrapped'
lead = next(x for x in port_today if x['headline'] == lead_title)
port_event.update(
    headline='Port of Colombo logistics hub: old JV scrapped; new PPP proposed',
    summary=PORT_SUMMARY,
    publication_date=TODAY,
    section='MACRO_SYSTEMIC_CONTEXT',
    category='INFRASTRUCTURE_INVESTMENT_RISK',
    importance='LOW',
)
port_event['sources'] = sorted(port_event['sources'],
    key=lambda x: (x['source_url'] != lead['source_url'],
                   x['source_url'] != port_prior['source_url'], x['source_name']))
port_event['member_count'] = len(port_event['sources'])
bank_event = by_key[banc['event_key']]
bank_event.update(summary=banc['summary'], publication_date='2026-10-19')

# Provide genuine, specific 'Why it matters' text, not generic ML rationale.
why_base = builder.why_it_matters
why_extra = {
    'BANCASSURANCE_DISTRIBUTION':
        'Tracks banks\' distribution of third-party insurance products and related customer-conduct considerations.',
    'INFRASTRUCTURE_INVESTMENT_RISK':
        'Provides context for large-investment execution and infrastructure financing; no bank exposure is confirmed.',
}
builder.why_it_matters = lambda category: why_extra.get(category, why_base(category))

qa = builder.validate_outputs(audit=rows, retained=retained, events=events)
semantic_issues = guard.semantic_checks(rows, events)
if semantic_issues:
    qa['failures'] = list(qa.get('failures') or []) + semantic_issues
    qa['status'] = 'FAIL'
if qa['status'] != 'PASS':
    raise RuntimeError('[STOP] QA failed without changing any files: ' + '; '.join(qa['failures']))

# Prevent duplicate stories, contaminated grouping, or unstated carry-forwards.
assert len([e for e in events if e['event_key'] == PORT_KEY]) == 1
assert len([e for e in events if e['event_key'] == banc['event_key']]) == 1
assert {r['source_url'] for r in (port_today + [port_prior])} == {
    s['source_url'] for s in port_event['sources']}
assert len({s['source_url'] for e in events for s in e['sources']}) == len(retained)
assert all(e['event_key'] != 'BOC_RMB_CLEARING_LAUNCH_20261019' or
           e['publication_date'] == '2026-10-19' for e in events)

html = builder.render_html(end_date=date.fromisoformat(TODAY), days=1,
                           events=events, audit_count=len(rows))
if any(token in html for token in ('&amp;amp;', 'â€™', 'â€œ', '�')):
    raise RuntimeError('[STOP] Invalid rendered character encoding')
from bs4 import BeautifulSoup
soup = BeautifulSoup(html, 'html.parser')
titles = [x.get_text(' ', strip=True) for x in soup.select('article h3')]
for text in ('Nations Trust Bank', 'Port of Colombo logistics hub:',
             'Bank of China Colombo branch', 'Nearly a third of banks AI-free',
             'Microfinance lenders to accept public deposits mooted'):
    if sum(text.lower() in x.lower() for x in titles) != 1:
        raise RuntimeError('[STOP] Required item missing or duplicated: ' + text)
if len(soup.select('article')) != len(events):
    raise RuntimeError('[STOP] Rendered story count disagrees with event count')

# Update all interdependent report artifacts, retaining reviewer fields.
payload = copy.deepcopy(current)
now = datetime.now(ZoneInfo('Asia/Colombo')).isoformat()
payload.update(status='SUCCESS', generated_at=now,
               article_count=len(rows), retained_article_count=len(retained),
               excluded_count=len(rows)-len(retained),
               consolidated_event_count=len(events),
               sections={s: sum(e['section'] == s for e in events)
                         for s in builder.SECTION_ORDER},
               audit=rows, events=events, qa=qa,
               prior_day_recovered_event_keys=sorted([
                   banc['event_key'], PORT_KEY, 'BOC_RMB_CLEARING_LAUNCH_20261019']),
               editorial_correction='2026-10-20 editorial v2: cross-day carry-forward and port follow-up')
manifest.update(status='SUCCESS', generated_at=now,
                article_count=len(rows), retained_article_count=len(retained),
                consolidated_event_count=len(events), qa=qa,
                editorial_correction=payload['editorial_correction'])

# Make the script repeatable: it will not append a source twice; backups are
# always created before files are updated. A failed write rolls back all files.
paths = [AUDIT, MANIFEST, REPORT, ALIAS, REVIEW, ARTICLES]
optional_qa_html = OUT / f'Daily Financial Sector News Brief {TODAY} QA.html'
if optional_qa_html.is_file():
    paths.append(optional_qa_html)
backup = ROOT / 'data/integration_backups' / ('oct20_final_crossday_' + now.replace(':', '-'))
backup.mkdir(parents=True, exist_ok=False)
for path in paths:
    shutil.copy2(path, backup / path.name)
try:
    builder.write_review_csv(REVIEW, rows)
    builder.write_article_audit_csv(ARTICLES, rows)
    AUDIT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    REPORT.write_text(html, encoding='utf-8')
    ALIAS.write_bytes(REPORT.read_bytes())
    if optional_qa_html.is_file():
        optional_qa_html.write_text(html, encoding='utf-8')
    if REPORT.read_bytes() != ALIAS.read_bytes():
        raise RuntimeError('Production alias differs from report')
except Exception:
    for path in paths:
        shutil.copy2(backup / path.name, path)
    raise

print('='*78)
print('OCTOBER 20 FINAL CROSS-DAY CORRECTION: PASS')
print('='*78)
print(f"Reviewed: {len(rows)} | Retained: {len(retained)} | Distinct stories: {len(events)}")
print('Added: Nations Trust Bank bancassurance renewal (19 Oct; unreported)')
print('Combined: 19 Oct port project cancellation + three 20 Oct follow-ups')
print('Preserved: CBSL AI risk, microfinance proposal, Bank of China RMB clearing')
print('All source URLs retained; no full collection or ML rerun')
print('Report:', REPORT)
print('Backup:', backup)
