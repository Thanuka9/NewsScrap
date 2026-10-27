"""Check that material evidence in a held edition has not silently disappeared.

Review-only audit. Does not promote stories, approve source checks, or mark sent.
"""
from __future__ import annotations
from collections import Counter
import csv
from datetime import date, timedelta
import json
from pathlib import Path
from .identity import canonical
from .reconcile import load_confirmed_history, was_sent


def _rows(path: Path) -> list[dict]:
    with path.open(encoding='utf-8-sig',newline='') as fh:
        return list(csv.DictReader(fh))


def audit_unpublished_carryover(report_root: Path, target: date,
                                current_trace: Path) -> dict:
    previous=target-timedelta(days=1)
    base=Path(report_root)/previous.isoformat()/'window_1d'
    out={'target':target.isoformat(),'prior_date':previous.isoformat(),
         'prior_edition_unpublished':False,'previous_priority_candidates':0,
         'current_population':0,'covered_in_today':0,'previously_sent':0,
         'missing_priority_candidates':[],'date_distribution':{},'issues':[]}
    if not current_trace.is_file():
        out['issues'].append({'kind':'CURRENT_DECISION_TRACE_MISSING'})
        return out
    today=_rows(current_trace)
    out['current_population']=len(today)
    out['date_distribution']=dict(sorted(Counter(x.get('publication_date','')[:10] for x in today).items()))
    if not base.is_dir():return out
    if any(base.rglob('SENT_EDITION.json')) or any((base/name).is_file() for name in ('PUBLISHED_URLS.json','PUBLISHED.ok')):
        return out
    status=base/'integrated_release_result.json'
    prior_trace=base/'integrated_edition'/'article_decision_trace.csv'
    if not status.is_file() or not prior_trace.is_file():return out
    try:
        state=json.loads(status.read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):
        out['issues'].append({'kind':'PREVIOUS_RELEASE_STATUS_UNREADABLE'})
        return out
    if state.get('status') not in ('HOLD_FOR_REVIEW','REVIEW_REQUIRED'):return out
    out['prior_edition_unpublished']=True
    earlier=_rows(prior_trace)
    # This checks both previously selected content and undecided material;
    # entire news-site inventory remains subject to its ordinary QA process.
    priority=[x for x in earlier if x.get('decision') in ('INCLUDE','REVIEW')
              and previous.isoformat()<=str(x.get('publication_date') or '')[:10]<=target.isoformat()]
    out['out_of_scope_priority_not_carried'] = sum(1 for x in earlier
        if x.get('decision') in ('INCLUDE','REVIEW')
        and str(x.get('publication_date') or '')[:10]<previous.isoformat())
    out['previous_priority_candidates']=len(priority)
    now={canonical(x.get('source_url','')):x for x in today}
    sent,invalid=load_confirmed_history(report_root,target.isoformat())
    if invalid:
        out['issues'].append({'kind':'INVALID_SENT_HISTORY','details':invalid[:6]})
    for row in priority:
        url=canonical(row.get('source_url',''))
        if url in now:
            out['covered_in_today']+=1
            # A previously selected item should not become a silent out-of-scope
            # exclusion just because a later rule revision changes its label.
            new=now[url]
            if row.get('decision')=='INCLUDE' and new.get('decision')=='EXCLUDE' and new.get('decision_reason')=='OUTSIDE_DATE_WINDOW':
                out['missing_priority_candidates'].append({'headline':row.get('headline'),'source_url':row.get('source_url'),
                    'kind':'RECLASSIFIED_OUTSIDE_WINDOW'})
            continue
        if was_sent(row,sent):
            out['previously_sent']+=1
            continue
        out['missing_priority_candidates'].append({'headline':row.get('headline',''),
            'source_url':row.get('source_url',''), 'publication_date':row.get('publication_date',''),
            'prior_decision':row.get('decision',''),'kind':'NOT_PRESENT_AND_NOT_RECORDED_AS_SENT'})
    if out['missing_priority_candidates']:
        out['issues'].append({'kind':'HELD_EDITION_MATERIAL_CARRYOVER_GAP',
                              'count':len(out['missing_priority_candidates'])})
    return out
