"""Score candidate stories from the operator's past selections.

The score advises and decides nothing: on 11 editions it would have dropped the
30 October lead, so it only orders the review queue and suggests an action.
"""
from __future__ import annotations

from pathlib import Path
import re

import numpy as np

MODEL_FILE = Path(__file__).resolve().parents[3] / 'data' / 'processed' / 'story_ranker' / 'ranker.joblib'
CATEGORIES = ('reason', 'section', 'source', 'decision')
LIKELY_KEEP = 0.5
LIKELY_DROP = 0.2


def candidate(article: dict, assessment: dict, trace: dict | None) -> dict:
    """The fields one story is scored on, the same in training and in use."""
    trace = trace or {}
    return {'headline': str(article.get('headline') or ''), 'lead': str(article.get('body') or article.get('summary') or '')[:400],
            'reason': re.sub(r':.*$', '', str(assessment.get('reason') or '')), 'section': str(assessment.get('section') or ''),
            'source': str(article.get('source_name') or ''), 'decision': str(assessment.get('decision') or ''),
            'keep': trace.get('raw_stage_a') == 'KEEP', 'confidence': float(trace.get('stage_a_confidence') or 0)}


def _text(rows: list[dict]) -> list[str]:
    # The headline counts double: it is what an editor decides on.
    return [f"{r['headline']} {r['headline']} {r['lead']}" for r in rows]


def _matrix(rows: list[dict], text_model, categories: list[str]):
    from scipy import sparse
    index = {name: i for i, name in enumerate(categories)}
    extra = sparse.lil_matrix((len(rows), len(categories) + 2))
    for i, row in enumerate(rows):
        for key in CATEGORIES:
            position = index.get(f'{key}={row[key]}')
            if position is not None:
                extra[i, position] = 1
        extra[i, len(categories)] = row['keep']
        extra[i, len(categories) + 1] = row['confidence']
    return sparse.hstack([text_model.transform(_text(rows)), extra.tocsr()]).tocsr()


def fit(rows: list[dict], labels: list[int]) -> dict:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    text_model = TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True, stop_words='english', max_features=6000)
    text_model.fit(_text(rows))
    categories = sorted({f'{key}={row[key]}' for row in rows for key in CATEGORIES})
    model = LogisticRegression(C=0.6, class_weight='balanced', max_iter=2000)
    model.fit(_matrix(rows, text_model, categories), labels)
    return {'text': text_model, 'categories': categories, 'model': model}


def score(ranker: dict, rows: list[dict]) -> np.ndarray:
    return ranker['model'].predict_proba(_matrix(rows, ranker['text'], ranker['categories']))[:, 1]


def suggestion(value: float) -> str:
    return 'LIKELY_KEEP' if value >= LIKELY_KEEP else 'LIKELY_DROP' if value < LIKELY_DROP else 'UNSURE'


def advise(work: list[dict], traces: dict, canonical, model_file: Path = MODEL_FILE) -> str:
    """Add a score and suggestion to each live candidate; decisions are untouched."""
    if not model_file.is_file():
        return 'NOT_TRAINED'
    import joblib
    live = [x for x in work if x['assessment']['decision'] in ('INCLUDE', 'REVIEW')]
    if not live:
        return 'NO_CANDIDATES'
    try:
        ranker = joblib.load(model_file)
        rows = [candidate(x['article'], x['assessment'], traces.get(canonical(x['article'].get('source_url', '')))) for x in live]
        values = score(ranker, rows)
    except Exception as error:
        # Advice is optional; it must never cost the edition.
        return f'UNAVAILABLE:{type(error).__name__}'
    for item, value in zip(live, values):
        item['assessment'].update(ranker_score=round(float(value), 3), ranker_suggestion=suggestion(float(value)))
    return 'ADVISED'
