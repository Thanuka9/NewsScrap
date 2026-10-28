import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import json
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from bank_intel.publication.identity import event_signature,same_event
from bank_intel.publication.reconcile import load_unconfirmed_prior_candidates,was_sent
from bank_intel.publication.decisions import decide
from bank_intel.publication.workflow import prepare
import csv

class HistoricalRegression(unittest.TestCase):
 def test_fitch_same_event_across_publishers(self):
  old={'headline':'Fitch Ratings recalibrates national rating scale','publication_date':'2026-10-22'}
  new={'publication_date':'2026-10-22','headline':"Fitch recalibrates Sri Lanka’s national ratings following sovereign upgrade"}
  self.assertTrue(same_event(old,new))

 def test_weekend_article_recounting_old_cse_close_needs_review(self):
  article={'source_name':'The Morning','publication_date':'2026-10-25','headline':'ASPI slips as three-day slide deepens',
           'body':'The Colombo stock market fell. The ASPI slipped as a three-day slide deepened and ended down on Wednesday (21), dropping 56.48 points to 19,927.30. ' * 15}
  self.assertEqual(decide(article)['reason'],'OLDER_MARKET_SESSION_RECAP_VERIFY_SENT_HISTORY')

 def test_unconfirmed_prior_html_never_becomes_confirmed_sent(self):
  with TemporaryDirectory() as d:
   folder=Path(d)/'2026-10-21'/'window_1d'; folder.mkdir(parents=True)
   (folder/'Daily Financial Sector News Brief 2026-10-21.html').write_text(
    '<article class="context-card"><h3>People’s Bank and Lanka Securities launch integrated banking and share trading platform</h3>'
    '<div class="story-topline">2026-10-21</div>'
    '<a href="https://www.themorning.lk/articles/old"></a></article>',encoding='utf-8')
   unconfirmed=load_unconfirmed_prior_candidates(Path(d),'2026-10-26')
   self.assertEqual(len(unconfirmed),1)
   self.assertTrue(was_sent({'headline':'People’s Bank-Lanka Securities pioneer integrated banking, share trading platform',
       'source_url':'https://www.dailymirror.lk/business/different', 'publication_date':'2026-10-23',
       'body':"People’s Bank has partnered with Lanka Securities to provide share trading in its app."},unconfirmed))
   (folder/'PUBLISHED_URLS.json').write_text('not examined')
   self.assertEqual(load_unconfirmed_prior_candidates(Path(d),'2026-10-26'),[])


 def test_unconfirmed_21_oct_same_event_forces_review_not_sent(self):
  with TemporaryDirectory() as d:
   root=Path(d);folder=root/'2026-10-21'/'window_1d';folder.mkdir(parents=True)
   (folder/'Daily Financial Sector News Brief 2026-10-21.html').write_text(
    '<article class="context-card"><h3>People’s Bank and Lanka Securities launch integrated banking and share trading platform</h3>'
    '<div class="story-topline">2026-10-21</div><a href="https://www.themorning.lk/articles/old"></a></article>',encoding='utf-8')
   row={'publication_date':'2026-10-23','source_name':'Daily Mirror',
     'headline':'People’s Bank-Lanka Securities pioneer integrated banking, share trading platform',
     'source_url':'https://www.dailymirror.lk/business/new',
     'body':"People’s Bank has partnered with Lanka Securities to launch a share trading app for customers. " * 6,
     'summary':'','section':'BANKING_SECTOR_DEVELOPMENTS'}
   audit=root/'audit.json';audit.write_text(json.dumps({'audit':[row]}),encoding='utf-8')
   inv=root/'inventory.csv'
   with inv.open('w',encoding='utf-8',newline='') as f:
    w=csv.DictWriter(f,fieldnames=['source_url']);w.writeheader();w.writerow({'source_url':row['source_url']})
   work,events,counts,earliest=prepare(date_iso='2026-10-26',audit=audit,inventory=inv,
      sent_paths=[],sent_report_root=root)
   self.assertEqual(counts['review'],1)
   self.assertEqual(counts['sent'],0)
   self.assertEqual(len(events),0)
   self.assertEqual(work[0]['assessment']['reason'],'POSSIBLE_REPEAT_FROM_UNCONFIRMED_PRIOR_EDITION')

 def test_confirmed_fitch_22_oct_suppresses_cross_publisher_reprint(self):
  with TemporaryDirectory() as d:
   root=Path(d);folder=root/'2026-10-22'/'window_1d';folder.mkdir(parents=True)
   old='https://www.themorning.lk/articles/HdLMdTjo1bdhrplewRyQ'
   lock={'report_date':'2026-10-22','source_urls':[old],
      'events':[{'headline':'Fitch Ratings recalibrates national rating scale',
           'publication_date':'2026-10-22','source_urls':[old]}]}
   (folder/'PUBLISHED_URLS.json').write_text(json.dumps(lock),encoding='utf-8')
   row={'publication_date':'2026-10-25','source_name':'The Island',
     'headline':'Fitch recalibrates Sri Lanka’s national ratings following sovereign upgrade',
     'source_url':'https://island.lk/fitch-recalibrates-sri-lankas-national-ratings-following-sovereign-upgrade',
     'body':'Fitch Ratings has announced a recalibration of Sri Lanka national rating scale following a sovereign upgrade. '*7,
     'summary':'','section':'MONETARY_MARKETS_EXTERNAL'}
   audit=root/'audit.json';audit.write_text(json.dumps({'audit':[row]}),encoding='utf-8')
   inv=root/'inventory.csv'
   with inv.open('w',encoding='utf-8',newline='') as f:
    w=csv.DictWriter(f,fieldnames=['source_url']);w.writeheader();w.writerow({'source_url':row['source_url']})
   work,events,counts,earliest=prepare(date_iso='2026-10-26',audit=audit,inventory=inv,
      sent_paths=[],sent_report_root=root)
   self.assertEqual(work[0]['assessment']['decision'],'ALREADY_SENT')
   self.assertEqual(counts['sent'],1)
   self.assertEqual(len(events),0)

if __name__=='__main__':unittest.main()
