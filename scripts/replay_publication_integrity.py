"""Offline historical replay; writes only to an isolated validation directory."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from bank_intel.publication.workflow import build
from bank_intel.publication.ledger import validate_outputs
from bank_intel.publication.reconcile import read_sent_html, was_sent
from bank_intel.publication.release import audit_release
from bank_intel.publication.history_import import import_distributed
from bank_intel.publication.delivery import record_send_time
from news_release_gate import prepare_coverage


def replay(output, fixture=None):
    fixture=fixture or ROOT/'tests/fixtures/publication_2026_10_26_27'
    frozen=json.loads((fixture/'fixture_manifest.json').read_text(encoding='utf-8'))
    for name,expected in frozen['files'].items():
        if hashlib.sha256((fixture/name).read_bytes()).hexdigest()!=expected:
            raise ValueError('Frozen evidence changed: '+name)
    output=Path(output).resolve()
    if output.is_relative_to((ROOT/'data/processed/reports').resolve()):
        raise ValueError('Replay cannot write into production reports')
    output.mkdir(parents=True,exist_ok=True)
    # Rebuild the confirmed sent register exactly as production holds it,
    # including the actual distribution time that governs late-news checks.
    history=output/'sent_history'
    if history.exists():shutil.rmtree(history)
    record=import_distributed(fixture/'prior_reference.html',history,'2026-10-26','replay fixture',True)
    sent_at=frozen.get('distribution_times',{}).get('prior_reference.html')
    if sent_at:record_send_time(record,sent_at,'replay fixture')
    manifest,report=build(date_iso='2026-10-27',audit=fixture/'audit.json',
        inventory=fixture/'inventory.csv',output=output,sent=[],sent_report_root=history,
        css_file=ROOT/'src/bank_intel/publication/news_style.css',source_status=fixture/'source_status',
        model_traces=json.loads((fixture/'model_traces.json').read_text(encoding='utf-8')),
        collection_metadata=json.loads((fixture/'collection_metadata.json').read_text(encoding='utf-8')))
    # Coverage preparation runs against frozen collector evidence, not live state.
    status_root=output/'frozen_inputs/data/source_status'
    status_root.mkdir(parents=True,exist_ok=True)
    for path in (fixture/'source_status').glob('*.json'):shutil.copyfile(path,status_root/path.name)
    from datetime import date
    coverage=prepare_coverage(date(2026,10,27),output/'frozen_inputs',output/'coverage_evidence.json')
    audit_release(report=report,manifest_path=output/'candidate_edition.json',
        inventory=fixture/'inventory.csv',coverage=coverage,out=output/'release_qa.json',report_date='2026-10-27')
    ledger=json.loads((output/'publication_decisions.json').read_text(encoding='utf-8'))
    prior=read_sent_html([fixture/'prior_reference.html'])
    repeated=[e['event_id'] for e in manifest['events'] if any(was_sent({**e,'body':e['summary'],'source_url':s['source_url']},prior) for s in e['sources'])]
    sent27=read_sent_html([fixture/'sent_reference_27.html'])
    comparison=[]
    for event in sent27:
        candidates=[r for r in ledger['articles'] if was_sent({**r,'body':''},[event])]
        comparison.append({'sent_headline':event['headline'],
                           'decisions':[{'source_url':r['source_url'],'decision':r['decision'],'reason':r['decision_reason']} for r in candidates]})
    result={'purpose':'Pipeline validation only; does not rebuild or publish an edition',
            'input_articles':len(ledger['articles']),'decision_counts':ledger['counts'],
            'displayed_events':len(manifest['events']),
            'prior_edition_repeats':len(repeated),
            'output_inconsistencies':validate_outputs(ledger,manifest,report.read_text(encoding='utf-8')),
            'outside_window':sum(e['publication_date'] not in ('2026-10-26','2026-10-27') for e in manifest['events']),
            'review_reasons':dict(Counter(r['decision_reason'] for r in ledger['articles'] if r['decision']=='REVIEW')),
            'sent_editorial_reference_comparison':comparison,
            'release_status':json.loads((output/'release_qa.json').read_text(encoding='utf-8'))['release_status']}
    (output/'replay_result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'review/publication_integrity_replay')
    result=replay(p.parse_args().output)
    print(json.dumps({k:v for k,v in result.items() if k!='sent_editorial_reference_comparison'},indent=2))
