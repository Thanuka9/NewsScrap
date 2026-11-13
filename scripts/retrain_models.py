"""Weekly retrain from the editions the editor actually sent; deploy only if better.

1. Stage A data from every edition with a sent version: the latest two edition
   days are the test, the two before them choose the best epoch, the rest train.
2. Fine-tune Stage A, then score it and the live model on the test days.
3. Deploy only when the new model finds at least as many of the editor's stories
   and has a better F1; refreeze (which runs the full test suite) or roll back.
4. Retrain the advisory story ranker on a fresh replay, under the same freeze.
The report in data/review/model_retrain records the outcome either way.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
PYTHON = sys.executable
ML_CONFIG = ROOT / 'config/intelligence/ml.yaml'
REPORTS = ROOT / 'data/processed/reports'
OUT = ROOT / 'data/review/model_retrain'


def edition_days() -> list[str]:
    """Days with both a sent version and the collected articles behind it."""
    days = {p.relative_to(REPORTS).parts[0] for p in REPORTS.glob('20*/window_1d/**/SENT_EDITION.json')}
    days |= {p.relative_to(REPORTS).parts[0] for p in REPORTS.glob('20*/window_1d/PUBLISHED_URLS.json')}
    index = ROOT / 'data/reference_editions/index.json'
    if index.is_file():
        days |= set(json.loads(index.read_text(encoding='utf-8')))
    return sorted(d for d in days if (REPORTS / d / 'window_1d/stakeholder_brief_v2_audit.json').is_file())


def splits(days: list[str]) -> tuple[set, set, set]:
    if len(days) < 9:
        raise SystemExit(f'Only {len(days)} edition days: too few to train and test honestly.')
    return set(days[:-4]), set(days[-4:-2]), set(days[-2:])


def better(new: dict, live: dict) -> bool:
    """The deployment rule used for v8: no fewer of the editor's stories, and a better F1."""
    return new['keep_recall'] >= live['keep_recall'] and new['keep_f1'] > live['keep_f1']


def live_stage_a() -> str:
    text = ML_CONFIG.read_text(encoding='utf-8')
    block = text[text.index('stage_a:'):text.index('stage_b:')]
    return re.search(r'model_path:\s*"([^"]+)"', block).group(1)


def point_stage_a(model_path: str, model_id: str, best_epoch: int, validation_f1: float) -> str:
    """Point the live Stage A model at a new folder; returns the old config text for rollback."""
    original = ML_CONFIG.read_text(encoding='utf-8')
    head, rest = original[:original.index('stage_a:')], original[original.index('stage_a:'):]
    block, tail = rest[:rest.index('stage_b:')], rest[rest.index('stage_b:'):]
    block = re.sub(r'model_path:\s*"[^"]+"', f'model_path: "{model_path}"', block, count=1)
    block = re.sub(r'model_id:\s*"[^"]+"', f'model_id: "{model_id}"', block, count=1)
    block = re.sub(r'expected_best_epoch:\s*\S+', f'expected_best_epoch: {best_epoch}', block, count=1)
    block = re.sub(r'expected_validation_macro_f1:\s*\S+', f'expected_validation_macro_f1: {validation_f1:.4f}', block, count=1)
    ML_CONFIG.write_text(head + block + tail, encoding='utf-8')
    return original


def freeze() -> bool:
    result = subprocess.run([PYTHON, 'scripts/news_release_gate.py', 'freeze', '--operator', 'weekly retrain'], cwd=ROOT,
                            capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(result.stdout[-400:])
    return result.returncode == 0 and '[FREEZE WRITTEN]' in result.stdout


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--epochs', type=int, default=4)
    p.add_argument('--skip-ranker', action='store_true')
    a = p.parse_args()
    import csv
    from build_editorial_stage_a_dataset import build
    from compare_stage_a_on_editorial_test import score
    stamp = date.today().strftime('%Y%m%d')
    version = f'supervisory_relevance_auto_{stamp}_stage_a'
    dataset = ROOT / 'data/training/datasets' / version
    output = ROOT / 'data/processed/relevance_two_stage' / version
    report = {'date': stamp, 'version': version, 'deployed': False}
    days = edition_days()
    train, validation, test = splits(days)
    report.update(train_days=sorted(train), validation_days=sorted(validation), test_days=sorted(test))
    build(Path.home() / 'Downloads', dataset, train, validation, test, 2.5, 42, 3.0, ai_rows=False)
    subprocess.run([PYTHON, 'scripts/run_finbert_two_stage_finetune.py', '--task', 'stage-a', '--dataset-root', str(dataset),
                    '--output-root', str(output), '--epochs', str(a.epochs), '--scheduler-epochs', str(a.epochs),
                    '--max-body-chunks', '3', '--seed', '42'], cwd=ROOT, check=True)
    with (dataset / 'test.csv').open(encoding='utf-8-sig') as stream:
        rows = list(csv.DictReader(stream))
    live_path = live_stage_a()
    new_path = str((output / 'best_model').relative_to(ROOT)).replace('\\', '/')
    live, new = score(live_path, rows), score(new_path, rows)
    report['live'] = {k: v for k, v in live.items() if k != 'predictions'}
    report['new'] = {k: v for k, v in new.items() if k != 'predictions'}
    deployed_config = None
    if better(new, live):
        metrics = json.loads((output / 'best_validation_metrics.json').read_text(encoding='utf-8'))
        manifest = json.loads((output / 'run_manifest.json').read_text(encoding='utf-8'))
        deployed_config = point_stage_a(new_path, f'stage_a_auto_{stamp}_epoch{manifest.get("best_epoch")}',
                                        int(manifest.get('best_epoch') or 0), float(metrics['macro_f1']))
        report['deployed'] = True
    ranker = ROOT / 'data/processed/story_ranker'
    ranker_backup = ROOT / 'data/processed/story_ranker_previous'
    if not a.skip_ranker:
        if ranker_backup.exists():
            shutil.rmtree(ranker_backup)
        shutil.copytree(ranker, ranker_backup)
        replay = Path.home() / 'AppData/Local/Temp' / f'rt{stamp}'
        subprocess.run([PYTHON, 'scripts/replay_editions.py', '--out', str(replay)], cwd=ROOT, check=True)
        subprocess.run([PYTHON, 'scripts/train_story_ranker.py', '--replay', str(replay)], cwd=ROOT, check=True)
        report['ranker'] = json.loads((ranker / 'metrics.json').read_text(encoding='utf-8'))
    if not freeze():
        # A failed test suite leaves production exactly as it was.
        if deployed_config is not None:
            ML_CONFIG.write_text(deployed_config, encoding='utf-8')
        if ranker_backup.exists():
            shutil.rmtree(ranker)
            shutil.copytree(ranker_backup, ranker)
        report.update(deployed=False, rolled_back='freeze failed: tests did not pass')
        freeze()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f'{stamp}.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    verdict = 'deployed' if report['deployed'] else 'kept the live model'
    print(f"[RETRAIN] {verdict}: new recall {report['new']['keep_recall']} F1 {report['new']['keep_f1']} "
          f"vs live recall {report['live']['keep_recall']} F1 {report['live']['keep_f1']}")


if __name__ == '__main__':
    main()
