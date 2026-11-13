"""Stage A (NOISE vs KEEP) training data from the editions actually distributed.

Labels come from editorial outcomes, not AI-assisted guesses:
* KEEP  - an article the edition carried, or a same-day report of the same story.
* NOISE - an article collected for that edition window that the edition did not
          carry and that does not repeat a story already sent.
* (none) - a repeat of a story sent in the previous seven days: it was left out
          for being repeated, not for being irrelevant, so it teaches nothing.

Splits are by edition date so evaluation never sees training days. The older
AI-assisted training rows are kept in train only; validation and test contain
editorial labels only, with their natural class balance.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from bank_intel.publication.identity import canonical, same_event
from bank_intel.publication.reconcile import read_sent_html

REPORTS = ROOT / 'data/processed/reports'
BASE_DATASET = ROOT / 'data/training/datasets/supervisory_relevance_v7_augmented_stage_a'
FIELDS = ['dataset_row_id', 'story_group_id', 'split', 'source_name', 'headline', 'article_text', 'published_at',
          'source_url', 'target_relevance_label', 'label_source', 'edition_date', 'original_target_relevance_label',
          'two_stage_task', 'label_mapping_version']


def sent_rows(day: str, downloads: Path) -> list[dict]:
    """Stories of the edition distributed for `day` (registered record first)."""
    folder = REPORTS / day / 'window_1d'
    record = next(iter(sorted(folder.rglob('SENT_EDITION.json'))), None) if folder.is_dir() else None
    if record:
        sent = json.loads(record.read_text(encoding='utf-8'))
        return [{'headline': e['headline'], 'summary': e.get('summary', ''), 'body': e.get('summary', ''),
                 'publication_date': e.get('publication_date') or day, 'source_url': s['source_url']}
                for e in sent['events'] for s in e['sources']]
    locked = folder / 'PUBLISHED_URLS.json'
    if locked.is_file():
        sent = json.loads(locked.read_text(encoding='utf-8-sig'))
        return [{'headline': e['headline'], 'summary': e.get('summary', ''), 'body': e.get('summary', ''),
                 'publication_date': e.get('publication_date') or day, 'source_url': u}
                for e in sent['events'] for u in e.get('source_urls', [])]
    for name in (f'Daily Financial Sector News Brief {day}.html', f'Daily Financial Sector News Brief {day} UPDATED.html'):
        if (downloads / name).is_file():
            return read_sent_html([downloads / name])
    # Editions sent before registration existed are archived with the project.
    index = ROOT / 'data/reference_editions/index.json'
    entry = json.loads(index.read_text(encoding='utf-8')).get(day) if index.is_file() else None
    if entry and (index.parent / entry['file']).is_file():
        return read_sent_html([index.parent / entry['file']])
    return []


def corrections() -> dict:
    """URL -> label from stories the editor added to or removed from a draft.

    These are the surest labels: a person changed the system's decision.
    """
    found = {}
    for path in REPORTS.glob('20*/window_1d/editorial_corrections_from_delivery.csv'):
        with path.open(encoding='utf-8-sig') as stream:
            for row in csv.DictReader(stream):
                found[canonical(row['source_url'])] = 'KEEP' if row['delivered_decision'] == 'INCLUDE' else 'NOISE'
    return found


def build(downloads: Path, out: Path, train_days: set[str], validation_days: set[str], test_days: set[str],
          noise_ratio: float, seed: int, validation_noise_ratio: float = 0.0, ai_rows: bool = True, gold_weight: int = 1) -> dict:
    days = sorted(train_days | validation_days | test_days)
    editions = {d: sent_rows(d, downloads) for d in days}
    missing = [d for d in days if not editions[d]]
    if missing:
        raise SystemExit('No distributed edition found for: ' + ', '.join(missing))
    # Every distributed edition we can find, for repeat detection.
    history = {d: rows for d in (date.fromisoformat(days[0]) - timedelta(days=n) for n in range(1, 8))
               for d, rows in [(d.isoformat(), sent_rows(d.isoformat(), downloads))] if rows}
    history.update(editions)
    rows_by_url: dict[str, dict] = {}
    for day in days:
        audit = REPORTS / day / 'window_1d' / 'stakeholder_brief_v2_audit.json'
        articles = json.loads(audit.read_text(encoding='utf-8-sig'))['audit']
        sent = editions[day]
        sent_urls = {canonical(r['source_url']) for r in sent}
        earlier = [r for d, rows in history.items() if date.fromisoformat(day) - timedelta(days=7) <= date.fromisoformat(d) < date.fromisoformat(day) for r in rows]
        for article in articles:
            url = canonical(article.get('source_url', ''))
            text = str(article.get('body') or '')
            if not url or len(text.split()) < 40:
                continue
            if url in sent_urls or any(same_event(article, s, max_gap_days=1) for s in sent):
                label, source = 'KEEP', 'EDITION_CARRIED'
            elif any(canonical(e['source_url']) == url or same_event(article, e) for e in earlier):
                continue  # left out as a repeat, not as irrelevant
            else:
                label, source = 'NOISE', 'EDITION_OMITTED'
            existing = rows_by_url.get(url)
            if existing and (existing['target_relevance_label'] == 'KEEP' or existing['edition_date'] <= day):
                continue
            split = 'train' if day in train_days else 'validation' if day in validation_days else 'test'
            rows_by_url[url] = {
                'dataset_row_id': 'ED-' + hashlib.sha256(url.encode()).hexdigest()[:12], 'story_group_id': url,
                'split': split, 'source_name': article.get('source_name', ''), 'headline': article.get('headline', ''),
                'article_text': text, 'published_at': article.get('published_at') or article.get('publication_date', ''),
                'source_url': url, 'target_relevance_label': label, 'label_source': source, 'edition_date': day,
                'original_target_relevance_label': label, 'two_stage_task': 'STAGE_A_NOISE_VS_KEEP',
                'label_mapping_version': 'editorial-v1'}
    gold = corrections()
    for row in rows_by_url.values():
        if gold.get(row['source_url']) == row['target_relevance_label']:
            row['label_source'] = 'EDITOR_CORRECTION'
    # Stories the editor said after sending should not have gone out.
    instructed = ROOT / 'data/training/labelling/editor_instructions.csv'
    if instructed.is_file():
        with instructed.open(encoding='utf-8-sig') as stream:
            for line in csv.DictReader(stream):
                row = rows_by_url.get(canonical(line['source_url']))
                if row:
                    row.update(target_relevance_label=line['label'], label_source='EDITOR_INSTRUCTION')
    editorial = list(rows_by_url.values())
    rng = random.Random(seed)
    splits = {s: [r for r in editorial if r['split'] == s] for s in ('train', 'validation', 'test')}
    base = []
    if ai_rows:
        # Earlier AI-assisted rows stay as training context only.
        with (BASE_DATASET / 'train.csv').open(encoding='utf-8-sig') as stream:
            base = [{k: r.get(k, '') for k in FIELDS} | {'split': 'train', 'label_source': 'V7_AI_ASSISTED', 'edition_date': ''}
                    for r in csv.DictReader(stream) if r['source_url'] not in rows_by_url]
    keep = [r for r in splits['train'] if r['target_relevance_label'] == 'KEEP']
    noise = [r for r in splits['train'] if r['target_relevance_label'] == 'NOISE']
    rng.shuffle(noise)
    # Stories the editor removed are always kept as examples; sampling picks the rest.
    noise.sort(key=lambda r: r['label_source'] != 'EDITOR_CORRECTION')
    corrected = [r for r in keep + noise if r['label_source'] == 'EDITOR_CORRECTION']
    repeats = [{**r, 'dataset_row_id': f"{r['dataset_row_id']}-x{n}"} for r in corrected for n in range(1, gold_weight)]
    splits['train'] = keep + noise[:int(len(keep) * noise_ratio)] + base + repeats
    rng.shuffle(splits['train'])
    # Validation only picks the best epoch; a smaller, balanced set keeps CPU
    # training practical. Test keeps the natural mix so precision is honest.
    if validation_noise_ratio:
        vkeep = [r for r in splits['validation'] if r['target_relevance_label'] == 'KEEP']
        vnoise = [r for r in splits['validation'] if r['target_relevance_label'] == 'NOISE']
        rng.shuffle(vnoise)
        splits['validation'] = vkeep + vnoise[:int(len(vkeep) * validation_noise_ratio)]
    out.mkdir(parents=True, exist_ok=True)
    summary = {}
    for name, rows in splits.items():
        with (out / f'{name}.csv').open('w', newline='', encoding='utf-8-sig') as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS); writer.writeheader(); writer.writerows(rows)
        summary[name] = {'rows': len(rows), 'keep': sum(r['target_relevance_label'] == 'KEEP' for r in rows),
                         'noise': sum(r['target_relevance_label'] == 'NOISE' for r in rows)}
    manifest = {'task_name': 'STAGE_A_NOISE_VS_KEEP', 'label_policy': 'editorial-v2' if not ai_rows else 'editorial-v1',
                'ai_assisted_rows': len(base), 'editor_corrections_repeated': len(repeats), 'train_days': sorted(train_days),
                'validation_days': sorted(validation_days), 'test_days': sorted(test_days), 'noise_ratio': noise_ratio,
                'seed': seed, 'validation_noise_ratio': validation_noise_ratio, 'splits': summary}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(manifest, indent=2))
    return manifest


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--downloads', type=Path, default=Path.home() / 'Downloads')
    p.add_argument('--out', type=Path, default=ROOT / 'data/training/datasets/supervisory_relevance_v8_editorial_stage_a')
    p.add_argument('--train-days', default='2026-10-16,2026-10-19,2026-10-20,2026-10-21,2026-10-22,2026-10-23')
    p.add_argument('--validation-days', default='2026-10-26,2026-10-27')
    p.add_argument('--test-days', default='2026-10-28,2026-10-29')
    p.add_argument('--noise-ratio', type=float, default=3.0)
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--validation-noise-ratio', type=float, default=3.0)
    p.add_argument('--no-ai-rows', action='store_true', help='Train on editor decisions only')
    p.add_argument('--gold-weight', type=int, default=1, help='Times each editor correction appears in train')
    a = p.parse_args()
    split = lambda v: {x.strip() for x in v.split(',') if x.strip()}
    build(a.downloads, a.out, split(a.train_days), split(a.validation_days), split(a.test_days), a.noise_ratio, a.seed,
          a.validation_noise_ratio, ai_rows=not a.no_ai_rows, gold_weight=a.gold_weight)
