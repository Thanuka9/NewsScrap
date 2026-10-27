"""Bounded, read-only carry-forward of unpublished daily editions.

Only an explicit on-disk held/review-required release is a backlog signal.
Generated HTML alone is not delivery evidence; a PUBLISHED_URLS.json or
confirmed SENT_EDITION.json is. Never mark a report sent or clear a QA hold.
"""
from __future__ import annotations
from datetime import date, timedelta
import json
from pathlib import Path


def default_earliest(target: date, days: int = 1) -> date:
    if days < 1:
        raise ValueError('days must be positive')
    return target - timedelta(days=(3 if target.weekday() == 0 else 1) if days == 1 else days - 1)


def earliest_unpublished_day(report_root: Path, target: date, *, days: int = 1,
                             max_backlog_days: int = 7) -> date:
    """Strict daily *publication* window, independent of held-edition status.

    Tuesday 2026-10-27 covers 26/27 only even when Monday was held.
    Monday may cover Friday/weekend as a separate explicit weekly rule.
    Prior sent and held editions remain available to event reconciliation,
    but cannot expand the next day's publication scope.
    """
    return default_earliest(target, days)



def merge_held_edition_evidence(*, target: date, report_root: Path,
                                current_audit: Path, current_inventory: Path,
                                destination: Path, max_backlog_days: int = 7):
    """Create an inspectable current-day and previous-day source union, never older days.

    This runs at the release boundary; the original daily inventory is never
    modified. A held/unsent prior report is a source of staged evidence, not
    proof that the article was already published. Confirmed-sent reconciliation
    is performed downstream by the normal historical gate.
    """
    import csv
    from .identity import canonical
    root=Path(report_root)
    earliest=earliest_unpublished_day(root,target,days=1,max_backlog_days=max_backlog_days)
    current_audit=Path(current_audit);current_inventory=Path(current_inventory)
    def read_j(path):
        return json.loads(path.read_text(encoding='utf-8-sig'))
    def read_c(path):
        with path.open(encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f);return list(reader),list(reader.fieldnames or [])
    current=read_j(current_audit)
    if not isinstance(current.get('audit'),list):raise ValueError('Current source audit missing articles')
    all_audit=current['audit']
    inventory,keys=read_c(current_inventory)
    all_selected={canonical(row.get('source_url','')):row for row in all_audit}
    all_indexed={canonical(row.get('source_url','')):row for row in inventory}
    if len(all_selected)!=len(all_audit) or len(all_indexed)!=len(inventory) or all_selected.keys()!=all_indexed.keys():
        raise ValueError('Current source inventory/audit mismatch')
    # Filter both halves together so the compiler's source/audit parity is exact.
    def in_scope(row):
        pub=str(row.get('publication_date') or '')[:10]
        return earliest.isoformat()<=pub<=target.isoformat()
    selected={url:row for url,row in all_selected.items() if in_scope(row)}
    indexed={url:all_indexed[url] for url in selected}
    added=[];source_days=[]
    for back in range(1,(target-earliest).days+1):
        previous=target-timedelta(days=back)
        folder=root/previous.isoformat()/'window_1d'
        # Never reuse a confirmed-sent edition's article population merely
        # because it remains on disk; sent events are compared separately.
        if any(folder.rglob('SENT_EDITION.json')) or any((folder/flag).is_file() for flag in ('PUBLISHED_URLS.json','PUBLISHED.ok')):
            continue
        status=folder/'integrated_release_result.json'
        if not status.is_file():continue
        state=read_j(status)
        if state.get('status') not in ('HOLD_FOR_REVIEW','REVIEW_REQUIRED'):continue
        old_audit=folder/f'Daily Financial Sector News Brief {previous.isoformat()} source_audit.json'
        if not old_audit.exists():old_audit=folder/'stakeholder_brief_v2_audit.json'
        old_inv=folder/f'Daily Financial Sector News Brief {previous.isoformat()} Articles.csv'
        if not old_audit.is_file() or not old_inv.is_file():
            raise ValueError('Held report source evidence missing: '+str(folder))
        package=read_j(old_audit)
        records=package.get('audit')
        if not isinstance(records,list):raise ValueError('Held source audit has invalid schema: '+str(old_audit))
        older,older_keys=read_c(old_inv)
        lookup={canonical(x.get('source_url','')):x for x in older}
        if len(lookup)!=len(older):raise ValueError('Held report has duplicate exact URLs: '+str(old_inv))
        day_count=0
        for article in records:
            url=canonical(article.get('source_url',''))
            pub=str(article.get('publication_date') or '')[:10]
            if not url or not earliest.isoformat()<=pub<=target.isoformat():continue
            if url in selected:continue
            inventory_row=lookup.get(url)
            if inventory_row is None:raise ValueError('Held article missing from source inventory: '+url)
            selected[url]=article
            indexed[url]=inventory_row
            added.append({'source_url':url,'publication_date':pub,'found_in':previous.isoformat()})
            day_count+=1
        if day_count:source_days.append({'held_edition':previous.isoformat(),'records_added':day_count})
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    audit_out=destination/'complete_window_source_audit.json'
    inventory_out=destination/'complete_window_articles.csv'
    payload=dict(current);payload['audit']=list(selected.values())
    audit_out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    all_rows=list(indexed.values())
    keys=list(dict.fromkeys(keys+[k for row in all_rows for k in row]))
    with inventory_out.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=keys,extrasaction='ignore')
        writer.writeheader();writer.writerows(all_rows)
    manifest={'status':'SOURCE_UNION_NOT_RELEASE_APPROVAL','target_date':target.isoformat(),
       'earliest_inclusive':earliest.isoformat(),'original_current_count':len(current['audit']),
       'excluded_out_of_scope_current_count':len(current['audit'])-len(selected)+len(added),
       'full_window_count':len(selected),'added_from_held_editions':len(added),
       'held_source_days':source_days,'added_source_urls':added,
       'current_unchanged':True}
    (destination/'complete_window_merge_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return audit_out,inventory_out,manifest
