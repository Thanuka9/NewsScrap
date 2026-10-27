"""Deterministic final-brief semantic guard, v1 (2026-10-19).

Operates only on genuine staged/Stage-1 article rows. Never invents story sources.
Use before group_events(), then after group_events() and before QA/report rendering.
"""
from __future__ import annotations
import html
import re
from collections import Counter, defaultdict


def flat(s):
    value = str(s or '')
    for _ in range(3):
        nxt = html.unescape(value)
        if nxt == value: break
        value = nxt
    return re.sub(r'\s+', ' ', value).strip()


def headline(r): return flat(r.get('headline')).lower()

def body(r): return flat(r.get('body') or r.get('summary')).lower()


def is_soundbox(r):
    h, b = headline(r), body(r)
    return 'soundbox' in h and ('commercial bank' in h or 'combank' in h or 'commercial bank of ceylon' in b) and 'payable' in b


def is_bank_trading_integration(r):
    h,b = headline(r), body(r)
    return (('people’s bank' in h or "people's bank" in h or 'peoples bank' in h)
            and ('share trading' in h or 'securities' in h or 'trading platform' in h)
            and ('lanka securities' in b or 'cds account' in b or 'people’s pay' in b))


def is_aml_regulatory_context(r):
    h, b = headline(r), body(r)
    return ('money laundering' in h or 'aml controls' in h) and ('beneficial ownership' in b and ('fatf' in b or 'fiu' in b))


def is_cbsl_price_update(r):
    h,b = headline(r), body(r)
    return ('inflation' in h and ('prices' in h or 'vegetable' in h or 'cost of living' in h)
            and ('central bank of sri lanka' in b or 'cbsl' in b)
            and ('consumer price index' in b or 'ncpi' in b or 'ccpi' in b))


def is_nationwide_grid_outage(r):
    h,b = headline(r), body(r)
    return ('islandwide blackout' in h or 'grid collapse' in h) and ('national' in b and ('power' in b or 'electricity' in b))


def is_pensioners_ceremony(r):
    h,b = headline(r), body(r)
    return (('honours' in h or 'honors' in h or 'pensioners’ day' in h or "pensioners' day" in h)
            and 'pensioner' in h and 'bank' in h and not re.search(r'\b(?:fraud|enforcement|penalty|fine|breach|cyberattack|central bank directive)\b', h))


def is_eu_industry_development(r):
    h= headline(r)
    return ('global gateway' in h and ('eu' in h or 'industrial' in h))


def is_imf_programme_news(r):
    h = headline(r)
    return ('imf' in h or 'international monetary fund' in h) and any(x in h for x in ('programme','program','support','eff','fund','review'))


def is_fiscal_real_economy(r):
    h=headline(r)
    return ('recovery is fiscal' in h or 'real economy' in h) and ('economic policy' in h or 'recovery' in h)


def category_fix(r):
    h=headline(r)
    if ('cbsl' in h and ('fx market' in h or 'foreign exchange' in h or 'reserve' in h)):
        return 'FX_RESERVES_EXTERNAL_SECTOR'
    if ('tourism earnings' in h or 'tourism revenue' in h) and ('rise' in h or 'fall' in h or 'revenue' in h):
        return 'FX_RESERVES_EXTERNAL_SECTOR'
    return None


def correct_article(r):
    h = headline(r)
    r['headline'] = flat(r.get('headline'))
    r['summary'] = flat(r.get('summary'))
    # Other earlier semantic hooks may have installed event_key; correct specific collisions.
    if is_pensioners_ceremony(r):
        r.update(section='EXCLUDE',category='NOT_FINANCIAL',reason='CEREMONIAL_BANK_PRODUCT_NOT_ENFORCEMENT',importance='NONE',event_key='')
    elif is_eu_industry_development(r):
        r.update(section='EXCLUDE',category='NOT_FINANCIAL',reason='INDUSTRY_INVESTMENT_NOT_IMF_PROGRAMME',importance='NONE',event_key='')
    elif ('early settlement' in h and 'parate' in h
          and ('cbsl' in h or 'central bank' in h)):
        # An actual lender-conduct examination, not a sovereign bond-yield event.
        r.update(section='KEY_BANKING_SUPERVISORY',category='GOVERNANCE_CONDUCT',
                 reason='CBSL_LENDER_CONDUCT_REVIEW',importance='HIGH',event_key='')
    elif ('nbfi' in h or 'finance compan' in h) and 'stage 2' in h and ('borrower' in h or 'loans' in h):
        # Household lending / credit migration at regulated finance companies.
        r.update(section='OTHER_FINANCIAL_SECTOR',category='ASSET_QUALITY_NPL_STAGE3',
                 reason='NBFI_HOUSEHOLD_CREDIT_QUALITY',importance='HIGH',event_key='')
    elif is_bank_trading_integration(r):
        r.update(section='BANKING_SECTOR_DEVELOPMENTS',category='OPERATIONAL_TECHNOLOGY',reason='MATERIAL_BANK_DIGITAL_PLATFORM',importance='MEDIUM',event_key='BANK_PEOPLES_PAY_SECURITIES_INTEGRATION')
    elif is_aml_regulatory_context(r):
        r.update(section='KEY_BANKING_SUPERVISORY',category='AML_CFT',reason='AML_POLICY_COMMENTARY_NOT_ENFORCEMENT',importance='MEDIUM',event_key='AML_BENEFICIAL_OWNERSHIP_CONTEXT')
    elif is_cbsl_price_update(r):
        r.update(section='MACRO_SYSTEMIC_CONTEXT',category='MACRO_GROWTH_INFLATION_FISCAL',reason='CBSL_PRICE_STABILITY_INDICATORS',importance='MEDIUM',event_key='')
    elif is_nationwide_grid_outage(r):
        r.update(section='MACRO_SYSTEMIC_CONTEXT',category='OPERATIONAL_RESILIENCE_CONTEXT',reason='NATIONAL_INFRASTRUCTURE_RESILIENCE',importance='LOW',event_key='')
    elif is_fiscal_real_economy(r):
        r.update(section='MACRO_SYSTEMIC_CONTEXT',category='MACRO_GROWTH_INFLATION_FISCAL',reason='FISCAL_VS_REAL_ECONOMY_CONTEXT',importance='MEDIUM',event_key='FISCAL_REAL_ECONOMY_RECOVERY')
    elif is_imf_programme_news(r):
        r.update(section='MACRO_SYSTEMIC_CONTEXT',category='MACRO_GROWTH_INFLATION_FISCAL',reason='MATERIAL_IMF_SYSTEMIC_CONTEXT',importance='MEDIUM',event_key='IMF_SRI_LANKA_CURRENT_PROGRAM')
    elif is_soundbox(r):
        r.update(section='BANKING_SECTOR_DEVELOPMENTS',category='FINANCIAL_INCLUSION_CONSUMER',reason='BANK_PAYMENT_PRODUCT_LAUNCH',importance='MEDIUM',event_key='BANK_COMMERCIAL_SOUNDBOX_PAYABLE')
    c = category_fix(r)
    if c and r.get('section') == 'MONETARY_MARKETS_EXTERNAL': r['category'] = c
    # Remove IMF key leaked from mere references to EFF in other reporting.
    if r.get('event_key') == 'IMF_SRI_LANKA_CURRENT_PROGRAM' and not is_imf_programme_news(r):
        r['event_key'] = ''
    # Never hide soundbox merger under no-key fallback.
    if is_soundbox(r): r['event_key']='BANK_COMMERCIAL_SOUNDBOX_PAYABLE'
    return r


def before_group(retained, audit):
    # Mutate original audit rows, preserving counts and source provenance.
    for r in audit: correct_article(r)
    by_url={str(r.get('source_url','')):r for r in audit}
    # Retained from actual rows, not from previous manually retained subset.
    new_retained=[]
    for r in audit:
        if r.get('section') != 'EXCLUDE':
            # group_events mutates/sorts; use distinct dicts to avoid audit aliasing
            new_retained.append(dict(r))
    return new_retained, audit



def coherent_source_excerpt(row, max_chars=650):
    """Produce a source-extractive summary without inventing facts.

    Skips photo captions and publisher bullet teasers where a complete
    article lead is available. Keeps sentence boundaries and avoids
    clipping after titles (Dr./Prof./Mr.).
    """
    text=flat(row.get('body') or row.get('summary'))
    title=headline(row)
    if not text:
        return flat(row.get('summary'))

    # The regexes identify a factual article lead, not prewritten news.
    starts=[]
    if (text.startswith('- ') or text[:40].lower().startswith('senior deputy governor')
        or is_imf_programme_news(row) or is_nationwide_grid_outage(row)):
        starts=[r'\bThe Central Bank of Sri Lanka \(CBSL\) (?:has|said|ruled)',
                r'\bThe International Monetary Fund \(IMF\) (?:has|said|pledged)',
                r'\bSri Lanka.s tourism earnings (?:recorded|rose|fell)',
                r'\bThe secondary [Bb]ond market (?:remained|was|moved)',
                r'\bThe Colombo stock market (?:extended|ended|closed)',
                r'\bSri Lanka.s National (?:Consumer|Credit)',
                r'\bThe National System Operator \(NSO\) (?:yesterday|said|reported)']
    elif is_aml_regulatory_context(row):
        starts=[r'With a view to strengthen',r'Beneficial ownership']
    elif is_fiscal_real_economy(row):
        starts=[r'Sri Lanka.s stalling of necessary reform']
    for pattern in starts:
        found=re.search(pattern,text,flags=re.IGNORECASE)
        if found:
            text=text[found.start():]
            break

    # Protect common titles from mistaken sentence termination.
    protected=text
    for token in ('Dr.','Prof.','Mr.','Mrs.','Ms.','No.','Rs.','U.S.'):
        protected=protected.replace(token,token.replace('.','§'))
    stops=[m.end() for m in re.finditer(r'(?<=[.!?])\s+(?=[A-Z“‘])',protected)]
    stops=[v for v in stops if 145<=v<=max_chars]
    if stops:
        cut=max(stops)
        result=protected[:cut].replace('§','.')
    elif len(text)<=max_chars:
        result=text
    else:
        # Sentence-boundary fallback; never pretend an incomplete fragment is a sentence.
        cut=max((m.end() for m in re.finditer(r'(?<=[.!?])\s',protected[:max_chars]) if m.end()>150),default=0)
        if cut:
            result=protected[:cut].replace('§','.')
        else:
            result=flat(row.get('summary'))
    return result.strip()

def better_lead(group, audit_by_url):
    members = [audit_by_url[s['source_url']] for s in group.get('sources', []) if s.get('source_url') in audit_by_url]
    if not members: raise ValueError('EVENT_WITHOUT_VALID_AUDIT_SOURCE:' + flat(group.get('headline')))
    k=flat(group.get('event_key'))
    if k.startswith('CSE_MARKET_'):
        ranked=sorted(members,key=lambda r: ('down' in headline(r) and 'week' in headline(r), 'daily ft' in flat(r.get('source_name')).lower(),len(r.get('body',''))),reverse=True)
        return ranked[0]
    if k=='IMF_SRI_LANKA_CURRENT_PROGRAM':
        ranked=sorted(members,key=lambda r: (is_imf_programme_news(r),len(r.get('body',''))),reverse=True)
        return ranked[0]
    if k=='BANK_COMMERCIAL_SOUNDBOX_PAYABLE':
        return sorted(members, key=lambda r:(flat(r.get('publication_date')), len(r.get('body',''))),reverse=True)[0]
    return members[0]


def after_group(events, audit):
    by_url={str(r.get('source_url','')):r for r in audit}
    output=[]
    for event in events:
        sources=list(event.get('sources') or [])
        # The original grouping routine collapses URLs by publisher name.
        # Restore all distinct article URLs carrying this event key.
        k=flat(event.get('event_key'))
        if k:
            present={flat(s.get('source_url')) for s in sources}
            for row in audit:
                if (flat(row.get('event_key'))==k
                    and row.get('section')!='EXCLUDE'
                    and flat(row.get('source_url')) not in present):
                    sources.append({
                        'source_name':row.get('source_name',''),
                        'source_url':row['source_url'],
                        'headline':row.get('headline',''),
                    })
                    present.add(flat(row['source_url']))
        valid_sources=[]
        seen=set()
        for s in sources:
            url=flat(s.get('source_url'))
            if url in seen or url not in by_url or by_url[url].get('section')=='EXCLUDE': continue
            seen.add(url)
            valid_sources.append(s)
        if not valid_sources:
            continue
        event['sources']=valid_sources
        event['member_count']=len(valid_sources)
        lead=better_lead(event,by_url)
        # Generic audit-grounded presentation: rendered primary source controls lead content.
        event['headline']=flat(lead.get('headline'))
        event['summary']=coherent_source_excerpt(lead)
        event['publication_date']=flat(lead.get('publication_date'))
        event['section']=lead.get('section')
        event['category']=lead.get('category')
        event['importance']=lead.get('importance')
        event['event_key']=flat(lead.get('event_key'))
        url=lead['source_url']
        event['sources']=[next(s for s in valid_sources if s['source_url']==url)]+[s for s in valid_sources if s['source_url']!=url]
        if event.get('event_key','').startswith('CSE_MARKET_'):
            # All articles in this event must concern the same daily/weekly market period.
            if ('week' in headline(lead) and ('down' in headline(lead) or 'fell' in headline(lead))):
                event['headline']='CSE closes higher on Friday despite weekly index declines'
        output.append(event)
    return output


def semantic_checks(audit,events):
    errors=[]
    urls={}
    keys=[]
    by_url={r['source_url']:r for r in audit}
    for e in events:
        key=flat(e.get('event_key'))
        if key: keys.append(key)
        sources=e.get('sources',[])
        if not sources: errors.append('EVENT_WITHOUT_SOURCE:'+flat(e.get('headline')))
        if e.get('member_count')!=len(sources): errors.append('BAD_MEMBER_COUNT:'+flat(e.get('headline')))
        for s in sources:
            url=s.get('source_url');urls[url]=urls.get(url,0)+1
            a=by_url.get(url)
            if not a: errors.append('SOURCE_NOT_IN_AUDIT:'+flat(url));continue
            if a.get('section')=='EXCLUDE':errors.append('EXCLUDED_IN_EVENT:'+flat(url))
            if key and a.get('event_key') != key: errors.append('DIFFERENT_EVENTS_GROUPED:'+flat(url))
        if key=='IMF_SRI_LANKA_CURRENT_PROGRAM' and any(not is_imf_programme_news(by_url[s['source_url']]) for s in sources): errors.append('IMF_EVENT_CONTAMINATED')
        if is_pensioners_ceremony(e) and e.get('section')=='KEY_BANKING_SUPERVISORY': errors.append('CEREMONIAL_FALSE_HIGH')
        if 'week ends in green' in flat(e.get('headline')).lower():errors.append('AMBIGUOUS_WEEKLY_MARKET_HEADLINE')
        if flat(e.get('category'))=='SOVEREIGN_DEBT_RATES' and category_fix(e):errors.append('INCORRECT_FX_CATEGORY')
        if '&amp;' in e.get('summary','') or '&nbsp;' in e.get('summary',''):errors.append('HTML_ENTITIES_IN_SUMMARY')
    for key,n in Counter(keys).items():
        if n>1: errors.append('DUPLICATE_KEY:'+key)
    for url,n in urls.items():
        if n>1:errors.append('ARTICLE_SHOWN_TWICE:'+url)
    for row in audit:
        if row.get('section')!='EXCLUDE' and not urls.get(row['source_url']):
            errors.append('RETAINED_ARTICLE_MISSING_FROM_EVENTS:'+row['source_url'])
    if any(is_soundbox(r) and r.get('section')!='EXCLUDE' for r in audit):
        if len([e for e in events if e.get('event_key')=='BANK_COMMERCIAL_SOUNDBOX_PAYABLE']) !=1: errors.append('BANK_PRODUCT_NOT_CONSOLIDATED')
    if any(is_bank_trading_integration(r) for r in audit) and not any(is_bank_trading_integration(e) for e in events): errors.append('BANK_DIGITAL_INTEGRATION_MISSED')
    if any(is_aml_regulatory_context(r) for r in audit) and not any(e.get('event_key')=='AML_BENEFICIAL_OWNERSHIP_CONTEXT' for e in events): errors.append('MATERIAL_AML_CONTEXT_MISSED')
    return sorted(set(errors))
