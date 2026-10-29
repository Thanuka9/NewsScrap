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
    reconcile_delivered(folder)
    return record


def reconcile_delivered(folder: Path) -> dict:
    """Tie the delivered edition back to the governed candidate it replaced.

    An operator may correct an approved edition before sending it. Record
    what changed, so the release manifest names the bytes actually sent and
    the earlier editorial decisions the delivery overrode are kept as dated
    corrections (labels must follow the delivered edition, not the draft).
    """
    import csv
    from .identity import canonical
    sent = json.loads((folder / 'SENT_EDITION.json').read_text(encoding='utf-8'))
    delivered = {canonical(u) for u in sent.get('source_urls', [])}
    manifest_path = folder / 'report_manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig')) if manifest_path.is_file() else {}
    candidate_path = Path(manifest['integrated_manifest']) if manifest.get('integrated_manifest') else None
    if not candidate_path or not candidate_path.is_file():
        # Older layout: the candidate sat directly in integrated_edition.
        candidate_path = folder / 'integrated_edition' / 'candidate_edition.json'
    result = {'date': sent['date'], 'delivered_sha256': sent['attachment_sha256'], 'delivered_events': sent['event_count'],
              'candidate': str(candidate_path) if candidate_path.is_file() else None}
    corrections = []
    if candidate_path.is_file():
        candidate = json.loads(candidate_path.read_text(encoding='utf-8'))
        shown = {canonical(s['source_url']) for e in candidate.get('events', []) for s in e.get('sources', [])}
        ledger = json.loads((candidate_path.parent / candidate['decision_ledger']).read_text(encoding='utf-8'))
        decisions = {a['source_url']: a for a in ledger['articles']}
        result.update(candidate_sha256=candidate.get('sha256'), candidate_events=len(candidate.get('events', [])),
                      added_sources=sorted(delivered - shown), removed_sources=sorted(shown - delivered),
                      matches_candidate=candidate.get('sha256') == sent['attachment_sha256'])
        for url in sorted(delivered - shown):
            earlier = decisions.get(url)
            if earlier and earlier['decision'] != 'INCLUDE':
                corrections.append({'source_url': url, 'headline': earlier.get('headline', ''),
                                    'earlier_decision': earlier['decision'], 'earlier_reason': earlier['decision_reason'],
                                    'delivered_decision': 'INCLUDE', 'superseded_at': sent.get('confirmed_at', ''),
                                    'evidence': sent['attachment']})
        for url in sorted(shown - delivered):
            earlier = decisions.get(url, {})
            corrections.append({'source_url': url, 'headline': earlier.get('headline', ''),
                                'earlier_decision': earlier.get('decision', 'INCLUDE'), 'earlier_reason': earlier.get('decision_reason', ''),
                                'delivered_decision': 'EXCLUDE', 'superseded_at': sent.get('confirmed_at', ''),
                                'evidence': sent['attachment']})
    result['editorial_corrections'] = len(corrections)
    if corrections:
        with (folder / 'editorial_corrections_from_delivery.csv').open('w', newline='', encoding='utf-8-sig') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(corrections[0]))
            writer.writeheader(); writer.writerows(corrections)
    (folder / 'delivered_reconciliation.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    if manifest:
        manifest.update(status='DELIVERED', delivered_attachment=sent['attachment'],
                        delivered_sha256=sent['attachment_sha256'], delivered_reconciliation=str(folder / 'delivered_reconciliation.json'))
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    return result
