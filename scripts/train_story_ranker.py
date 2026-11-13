"""Train the story ranker on a replay, testing each edition on the others.

Run scripts/replay_editions.py first. A retrained model changes a frozen file, so
run news_release_gate.py freeze afterwards or the next scheduled run will stop.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from bank_intel.publication.identity import canonical, publisher_article_id
from bank_intel.publication.ml_trace import read_model_traces
from bank_intel.publication.ranking import LIKELY_DROP, LIKELY_KEEP, MODEL_FILE, candidate, fit, score


def examples(replay: Path) -> list[dict]:
    out = []
    for truth_file in sorted(replay.glob('*/truth.json')):
        day = truth_file.parent.name
        truth = json.loads(truth_file.read_text(encoding='utf-8'))
        urls, ids = set(truth['urls']), set(truth['ids'])
        ledger = json.loads((truth_file.parent / 'publication_decisions.json').read_text(encoding='utf-8'))
        audit = json.loads((ROOT / f'data/processed/reports/{day}/window_1d/stakeholder_brief_v2_audit.json').read_text(encoding='utf-8'))
        bodies = {canonical(r.get('source_url', '')): r.get('body') or '' for r in audit['audit']}
        traces, _ = read_model_traces(ROOT, sorted({a.get('publication_date', '')[:10] for a in ledger['articles']}))
        for row in ledger['articles']:
            if row['decision'] not in ('INCLUDE', 'REVIEW'):
                continue
            url = canonical(row['source_url'])
            article = {**row, 'body': bodies.get(url, '')}
            out.append({'day': day, 'label': int(url in urls or publisher_article_id(url) in ids),
                        **candidate(article, {'reason': row['decision_reason'], 'section': row['section'], 'decision': row['decision']}, traces.get(url))})
    return out


def held_out(rows: list[dict]) -> dict:
    days = sorted({r['day'] for r in rows})
    keep_right = keep_wrong = unsure = dropped_kept = positives = 0
    for day in days:
        train = [r for r in rows if r['day'] != day]
        test = [r for r in rows if r['day'] == day]
        for row, value in zip(test, score(fit(train, [r['label'] for r in train]), test)):
            positives += row['label']
            if value >= LIKELY_KEEP:
                keep_right += row['label']
                keep_wrong += 1 - row['label']
            elif value < LIKELY_DROP:
                dropped_kept += row['label']
            else:
                unsure += 1
    return {'editions': len(days), 'candidates': len(rows), 'kept_by_editor': positives,
            'kept_found_as_likely_keep': round(keep_right / max(1, positives), 3), 'likely_keep_unwanted_per_edition': round(keep_wrong / len(days), 1),
            'unsure_per_edition': round(unsure / len(days), 1), 'kept_scored_likely_drop': dropped_kept}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--replay', type=Path, required=True)
    args = parser.parse_args()
    import joblib
    rows = examples(args.replay)
    metrics = held_out(rows)
    MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(fit(rows, [r['label'] for r in rows]), MODEL_FILE)
    (MODEL_FILE.parent / 'metrics.json').write_text(json.dumps(metrics, indent=1), encoding='utf-8')
    print(json.dumps(metrics, indent=1))


if __name__ == '__main__':
    main()
