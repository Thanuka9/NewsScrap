"""Real historical evidence plus adverse mutations through the release boundary."""
import copy
import csv
from datetime import date
import hashlib
import json
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from replay_publication_integrity import replay
from bank_intel.publication.decisions import decide
from bank_intel.publication.delivery import approve, record_sent
from bank_intel.publication.history_import import import_distributed
from bank_intel.publication.identity import canonical, event_signature
from bank_intel.publication.ledger import validate_outputs, template_issues
from bank_intel.publication.reconcile import read_sent_html, was_sent, load_confirmed_history
from bank_intel.publication.release import audit_release
from bank_intel.publication.temporal import late_status, load_collection_metadata
from bank_intel.publication.workflow import load_data
from news_publication_history import assert_edition_not_sent, published_cards
from news_release_gate import prepare_coverage

FIXTURE=ROOT/'tests/fixtures/publication_2026_10_26_27'

def read(path):return json.loads(path.read_text(encoding='utf-8'))

@pytest.fixture(scope='module')
def historical(tmp_path_factory):
    out=tmp_path_factory.mktemp('historical_integrity')
    result=replay(out)
    return out,result,read(out/'publication_decisions.json'),read(out/'candidate_edition.json')


def test_complete_historical_population_and_output_consistency(historical):
    out,result,ledger,manifest=historical
    assert result['input_articles']==320
    assert sum(result['decision_counts'].values())==320
    assert result['prior_edition_repeats']==result['outside_window']==0
    assert result['output_inconsistencies']==[]
    assert manifest['counts']['included']==sum(len(e['sources']) for e in manifest['events'])
    for path in (out/'release_qa.csv',out/'final_article_inventory.csv',out/'article_decision_trace.csv'):
        with path.open(encoding='utf-8-sig') as stream:rows=list(csv.DictReader(stream))
        assert {(r['decision_id'],r['decision']) for r in rows}=={(r['decision_id'],r['decision']) for r in ledger['articles']}


def test_real_prior_summary_blocks_reprinted_subscription_result():
    prior=read_sent_html([FIXTURE/'prior_reference.html'])
    previous=next(p for p in prior if 'Sampath Bank discloses' in p['headline'])
    assert '12.27' in previous['summary'] and '22.7' in previous['summary']
    new={**previous,'headline':'Sampath Bank debenture subscriptions exceed offer','publication_date':'2026-10-27',
         'source_url':'https://island.lk/reprint','body':previous['summary']}
    assert was_sent(new,prior)


def test_named_repeats_suppressed_and_material_exclusions_recovered(historical):
    _,_,ledger,_=historical
    rows=ledger['articles']
    for token in ('Sampath-Bank-s-Rs-10-b','Amana-Bank-opens-50th','HNB-FINANCE-Toyota'):
        matches=[r for r in rows if token in r['source_url']]
        assert matches and all(r['decision']=='ALREADY_SENT' or r['decision_reason'].startswith('ALIAS_') for r in matches)
    material=[r for r in rows if '13.6' in r['headline'] or 'Resus Energy' in r['headline']]
    assert len(material)>=5
    assert all(r['decision'] in ('INCLUDE','REVIEW') for r in material)
    bangladesh=next(r for r in rows if r['headline'].startswith('Inflation pushes'))
    assert bangladesh['decision']=='EXCLUDE'
    uae=next(r for r in rows if 'opportunities for Lankans in UAE' in r['headline'])
    assert uae['decision']=='REVIEW'


def test_export_and_bond_reports_consolidated(historical):
    _,_,_,manifest=historical
    assert len([e for e in manifest['events'] if e['category']=='EXPORT_EARNINGS'])==1
    bonds=[e for e in manifest['events'] if e['category']=='CAPITAL_MARKETS_FUNDING']
    assert len(bonds)==1 and len(bonds[0]['sources'])==2


def test_html_tampering_and_missing_members_fail(historical):
    out,_,ledger,manifest=historical
    html=next(out.glob('*.html')).read_text(encoding='utf-8')
    assert validate_outputs(ledger,manifest,html.replace(manifest['events'][0]['summary'],'fabricated summary'))
    changed=copy.deepcopy(manifest);changed['events'][0]['sources'].pop()
    assert validate_outputs(ledger,changed,html)
    changed=copy.deepcopy(ledger)
    next(r for r in changed['articles'] if r['decision']=='INCLUDE')['decision']='EXCLUDE'
    kinds={r['kind'] for r in validate_outputs(changed,manifest,html)}
    assert 'CONTRADICTORY_PUBLICATION_DECISION' in kinds and 'DECISION_HASH_MISMATCH' in kinds


def test_partial_coverage_stays_held_with_identifiable_evidence(historical):
    out,result,_,_=historical
    assert result['release_status']=='HOLD_FOR_REVIEW'
    coverage=read(out/'coverage_evidence.json')
    for publisher in ('Daily FT','NewsFirst','Ceylon Today'):
        evidence=coverage['publishers'][publisher]
        assert evidence['automatic_issues']
        assert 'sha256=' in evidence['evidence']
        assert 'EXTRACTION_FAILURE_URLS_NOT_RECORDED' in evidence['automatic_issues']
    with pytest.raises(RuntimeError):approve(out/'candidate_edition.json',next(out.glob('*.html')),'test')


def test_distributed_import_is_immutable_and_locks_future_reruns(tmp_path):
    for day,name in [('26','prior_reference.html'),('27','sent_reference_27.html')]:
        record=import_distributed(FIXTURE/name,tmp_path,f'2026-10-{day}','test operator',True)
        data=read(record)
        assert data['sent_at']==''  # Do not fabricate a historical distribution time.
        assert hashlib.sha256(Path(data['attachment']).read_bytes()).hexdigest()==data['attachment_sha256']
        with pytest.raises(RuntimeError):assert_edition_not_sent(tmp_path,date(2026,10,int(day)))
    previous,issues=load_confirmed_history(tmp_path,'2026-10-28')
    assert previous and not issues
    assert len(published_cards(tmp_path,date(2026,10,28)))==41
    assert all(was_sent(r,previous) for r in read_sent_html([FIXTURE/'sent_reference_27.html']))


def test_date_and_late_discovery_are_separate():
    yesterday={'publication_date':'2026-11-03','first_seen_at':'2026-11-04T01:00:00+00:00'}
    assert late_status(yesterday,date(2026,11,4),[])=='LATE_DISCOVERY'
    assert late_status({**yesterday,'first_seen_at':''},date(2026,11,4),[])=='MISSING_FIRST_DISCOVERY_EVIDENCE'
    assert late_status({**yesterday,'first_seen_at':'2026-11-03T01:00:00+00:00'},date(2026,11,4),[])=='LATE_NEWS_NOT_ESTABLISHED'


def test_generic_discovery_uses_first_version_not_latest(tmp_path):
    folder=tmp_path/'data/staged/newsfirst/2026-11-03/one';folder.mkdir(parents=True)
    original={'source_url':'https://newsfirst.lk/one','collected_at':'2026-11-03T09:00:00+05:30','published_at':'2026-11-03T08:00:00+05:30'}
    (folder/'v001.json').write_text(json.dumps(original))
    (folder/'v002.json').write_text(json.dumps({**original,'collected_at':'2026-11-04T09:00:00+05:30'}))
    result=load_collection_metadata(tmp_path,['2026-11-03'])
    assert result[original['source_url']]['first_seen_at']==original['collected_at']


def test_future_material_recall_is_not_tied_to_october():
    for h,b in [('Sri Lanka exports earn US$14 billion in ten months','Sri Lanka export earnings reached US$14 billion in the first ten months of 2027.'),
                ('New Energy lists green bonds on stock exchange','New Energy issued a corporate green bond to finance its renewable energy projects in Sri Lanka.')]:
        row={'headline':h,'body':b+' This announcement describes the financial transaction and its funding impact on the company and the economy.','publication_date':'2027-11-04','section':'EXCLUDE'}
        assert decide(row)['decision']=='INCLUDE'


def test_approved_template_is_locked():
    root=ROOT/'src/bank_intel/publication'
    assert template_issues(root,root/'news_style.css')==[]
    assert '{{DISPLAY_DATE}}' in (root/'report_template.html').read_text(encoding='utf-8')


def test_inventory_factual_conflict_fails_before_selection(tmp_path):
    audit=tmp_path/'audit.json';inventory=tmp_path/'inventory.csv'
    audit.write_text(json.dumps({'audit':[{'source_url':'https://ft.lk/a','headline':'A','publication_date':'2026-11-04','source_name':'Daily FT'}]}))
    inventory.write_text('source_url,headline,publication_date,source_name\nhttps://ft.lk/a,A,2026-11-03,Daily FT\n')
    with pytest.raises(ValueError,match='factual disagreement'):load_data(audit,inventory)


def test_recurring_transactions_do_not_share_identity_across_months():
    row={'headline':'Sampath Bank debenture issue oversubscribed','body':'Sampath Bank announced subscriptions for its debenture issue.', 'publication_date':'2026-11-04'}
    from bank_intel.publication.identity import same_event
    assert not same_event(row,{**row,'source_url':'https://ft.lk/other','publication_date':'2027-11-04'})


def test_coverage_signoff_invalidated_by_changed_evidence(tmp_path):
    statuses=tmp_path/'data/source_status';statuses.mkdir(parents=True)
    payload={'execution_status':'SUCCESS','health_status':'HEALTHY','completed_at':'2026-11-04T09:00:00+05:30',
             'discovery_surfaces':['FINANCIAL_SERVICES','FRONT_PAGE','TOP_STORY','HOMEPAGE'],
             'candidate_urls':20}
    status=statuses/'daily_ft.json';status.write_text(json.dumps(payload))
    output=tmp_path/'coverage.json';prepare_coverage(date(2026,11,4),tmp_path,output)
    signed=read(output);signed['publishers']['Daily FT'].update(status='VERIFIED',reviewer='test',reviewed_at='2026-11-04T10:00:00+05:30')
    output.write_text(json.dumps(signed))
    # Changed clean evidence verifies under the automatic policy, not the old signature.
    status.write_text(json.dumps({**payload,'candidate_urls':21}))
    prepare_coverage(date(2026,11,4),tmp_path,output)
    assert read(output)['publishers']['Daily FT']['reviewer']=='AUTOMATIC_COVERAGE_POLICY_V1'
    # Changed evidence with a possibly financial failure needs a person again.
    status.write_text(json.dumps({**payload,'candidate_urls':22,'health_status':'PARTIAL_ERROR',
        'unresolved_fetch_urls':['https://www.ft.lk/financial-services/Bank-capital-rules-tightened/42-1']}))
    prepare_coverage(date(2026,11,4),tmp_path,output)
    assert read(output)['publishers']['Daily FT']['status']=='PENDING'


def test_collector_retries_and_identifies_unresolved_urls(monkeypatch,tmp_path):
    from bank_intel.collection import generic_source_engine as engine
    urls=['https://newsfirst.lk/recovered','https://newsfirst.lk/broken']
    monkeypatch.setattr(engine,'discover_article_links',lambda _: (urls,{}))
    from collections import Counter
    calls=Counter()
    def extract(url,profile):
        calls[url]+=1
        ok=url==urls[0] and calls[url]==2
        return engine.ArticleResult(url=url,valid=ok,headline='Sri Lanka exports earn revenue',
            published_at='2026-11-04T07:00:00+05:30',article_text='Source article body',errors=[] if ok else ['EMPTY_BODY'])
    monkeypatch.setattr(engine,'safe_extract_article',extract)
    result=engine.collect_and_stage(project_root=tmp_path,source_key='newsfirst',profile={'name':'NewsFirst'},
        from_date=date(2026,11,4),to_date=date(2026,11,4),production=True)
    assert result['invalid_extractions']==1 and result['recovered_urls']==[urls[0]]
    assert result['extraction_failures'][0]['url']==urls[1]
    assert all(n==2 for n in calls.values())


def test_clean_future_release_approval_and_post_send_register(tmp_path):
    from bank_intel.publication.workflow import build
    from bank_intel.publication.coverage import REQUIRED_SURFACES
    row={'headline':'Commercial Bank reports quarterly profit growth',
         'body':'Commercial Bank reports quarterly profit growth in its latest financial results. The bank said earnings increased as funding costs declined and credit quality improved, with capital remaining above the applicable regulatory requirements.',
         'source_url':'https://ft.lk/future-result','source_name':'Daily FT',
         'publication_date':'2026-11-04','section':'EXCLUDE'}
    audit=tmp_path/'audit.json';audit.write_text(json.dumps({'audit':[row]}))
    inventory=tmp_path/'inventory.csv'
    with inventory.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(row));writer.writeheader();writer.writerow(row)
    statuses=tmp_path/'source_status';statuses.mkdir()
    from bank_intel.publication.identity import SOURCE_KEYS
    names=SOURCE_KEYS
    evidence={}
    for publisher,key in names.items():
        surfaces=sorted(REQUIRED_SURFACES[publisher])
        (statuses/(key+'.json')).write_text(json.dumps({'execution_status':'SUCCESS','health_status':'HEALTHY','completed_at':'2026-11-04T10:00:00+05:30','discovery_surfaces':surfaces}))
        evidence[publisher]={'status':'VERIFIED','reviewer':'test editor','reviewed_at':'2026-11-04T10:00:00+05:30','evidence':'Synthetic fully observed coverage for this test','surfaces':surfaces,'automatic_issues':[]}
    output=tmp_path/'reports/2026-11-04/window_1d/integrated_edition'
    manifest,report=build(date_iso='2026-11-04',audit=audit,inventory=inventory,output=output,
        sent=[FIXTURE/'prior_reference.html'],css_file=ROOT/'src/bank_intel/publication/news_style.css',source_status=statuses)
    assert manifest['status']=='QA_PASS_NOT_DELIVERED'
    coverage=tmp_path/'coverage.json';coverage.write_text(json.dumps({'report_date':'2026-11-04','publishers':evidence}))
    qa=output/'news_release_qa.json'
    assert audit_release(report=report,manifest_path=output/'candidate_edition.json',inventory=inventory,coverage=coverage,out=qa,report_date='2026-11-04')==0
    approval=approve(output/'candidate_edition.json',report,'test editor')
    assert not (output/'SENT_EDITION.json').exists()
    with pytest.raises(PermissionError):record_sent(approval,'test recipients',False)
    sent=record_sent(approval,'test recipients',True,'simulated-test-message')
    assert read(sent)==read(output/'sent_events.json')
    prior,issues=load_confirmed_history(tmp_path/'reports','2026-11-05')
    assert not issues and was_sent(row,prior)
    with pytest.raises(RuntimeError):assert_edition_not_sent(tmp_path/'reports',date(2026,11,4))


def test_signed_include_cannot_resurrect_previously_sent_exclusion(tmp_path):
    from bank_intel.publication.workflow import prepare
    prior=read_sent_html([FIXTURE/'prior_reference.html'])[0]
    row={**prior,'headline':'Routine meeting','body':'Meeting attendees discussed general issues.','source_name':'Daily Mirror','section':'EXCLUDE','publication_date':'2026-10-27'}
    audit=tmp_path/'audit.json';audit.write_text(json.dumps({'audit':[row]}))
    inventory=tmp_path/'inventory.csv'
    inventory.write_text('source_url\n'+row['source_url']+'\n')
    review=tmp_path/'review.csv'
    with review.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['source_url','editorial_action','reviewer','reason']);writer.writeheader()
        writer.writerow({'source_url':row['source_url'],'editorial_action':'INCLUDE','reviewer':'test','reason':'Please include'})
    work,events,_,_=prepare(date_iso='2026-10-27',audit=audit,inventory=inventory,sent_paths=[FIXTURE/'prior_reference.html'],review_decisions=review)
    assert work[0]['assessment']['decision']=='ALREADY_SENT' and events==[]


def test_publisher_alias_discovery_uses_earliest_alias(tmp_path):
    base='https://www.dailymirror.lk/{}/CBSL-reviews-fees/{}-353382'
    for name,path,section,seen in [('a','business',215,'2026-10-26T15:13:00+05:30'),
                                   ('b','business-news',273,'2026-10-27T08:32:00+05:30')]:
        folder=tmp_path/'data/staged/daily_mirror/2026-10-26'/name;folder.mkdir(parents=True)
        (folder/'v001.json').write_text(json.dumps({'source_url':base.format(path,section),'collected_at':seen,
                                                    'published_at':'2026-10-26T12:12:00+05:30'}))
    result=load_collection_metadata(tmp_path,['2026-10-26'])
    later=result[canonical(base.format('business-news',273))]
    assert later['first_seen_at']=='2026-10-26T15:13:00+05:30'
    # Seen on the 26th: a 27th alias fetch must not turn it into a late discovery.
    row={'publication_date':'2026-10-26',**later}
    assert late_status(row,date(2026,10,27),[])=='LATE_NEWS_NOT_ESTABLISHED'
    sent=[{'sent_at':'2026-10-26T09:00:00+05:30'}]
    assert late_status(row,date(2026,10,27),sent)=='PUBLISHED_AFTER_PREVIOUS_SEND'


def test_summary_drops_subheadline_deck_and_inline_byline():
    from bank_intel.publication.evidence import summary
    deck={'headline':'Cargills Bank introduces ocean plastic debit card',
          'body':'- Initiative to support marine conservation Cargills Bank has launched the Ocean Plastic Debit Card. It is the first such card in Sri Lanka.'}
    assert summary(deck).startswith('Cargills Bank has launched')
    byline={'headline':'Bond yields steady','body':'By Wealth Trust Securities The secondary Bond market yesterday saw yields hold steady. Volumes were moderate.'}
    assert summary(byline).startswith('The secondary Bond market')
    # No recognisable lead: keep the source text rather than guess a cut point;
    # the deck's points are kept, written as sentences.
    unclear={'headline':'Port talks','body':'- Notes regional policy shifts - Talks continue. Officials met on Monday.'}
    assert summary(unclear).startswith('Notes regional policy shifts.')


def test_budget_advocacy_is_not_an_automatic_fiscal_indicator():
    body='The association presented its recommendations for the national budget to the Treasury Secretary at a meeting this month.'
    # Editor, 12 Nov 2026: a Budget wish-list with no domain subject stays out altogether.
    assert decide({'headline':'OPA presents collective professional proposals for Budget 2027','body':body,'publication_date':'2026-10-27'})['decision']=='EXCLUDE'
    # A wish-list on tax is in the domain and still goes to an editor.
    assert decide({'headline':'OPA presents tax proposals for Budget 2027','body':body,'publication_date':'2026-10-27'})['decision']=='REVIEW'
    fiscal=('The Budget 2027 deficit target has been set at 5.1% of GDP, the Treasury said, with revenue measures '
            'expected to lift collections and reduce government borrowing from domestic banks over the year.')
    assert decide({'headline':'Budget 2027 deficit target set at 5.1% of GDP','body':fiscal,'publication_date':'2026-10-27'})['decision']=='INCLUDE'


def test_send_time_recorded_once_for_imported_edition(tmp_path):
    from bank_intel.publication.delivery import record_send_time
    record=import_distributed(FIXTURE/'prior_reference.html',tmp_path,'2026-10-26','test operator',True)
    with pytest.raises(ValueError):record_send_time(record,'2026-10-26T09:10:00','op')  # no offset
    with pytest.raises(ValueError):record_send_time(record,'2026-10-25T09:10:00+05:30','op')
    with pytest.raises(ValueError):record_send_time(record,'2099-01-01T09:10:00+05:30','op')
    record_send_time(record,'2026-10-26T09:10:00+05:30','op')
    for path in (record,record.parent/'sent_events.json'):
        assert read(path)['sent_at']=='2026-10-26T09:10:00+05:30'
    with pytest.raises(FileExistsError):record_send_time(record,'2026-10-26T10:00:00+05:30','op')
    previous,issues=load_confirmed_history(tmp_path,'2026-10-27')
    assert not issues and {p['sent_at'] for p in previous}=={'2026-10-26T09:10:00+05:30'}


def test_coverage_exception_needs_reason_and_permitted_defect():
    from bank_intel.publication.coverage import open_issues
    evidence={'automatic_issues':['INVALID_ARTICLE_EXTRACTION','COLLECTOR_HEALTH:PARTIAL_ERROR']}
    assert open_issues(evidence)==evidence['automatic_issues']
    evidence['accepted_issues']={'INVALID_ARTICLE_EXTRACTION':'Photo-only page, URL inspected','COLLECTOR_HEALTH:PARTIAL_ERROR':' '}
    assert open_issues(evidence)==['COLLECTOR_HEALTH:PARTIAL_ERROR']
    # Unknowable coverage can never be signed away.
    for issue in ('COLLECTOR_NOT_SUCCESS','STALE_STATUS','MISSING_SECTIONS:business','EXTRACTION_FAILURE_URLS_NOT_RECORDED'):
        assert open_issues({'automatic_issues':[issue],'accepted_issues':{issue:'reason'}})==[issue]


def test_coverage_signature_survives_relative_and_absolute_root(tmp_path, monkeypatch):
    statuses=tmp_path/'data/source_status';statuses.mkdir(parents=True)
    for key in ('daily_ft','daily_mirror','daily_news','the_morning','island','newsfirst','ceylon_today'):
        (statuses/(key+'.json')).write_text(json.dumps({'execution_status':'SUCCESS','health_status':'HEALTHY','completed_at':'2026-11-04T09:00:00+05:30','discovery_surfaces':[]}))
    out=tmp_path/'coverage.json'
    prepare_coverage(date(2026,11,4),tmp_path,out)
    sheet=read(out);sheet['publishers']['Daily FT'].update(status='VERIFIED',reviewer='r',reviewed_at='t')
    out.write_text(json.dumps(sheet))
    monkeypatch.chdir(tmp_path)
    prepare_coverage(date(2026,11,4),Path('.'),out)
    assert read(out)['publishers']['Daily FT']['status']=='VERIFIED'
