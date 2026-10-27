"""Source -> staged -> report visibility audit; no scraping, no ML reruns.

Fail closed for current-run, materially relevant staged news missing from
stakeholder review. It never invents summaries or forces inclusion.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SURFACES = {
    'daily_ft': 4, 'ceylon_today': 3, 'daily_mirror': 5,
    'island': 3, 'daily_news': 3, 'newsfirst': 5, 'the_morning': 2,
}
SOURCES = (
    'daily_ft', 'ceylon_today', 'daily_news', 'daily_mirror',
    'island', 'newsfirst', 'the_morning',
)
IMPORTANT = re.compile(
    r'\b(?:cbsl|central bank|licensed banks?|commercial banks?|bank of china|'
    r'nations trust bank|hsbc|microfinance|money laundering|aml|financial intelligence|'
    r'capital adequacy|prudential|deposits?|payments? infrastructure|clearing bank|'
    r'financial stability|fraud|cyber attacks?|cyber risk|iosco|regulator(?:y|s))\b',
    re.I,
)


# High-confidence headline agreement only: a same-entity follow-up with a
# different action must NOT be automatically treated as the same story.
EVENT_STOPWORDS = {
    'the', 'and', 'for', 'with', 'from', 'into', 'that', 'this',
    'sri', 'lanka', 'lankas', 'its', 'first', 'official', 'branch',
    'as', 'of', 'to', 'in', 'at', 'on', 'by', 'a', 'an', 'new',
}


def headline_tokens(value: str) -> set[str]:
    normalized = re.sub(r'\brenminbi\b', 'rmb', str(value).lower())
    return {
        token for token in re.findall(r'[a-z0-9]+', normalized)
        if len(token) > 2 and token not in EVENT_STOPWORDS
    }


def same_reported_event(headline: str, rendered_headlines: list[str]) -> bool:
    tokens = headline_tokens(headline)
    if len(tokens) < 4:
        return False
    for other in rendered_headlines:
        comparison = headline_tokens(other)
        if len(comparison) < 4:
            continue
        shared = tokens & comparison
        if len(shared) >= 4 and len(shared) / min(len(tokens), len(comparison)) >= 0.80:
            return True
    return False


def canon(url: str) -> str:
    parsed = urlsplit((url or '').strip())
    host = parsed.netloc.lower().removeprefix('www.')
    return urlunsplit(('https', host, parsed.path.rstrip('/'), '', '')) if host else ''


def iso_date(value) -> date | None:
    match = re.search(r'20\d\d-\d\d-\d\d', str(value or ''))
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(0))
    except ValueError:
        return None


def last_json(folder: Path) -> dict | None:
    for path in sorted(folder.glob('v*.json'), reverse=True):
        try:
            return json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            pass
    return None


def prior_report_urls(report_root: Path, target: date) -> set[str]:
    from news_publication_history import published_urls
    return published_urls(report_root, target)


def source_audit(root: Path, target: date, days: int) -> dict:
    start = target - timedelta(days=days - 1)
    from news_publication_history import earliest_eligible_day
    late_start = earliest_eligible_day(target) if days == 1 else start - timedelta(days=1)
    report = root / 'data' / 'processed' / 'reports' / target.isoformat() / f'window_{days}d'
    obj = json.loads((report / 'stakeholder_brief_v2_audit.json').read_text(encoding='utf-8-sig'))
    governed=report/'integrated_edition/publication_decisions.json'
    if governed.is_file():
        final=json.loads(governed.read_text(encoding='utf-8'))
        manifest=json.loads((governed.parent/'candidate_edition.json').read_text(encoding='utf-8'))
        obj={'audit':[{**r,'section':'EXCLUDE' if r['decision'] in ('EXCLUDE','ALREADY_SENT') else r['section'],'reason':r['decision_reason']} for r in final['articles']], 'events':manifest['events']}
    reviewed = {canon(x.get('source_url', '')): x for x in obj.get('audit', []) or []}
    rendered = {canon(x.get('source_url', '')) for event in obj.get('events', []) or []
                for x in event.get('sources', []) or []}
    earlier = prior_report_urls(root / 'data' / 'processed' / 'reports', start)
    rendered_headlines = [str(event.get('headline') or '')
                          for event in obj.get('events', []) or []]
    rows = []
    missing = []
    previously_reported_again = []
    material_exclusions = []
    covered_by_other_source = []
    breakdown = defaultdict(Counter)
    seen = set()
    for source in SOURCES:
        src = root / 'data' / 'staged' / source
        if not src.exists():
            continue
        for folder in src.glob('*/*'):
            if not folder.is_dir():
                continue
            payload = last_json(folder)
            if payload is None:
                continue
            run_day = iso_date(payload.get('collection_run_date') or payload.get('collected_at'))
            pub = iso_date(payload.get('published_at') or payload.get('publication_date'))
            if run_day is None or run_day > target or pub is None or not (late_start <= pub <= target):
                continue
            url = canon(payload.get('source_url') or payload.get('canonical_url') or '')
            if not url or url in seen:
                continue
            seen.add(url)
            headline = str(payload.get('headline') or '')
            item = reviewed.get(url)
            if url in rendered:
                decision = 'RENDERED'
            elif item is not None and item.get('section') == 'EXCLUDE':
                decision = 'REVIEWED_EXCLUDED'
            elif item is not None:
                decision = 'RETAINED_NOT_RENDERED'
            elif url in earlier:
                decision = 'ALREADY_PUBLISHED'
            elif same_reported_event(headline, rendered_headlines):
                decision = 'EVENT_COVERED_BY_OTHER_SOURCE_UNREVIEWED'
                covered_by_other_source.append(f'{source}: {headline} | {url}')
            elif pub < start:
                decision = 'LATE_UNREVIEWED'
            else:
                decision = 'CURRENT_UNREVIEWED'
            critical = bool(IMPORTANT.search(headline)) and decision in {
                'CURRENT_UNREVIEWED', 'LATE_UNREVIEWED', 'RETAINED_NOT_RENDERED',
            }
            if critical:
                missing.append(f'{source}: {headline} | {url}')
            if url in earlier and url in rendered and pub < start:
                previously_reported_again.append(f'{source}: {headline} | {url}')
            if decision == 'REVIEWED_EXCLUDED' and IMPORTANT.search(headline):
                material_exclusions.append(f'{source}: {headline} | {url}')
            breakdown[source][decision] += 1
            rows.append({
                'source': source, 'publication_date': pub.isoformat(),
                'collection_date': run_day.isoformat(), 'headline': headline,
                'source_url': url, 'decision': decision,
                'previous_edition': url in earlier,
                'potential_material_gap': critical,
                'exclusion_reason': item.get('reason', '') if item else '',
            })
    # Check collection status, including generic truncation and partial failures.
    status_issues = []
    for source in SOURCES:
        path = root / 'data' / 'source_status' / f'{source}.json'
        if not path.exists():
            status_issues.append(f'MISSING_SOURCE_STATUS:{source}')
            continue
        try:
            item = json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            status_issues.append(f'UNREADABLE_SOURCE_STATUS:{source}')
            continue
        completed = iso_date(item.get('completed_at'))
        if completed != target:
            status_issues.append(f'STALE_SOURCE_STATUS:{source}:{completed}')
        if item.get('execution_status') != 'SUCCESS':
            status_issues.append(f'FAILED_SOURCE:{source}')
        # Count distinct section names, not listing pages. A paginated
        # NewsFirst Latest surface must not masquerade as five sections.
        surfaces = item.get('discovery_surfaces') or []
        surface_names = {
            str(x.get('name') or x.get('listing_name') or '').strip().lower()
            if isinstance(x, dict) else str(x).strip().lower()
            for x in surfaces
        } - {''}
        if source == 'daily_mirror':
            required={'business','homepage','breaking news','top stories','latest news'}
            absent=required-surface_names
            if absent:
                status_issues.append('MISSING_REQUIRED_SURFACES:daily_mirror:'+','.join(sorted(absent)))
        if len(surface_names) < EXPECTED_SURFACES[source]:
            status_issues.append(
                f'INCOMPLETE_SECTION_COVERAGE:{source}:'
                f'{len(surface_names)}/{EXPECTED_SURFACES[source]}')
        if item.get('failed_discovery_surfaces'):
            status_issues.append(f'FAILED_SECTION_COVERAGE:{source}')
        if item.get('unresolved_fetch_urls') or int(item.get('fetch_failures', 0) or 0):
            status_issues.append(f'UNRESOLVED_SOURCE_FETCHES:{source}')
        if int(item.get('truncated_discovery_urls', 0) or 0):
            status_issues.append(f'TRUNCATED_DISCOVERY:{source}')
    return {
        'date': target.isoformat(), 'reviewed_count': len(reviewed),
        'rendered_unique_urls': len(rendered),
        'late_discovered_staged': sum(iso_date(r['publication_date']) < start for r in rows),
        'stage_status_by_source': {k: dict(v) for k, v in breakdown.items()},
        'material_unreviewed': missing,
        'already_reported_repeated': previously_reported_again,
        'material_exclusions_for_review': material_exclusions,
        'covered_by_other_source_unreviewed': covered_by_other_source,
        'collection_issues': status_issues,
        'status': 'PASS' if not missing and not status_issues and not previously_reported_again else 'FAIL',
        'rows': rows,
        'note': ('Only explicit PUBLISHED_URLS.json or PUBLISHED.ok proves distribution. '
                 'This trace covers staged articles, not publisher articles never discovered.'),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--end-date', required=True)
    ap.add_argument('--days', type=int, default=1)
    ap.add_argument('--root', type=Path, default=ROOT)
    ap.add_argument('--enforce', action='store_true')
    args = ap.parse_args()
    d = date.fromisoformat(args.end_date)
    result = source_audit(args.root, d, args.days)
    out = args.root / 'data' / 'processed' / 'news_flow_audit' / d.isoformat()
    out.mkdir(parents=True, exist_ok=True)
    (out / 'source_flow.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    cols = ('source', 'publication_date', 'collection_date', 'headline',
            'source_url', 'decision', 'previous_edition', 'potential_material_gap',
            'exclusion_reason')
    with (out / 'source_flow.csv').open('w', newline='', encoding='utf-8-sig') as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        writer.writerows(result['rows'])
    print('SOURCE FLOW AUDIT:', result['status'])
    print('Staged rows scanned:', len(result['rows']))
    print('Late staged rows:', result['late_discovered_staged'])
    for source, counts in result['stage_status_by_source'].items():
        print(source, counts)
    print('Material excluded stories for editorial review:', len(result['material_exclusions_for_review']))
    for issue in result['material_unreviewed'] + result['already_reported_repeated'] + result['collection_issues']:
        print('[ISSUE]', issue)
    print('Audit:', out / 'source_flow.json')
    if args.enforce and result['status'] != 'PASS':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
