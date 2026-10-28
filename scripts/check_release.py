"""Read-only shared scheduler/release validation. Never approves or sends."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from bank_intel.publication.contract import validate_release

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date',required=True)
    parser.add_argument('--project-root',type=Path,default=ROOT)
    args=parser.parse_args()
    try:
        result=validate_release(args.project_root,args.date)
        print(json.dumps(result,ensure_ascii=True))
        raise SystemExit(2 if result['status']=='REVIEW_REQUIRED' else 0)
    except (OSError,ValueError,KeyError,RuntimeError) as exc:
        print(json.dumps({'status':'FAILED','error':str(exc)}))
        raise SystemExit(1)
