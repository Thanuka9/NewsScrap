"""Meaning-based same-story check for reports the rules leave apart.

Calibrated on the editor's own multi-source cards (16-28 Oct 2026): FinBERT
similarity alone confuses 'same topic' with 'same story' (different bank
partnerships score up to 0.96). Requiring similarity >= 0.94 AND a shared
organisation joined 2 more same-story pairs (11/22 with the rules) with no
wrong merge among 1,317 different-story pairs. Lower thresholds merged
different stories (ADB vs OPEC loans), so the bar stays strict.
"""
from __future__ import annotations

from datetime import date

THRESHOLD = 0.94
_ENGINE = None
_CACHE: dict[str, object] = {}


def _engine():
    global _ENGINE
    if _ENGINE is None:
        from bank_intel.intelligence.finbert_engine import FinBertEngine
        _ENGINE = FinBertEngine()
    return _ENGINE


def _vector(article: dict):
    import numpy as np
    key = str(article.get('source_url') or '') + '|' + str(article.get('headline') or '')
    if key not in _CACHE:
        _, embedding = _engine().analyse(headline=str(article.get('headline') or ''),
                                         article_text=str(article.get('body') or article.get('summary') or '')[:1500])
        _CACHE[key] = embedding / (np.linalg.norm(embedding) or 1.0)
    return _CACHE[key]


def _days_apart(a: dict, b: dict) -> int:
    try:
        return abs((date.fromisoformat(str(a.get('publication_date'))[:10]) - date.fromisoformat(str(b.get('publication_date'))[:10])).days)
    except ValueError:
        return 99


def same_story(a: dict, b: dict) -> bool:
    """True only for near-identical meaning about a shared organisation, a day apart at most."""
    from .identity import entities, organisations, event_types, main_type
    if _days_apart(a, b) > 1:
        return False
    if not organisations(entities(a) & entities(b)):
        return False
    ta, tb = event_types(a), event_types(b)
    if ta and tb and main_type(ta) != main_type(tb):
        return False
    return float(_vector(a) @ _vector(b)) >= THRESHOLD
