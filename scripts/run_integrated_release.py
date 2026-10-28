"""Production release authority for News Scrap October integrated source.

The legacy report builder supplies source evidence only. This step rebuilds the
edition from the full audited extracted inventory, traces deployed FinBERT,
compares confirmed sent editions, validates source status and independent QA.
No final attachment is left at the ordinary send path on HOLD.
"""
from __future__ import annotations
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from bank_intel.publication.workflow import build


def write_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding='utf-8')


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--date', required=True)
    p.add_argument('--days', type=int, default=1)
    p.add_argument('--project-root', type=Path, default=ROOT)
    p.add_argument('--review-decisions', type=Path)
    args = p.parse_args(argv)
    root=args.project_root.resolve()
    date=args.date
    report=root/'data'/'processed'/'reports'/date/f'window_{args.days}d'
    title=f'Daily Financial Sector News Brief {date}'
    audit=report/'stakeholder_brief_v2_audit.json'
    inventory=report/(title+' Articles.csv')
    statuses=root/'data'/'source_status'
    script_dir=root/'scripts'
    output=report/'integrated_edition'
    history=root/'data'/'processed'/'reports'
    named=report/(title+'.html')
    alias=report/'supervisory_digest.html'
    legacy_folder=report/'legacy_builder_snapshot'
    preview_result=report/'integrated_release_result.json'
    report.mkdir(parents=True,exist_ok=True)
    # Freeze/lock is checked by the master before any collection or report
    # generation, but guard direct standalone invocation too.
    sys.path.insert(0,str(script_dir))
    from news_publication_history import assert_edition_not_sent
    from datetime import date as DTDate
    assert_edition_not_sent(history,DTDate.fromisoformat(date))
    for required in (audit,inventory,script_dir/'news_release_gate.py'):
        if not required.is_file():
            raise RuntimeError('Required October pipeline evidence missing: '+str(required))
    # Move the legacy builder's unapproved file out of the normal send path,
    # retaining it to inspect differences. Do not leave the old attachment
    # exposed if new integrated review ends in HOLD.
    legacy_folder.mkdir(parents=True,exist_ok=True)
    for source in (named,alias):
        if source.is_file():
            target=legacy_folder/source.name
            shutil.move(str(source),str(target))
    # Previously held reports may contain Friday/weekend publications that the
    # 27 October legacy source CSV (26/27 only) omitted. Reconstruct them from
    # actual immutable report evidence without pretending they were sent.
    from bank_intel.publication.backlog import merge_held_edition_evidence
    merged_audit,merged_inventory,source_merge=merge_held_edition_evidence(
        target=DTDate.fromisoformat(date),report_root=history,
        current_audit=audit,current_inventory=inventory,
        destination=output/'source_union')
    print('[HELD SOURCE UNION]', 'current=',source_merge['original_current_count'],
          'additional=',source_merge['added_from_held_editions'],
          'all=',source_merge['full_window_count'])
    if inventory.is_file():shutil.copyfile(inventory,legacy_folder/inventory.name)
    review=args.review_decisions or report/'news_material_editorial_decisions.csv'
    manifest,preview=build(
        date_iso=date,audit=merged_audit,inventory=merged_inventory,output=output,sent=[],
        review=review if review.is_file() else None,source_status=statuses,
        model_project_root=root,sent_report_root=history,window_days=args.days,
        css_file=root/'src'/'bank_intel'/'publication'/'news_style.css')
    # NEWS_OCT27_UNSENT_BACKLOG_AUDIT: a held Monday edition must be visible
    # in Tuesday's candidate inventory or specifically accounted for as sent.
    from bank_intel.publication.backlog_audit import audit_unpublished_carryover
    carryover=audit_unpublished_carryover(history,DTDate.fromisoformat(date),
                                          output/'article_decision_trace.csv')
    write_json(report/'late_carryforward_audit.json',carryover)
    if carryover.get('prior_edition_unpublished'):
        print('[HELD-EDITION CARRYOVER]',
              'priority=',carryover.get('previous_priority_candidates'),
              'present=',carryover.get('covered_in_today'),
              'already_sent=',carryover.get('previously_sent'),
              'unexplained=',len(carryover.get('missing_priority_candidates') or []))
    source_qa=report/'news_coverage_verified.json'
    gate=script_dir/'news_release_gate.py'
    prep=subprocess.run([sys.executable,str(gate),'prepare-coverage','--date',date,
                         '--project-root',str(root),'--out',str(source_qa)],
                         cwd=root,check=False)
    qa_path=report/'news_release_qa.json'
    if prep.returncode==0:
        command=[sys.executable,str(gate),'audit','--date',date,'--report',str(preview),
                 '--inventory',str(merged_inventory),'--coverage',str(source_qa),
                 '--decision-manifest',str(output/'candidate_edition.json'),
                 '--out',str(qa_path)]
        if review.is_file():command+=['--reviewed',str(review)]
        independent=subprocess.run(command,cwd=root,check=False)
    else:
        independent=None
    issues=list(manifest.get('issues') or []) + list(carryover.get('issues') or [])
    if prep.returncode!=0:issues.append({'kind':'COVERAGE_PREPARATION_FAILED','return_code':prep.returncode})
    if independent is None or independent.returncode!=0:
        issues.append({'kind':'INDEPENDENT_RELEASE_GATE_NOT_PASSED',
                       'return_code':independent.returncode if independent else None})
    if qa_path.is_file():
        independent_issues=json.loads(qa_path.read_text(encoding='utf-8-sig')).get('issues',[])
        issues.extend(x for x in independent_issues if x not in issues)
    shutil.copyfile(output/'final_article_inventory.csv',inventory)
    manifest['issues']=issues
    manifest['status']='HOLD_FOR_REVIEW' if issues else 'QA_PASS_NOT_DELIVERED'
    manifest['release_qa']=str(qa_path)
    write_json(output/'candidate_edition.json',manifest)
    # Preserve any existing report manifest and clearly mark it no longer
    # authoritative when integrated QA has not cleared.
    report_manifest=report/'report_manifest.json'
    original=json.loads(report_manifest.read_text(encoding='utf-8-sig')) if report_manifest.is_file() else {}
    # A version already approved by an editor stays the one at the send path;
    # a rebuild with different bytes needs that approval withdrawn first.
    prior_approval=output/'APPROVED_EDITION.json'
    if not issues and prior_approval.is_file():
        held=json.loads(prior_approval.read_text(encoding='utf-8'))
        if held.get('attachment_sha256')!=manifest['sha256']:
            issues.append({'kind':'DIFFERENT_VERSION_ALREADY_APPROVED','approved_by':held.get('reviewer'),
                           'approved_at':held.get('approved_at'),'approved_sha256':held.get('attachment_sha256')})
            manifest.update(issues=issues,status='HOLD_FOR_REVIEW')
            write_json(output/'candidate_edition.json',manifest)
    approved=not issues
    original.update({
       'status':'QA_PASS_NOT_DELIVERED' if approved else 'HOLD_FOR_REVIEW',
       'integrated_manifest':str(output/'candidate_edition.json'),
       'decision_ledger':str(output/'publication_decisions.json'),
       'article_inventory_role':'FINAL_GOVERNED_DECISIONS',
       'legacy_inventory':str(legacy_folder/inventory.name),
       'integrated_preview':str(preview), 'release_qa':str(qa_path),
       'release_issues':issues, 'final_attachment':str(named) if approved else None})
    if approved:
        # Only the integrated, independently audited bytes can become the
        # named attachment. Copy through a temporary file then replace.
        temporary=report/(title+'.verified.tmp')
        shutil.copyfile(preview,temporary)
        temporary.replace(named)
        shutil.copyfile(named,alias)
        digest=hashlib.sha256(named.read_bytes()).hexdigest()
        if digest!=manifest['sha256'] or alias.read_bytes()!=named.read_bytes():
            named.unlink(missing_ok=True);alias.unlink(missing_ok=True)
            raise RuntimeError('FINAL_ATTACHMENT_SHA_OR_ALIAS_MISMATCH')
        original['sha256']=digest
    else:
        named.unlink(missing_ok=True)
        alias.unlink(missing_ok=True)
    write_json(report_manifest,original)
    status={'status':original['status'],'date':date,'created_at':datetime.now(ZoneInfo('Asia/Colombo')).isoformat(),
       'articles':manifest['source_row_count'],'events':manifest['event_count'],
       'model_trace_coverage':manifest['model_trace_coverage'],'issues':issues,
       'preview':str(preview),'final_attachment':str(named) if approved else None}
    write_json(preview_result,status)
    print('[INTEGRATED RELEASE]',status['status'],'articles=',status['articles'],
          'events=',status['events'],'model_traces=',status['model_trace_coverage'])
    print('[PREVIEW]',preview)
    print('[RELEASE STATUS]',preview_result)
    if issues:print('[BLOCKERS]',json.dumps(issues[:12],ensure_ascii=False))
    else:
        # The sent register only knows what the operator records; an unrecorded
        # send leaves tomorrow's repeat and late-news checks without evidence.
        gate_cmd='python scripts/news_release_gate.py'
        print('[NEXT] approve :',gate_cmd,'promote --date',date,'--reviewer "<name>" --report "'+str(preview)+'" --qa "'+str(qa_path)+'"')
        print('[NEXT] after sending:',gate_cmd,'mark-sent --date',date,'--report "'+str(output/(title+'.html'))+'" --recipient-group "<group>" --sent-at <ISO time, e.g. '+date+'T09:00:00+05:30> --confirm-sent')
    return 0 if approved else 2

if __name__=='__main__':
    raise SystemExit(main())
