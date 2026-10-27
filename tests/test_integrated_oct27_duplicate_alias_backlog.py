"""27 October live-preview regression: publisher aliases, factual merges, held backlog."""
import csv
from datetime import date
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'scripts'))
from bank_intel.publication.identity import event_signature,publisher_article_id
from bank_intel.publication.reconcile import group
from bank_intel.publication.workflow import prepare
from bank_intel.publication.backlog import merge_held_edition_evidence
from bank_intel.publication.evidence import summary
import build_financial_brief_v2 as legacy

MIRROR_A='https://www.dailymirror.lk/business-news/CBSL-reviews-lenders-early-settlement-fees-and-parate-auctions-after-complaints-pile-up/273-353382'
MIRROR_A_ALIAS='https://www.dailymirror.lk/business/CBSL-reviews-lenders-early-settlement-fees-and-parate-auctions-after-complaints-pile-up/215-353382'
MIRROR_B='https://www.dailymirror.lk/business-news/Over-half-of-NBFI-household-borrowers-in-high-risk-bands-as-Stage-2-loans-nearly-double/273-353383'
MIRROR_B_ALIAS='https://www.dailymirror.lk/business/Over-half-of-NBFI-household-borrowers-in-high-risk-bands-as-Stage-2-loans-nearly-double/215-353383'


def article(url,title,body,source='Daily Mirror',day='2026-10-26'):
    return {'source_url':url,'headline':title,'body':body,'publication_date':day,'source_name':source,
            'section':'KEY_BANKING_SUPERVISORY','category':'REGULATORY_RISK','importance':'HIGH',
            'reason':'KEY_SUPERVISORY','event_key':''}

def fixture(files,rows):
    files.mkdir(parents=True,exist_ok=True)
    audit=files/'stakeholder_brief_v2_audit.json';inventory=files/('Daily Financial Sector News Brief '+files.parent.name+' Articles.csv')
    audit.write_text(json.dumps({'audit':rows}),encoding='utf-8')
    with inventory.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['source_url','headline','body','source_name','publication_date','section','category','reason','importance','event_key']);w.writeheader();w.writerows(rows)
    return audit,inventory

class Oct27CompleteRegression(unittest.TestCase):
    def test_mirror_aliases_same_article_and_different_story(self):
        self.assertEqual(publisher_article_id(MIRROR_A),publisher_article_id(MIRROR_A_ALIAS))
        self.assertEqual(publisher_article_id(MIRROR_B),publisher_article_id(MIRROR_B_ALIAS))
        self.assertNotEqual(publisher_article_id(MIRROR_A),publisher_article_id(MIRROR_B))

    def test_separate_urls_dedup_without_dropping_other_article(self):
        a=article(MIRROR_A,'CBSL reviews lenders early settlement fees and parate auctions after complaints pile up',
                  'The Central Bank of Sri Lanka has reviewed lender early settlement fees and parate auctions after consumer complaints. '+('The reviews concern licensed banks and customer protections. '*9))
        b=article(MIRROR_B,'Over half of NBFI household borrowers in high risk bands as Stage 2 loans nearly double',
                  'The finance company sector recorded 52.4% of household borrowers in high risk bands and rising Stage 2 exposures. '+('The borrowing risk assessment compares finance companies with banks. '*9))
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);out=root/'2026-10-27'/'window_1d';rows=[a,{**a,'source_url':MIRROR_A_ALIAS},b,{**b,'source_url':MIRROR_B_ALIAS}]
            audit,inventory=fixture(out,rows)
            decisions,events,counts,_=prepare(date_iso='2026-10-27',audit=audit,inventory=inventory,sent_paths=[],collection_metadata={r['source_url'].replace('www.',''):{'first_seen_at':'2026-10-27T08:00:00+05:30'} for r in rows})
            self.assertEqual(counts['publisher_aliases'],2)
            self.assertEqual(sum(x['assessment']['decision']=='INCLUDE' for x in decisions),2)
            self.assertEqual(len(events),2)
            self.assertEqual({x['event_signature'] for x in events},{'CBSL_EARLY_SETTLEMENT_PARATE_THEMATIC@2026-10','NBFI_HOUSEHOLD_STAGE2_RISK@2026-10'})

    def test_factual_duplicate_cross_publisher_merges(self):
        pairs=[
          (('BOC opens largest ever Debenture Issue on November 3, 2026','Bank of Ceylon offers Tier 2 subordinated debentures to raise a maximum of Rs. 30 billion.'),
           ('BOC to raise up to Rs. 30 b in Sri Lanka largest ever listed debt issue','The Bank of Ceylon launches subordinated Tier 2 debentures worth Rs. 30 billion.')),
          (('HNB FINANCE-Toyota Lanka expand vehicle-leasing solutions','HNB FINANCE PLC and Toyota Lanka have agreed on vehicle leasing.'),
           ('HNB FINANCE Toyota Lanka forge partnership to expand vehicle leasing','HNB FINANCE and Toyota Lanka offer leasing to Toyota customers.')),
          (('Seylan Bank Fairway Latitude make home ownership more accessible','Seylan Bank and Fairway Latitude offer housing loans.'),
           ('Seylan Bank partners with Fairway Latitude for housing loans','Seylan Bank and Fairway Latitude signed a home loan agreement.'))
        ]
        for (a_title,a_text),(b_title,b_text) in pairs:
            with self.subTest(a=a_title):
                a=article('https://dailynews.lk/article-abc',a_title,a_text,source='Daily News',day='2026-10-27')
                b=article('https://www.ft.lk/article-abc',b_title,b_text,source='Daily FT',day='2026-10-27')
                sig_a=event_signature(a);sig_b=event_signature(b)
                self.assertEqual(sig_a,sig_b)
                self.assertTrue(sig_a)
                entries=[{'article':z,'assessment':{'event_signature':event_signature(z)}} for z in (a,b)]
                result=group(entries)
                self.assertEqual(len(result),1)
                self.assertEqual(len(result[0]['members']),2)

    def test_distinct_hnb_partnerships_never_merge(self):
        one=article('https://dailynews.lk/one','HNB FINANCE Toyota Lanka offer vehicle leasing',
                     'HNB FINANCE and Toyota Lanka lease Toyota vehicles.')
        other=article('https://dailynews.lk/two','HNB partners with IDEAL Motors',
                       'HNB PLC and IDEAL Motors offer vehicle financing.')
        self.assertNotEqual(event_signature(one),event_signature(other))

    def test_legacy_uses_factual_aliases_and_preserves_url_lineage(self):
        a=article(MIRROR_A,'CBSL reviews lenders early settlement fees and parate auctions after complaints pile up',
                  'The Central Bank reviews parate and early settlement. '+('Banks received consumer complaints. '*8))
        aa=dict(a,source_url=MIRROR_A_ALIAS)
        a['event_key']=legacy.event_key(a);aa['event_key']=legacy.event_key(aa)
        a['summary']=a['body'];aa['summary']=aa['body']
        self.assertTrue(legacy.same_event(a,aa))
        retained,audit=legacy._news_separate_unrelated_members([a,aa],[a,aa])
        groups=legacy.group_events(retained)
        self.assertEqual(len(groups),1)
        self.assertEqual(len(groups[0]['sources']),2)

    def test_held_friday_articles_excluded_from_tuesday_union(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);old=root/'2026-10-26'/'window_1d';new=root/'2026-10-27'/'window_1d'
            current=article('https://www.ft.lk/27','BOC Tier 2 debenture announcement',
                            'BOC proposes new financing.',source='Daily FT',day='2026-10-27')
            past=article('https://www.ft.lk/23','Important Friday banking development',
                          'Bank risk news.',source='Daily FT',day='2026-10-23')
            ca,ci=fixture(new,[current]);fixture(old,[past])
            (old/'integrated_release_result.json').write_text(json.dumps({'status':'HOLD_FOR_REVIEW'}))
            a,i,info=merge_held_edition_evidence(target=date(2026,10,27),report_root=root,
                                                    current_audit=ca,current_inventory=ci,destination=new/'integrated_edition'/'source_union')
            self.assertEqual(info['earliest_inclusive'],'2026-10-26')
            self.assertEqual(info['full_window_count'],1)
            self.assertEqual(len(json.loads(a.read_text())['audit']),1)
            with i.open(encoding='utf-8-sig') as fin:
                self.assertEqual(len(list(csv.DictReader(fin))),1)
            (old/'PUBLISHED_URLS.json').write_text('{}')
            a,i,info=merge_held_edition_evidence(target=date(2026,10,27),report_root=root,
                                                    current_audit=ca,current_inventory=ci,destination=new/'integrated_edition'/'source_union')
            self.assertEqual(info['full_window_count'],1)


    def test_conflicting_same_day_stock_market_values_held_not_duplicated(self):
        island=article('https://island.lk/stock-market-oil','Stock market rattled by oil supply chain disruptions',
            'Stock market rattled by oil supply chain disruptions yesterday. The All Share Price Index went down by 11.06 points while markets responded to oil disruption. Yesterday trading was mixed. '+('The markets responded to oil disruption. '*12),
            source='The Island',day='2026-10-27')
        ft=article('https://www.ft.lk/cse-27','CSE starts week in red, down 0.23%',
            'CSE starts week in red, down 0.23%. The ASPI was down 0.23% or 44.35 points at 19,640.56 yesterday. '+('Shares fell during the session with prices declining. '*12),
            source='Daily FT',day='2026-10-27')
        with tempfile.TemporaryDirectory() as d:
            audit,inventory=fixture(Path(d)/'2026-10-27'/'window_1d',[island,ft])
            work,events,counts,_=prepare(date_iso='2026-10-27',audit=audit,inventory=inventory,sent_paths=[])
            self.assertEqual(counts['review'],2)
            self.assertEqual(counts['included'],0)
            self.assertEqual(len(events),0)
            self.assertTrue(all(x['assessment']['reason']=='CONFLICTING_SAME_CSE_SESSION_NUMBERS' for x in work))

    def test_rejected_dollar_bond_auction_is_financial_not_noise(self):
        from bank_intel.publication.decisions import decide
        row=article('https://www.ft.lk/pdmo','PDMO rejects all Domestic Dollar Bond bids; market says bid rates too high',
            'The Public Debt Management Office rejected all bids under the domestic dollar bond auction because offered rates were high. '+('The debt issuance was for foreign-currency financing. '*9),
            source='Daily FT',day='2026-10-27')
        result=decide(row)
        self.assertEqual(result['decision'],'INCLUDE')
        self.assertEqual(result['section'],'MONETARY_MARKETS_EXTERNAL')

    def test_photo_caption_not_lead(self):
        a=article('https://dailymirror.lk/a','HNB FINANCE Toyota Lanka expand vehicle leasing',
            'Manager Ruwan Indika HNB FINANCE PLC and Toyota Lanka have entered a partnership to offer competitive vehicle leasing. They said loans will cover eligible customers.')
        self.assertTrue(summary(a).startswith('HNB FINANCE PLC'))

if __name__=='__main__':unittest.main()
