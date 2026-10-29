"""Replay a sent edition in a separate folder and measure exact editorial agreement.

Signed replay checks reproducibility. Automatic replay measures remaining editorial
work; it is not an independent estimate of future model accuracy.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from bank_intel.publication.contract import sent_record
from bank_intel.publication.identity import canonical
from bank_intel.publication.workflow import build


def verify_replay_inputs(revision, record):
    """Reject damaged frozen evidence instead of measuring against changed truth."""
    sent=json.loads(Path(record['sent_record']).read_text(encoding='utf-8-sig'))
    ledger=revision/'publication_decisions.json'
    if hashlib.sha256(ledger.read_bytes()).hexdigest()!=sent['decision_ledger_sha256']:
        raise RuntimeError('SENT_LEDGER_HASH_MISMATCH')
    snapshot=json.loads((revision/'input_snapshot.json').read_text(encoding='utf-8-sig'))
    for entry in snapshot['files']:
        path=(revision/entry['path']).resolve()
        if not path.is_relative_to(revision.resolve()):
            raise RuntimeError('INVALID_SNAPSHOT_PATH')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=entry['sha256']:
            raise RuntimeError('FROZEN_REPLAY_INPUT_CHANGED: '+entry['path'])
    return sent


def compare(expected, actual):
    def urls(event):
        return {canonical(s['source_url']) for s in event['sources']}
    wanted=[urls(e) for e in expected['events']]
    observed=[urls(e) for e in actual['events']]
    missed=[e['headline'] for e,u in zip(expected['events'],wanted) if not any(u & v for v in observed)]
    extra=[e['headline'] for e,u in zip(actual['events'],observed) if not any(u & v for v in wanted)]
    sections=[]
    for event,u in zip(expected['events'],wanted):
        matches=[e for e,v in zip(actual['events'],observed) if u & v]
        if any(e['section'] != event['section'] for e in matches):
            sections.append(event['headline'])
    exact={ (frozenset(urls(e)),e['section']) for e in expected['events']} == {
        (frozenset(urls(e)),e['section']) for e in actual['events']}
    return {'expected_events':len(wanted),'actual_events':len(observed),
            'missed_sent_stories':missed,'extra_stories':extra,'section_disagreements':sections,
            'exact_event_membership_and_sections':exact,'review_candidates':actual['counts']['review']}


def replay(root, target, output):
    root=Path(root).resolve();output=Path(output).resolve()
    if output.is_relative_to(root/'data/processed/reports'):
        raise ValueError('Replay output must be outside production reports')
    record=sent_record(root,target)
    if not record or not record.get('sent_record'):
        raise ValueError('A hash-verified sent revision is required')
    revision=Path(record['sent_record']).parent
    sent=verify_replay_inputs(revision,record)
    expected=json.loads((revision/'candidate_edition.json').read_text(encoding='utf-8'))
    if expected['events'] != sent['events']:
        raise RuntimeError('CANDIDATE_DIFFERS_FROM_SENT_EVENTS')
    inputs=revision/'inputs'
    result={'date':target,'sent_sha256':record['attachment_sha256'],
            'scope':'Retrospective agreement with this approved edition; not unseen accuracy.'}
    for mode in ('signed','automatic'):
        manifest,_=build(date_iso=target,audit=inputs/'audit.json',inventory=inputs/'inventory.csv',
            output=output/mode,sent=[],review=(inputs/'editorial_decisions.csv') if mode=='signed' else None,
            source_status=inputs/'source_status',model_project_root=root,
            sent_report_root=root/'data/processed/reports',
            css_file=root/'src/bank_intel/publication/news_style.css')
        result[mode]=compare(expected,manifest)
        actual=json.loads((output/mode/'publication_decisions.json').read_text(encoding='utf-8'))
        prior=json.loads((revision/'publication_decisions.json').read_text(encoding='utf-8'))
        left={a['source_url']:a for a in prior['articles']}
        right={a['source_url']:a for a in actual['articles']}
        result[mode]['exact_article_population']=set(left)==set(right) and len(right)==len(actual['articles'])
        result[mode]['article_count']=len(actual['articles'])
        result[mode]['decision_changes']=[{'headline':a['headline'],'source_url':a['source_url'],
             'expected':left.get(a['source_url'],{}).get('decision'),'actual':a['decision'],
             'expected_reason':left.get(a['source_url'],{}).get('decision_reason'),'actual_reason':a.get('decision_reason')}
             for a in actual['articles'] if left.get(a['source_url'],{}).get('decision')!=a['decision']]
        result[mode]['missing_articles']=sorted(set(left)-set(right))
    # Revalidate the sent hash after both replays.
    assert sent_record(root,target)['attachment_sha256']==record['attachment_sha256']
    (output/'replay_summary.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--project-root',type=Path,default=ROOT)
    args=parser.parse_args()
    result=replay(args.project_root,args.date,args.output)
    print(json.dumps(result,indent=2,ensure_ascii=True))
    raise SystemExit(0 if result['signed']['exact_event_membership_and_sections']
                     and result['signed']['exact_article_population'] and not result['signed']['decision_changes'] else 2)
