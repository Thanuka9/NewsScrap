"""Import an operator-identified, already distributed HTML edition verbatim."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from .identity import event_signature
from .ledger import digest
from .reconcile import SentHTML


def import_distributed(report: Path, report_root: Path, edition_date: str,
                       operator: str, confirmed: bool):
    if not confirmed or not operator.strip():raise ValueError('Actual distribution confirmation and operator required')
    contents = report.read_text(encoding='utf-8-sig')
    header = re.search(r'class="date-value">([^<]+)', contents)
    if not header:raise ValueError('Missing report date')
    value = header.group(1).strip()
    printed = ''
    # Earlier editions print '19 October 2026'; current ones add the weekday.
    for layout in ('%A, %d %B %Y', '%d %B %Y', '%Y-%m-%d'):
        try:printed = datetime.strptime(value, layout).date().isoformat();break
        except ValueError:continue
    if printed != edition_date:raise ValueError('Printed edition date mismatch')
    parser = SentHTML();parser.feed(contents)
    if not parser.stories or any(not s['summary'] or not s['source_urls'] for s in parser.stories):
        raise ValueError('Incomplete distributed story facts')
    folder = report_root / edition_date / 'window_1d'
    target = folder / 'distributed_archive' / report.name
    fingerprint = hashlib.sha256(report.read_bytes()).hexdigest()
    record = folder / 'SENT_EDITION.json'
    if record.exists():
        previous = json.loads(record.read_text(encoding='utf-8'))
        if previous.get('attachment_sha256') != fingerprint:raise FileExistsError('Different sent edition is already registered')
        return record
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()!=fingerprint:
        raise FileExistsError('Different distributed attachment exists')
    if not target.exists():
        with target.open('xb') as stream:stream.write(report.read_bytes())
    events = []
    for story in parser.stories:
        signature = event_signature(story)
        events.append({**{k:story[k] for k in ('headline','publication_date','summary')},
                       'reported_facts': story['summary'], 'event_signature': signature,
                       'event_id': signature or 'EVENT_' + digest(sorted(story['source_urls']))[:24],
                       'sources': [{'source_url': u} for u in story['source_urls']]})
    payload = {'date': edition_date, 'state': 'DELIVERED_CONFIRMED_BY_OPERATOR',
               'attachment': str(target.resolve()), 'attachment_sha256': fingerprint,
               'source_report': str(report.resolve()), 'confirmed_by': operator,
               'confirmation_evidence': 'Operator identified this file as already distributed',
               'confirmed_at': datetime.now(timezone.utc).isoformat(),
               'sent_at': '', 'sent_time_status': 'NOT_PROVIDED',
               'events': events, 'event_count': len(events),
               'source_urls': sorted({s['source_url'] for e in events for s in e['sources']})}
    for path in (record, folder / 'sent_events.json'):
        with path.open('x', encoding='utf-8') as stream:json.dump(payload,stream,indent=2,ensure_ascii=False)
    return record
