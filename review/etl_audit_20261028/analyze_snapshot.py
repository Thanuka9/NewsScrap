"""Read-only audit of production inputs; writes evidence only beside this script.

Run from the project root with .venv/Scripts/python.exe. No crawling, inference,
approval, release rebuilding or mutation of production data is performed.
"""
from __future__ import annotations
import csv
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))
from bank_intel.publication.identity import canonical
from bank_intel.publication.reconcile import SentHTML
from bank_intel.publication.workflow import load_data, prepare
from bank_intel.publication.ml_trace import read_model_traces
from bank_intel.publication.temporal import load_collection_metadata


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def rows(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stories(path):
    parser = SentHTML()
    parser.feed(path.read_text(encoding='utf-8-sig'))
    return parser.stories


def main():
    report = ROOT / 'data/processed/reports/2026-10-28/window_1d'
    candidate = read(report / 'integrated_edition/candidate_edition.json')
    evidence = {'audit_time': datetime.now(ZoneInfo('Asia/Colombo')).isoformat(),
                'scope': 'Local snapshot; replay current code against stored evidence, not a historical execution replay'}
    downloaded = Path.home() / 'Downloads/Daily Financial Sector News Brief 2026-10-28.html'
    paths = {'downloaded': downloaded, 'named': report / downloaded.name,
             'archive': report / 'distributed_archive' / downloaded.name,
             'approved': report / 'integrated_edition' / downloaded.name,
             'qa_preview': report / 'integrated_edition' / downloaded.name.replace('.html', ' QA.html')}
    evidence['hashes'] = {k: {'path': str(p), 'sha256': sha(p)} for k, p in paths.items()}
    evidence['recorded_hashes'] = {
        'qa': read(report / 'news_release_qa.json')['report_sha256'],
        'approval': read(report / 'integrated_edition/APPROVED_EDITION.json')['attachment_sha256'],
        'sent': read(report / 'SENT_EDITION.json')['attachment_sha256']}
    evidence['release'] = {k: candidate[k] for k in ('status', 'source_row_count', 'event_count', 'model_trace_coverage', 'counts', 'issues')}
    sent_stories = stories(downloaded)
    generated = stories(paths['qa_preview'])
    approved = stories(paths['approved'])
    differences = []
    for item in sent_stories:
        urls = set(map(canonical, item['source_urls']))
        match = next((x for x in generated if urls & set(map(canonical, x['source_urls']))), None)
        old = next((x for x in approved if urls & set(map(canonical, x['source_urls']))), None)
        differences.append({'headline': item['headline'], 'publication_date': item['publication_date'],
            'generated_match': bool(match), 'generated_headline': match['headline'] if match else '',
            'headline_changed': bool(match and item['headline'] != match['headline']),
            'summary_changed': bool(match and item['summary'] != match['summary']),
            'sent_summary_words': len(item['summary'].split()),
            'generated_summary_words': len(match['summary'].split()) if match else None,
            'generated_summary': match['summary'] if match else '',
            'approved_summary': old['summary'] if old else '', 'sent_summary': item['summary'],
            'added_source_urls': sorted(set(map(canonical, item['source_urls'])) - set(map(canonical, match['source_urls'] if match else []))),
            'sent_source_urls': item['source_urls']})
    evidence['story_comparison'] = differences
    trace = rows(report / 'integrated_edition/article_decision_trace.csv')
    by_url = {canonical(x['source_url']): x for x in trace}
    evidence['sent_url_decisions'] = [{
        'headline': s['headline'], 'source_url': canonical(u),
        'decision': by_url.get(canonical(u), {}).get('decision', 'NOT_IN_INVENTORY'),
        'reason': by_url.get(canonical(u), {}).get('decision_reason', '')
    } for s in sent_stories for u in s['source_urls']]
    decisions = rows(report / 'news_material_editorial_decisions.csv')
    evidence['trace'] = {'rows': len(trace), 'unique_canonical_urls': len({canonical(x['source_url']) for x in trace}),
        'counts': {k: dict(Counter(x[k] for x in trace)) for k in ('decision', 'source_name', 'publication_date', 'ml_action', 'late_news_status')},
        'nulls': {k: sum(not x.get(k) for x in trace) for k in ('source_url', 'headline', 'publication_date', 'first_discovered_at', 'published_at', 'collected_at')},
        'signed_decisions_in_final_trace': sum(x['decision_reason'].startswith('SIGNED_EDITORIAL_') for x in trace)}
    evidence['editorial'] = {'rows': len(decisions), 'counts': dict(Counter(x['review_decision'] for x in decisions))}
    coverage = read(report / 'news_coverage_verified.json')
    sources = {}
    for key in ('daily_ft', 'daily_mirror', 'daily_news', 'the_morning', 'island', 'newsfirst', 'ceylon_today'):
        obj = read(ROOT / 'data/source_status' / (key + '.json'))
        sources[key] = {k: obj.get(k) for k in ('execution_status', 'health_status', 'completed_at', 'candidate_urls', 'invalid_extraction', 'fetch_failures', 'failed_discovery_surfaces', 'wp_api_discovery')}
        sources[key]['unresolved_url_count'] = len(obj.get('unresolved_fetch_urls') or [])
        sources[key]['extraction_failures'] = obj.get('extraction_failures')
    evidence['sources'] = sources
    evidence['coverage'] = {k: {f: v.get(f) for f in ('status', 'automatic_issues', 'accepted_issues')} for k, v in coverage['publishers'].items()}
    audit, inventory = Path(candidate['source_audit_path']), Path(candidate['source_inventory_path'])
    data = load_data(audit, inventory)
    dates = sorted({x['publication_date'][:10] for x in data})
    models, warnings = read_model_traces(ROOT, dates)
    metadata = load_collection_metadata(ROOT, dates)
    evidence['model_warnings'] = warnings
    runs = {}
    replay_rows = {}
    for label, review in [('without_editorial', None), ('with_editorial', report / 'news_material_editorial_decisions.csv')]:
        work, groups, counts, earliest = prepare(date_iso='2026-10-28', audit=audit, inventory=inventory,
            sent_paths=[], review_decisions=review, sent_report_root=ROOT / 'data/processed/reports',
            model_traces=models, collection_metadata=metadata)
        runs[label] = {'counts': counts, 'event_count': len(groups), 'window_start': earliest.isoformat(),
            'review_reasons': dict(Counter(x['assessment']['reason'] for x in work if x['assessment']['decision'] == 'REVIEW'))}
        replay_rows[label] = {canonical(x['article']['source_url']): {'headline': x['article']['headline'],
            'decision': x['assessment']['decision'], 'reason': x['assessment']['reason']} for x in work}
    evidence['replay'] = runs
    differences = []
    for url, before in replay_rows['without_editorial'].items():
        after = replay_rows['with_editorial'][url]
        if before['decision'] != after['decision']:
            differences.append({'source_url': url, 'headline': before['headline'], 'before': before['decision'],
                'after': after['decision'], 'before_reason': before['reason'], 'after_reason': after['reason']})
    evidence['replay_changed_decisions'] = differences
    evidence['replay_vs_recorded_differences'] = [x['source_url'] for x in trace if replay_rows['with_editorial'][canonical(x['source_url'])]['decision'] != x['decision']]
    freeze = read(ROOT / 'config/production_freeze.json')
    evidence['freeze'] = {'created_at': freeze['created_at'], 'entries': len(freeze['files']),
        'mismatches': [x['path'] for x in freeze['files'] if sha(ROOT / x['path']).lower() != x['sha256'].lower()]}
    evidence['scheduler'] = read(ROOT / 'data/scheduler/last_run_status.json')
    evidence['runs'] = []
    for path in sorted((ROOT / 'data/run_summaries').glob('news_pipeline_2026102*.json')):
        run = read(path)
        evidence['runs'].append({'file': path.name, 'status': run['status'], 'error': run.get('error'),
            'started_at': run['started_at'], 'completed_at': run['completed_at'], 'collection_skipped': run.get('collection_skipped')})
    evidence['sent_history'] = []
    for day in ('2026-10-26', '2026-10-27', '2026-10-28'):
        for path in (ROOT / 'data/processed/reports' / day).rglob('SENT_EDITION.json'):
            obj = read(path)
            evidence['sent_history'].append({'path': str(path), 'state': obj.get('state'), 'sent_at': obj.get('sent_at'), 'sent_time_status': obj.get('sent_time_status'), 'event_count': obj.get('event_count')})
    (OUT / 'snapshot_evidence.json').write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding='utf-8')
    with (OUT / 'editorial_decision_changes.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=['source_url', 'headline', 'before', 'after', 'before_reason', 'after_reason'])
        writer.writeheader(); writer.writerows(differences)
    print(json.dumps({'replay': runs, 'editorial': evidence['editorial'], 'trace': evidence['trace'],
        'summary_changes': sum(x['summary_changed'] for x in evidence['story_comparison']),
        'added_source_links': sum(len(x['added_source_urls']) for x in evidence['story_comparison']),
        'freeze_mismatches': evidence['freeze']['mismatches'],
        'replay_vs_recorded_differences': evidence['replay_vs_recorded_differences'],
        'sent_history': evidence['sent_history']}, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
