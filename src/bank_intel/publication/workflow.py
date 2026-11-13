"""Deterministic edition compiler. No external web crawling, no unseen article injection."""
from __future__ import annotations
import csv
from datetime import date, timedelta
import hashlib
import html
import json
from pathlib import Path
import re
from .identity import SOURCES, SOURCE_KEYS, sources_for_date, approved,canonical,first_window_day,publisher_article_id,explain_match,reported_aspi_fall,possible_duplicate
from .backlog import earliest_unpublished_day
from .evidence import summary, evidence_issues, SUMMARY_WORDS
from .decisions import decide,why_it_matters
from .ranking import advise
from .writing import story_text,write_events,focus_label
from .reconcile import was_sent, prior_matches, group, read_sent_html, load_confirmed_history, load_unconfirmed_prior_candidates
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



def _verified_prior_edition_omission(*, signed: dict | None, assessment: dict,
                                     row: dict, matches: list,
                                     publisher_alias: bool,
                                     sent_report_root: Path | None,
                                     target: date) -> bool:
 """Recover a *confirmed missed* material prior-day story, never a repeat.

 Timing evidence remains in late_news_status. Only a separately reviewed
 omission with the exact confirmed-sent register and no evidence deficiencies
 may supersede the standard temporal exclusion. The ordinary news QA and
 cross-day duplicate checks still run. Other temporal overrides remain closed.
 """
 if not signed or signed.get('editorial_action') != 'INCLUDE':
  return False
 if str(signed.get('carry_forward_reason') or '').strip() != 'CONFIRMED_UNREPORTED_PRIOR_EDITION':
  return False
 reviewer=str(signed.get('reviewer') or '').strip()
 if not reviewer or reviewer.casefold() in {
     'your actual name', '<actual editor>', '<name>', 'reviewer', 'placeholder'}:
  return False
 if publisher_alias or matches or assessment.get('previous_send_status')!='NOT_IN_CONFIRMED_HISTORY':
  return False
 if assessment.get('evidence_issues'):
  return False
 if not str(signed.get('reason') or '').strip():
  return False
 try:
  pub=date.fromisoformat(str(row.get('publication_date') or '')[:10])
 except ValueError:
  return False
 prior_date=target-timedelta(days=1)
 if pub!=prior_date or sent_report_root is None:
  return False
 expected='data/processed/reports/'+prior_date.isoformat()+'/window_1d/SENT_EDITION.json'
 if str(signed.get('prior_report') or '').replace('\\','/').strip() != expected:
  return False
 register=Path(sent_report_root)/prior_date.isoformat()/'window_1d'/'SENT_EDITION.json'
 if not register.is_file():
  return False
 try:
  history=json.loads(register.read_text(encoding='utf-8-sig'))
 except (OSError,ValueError):
  return False
 if history.get('date')!=prior_date.isoformat() or history.get('state')!='DELIVERED_CONFIRMED_BY_OPERATOR':
  return False
 # Avoid accidentally treating a directly repeated source as a missed item.
 source=canonical(str(row.get('source_url') or ''))
 for event in history.get('events',[]) or []:
  urls=list(event.get('source_urls',[]) or [])
  for item in event.get('sources',[]) or []:
   if isinstance(item,str):
    urls.append(item)
   elif isinstance(item,dict):
    urls.append(item.get('source_url') or item.get('url') or '')
  if source and any(canonical(u)==source for u in urls):
   return False
 return bool(source)

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
  ass=reconcile(decide(row),(model_traces or {}).get(canonical(row.get('source_url',''))),row)
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
  matches,updates=prior_matches(row,prior)
  ass['previous_send_status']='ALREADY_SENT' if matches else ('POSSIBLE_UPDATE_OF_SENT_EVENT' if updates else ('NOT_IN_CONFIRMED_HISTORY' if prior else 'UNKNOWN'))
  ass['prior_reports']=sorted({p.get('distributed_report','') for p in matches+updates})
  # Say which sent story this matched, and on what evidence.
  ass['prior_match']=[explain_match(row,p) for p in (matches+updates)[:3]]
  if not publisher_alias and pub and earliest<=pub<=target and approved(row.get('source_name',''),row.get('source_url','')) and matches:
   ass.update(decision='ALREADY_SENT',reason='SENT_EDITION_SAME_EVENT');counts['sent']+=1
  elif updates and ass['decision']=='INCLUDE' and not row.get('material_update'):
   # A sent event with a new figure or outcome needs an editor's comparison.
   ass.update(decision='REVIEW',reason='POSSIBLE_MATERIAL_UPDATE_OF_SENT_EVENT')
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
      if str(override.get('same_event_as') or '').strip():
       ass['same_event_as']=canonical(override['same_event_as'])
      # The editor keeps this as its own story ("Sujeewa Mudalige quits NDB Board"
      # beside "NDB reconstitutes Board Subcommittees", 13 Nov 2026).
      if str(override.get('standalone') or '').strip().lower() in ('1','yes','true'):
       ass['standalone']=True
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
  if ass['decision'] in ('INCLUDE','REVIEW') and ass['late_news_status']=='AVAILABLE_BEFORE_PREVIOUS_SEND':
   # "Available before send" is NOT proof this event was reported. A confirmed
   # material omission is eligible only with separately signed, evidence-backed
   # editorial recovery; otherwise retain the original fail-closed exclusion.
   if ass['decision']=='INCLUDE' and _verified_prior_edition_omission(
           signed=signed, assessment=ass, row=row, matches=matches,
           publisher_alias=publisher_alias, sent_report_root=sent_report_root,
           target=target):
    ass['reason']='SIGNED_MATERIAL_PREVIOUS_OMISSION:'+str(signed.get('reason',''))
    ass['material_omission_recovery']=True
   else:
    ass.update(decision='EXCLUDE',reason='AVAILABLE_BEFORE_PREVIOUS_SEND')
  elif ass['decision']=='INCLUDE' and ass['late_news_status'] not in ('TODAY','LATE_DISCOVERY','PUBLISHED_AFTER_PREVIOUS_SEND'):
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
   points=reported_aspi_fall(x['article'])
   if points:observed.add(points)
  # One session cannot close both up and down: a report of the previous day's
  # fall ("Indices close in red") beside today's rise is a different session.
  moves={cse_direction(x['article'].get('headline','')) for x in members}-{''}
  if len(observed)>1 or len(moves)>1:
   for x in members:
    x['assessment'].update(decision='REVIEW',reason='CONFLICTING_SAME_CSE_SESSION_NUMBERS')
   counts['included']-=len(members);counts['review']+=len(members)
 counts['included']-=supersede_rupee_quotes(work)
 # Exact source lineage is retained; event count is deliberately not a story target.
 groups=group([x for x in work if x['assessment']['decision']=='INCLUDE'])
 # Two stories the rules keep apart that may still be one development (the same
 # speaker, names or phrase on the same day): the second waits for an editor.
 # A signed decision settles it, so this never holds a story twice.
 kept=[]
 for e in groups:
  if not any(str(m['assessment'].get('reason','')).startswith('SIGNED') for m in e['members']):
   twin=next((k for k in kept if any(possible_duplicate(m['article'],t['article']) for m in e['members'] for t in k['members'])),None)
   if twin is not None:
    for m in e['members']:
     m['assessment'].update(decision='REVIEW',reason='POSSIBLE_DUPLICATE_OF:'+str(twin['members'][0]['article'].get('headline',''))[:90])
    counts['included']-=len(e['members']);counts['review']+=len(e['members'])
    continue
  kept.append(e)
 # The same development may also be waiting in review under another paper's
 # wording. Then the included report waits with it, so the editor keeps one.
 waiting=[x for x in work if x['assessment']['decision']=='REVIEW']
 final=[]
 for e in kept:
  if not any(str(m['assessment'].get('reason','')).startswith('SIGNED') for m in e['members']):
   twin=next((w for w in waiting if any(possible_duplicate(m['article'],w['article']) for m in e['members'])),None)
   if twin is not None:
    for m in e['members']:
     m['assessment'].update(decision='REVIEW',reason='POSSIBLE_DUPLICATE_OF:'+str(twin['article'].get('headline',''))[:90])
    counts['included']-=len(e['members']);counts['review']+=len(e['members'])
    continue
  final.append(e)
 return work,final,counts,earliest


def cse_direction(headline:str)->str:
 """'up', 'down' or '' from a stock-market headline."""
 up=re.search(r'(?i)\b(?:green|gains?|rise[sn]?|rising|rebound\w*|rall(?:y|ies)|higher|buoyan\w*|surges?|climbs?|advances?|lifts?|positive)\b',headline)
 down=re.search(r'(?i)\b(?:red|falls?|fell|declin\w*|loss(?:es)?|lower|plunge\w*|drops?|slides?|dips?|profit.taking|negative|ends? .*rally)\b',headline)
 return 'up' if up and not down else 'down' if down and not up else ''


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
   title,happened,matters=story_text(ev,primary['headline'],summary(primary,SUMMARY_WORDS),why_it_matters(primary,ass),primary.get('body',''))
   priority=section=='KEY_BANKING_SUPERVISORY';card='priority-card' if priority else 'context-card'
   out.append('<article class="'+card+'" data-source-date="'+esc(primary['publication_date'])+'">')
   if priority:out.append('<div class="priority-number">'+str(i).zfill(2)+'</div><div class="priority-body">')
   else:out.append('<div class="context-card-head"><div>')
   out.append('<div class="story-topline">'+esc(primary['source_name'])+' <span class="dot">•</span> '+esc(primary['publication_date'])+'</div>')
   out.append('<h3>'+esc(title)+'</h3>')
   if not priority:out.append('</div></div><div class="compact-meta"><span class="topic-chip">'+esc(focus_label(ass['category']))+'</span></div>')
   else:out.append('<div class="meta-row supervisory-focus-row"><div class="meta-main"><div class="meta-label">Supervisory focus</div><div class="entity-name">'+esc(focus_label(ass['category']))+'</div></div></div>')
   out.append('<div class="summary-block"><div class="block-label">What happened</div><p class="context-summary">'+esc(happened)+'</p></div>')
   out.append('<div class="relevance-block"><div class="block-label">Why it matters</div><p>'+esc(matters)+'</p></div>')
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



def supersede_rupee_quotes(work:list[dict])->int:
 # Dealers' daily series: the close replaces the same day's earlier quote.
 # Returns how many included quotes were dropped; signed decisions stand.
 import re as _re
 def day(x):return str(x['article'].get('publication_date') or '')[:10]
 closed={day(x) for x in work if _re.search(r'(?i)\brupee (?:closes|closed|ends|ended)\b',str(x['article'].get('headline') or ''))}
 dropped=0
 for x in work:
  ass=x['assessment']
  if ass['decision']=='INCLUDE' and day(x) in closed and not str(ass.get('reason','')).startswith('SIGNED_EDITORIAL') \
     and _re.search(r'(?i)\brupee (?:at |\d)',str(x['article'].get('headline') or '')):
   ass.update(decision='EXCLUDE',reason='RUPEE_QUOTE_SUPERSEDED_BY_CLOSE');dropped+=1
 return dropped


def build(*,date_iso:str,audit:Path,inventory:Path,output:Path,sent:list[Path],css_file:Path,review:Path|None=None,source_status:Path|None=None,model_project_root:Path|None=None,sent_report_root:Path|None=None,window_days:int=1,model_traces:dict|None=None,collection_metadata:dict|None=None,writer=None):
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
 writing=write_events(events,draft=writer)
 ranking=advise(work,model_data,canonical)
 matched_model_traces=sum(bool(model_data.get(canonical(x['article'].get('source_url','')))) for x in work)
 ledger=make_ledger(work,events,date_iso)
 ledger_path=output/'publication_decisions.json'
 ledger_path.write_text(json.dumps(ledger,indent=2,ensure_ascii=False),encoding='utf-8')
 cards=[];issues=template_issues(Path(__file__).parent,css_file);coverage_defects=[]
 for e in events:
  primary=e['members'][0]['article'];ass=e['members'][0]['assessment']
  bad=evidence_issues(primary)
  if 'HEADLINE_BODY_TOPIC_MISMATCH' in bad or 'SHORT_SOURCE_TEXT' in bad:
   issues.append({'kind':'SOURCE_EVIDENCE','headline':primary['headline'],'details':bad})
  title,happened,matters=story_text(e,primary['headline'],summary(primary,SUMMARY_WORDS),why_it_matters(primary,ass),primary.get('body',''))
  # A story card without 'What happened' must never reach the reader.
  if not str(happened or '').strip():
   issues.append({'kind':'EMPTY_SUMMARY','headline':primary['headline']})
  # Nor one that opens mid-sentence ('the Central Bank ...', '- point', 'and X From').
  elif re.match(r'(?:[a-z]|[-–•]\s|(?:and|but|or|which|who)\s)',str(happened).strip()):
   issues.append({'kind':'SUMMARY_STARTS_MID_SENTENCE','headline':primary['headline'],'summary':str(happened)[:80]})
  cards.append({'headline':title,'source_headline':primary['headline'],'publication_date':primary['publication_date'],
    'section':ass['section'],'category':ass['category'],'reason':ass['reason'],
    'event_id':e['event_id'],'event_signature':e['event_signature'],'summary':happened,'why_it_matters':matters,
    'sources':[{'source_name':x['article']['source_name'],'source_url':x['article']['source_url']} for x in e['members']]})
 if not sent and not any(x['assessment']['previous_send_status']!='UNKNOWN' for x in work):
  issues.append({'kind':'PRIOR_SENT_EDITION_NOT_RECORDED'})
 if counts['review']:issues.append({'kind':'UNREVIEWED_CANDIDATES','count':counts['review']})
 missing_send={x['assessment']['late_news_status'].split(':',1)[1] for x in work
               if str(x['assessment'].get('late_news_status','')).startswith('PREVIOUS_SEND_TIME_MISSING:')}
 for edition in sorted(missing_send):
  issues.append({'kind':'PREVIOUS_SEND_TIME_MISSING','edition':edition,
                 'action':f'python scripts/news_release_gate.py record-send-time --date {edition} --sent-at <Outlook time> --operator <name>'})
 if model_project_root and not any((x.get('date')==date_iso) for x in model_data.values()):
  issues.append({'kind':'CURRENT_DATE_MODEL_EVIDENCE_UNAVAILABLE','date':date_iso})
 if source_status:
  for source in sources_for_date(date_iso):
   ident=SOURCE_KEYS[source]
   p=source_status/(ident+'.json')
   if not p.is_file():issues.append({'kind':'MISSING_PUBLISHER_STATUS','publisher':source});continue
   info=read_json(p)
   defects=status_defects(source,info,date_iso)
   # Defects are evidence for the signed coverage worksheet, which the
   # release audit enforces; blocking here too would make a recorded,
   # reviewed single-article failure impossible to clear.
   if defects:coverage_defects.append({'kind':'SOURCE_COVERAGE_PARTIAL','publisher':source,'evidence':str(p),'defects':defects,'failed_articles':info.get('extraction_failures',[])})
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
    'ml_action':a.get('ml_action',''),'model_evidence_found':a.get('ml_trace_found',False),
    'ranker_score':a.get('ranker_score',''),'ranker_suggestion':a.get('ranker_suggestion','')})
 write_csv(output/'article_decision_trace.csv',lines,list(lines[0]))
 review_rows=[{**x,'editorial_action':'','review_decision':'','review_notes':'','reviewer':''}
               for x in lines if x['decision']=='REVIEW']
 # The editor's time goes furthest with the likeliest keeps first.
 review_rows.sort(key=lambda x:-float(x['ranker_score'] or 0))
 write_csv(output/'review_queue.csv',review_rows,
           list(lines[0])+['editorial_action','review_decision','review_notes','reviewer'])
 manifest={'schema':'NEWS_INTEGRATED_V3','date':date_iso,'writing':writing,'ranking':ranking,'earliest_inclusive_date':earliest.isoformat(),
    'source_row_count':counts['raw'],'source_count':len(sources_for_date(date_iso)),'counts':counts,'event_count':len(events),
    'decision_ledger':'publication_decisions.json','decision_ledger_sha256':hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
    'renderer':'bank_intel.publication.workflow.render_html','template_css_sha256':hashlib.sha256(css_file.read_bytes()).hexdigest(),
    'sha256':sha,'source_audit_path':str(audit),'source_inventory_path':str(inventory),
    'confirmed_sent_inputs':[str(p) for p in sent],'sent_history_root':str(sent_report_root) if sent_report_root else None,'model_trace_coverage':matched_model_traces, 'model_traces_available':len(model_data),
    'model_trace_warnings':model_warnings[:40],'coverage_defects':coverage_defects,'issues':issues,
    'status': 'HOLD_FOR_REVIEW' if issues else 'QA_PASS_NOT_DELIVERED','events':cards}
 manifest['issues'].extend(validate_outputs(ledger,manifest,contents))
 manifest['status']='HOLD_FOR_REVIEW' if manifest['issues'] else 'QA_PASS_NOT_DELIVERED'
 write_csv(output/'final_article_inventory.csv',[{**r,'evidence_issues':';'.join(r['evidence_issues']),'prior_reports':';'.join(r['prior_reports'])} for r in ledger['articles']],list(ledger['articles'][0]))
 (output/'candidate_edition.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf-8')
 return manifest,path
