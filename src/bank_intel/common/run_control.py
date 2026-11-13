"""Decide how a scheduled run should start, and whether sources look healthy."""
from __future__ import annotations

from datetime import datetime, time
import json
from pathlib import Path
import statistics

# The editor moved the daily run to 08:00 on 11 Nov 2026 so the brief goes out earlier.
SCHEDULED_START = time(8, 0)
# A run that reached one of these needs no repeat today, only an editor.
COMPLETED = ('SUCCESS', 'REVIEW_REQUIRED', 'ALREADY_SENT')
FAR_BELOW_USUAL = 0.35
MINIMUM_USUAL = 6
HISTORY_DAYS = 10


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}


def collected_on(day: str, summaries: Path) -> bool:
    """True once a run today finished collection, so it need not be repeated."""
    for path in sorted(summaries.glob(f"news_pipeline_{day.replace('-', '')}_*.json")):
        steps = _read(path).get('steps') or []
        if any(str(s.get('name', '')).startswith('1/6 COLLECTION') and s.get('status') == 'SUCCESS' for s in steps):
            return True
    return False


def run_decision(now: datetime, status: dict, summaries: Path, force: bool = False) -> str:
    """How today's scheduled or login-triggered run should start."""
    day = now.date().isoformat()
    if force:
        return 'RUN_SKIP_COLLECTION' if collected_on(day, summaries) else 'RUN'
    if now.weekday() >= 5:
        return 'SKIP_WEEKEND'
    if now.time() < SCHEDULED_START:
        return 'SKIP_EARLY'
    if status.get('run_date') == day and status.get('status') in COMPLETED:
        return 'SKIP_DONE'
    return 'RUN_SKIP_COLLECTION' if collected_on(day, summaries) else 'RUN'


def source_health(status_root: Path, day: str) -> dict:
    """Compare today's collection with each source's recent normal."""
    history = status_root / 'history'
    report = {'date': day, 'sources': {}, 'problems': []}
    for path in sorted(status_root.glob('*.json')):
        if path.name == 'source_health.json':
            continue
        current = _read(path)
        key = path.stem
        candidates = int(current.get('candidate_urls') or 0)
        usual = []
        for folder in sorted(p for p in history.glob('20*') if p.is_dir() and p.name < day)[-HISTORY_DAYS:]:
            runs = sorted(folder.glob(f'{key}_*.json'))
            if runs:
                usual.append(int(_read(runs[-1]).get('candidate_urls') or 0))
        median = statistics.median(usual) if usual else None
        issues = []
        if not str(current.get('completed_at') or '').startswith(day):
            issues.append('NOT_COLLECTED_TODAY')
        elif candidates == 0 and not (current.get('health_status') == 'HEALTHY' and current.get('content_status') == 'NO_CURRENT_UPDATE'):
            # A healthy source that checked and found nothing new is just a quiet day.
            issues.append('NO_ARTICLES_FOUND')
        elif median is not None and len(usual) >= 3 and median >= MINIMUM_USUAL and candidates < median * FAR_BELOW_USUAL:
            issues.append('FAR_BELOW_USUAL')
        if int(current.get('fetch_failures') or 0) and not current.get('unresolved_fetch_urls'):
            issues.append('PAGES_REFUSED')
        report['sources'][key] = {'candidates': candidates, 'usual': median, 'issues': issues}
        report['problems'] += [f'{current.get("source_name") or key}: {issue}' for issue in issues]
    return report
