"""Stage B training data: which section the editor put each story in.

Labels are the section headings of the editions actually sent. Each source
article of a story carries that story's section. Splits are by edition date.
With --external, topic-labelled financial news (zeroshot/twitter-financial-news-topic,
MIT licence) is added to train only, for the topics that map onto one section.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
from html import unescape
import io
import json
from pathlib import Path
import random
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from bank_intel.publication.identity import canonical, publisher_article_id

REPORTS = ROOT / 'data/processed/reports'
REFERENCE = ROOT / 'data/reference_editions'
SECTIONS = ('KEY_BANKING_SUPERVISORY', 'BANKING_SECTOR_DEVELOPMENTS', 'OTHER_FINANCIAL_SECTOR', 'MONETARY_MARKETS_EXTERNAL', 'MACRO_SYSTEMIC_CONTEXT')
# Headings the editions have used; numbered headings ('SECTION 01') say nothing and are skipped.
HEADINGS = {
    'KEY BANKING SUPERVISORY': 'KEY_BANKING_SUPERVISORY', 'NEWS HIGHLIGHTS': 'KEY_BANKING_SUPERVISORY',
    'BANKING SECTOR DEVELOPMENTS': 'BANKING_SECTOR_DEVELOPMENTS', 'BANKING SECTOR': 'BANKING_SECTOR_DEVELOPMENTS',
    'OTHER FINANCIAL SECTOR': 'OTHER_FINANCIAL_SECTOR',
    'MONETARY MARKETS EXTERNAL': 'MONETARY_MARKETS_EXTERNAL', 'ECONOMY & MARKETS': 'MONETARY_MARKETS_EXTERNAL',
    'MACRO SYSTEMIC CONTEXT': 'MACRO_SYSTEMIC_CONTEXT', 'MACRO & SYSTEMIC': 'MACRO_SYSTEMIC_CONTEXT',
}
# Only topics that belong to one section; the rest ('Fed', 'Legal', 'Earnings') depend on who is involved.
EXTERNAL_TOPICS = {3: 'MONETARY_MARKETS_EXTERNAL', 8: 'MONETARY_MARKETS_EXTERNAL', 15: 'MONETARY_MARKETS_EXTERNAL',
                   14: 'MACRO_SYSTEMIC_CONTEXT', 7: 'BANKING_SECTOR_DEVELOPMENTS'}
EXTERNAL_URL = 'https://huggingface.co/datasets/zeroshot/twitter-financial-news-topic/resolve/main/topic_train.csv'
FIELDS = ['dataset_row_id', 'story_group_id', 'split', 'source_name', 'headline', 'article_text', 'published_at',
          'source_url', 'target_relevance_label', 'label_source', 'edition_date', 'original_target_relevance_label',
          'two_stage_task', 'label_mapping_version']


def editions() -> dict:
    found = {}
    index = REFERENCE / 'index.json'
    if index.is_file():
        found = {day: REFERENCE / entry['file'] for day, entry in json.loads(index.read_text(encoding='utf-8')).items()}
    for record in REPORTS.glob('20*/window_1d/**/SENT_EDITION.json'):
        entry = json.loads(record.read_text(encoding='utf-8'))
        if Path(entry.get('attachment') or '').is_file():
            found[entry['date']] = Path(entry['attachment'])
    return found


def sectioned_stories(html: str) -> list[tuple[str, list[str]]]:
    """(section, source links) for every story, in page order."""
    out = []
    for part in re.split(r'(?=<section\b)', html):
        heading = re.search(r'class="section-kicker">([^<]+)<', part)
        section = HEADINGS.get(' '.join(unescape(heading.group(1)).split()).upper()) if heading else None
        if not section:
            continue
        for article in re.findall(r'<article\b.*?</article>', part, re.S):
            links = re.findall(r'href="(https?://[^"]+)"', article)
            if links:
                out.append((section, [unescape(u) for u in links]))
    return out


def external_rows(limit: int, seed: int) -> list[dict]:
    import requests
    text = requests.get(EXTERNAL_URL, timeout=120).text
    rows = [r for r in csv.DictReader(io.StringIO(text)) if int(r['label']) in EXTERNAL_TOPICS]
    rows = [r for r in rows if not re.search(r'https?://', r['text']) or len(r['text'].split()) > 8]
    random.Random(seed).shuffle(rows)
    out, seen = [], set()
    for r in rows:
        clean = re.sub(r'https?://\S+', '', r['text']).strip()
        key = 'EXT-' + hashlib.sha256(clean.encode()).hexdigest()[:12]
        if key in seen:
            continue
        if len(out) >= limit:
            break
        seen.add(key)
        out.append({'dataset_row_id': key, 'story_group_id': key, 'split': 'train', 'source_name': 'external topic dataset',
                    'headline': clean[:200], 'article_text': clean, 'published_at': '', 'source_url': 'external://' + key,
                    'target_relevance_label': EXTERNAL_TOPICS[int(r['label'])], 'label_source': 'EXTERNAL_TOPIC_MAPPED',
                    'edition_date': '', 'original_target_relevance_label': r['label'], 'two_stage_task': 'STAGE_B_SECTION',
                    'label_mapping_version': 'section-v1'})
    return out


def build(out: Path, train: set, validation: set, test: set, external: int, seed: int, assistant: bool = False) -> dict:
    rows, by_url = [], {}
    for day, path in sorted(editions().items()):
        split = 'train' if day in train else 'validation' if day in validation else 'test' if day in test else None
        audit = REPORTS / day / 'window_1d' / 'stakeholder_brief_v2_audit.json'
        if not split or not audit.is_file():
            continue
        articles = {}
        for a in json.loads(audit.read_text(encoding='utf-8-sig'))['audit']:
            for key in (canonical(a.get('source_url', '')), publisher_article_id(a.get('source_url', ''))):
                if key:
                    articles[key] = a
        for section, links in sectioned_stories(path.read_text(encoding='utf-8', errors='replace')):
            for link in links:
                article = articles.get(canonical(link)) or articles.get(publisher_article_id(link))
                url = canonical(link)
                if not article or not article.get('body') or url in by_url:
                    continue
                by_url[url] = {'dataset_row_id': 'SEC-' + hashlib.sha256(url.encode()).hexdigest()[:12], 'story_group_id': url,
                               'split': split, 'source_name': article.get('source_name', ''), 'headline': article.get('headline', ''),
                               'article_text': article['body'], 'published_at': article.get('published_at') or article.get('publication_date', ''),
                               'source_url': url, 'target_relevance_label': section, 'label_source': 'EDITION_SECTION',
                               'edition_date': day, 'original_target_relevance_label': section, 'two_stage_task': 'STAGE_B_SECTION',
                               'label_mapping_version': 'section-v1'}
    rows = list(by_url.values())
    if assistant:
        # Archive articles labelled in session by an assistant: train only, never test.
        with (ROOT / 'data/training/labelling/assistant_labels.csv').open(encoding='utf-8-sig') as stream:
            for r in csv.DictReader(stream):
                url = canonical(r['source_url'])
                if r['keep'] != 'KEEP' or not r['section'] or url in by_url:
                    continue
                rows.append({'dataset_row_id': 'ASL-' + hashlib.sha256(url.encode()).hexdigest()[:12], 'story_group_id': url, 'split': 'train',
                             'source_name': r['source_name'], 'headline': r['headline'], 'article_text': r['article_text'], 'published_at': r['date'],
                             'source_url': url, 'target_relevance_label': r['section'], 'label_source': 'ASSISTANT_LABEL', 'edition_date': '',
                             'original_target_relevance_label': r['section'], 'two_stage_task': 'STAGE_B_SECTION', 'label_mapping_version': 'section-v1'})
    if external:
        rows += external_rows(external, seed)
    out.mkdir(parents=True, exist_ok=True)
    summary = {}
    for split in ('train', 'validation', 'test'):
        part = [r for r in rows if r['split'] == split]
        with (out / f'{split}.csv').open('w', newline='', encoding='utf-8-sig') as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(part)
        summary[split] = {s: sum(r['target_relevance_label'] == s for r in part) for s in SECTIONS} | {'rows': len(part)}
    manifest = {'task_name': 'STAGE_B_SECTION', 'labels': SECTIONS, 'train_days': sorted(train), 'validation_days': sorted(validation),
                'test_days': sorted(test), 'external_rows': external, 'seed': seed, 'splits': summary}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(manifest, indent=2))
    return manifest


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--train-days', default='2026-10-16,2026-10-21,2026-10-26,2026-10-27,2026-10-28')
    p.add_argument('--validation-days', default='2026-10-29,2026-10-30')
    p.add_argument('--test-days', default='2026-11-02,2026-11-03')
    p.add_argument('--external', type=int, default=0, help='Mapped topic-dataset rows to add to train')
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--assistant-labels', action='store_true', help='Add in-session assistant labels to train')
    a = p.parse_args()
    split = lambda s: set(filter(None, s.split(',')))
    build(a.out, split(a.train_days), split(a.validation_days), split(a.test_days), a.external, a.seed, a.assistant_labels)
