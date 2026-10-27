import unittest,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from bank_intel.publication.decisions import decide
from bank_intel.publication.identity import event_signature
from bank_intel.publication.reconcile import group

class NewHistoricalFindings(unittest.TestCase):
 def test_tourism_release_recovered_only_from_supported_evidence(self):
  a={'source_name':'NewsFirst','headline':'Sri Lanka Welcomes Over 1.8M Tourists as Revenue Hits $2.24 Billion Through September','publication_date':'2026-10-25','body':'The SLTDA confirmed 1,813,484 visitors January 1 to October 22; tourism earnings through September were USD 2,245 million. The monetary receipts are relevant to external funding positions and foreign exchange inflows. Figures are attributed to the tourism regulator.'}
  self.assertEqual(decide(a)['reason'],'MATERIAL_TOURISM_EXTERNAL_STATISTICS')
  self.assertEqual(decide(a)['section'],'MONETARY_MARKETS_EXTERNAL')
 def test_tourism_cross_publisher_sources_group_and_keep_revenue(self):
  a={'source_name':'NewsFirst','headline':'Sri Lanka Welcomes Over 1.8M Tourists as Revenue Hits $2.24 Billion Through September','publication_date':'2026-10-25','source_url':'https://english.newsfirst.lk/2026/10/25/a','body':'Tourists in October: 119,805. As of September cumulative tourism revenue reached USD 2,245 million. '+('Travel receipts supported external liquidity. '*5)}
  b={'source_name':'The Morning','headline':'Tourism: Arrivals surpass 1.8 m in 2026','publication_date':'2026-10-26','source_url':'https://www.themorning.lk/articles/b','body':'Tourists arrivals through October 22 are 1,813,484. '+('Visitors arrived in Sri Lanka. '*8)}
  self.assertEqual(event_signature(a),event_signature(b))
  grouped=group([{'article':b,'assessment':decide(b)},{'article':a,'assessment':decide(a)}])
  self.assertEqual(len(grouped),1)
  self.assertEqual(grouped[0]['members'][0]['article']['source_name'],'NewsFirst')
 def test_generic_government_php_mandate_requires_review_not_invisible_exclusion(self):
  a={'source_name':'The Morning','headline':'PHP security: Govt institutions directed to fix critical vulnerabilities','publication_date':'2026-10-26','body':'The Ministry of Digital Economy directed government institutions to address vulnerabilities.'}
  self.assertEqual(decide(a)['decision'],'REVIEW')
  self.assertEqual(decide(a)['category'],'CROSS_SECTOR_CYBER')
 def test_lolc_legal_order_and_assetline_finance_are_reviewable(self):
  a={'headline':'False statements: Court orders removal of statements targeting LOLC','body':'Court directed removal of online statements.','publication_date':'2026-10-26'}
  b={'headline':'Assetline Finance deepens footprint with 64th Branch in Wattala','body':'Assetline Finance opened 64th branch.','publication_date':'2026-10-26'}
  self.assertEqual(decide(a)['section'],'OTHER_FINANCIAL_SECTOR')
  self.assertEqual(decide(b)['reason'],'REGULATED_FINANCE_COMPANY_EXPANSION_REVIEW')

if __name__=='__main__':unittest.main()

class SentFollowUpRegression(unittest.TestCase):
 def test_final_debenture_subscription_not_lost_as_identical_old_event(self):
  from bank_intel.publication.reconcile import was_sent
  old={'headline':'Rs. 10 b Sampath Bank debenture issue oversubscribed', 'publication_date':'2026-10-21', 'source_url':'https://www.ft.lk/old','summary':'The initial issue had drawn over Rs.10 billion and closed.'}
  new={'headline':'Sampath Bank’s Rs. 10 b debenture issue oversubscribed by 22.7%', 'publication_date':'2026-10-26', 'source_url':'https://www.ft.lk/new', 'body':'Applications totalled Rs.12.27 billion for 122.66 million debentures, up 22.7%. '+('Regulatory Tier 2 subscription terms were stated. '*7)}
  self.assertTrue(was_sent(new,[old]))
  old['distributed_report']='sent-oct21.html'
  new['material_update']={'reviewer':'test editor','reason':'Verified final subscription figures','prior_report':'sent-oct21.html','new_fact':'12.27 billion'}
  self.assertFalse(was_sent(new,[old]))
  old2={**old,'summary':'The bank had already disclosed Rs.12.27 billion applications and 22.7% oversubscription.'}
  self.assertTrue(was_sent(new,[old2]))
  self.assertTrue(was_sent({**new,'source_url':old['source_url']},[old]))

class SignedEditorialClassification(unittest.TestCase):
 def test_recovered_financial_company_keeps_signed_other_fin_section(self):
  import tempfile,csv,json
  from bank_intel.publication.workflow import prepare
  with tempfile.TemporaryDirectory() as d:
   rt=Path(d);url='https://www.ft.lk/business/finance-company-court-order'
   article={'publication_date':'2026-10-26','source_name':'Daily FT','source_url':url,'headline':'Court orders removal of statements targeting LOLC','body':'A conditional court order concerning LOLC Holdings PLC was issued in relation to online statements. '*8,'section':'EXCLUDE'}
   (rt/'audit.json').write_text(json.dumps({'audit':[article]}))
   with (rt/'inventory.csv').open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=['source_url']);w.writeheader();w.writerow({'source_url':url})
   with (rt/'review.csv').open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=['source_url','review_decision','reviewer','review_notes','editorial_section']);w.writeheader();w.writerow({'source_url':url,'review_decision':'INCLUDE','reviewer':'editor-reviewed','review_notes':'Confirmed direct effect on financial group','editorial_section':'OTHER_FINANCIAL_SECTOR'})
   items,events,counts,_=prepare(date_iso='2026-10-26',audit=rt/'audit.json',inventory=rt/'inventory.csv',sent_paths=[],review_decisions=rt/'review.csv')
   self.assertEqual(counts['included'],1)
   self.assertEqual(items[0]['assessment']['section'],'OTHER_FINANCIAL_SECTOR')
   self.assertEqual(items[0]['assessment']['category'],'FINANCIAL_GROUP_LEGAL_GOVERNANCE')
