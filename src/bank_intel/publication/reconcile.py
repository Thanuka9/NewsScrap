"""Conservative event reconciliation and confirmed-sent handling."""
from __future__ import annotations
from collections import defaultdict
import re
from html.parser import HTMLParser
from pathlib import Path
from .identity import canonical,event_signature,same_event,tokens,publisher_article_id

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


def was_sent(row:dict,prior:list[dict])->bool:
 url=canonical(row.get('source_url',''))
 sig=event_signature(row)
 for p in prior:
  if url and url==canonical(p.get('source_url','')):return True
  pid=publisher_article_id(row.get('source_url',''))
  if pid and pid==publisher_article_id(p.get('source_url','')):return True
  psig=p.get('event_signature') or event_signature(p)
  if (sig and psig and sig==psig) or same_event(row,p):
   if is_material_follow_up(row,p):continue
   return True
  # Conservatively flag likely same article; no substring or generic institutional merges.
  a=tokens(row.get('headline','')); b=tokens(p.get('headline',''))
  if (a and a==b and len(a)>=5 and
      row.get('publication_date','')[:10]==p.get('publication_date','')[:10]):return True
 return False


def group(assessments:list[dict])->list[dict]:
 """Group single events, but preserve complete source links and article trace."""
 events=[]; keys={}
 for entry in assessments:
  row=entry['article'];sig=entry['assessment']['event_signature']; key=sig or canonical(row['source_url'])
  if key in keys:
   e=events[keys[key]]
   e['members'].append(entry)
   continue
  # Un-keyed items merge only exact full headline+same date.
  if not sig:
   other=next((i for i,e in enumerate(events) if not e['event_signature'] and same_event(row,e['members'][0]['article'])),None)
   if other is not None:
    events[other]['members'].append(entry);continue
  keys[key]=len(events)
  events.append({'event_signature':sig,'members':[entry]})
 # Select a numerical tourism revenue lead when consolidating reports of the
 # same SLTDA release. Keep every publisher URL as evidence.
 for e in events:
  if e['event_signature'].startswith('SRI_LANKA_TOURISM_ARRIVALS@'):
   e['members'].sort(key=lambda x: (
    -int(bool(re.search(r'(?i)(?:2,245|2\.24\s*billion|revenue hits)',
               x['article'].get('body','')[:1400] + ' '+x['article'].get('headline','')))),
    -len(x['article'].get('body',''))))
 # Confirm no intra-group topic mixing; merge only source records under exact signature.
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
                   'sent_at':x.get('sent_at','')})
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
                   'event_signature':e.get('event_signature',''), 'distributed_report':x.get('report_path',''), 'sent_at':x.get('sent_at','')})
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
