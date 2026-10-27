"""Regression tests for the October-integrated production path (offline)."""
from __future__ import annotations
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from bank_intel.publication.identity import approved,first_window_day,event_signature
from bank_intel.publication.decisions import decide
from bank_intel.publication.model_policy import reconcile
from bank_intel.publication.reconcile import load_confirmed_history,was_sent,group
from bank_intel.publication.ml_trace import read_model_traces
from bank_intel.publication.workflow import build


class IntegratedNewsRegression(unittest.TestCase):
 def test_monday_includes_friday_and_weekend(self):
  from datetime import date
  self.assertEqual(first_window_day(date(2026,10,26)).isoformat(),'2026-10-23')
  self.assertEqual(first_window_day(date(2026,10,27)).isoformat(),'2026-10-26')
 def test_unapproved_domain(self):
  self.assertFalse(approved('Daily FT','https://economynext.com/sri-lanka-central-bank-seeks-panel-for-troubled-bank-resolutions-287745/'))
  self.assertTrue(approved('Daily FT','https://www.ft.lk/front-page/whatever/44-999999'))
 def test_attributed_fraud_uses_body_not_keywords_alone(self):
  row={'headline':'Pursues fraud recoveries; amount and timetable undisclosed',
       'body':'NDB is pursuing recovery and legal action over its Rs. 13.64 billion fraud loss. The bank gave shareholders no amount recovered or completion timetable. NDB said control and governance actions are under way.',
       'publication_date':'2026-10-23','source_url':'https://www.dailymirror.lk/business/test'}
  assessed=decide(row)
  self.assertEqual(assessed['section'],'KEY_BANKING_SUPERVISORY')
 def test_rating_development_not_lost(self):
  assessed=decide({'headline':'Fitch recalibrates Sri Lanka national ratings following sovereign upgrade',
   'body':('Fitch Ratings is recalibrating national ratings after upgrading the sovereign. ')*8,'source_url':'https://island.lk/test'})
  self.assertEqual(assessed['section'],'MONETARY_MARKETS_EXTERNAL')
 def test_model_keep_suppression_is_review_not_silent_drop(self):
  ass={'decision':'EXCLUDE','reason':'OUT_OF_SCOPE','section':'','evidence_issues':[]}
  out=reconcile(ass,{'raw_stage_a':'KEEP','operational_keep':False})
  self.assertEqual(out['decision'],'REVIEW')
  self.assertEqual(out['ml_action'],'POST_MODEL_SUPPRESSION')
 def test_stage_b_watch_is_not_auto_exclusion(self):
  ass={'decision':'INCLUDE','reason':'DIRECT_BANK_HEADLINE'}
  out=reconcile(ass,{'raw_stage_a':'KEEP','stage_b_label':'WATCH','operational_keep':True})
  self.assertEqual(out['decision'],'INCLUDE')
 def test_extraction_false_match(self):
  row={'headline':'Namal R and Janaki Siriwardena further remanded',
       'body':'Reports of dangerous lead-acid battery recycling operations emerged, with major concerns over the environment, worker safety and hazardous metals in the atmosphere.'}
  from bank_intel.publication.evidence import evidence_issues
  self.assertIn('HEADLINE_BODY_TOPIC_MISMATCH',evidence_issues(row))
 def test_sent_history_reads_actual_published_url_event_schema(self):
  with tempfile.TemporaryDirectory() as t:
   base=Path(t)/'2026-10-22'/'window_1d';base.mkdir(parents=True)
   u='https://www.ft.lk/front-page/story/44-123456'
   lock={'report_date':'2026-10-22','source_urls':[u],
         'events':[{'headline':'ADB approves US$ 100mn for social protection','publication_date':'2026-10-22','source_urls':[u]}]}
   (base/'PUBLISHED_URLS.json').write_text(json.dumps(lock),encoding='utf-8')
   sent,issues=load_confirmed_history(Path(t),'2026-10-26')
   self.assertFalse(issues)
   self.assertTrue(was_sent({'source_url':u,'headline':'Another headline','publication_date':'2026-10-23'},sent))
   row={'source_url':'https://www.dailymirror.lk/business/other','headline':'ADB approves US$ 100mn to support social protection reforms',
        'body':'The Asian Development Bank approved $100 million of funds for social protection and elderly care','publication_date':'2026-10-23'}
   self.assertTrue(was_sent(row,sent))
 def test_invalid_sent_history_blocks_release(self):
  with tempfile.TemporaryDirectory() as t:
   base=Path(t)/'2026-10-22'/'window_1d';base.mkdir(parents=True)
   (base/'PUBLISHED_URLS.json').write_text('{}')
   prior,issues=load_confirmed_history(Path(t),'2026-10-26')
   self.assertTrue(issues)
 def test_real_finetuned_manifest_record_mapping(self):
  with tempfile.TemporaryDirectory() as t:
   base=Path(t);date='2026-10-26'
   ml=base/'data'/'processed'/'ml'/date;ml.mkdir(parents=True)
   rec=ml/'analysis.json';url='https://www.ft.lk/front-page/a/44-33'
   rec.write_text(json.dumps({'source_url':url,'ml':{'supervisory_relevance':{
       'stage_a':{'predicted_label':'KEEP','confidence':0.91},
       'operational_keep':False,'stage_b':{'predicted_label':'WATCH'},'stage_b_executed':True}}}))
   (ml/'ml_manifest.json').write_text(json.dumps({'status':'SUCCESS','articles':[{'analysis_file':str(rec)}]}))
   got,issues=read_model_traces(base,[date])
   self.assertEqual(len(got),1)
   self.assertEqual(next(iter(got.values()))['raw_stage_a'],'KEEP')
   self.assertFalse(issues)
 def test_cse_not_merged_when_daily_close_differs(self):
  a={'headline':'CSE ends down','publication_date':'2026-10-23',
     'body':'The ASPI closed at 19,717.92 after a decline in the last trading session.',
     'source_url':'https://www.ft.lk/a'}
  b={'headline':'Week closes in red','publication_date':'2026-10-24',
     'body':'The ASPI closed at 19,684.91 and turnover reached Rs. 825 million.',
     'source_url':'https://www.dailymirror.lk/b'}
  self.assertNotEqual(event_signature(a),event_signature(b))
 def test_report_runner_invokes_integrated_source(self):
  source=(ROOT/'scripts'/'run_pipeline.py').read_text(encoding='utf-8')
  self.assertIn('INTEGRATED_RELEASE_SCRIPT',source)
  self.assertIn('INTEGRATED EDITION AND INDEPENDENT RELEASE GATE',source)
  self.assertIn('REVIEW_REQUIRED',source)
 def test_synthetic_edition_holds_without_coverage(self):
  with tempfile.TemporaryDirectory() as t:
   folder=Path(t)
   raw=[{'source_name':'Daily Mirror','source_url':'https://www.dailymirror.lk/business/test',
     'publication_date':'2026-10-23','headline':'NDB records quarterly profit','section':'KEY_BANKING_SUPERVISORY',
     'body':'NDB recorded quarterly profit and earnings growth for the September quarter. '+('Bank earnings changed and provisions changed. '*5)}]
   au=folder/'audit.json';au.write_text(json.dumps({'audit':raw}))
   inv=folder/'inventory.csv'
   with inv.open('w',newline='',encoding='utf-8-sig') as f:
    w=csv.DictWriter(f,fieldnames=['source_url','publication_date','headline','source_name']);w.writeheader();w.writerows([{k:x[k] for k in w.fieldnames} for x in raw])
   result,path=build(date_iso='2026-10-26',audit=au,inventory=inv,output=folder/'out',sent=[],
      css_file=ROOT/'src'/'bank_intel'/'publication'/'news_style.css')
   self.assertEqual(result['status'],'HOLD_FOR_REVIEW')
   self.assertTrue(path.exists())
   self.assertTrue(any(x['kind']=='COVERAGE_NOT_AUDITED' for x in result['issues']))

if __name__=='__main__':unittest.main()
