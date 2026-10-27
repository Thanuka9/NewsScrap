"""Immutable review, approval and confirmed-send records (no silent promotion)."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
import hashlib,json,shutil

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def approve(candidate_manifest:Path,report:Path,reviewer:str)->Path:
 obj=json.loads(candidate_manifest.read_text(encoding='utf-8'))
 if obj.get('status')!='QA_PASS_NOT_DELIVERED' or obj.get('issues'):
  raise RuntimeError('Cannot approve a candidate with unresolved QA or coverage issues')
 ledger=candidate_manifest.parent/obj['decision_ledger']
 if sha(ledger)!=obj.get('decision_ledger_sha256'):
  raise RuntimeError('Decision ledger differs from reviewed candidate')
 qa=Path(obj.get('release_qa') or candidate_manifest.parent/'news_release_qa.json')
 if not qa.is_file():raise RuntimeError('Independent release QA is required')
 verified=json.loads(qa.read_text(encoding='utf-8'))
 if verified.get('release_status')!='PASS' or verified.get('report_sha256')!=obj.get('sha256') or verified.get('decision_ledger_sha256')!=obj.get('decision_ledger_sha256'):
  raise RuntimeError('Independent release QA does not approve these exact decisions and bytes')
 from .ledger import validate_outputs
 consistency=validate_outputs(json.loads(ledger.read_text(encoding='utf-8')),obj,report.read_text(encoding='utf-8'))
 if consistency:raise RuntimeError('Candidate outputs no longer agree: '+str(consistency))
 if sha(report)!=obj.get('sha256'):
  raise RuntimeError('Candidate HTML differs from QA manifest')
 if not reviewer.strip():raise ValueError('Reviewer is required')
 folder=candidate_manifest.parent
 final=folder/f'Daily Financial Sector News Brief {obj["date"]}.html'
 if final.exists():
  if sha(final)!=sha(report):raise FileExistsError('Existing approved attachment must not be overwritten')
 else:shutil.copyfile(report,final)
 frozen=folder/'APPROVED_EDITION.json'
 payload={'date':obj['date'],'approved_at':datetime.now().astimezone().isoformat(),
  'reviewer':reviewer,'attachment':str(final),'attachment_sha256':sha(final),
  'source_count':obj['source_count'],'event_count':obj['event_count'],
  'events':obj['events'],'decision_ledger_sha256':obj['decision_ledger_sha256'],'state':'APPROVED_NOT_SENT'}
 if frozen.exists():
  past=json.loads(frozen.read_text(encoding='utf-8'))
  if past.get('attachment_sha256')!=payload['attachment_sha256']:raise FileExistsError('Approved edition already exists with different SHA')
  return frozen
 frozen.write_text(json.dumps(payload,indent=2,ensure_ascii=False),encoding='utf-8')
 return frozen

def actual_send_time(value:str)->str:
 """Validated, timezone-aware actual distribution time (never in the future)."""
 try:sent=datetime.fromisoformat(str(value).strip().replace('Z','+00:00'))
 except ValueError:raise ValueError('sent_at must be ISO 8601, e.g. 2026-10-28T08:45:00+05:30')
 if sent.tzinfo is None:raise ValueError('sent_at must include a timezone offset')
 if sent>datetime.now().astimezone():raise ValueError('sent_at cannot be in the future')
 return sent.isoformat()

def record_send_time(sent_record:Path,sent_at:str,operator:str)->Path:
 """Fill the missing send time of an operator-imported edition exactly once.

 Late-news qualification compares publication times with the previous send;
 an imported edition without it forces every yesterday story into review.
 """
 if not operator.strip():raise ValueError('Operator is required')
 value=actual_send_time(sent_at)
 obj=json.loads(sent_record.read_text(encoding='utf-8'))
 if obj.get('state')!='DELIVERED_CONFIRMED_BY_OPERATOR':raise RuntimeError('Not a confirmed sent edition')
 if datetime.fromisoformat(value).astimezone().date().isoformat()<obj['date']:
  raise ValueError('sent_at precedes the edition date')
 if obj.get('sent_at'):
  if obj['sent_at']!=value:raise FileExistsError('Send time already recorded: '+obj['sent_at'])
  return sent_record
 update={'sent_at':value,'sent_time_status':'OPERATOR_RECORDED','sent_time_recorded_by':operator,
  'sent_time_recorded_at':datetime.now().astimezone().isoformat()}
 register=sent_record.parent/'sent_events.json'
 for path in (sent_record,register):
  if not path.exists():continue
  current=json.loads(path.read_text(encoding='utf-8'))
  if current.get('attachment_sha256')!=obj.get('attachment_sha256'):raise RuntimeError('Sent event register differs from confirmed edition')
  current.update(update)
  path.write_text(json.dumps(current,indent=2,ensure_ascii=False),encoding='utf-8')
 return sent_record

def record_sent(approval:Path,recipient_group:str,confirm:bool,message_id:str='',sent_at:str='')->Path:
 if not confirm:raise PermissionError('Explicit actual-send confirmation required')
 obj=json.loads(approval.read_text(encoding='utf-8'))
 if obj.get('state')!='APPROVED_NOT_SENT':raise RuntimeError('Not an approved attachment')
 artifact=Path(obj['attachment'])
 if not artifact.is_file() or sha(artifact)!=obj['attachment_sha256']:
  raise RuntimeError('Attachment does not match approved SHA256')
 if not recipient_group.strip(): raise ValueError('Specify recipient group')
 dst=approval.parent/'SENT_EDITION.json'
 payload={'date':obj['date'],'attachment':str(artifact),'attachment_sha256':obj['attachment_sha256'],
  'decision_ledger_sha256':obj.get('decision_ledger_sha256',''),
  'sent_at':actual_send_time(sent_at) if sent_at else datetime.now().astimezone().isoformat(),
  'sent_time_status':'OPERATOR_RECORDED' if sent_at else 'RECORDED_AT_CONFIRMATION','recipient_group':recipient_group,
  'message_id':message_id,'event_count':len(obj['events']),
  'source_urls':sorted({s['source_url'] for e in obj['events'] for s in e['sources']}),
  'events':obj['events'],'state':'DELIVERED_CONFIRMED_BY_OPERATOR'}
 if dst.exists():
  old=json.loads(dst.read_text(encoding='utf-8'))
  if old.get('attachment_sha256')!=payload['attachment_sha256']:
   raise FileExistsError('Cannot overwrite an existing different sent edition')
  payload=old
 else:
  with dst.open('x',encoding='utf-8') as stream:json.dump(payload,stream,indent=2,ensure_ascii=False)
 register=approval.parent/'sent_events.json'
 if register.exists():
  if json.loads(register.read_text(encoding='utf-8'))!=payload:raise RuntimeError('Sent event register differs from confirmed edition')
 else:
  with register.open('x',encoding='utf-8') as stream:json.dump(payload,stream,indent=2,ensure_ascii=False)
 return dst
