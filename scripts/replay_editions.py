"""Replay every sent edition through today's rules and score the result.

Each day is rebuilt with only the editions sent before it, so nothing leaks from
the future. Sent files come from the registered records and from
data/reference_editions/index.json, which holds editions from before registration.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from bank_intel.publication.delivery import record_send_time
from bank_intel.publication.history_import import import_distributed
from bank_intel.publication.identity import canonical, publisher_article_id
from bank_intel.publication.reconcile import read_sent_html
from bank_intel.publication.workflow import build

REPORTS = ROOT / 'data' / 'processed' / 'reports'
REFERENCE = ROOT / 'data' / 'reference_editions'


def sent_editions() -> dict:
    """Day to (sent file, send time) for every edition with its bytes on disk."""
    found = {}
    index = REFERENCE / 'index.json'
    if index.is_file():
        for day, entry in json.loads(index.read_text(encoding='utf-8')).items():
            found[day] = (REFERENCE / entry['file'], entry['sent_at'])
    for record in REPORTS.glob('20*/window_1d/**/SENT_EDITION.json'):
        entry = json.loads(record.read_text(encoding='utf-8'))
        attachment = Path(entry.get('attachment') or '')
        if attachment.is_file() and entry.get('sent_at'):
            found[entry['date']] = (attachment, entry['sent_at'])
    return found


def stories(day: str, editions: dict) -> dict | None:
    if day in editions:
        out = collections.defaultdict(set)
        for row in read_sent_html([editions[day][0]]):
            out[row['headline']].add(canonical(row['source_url']))
        return dict(out)
    published = REPORTS / day / 'window_1d' / 'PUBLISHED_URLS.json'
    if published.is_file():
        record = json.loads(published.read_text(encoding='utf-8-sig'))
        return {e['headline']: {canonical(u) for u in e.get('source_urls', [])} for e in record['events']}
    return None


def history(day: str, root: Path, editions: dict) -> None:
    if root.exists():
        shutil.rmtree(root)
    for earlier, (path, sent_at) in editions.items():
        if earlier < day:
            record = import_distributed(path, root, earlier, 'replay', True)
            # An unknown send time stays unknown; late-news rules then ask for review.
            if sent_at:
                record_send_time(record, sent_at, 'replay')
    for folder in REPORTS.glob('20*/window_1d'):
        earlier = folder.parent.name
        if earlier < day and earlier not in editions and (folder / 'PUBLISHED_URLS.json').is_file():
            target = root / earlier / 'window_1d'
            target.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(folder / 'PUBLISHED_URLS.json', target / 'PUBLISHED_URLS.json')


def replay_day(day: str, out: Path, editions: dict) -> dict | None:
    folder = REPORTS / day / 'window_1d'
    audit, inventory = folder / 'stakeholder_brief_v2_audit.json', folder / f'Daily Financial Sector News Brief {day} Articles.csv'
    truth = stories(day, editions)
    if not (audit.is_file() and inventory.is_file() and truth):
        return None
    root = out / 'history' / day
    history(day, root, editions)
    manifest, _ = build(date_iso=day, audit=audit, inventory=inventory, output=out / day, sent=[], sent_report_root=root,
                        css_file=ROOT / 'src/bank_intel/publication/news_style.css', source_status=None, model_project_root=ROOT)
    ledger = json.loads((out / day / 'publication_decisions.json').read_text(encoding='utf-8'))
    by_url = {a['source_url']: a for a in ledger['articles']}
    by_id = {publisher_article_id(a['source_url']): a for a in ledger['articles']}
    auto = review = missed = absent = 0
    for urls in truth.values():
        decisions = {(by_url.get(u) or by_id.get(publisher_article_id(u)) or {}).get('decision') for u in urls} - {None}
        if not decisions:
            absent += 1
        elif 'INCLUDE' in decisions:
            auto += 1
        elif 'REVIEW' in decisions:
            review += 1
        else:
            missed += 1
    wanted = set().union(*truth.values())
    wanted_ids = {publisher_article_id(u) for u in wanted} - {''}
    extra = [e['headline'] for e in manifest['events']
             if not any(canonical(s['source_url']) in wanted or publisher_article_id(s['source_url']) in wanted_ids for s in e['sources'])]
    (out / day / 'truth.json').write_text(json.dumps({'urls': sorted(wanted), 'ids': sorted(wanted_ids), 'stories': len(truth)}, indent=1), encoding='utf-8')
    return {'day': day, 'sent': len(truth), 'auto': auto, 'review': review, 'missed': missed, 'not_in_inventory': absent,
            'shown': len(manifest['events']), 'extra': len(extra), 'review_total': ledger['counts'].get('REVIEW', 0), 'extra_detail': extra}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', type=Path, required=True, help='Short path: replay folders are deep')
    args = parser.parse_args()
    editions = sent_editions()
    rows = [r for r in (replay_day(f.name, args.out, editions) for f in sorted(REPORTS.glob('20*')) if f.is_dir()) if r]
    (args.out / 'summary.json').write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding='utf-8')
    keys = ('sent', 'auto', 'review', 'missed', 'not_in_inventory', 'shown', 'extra', 'review_total')
    for row in rows:
        print({k: row[k] for k in ('day',) + keys})
    print('TOTAL', {k: sum(r[k] for r in rows) for k in keys})


if __name__ == '__main__':
    main()
