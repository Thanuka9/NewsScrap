"""Resume News Scrap at final brief with existing Stage1/FinBERT/corpus/taxonomy.

The historical 2026-10-27 run finished upstream stages but final builder failed.
This command does not fetch, reclassify or silently approve anything. Release is
withheld unless both source-flow and independent integrated QA pass.
"""
from __future__ import annotations
import argparse
from datetime import date
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
REPORTS = ROOT / 'data' / 'processed' / 'reports'

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True, help='YYYY-MM-DD')
    parser.add_argument('--days', default=1, type=int)
    args = parser.parse_args(argv)
    day = date.fromisoformat(args.date)
    if args.days != 1:
        parser.error('Resume is supported for the existing 1-day report run only')
    sys.path.insert(0,str(SCRIPTS))
    from news_publication_history import assert_edition_not_sent
    assert_edition_not_sent(REPORTS,day)
    output=REPORTS/day.isoformat()/'window_1d'
    if not output.parent.is_dir():
        raise SystemExit('No current date processed results: '+str(output.parent))
    # Never use a prior successful report from another run as proof of this run.
    prerequisites = [
        ROOT/'data'/'processed'/'ml'/args.date/'ml_manifest.json',
        ROOT/'data'/'processed'/'ml_corpus'/args.date/'window_1d'/'corpus_manifest.json',
        ROOT/'data'/'processed'/'taxonomy'/args.date/'window_1d'/'taxonomy_manifest.json',
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
    stale=output/'pre_resume_unsent_snapshot'
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
    try:
        for label,cmd in steps:
            print('\n=== '+label+' ===',flush=True)
            result=subprocess.run(cmd,cwd=str(ROOT),check=False)
            if result.returncode:
                rc=result.returncode
                print(f'[RESUME] {label} exited {rc}. No approved attachment.',flush=True)
                return rc
        result_file=output/'integrated_release_result.json'
        result=json.loads(result_file.read_text(encoding='utf-8-sig')) if result_file.is_file() else {}
        if result.get('status') != 'QA_PASS_NOT_DELIVERED':
            print('[RESUME] No independently approved edition; HOLD.',flush=True)
            return 2
        rc=0
        print('[RESUME] QA passed. Edition is NOT marked sent. Review final attachment before distribution.',flush=True)
        return 0
    finally:
        # On any exception/hold, remove any interim builder-produced attachment.
        # The independent gate alone may recreate these after successful QA.
        if rc != 0:
            for name in (f'Daily Financial Sector News Brief {args.date}.html','supervisory_digest.html'):
                (output/name).unlink(missing_ok=True)

if __name__=='__main__':
    raise SystemExit(main())
