"""Conservative event reconciliation and confirmed-sent handling."""
from __future__ import annotations
from collections import defaultdict
import re
from html.parser import HTMLParser
from pathlib import Path
from .identity import canonical,same_event,publisher_article_id,figures,_close

class SentHTML(HTMLParser):
 """Read the actual distributed facts, including nested summary markup."""
 def __init__(self):
  super().__init__(convert_charrefs=True)
  self.stories=[];self.card=None;self.stack=[]
 def handle_starttag(self,tag,attrs):
  d=dict(attrs);classes=set((d.get('class') or '').split())
  if tag=='article' and classes & {'priority-card','context-card'}:
   self.card={'headline':'','source_urls':[],'publication_date':'','summary':''};self.stack=[]
  if self.card is None:return
  self.stack.append((tag,classes))
  if tag=='a' and d.get('href','').startswith('http'):self.card['source_urls'].append(d['href'])
  if tag in {'br','img','meta','link','hr','input'}:self.stack.pop()
 def handle_data(self,data):
  if self.card is None:return
  if any(tag=='h3' for tag,_ in self.stack):self.card['headline']+=data
  if any('story-topline' in classes for _,classes in self.stack):
   m=re.search(r'20\d\d-\d\d-\d\d',data)
   if m:self.card['publication_date']=m.group()
  if any(classes & {'context-summary','story-summary'} for _,classes in self.stack) or (
      any('summary-block' in classes for _,classes in self.stack) and any(tag=='p' for tag,_ in self.stack)):
   self.card['summary']+=data
 def handle_endtag(self,tag):
  if self.card is None:return
  if tag=='article':
   self.card['summary']=' '.join(self.card['summary'].split())
   self.stories.append(self.card);self.card=None;self.stack=[];return
  for i in range(len(self.stack)-1,-1,-1):
   if self.stack[i][0]==tag:
    del self.stack[i:];break


def read_sent_html(paths: list[Path]) -> list[dict]:
 out=[]
 for p in paths:
  x=SentHTML();x.feed(p.read_text(encoding='utf-8-sig'))
  if not x.stories: raise ValueError('No story cards in explicit sent HTML: '+str(p))
  for s in x.stories:
   for u in s['source_urls']:
    out.append({'headline':s['headline'],'publication_date':s['publication_date'],'source_url':u,'body':s['summary'],'summary':s['summary'],
                'distributed_report':str(p),'report_sha256':__import__('hashlib').sha256(p.read_bytes()).hexdigest()})
 return out


def is_material_follow_up(row: dict, previous: dict) -> bool:
 """Only a signed comparison of new, source-grounded facts permits a repeat."""
 review=row.get('material_update')
 if not isinstance(review,dict):return False
 if not all(str(review.get(k) or '').strip() for k in ('reviewer','reason','prior_report','new_fact')):return False
 if review['prior_report']!=previous.get('distributed_report'):return False
 if str(row.get('publication_date',''))<=str(previous.get('publication_date','')):return False
 fact=' '.join(review['new_fact'].casefold().split())
 current=' '.join(str(row.get('body') or '').casefold().split())
 old=' '.join(str(previous.get('body') or previous.get('summary') or '').casefold().split())
 return bool(old and fact in current and fact not in old)


OUTCOME=re.compile(r'(?i)\b(?:oversubscri\w*|allot\w*|listed|listing|approv\w*|reject\w*|complet\w*|signs?|signed|'
                   r'opens?|opened|closes?|closed|default\w*|downgrad\w*|upgrad\w*|assign\w*|withdraw\w*|cancel\w*|suspend\w*|'
                   r'fines?|fined|penal\w*)\b')


def new_development(row:dict,previous:dict)->bool:
 """Headline carries a figure or outcome the sent story did not report."""
 h=str(row.get('headline') or '')
 reported=str(previous.get('summary') or previous.get('body') or '')
 old=str(previous.get('headline') or '')+' '+reported
 known=figures(old)
 if any(not any(_close(f,k) for k in known) for f in figures(h)):return True
 # A headline-only sent record cannot show which outcomes were reported.
 if not reported.strip():return False
 return any(m.group().casefold()[:6] not in old.casefold() for m in OUTCOME.finditer(h))


def prior_matches(row:dict,prior:list[dict])->tuple[list[dict],list[dict]]:
 """(already reported, same event with a possible material development)."""
 url=canonical(row.get('source_url',''));pid=publisher_article_id(row.get('source_url',''))
 sent=[];updates=[]
 for p in prior:
  if (url and url==canonical(p.get('source_url',''))) or (pid and pid==publisher_article_id(p.get('source_url',''))):
   sent.append(p);continue
  if not same_event(row,p):continue
  if is_material_follow_up(row,p):continue
  (updates if new_development(row,p) else sent).append(p)
 return sent,updates


def was_sent(row:dict,prior:list[dict])->bool:
 return bool(prior_matches(row,prior)[0])


SINGLE_EVENT_REASONS={'AML_MUTUAL_EVALUATION'}


def _days_apart(a:dict,b:dict)->int:
 from datetime import date
 try:return abs((date.fromisoformat(str(a.get('publication_date'))[:10])-date.fromisoformat(str(b.get('publication_date'))[:10])).days)
 except ValueError:return 99


def group(assessments:list[dict])->list[dict]:
 """Group single events, but preserve complete source links and article trace."""
 events=[]; keys={}
 for entry in assessments:
  row=entry['article'];sig=entry['assessment']['event_signature']; key=sig or canonical(row['source_url'])
  if key in keys:
   e=events[keys[key]]
   e['members'].append(entry)
   continue
  # Same development from another publisher joins the existing event. A
  # classification that names one specific event (an AML mutual evaluation)
  # on the same day is the same story even when the wording differs.
  reason=entry['assessment'].get('reason','')
  other=next((i for i,e in enumerate(events) if any(same_event(row,m['article'],max_gap_days=1) or
             (reason in SINGLE_EVENT_REASONS and m['assessment'].get('reason')==reason and
              _days_apart(m['article'],row)<=1)
             for m in e['members'])),None)
  if other is not None:
   events[other]['members'].append(entry)
   if sig:keys[key]=other
   continue
  keys[key]=len(events)
  events.append({'event_signature':sig,'members':[entry]})
 # Lead each merged event with the report carrying the most reported figures,
 # then the fullest text. Every publisher URL stays as evidence.
 for e in events:
  if len(e['members'])>1:
   e['members'].sort(key=lambda x:(-len(figures(x['article'].get('headline','')+' '+str(x['article'].get('body',''))[:1400])),
                                   -len(str(x['article'].get('body','')))))
 return events


def load_confirmed_history(report_root:Path,target_date:str,lookback_days:int|None=None)->tuple[list[dict],list[str]]:
 """Only explicit PUBLISHED_URLS or SENT_EDITION evidence; never QA PASS."""
 import json
 from datetime import date,timedelta
 target=date.fromisoformat(target_date);prior=[];issues=[]
 folders=sorted({p.parent for name in ('SENT_EDITION.json','PUBLISHED_URLS.json') for p in report_root.rglob(name)})
 for folder in folders:
  day=next((part for part in folder.relative_to(report_root).parts if re.fullmatch(r'20\d{2}-\d{2}-\d{2}',part)), '')
  if not day or day>=target_date:continue
  if lookback_days is not None and (target-date.fromisoformat(day)).days>lookback_days:continue
  sent=folder/'SENT_EDITION.json';lock=folder/'PUBLISHED_URLS.json'
  if sent.is_file():
   x=json.loads(sent.read_text(encoding='utf-8-sig'))
   if x.get('state')!='DELIVERED_CONFIRMED_BY_OPERATOR':
    issues.append('UNRECOGNIZED_SENT_STATE:'+day);continue
   if x.get('date')!=day or not x.get('events') or not x.get('attachment_sha256'):
    issues.append('INCOMPLETE_SENT_HISTORY:'+day);continue
   registry=folder/'sent_events.json'
   if registry.exists():
    registered=json.loads(registry.read_text(encoding='utf-8'))
    if registered.get('events')!=x.get('events') or registered.get('attachment_sha256')!=x.get('attachment_sha256'):
     issues.append('SENT_REGISTER_MISMATCH:'+day);continue
   for e in x.get('events') or []:
    if not e.get('sources') or not e.get('summary'):
     issues.append('INCOMPLETE_SENT_EVENT:'+day);continue
    for s in e.get('sources') or []:
     prior.append({'source_url':s.get('source_url',''),'headline':e.get('headline',''),
                   'publication_date':e.get('publication_date',''), 'summary':e.get('summary',''), 'body':e.get('reported_facts') or e.get('summary',''),
                   'event_signature':e.get('event_signature',''), 'distributed_report':x.get('attachment',''),
                   'sent_at':x.get('sent_at',''),'edition_date':day})
  elif lock.is_file():
   # The October production ledger stores *actual sent events* with
   # `source_urls` (plural), not the candidate compiler's `sources` array.
   x=json.loads(lock.read_text(encoding='utf-8-sig'))
   if x.get('report_date') != day:
    issues.append('SENT_RECORD_DATE_MISMATCH:'+day);continue
   urls=x.get('source_urls'); events=x.get('events')
   if not isinstance(urls,list) or not urls or not isinstance(events,list) or not events:
    issues.append('INCOMPLETE_SENT_HISTORY:'+day);continue
   canon_urls={canonical(u) for u in urls}
   if '' in canon_urls:
    issues.append('INVALID_SENT_URL:'+day);continue
   recorded=set()
   for e in events:
    if not isinstance(e,dict) or not e.get('headline'):
     issues.append('INVALID_SENT_EVENT:'+day);continue
    for url in e.get('source_urls') or []:
     c=canonical(url)
     if not c or c not in canon_urls:
      issues.append('SENT_EVENT_URL_NOT_IN_LOCK:'+day);continue
     recorded.add(c)
     prior.append({'source_url':url,'headline':str(e.get('headline') or ''),
                   'publication_date':str(e.get('publication_date') or ''), 'summary':str(e.get('summary') or ''), 'body':str(e.get('summary') or ''),
                   'event_signature':e.get('event_signature',''), 'distributed_report':x.get('report_path',''), 'sent_at':x.get('sent_at') or x.get('sent_at_utc',''),'edition_date':day})
   if recorded!=canon_urls:
    issues.append('SENT_URL_EVENT_INCONSISTENCY:'+day)
 return prior,issues


def load_unconfirmed_prior_candidates(report_root: Path, target_date: str, lookback_days: int=10) -> list[dict]:
 """Check previous *candidate* editions for overlap, without claiming delivery.

 We do not suppress stories using unconfirmed artifacts; we alert editors
 to likely repeats when an old final-looking file exists without a sent lock.
 """
 from datetime import date,timedelta
 out=[]
 target=date.fromisoformat(target_date)
 for n in range(1,lookback_days+1):
  day=(target-timedelta(days=n)).isoformat()
  folder=report_root/day/'window_1d'
  if (folder/'PUBLISHED_URLS.json').is_file() or any(folder.rglob('SENT_EDITION.json')):
   continue
  path=folder/(f'Daily Financial Sector News Brief {day}.html')
  if not path.is_file():
   alternatives=[folder/f'Daily Financial Sector News Brief {day} QA.html',folder/'legacy_builder_snapshot'/f'Daily Financial Sector News Brief {day}.html']
   path=next((p for p in alternatives if p.is_file()),path)
  if not path.is_file():continue
  try:
   entries=read_sent_html([path])
  except (OSError,ValueError):continue
  for entry in entries:
   entry['unconfirmed_edition_path']=str(path)
   out.append(entry)
 return out
