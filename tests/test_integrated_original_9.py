"""Known production regressions: URL policy, date, factual merge, failed extraction, prior sent, model audit."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from bank_intel.publication.identity import approved,first_window_day,event_signature,same_event
from bank_intel.publication.decisions import decide
from bank_intel.publication.evidence import evidence_issues
from bank_intel.publication.reconcile import group,was_sent
from datetime import date

class TestNews(unittest.TestCase):
 def r(self,headline,body='',date='2026-10-26',source='Daily FT',url='https://www.ft.lk/a'):
  return {'headline':headline,'body':body,'publication_date':date,'source_name':source,'source_url':url,'section':'EXCLUDE'}
 def test_monday_window(self):
  self.assertEqual(first_window_day(date(2026,10,26)),date(2026,10,23))
  self.assertEqual(first_window_day(date(2026,10,27)),date(2026,10,26))
 def test_outside_source_disallowed(self):
  self.assertFalse(approved('Daily FT','https://economynext.com/sri-lanka-central-bank-seeks-panel'))
  self.assertFalse(approved('Daily Mirror','https://evil-dailymirror.lk/a'))
  self.assertTrue(approved('Daily FT','https://www.ft.lk/financial-services/a'))
 def test_adb_same_event(self):
  a=self.r('ADB approves $100m for social protection','Asian Development Bank has approved 100 million to support social protection')
  b=self.r('Developmental aid ADB approves $100 million','ADB 100 million in financing social protection')
  self.assertTrue(same_event(a,{**b,'source_url':'https://www.ft.lk/b'}))
 def test_cse_consolidate_only_same_close(self):
  a=self.r('CSE ends down', 'The ASPI ended at 19,717.92 on 22 October')
  b=self.r('Indices close in red','ASPI 19,717.92 and other indices')
  c=self.r('CSE falls 2.48% during week','ASPI at 19,684.91')
  self.assertTrue(same_event(a,{**b,'source_url':'https://www.ft.lk/b'}));self.assertFalse(same_event(a,{**c,'source_url':'https://www.ft.lk/c'}))
 def test_imf_oration_only(self):
  a=self.r('CBSL 76th Anniversary Oration by IMF CIO Shirin Hamid', 'The IMF CIO Shirin Hamid delivered the Intelligent Central Bank oration')
  b=self.r('Trust a National Asset IMF Chief Information Officer','Shirin Hamid gave the oration at CBSL')
  c=self.r('IPS report outlines reforms','IMF programme context and Sri Lanka growth')
  self.assertTrue(same_event(a,{**b,'source_url':'https://www.ft.lk/b'}));self.assertFalse(same_event(a,{**c,'source_url':'https://www.ft.lk/c'}))
 def test_mismatch(self):
  a=self.r('Namal R and Janaki Siriwardena further remanded','Lead-acid battery recycling is harming the environment and toxic chemicals')
  self.assertIn('HEADLINE_BODY_TOPIC_MISMATCH',evidence_issues(a))
  self.assertEqual(decide(a)['decision'],'EXCLUDE')
  self.assertIn('HEADLINE_BODY_TOPIC_MISMATCH',decide(a)['evidence_issues'])
 def test_finance_rescue(self):
  a=self.r('Asia Asset Finance plans Rs 2 billion debenture issue', 'Asia Asset Finance PLC announced plans to raise two billion rupees through a proposed listed debenture offering. The new debt funding is expected to support the financial institution and its expansion of regulated services.')
  self.assertEqual(decide(a)['decision'],'INCLUDE')
 def test_prior_url(self):
  a=self.r('ADB approves loans','Social protection development')
  self.assertTrue(was_sent(a,[dict(a)]))
 def test_prior_semantic(self):
  a=self.r('ADB approves $100m for social protection','ADB approved $100 million for social protection',url='https://www.ft.lk/1')
  b=self.r('Developmental aid: ADB approves $100m','ADB approved 100 million for social protection',url='https://www.ft.lk/2')
  self.assertTrue(was_sent(a,[b]))
if __name__=='__main__':unittest.main()
