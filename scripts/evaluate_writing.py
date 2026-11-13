"""Score the wording check on the operator's own stories, then on damaged copies.

The operator's edited stories are correct by definition, so a good check passes
them; copies with a changed figure or an invented name must fail. With --live,
fresh drafts are written for the same stories and checked the same way.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from bank_intel.publication.identity import canonical, publisher_article_id
from bank_intel.publication.reconcile import SentHTML
from bank_intel.publication.writing import default_draft, source_text, verify_draft


def bodies(day: str) -> dict:
    audit = json.loads((ROOT / f'data/processed/reports/{day}/window_1d/stakeholder_brief_v2_audit.json').read_text(encoding='utf-8'))
    found = {}
    for row in audit['audit']:
        for key in (canonical(row.get('source_url', '')), publisher_article_id(row.get('source_url', ''))):
            if key:
                found[key] = row
    return found


def stories(day: str, edition: Path) -> list[dict]:
    parser = SentHTML()
    parser.feed(edition.read_text(encoding='utf-8-sig'))
    known = bodies(day)
    out = []
    for story in parser.stories:
        rows = [known.get(canonical(u)) or known.get(publisher_article_id(u)) for u in story['source_urls']]
        rows = [r for r in rows if r and r.get('body')]
        if rows:
            records = [{'publisher': r.get('source_name', ''), 'date': str(r.get('publication_date', ''))[:10],
                        'headline': r.get('headline', ''), 'text': r.get('body', '')} for r in rows]
            out.append({'headline': story['headline'], 'summary': story['summary'], 'records': records,
                        'sources': [source_text(r) for r in records]})
    return out


def damaged(summary: str) -> list[str]:
    copies = []
    changed = re.sub(r'\d+(?:\.\d+)?', lambda m: str(float(m.group()) + 7), summary, count=1)
    if changed != summary:
        copies.append(changed)
    copies.append(summary + ' The Governor of Maldives Monetary Authority, Ahmed Zubair, also commented.')
    return copies


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--edition', action='append', nargs=2, metavar=('DATE', 'FILE'), required=True)
    parser.add_argument('--live', action='store_true', help='Draft fresh wording for each story (uses the paid API)')
    args = parser.parse_args()
    passed = caught = copies = 0
    total = []
    reasons = collections.Counter()
    draft = default_draft() if args.live else None
    if args.live and draft is None:
        raise SystemExit('No API credentials are configured.')
    live = collections.Counter()
    for day, path in args.edition:
        for story in stories(day, Path(path)):
            total.append(story)
            issues = verify_draft({'headline': story['headline'], 'what_happened': story['summary'], 'why_it_matters': 'Relevant to supervision.'}, story['sources'])
            if not issues:
                passed += 1
            for issue in issues:
                reasons[issue.split(':')[0]] += 1
                print(f'  {day} {story["headline"][:60]} | {issue}')
            for summary in damaged(story['summary']):
                copies += 1
                caught += bool(verify_draft({'headline': story['headline'], 'what_happened': summary, 'why_it_matters': 'Relevant to supervision.'}, story['sources']))
            if draft:
                result = draft(story['records'], [])
                live['verified' if result and not verify_draft(result, story['sources']) else 'rejected'] += 1
    print(json.dumps({'stories': len(total), 'operator_stories_passed': passed, 'false_alarms_by_kind': reasons,
                      'damaged_copies': copies, 'damaged_copies_caught': caught, 'live_drafts': live}, indent=1))


if __name__ == '__main__':
    main()
