"""Labelling datasheets: a blind check against the editor, and batches from the archive.

check:   articles the editor has already decided, shown without their answers;
         the answers go to a separate key file used only for scoring.
archive: unlabelled archive articles, most uncertain first, for labelling.
score:   agreement between a filled check sheet and its key.
Labels written by an assistant are marked as such and are never used for testing.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import random
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
csv.field_size_limit(10 ** 8)
SHEETS = ROOT / 'data/training/labelling'
SHEET_FIELDS = ['item_id', 'date', 'source', 'headline', 'lead', 'keep', 'section', 'note']
SECTION_CODES = {'K': 'KEY_BANKING_SUPERVISORY', 'B': 'BANKING_SECTOR_DEVELOPMENTS', 'O': 'OTHER_FINANCIAL_SECTOR',
                 'M': 'MONETARY_MARKETS_EXTERNAL', 'X': 'MACRO_SYSTEMIC_CONTEXT'}


def lead(text: str, words: int = 45) -> str:
    from bank_intel.publication.evidence import clean_lead
    return ' '.join(clean_lead(text or '').split()[:words])


def read(path: Path) -> list[dict]:
    with path.open(encoding='utf-8-sig') as stream:
        return list(csv.DictReader(stream))


def write(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def check_sheet(name: str, size: int, seed: int) -> None:
    stage_a = ROOT / 'data/training/datasets/supervisory_relevance_v9_editorial_stage_a'
    sections = {r['source_url']: r['target_relevance_label']
                for split in ('train', 'validation') for r in read(ROOT / f'data/training/datasets/stage_b_section_v1/{split}.csv')}
    pool = [r for split in ('train', 'validation') for r in read(stage_a / f'{split}.csv')]
    rng = random.Random(seed)
    kept = [r for r in pool if r['target_relevance_label'] == 'KEEP' and r['source_url'] in sections]
    dropped = [r for r in pool if r['target_relevance_label'] == 'NOISE']
    rng.shuffle(kept)
    rng.shuffle(dropped)
    chosen = kept[:size * 2 // 5] + dropped[:size - size * 2 // 5]
    rng.shuffle(chosen)
    sheet, key = [], []
    for n, r in enumerate(chosen, 1):
        item = f'C{n:03d}'
        sheet.append({'item_id': item, 'date': r['edition_date'], 'source': r['source_name'], 'headline': r['headline'],
                      'lead': lead(r['article_text']), 'keep': '', 'section': '', 'note': ''})
        key.append({'item_id': item, 'keep': r['target_relevance_label'], 'section': sections.get(r['source_url'], ''),
                    'source_url': r['source_url']})
    write(SHEETS / f'{name}.csv', sheet, SHEET_FIELDS)
    write(SHEETS / f'{name}_KEY_do_not_open.csv', key, ['item_id', 'keep', 'section', 'source_url'])
    print(f'{len(sheet)} items -> {SHEETS / (name + ".csv")}')


def score_sheet(name: str) -> None:
    filled = {r['item_id']: r for r in read(SHEETS / f'{name}.csv')}
    key = read(SHEETS / f'{name}_KEY_do_not_open.csv')
    keep_agree = sum(filled[k['item_id']]['keep'].strip().upper() == k['keep'] for k in key)
    kept = [k for k in key if k['keep'] == 'KEEP']
    # Sections are scored where the editor kept the story and so gave a section.
    section_agree = sum(SECTION_CODES.get(filled[k['item_id']]['section'].strip().upper(), filled[k['item_id']]['section'].strip()) == k['section'] for k in kept)
    said_keep = [k for k in key if filled[k['item_id']]['keep'].strip().upper() == 'KEEP']
    result = {'items': len(key), 'keep_or_drop_agreement': round(keep_agree / len(key), 3),
              'editor_keeps_found': round(sum(filled[k['item_id']]['keep'].strip().upper() == 'KEEP' for k in kept) / max(1, len(kept)), 3),
              'my_keeps_the_editor_kept': round(sum(k['keep'] == 'KEEP' for k in said_keep) / max(1, len(said_keep)), 3),
              'section_agreement_on_editor_keeps': round(section_agree / max(1, len(kept)), 3)}
    print(json.dumps(result, indent=1))
    (SHEETS / f'{name}_score.json').write_text(json.dumps(result, indent=1), encoding='utf-8')


ARCHIVE_INDEX = SHEETS / 'archive_index.csv'
INDEX_FIELDS = ['item_id', 'batch', 'date', 'gate', 'source_url', 'staged_file', 'source', 'headline']


def archive_pool(before: str, noise_sample: int, seed: int) -> list[dict]:
    """Every likely or uncertain archive article, plus a sample of rule-rejected ones."""
    rng = random.Random(seed)
    found, rejected = [], []
    for path in sorted((ROOT / 'data/processed/stage1').rglob('*.json')):
        if path.name.endswith('manifest.json') or path.parent.parent.name >= before:
            continue
        try:
            record = json.loads(path.read_text(encoding='utf-8-sig'))
        except ValueError:
            continue
        staged = ROOT / str(record.get('input_file') or '')
        gate = (record.get('stage1') or {}).get('gate_decision')
        if not record.get('source_url') or not staged.is_file():
            continue
        row = {'date': path.parent.parent.name, 'gate': gate, 'source_url': record['source_url'],
               'staged_file': str(staged.relative_to(ROOT)), 'source': record.get('source_name', ''), 'headline': record.get('headline', '')}
        (rejected if gate == 'EXCLUDE_NOISE' else found).append(row)
    rng.shuffle(rejected)
    pool = found + rejected[:noise_sample]
    # Batches mix months and gates, so each one moves training forward.
    rng.shuffle(pool)
    return pool


def archive_batch(number: int, size: int, before: str, noise_sample: int, seed: int) -> None:
    index = read(ARCHIVE_INDEX) if ARCHIVE_INDEX.is_file() else []
    if not index:
        seen = set()
        for n, row in enumerate(archive_pool(before, noise_sample, seed)):
            if row['source_url'] in seen:
                continue
            seen.add(row['source_url'])
            index.append({'item_id': f'A{len(index) + 1:05d}', 'batch': str(len(index) // size + 1), **row})
        write(ARCHIVE_INDEX, index, INDEX_FIELDS)
    items = [r for r in index if r['batch'] == str(number)]
    sheet = []
    for r in items:
        staged = json.loads((ROOT / r['staged_file']).read_text(encoding='utf-8-sig'))
        sheet.append({'item_id': r['item_id'], 'date': r['date'], 'source': r['source'], 'headline': r['headline'],
                      'lead': lead(str(staged.get('article_text') or '')), 'keep': '', 'section': '', 'note': ''})
    write(SHEETS / f'archive_batch_{number:03d}.csv', sheet, SHEET_FIELDS)
    print(f'batch {number}: {len(sheet)} items of {len(index)} in the archive pool')


def intake(out: Path) -> None:
    """Training rows from every filled archive batch, marked as assistant labels."""
    index = {r['item_id']: r for r in read(ARCHIVE_INDEX)}
    rows = []
    for sheet in sorted(SHEETS.glob('archive_batch_*.csv')):
        for r in read(sheet):
            keep = r['keep'].strip().upper()
            if keep not in ('KEEP', 'NOISE'):
                continue
            source = index[r['item_id']]
            staged = json.loads((ROOT / source['staged_file']).read_text(encoding='utf-8-sig'))
            rows.append({'item_id': r['item_id'], 'date': source['date'], 'source_name': source['source'], 'headline': source['headline'],
                         'article_text': staged.get('article_text') or '', 'source_url': source['source_url'], 'keep': keep,
                         'section': SECTION_CODES.get(r['section'].strip().upper(), '') if keep == 'KEEP' else ''})
    write(out, rows, ['item_id', 'date', 'source_name', 'headline', 'article_text', 'source_url', 'keep', 'section'])
    print(f'{len(rows)} labelled archive articles -> {out}; keep {sum(r["keep"] == "KEEP" for r in rows)}')


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('mode', choices=('check', 'score', 'archive', 'intake'))
    p.add_argument('--name', default='check_v1')
    p.add_argument('--size', type=int, default=60)
    p.add_argument('--seed', type=int, default=7)
    p.add_argument('--batch', type=int, default=1)
    p.add_argument('--before', default='2026-10-01', help='Archive days before this date (never the editor-labelled days)')
    p.add_argument('--noise-sample', type=int, default=300)
    p.add_argument('--out', type=Path, default=SHEETS / 'assistant_labels.csv')
    a = p.parse_args()
    if a.mode == 'check':
        check_sheet(a.name, a.size, a.seed)
    elif a.mode == 'score':
        score_sheet(a.name)
    elif a.mode == 'archive':
        archive_batch(a.batch, 100 if a.size == 60 else a.size, a.before, a.noise_sample, a.seed)
    else:
        intake(a.out)


if __name__ == '__main__':
    main()
