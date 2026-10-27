"""Read deployed two-stage FinBERT evidence. Never substitute model predictions.

Stage-A raw and operational outcomes are different decisions. The runner applies
production policy after ML inference; labels below preserve that distinction.
"""
from __future__ import annotations
from pathlib import Path
import json
from .identity import canonical


def locate(root: Path, recorded: str) -> Path:
    p = Path(recorded)
    if p.is_absolute():
        return p
    # An absolute Windows path recorded on another OS is not usable locally.
    return root / p


def read_model_traces(project_root: Path, date_strings: list[str]) -> tuple[dict[str, dict], list[str]]:
    traces: dict[str, dict] = {}
    problems: list[str] = []
    for day in sorted({str(d)[:10] for d in date_strings if str(d)[:10]}):
        manifest = project_root / 'data' / 'processed' / 'ml' / day / 'ml_manifest.json'
        if not manifest.is_file():
            problems.append('ML_MANIFEST_NOT_FOUND:' + day)
            continue
        try:
            data = json.loads(manifest.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            problems.append('INVALID_ML_MANIFEST:' + day)
            continue
        if data.get('status') != 'SUCCESS':
            problems.append('ML_MANIFEST_NOT_SUCCESS:' + day)
        for entry in data.get('articles') or []:
            recorded = str(entry.get('analysis_file') or '')
            if not recorded:
                problems.append('ML_ANALYSIS_PATH_MISSING:' + day)
                continue
            path = locate(project_root, recorded)
            if not path.is_file():
                problems.append('ML_ANALYSIS_FILE_MISSING:' + day + ':' + path.name)
                continue
            try:
                article = json.loads(path.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError):
                problems.append('ML_ANALYSIS_FILE_INVALID:' + day + ':' + path.name)
                continue
            url = canonical(entry.get('source_url') or (article.get('article') or {}).get('source_url') or article.get('source_url') or '')
            if not url:
                problems.append('ML_ARTICLE_URL_UNKNOWN:' + day + ':' + path.name)
                continue
            ml = ((article.get('ml') or {}).get('supervisory_relevance') or {})
            a = ml.get('stage_a') or {}
            b = ml.get('stage_b') or {}
            traces[url] = {
                'date': day, 'analysis_file': str(path),
                'architecture': ml.get('architecture'),
                'raw_stage_a': str(a.get('raw_predicted_label') or a.get('predicted_label') or '').upper(),
                'stage_a_confidence': a.get('confidence'),
                'operational_keep': ml.get('operational_keep'),
                'operational_label': ml.get('operational_label'),
                'stage_b_executed': ml.get('stage_b_executed'),
                'stage_b_label': str(b.get('predicted_label') or '').upper(),
                'stage_b_confidence': b.get('confidence'),
            }
    return traces, problems
