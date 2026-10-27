from pathlib import Path
from datetime import datetime
import shutil, subprocess, sys

root = Path.cwd()
brief = root / 'scripts/build_financial_brief_v2.py'
pipe = root / 'scripts/run_pipeline.py'
quality = root / 'scripts/news_quality_policy.py'
assert brief.is_file() and pipe.is_file(), 'Run in News Scrap project root'

policy = r'''
"""Production editorial policy: source-grounded, date-aware, fail closed."""
import re, json
from pathlib import Path
from datetime import timedelta

# Specific material actions, not mere financial buzzwords or promotional awards.
RULES = [
 (r'offshore lending.*stage 2', 'KEY_BANKING_SUPERVISORY','CREDIT_ASSET_QUALITY','BANK_FCY_CREDIT_RISK','HIGH'),
 (r'credit risk assessment.*(?:financial institutions|state guarantees)', 'BANKING_SECTOR_DEVELOPMENTS','CREDIT_ASSET_QUALITY','STATE_GUARANTEE_CREDIT_RISK','MEDIUM'),
 (r'(?:regulat\w*|supervis\w*).*microfinance sector', 'OTHER_FINANCIAL_SECTOR','OTHER_FINANCIAL_SECTOR','MICROFINANCE_REGULATION','MEDIUM'),
 (r'hlab.*fund|fund.*hlab', 'BANKING_SECTOR_DEVELOPMENTS','FINANCIAL_INCLUSION_CONSUMER','BUSINESS_GROWTH_FUND','LOW'),
 (r'green bond.*(?:dual.listed|listing|listed)', 'MONETARY_MARKETS_EXTERNAL','CAPITAL_MARKETS','GREEN_BOND_LISTING','LOW'),
 (r'tea export earnings', 'MONETARY_MARKETS_EXTERNAL','FX_RESERVES_EXTERNAL_SECTOR','EXPORT_FCY_EARNINGS','LOW'),
 (r'(?:fallout|loss|terminated).*logistics hub|logistics hub.*(?:fallout|loss|terminated)', 'MACRO_SYSTEMIC_CONTEXT','MACRO_GROWTH_INFLATION_FISCAL','PROJECT_FINANCIAL_FALLOUT','LOW'),
 (r'(?:ppps?|public.private partnership).*(?:bill|agency|cabinet)|(?:bill|agency|cabinet).*(?:ppps?|public.private partnership)', 'MACRO_SYSTEMIC_CONTEXT','MACRO_GROWTH_INFLATION_FISCAL','PPP_PROPOSAL','LOW'),
 (r'multilateral development banks?.*(?:financing|private investment)', 'MACRO_SYSTEMIC_CONTEXT','MACRO_GROWTH_INFLATION_FISCAL','MULTILATERAL_FINANCE','LOW'),
 (r'(?:sarvodaya development finance|licensed finance company).*(?:expands|branch|access to financial services)', 'OTHER_FINANCIAL_SECTOR','FINANCIAL_INCLUSION_CONSUMER','FINANCE_COMPANY_NETWORK','LOW'),
]
CRITICAL = re.compile(r'credit risk assessment|stage 2 loans?|non.performing loans?|prudential|bank fraud|financial intelligence unit|money laundering controls?|microfinance regulat\w*|capital adequacy|deposit.taking proposal|offshore lending|licensed banks? liquidity', re.I)
NOISE = re.compile(r'award|congratulat\w*|sponsor\w*|csr|ceremony', re.I)

def rescue(audit, event_key):
    fixed=[]
    for r in audit:
        h=str(r.get('headline') or '')
        for pat,section,category,reason,level in RULES:
            if re.search(pat,h,re.I):
                if r.get('section')!=section or r.get('category')!=category:
                    old=r.get('section')
                    r.update(section=section,category=category,reason=reason,importance=level)
                    r['event_key']=event_key(r)
                    r['policy_rescued_from']=old
                    fixed.append(h)
                break
    return [dict(r) for r in audit if r.get('section')!='EXCLUDE'], fixed

def source_summary(text):
    text=re.sub(r'\s+',' ',str(text or '')).strip()
    if not text: return ''
    for token in ('Dr.','Prof.','Mr.','Mrs.','Ms.','No.','Rs.','U.S.','PLC.'):
        text=text.replace(token,token.replace('.','§'))
    sentences=re.split(r'(?<=[.!?])\s+(?=[“"‘\(]?[A-Z0-9])',text)
    out=[]; count=0
    for item in sentences:
        item=item.replace('§','.').strip()
        if not item: continue
        n=len(item.split())
        if out and count+n>145: break
        if not out and n>145: return ' '.join(item.split()[:145]).rstrip(',;:')+'…'
        out.append(item); count+=n
        if count>=110: break
    return ' '.join(out)

def editorial_issues(events,audit,report_root,current_date):
    by_url={str(r.get('source_url')):r for r in audit}
    failures=[]
    for e in events:
        sources=e.get('sources') or []
        r=by_url.get(str(sources[0].get('source_url'))) if sources else None
        if not r:
            failures.append('NO_ARTICLE_FOR_SUMMARY:'+str(e.get('headline'))); continue
        body=str(r.get('body') or '')
        words=len(body.split())
        if words<30:
            failures.append('INSUFFICIENT_SOURCE_TEXT:'+str(e.get('headline'))); continue
        summary=source_summary(body)
        if summary.endswith('…') or (words>=140 and len(summary.split())<75):
            failures.append('INCOMPLETE_SUMMARY:'+str(e.get('headline'))); continue
        e['summary']=summary
    for r in audit:
        if r.get('section')=='EXCLUDE' and CRITICAL.search(str(r.get('headline',''))) and not NOISE.search(str(r.get('headline',''))):
            failures.append('MATERIAL_EXCLUDED:'+str(r.get('headline')))

    # Compare the same publication-day stories with previously generated editions.
    previous=[]
    for ago in range(1,4):
        d=current_date-timedelta(days=ago)
        f=Path(report_root)/d.isoformat()/'window_1d'/'stakeholder_brief_v2_audit.json'
        if f.is_file():
            try: obj=json.loads(f.read_text(encoding='utf-8-sig'))
            except (OSError,ValueError): continue
            if (obj.get('qa') or {}).get('status')=='PASS': previous+=obj.get('events',[])
    stop={'the','and','for','with','from','sri','lanka','its','new','bank','banks'}
    def tokens(h): return {x for x in re.findall(r'[a-z0-9]+',str(h).lower()) if len(x)>2 and x not in stop}
    for e in events:
        urls={str(s.get('source_url','')).split('?')[0].rstrip('/') for s in e.get('sources',[])}
        for prior in previous:
            if str(e.get('publication_date',''))[:10]!=str(prior.get('publication_date',''))[:10]: continue
            old_urls={str(s.get('source_url','')).split('?')[0].rstrip('/') for s in prior.get('sources',[])}
            a,b=tokens(e.get('headline')),tokens(prior.get('headline'))
            similar=len(a&b)>=5 and len(a&b)/max(1,min(len(a),len(b)))>=0.88
            if (urls & old_urls) or similar:
                failures.append('POSSIBLE_PREVIOUS_EDITION_DUPLICATE:'+str(e.get('headline')))
                break
    return failures
'''

original_brief = brief.read_text(encoding='utf-8-sig')
original_pipe = pipe.read_text(encoding='utf-8-sig')
if 'NEWS_PERMANENT_EDITORIAL_QA_V1' in original_brief:
    print('Already installed. No changes.')
    raise SystemExit(0)

def once(text, old, new, name):
    matches=text.count(old)
    if matches!=1: raise RuntimeError(f'Unexpected code layout for {name}: {matches} matches')
    return text.replace(old,new,1)

new_brief = once(original_brief,
    '    retained, audit = _brief_guard.before_group(retained, audit)\n\n',
    '''    retained, audit = _brief_guard.before_group(retained, audit)
    # NEWS_PERMANENT_EDITORIAL_QA_V1
    import news_quality_policy as _permanent
    retained, rescued = _permanent.rescue(audit, event_key)
    for headline in rescued: print('[EDITORIAL RESCUE]', headline)

''', 'classification')
new_brief = once(new_brief,
    '    events = _brief_guard.after_group(events, audit)\n\n    qa = validate_outputs(',
    '''    events = _brief_guard.after_group(events, audit)
    editorial_issues = _permanent.editorial_issues(events, audit, REPORT_ROOT, end_date)

    qa = validate_outputs(''', 'summary and prior edition QA')
new_brief = once(new_brief,
    '    # Independently verify semantic membership and source consistency.\n',
    '''    qa.setdefault('checks', {})['source_grounded_editorial_qa'] = not editorial_issues
    if editorial_issues:
        qa['failures'] = list(qa.get('failures', [])) + editorial_issues
        qa['status'] = 'FAIL'

    # Independently verify semantic membership and source consistency.
''', 'final QA')
lookback='    lookback_days = 3 if end_date.weekday() == 0 else 1\n'
if new_brief.count(lookback)!=2: raise RuntimeError('Late intake anchors changed')
new_brief=new_brief.replace(lookback,'    lookback_days = 3 if end_date.weekday() == 0 else 2\n')
new_brief=once(new_brief,
    '''            for item in payload.get("audit", []) or []:
                url = safe_text(item.get("source_url"))
                if url:
                    seen.add(url)
''',
    '''            # Only ALREADY RENDERED articles count as reported.
            if (payload.get("qa") or {}).get("status") != "PASS": continue
            for event in payload.get("events", []) or []:
                for source in event.get("sources", []) or []:
                    url = safe_text(source.get("source_url"))
                    if url: seen.add(url)
''', 'prior edition evidence')

preflight='''        # NEWS_SOURCE_FRESHNESS_GATE_V1: block before final HTML is created.
        from audit_news_flow import SOURCES, EXPECTED_SURFACES, iso_date
        gaps=[]
        for source in SOURCES:
            file=PROJECT_ROOT/'data'/'source_status'/f'{source}.json'
            if not file.is_file():
                gaps.append('MISSING_SOURCE:'+source); continue
            try:
                obj=load_json(file)
                day=iso_date(obj.get('completed_at'))
                names={str(s.get('name') or s.get('listing_name') or '').lower()
                       for s in (obj.get('discovery_surfaces') or []) if isinstance(s,dict)}-{''}
                if not day or day.isoformat()!=target_date: gaps.append('STALE_SOURCE:'+source)
                if obj.get('execution_status')!='SUCCESS': gaps.append('FAILED_SOURCE:'+source)
                if len(names)<EXPECTED_SURFACES[source]: gaps.append('INCOMPLETE_SURFACES:'+source)
                if obj.get('unresolved_fetch_urls') or int(obj.get('fetch_failures') or 0): gaps.append('UNRESOLVED_FETCHES:'+source)
                if obj.get('failed_discovery_surfaces'): gaps.append('FAILED_SURFACE:'+source)
            except (OSError,ValueError,TypeError) as exc:
                gaps.append('INVALID_SOURCE_STATUS:'+source+':'+type(exc).__name__)
        if gaps: raise RuntimeError('SEVEN-SOURCE PUBLICATION GATE: '+'; '.join(gaps))

'''
new_pipe=once(original_pipe,
    '        run_step(\n            name="6/6 FINAL FINANCIAL BRIEF",\n',
    preflight+'        run_step(\n            name="6/6 FINAL FINANCIAL BRIEF",\n', 'seven source preflight')
new_pipe=once(new_pipe,
    '''        if not args.skip_collection:
            flow_command.append("--enforce")
''',
    '''        # --skip-collection is NOT permission to waive source coverage.
        flow_command.append("--enforce")
''', 'source flow gate')

backup=root/'data'/'integration_backups'/('quality_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
backup.mkdir(parents=True, exist_ok=True)
shutil.copy2(brief,backup/brief.name)
shutil.copy2(pipe,backup/pipe.name)
created=not quality.exists()
try:
    quality.write_text(policy.strip()+'\n', encoding='utf-8')
    brief.write_text(new_brief, encoding='utf-8')
    pipe.write_text(new_pipe, encoding='utf-8')
    subprocess.run([sys.executable,'-m','compileall','-q',str(root/'scripts')],check=True)
    subprocess.run([sys.executable,'-m','pytest','-q',
        'tests/intelligence/test_news_pipeline_coverage_upgrade.py',
        'tests/intelligence/test_run_pipeline_dates.py'],check=True)
except Exception:
    shutil.copy2(backup/brief.name,brief)
    shutil.copy2(backup/pipe.name,pipe)
    if created: quality.unlink(missing_ok=True)
    print('[ROLLED BACK] Existing scripts restored after error')
    raise
print('[PASS] Permanent financial news quality fixes installed')
print('Backup:',backup)
print('Published October 21 HTML: unchanged')
