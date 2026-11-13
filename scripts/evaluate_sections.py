"""Compare ways of choosing a story's section on edition days none of them trained on.

Methods: today's rules; a classifier on frozen FinBERT features; and any fine-tuned
section models given with --model. Scored against the sections the editor used.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
csv.field_size_limit(10 ** 8)


def rows(path: Path, sources=('EDITION_SECTION',)) -> list[dict]:
    with path.open(encoding='utf-8-sig') as stream:
        return [r for r in csv.DictReader(stream) if r['label_source'] in sources]


def score(truth: list[str], predicted: list[str]) -> dict:
    from sklearn.metrics import accuracy_score, f1_score
    labels = sorted(set(truth))
    return {'stories': len(truth), 'accuracy': round(accuracy_score(truth, predicted), 3),
            'macro_f1': round(f1_score(truth, predicted, labels=labels, average='macro', zero_division=0), 3)}


def by_rules(test: list[dict]) -> list[str]:
    from bank_intel.publication.decisions import decide
    return [decide({'headline': r['headline'], 'body': r['article_text'], 'source_name': r['source_name'], 'source_url': r['source_url'],
                    'publication_date': r['edition_date']})['section'] or 'NONE' for r in test]


def by_frozen_features(train: list[dict], test: list[dict]) -> list[str]:
    from sklearn.linear_model import LogisticRegression
    from bank_intel.publication.semantic import _engine
    engine = _engine()

    def vector(r):
        _, embedding = engine.analyse(headline=r['headline'], article_text=r['article_text'][:1500])
        return embedding
    model = LogisticRegression(max_iter=3000, class_weight='balanced', C=1.0)
    model.fit([vector(r) for r in train], [r['target_relevance_label'] for r in train])
    return list(model.predict([vector(r) for r in test]))


def by_model(folder: Path, test: list[dict]) -> list[str]:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(folder)
    model = AutoModelForSequenceClassification.from_pretrained(folder).eval()
    names = model.config.id2label
    out = []
    with torch.no_grad():
        for r in test:
            encoded = tokenizer(r['headline'], r['article_text'][:3000], truncation=True, max_length=512, return_tensors='pt')
            out.append(names[int(model(**encoded).logits.argmax(-1))])
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--dataset', type=Path, default=ROOT / 'data/training/datasets/stage_b_section_v1')
    p.add_argument('--model', type=Path, action='append', default=[])
    p.add_argument('--out', type=Path)
    a = p.parse_args()
    # Training may include assistant labels; the test is always the editor's own sections.
    sources = ('EDITION_SECTION', 'ASSISTANT_LABEL')
    train, test = rows(a.dataset / 'train.csv', sources) + rows(a.dataset / 'validation.csv', sources), rows(a.dataset / 'test.csv')
    truth = [r['target_relevance_label'] for r in test]
    results = {'rules': score(truth, by_rules(test)), 'frozen_finbert_features': score(truth, by_frozen_features(train, test))}
    for folder in a.model:
        results[str(folder.relative_to(ROOT)) if folder.is_absolute() else str(folder)] = score(truth, by_model(folder, test))
    print(json.dumps(results, indent=1))
    if a.out:
        a.out.write_text(json.dumps(results, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
