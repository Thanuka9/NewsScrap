"""Read-only evidence audit of Stage-A/B training assets and production trace.

Does not retrain, alter probabilities, promote checkpoints or leak test examples
into a training dataset. Every metric absent from actual files stays unknown.
"""
from __future__ import annotations
import argparse,csv,json
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path

STAGES=('stage_a_noise_vs_keep','stage_b_watch_vs_relevant')
SPLITS=('train.csv','validation.csv','test.csv')

def rows(path):
    with path.open(encoding='utf-8-sig',newline='') as h:return list(csv.DictReader(h))

def run(project_root,out):
    project_root=Path(project_root); datasets=project_root/'data/training/datasets'
    systems=[]; warnings=[]
    if datasets.is_dir():
        for directory in sorted(datasets.iterdir()):
            if not directory.is_dir():continue
            for stage in STAGES:
                stage_root=directory/stage
                if not stage_root.is_dir():continue
                item={'dataset':directory.name,'stage':stage,'splits':{},'status':'INSPECTED'}
                partition={}
                for name in SPLITS:
                    file=stage_root/name
                    if not file.is_file():
                        item['splits'][name]={'exists':False};continue
                    r=rows(file);keys=set(r[0]) if r else set();label_key=next((c for c in ('label','human_label','target','relevance_label') if c in keys),None)
                    group_key=next((c for c in ('story_group_id','group_id','event_id','group_key') if c in keys),None)
                    val=Counter(x.get(label_key,'') for x in r) if label_key else {}
                    item['splits'][name]={'exists':True,'rows':len(r),'label_counts':dict(val),
                                          'label_column':label_key,'group_column':group_key}
                    if group_key:partition[name]={x.get(group_key) for x in r if x.get(group_key)}
                if partition:
                    item['cross_split_group_overlap']={a+'__'+b:len(partition[a]&partition[b])
                         for i,a in enumerate(partition) for b in list(partition)[i+1:]}
                    if any(item['cross_split_group_overlap'].values()):warnings.append('STORY_GROUP_LEAKAGE:'+directory.name+':'+stage)
                systems.append(item)
    if not systems:warnings.append('TRAINING_DATASETS_UNAVAILABLE: cannot diagnose retraining or independently recompute model metrics')
    production=project_root/'data/processed/relevance_two_stage'
    manifests=[]
    if production.is_dir():
        for file in production.rglob('run_manifest.json'):
            if len(manifests)>=80:break
            try:
                j=json.loads(file.read_text(encoding='utf-8-sig'))
                manifests.append({'path':str(file),'task':j.get('task'),
                                  'status':j.get('status'),'selected_checkpoint':j.get('best_checkpoint',j.get('best_epoch'))})
            except (ValueError,OSError):warnings.append('UNREADABLE_TRAINING_MANIFEST:'+str(file))
    if not manifests:warnings.append('MODEL_RUN_MANIFESTS_UNAVAILABLE: cannot confirm active trained checkpoint')
    payload={'schema':'1.0','generated_at_utc':datetime.now(timezone.utc).isoformat(),
       'datasets':systems,'model_run_manifests':manifests,'warnings':warnings,
       'current_trained_model_version':'NOT_VERIFIED_FROM_RUNTIME',
       'recommended_decision':'COMPARE_FROZEN_STAGE_A_RAW_VS_OVERRIDES_FIRST',
       'stage_b_retraining_decision':'DEFER_UNTIL_WATCH_LABELS_AND_CHRONOLOGICAL_HOLDOUT_AUDITED',
       'new_model_requirement':'NONE_PROVEN_BY_AVAILABLE_EVIDENCE',
       'metrics_recomputed':False}
    out=Path(out);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(payload,indent=2,ensure_ascii=False),encoding='utf-8')
    print('[MODEL BASELINE]',len(systems),'datasets,',len(manifests),'run manifests')
    print('[OUTPUT]',out)
    for w in warnings:print(' -',w)
    return payload

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--project-root',type=Path,default=Path.cwd());p.add_argument('--out',type=Path,default=Path('data/processed/news_model_baseline.json'));a=p.parse_args()
    run(a.project_root,a.out)
