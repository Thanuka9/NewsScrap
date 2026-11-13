"""Replay the automatic decisions against every signed editorial correction.

Each signed decision records what the automatic system should have done:
a repeat of a sent story ("sent on 9 Nov"), a duplicate of a story already in
the edition ("Same ... as"), a story that belonged in the brief (INCLUDE), or
one that did not ("not related", awards, profiles). This replays decide() and
prior_matches() without the signed overrides and counts what still leaks.

  python scripts/leak_audit.py              report
  python scripts/leak_audit.py --baseline   also record the counts the tests hold to
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from bank_intel.publication.decisions import decide
from bank_intel.publication.identity import canonical, same_event
from bank_intel.publication.reconcile import load_confirmed_history, prior_matches

REPORTS = ROOT / 'data/processed/reports'
BASELINE = ROOT / 'data/training/labelling/leak_baseline.json'
REPEAT = re.compile(r'(?i)\bsent on\b|\bsent \d|already sent|covered on|\bwas sent\b|^sent on')
DUPLICATE = re.compile(r'(?i)^same\b|same .* as the .* (?:report|included)|merges with')
NOT_RELEVANT = re.compile(r'(?i)not (?:a )?(?:financial|related)|^award|profile feature|promotional|editor:|no development|out of scope')
# Signed exclusions that are editorial choices, not leaks of the automatic system.
CHOICE = re.compile(r'(?i)intraday|criminal case|court date|advertorial|feature, no new release|commentary\.?$')


# Signed decisions later found to be wrong, kept here with the reason rather than edited away.
LABEL_CORRECTIONS = {
    ('2026-10-30', 'Alliance Finance plans Rs.1bn bond issue'): ('REPEAT', 'Same bond as "AFC to raise Rs. 1 b via Sri Lanka’s first Orange Bond", sent 29 Oct.'),
    ('2026-11-02', 'Sunshine Holdings records strong growth'): ('', 'Superseded: listed-company results are carried (JKH, Hemas, CIC sent since).'),
    ('2026-11-04', 'Sunshine Holdings records strong growth'): ('', 'Superseded as on 2 Nov; "already reported" meant another paper, not a sent edition.'),
}


def signed_days() -> list[str]:
    return sorted(p.parents[1].name for p in REPORTS.glob('20*/window_1d/news_material_editorial_decisions.csv'))


def label(row: dict) -> str:
    note = str(row.get('review_notes') or row.get('reason') or '')
    decision = str(row.get('review_decision') or row.get('editorial_action') or '').upper()
    if decision.startswith('INCLUDE'):
        # Editor, 12 Nov 2026: of the Budget, only our domain. General Budget pieces
        # included before that instruction are superseded, not misses.
        from bank_intel.publication.decisions import decide
        if re.search(r'(?i)\bbudget', str(row.get('headline') or '')) and \
           decide({'headline': row.get('headline', ''), 'body': '', 'source_url': row.get('source_url', '')})['reason'] == 'BUDGET_OUTSIDE_DOMAIN':
            return ''
        return 'MISS'
    if CHOICE.search(note):
        return ''
    if REPEAT.search(note):
        return 'REPEAT'
    if DUPLICATE.search(note):
        return 'DUPLICATE'
    if NOT_RELEVANT.search(note):
        return 'NOISE'
    return ''


def automatic(day: str) -> tuple[dict, dict]:
    """The full automatic decision step for a day (rules, model, timing, sent
    history, grouping) with no signed overrides: URL -> assessment, URL -> event size."""
    from bank_intel.publication.workflow import prepare, load_data
    from bank_intel.publication.ml_trace import read_model_traces
    from bank_intel.publication.temporal import load_collection_metadata
    folder = REPORTS / day / 'window_1d'
    audit, inventory = folder / 'stakeholder_brief_v2_audit.json', folder / f'Daily Financial Sector News Brief {day} Articles.csv'
    dates = sorted({x.get('publication_date', '')[:10] for x in load_data(audit, inventory)})
    traces, _ = read_model_traces(ROOT, dates)
    work, events, _, _ = prepare(date_iso=day, audit=audit, inventory=inventory, sent_paths=[], review_decisions=None,
                                 sent_report_root=REPORTS, model_traces=traces, collection_metadata=load_collection_metadata(ROOT, dates))
    decided = {canonical(x['article']['source_url']): x for x in work}
    sizes = {canonical(m['article']['source_url']): len(e['members']) for e in events for m in e['members']}
    return decided, sizes


def audit_day(day: str) -> list[dict]:
    folder = REPORTS / day / 'window_1d'
    with (folder / 'news_material_editorial_decisions.csv').open(encoding='utf-8-sig') as stream:
        signed = list(csv.DictReader(stream))
    decided, sizes = automatic(day)
    out = []
    for row in signed:
        kind = label(row)
        item = decided.get(canonical(row['source_url']))
        if not item:
            continue
        correction = next((v for (d, k), v in LABEL_CORRECTIONS.items() if d == day and item['article']['headline'].startswith(k)), None)
        if correction:
            kind = correction[0]
        if not kind:
            continue
        auto = item['assessment']['decision']
        reason = str(item['assessment'].get('reason', ''))
        # Published before the previous edition went out: the story belonged to that
        # day. It counts as caught when today's rules would have taken it then.
        if kind == 'MISS' and reason == 'AVAILABLE_BEFORE_PREVIOUS_SEND' and decide(item['article'])['decision'] in ('INCLUDE', 'REVIEW'):
            auto = 'INCLUDE'
        # Another web address for an article already in the edition.
        if reason.startswith('ALIAS_OF_PUBLISHER_ARTICLE'):
            auto = 'INCLUDE' if kind == 'MISS' else 'EXCLUDE'
        if kind == 'REPEAT':
            leak = 'LEAKED' if auto == 'INCLUDE' else 'FLAGGED_ONLY' if auto == 'REVIEW' else ''
        elif kind == 'DUPLICATE':
            # Merged into another story's sources is right; standing alone is a duplicate,
            # unless its twin is the one held back as a possible duplicate of it.
            alone = sizes.get(canonical(row['source_url']), 1) == 1
            held = any(str(x['assessment'].get('reason', '')).startswith('POSSIBLE_DUPLICATE_OF:' + item['article']['headline'][:40])
                       for x in decided.values())
            leak = ('FLAGGED_ONLY' if held else 'LEAKED' if alone else '') if auto == 'INCLUDE' else 'FLAGGED_ONLY' if auto == 'REVIEW' else ''
        elif kind == 'MISS':
            leak = '' if auto == 'INCLUDE' else 'FLAGGED_ONLY' if auto == 'REVIEW' else 'LEAKED'
        else:
            leak = 'LEAKED' if auto == 'INCLUDE' else 'FLAGGED_ONLY' if auto == 'REVIEW' else ''
        out.append({'day': day, 'kind': kind, 'leak': leak, 'headline': item['article']['headline'], 'note': row.get('review_notes', ''),
                    'auto': auto + ':' + str(item['assessment'].get('reason', ''))[:30]})
    return out


def fast_counts(days: list[str] | None = None) -> dict:
    """Rules and sent-history matching only (no model, no timing): quick enough for
    the test suite, so the freeze refuses a change that lets more leak through.
    Only the given days count, so a newly signed day never moves the baseline."""
    leaks = collections.Counter()
    for day in (days if days is not None else signed_days()):
        folder = REPORTS / day / 'window_1d'
        audit = {canonical(r['source_url']): r for r in json.loads((folder / 'stakeholder_brief_v2_audit.json').read_text(encoding='utf-8'))['audit']}
        history, _ = load_confirmed_history(REPORTS, day)
        with (folder / 'news_material_editorial_decisions.csv').open(encoding='utf-8-sig') as stream:
            signed = list(csv.DictReader(stream))
        for row in signed:
            kind, article = label(row), audit.get(canonical(row['source_url']))
            if not article:
                continue
            correction = next((v for (d, k), v in LABEL_CORRECTIONS.items() if d == day and article['headline'].startswith(k)), None)
            if correction:
                kind = correction[0]
            if not kind:
                continue
            auto = decide(article)['decision']
            if kind == 'REPEAT':
                already, updates = prior_matches(article, history)
                leaks[kind] += auto == 'INCLUDE' and not already and not updates
            elif kind == 'MISS':
                leaks[kind] += auto == 'EXCLUDE'
            elif kind == 'NOISE':
                leaks[kind] += auto == 'INCLUDE'
    return {k: leaks.get(k, 0) for k in ('REPEAT', 'MISS', 'NOISE')}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--baseline', action='store_true')
    parser.add_argument('--show', default='LEAKED', help='LEAKED, FLAGGED_ONLY or ALL')
    args = parser.parse_args()
    rows = [r for day in signed_days() for r in audit_day(day)]
    totals = collections.Counter((r['kind'], r['leak'] or 'CAUGHT') for r in rows)
    for kind in ('REPEAT', 'DUPLICATE', 'MISS', 'NOISE'):
        cases = {k[1]: v for k, v in totals.items() if k[0] == kind}
        print(f"{kind:9} {sum(cases.values()):4} cases | caught {cases.get('CAUGHT', 0):4} | review only {cases.get('FLAGGED_ONLY', 0):3} | leaked {cases.get('LEAKED', 0):3}")
    for r in rows:
        if args.show == 'ALL' or r['leak'] == args.show:
            print(f"  {r['leak'] or 'CAUGHT':12} {r['kind']:9} {r['day']} | {r['headline'][:70]} | {r['note'][:50]}")
    leaks = {kind: sum(1 for r in rows if r['kind'] == kind and r['leak'] == 'LEAKED') for kind in ('REPEAT', 'DUPLICATE', 'MISS', 'NOISE')}
    if args.baseline:
        fast = fast_counts()
        BASELINE.write_text(json.dumps({'leaked': leaks, 'fast_leaked': fast, 'cases': len(rows)}, indent=1), encoding='utf-8')
        print('baseline written:', leaks, '| fast:', fast)


if __name__ == '__main__':
    main()
