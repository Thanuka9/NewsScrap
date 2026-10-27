"""News Scrap: independent article/content/model provenance audit.

This module audits every candidate, not just the published subset. It NEVER
silently approves coverage, rewrites source records or marks a report sent.
Python stdlib only; safe to rerun and can be called after the legacy builder.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
from datetime import date,timedelta,datetime,timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlsplit,unquote

PUBLISHERS={'ft.lk':'Daily FT','dailymirror.lk':'Daily Mirror','dailynews.lk':'Daily News',
            'themorning.lk':'The Morning','island.lk':'The Island',
            'english.newsfirst.lk':'NewsFirst','newsfirst.lk':'NewsFirst',
            'ceylontoday.lk':'Ceylon Today','ceylontoday.lk':'Ceylon Today'}
STOP=set('a an as at and the in on by from for is to with of or it into its after more than new sri lanka bank banks said govt government their says'.split())
KEY_TERMS=re.compile(r'(?i)\b(?:cbsl|central bank|prudential|supervisory|capital adequacy|liquidity|fraud|banking|\w+ bank|finance|financial|microfinance|micro-finance|debenture|fitch|moody|credit|bond|t.bill|treasury|policy rate|payment|qr|forex|remittance|aml|fiu|cyber|malware|scam|certificate|budget|tax|imf|world bank|adb|loan|deposit|audit|auditor|corporate governance|cse|securities|inflation|interest rate|external reserves|economy|tourism revenue)\b')
HIGH_TERMS=re.compile(r'(?i)\b(?:cbsl|central bank|\w+ bank|finance company|finance plc|liquidity|fraud|cyber|scam|banking|debenture|fiu|aml|capital|depositor|resolution|impaired|loan|payment|foreign exchange|auditor|watch list|credit|reserves)\b')
CAPTION=re.compile(r'(?i)\b(?:from left:|pic by|photo by|photo:|pictured|share mou from left|exchange agreement in a bold|news published on by|latest news published on by|by by [A-Z])\b')
URLKEYS=('source_url','url','article_url','canonical_url')

def canonical(raw):
    p=urlsplit((raw or '').strip()); host=p.netloc.lower().removeprefix('www.')
    return ('https://'+host+p.path.rstrip('/')) if host else ''

def publisher(u):
    host=urlsplit(u).netloc.lower().removeprefix('www.')
    return next((s for d,s in PUBLISHERS.items() if host==d or host.endswith('.'+d)),None)

def tokens(s):
    return {x for x in re.findall(r'[a-z0-9]+', (s or '').casefold()) if x not in STOP and len(x)>2 and not x.isdigit()}

def overlap(a,b):
    a=tokens(a);b=tokens(b)
    return round(len(a&b)/len(a),3) if a else 0.0

class CardParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.cards=[];self.current=None;self.depth=0;self.tagstack=[];self.captured=None
    def handle_starttag(self,tag,attrs):
        d=dict(attrs);c=(d.get('class') or '').split()
        if tag=='article' and set(c)&{'priority-card','context-card'}:
            if self.current is not None:raise ValueError('Nested story cards')
            self.current={'headline':'','summary':'','source_urls':[],'publication_date':'','topline':''};self.depth=1;self.tagstack=[];return
        if self.current is None:return
        if tag not in {'img','br','hr','input','meta','link','source','wbr'}:self.depth+=1
        if 'story-topline' in c:self.tagstack.append(('topline',self.depth))
        if tag=='h3' and not any(t=='headline' for t,_ in self.tagstack):self.tagstack.append(('headline',self.depth))
        if 'summary-block' in c:self.tagstack.append(('block',self.depth))
        if tag=='p' and ('context-summary' in c or any(t=='block' for t,_ in self.tagstack)):
            self.tagstack.append(('summary',self.depth))
        if tag=='a' and d.get('href','').startswith(('http://','https://')):
            self.current['source_urls'].append(d['href'])
    def handle_data(self,data):
        if self.current is None:return
        for name in ('headline','summary','topline'):
            if any(t==name for t,_ in self.tagstack):self.current[name]+=data+' '
    def handle_endtag(self,tag):
        if self.current is None or tag in {'img','br','hr','input','meta','link','source','wbr'}:return
        self.tagstack=[(t,n) for t,n in self.tagstack if n!=self.depth]
        self.depth-=1
        if self.depth==0:
            for key in ('headline','summary','topline'):self.current[key]=' '.join(self.current[key].split())
            m=re.search(r'\b20\d\d-\d\d-\d\d\b',self.current['topline'])
            self.current['publication_date']=m.group(0) if m else ''
            self.current['source_urls']=list(dict.fromkeys(self.current['source_urls']))
            self.cards.append(self.current);self.current=None

def load_cards(path):
    p=CardParser();p.feed(Path(path).read_text(encoding='utf-8-sig'));return p.cards

def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def eligible_start(d):return d-timedelta(days=3 if d.weekday()==0 else 1)

def load_articles(csv_file):
    with Path(csv_file).open(encoding='utf-8-sig',newline='') as f: return list(csv.DictReader(f))

def extract_source_evidence(audit_file):
    if not audit_file or not Path(audit_file).is_file():return {}
    try: doc=load_json(audit_file)
    except (ValueError,OSError):return {}
    return {canonical(x.get('source_url','')):x for x in doc.get('audit',[]) if isinstance(x,dict) and canonical(x.get('source_url',''))}

def find_url(obj,depth=0):
    if not isinstance(obj,dict) or depth>2:return ''
    for k in URLKEYS:
        if obj.get(k) and isinstance(obj[k],str):return canonical(obj[k])
    for v in obj.values():
        if isinstance(v,dict):
            hit=find_url(v,depth+1)
            if hit:return hit
    return ''

def model_lineage(root, report_date):
    """Read actual Phase-2 per-article analysis files when available. Never guess probabilities."""
    result={}; latest=eligible_start(report_date)
    for day in (latest+timedelta(days=i) for i in range((report_date-latest).days+1)):
        path=root/'data/processed/ml'/day.isoformat()/'ml_manifest.json'
        if not path.is_file():continue
        try: manifest=load_json(path)
        except (OSError,ValueError):continue
        for item in manifest.get('articles',[]):
            raw=item.get('analysis_file')
            if not raw:continue
            full=Path(raw);full=full if full.is_absolute() else root/full
            if not full.is_file():continue
            try: rec=load_json(full)
            except (OSError,ValueError):continue
            url=find_url(item) or find_url(rec)
            if not url:continue
            rel=((rec.get('ml') or {}).get('supervisory_relevance') or {})
            stagea=rel.get('stage_a') or {};stageb=rel.get('stage_b') or {}
            result[url]={'stage_a_raw':stagea.get('predicted_label',''),
                         'stage_a_operational':rel.get('operational_label',''),
                         'operational_keep':rel.get('operational_keep',''),
                         'stage_a_score':stagea.get('confidence',stagea.get('score','')),
                         'stage_b':stageb.get('predicted_label',''),
                         'stage_b_executed':rel.get('stage_b_executed',''),
                         'relevance_architecture':rel.get('architecture',''),
                         'analysis_file':str(full)}
    return result

def event_fingerprint(x):
    """Only flag possible grouping for review, never auto-drop based on this key."""
    s=(x.get('headline','')+' '+x.get('summary','')).casefold()
    if 'adb' in s and ('$100' in s or '100 million' in s or '100 m' in s) and ('social protection' in s or 'eldercare' in s):return 'ADB_SOCIAL_PROTECTION_100M'
    if ('76th anniversary oration' in s or 'intelligent central bank' in s or ('shirin hamid' in s and 'central bank' in s)):return 'CBSL_76TH_ANNIVERSARY_ORATION'
    if ('cse' in s or 'aspi' in s or 'indices' in s or 'colombo stock' in s) and ('19,717' in s or '1.1 bn' in s or '1.1 billion' in s or '1.1bn' in s):return 'CSE_SESSION_ASPI_19717'
    if ('cse' in s or 'aspi' in s or 'indices' in s or 'colombo stock' in s) and ('19,684' in s or '825 million' in s or '825mn' in s or '825 mn' in s):return 'CSE_SESSION_ASPI_19684'
    return ''

def run(*,report,inventory,date_arg,out,project_root,audit_source=None):
    report=Path(report);inventory=Path(inventory);out=Path(out);root=Path(project_root)
    rows=load_articles(inventory);cards=load_cards(report);by_url={canonical(x['source_url']):x for x in rows}
    evidence=extract_source_evidence(audit_source)
    lineage=model_lineage(root,date_arg)
    earliest=eligible_start(date_arg).isoformat();end=date_arg.isoformat()
    issues=[];art_records=[]; cards_sources=set();groups=defaultdict(list)
    for c in cards:
        card_issues=[]; source_records=[]
        if not earliest<=c['publication_date']<=end:card_issues.append('OUTSIDE_MONDAY_AWARE_WINDOW')
        if not c['source_urls']:card_issues.append('MISSING_SOURCE_URL')
        if CAPTION.search(c['summary']):card_issues.append('CAPTION_OR_BOILERPLATE_IN_SUMMARY')
        for u in c['source_urls']:
            cu=canonical(u);cards_sources.add(cu)
            if not publisher(u):card_issues.append('UNAPPROVED_SOURCE_DOMAIN')
            if cu not in by_url:card_issues.append('SOURCE_NOT_IN_EXTRACTED_INVENTORY')
            ev=evidence.get(cu)
            if ev:
                h=overlap(c['headline'],ev.get('body') or ev.get('summary',''))
                s=overlap(c['summary'],ev.get('body') or ev.get('summary',''))
                if h<.30 and len(tokens(c['headline']))>=4:card_issues.append('HEADLINE_SOURCE_BODY_MISMATCH')
                if s<.12 and len(tokens(c['summary']))>=12:card_issues.append('SUMMARY_SOURCE_BODY_MISMATCH')
                source_records.append({'url':cu,'headline_body_overlap':h,'summary_body_overlap':s})
        fp=event_fingerprint(c)
        if fp:groups[fp].append(c['headline'])
        issues.extend({'kind':x,'headline':c['headline']} for x in sorted(set(card_issues)))
        art_records.append({'headline':c['headline'],'publication_date':c['publication_date'],
                            'source_urls':c['source_urls'],'issues':sorted(set(card_issues)),
                            'evidence':source_records,'possible_event_group':fp})
    duplicated_groups={key:titles for key,titles in groups.items() if len(titles)>1}
    for group,titles in duplicated_groups.items():issues.append({'kind':'POSSIBLE_DUPLICATE_EVENT','event_group':group,'headlines':titles})
    # All 428 rows are evaluated; classify high materiality exclusions as candidate leads.
    excluded=[];all_lineage=[]
    for row in rows:
        url=canonical(row.get('source_url',''))
        section=row.get('section',row.get('suggested_section',''))
        model=lineage.get(url,{})
        title=row.get('headline','')
        ev=evidence.get(url,{})
        body=(ev.get('body') or ev.get('summary') or '')
        hidden_financial_context=bool(re.search(r'(?i)\b(?:treasury account|capital expenditure|sovereign debt|fiscal stability|foreign exchange reserves|central bank|bank guarantees|banking system liquidity|bond market|cse watch list|tax administration|tourism revenue|financial results|corporate debt|interest rate)\b',body))
        candidate=(section=='EXCLUDE' and url not in cards_sources and (HIGH_TERMS.search(title) or hidden_financial_context))
        # Suppress only noise-like title patterns, not institutional capital/earnings/risks.
        all_lineage.append({'publication_date':row.get('publication_date',''),'source_name':row.get('source_name',''),
                    'headline':title,'source_url':row.get('source_url',''),'original_section':section,
                    'original_reason':row.get('reason',''),'appears_in_report':url in cards_sources,
                    'material_review_candidate':bool(candidate),
                    'hidden_financial_context':hidden_financial_context,
                    'stage_a_raw':model.get('stage_a_raw',''),
                    'stage_a_operational':model.get('stage_a_operational',''),
                    'stage_b':model.get('stage_b',''),
                    'model_analysis_file':model.get('analysis_file','')})
        if candidate:
            excluded.append({'publication_date':row.get('publication_date'),
                             'headline':title,'source_url':row.get('source_url'),
                             'original_reason':row.get('reason',''),
                             'stage_a_raw':model.get('stage_a_raw',''),
                             'stage_a_operational':model.get('stage_a_operational',''),
                             'stage_b':model.get('stage_b',''),
                             'human_decision':'','reviewer':'','review_notes':''})
    missing_lineage=len(rows)-sum(canonical(row.get('source_url','')) in lineage for row in rows)
    counts=Counter(item['kind'] for item in issues)
    result={'schema_version':'1.0','report_date':date_arg.isoformat(),
            'window_start':earliest,'report':str(report),'inventory':str(inventory),
            'report_sha256':hashlib.sha256(report.read_bytes()).hexdigest(),
            'inventory_count':len(rows),'distinct_inventory_urls':len(by_url),
            'report_story_count':len(cards),'report_source_count':len(cards_sources),
            'candidate_exclusions_to_review':len(excluded),
            'model_lineage_records_found':len(lineage),'model_lineage_not_available_for':missing_lineage,
            'audit_source_available':bool(evidence),'flags_by_type':dict(counts),
            'issues':issues,'report_cards':art_records,
            'status':'CONTENT_REVIEW_REQUIRED' if issues else 'NO_CONTENT_FLAGS',
            'coverage_certified':False,'model_recall_evaluated':False,
            'checked_at_utc':datetime.now(timezone.utc).isoformat()}
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    f=out.with_name('news_high_materiality_candidates.csv')
    with f.open('w',encoding='utf-8-sig',newline='') as h:
        if excluded:
            w=csv.DictWriter(h,fieldnames=list(excluded[0]));w.writeheader();w.writerows(excluded)
        else:csv.writer(h).writerow(['source_url','headline'])
    lineage_file=out.with_name('news_all_articles_lineage.csv')
    with lineage_file.open('w',encoding='utf-8-sig',newline='') as h:
        w=csv.DictWriter(h,fieldnames=list(all_lineage[0]) if all_lineage else ['headline']);w.writeheader();w.writerows(all_lineage)
    print('[ALL ARTICLES]',lineage_file)
    print('[NEWS INTEGRITY]',result['status'],len(rows),'source rows,',len(cards),'stories')
    print('[MODEL TRACE]',len(lineage),'records; never infer model errors from final rule exclusions')
    print('[CONTENT FLAGS]',dict(counts),'[MATERIAL EXCLUSIONS]',len(excluded))
    print('[REVIEW JSON]',out,'[REVIEW CSV]',f)
    # No false approval: integration reads status as part of final HOLD decision.
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--date',required=True)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--inventory',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--project-root',type=Path,default=Path.cwd())
    p.add_argument('--audit-source',type=Path,default=None)
    a=p.parse_args()
    if not a.report.is_file() or not a.inventory.is_file():p.error('Report and inventory must exist')
    run(report=a.report,inventory=a.inventory,date_arg=date.fromisoformat(a.date),out=a.out,project_root=a.project_root,audit_source=a.audit_source)

if __name__=='__main__':main()
