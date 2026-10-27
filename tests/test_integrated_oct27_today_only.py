import csv
import json
import tempfile
import unittest
from pathlib import Path
from datetime import date
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from bank_intel.publication.backlog import earliest_unpublished_day,merge_held_edition_evidence
from bank_intel.publication.backlog_audit import audit_unpublished_carryover

class TodayOnly(unittest.TestCase):
 def test_window_never_extends_held_tuesday(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);p=r/'2026-10-26'/'window_1d';p.mkdir(parents=True)
   (p/'integrated_release_result.json').write_text(json.dumps({'status':'HOLD_FOR_REVIEW'}))
   self.assertEqual(earliest_unpublished_day(r,date(2026,10,27)).isoformat(),'2026-10-26')
   self.assertEqual(earliest_unpublished_day(r,date(2026,10,26)).isoformat(),'2026-10-23')

 def test_held_evidence_only_merges_yesterday_not_friday(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);old=r/'2026-10-26'/'window_1d';old.mkdir(parents=True)
   cur=r/'2026-10-27'/'window_1d';cur.mkdir(parents=True)
   urls=['https://www.ft.lk/news23','https://www.ft.lk/news26','https://www.ft.lk/news27']
   def payload(path,rows):
    (path/'stakeholder_brief_v2_audit.json').write_text(json.dumps({'audit':rows}))
    with (path/f'Daily Financial Sector News Brief {path.parent.name} Articles.csv').open('w',newline='',encoding='utf-8-sig') as f:
     w=csv.DictWriter(f,fieldnames=['source_url','publication_date','headline','source_name']);w.writeheader();w.writerows(rows)
   rows=[{'source_url':u,'publication_date':dt,'headline':f'Story {i}','source_name':'Daily FT'} for i,(u,dt) in enumerate(zip(urls,['2026-10-23','2026-10-26','2026-10-27']))]
   payload(old,rows[:2]); payload(cur,rows[2:])
   (old/'integrated_release_result.json').write_text(json.dumps({'status':'HOLD_FOR_REVIEW'}))
   a,i,info=merge_held_edition_evidence(target=date(2026,10,27),report_root=r,current_audit=cur/'stakeholder_brief_v2_audit.json',current_inventory=cur/'Daily Financial Sector News Brief 2026-10-27 Articles.csv',destination=cur/'integrated_edition'/'source_union')
   self.assertEqual(info['earliest_inclusive'],'2026-10-26')
   self.assertEqual({x['publication_date'] for x in json.loads(a.read_text())['audit']},{'2026-10-26','2026-10-27'})
   self.assertEqual(info['full_window_count'],2)

if __name__=='__main__':unittest.main()
