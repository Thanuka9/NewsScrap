"""Read-only checks before reusing completed analysis for a final-stage resume."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from bank_intel.publication.identity import canonical, SOURCE_KEYS, sources_for_date
from bank_intel.publication.coverage import missing_surfaces


def _read(path):
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    return value


def _locate(root, value):
    path = Path(str(value).replace('\\', '/'))
    return path if path.is_absolute() else root/path


def collection_issues(root: Path, target: str) -> list[str]:
    issues = []
    for name in sources_for_date(target):
        key = SOURCE_KEYS[name]
        try:
            info = _read(root/'data/source_status'/f'{key}.json')
            if info.get('execution_status') != 'SUCCESS':
                issues.append(f'COLLECTOR_NOT_SUCCESS:{key}')
            if str(info.get('completed_at', ''))[:10] != target:
                issues.append(f'STALE_SOURCE:{key}')
            if missing_surfaces(name, info.get('discovery_surfaces') or []):
                issues.append(f'MISSING_SURFACES:{key}')
            end = str(info.get('to_date') or info.get('window_end') or '')[:10]
            if end != target:
                issues.append(f'COLLECTION_DATE_MISMATCH:{key}')
        except (OSError, ValueError, TypeError, AttributeError):
            issues.append(f'MISSING_OR_INVALID_SOURCE:{key}')
    return issues


def final_stage_readiness(root: Path, target: str, days: int = 1) -> dict:
    root = Path(root).resolve()
    base = root/'data/processed'
    paths = {
        'stage1': base/'stage1'/target/'stage1_manifest.json',
        'ml': base/'ml'/target/'ml_manifest.json',
        'corpus': base/'ml_corpus'/target/f'window_{days}d'/'corpus_manifest.json',
        'taxonomy': base/'taxonomy'/target/f'window_{days}d'/'taxonomy_manifest.json',
        'collection': base/'collection_completeness'/target/f'window_{days}d'/'collection_completeness_manifest.json',
    }
    issues = collection_issues(root, target)
    manifests = {}
    for name, path in paths.items():
        try:
            data = _read(path)
            manifests[name] = data
            if data.get('status') != ('PASS' if name == 'collection' else 'SUCCESS'):
                issues.append(f'UPSTREAM_NOT_SUCCESS:{name}')
            recorded = data.get('publication_date') or data.get('window_end') or data.get('end_date')
            if recorded != target:
                issues.append(f'UPSTREAM_DATE_MISMATCH:{name}')
            if name in ('corpus', 'taxonomy') and data.get('days') != days:
                issues.append(f'UPSTREAM_WINDOW_MISMATCH:{name}')
        except (OSError, ValueError, TypeError, AttributeError):
            issues.append(f'MISSING_OR_INVALID_MANIFEST:{name}')
    s1 = manifests.get('stage1', {})
    ml = manifests.get('ml', {})
    count = s1.get('article_count', 0)
    if type(count) is not int or count <= 0 or count != ml.get('article_count'):
        issues.append('STAGE1_ML_COUNT_MISMATCH')
    if s1.get('failed_count'):
        issues.append('STAGE1_FAILED_ARTICLES')
    if manifests.get('collection', {}).get('recovered_article_count'):
        issues.append('UNRESOLVED_COLLECTION_RECOVERIES')
    if manifests.get('corpus', {}).get('all_phase2_article_count') != ml.get('article_count'):
        issues.append('CORPUS_INPUT_COUNT_MISMATCH')
    if manifests.get('taxonomy', {}).get('article_count') != manifests.get('corpus', {}).get('stage_a_keep_article_count'):
        issues.append('TAXONOMY_INPUT_COUNT_MISMATCH')
    for name, fields in {
        'corpus': ('cluster_file', 'cluster_profile_file', 'candidate_event_groups_file'),
        'taxonomy': ('article_taxonomy_json', 'cluster_taxonomy_json', 'article_taxonomy_csv'),
    }.items():
        for field in fields:
            recorded = manifests.get(name, {}).get(field)
            if not recorded or not _locate(root, recorded).is_file():
                issues.append(f'MISSING_UPSTREAM_OUTPUT:{name}:{field}')
    # A new collector pass can introduce articles absent from otherwise valid
    # old manifests. Require analysis after the source evidence being reused.
    try:
        analysed = datetime.fromisoformat(s1['created_at'])
        for name in sources_for_date(target):
            path = root/'data/source_status'/(SOURCE_KEYS[name]+'.json')
            info = _read(path)
            completed = datetime.fromisoformat(info['completed_at'])
            if completed > analysed:
                issues.append('COLLECTION_NEWER_THAN_ANALYSIS:'+SOURCE_KEYS[name])
    except (OSError, KeyError, ValueError, TypeError):
        issues.append('MISSING_OR_INVALID_FRESHNESS_EVIDENCE')

    populations = {}
    entries = ml.get('articles')
    if not isinstance(entries, list) or any(not isinstance(r, dict) for r in entries):
        issues.append('INVALID_ML_ARTICLE_LIST')
        entries = []
    for stage, files in (('stage1', s1.get('analysis_files') or []),
                         ('ml', [r.get('analysis_file') for r in entries])):
        if not isinstance(files, list) or any(not isinstance(f, str) or not f for f in files):
            issues.append(f'INVALID_ANALYSIS_FILE_LIST:{stage}')
            files = []
        expected = s1.get('article_count') if stage == 'stage1' else ml.get('article_count')
        if len(files) != expected or len(set(files)) != len(files):
            issues.append(f'ANALYSIS_FILE_COUNT_MISMATCH:{stage}')
        rows = {}
        for recorded in files:
            try:
                path = _locate(root, recorded)
                data = _read(path)
                article = data.get('article') or data
                url = canonical(article.get('source_url') or '')
                if not url or url in rows:
                    issues.append(f'INVALID_OR_DUPLICATE_ANALYSIS_URL:{stage}:{path.name}')
                rows[url] = article
                if stage == 'stage1':
                    source_path = _locate(root, data.get('input_file') or '')
                    source = _read(source_path)
                    if not data.get('content_hash') or data.get('content_hash') != source.get('content_hash'):
                        issues.append(f'SOURCE_CONTENT_CHANGED:{path.name}')
                    versions = sorted(source_path.parent.glob('v[0-9]*.json'))
                    if versions and versions[-1] != source_path:
                        newer = _read(versions[-1])
                        if newer.get('content_hash') != data.get('content_hash'):
                            issues.append(f'NEWER_STAGED_CONTENT:{path.name}')
            except (OSError, ValueError, TypeError, AttributeError):
                issues.append(f'MISSING_OR_INVALID_ANALYSIS:{stage}:{recorded}')
        populations[stage] = rows
    if set(populations['stage1']) != set(populations['ml']):
        issues.append('STAGE1_ML_MEMBERSHIP_MISMATCH')
    for entry in entries:
        if not entry.get('embedding_file') or not _locate(root, entry['embedding_file']).is_file():
            issues.append('MISSING_ML_EMBEDDING:'+str(entry.get('source_url')))
    return {'date': target, 'ready': not issues, 'article_count': count,
            'issues': issues, 'manifests': {k: str(v) for k,v in paths.items()}}
