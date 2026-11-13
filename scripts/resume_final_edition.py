"""Resume final reporting from verified existing analysis, with all release checks."""
from __future__ import annotations
import argparse
from datetime import date
import json
from pathlib import Path
import shutil
import subprocess
from _runtime import run_process, locked_main
from bank_intel.common.readiness import final_stage_readiness
from bank_intel.publication.contract import sent_record, validate_release
from run_pipeline import run_step, validate_phase2_structural_contract
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
REPORTS = ROOT / 'data' / 'processed' / 'reports'

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True, help='YYYY-MM-DD')
    parser.add_argument('--days', default=1, type=int)
    parser.add_argument('--review-decisions', type=Path,
                        help='Existing signed editorial CSV, if supplied by the editor.')
    args = parser.parse_args(argv)
    day = date.fromisoformat(args.date)
    if args.days != 1:
        parser.error('Resume is supported for the existing 1-day report run only')
    sys.path.insert(0,str(SCRIPTS))
    from news_publication_history import assert_edition_not_sent
    sent = sent_record(ROOT, args.date)
    if sent:
        print(json.dumps(sent))
        return 0
    assert_edition_not_sent(REPORTS,day)
    output=REPORTS/day.isoformat()/'window_1d'
    if not output.parent.is_dir():
        raise SystemExit('No current date processed results: '+str(output.parent))
    readiness = final_stage_readiness(ROOT, args.date, args.days)
    if not readiness['ready']:
        raise RuntimeError('FINAL_STAGE_RESUME_UNSAFE: '+'; '.join(readiness['issues']))
    validate_phase2_structural_contract(args.date)
    # Never use a prior successful report from another run as proof of this run.
    prerequisites = [
        ROOT/'data'/'processed'/'ml'/args.date/'ml_manifest.json',
        ROOT/'data'/'processed'/'collection_completeness'/args.date/'window_1d'/'collection_completeness_manifest.json',
    ]
    missing=[str(p) for p in prerequisites if not p.is_file()]
    if missing:
        raise SystemExit('Upstream evidence is missing; final-stage-only replay unsafe: '+'; '.join(missing))
    status=json.loads(prerequisites[-1].read_text(encoding='utf-8-sig'))
    if status.get('status') != 'PASS' or int(status.get('recovered_article_count') or 0):
        raise SystemExit('Collection-completeness manifest not a clean PASS')
    # Isolate all existing unsent HTML before a new builder attempts release.
    output.mkdir(parents=True,exist_ok=True)
    from datetime import datetime
    stale=output/'pre_resume_unsent_snapshot'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    for name in (f'Daily Financial Sector News Brief {args.date}.html','supervisory_digest.html'):
        old=output/name
        if old.is_file():
            stale.mkdir(parents=True,exist_ok=True)
            shutil.move(str(old),str(stale/name))
    steps = (
        ('FINAL BRIEF', [sys.executable,str(SCRIPTS/'build_financial_brief_v2.py'),
                         '--end-date',args.date,'--days',str(args.days)]),
        ('CROSS-DAY SOURCE FLOW', [sys.executable,str(SCRIPTS/'audit_news_flow.py'),
                                   '--end-date',args.date,'--days',str(args.days),'--enforce']),
        ('INTEGRATED RELEASE', [sys.executable,str(SCRIPTS/'run_integrated_release.py'),
                               '--date',args.date,'--days',str(args.days),'--project-root',str(ROOT)]),
    )
    rc=1
    records=[]
    try:
        for label,cmd in steps:
            if label == 'INTEGRATED RELEASE' and args.review_decisions:
                cmd += ['--review-decisions', str(args.review_decisions.resolve())]
            print('\n=== '+label+' ===',flush=True)
            try:
                code=run_step(name=label, command=cmd, steps=records,
                              review_exit=label == 'INTEGRATED RELEASE')
                if code == 2:
                    rc=2
                    print('[REVIEW REQUIRED] Complete the current review queue; the pipeline did not crash.',flush=True)
                    return 2
            except RuntimeError:
                rc=records[-1]['return_code'] if records else 1
                if label == 'INTEGRATED RELEASE' and rc == 2:
                    print('[REVIEW REQUIRED] Complete the current review queue; the pipeline did not crash.',flush=True)
                    return 2
                print(f'[RESUME] {label} exited {rc}. No approved attachment.',flush=True)
                raise
        result_file=output/'integrated_release_result.json'
        result=json.loads(result_file.read_text(encoding='utf-8-sig')) if result_file.is_file() else {}
        if result.get('status') != 'QA_PASS_NOT_DELIVERED':
            print('[RESUME] No independently approved edition; HOLD.',flush=True)
            rc=2
            return 2
        validated=validate_release(ROOT,args.date)
        if validated.get('status') != 'QA_PASS_NOT_DELIVERED':
            raise RuntimeError('Final release validation did not approve the edition: '+str(validated))
        rc=0
        print('[RESUME] QA passed. Edition is NOT marked sent. Review final attachment before distribution.',flush=True)
        return 0
    finally:
        from bank_intel.common.reliability import atomic_json
        from datetime import datetime
        from zoneinfo import ZoneInfo
        stamp=datetime.now(ZoneInfo('Asia/Colombo')).strftime('%Y%m%d_%H%M%S')
        atomic_json(ROOT/'data/run_summaries'/f'final_resume_{stamp}.json',
                    {'date':args.date,'status':'SUCCESS' if rc==0 else 'REVIEW_REQUIRED' if rc==2 else 'FAILED','steps':records})
        # On any exception/hold, remove any interim builder-produced attachment.
        # The independent gate alone may recreate these after successful QA.
        if rc != 0:
            for name in (f'Daily Financial Sector News Brief {args.date}.html','supervisory_digest.html'):
                (output/name).unlink(missing_ok=True)

if __name__=='__main__':
    raise SystemExit(locked_main(main))
