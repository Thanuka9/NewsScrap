"""Diagnostics for already fine-tuned two-stage production output; no fabricated model results."""
from __future__ import annotations
import csv,json
from pathlib import Path
from .identity import canonical

def evaluate_manifest(manifest_path:Path,project_root:Path,truth_csv:Path|None=None)->dict:
 m=json.loads(manifest_path.read_text(encoding='utf-8-sig'))
 models=m.get('supervisory_relevance_models') or {}
 truth={}
 if truth_csv:
  with truth_csv.open(encoding='utf-8-sig',newline='') as f:
   for r in csv.DictReader(f):
    label=(r.get('true_label') or r.get('review_label') or '').upper()
    if label in ('KEEP','NOISE','WATCH','RELEVANT'):truth[canonical(r.get('source_url',''))]=label
 details=[];missing=0
 for rec in m.get('articles') or []:
  raw=str(rec.get('analysis_file') or '')
  if not raw: missing+=1; continue
  path=Path(raw)
  if not path.is_absolute():path=project_root/path
  if not path.is_file():missing+=1;continue
  a=json.loads(path.read_text(encoding='utf-8-sig'))
  ml=(a.get('ml') or {}).get('supervisory_relevance') or {}
  source=canonical(rec.get('source_url') or (a.get('article') or {}).get('source_url') or a.get('source_url') or '')
  stagea=ml.get('stage_a') or {};stageb=ml.get('stage_b') or {}
  details.append({'url':source,'stage_a':stagea.get('predicted_label'),
   'stage_a_confidence':stagea.get('confidence'), 'operational_keep':ml.get('operational_keep'),
   'stage_b_executed':ml.get('stage_b_executed'),'stage_b':stageb.get('predicted_label'),
   'truth':truth.get(source,''), 'rule_changed_decision':bool(
    (stagea.get('predicted_label')=='KEEP') != bool(ml.get('operational_keep'))) if isinstance(ml.get('operational_keep'),bool) else None})
 def table(stage,labels):
  vals=[(r['truth'],r[stage]) for r in details if r['truth'] in labels and r[stage] in labels]
  by={}
  for label in labels:
   tp=sum(a==label and b==label for a,b in vals)
   fp=sum(a!=label and b==label for a,b in vals)
   fn=sum(a==label and b!=label for a,b in vals)
   by[label]={'support':sum(a==label for a,b in vals),'precision':round(tp/(tp+fp),4) if tp+fp else None,'recall':round(tp/(tp+fn),4) if tp+fn else None,'f1':round(2*tp/(2*tp+fp+fn),4) if 2*tp+fp+fn else None}
  return {'evaluated_rows':len(vals),'by_class':by}
 return {'status':'EVALUATED' if truth else 'PREDICTIONS_ONLY_NO_HUMAN_TRUTH',
         'manifest':str(manifest_path),'models':models,'analysis_found':len(details),'missing_analysis':missing,
         'raw_stage_a_distribution':{x:sum(d['stage_a']==x for d in details) for x in ('KEEP','NOISE')},
         'post_rule_keep_count':sum(d['operational_keep'] is True for d in details),
         'post_rule_suppression_count':sum(d['stage_a']=='KEEP' and d['operational_keep'] is False for d in details),
         'stage_b_executed':sum(d['stage_b_executed'] is True for d in details),
         'stage_a_metrics':table('stage_a',('KEEP','NOISE')) if truth else None,
         'stage_b_metrics':table('stage_b',('WATCH','RELEVANT')) if truth else None,
         'articles':details}
