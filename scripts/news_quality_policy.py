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
        hlow=h.casefold()
        # Cross-agency financial crime / AML information sharing is material
        # to the banking-risk audience even if it does not name a bank.
        if (('inland revenue' in hlow or 'ird' in hlow or 'tax evaders' in hlow)
            and 'excise' in hlow
            and ('money laundering' in hlow or 'tax evasion' in hlow
                 or 'tax evaders' in hlow or 'join forces' in hlow)):
            if r.get('section') == 'EXCLUDE':
                r.update(section='OTHER_FINANCIAL_SECTOR',category='OTHER_FINANCIAL_SECTOR',
                         reason='FINANCIAL_CRIME_INFO_SHARING',importance='MEDIUM')
                r['event_key']='EDITORIAL_IRD_EXCISE_MOU_SIGNING_2026_10'
                fixed.append(h)
            continue
        # A confirmed inflation release or sector-wide food inflation
        # development is monetary context, not miscellaneous non-finance.
        if ('food inflation' in hlow and 'headline' in hlow
            and ('rate' in hlow or 'steady' in hlow)):
            if r.get('section') == 'EXCLUDE':
                r.update(section='MACRO_SYSTEMIC_CONTEXT',
                         category='MACRO_GROWTH_INFLATION_FISCAL',
                         reason='INFLATION_RELEASE',importance='MEDIUM')
                r['event_key']=event_key(r)
                fixed.append(h)
            continue
        for pat,section,category,reason,level in RULES:
            if re.search(pat,h,re.I):
                # A lower-priority corporate/product article may remain
                # retained by the primary classifier, but should never be
                # forcibly rescued from EXCLUDE just by sharing buzzwords.
                if level == 'LOW' and r.get('section') == 'EXCLUDE':
                    break
                if r.get('section')!=section or r.get('category')!=category:
                    old=r.get('section')
                    r.update(section=section,category=category,reason=reason,importance=level)
                    r['event_key']=event_key(r)
                    r['policy_rescued_from']=old
                    fixed.append(h)
                break
    return [dict(r) for r in audit if r.get('section')!='EXCLUDE'], fixed

def source_summary(text):
    from html import unescape
    text = str(text or '')
    for _ in range(3):
        decoded = unescape(text)
        if decoded == text:
            break
        text = decoded
    text = re.sub(r'\s+', ' ', text.replace(chr(160), ' ')).strip()
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

    # A generated QA PASS is not proof of distribution. Compare only
    # against source URLs/headlines from actual sent-edition manifests.
    from news_publication_history import published_cards, canonical
    previous=published_cards(Path(report_root), current_date, max_days=400)
    stop={'the','and','for','with','from','sri','lanka','its','new','bank','banks'}
    def tokens(h): return {x for x in re.findall(r'[a-z0-9]+',str(h).lower()) if len(x)>2 and x not in stop}
    for e in events:
        urls={canonical(s.get('source_url','')) for s in e.get('sources',[])}
        for prior in previous:
            old_urls={canonical(u) for u in prior.get('source_urls',[])}
            a,b=tokens(e.get('headline')),tokens(prior.get('headline'))
            similar=len(a&b)>=5 and len(a&b)/max(1,min(len(a),len(b)))>=0.92
            if (urls & old_urls) or similar:
                failures.append('POSSIBLE_PREVIOUS_EDITION_DUPLICATE:'+str(e.get('headline')))
                break
    return failures
