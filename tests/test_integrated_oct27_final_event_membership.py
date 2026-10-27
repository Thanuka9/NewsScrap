"""Regression for 27 Oct actual builder failure, preserving source-level lineage."""
import sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import build_financial_brief_v2 as brief
import brief_semantic_guard as guard

A='https://www.dailymirror.lk/business/CBSL-reviews-lenders-early-settlement-fees-and-parate-auctions-after-complaints-pile-up/215-353382'
B='https://www.dailymirror.lk/business/Over-half-of-NBFI-household-borrowers-in-high-risk-bands-as-Stage-2-loans-nearly-double/215-353383'

def make(url,title,category='SOVEREIGN_DEBT_RATES',publisher='Daily Mirror'):
    return {'source_url':url,'source_name':publisher,'headline':title,
            'publication_date':'2026-10-26','section':'MONETARY_MARKETS_EXTERNAL',
            'category':category,'reason':'OLD_INCORRECT_MARKET_MATCH',
            'importance':'MEDIUM','event_key':'',
            'body':'The Central Bank of Sri Lanka reported a materially important financial-sector development. '*9,
            'summary':'The Central Bank of Sri Lanka reported a materially important financial-sector development. '*8}

class MemberRegression(unittest.TestCase):
    def test_two_oct26_articles_preserved_and_correctly_classified(self):
        a=make(A,'CBSL reviews lenders’ early settlement fees and parate auctions after complaints pile up')
        b=make(B,'Over half of NBFI household borrowers in high-risk bands as Stage 2 loans nearly double')
        retained,audit=guard.before_group([a,b],[a,b]);events=guard.after_group(brief.group_events(retained),audit)
        urls={s['source_url'] for e in events for s in e['sources']}
        self.assertEqual(urls,{A,B});self.assertEqual(len(events),2)
        self.assertEqual(guard.semantic_checks(audit,events),[])
        by_url={r['source_url']:r for r in audit}
        self.assertEqual(by_url[A]['section'],'KEY_BANKING_SUPERVISORY')
        self.assertEqual(by_url[B]['section'],'OTHER_FINANCIAL_SECTOR')

    def test_same_publisher_fuzzy_match_never_loses_one_story(self):
        a=make(A,'CBSL reviews lenders early settlement fees after complaints')
        b=make(B,'CBSL reviews lenders early settlement fees amid new complaints')
        self.assertFalse(brief.same_event(a,b))
        events=guard.after_group(brief.group_events([a,b]),[a,b]);self.assertEqual(len(events),2)
        self.assertEqual(guard.semantic_checks([a,b],events),[])

    def test_cross_publisher_factual_same_event_preserves_both_links(self):
        a=make(A,'CBSL reviews lenders early settlement fee practices')
        b=make('https://island.lk/cbsl-reviews-lender-fees','CBSL reviews lenders early settlement fee practices',publisher='The Island')
        a['event_key']='EDITORIAL_LENDER_REVIEW';b['event_key']='EDITORIAL_LENDER_REVIEW'
        events=guard.after_group(brief.group_events([a,b]),[a,b])
        self.assertEqual(len(events),1)
        self.assertEqual({s['source_url'] for s in events[0]['sources']},{a['source_url'],b['source_url']})
        self.assertEqual(guard.semantic_checks([a,b],events),[])

    def test_same_publisher_distinct_urls_even_with_same_event_key(self):
        a=make(A,'CB reviews fees first examination');b=make(B,'CB reviews fees second examination')
        a['event_key']='OVERBROAD_KEY';b['event_key']='OVERBROAD_KEY'
        self.assertFalse(brief.same_event(a,b))
        # A later release QA may flag a duplicate key if the key is too broad;
        # neither distinct source can disappear from the inventory.
        e=brief.group_events([a,b]);self.assertEqual(len(e),2)
        self.assertEqual({s['source_url'] for event in e for s in event['sources']},{A,B})

    def test_broad_shared_event_key_is_split_safely_before_grouping(self):
        a=make(A,'CBSL reviews lenders early settlement fees after complaints')
        b=make(B,'CBSL reviews lenders early settlement fees amid new complaints')
        a['event_key']=b['event_key']='OLD_BROAD_SUPERVISORY_KEY'
        retained,audit=brief._news_separate_unrelated_members([a,b],[a,b])
        self.assertNotEqual(a['event_key'],b['event_key'])
        events=guard.after_group(brief.group_events(retained),audit)
        self.assertEqual(guard.semantic_checks(audit,events),[])
        self.assertEqual(len(events),2)

    def test_original_failure_repro_and_absent_url_check(self):
        a=make(A,'CBSL reviews lenders early settlement fees after complaints')
        b=make(B,'CBSL reviews lenders early settlement fees amid new complaints')
        # The old group matcher used to merge these and keep only the first
        # Daily Mirror URL. New policy must not merge them.
        self.assertNotEqual(a['source_url'],b['source_url'])
        self.assertEqual(len(brief.group_events([a,b])),2)

if __name__=='__main__':unittest.main()

class FinalResumeWiringRegression(unittest.TestCase):
    def test_resume_does_not_rerun_ml_or_collection(self):
        source=(ROOT/'scripts'/'resume_final_edition.py').read_text(encoding='utf-8')
        self.assertIn("'build_financial_brief_v2.py'",source)
        self.assertIn("'audit_news_flow.py'",source)
        self.assertIn("'run_integrated_release.py'",source)
        self.assertNotIn('run_intelligence_stage1.py',source)
        self.assertNotIn('run_ml_corpus_analysis.py',source)
        self.assertNotIn('run_collection.py',source)

    def test_resume_fail_closed_gate(self):
        source=(ROOT/'scripts'/'resume_final_edition.py').read_text(encoding='utf-8')
        self.assertIn("'QA_PASS_NOT_DELIVERED'",source)
        self.assertIn("assert_edition_not_sent(REPORTS,day)",source)
        self.assertIn('finally:',source)
