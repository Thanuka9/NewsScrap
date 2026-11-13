"""Score Stage A checkpoints on the editorial test split (held-out edition days).

Reports KEEP recall, KEEP precision and macro-F1 per checkpoint so a retrained
model is deployed only when it beats the production one on days neither saw.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from bank_intel.intelligence.relevance_engine import SupervisoryRelevanceEngine


def score(model_path: str, rows: list[dict]) -> dict:
    config = yaml.safe_load((ROOT / 'config/intelligence/ml.yaml').read_text(encoding='utf-8'))
    stage_a = dict(config['supervisory_relevance']['stage_a'], model_path=model_path)
    engine = SupervisoryRelevanceEngine(config={**config, 'supervisory_relevance': stage_a})
    tp = fp = fn = tn = 0
    predictions = []
    for row in rows:
        result = engine.analyse(headline=row['headline'], article_text=row['article_text'])
        predicted = str(result.predicted_label or '').upper()
        truth = row['target_relevance_label']
        tp += predicted == 'KEEP' and truth == 'KEEP'; fp += predicted == 'KEEP' and truth == 'NOISE'
        fn += predicted != 'KEEP' and truth == 'KEEP'; tn += predicted != 'KEEP' and truth == 'NOISE'
        predictions.append({'source_url': row['source_url'], 'truth': truth, 'predicted': predicted,
                            'confidence': result.confidence, 'headline': row['headline']})
    def f1(p, r): return 2 * p * r / (p + r) if p + r else 0.0
    keep_p = tp / (tp + fp) if tp + fp else 0.0; keep_r = tp / (tp + fn) if tp + fn else 0.0
    noise_p = tn / (tn + fn) if tn + fn else 0.0; noise_r = tn / (tn + fp) if tn + fp else 0.0
    return {'model_path': model_path, 'rows': len(rows), 'keep_recall': round(keep_r, 4), 'keep_precision': round(keep_p, 4),
            'keep_f1': round(f1(keep_p, keep_r), 4), 'macro_f1': round((f1(keep_p, keep_r) + f1(noise_p, noise_r)) / 2, 4),
            'confusion': {'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn}, 'predictions': predictions}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--test', type=Path, default=ROOT / 'data/training/datasets/supervisory_relevance_v8_editorial_stage_a/test.csv')
    p.add_argument('--model', action='append', required=True, help='checkpoint folder; repeat to compare')
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    with a.test.open(encoding='utf-8-sig') as stream:
        rows = list(csv.DictReader(stream))
    results = [score(m, rows) for m in a.model]
    a.out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')
    for r in results:
        print(json.dumps({k: v for k, v in r.items() if k != 'predictions'}))
