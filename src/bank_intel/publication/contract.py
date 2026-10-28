"""One release contract for manual execution and both schedulers."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from .ledger import validate_outputs

CONTRACT = 'NEWS_RELEASE_V1'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def resolve(root, value):
    path = Path(value)
    return path if path.is_absolute() else Path(root) / path


def sent_record(root: Path, date: str):
    folder = root / 'data/processed/reports' / date / 'window_1d'
    records = sorted(folder.rglob('SENT_EDITION.json')) if folder.exists() else []
    if not records:
        if (folder / 'PUBLISHED_URLS.json').exists() or (folder / 'PUBLISHED.ok').exists():
            return {'status': 'ALREADY_SENT', 'date': date, 'legacy_sent_lock': True}
        return None
    hashes=set()
    for path in records:
        obj=read(path)
        if obj.get('date') != date or obj.get('state') != 'DELIVERED_CONFIRMED_BY_OPERATOR':
            raise RuntimeError('INVALID_SENT_RECORD: '+str(path))
        artifact=resolve(root,obj['attachment'])
        if sha(artifact) != obj['attachment_sha256']:
            raise RuntimeError('SENT_ARCHIVE_HASH_MISMATCH: '+str(artifact))
        hashes.add(obj['attachment_sha256'])
    if len(hashes)!=1:raise RuntimeError('CONFLICTING_SENT_REVISIONS')
    return {'status':'ALREADY_SENT','date':date,'attachment_sha256':next(iter(hashes)),
            'sent_record':str(records[0]),'sent_at':read(records[0]).get('sent_at','')}


def validate_release(root: Path, date: str) -> dict:
    root=Path(root).resolve()
    sent=sent_record(root,date)
    if sent:return sent
    folder=root/'data/processed/reports'/date/'window_1d'
    report=read(folder/'report_manifest.json')
    if report.get('publication_date')!=date:raise RuntimeError('RELEASE_DATE_MISMATCH')
    status=report.get('status')
    if status in ('HOLD_FOR_REVIEW','REVIEW_REQUIRED'):
        return {'status':'REVIEW_REQUIRED','date':date,'issues':report.get('release_issues',[])}
    if status!='QA_PASS_NOT_DELIVERED':raise RuntimeError('UNSUPPORTED_RELEASE_STATUS: '+str(status))
    candidate_path=resolve(root,report['integrated_manifest']);candidate=read(candidate_path)
    qa=read(resolve(root,report['release_qa']))
    ledger_path=candidate_path.parent/candidate['decision_ledger'];ledger=read(ledger_path)
    named=resolve(root,report['final_attachment']);alias=folder/'supervisory_digest.html'
    digest=sha(named)
    if candidate.get('date')!=date or candidate.get('status')!='QA_PASS_NOT_DELIVERED' or candidate.get('issues'):
        raise RuntimeError('CANDIDATE_NOT_READY')
    if qa.get('release_status')!='PASS' or qa.get('issues'):
        raise RuntimeError('INDEPENDENT_QA_NOT_PASSED')
    if any(value!=digest for value in (report.get('sha256'),candidate.get('sha256'),qa.get('report_sha256'),sha(alias))):
        raise RuntimeError('RELEASE_ATTACHMENT_HASH_MISMATCH')
    if sha(ledger_path)!=candidate.get('decision_ledger_sha256') or qa.get('decision_ledger_sha256')!=candidate['decision_ledger_sha256']:
        raise RuntimeError('RELEASE_LEDGER_HASH_MISMATCH')
    issues=validate_outputs(ledger,candidate,named.read_text(encoding='utf-8'))
    if issues:raise RuntimeError('RELEASE_CONTENT_MISMATCH: '+str(issues))
    if candidate.get('input_snapshot'):
        snapshot=read(candidate_path.parent/candidate['input_snapshot'])
        for entry in snapshot['files']:
            if sha(candidate_path.parent/entry['path'])!=entry['sha256']:
                raise RuntimeError('RELEASE_INPUT_SNAPSHOT_CHANGED: '+entry['path'])
    count=len(ledger['articles']);included=ledger['counts'].get('INCLUDE',0);events=len(candidate['events'])
    for key,value in [('article_count',count),('retained_article_count',included),('consolidated_event_count',events)]:
        if report.get(key)!=value:raise RuntimeError('RELEASE_COUNT_MISMATCH: '+key)
    return {'contract':CONTRACT,'status':'QA_PASS_NOT_DELIVERED','date':date,'qa_status':'PASS',
            'report_manifest':str(folder/'report_manifest.json'),'named_report':str(named),'production_alias':str(alias),
            'article_count':count,'retained_article_count':included,'consolidated_event_count':events,
            'policy_version':report.get('policy_version',''),'sha256':digest,'revision_id':candidate.get('revision_id','')}
