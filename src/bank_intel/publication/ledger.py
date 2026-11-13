"""Final publication decisions and mechanical output reconciliation.

The source inventory remains evidence. Only this ledger determines eligibility;
QA validates it and never runs a second editorial selection algorithm.
"""
from collections import Counter
import hashlib
import json
from .identity import canonical, publisher_article_id
from .reconcile import SentHTML


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def make_ledger(work, events, date_iso):
    memberships = {}
    for event in events:
        members = event['members']
        event_id = event['event_signature'] or 'EVENT_' + digest(sorted(canonical(x['article']['source_url']) for x in members))[:24]
        event['event_id'] = event_id
        for item in members:
            memberships[canonical(item['article']['source_url'])] = event_id
    rows = []
    for item in work:
        article, assessment = item['article'], item['assessment']
        url = canonical(article['source_url'])
        row = {
            'article_id': publisher_article_id(url) or 'ARTICLE_' + digest(url)[:24],
            'source_url': url, 'source_name': article['source_name'],
            'headline': article['headline'], 'publication_date': article['publication_date'],
            'published_at': article.get('published_at', ''),
            'first_discovered_at': article.get('first_seen_at', ''),
            'collected_at': article.get('collected_at', ''),
            'legacy_decision': article.get('section', ''),
            'legacy_reason': article.get('reason', ''),
            'decision': assessment['decision'], 'decision_reason': assessment['reason'],
            'section': assessment.get('section', ''), 'category': assessment.get('category', ''),
            'event_id': memberships.get(url, ''),
            'event_signature': assessment.get('event_signature', ''),
            'previous_send_status': assessment.get('previous_send_status', 'UNKNOWN'),
            'prior_reports': assessment.get('prior_reports', []),
            'prior_match': assessment.get('prior_match', []),
            'late_news_status': assessment.get('late_news_status', ''),
            'reviewer': assessment.get('reviewer', ''),
            'evidence_issues': assessment.get('evidence_issues', []),
            'source_evidence_sha256': digest(article),
        }
        row['decision_id'] = digest(row)
        rows.append(row)
    return {'schema': 'PUBLICATION_DECISIONS_V1', 'report_date': date_iso,
            'counts': dict(Counter(r['decision'] for r in rows)), 'articles': rows}


def validate_outputs(ledger, manifest, report):
    issues = []
    from datetime import datetime
    import re
    header=re.search(r'class="date-value">([^<]+)',report)
    try:
        printed=datetime.strptime(header.group(1).strip(),'%A, %d %B %Y').date().isoformat()
    except (ValueError,AttributeError):printed=''
    if printed!=ledger['report_date']:issues.append({'kind':'PRINTED_REPORT_DATE_MISMATCH'})
    rows = ledger['articles']
    by_url = {canonical(r['source_url']): r for r in rows}
    if len(rows) != len(by_url):issues.append({'kind': 'DUPLICATE_LEDGER_URL'})
    for row in rows:
        if row['decision_id'] != digest({k:v for k,v in row.items() if k != 'decision_id'}):
            issues.append({'kind': 'DECISION_HASH_MISMATCH', 'source_url': row['source_url']})
    parser = SentHTML();parser.feed(report)
    expected = manifest['events']
    if len(parser.stories) != len(expected):issues.append({'kind': 'HTML_EVENT_COUNT_MISMATCH'})
    used = []
    event_ids = []
    for event in expected:
        event_ids.append(event['event_id'])
        urls = {canonical(s['source_url']) for s in event['sources']}
        primary=by_url.get(canonical(event['sources'][0]['source_url'])) if event['sources'] else None
        # A verified rewrite prints a new headline; the source headline still ties it to its article.
        source_event={**event,'headline':event.get('source_headline',event.get('headline'))}
        if not primary or any(primary.get(k)!=source_event.get(k) for k in ('section','category','headline','publication_date')):
            issues.append({'kind':'EVENT_PRIMARY_DECISION_MISMATCH','event_id':event['event_id']})
        matches = [s for s in parser.stories if {canonical(u) for u in s['source_urls']} == urls]
        if len(matches) != 1 or matches[0]['headline'] != event['headline'] or matches[0]['summary'] != event['summary'] or matches[0]['publication_date'] != event['publication_date']:
            issues.append({'kind': 'HTML_EVENT_CONTENT_MISMATCH', 'event_id': event['event_id']})
        for url in urls:
            used.append(url)
            row = by_url.get(url)
            if not row or row['decision'] != 'INCLUDE' or row['event_id'] != event['event_id']:
                issues.append({'kind': 'CONTRADICTORY_PUBLICATION_DECISION', 'source_url': url})
    if len(set(event_ids)) != len(event_ids):issues.append({'kind': 'DUPLICATE_EVENT_ID'})
    if len(used) != len(set(used)):issues.append({'kind': 'ARTICLE_IN_MULTIPLE_EVENTS'})
    if set(used) != {u for u,r in by_url.items() if r['decision'] == 'INCLUDE'}:
        issues.append({'kind': 'INCLUDED_ARTICLE_EVENT_MEMBERSHIP_MISMATCH'})
    if len(rows) != manifest['source_row_count']:issues.append({'kind': 'LEDGER_COUNT_MISMATCH'})
    return issues


def template_issues(root, css_file):
    lock=json.loads((root/'template_lock.json').read_text(encoding='utf-8'))
    issues=[]
    for name,expected in lock['sha256'].items():
        path=css_file if name=='news_style.css' else root/name
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            issues.append({'kind':'APPROVED_TEMPLATE_MISMATCH','file':name})
    return issues
