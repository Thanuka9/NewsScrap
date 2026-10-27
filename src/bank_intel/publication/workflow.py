"""Deterministic edition compiler. No external web crawling, no unseen article injection."""
from __future__ import annotations
import csv
from datetime import date, timedelta
import hashlib
import html
import json
from pathlib import Path
from .identity import SOURCES, approved,canonical,first_window_day,publisher_article_id
from .backlog import earliest_unpublished_day
from .evidence import summary, evidence_issues
from .decisions import decide,why_it_matters
from .reconcile import was_sent, group, read_sent_html, load_confirmed_history, load_unconfirmed_prior_candidates
from .ml_trace import read_model_traces
from .model_policy import reconcile
from .ledger import make_ledger, validate_outputs, digest, template_issues
from .temporal import load_collection_metadata, late_status
from .coverage import status_defects

SECTION_ORDER=('KEY_BANKING_SUPERVISORY','BANKING_SECTOR_DEVELOPMENTS','OTHER_FINANCIAL_SECTOR','MONETARY_MARKETS_EXTERNAL','MACRO_SYSTEMIC_CONTEXT')
SECTION_TITLES={'KEY_BANKING_SUPERVISORY':'CBSL & Bank Supervisory Developments','BANKING_SECTOR_DEVELOPMENTS':'Banking Sector Developments & Risk Context', 'MONETARY_MARKETS_EXTERNAL':'Monetary, Markets & External Sector','MACRO_SYSTEMIC_CONTEXT':'Macro & Systemic Context','OTHER_FINANCIAL_SECTOR':'Other Financial-Sector Developments'}


def read_json(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def read_csv(path):
 with Path(path).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))


def write_csv(path, records,keys):
 with Path(path).open('w',newline='',encoding='utf-8-sig') as f:
  w=csv.DictWriter(f,fieldnames=keys,extrasaction='ignore');w.writeheader();w.writerows(records)


def load_data(audit_path:Path,inventory_path:Path) -> list[dict]:
 raw=read_json(audit_path)
 if 'audit' not in raw or not isinstance(raw['audit'],list):raise ValueError('Expected full article-level audit field in '+str(audit_path))
 audit=raw['audit'];inventory=read_csv(inventory_path)
 inv_urls={canonical(x['source_url']) for x in inventory};audit_urls={canonical(x.get('source_url','')) for x in audit}
 if '' in inv_urls or '' in audit_urls:raise ValueError('Invalid empty source URLs')
 if len(inv_urls)!=len(inventory):raise ValueError('Duplicate URLs in inventory')
 if len(audit_urls)!=len(audit):raise ValueError('Duplicate URLs in audit')
 if inv_urls!=audit_urls:raise ValueError(f'Inventory/audit disagreement: missing audit={len(inv_urls-audit_urls)}, missing inventory={len(audit_urls-inv_urls)}')
 by_url={canonical(x['source_url']):x for x in inventory}
 for row in audit:
  for key in ('publication_date','headline','source_name'):
   value=by_url[canonical(row['source_url'])].get(key)
   if value is not None and value!=row.get(key):raise ValueError('Inventory/audit factual disagreement: '+key+' '+row['source_url'])
 return audit


def prepare(*,date_iso:str,audit:Path,inventory:Path,sent_paths:list[Path],review_decisions:Path|None=None,sent_report_root:Path|None=None,model_traces:dict|None=None,window_days:int=1,collection_metadata:dict|None=None):
 target=date.fromisoformat(date_iso)
 if window_days<1:raise ValueError('window_days must be positive')
 earliest=(earliest_unpublished_day(sent_report_root,target,days=window_days)
           if sent_report_root else (first_window_day(target) if window_days==1 else target-timedelta(days=window_days-1)))
 data=load_data(audit,inventory)
 if not data:raise ValueError('No extracted records')
 prior=read_sent_html(sent_paths) if sent_paths else []
 if sent_report_root:
  extra,history_issues=load_confirmed_history(sent_report_root,date_iso)
  if history_issues: raise ValueError('Invalid confirmed publication history: '+ '; '.join(history_issues[:10]))
  prior.extend(extra)
 unconfirmed=load_unconfirmed_prior_candidates(sent_report_root,date_iso) if sent_report_root else []
 overrides={}
 if review_decisions and review_decisions.exists():
  for row in read_csv(review_decisions):
   url=canonical(row.get('source_url',''))
   if not url:continue
   if url in overrides:raise ValueError('Duplicate review decision: '+url)
   decision=str(row.get('editorial_action') or row.get('review_decision') or '').strip().upper()
   mapping={'INCLUDE':'INCLUDE','INCLUDE_REVIEWED':'INCLUDE',
            'EXCLUDE':'EXCLUDE','EXCLUDE_REVIEWED':'EXCLUDE',
            'OUT_OF_SCOPE':'EXCLUDE','SAME_EVENT_REPORTED':'EXCLUDE'}
   action=mapping.get(decision)
   if not action:continue
   rationale=str(row.get('reason') or row.get('review_notes') or '').strip()
   if not str(row.get('reviewer') or '').strip() or not rationale:
    raise ValueError('Unsigned editorial decision for '+url)
   # Reuse the existing gate's `review_decision` schema; other columns are
   # optional. Unsupported reasons do not silently turn into approvals.
   overrides[url]={**row,'editorial_action':action,'reason':rationale}
 # Keep the entire 320-row trace, but choose one representative for
 # alternate publisher URL paths to the same physical article. Prefer a
 # substantive, well-extracted source then the publisher's business-news path.
 preferred={}
 for candidate in data:
  pid=publisher_article_id(candidate.get('source_url',''))
  if not pid:continue
  score=(len(str(candidate.get('body') or candidate.get('summary') or '')),
         int('/business-news/' in str(candidate.get('source_url',''))),
         -len(str(candidate.get('source_url',''))))
  if pid not in preferred or score>preferred[pid][0]:
   preferred[pid]=(score,canonical(candidate['source_url']))
 work=[];counts={'raw':len(data),'in_window':0,'external_source':0,'excluded':0,'review':0,'included':0,'sent':0,'publisher_aliases':0}
 for row in data:
  row=dict(row)
  row.update((collection_metadata or {}).get(canonical(row.get('source_url','')),{}))
  try:pub=date.fromisoformat(row['publication_date'][:10])
  except ValueError:pub=None
  ass=reconcile(decide(row),(model_traces or {}).get(canonical(row.get('source_url',''))))
  pid=publisher_article_id(row.get('source_url',''))
  alias_of=(preferred.get(pid) or (None,None))[1] if pid else None
  publisher_alias=bool(alias_of and alias_of!=canonical(row.get('source_url','')))
  ass['raw_legacy_decision']=row.get('section')
  if not pub or not earliest<=pub<=target:
   ass.update(decision='EXCLUDE',reason='OUTSIDE_DATE_WINDOW')
  else:counts['in_window']+=1
  if not approved(row.get('source_name',''),row.get('source_url','')):
   ass.update(decision='EXCLUDE',reason='UNAPPROVED_SOURCE_DOMAIN');counts['external_source']+=1
  if publisher_alias:
   ass.update(decision='EXCLUDE',reason='ALIAS_OF_PUBLISHER_ARTICLE:'+str(alias_of))
   counts['publisher_aliases']+=1
  signed=overrides.get(canonical(row['source_url']))
  if signed and signed.get('new_fact') and signed.get('prior_report'):
   row['material_update']={k:signed.get(k,'') for k in ('reviewer','reason','new_fact','prior_report')}
  matches=[p for p in prior if was_sent(row,[p])]
  ass['previous_send_status']='ALREADY_SENT' if matches else ('NOT_IN_CONFIRMED_HISTORY' if prior else 'UNKNOWN')
  ass['prior_reports']=sorted({p.get('distributed_report','') for p in matches})
  if not publisher_alias and pub and earliest<=pub<=target and approved(row.get('source_name',''),row.get('source_url','')) and matches:
   ass.update(decision='ALREADY_SENT',reason='SENT_EDITION_SAME_EVENT');counts['sent']+=1
  # Detect overlap with a prior file that looks like an edition but lacks
  # delivery confirmation. Never treat such a file as SENT, or erase it.
  if ass['decision']=='INCLUDE' and unconfirmed and was_sent(row,unconfirmed):
   ass.update(decision='REVIEW',reason='POSSIBLE_REPEAT_FROM_UNCONFIRMED_PRIOR_EDITION')
  override=overrides.get(canonical(row['source_url']))
  if override and not publisher_alias and ass['decision']!='ALREADY_SENT':
   # reviewer may exclude a headline/body mismatch; cannot override unapproved source or window.
   if ass['reason'] not in ('UNAPPROVED_SOURCE_DOMAIN','OUTSIDE_DATE_WINDOW'):
    if override['editorial_action'].upper()=='INCLUDE' and 'HEADLINE_BODY_TOPIC_MISMATCH' in ass['evidence_issues']:
     ass['reason']='NEEDS_SOURCE_CORRECTION';ass['decision']='REVIEW'
    else:
     ass['decision']=override['editorial_action'].upper();ass['reason']='SIGNED_EDITORIAL_'+override['reason']
     if ass['decision']=='INCLUDE':
      # An editor can explicitly classify a recovered EXCLUDE record; a generic
      # macro fallback would misfile regulated finance-company or cyber news.
      wanted=str(override.get('editorial_section') or override.get('section') or '').strip().upper()
      if wanted:
       if wanted not in SECTION_ORDER:
        raise ValueError('Unsupported signed editorial section: '+wanted)
       ass['section']=wanted
      elif not ass.get('section'):
       ass['section']='MACRO_SYSTEMIC_CONTEXT'
      if not ass.get('category'):
       ass['category']={
        'KEY_BANKING_SUPERVISORY':'REGULATORY_RISK',
        'BANKING_SECTOR_DEVELOPMENTS':'BANKING_DEVELOPMENTS',
        'MONETARY_MARKETS_EXTERNAL':'MARKET_EXTERNAL',
        'MACRO_SYSTEMIC_CONTEXT':'SYSTEMIC_MACRO',
        'OTHER_FINANCIAL_SECTOR':'REGULATED_FINANCE'
       }[ass['section']]
     ass['reviewer']=override.get('reviewer')
  ass['late_news_status']=late_status(row,target,prior) if pub and earliest<=pub<=target else 'OUTSIDE_WINDOW'
  if ass['decision']=='INCLUDE' and ass['late_news_status'] not in ('TODAY','LATE_DISCOVERY','PUBLISHED_AFTER_PREVIOUS_SEND'):
   ass.update(decision='REVIEW',reason=ass['late_news_status'])
  if ass['decision']=='INCLUDE':counts['included']+=1
  elif ass['decision']=='REVIEW':counts['review']+=1
  elif ass['decision']=='EXCLUDE':counts['excluded']+=1
  work.append({'article':row,'assessment':ass})
 # If different publishers describe the same actual CSE session but report
 # incompatible numerical index changes, queue BOTH records. Publishing two
 # contradictory "same session" entries is not acceptable; automatically
 # selecting a numerical winner without source verification is also unsafe.
 import re as _re
 from collections import defaultdict as _defaultdict
 daily=_defaultdict(list)
 for x in work:
  if x['assessment']['decision']=='INCLUDE' and str(x['assessment']['event_signature']).startswith('CSE_DAILY_SESSION_'):
   daily[x['assessment']['event_signature']].append(x)
 for signature,members in daily.items():
  observed=set()
  for x in members:
   body=str(x['article'].get('body') or '')
   match=_re.search(r'(?i)(?:\bASPI\b|All Share Price Index).{0,130}?(?:down|fell|declined).{0,35}?([0-9]{1,3}\.[0-9]{1,2})\s+points\b',body)
   if match:observed.add(match.group(1))
  if len(observed)>1:
   for x in members:
    x['assessment'].update(decision='REVIEW',reason='CONFLICTING_SAME_CSE_SESSION_NUMBERS')
   counts['included']-=len(members);counts['review']+=len(members)
 # Exact source lineage is retained; event count is deliberately not a story target.
 groups=group([x for x in work if x['assessment']['decision']=='INCLUDE'])
 return work, groups,counts,earliest


def render_html(date_iso:str,events:list[dict],css:str)->str:
 esc=html.escape
 template_root=Path(__file__).parent
 template=(template_root/'report_template.html').read_text(encoding='utf-8')
 headers=json.loads((template_root/'section_headers.json').read_text(encoding='utf-8'))
 out=[]
 for section in SECTION_ORDER:
  evs=[e for e in events if e['members'][0]['assessment']['section']==section]
  if not evs:continue
  out.append('<section class="report-section">'+headers[SECTION_ORDER.index(section)])
  for i,ev in enumerate(evs,1):
   members=ev['members']; primary=members[0]['article'];ass=members[0]['assessment']
   priority=section=='KEY_BANKING_SUPERVISORY';card='priority-card' if priority else 'context-card'
   out.append('<article class="'+card+'" data-source-date="'+esc(primary['publication_date'])+'">')
   if priority:out.append('<div class="priority-number">'+str(i).zfill(2)+'</div><div class="priority-body">')
   else:out.append('<div class="context-card-head"><div>')
   out.append('<div class="story-topline">'+esc(primary['source_name'])+' <span class="dot">•</span> '+esc(primary['publication_date'])+'</div>')
   out.append('<h3>'+esc(primary['headline'])+'</h3>')
   if not priority:out.append('</div></div><div class="compact-meta"><span class="topic-chip">'+esc(ass['category'].replace('_',' ').title())+'</span></div>')
   else:out.append('<div class="meta-row supervisory-focus-row"><div class="meta-main"><div class="meta-label">Supervisory focus</div><div class="entity-name">'+esc(ass['category'].replace('_',' ').title())+'</div></div></div>')
   out.append('<div class="summary-block"><div class="block-label">What happened</div><p class="context-summary">'+esc(summary(primary))+'</p></div>')
   out.append('<div class="relevance-block"><div class="block-label">Why it matters</div><p>'+esc(why_it_matters(primary,ass))+'</p></div>')
   out.append('<div class="context-bottom"><div class="source-list"><strong>Sources: </strong>')
   out.append(' · '.join('<a target="_blank" rel="noopener noreferrer" href="'+esc(x['article']['source_url'],quote=True)+'">'+esc(x['article']['source_name'])+'</a>' for x in members))
   out.append('</div></div>')
   if priority:out.append('</div>')
   out.append('</article>')
  out.append('</section>')
 day=date.fromisoformat(date_iso)
 coverage='Reporting coverage: '+day.strftime('%A %d %B %Y')+' and verified late, previously unreported releases within the approved lookback window.'
 return (template.replace('{{CSS}}',css).replace('{{DISPLAY_DATE}}',day.strftime('%A, %d %B %Y'))
         .replace('{{DATE}}',date_iso).replace('{{TITLE_DATE}}',day.strftime('%d %B %Y'))
         .replace('{{COVERAGE}}',esc(coverage)).replace('{{CONTENT}}','\n'.join(out)))



def build(*,date_iso:str,audit:Path,inventory:Path,output:Path,sent:list[Path],css_file:Path,review:Path|None=None,source_status:Path|None=None,model_project_root:Path|None=None,sent_report_root:Path|None=None,window_days:int=1,model_traces:dict|None=None,collection_metadata:dict|None=None):
 model_data=model_traces or {};model_warnings=[]
 if model_project_root:
  # Read the actual analysis files BEFORE decisions so raw KEEP suppressed by
  # later rules becomes an explicit review case rather than a silent omission.
  candidate_dates=sorted({x.get('publication_date','')[:10] for x in load_data(audit,inventory)})
  model_data,model_warnings=read_model_traces(model_project_root,candidate_dates)
 work,events,counts,earliest=prepare(date_iso=date_iso,audit=audit,inventory=inventory,sent_paths=sent,review_decisions=review,sent_report_root=sent_report_root,model_traces=model_data,window_days=window_days,collection_metadata=collection_metadata if collection_metadata is not None else (load_collection_metadata(model_project_root,candidate_dates) if model_project_root else None))
 output.mkdir(parents=True,exist_ok=True)
 # Order is predictable, prioritise source publication recency within sections.
 events.sort(key=lambda e:(SECTION_ORDER.index(e['members'][0]['assessment']['section']),-int(e['members'][0]['article']['publication_date'].replace('-','')),
                          e['members'][0]['article']['headline'].casefold()))
 matched_model_traces=sum(bool(model_data.get(canonical(x['article'].get('source_url','')))) for x in work)
 ledger=make_ledger(work,events,date_iso)
 ledger_path=output/'publication_decisions.json'
 ledger_path.write_text(json.dumps(ledger,indent=2,ensure_ascii=False),encoding='utf-8')
 cards=[];issues=template_issues(Path(__file__).parent,css_file)
 for e in events:
  primary=e['members'][0]['article'];ass=e['members'][0]['assessment']
  bad=evidence_issues(primary)
  if 'HEADLINE_BODY_TOPIC_MISMATCH' in bad or 'SHORT_SOURCE_TEXT' in bad:
   issues.append({'kind':'SOURCE_EVIDENCE','headline':primary['headline'],'details':bad})
  cards.append({'headline':primary['headline'],'publication_date':primary['publication_date'],
    'section':ass['section'],'category':ass['category'],'reason':ass['reason'],
    'event_id':e['event_id'],'event_signature':e['event_signature'],'summary':summary(primary),'why_it_matters':why_it_matters(primary,ass),
    'sources':[{'source_name':x['article']['source_name'],'source_url':x['article']['source_url']} for x in e['members']]})
 if not sent and not any(x['assessment']['previous_send_status']!='UNKNOWN' for x in work):
  issues.append({'kind':'PRIOR_SENT_EDITION_NOT_RECORDED'})
 if counts['review']:issues.append({'kind':'UNREVIEWED_CANDIDATES','count':counts['review']})
 if model_project_root and not any((x.get('date')==date_iso) for x in model_data.values()):
  issues.append({'kind':'CURRENT_DATE_MODEL_EVIDENCE_UNAVAILABLE','date':date_iso})
 if source_status:
  for source in SOURCES:
   ident={'Daily FT':'daily_ft','Daily Mirror':'daily_mirror','Daily News':'daily_news','The Morning':'the_morning','The Island':'island','NewsFirst':'newsfirst','Ceylon Today':'ceylon_today'}[source]
   p=source_status/(ident+'.json')
   if not p.is_file():issues.append({'kind':'MISSING_PUBLISHER_STATUS','publisher':source});continue
   info=read_json(p)
   defects=status_defects(source,info,date_iso)
   if defects:issues.append({'kind':'SOURCE_COVERAGE_PARTIAL','publisher':source,'evidence':str(p),'defects':defects,'failed_articles':info.get('extraction_failures',[])})
 else:issues.append({'kind':'COVERAGE_NOT_AUDITED','message':'Supply current source_status snapshots for release review'})
 contents=render_html(date_iso,events,css_file.read_text(encoding='utf-8'))
 path=output/f'Daily Financial Sector News Brief {date_iso} QA.html'
 path.write_text(contents,encoding='utf-8')
 sha=hashlib.sha256(path.read_bytes()).hexdigest()
 lines=[]
 for x in work:
  r=x['article'];a=x['assessment']
  governed=next(z for z in ledger['articles'] if z['source_url']==canonical(r['source_url']))
  lines.append({'decision_id':governed['decision_id'],'article_id':governed['article_id'],'event_id':governed['event_id'],
    'previous_send_status':governed['previous_send_status'],'late_news_status':governed['late_news_status'],
    'first_discovered_at':governed['first_discovered_at'],'collected_at':governed['collected_at'],'published_at':governed['published_at'],
    'publication_date':r['publication_date'],'source_name':r['source_name'],'headline':r['headline'],'source_url':r['source_url'],
    'legacy_section':r.get('section',''),'decision':a['decision'],'decision_reason':a['reason'],'section':a.get('section') or '',
    'importance':a.get('importance','NONE'),'event_signature':a.get('event_signature',''),'evidence_issues':';'.join(a['evidence_issues']),
    'model_label_reported':(model_data.get(canonical(r['source_url'])) or {}).get('raw_stage_a',''),
    'model_operational_keep':(model_data.get(canonical(r['source_url'])) or {}).get('operational_keep',''),
    'model_stage_b_label':(model_data.get(canonical(r['source_url'])) or {}).get('stage_b_label',''),
    'ml_action':a.get('ml_action',''),'model_evidence_found':a.get('ml_trace_found',False)})
 write_csv(output/'article_decision_trace.csv',lines,list(lines[0]))
 review_rows=[{**x,'editorial_action':'','review_decision':'','review_notes':'','reviewer':''}
               for x in lines if x['decision']=='REVIEW']
 write_csv(output/'review_queue.csv',review_rows,
           list(lines[0])+['editorial_action','review_decision','review_notes','reviewer'])
 manifest={'schema':'NEWS_INTEGRATED_V3','date':date_iso,'earliest_inclusive_date':earliest.isoformat(),
    'source_row_count':counts['raw'],'source_count':len(SOURCES),'counts':counts,'event_count':len(events),
    'decision_ledger':'publication_decisions.json','decision_ledger_sha256':hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
    'renderer':'bank_intel.publication.workflow.render_html','template_css_sha256':hashlib.sha256(css_file.read_bytes()).hexdigest(),
    'sha256':sha,'source_audit_path':str(audit),'source_inventory_path':str(inventory),
    'confirmed_sent_inputs':[str(p) for p in sent],'sent_history_root':str(sent_report_root) if sent_report_root else None,'model_trace_coverage':matched_model_traces, 'model_traces_available':len(model_data),
    'model_trace_warnings':model_warnings[:40],'issues':issues,
    'status': 'HOLD_FOR_REVIEW' if issues else 'QA_PASS_NOT_DELIVERED','events':cards}
 manifest['issues'].extend(validate_outputs(ledger,manifest,contents))
 manifest['status']='HOLD_FOR_REVIEW' if manifest['issues'] else 'QA_PASS_NOT_DELIVERED'
 write_csv(output/'final_article_inventory.csv',[{**r,'evidence_issues':';'.join(r['evidence_issues']),'prior_reports':';'.join(r['prior_reports'])} for r in ledger['articles']],list(ledger['articles'][0]))
 (output/'candidate_edition.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf-8')
 return manifest,path
