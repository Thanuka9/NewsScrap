"""Regressions reproduced by the 28 October 2026 ETL audit (review/etl_audit_20261028)."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from bank_intel.publication.identity import same_event
from bank_intel.publication.reconcile import prior_matches
from bank_intel.publication.evidence import summary
from bank_intel.publication.decisions import decide
from bank_intel.publication.contract import validate_release
from bank_intel.common.state import JsonArticleState

CABINET_ADB = {
    'headline': 'Cabinet approves $ 100 m ADB loan to support Sri Lanka’s economic recovery',
    'source_url': 'https://www.ft.lk/front-page/Cabinet-approves-100-m-ADB-loan-to-support-Sri-Lanka-s-economic-recovery/44-799234',
    'publication_date': '2026-10-28',
    'body': ('The Cabinet of Ministers has approved a $ 100 million loan agreement with the Asian Development Bank (ADB) '
             'to support Sri Lanka’s economic recovery and reform agenda. The facility forms part of the Macro-Recovery '
             'Sectoral Development Program and comprises a policy-based loan and an investment loan.'),
}
# The 22 October sent record holds only a headline and URL.
OLD_ADB = {'headline': 'ADB approves $ 100 m to strengthen social protection and expand economic opportunities',
           'source_url': 'https://www.ft.lk/front-page/ADB-approves-100-m/44-798982', 'publication_date': '', 'summary': ''}


def test_same_lender_and_amount_for_different_programme_is_not_already_sent():
    assert not same_event(CABINET_ADB, OLD_ADB)
    sent, updates = prior_matches(CABINET_ADB, [OLD_ADB])
    assert sent == [] and updates == []


def test_true_reprint_of_a_financing_story_still_matches():
    reprint = {**CABINET_ADB, 'source_url': 'https://www.themorning.lk/articles/adb-loan',
               'headline': 'Cabinet nod for $ 100 m ADB loan for economic recovery'}
    assert same_event(CABINET_ADB, reprint)


def test_body_mention_of_imf_does_not_split_a_same_event_pair():
    a = {'headline': 'NDB posts 3Q PAT of Rs. 2.9 b', 'publication_date': '2026-10-22', 'source_url': 'https://ft.lk/a',
         'body': 'National Development Bank posted a profit of Rs. 2.9 billion; the IMF programme and US$ 3 billion reserves supported confidence.'}
    b = {'headline': 'NDB records PAT of Rs 2.9 b for Q3', 'publication_date': '2026-10-22', 'source_url': 'https://dailynews.lk/b',
         'body': 'National Development Bank recorded profit after tax of Rs 2.9 billion for the third quarter.'}
    assert same_event(a, b)


def test_material_policy_examples_from_corrected_edition():
    apg = json.loads((ROOT / 'tests/fixtures/apg_aml_2026_10_28.json').read_text(encoding='utf-8'))
    assert decide(apg)['decision'] == 'INCLUDE' and decide(apg)['section'] == 'KEY_BANKING_SUPERVISORY'
    assert decide(CABINET_ADB)['decision'] == 'INCLUDE'


def test_summary_never_ends_on_an_honorific_fragment():
    apg = json.loads((ROOT / 'tests/fixtures/apg_aml_2026_10_28.json').read_text(encoding='utf-8'))
    text = summary(apg)
    assert not text.rstrip().endswith(('Dr.', 'Mr.', 'Prof.', 'Eng.'))
    assert text.rstrip()[-1] in '.!?"”'
    assert 'Prime Minister Dr. Harini Amarasuriya' in text or 'Prime Minister' not in text


def test_corrupt_state_fails_loudly_instead_of_resetting(tmp_path):
    path = tmp_path / 'state.json'
    path.write_text('{"articles": {', encoding='utf-8')
    with pytest.raises(RuntimeError, match='CORRUPT_ARTICLE_STATE'):
        JsonArticleState(path)


def test_release_contract_reports_already_sent_for_a_registered_edition():
    result = validate_release(ROOT, '2026-10-28')
    assert result['status'] == 'ALREADY_SENT'


def test_release_contract_distinguishes_review_from_failure(tmp_path):
    folder = tmp_path / 'data/processed/reports/2026-11-04/window_1d'
    folder.mkdir(parents=True)
    (folder / 'report_manifest.json').write_text(json.dumps({'publication_date': '2026-11-04', 'status': 'HOLD_FOR_REVIEW',
                                                            'release_issues': [{'kind': 'UNREVIEWED_CANDIDATES'}]}))
    assert validate_release(tmp_path, '2026-11-04')['status'] == 'REVIEW_REQUIRED'
    (folder / 'report_manifest.json').write_text(json.dumps({'publication_date': '2026-11-04', 'status': 'SUCCESS'}))
    with pytest.raises(RuntimeError, match='UNSUPPORTED_RELEASE_STATUS'):
        validate_release(tmp_path, '2026-11-04')


def _ceylon_status(tmp_path, oldest):
    statuses = tmp_path / 'data/source_status'; statuses.mkdir(parents=True, exist_ok=True)
    for key in ('daily_ft', 'daily_mirror', 'daily_news', 'the_morning', 'island', 'newsfirst', 'ceylon_today'):
        (statuses / (key + '.json')).write_text(json.dumps({'execution_status': 'SUCCESS', 'health_status': 'HEALTHY',
            'completed_at': '2026-11-04T09:00:00+05:30', 'discovery_surfaces': []}))
    (statuses / 'ceylon_today.json').write_text(json.dumps({
        'execution_status': 'SUCCESS', 'health_status': 'HEALTHY', 'completed_at': '2026-11-04T09:00:00+05:30',
        'discovery_surfaces': ['DAILY_FINANCE', 'SUNDAY_FINANCE', 'HOMEPAGE'],
        'failed_discovery_surfaces': ['DAILY_FINANCE', 'SUNDAY_FINANCE'],
        'listing_pages': [{'listing_name': 'DAILY_FINANCE', 'pagination_complete': False},
                          {'listing_name': 'SUNDAY_FINANCE', 'pagination_complete': False}],
        'wp_api_discovery': {'api_status': 'VERIFIED', 'api_pagination_complete': True, 'api_oldest_date': oldest}}))


def test_wordpress_api_clears_html_archive_gaps_only_when_it_reaches_past_the_window(tmp_path):
    sys.path.insert(0, str(ROOT / 'scripts'))
    from news_release_gate import prepare_coverage
    from datetime import date
    _ceylon_status(tmp_path, '2026-10-01')
    sheet = prepare_coverage(date(2026, 11, 4), tmp_path, tmp_path / 'a.json')
    assert json.loads(sheet.read_text(encoding='utf-8'))['publishers']['Ceylon Today']['automatic_issues'] == []
    _ceylon_status(tmp_path, '2026-11-04')  # API page ends inside the window: not proof
    sheet = prepare_coverage(date(2026, 11, 4), tmp_path, tmp_path / 'b.json')
    issues = json.loads(sheet.read_text(encoding='utf-8'))['publishers']['Ceylon Today']['automatic_issues']
    assert 'UNRESOLVED_FETCH_OR_DISCOVERY' in issues and 'INCOMPLETE_PAGINATION:DAILY_FINANCE' in issues


def test_island_advertisement_slots_are_not_articles():
    from bank_intel.collectors.sources.island import IslandBusinessAdapter
    adapter = IslandBusinessAdapter()
    assert not adapter.is_article_url('https://island.lk/homepage-advertiesment-left-1')
    assert adapter.is_article_url('https://island.lk/airbus-deal-namal-among-four-indicted')


def test_missing_previous_send_time_is_named_not_assumed():
    from datetime import date
    from bank_intel.publication.temporal import late_status
    article = {'publication_date': '2026-10-28', 'first_seen_at': '2026-10-28T10:00:00+05:30',
               'published_at': '2026-10-28T09:00:00+05:30'}
    prior = [{'edition_date': '2026-10-28', 'sent_at': ''}, {'edition_date': '2026-10-27', 'sent_at': '2026-10-27T12:19:00+05:30'}]
    assert late_status(article, date(2026, 10, 29), prior) == 'PREVIOUS_SEND_TIME_MISSING:2026-10-28'
    prior[0]['sent_at'] = '2026-10-28T08:30:00+05:30'
    assert late_status(article, date(2026, 10, 29), prior) == 'PUBLISHED_AFTER_PREVIOUS_SEND'


def test_short_structured_body_is_kept_and_judged_on_relevance():
    from bank_intel.collectors.sources._public_business_base import short_article_is_valid
    body = ('An eight-member team from Ceylinco Life recently participated in a four-day leadership training programme '
            'in Kuala Lumpur, Malaysia, designed to strengthen agency management capabilities and sales leadership.')
    assert short_article_is_valid('Ceylinco Life sales team sharpens leadership skills in Malaysia', body, '2026-10-28')
    assert not short_article_is_valid('CBSL cuts policy rate', body, '2026-10-28')
    assert not short_article_is_valid('Ceylinco Life sales team', 'Too short.', '2026-10-28')
    assert not short_article_is_valid('Ceylinco Life sales team sharpens leadership skills', body, None)


def test_partial_source_gets_one_recovery_run_and_keeps_the_better_result(monkeypatch):
    sys.path.insert(0, str(ROOT / 'scripts'))
    import run_collection as rc
    monkeypatch.setattr(rc.time, 'sleep', lambda s: None)
    written = {}
    import bank_intel.common.source_status as ss
    monkeypatch.setattr(ss, 'write_source_status', lambda **kw: written.update(kw))
    first = {'execution_status': 'SUCCESS', 'unresolved_fetch_urls': ['a', 'b']}
    monkeypatch.setattr(rc, 'run_source', lambda **kw: {'execution_status': 'SUCCESS', 'unresolved_fetch_urls': []})
    chosen = rc.recover_partial_source(source_key='island', source_config={}, run_id='r', result=dict(first))
    assert chosen['unresolved_fetch_urls'] == [] and chosen['partial_recovery']['recovery_run_used']
    monkeypatch.setattr(rc, 'run_source', lambda **kw: {'execution_status': 'FAILED'})
    chosen = rc.recover_partial_source(source_key='island', source_config={}, run_id='r', result=dict(first))
    assert chosen['unresolved_fetch_urls'] == ['a', 'b'] and written['payload'] is chosen
    clean = {'execution_status': 'SUCCESS', 'unresolved_fetch_urls': []}
    assert rc.recover_partial_source(source_key='x', source_config={}, run_id='r', result=clean) is clean


def test_28_october_replay_reproduces_delivered_edition_without_editorial_input(tmp_path):
    """The pipeline alone must select every story of the distributed edition."""
    import collections, hashlib
    from bank_intel.publication.workflow import build
    from bank_intel.publication.reconcile import read_sent_html
    from bank_intel.publication.identity import canonical
    fixture = ROOT / 'tests/fixtures/publication_2026_10_28'
    frozen = json.loads((fixture / 'fixture_manifest.json').read_text(encoding='utf-8'))
    for name, digest in frozen['files'].items():
        assert hashlib.sha256((fixture / name).read_bytes()).hexdigest() == digest, name
    manifest, _ = build(date_iso='2026-10-28', audit=fixture / 'audit.json', inventory=fixture / 'inventory.csv',
                        output=tmp_path, sent=[], sent_report_root=fixture / 'sent_history',
                        css_file=ROOT / 'src/bank_intel/publication/news_style.css', source_status=None,
                        model_traces=json.loads((fixture / 'model_traces.json').read_text(encoding='utf-8')),
                        collection_metadata=json.loads((fixture / 'collection_metadata.json').read_text(encoding='utf-8')))
    ledger = json.loads((tmp_path / 'publication_decisions.json').read_text(encoding='utf-8'))
    decision = {a['source_url']: a['decision'] for a in ledger['articles']}
    sent = collections.defaultdict(set)
    for row in read_sent_html([fixture / 'sent_reference_28.html']):
        sent[row['headline']].add(canonical(row['source_url']))
    missed = [h for h, urls in sent.items() if not any(decision.get(u) == 'INCLUDE' for u in urls)]
    assert missed == []
    delivered = set().union(*sent.values())
    extra = [e['headline'] for e in manifest['events']
             if not any(canonical(s['source_url']) in delivered for s in e['sources'])]
    assert extra == []
    assert len(manifest['events']) == len(sent)


def test_second_run_is_refused_while_a_child_stage_shares_the_lock(tmp_path, monkeypatch):
    from bank_intel.common.reliability import project_lock
    monkeypatch.delenv('NEWS_PIPELINE_LOCK_TOKEN', raising=False)
    with project_lock(tmp_path):
        with project_lock(tmp_path):  # a child stage inherits the owner's token
            pass
        import os
        token = os.environ.pop('NEWS_PIPELINE_LOCK_TOKEN')  # a separate, manual run has no token
        try:
            with pytest.raises(RuntimeError, match='PIPELINE_BUSY'):
                with project_lock(tmp_path):
                    pass
        finally:
            os.environ['NEWS_PIPELINE_LOCK_TOKEN'] = token
    with project_lock(tmp_path):  # released after the owner finishes
        pass


def test_delivered_edition_reconciliation_records_overridden_decisions(tmp_path):
    from bank_intel.publication.history_import import reconcile_delivered
    folder = tmp_path / '2026-11-04/window_1d'; edition = folder / 'integrated_edition'; edition.mkdir(parents=True)
    (edition / 'publication_decisions.json').write_text(json.dumps({'articles': [
        {'source_url': 'https://ft.lk/a', 'headline': 'A', 'decision': 'INCLUDE', 'decision_reason': 'X'},
        {'source_url': 'https://ft.lk/b', 'headline': 'B', 'decision': 'EXCLUDE', 'decision_reason': 'SIGNED_EDITORIAL_no'}]}))
    (edition / 'candidate_edition.json').write_text(json.dumps({'sha256': 'draft', 'decision_ledger': 'publication_decisions.json',
        'events': [{'sources': [{'source_url': 'https://ft.lk/a'}]}]}))
    (folder / 'report_manifest.json').write_text(json.dumps({'status': 'QA_PASS_NOT_DELIVERED', 'integrated_manifest': str(edition / 'candidate_edition.json')}))
    (folder / 'SENT_EDITION.json').write_text(json.dumps({'date': '2026-11-04', 'attachment': 'sent.html', 'attachment_sha256': 'sent',
        'event_count': 2, 'confirmed_at': 't', 'source_urls': ['https://ft.lk/a', 'https://ft.lk/b']}))
    result = reconcile_delivered(folder)
    assert result['added_sources'] == ['https://ft.lk/b'] and not result['matches_candidate']
    assert 'SIGNED_EDITORIAL_no' in (folder / 'editorial_corrections_from_delivery.csv').read_text(encoding='utf-8-sig')
    manifest = json.loads((folder / 'report_manifest.json').read_text(encoding='utf-8'))
    assert manifest['status'] == 'DELIVERED' and manifest['delivered_sha256'] == 'sent'


def test_coverage_verifies_itself_only_when_every_failure_is_non_financial():
    sys.path.insert(0, str(ROOT / 'scripts'))
    from news_release_gate import automatic_coverage_acceptance
    issues = ['UNRESOLVED_FETCH_OR_DISCOVERY', 'COLLECTOR_HEALTH:PARTIAL_ERROR']
    sport = {'unresolved_fetch_urls': ['https://island.lk/madushanka-skittles-pakistan-for-110-secures-final']}
    assert automatic_coverage_acceptance(sport, issues)
    bank = {'unresolved_fetch_urls': ['https://www.ft.lk/financial-services/Peoples-Bank-explores-banking-in-UAE/42-1']}
    assert automatic_coverage_acceptance(bank, issues) is None
    unreadable = {'unresolved_fetch_urls': ['https://island.lk/294648-2']}
    assert automatic_coverage_acceptance(unreadable, issues) is None
    assert automatic_coverage_acceptance({**sport, 'failed_discovery_surfaces': ['News']}, issues) is None
    assert automatic_coverage_acceptance({}, ['COLLECTOR_NOT_SUCCESS']) is None
    assert automatic_coverage_acceptance({}, []) == {}
    unrecorded = {'invalid_extraction': 1}
    assert automatic_coverage_acceptance(unrecorded, ['INVALID_ARTICLE_EXTRACTION']) is None


def test_holdout_20_october_matching_and_wording_fixes():
    """Bugs found on the 20 Oct 2026 holdout edition."""
    from bank_intel.publication.model_policy import financial_signal
    # A 0.62% fall is not the 0.65% move of a different session.
    a = {'headline': 'CSE begins weak down 0.62% on muted activity', 'publication_date': '2026-10-20', 'source_url': 'https://ft.lk/a',
         'body': 'The ASPI fell 0.62% while the S&P SL20 declined 0.66% on turnover of Rs. 680 million.'}
    b = {'headline': 'CSE closes higher on Friday despite weekly index declines', 'publication_date': '2026-10-19', 'source_url': 'https://ft.lk/b',
         'body': 'The ASPI rose 0.65% on Friday though it lost 0.86% and 1.07% over the week.'}
    assert not same_event(a, b)
    # Commentaries on the same 5% target on different days are different stories.
    c = {'headline': 'Inflation target: 5% achievable if oil moderates, food supply stabilises', 'publication_date': '2026-10-20', 'source_url': 'https://ft.lk/c'}
    d = {'headline': 'We are not inflation nutters; SL not ready for sub-5% inflation target', 'publication_date': '2026-10-19', 'source_url': 'https://ft.lk/d'}
    assert not same_event(c, d)
    # 'award' inside a surname is not a promotional award.
    board = {'headline': 'Sumathi Dharmawardena joins Vallibel Finance Board', 'publication_date': '2026-10-20',
             'body': 'Vallibel Finance PLC announced the appointment of Sumathi Dharmawardena to its Board of Directors with effect from this month, the company told the Colombo Stock Exchange in a filing.'}
    assert decide(board)['reason'] != 'LOW_MATERIALITY_PROMOTIONAL'
    assert financial_signal({'headline': 'Sri Lanka faces jobs test as educated youth bulge enters workforce', 'source_url': 'x'}) == ''


def test_backup_restores_usable_sent_history(tmp_path):
    sys.path.insert(0, str(ROOT / 'scripts'))
    from news_release_gate import backup_records, verify_backup
    from bank_intel.publication.history_import import import_distributed
    root = tmp_path / 'proj'
    (root / 'config').mkdir(parents=True); (root / 'config/x.yaml').write_text('a: 1')
    (root / 'data/state').mkdir(parents=True); (root / 'data/state/s.json').write_text('{"articles": {}}')
    (root / 'requirements.lock.txt').write_text('x==1')
    import_distributed(ROOT / 'tests/fixtures/publication_2026_10_26_27/prior_reference.html', root / 'data/processed/reports', '2026-10-26', 'test', True)
    archive = backup_records(root, tmp_path / 'backups')
    result = verify_backup(archive)
    assert result['sent_editions'] == ['2026-10-26'] and result['sent_story_sources'] > 0
    # A damaged backup must be refused, not restored.
    import zipfile
    damaged = tmp_path / 'damaged.zip'
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(damaged, 'w') as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            dst.writestr(item, b'{"articles": {"x": 1}}' if item.filename.endswith('s.json') else data)
    with pytest.raises(RuntimeError, match='BACKUP_HASH_MISMATCH'):
        verify_backup(damaged)


def test_readiness_counts_completed_days_on_one_build(tmp_path):
    sys.path.insert(0, str(ROOT / 'scripts'))
    from news_release_gate import readiness
    rows = [('2026-11-02', 'FAILED', 30, 'A'), ('2026-11-03', 'REVIEW_REQUIRED', 50, 'B'), ('2026-11-04', 'QA_PASS_NOT_DELIVERED', 60, 'B'),
            ('2026-11-05', 'ALREADY_SENT', 2, 'B'), ('2026-11-05', 'FAILED', 5, 'B')]
    history = tmp_path / 'h.jsonl'
    # The later failed rerun on 11-05 is that day's outcome: the streak stops there.
    history.write_text('\n'.join(json.dumps({'run_date': d, 'status': s, 'duration_minutes': m, 'freeze_sha256': f}) for d, s, m, f in rows))
    assert readiness(history)['consecutive_days'] == 0
    history.write_text('\n'.join(json.dumps({'run_date': d, 'status': s, 'duration_minutes': m, 'freeze_sha256': f}) for d, s, m, f in rows[:4]))
    result = readiness(history)
    assert result['consecutive_days'] == 3 and result['days'] == ['2026-11-03', '2026-11-04', '2026-11-05']
    rows2 = rows[:3] + [('2026-11-05', 'ALREADY_SENT', 2, 'C')]  # a new build restarts the count
    history.write_text('\n'.join(json.dumps({'run_date': d, 'status': s, 'duration_minutes': m, 'freeze_sha256': f}) for d, s, m, f in rows2))
    assert readiness(history)['consecutive_days'] == 1



@pytest.mark.semantic_model
def test_semantic_pass_joins_reworded_same_story_but_not_same_topic():
    """Real FinBERT: the 26 Oct CERT warning, reported in different words."""
    from bank_intel.publication.semantic import same_story
    day = '2026-10-26'
    a, b = json.loads((ROOT / 'tests/fixtures/cert_vap_poya_2026_10_26.json').read_text(encoding='utf-8'))
    c = {'headline': 'Sampath Bank and David Pieris Automobiles introduce new customer-centric mobility solution', 'publication_date': day,
         'source_url': 'https://ft.lk/c', 'body': 'Sampath Bank and David Pieris Automobiles have partnered to offer leasing facilities for motorcycles.'}
    d = {'headline': 'Seylan Bank partners with Fairway Latitude to make home ownership more accessible', 'publication_date': day,
         'source_url': 'https://ft.lk/d', 'body': 'Seylan Bank has partnered with Fairway Latitude to offer housing loans to apartment buyers.'}
    assert same_story(a, b)
    assert not same_story(c, d)


def test_recorded_failed_pages_do_not_block_collection_but_unknown_coverage_does():
    sys.path.insert(0, str(ROOT / 'scripts'))
    from run_collection import is_blocking_degraded
    ok = {'execution_status': 'SUCCESS', 'health_status': 'PARTIAL_ERROR', 'candidate_urls': 73, 'invalid_extraction': 2,
          'unresolved_fetch_urls': ['https://www.ft.lk/a', 'https://www.ft.lk/b'], 'fetch_failures': 0}
    assert not is_blocking_degraded(ok)
    assert is_blocking_degraded({**ok, 'listing': {'listing_pages_failed': 1}})
    assert is_blocking_degraded({**ok, 'unresolved_fetch_urls': [], 'fetch_failures': 3})
    assert is_blocking_degraded({**ok, 'unresolved_fetch_urls': [f'u{i}' for i in range(40)]})
    assert is_blocking_degraded({**ok, 'execution_status': 'FAILED'})


def test_sport_ceremony_and_attributed_budget_demands_are_not_auto_included():
    body = ' The statement was made in Colombo this week and details were reported with figures and comments from officials and analysts.' * 3
    def d(h): return decide({'headline': h, 'body': h + '.' + body, 'publication_date': '2026-10-30'})
    assert d('HNB retain Mercantile Netball A Division title for 11th successive year')['reason'] == 'LOW_MATERIALITY_PROMOTIONAL'
    assert d('People’s Bank rings opening bell at CSE')['reason'] == 'LOW_MATERIALITY_PROMOTIONAL'
    assert d('Provide relief, tax reductions, social safety nets in Budget 2027 – Vasu')['decision'] == 'REVIEW'
    assert d('Budget 2027: COPF approves Appropriation Bill for 2027')['decision'] == 'INCLUDE'


# The 29 October close as three publishers reported it, and the edited sent story.
CSE_FT = {'headline': 'CSE crosses 20,000-point mark, ends 1.5% on the up', 'publication_date': '2026-10-30', 'source_url': 'https://ft.lk/cse/42-799383',
          'body': 'The Colombo stock market ended up sharply yesterday, extending a rally into three days. The ASPI ended up 1.49% or 296.93 points at 20,267.15 and the active S&P SL20 gained 1.43%.'}
CSE_EN = {'headline': 'Sri Lanka stocks close up 1.5-pct, banks lead turnover', 'publication_date': '2026-10-29', 'source_url': 'https://economynext.com/close-288282',
          'body': 'ECONOMYNEXT – Sri Lanka’s Colombo Stock Exchange closed up on Thursday, CSE data showed, with the benchmark All Share Price Index moving up 1.49 percent. The ASPI was up 296.93 points at 20,267.15.'}
CSE_DM = {'headline': 'Indices close in green', 'publication_date': '2026-10-29', 'source_url': 'https://dailymirror.lk/indices/273-353688',
          'body': 'Indices closed in green as a result of price gains in counters such as Commercial Bank, Dialog Axiata and Hatton National Bank with the turnover crossing Rs. 2.2 Bn.'}
CSE_SENT = {'headline': 'CSE closes above 20,000 after a 1.49% ASPI rise', 'publication_date': '2026-10-30', 'source_url': 'https://ft.lk/cse/42-799383',
            'body': 'The Colombo Stock Exchange ended the 29 October session with the All Share Price Index at 20,267.15, an increase of 296.93 points or 1.49%.'}


def test_every_report_of_one_cse_close_is_the_same_session():
    from bank_intel.publication.identity import event_signature
    assert {event_signature(a) for a in (CSE_FT, CSE_EN, CSE_DM, CSE_SENT)} == {'CSE_DAILY_SESSION_2026-10-29'}
    assert same_event(CSE_FT, CSE_EN) and same_event(CSE_EN, CSE_DM)


def test_the_next_session_is_not_already_sent_when_the_sent_story_is_dated_a_day_later():
    # The sent story carries the 30 October edition date but describes the 29th.
    next_close = {'headline': 'Sri Lanka stocks close down 0.4-pct', 'publication_date': '2026-10-30', 'source_url': 'https://economynext.com/close-288400',
                  'body': 'ECONOMYNEXT – Sri Lanka’s Colombo Stock Exchange closed down on Friday, with the ASPI down 81.07 points at 20,186.08.'}
    assert not same_event(next_close, CSE_SENT)
    # Same day key without a stated session, but the closing levels disagree.
    undated = {**CSE_SENT, 'body': 'The ASPI finished at 20,267.15, up 296.93 points.'}
    assert not same_event(next_close, undated)


def test_opening_market_update_is_not_a_closing_session():
    from bank_intel.publication.identity import event_signature
    opening = {'headline': 'Sri Lanka stocks trend up 1.27-pct; DCSL gains', 'publication_date': '2026-10-29', 'source_url': 'https://economynext.com/open-288270',
               'body': 'ECONOMYNEXT – Sri Lanka’s Colombo Stock Exchange opened up on Thursday, CSE data showed, with the benchmark All Share Price Index moving up 1.27 percent. The ASPI was up 253.39 points at 20,223.61.'}
    assert event_signature(opening) == ''
    assert decide(opening)['decision'] == 'EXCLUDE' and decide(opening)['reason'] == 'INTRADAY_MARKET_UPDATE'


def test_routine_items_the_operator_removes_are_not_auto_included():
    body = ' The statement was made in Colombo this week and details were reported with figures and comments from officials and analysts.' * 3
    def d(h): return decide({'headline': h, 'body': h + '.' + body, 'publication_date': '2026-10-30'})
    assert d('Namal money laundering case put off to Feb., pending AG’s advice')['reason'] == 'COURT_PROCEDURAL_STEP'
    assert d('Namal Money Laundering Case Referred to February')['reason'] == 'COURT_PROCEDURAL_STEP'
    assert d('Mixed sentiment in secondary market amid subdued trading activity')['reason'] == 'DAILY_MARKET_COMMENTARY'
    assert d('ComBank hands over fully refurbished wards at De Soysa Hospital')['reason'] == 'LOW_MATERIALITY_PROMOTIONAL'
    assert d('HNB Finance takes financial inclusion drive to 26 towns across Sri Lanka')['reason'] == 'LOW_MATERIALITY_PROMOTIONAL'
    assert d('DFCC Bank strengthens regional development-finance links through exchange with Bhutan Development Bank')['decision'] == 'EXCLUDE'
    # Nearby stories that must stay: an outcome in court, an auction, a bank action.
    assert d('Court fines finance company over money laundering breach')['reason'] != 'COURT_PROCEDURAL_STEP'
    assert d('Rs. 100 b T-Bond auction fully subscribed')['decision'] == 'INCLUDE'
    assert d('ComBank launches dedicated loan scheme to revitalise Sri Lanka’s tea industry')['decision'] == 'INCLUDE'


def test_same_lender_purpose_stage_and_amount_is_one_transaction():
    sent = {'headline': 'Cabinet approves US$100 million ADB macro-recovery financing', 'publication_date': '2026-10-28', 'source_url': 'https://ft.lk/adb/44-799234',
            'body': 'The Cabinet has approved a US$100 million loan from the Asian Development Bank for the Macro-Recovery Sector Development Programme and water security work.'}
    later = {'headline': 'Cabinet approves US$ 100mn ADB loan for economic recovery programme', 'publication_date': '2026-10-28', 'source_url': 'https://dailymirror.lk/adb/273-1',
             'body': 'The Cabinet of Ministers has approved entering into a loan agreement with the Asian Development Bank (ADB) to obtain US$100 million to implement the Macro-Recovery Sector Development Programme.'}
    assert same_event(later, sent)


def test_mutual_evaluation_reported_the_next_day_is_already_sent():
    sent = {'headline': 'Sri Lanka’s AML/CFT framework under international scrutiny as APG evaluation begins', 'publication_date': '2026-10-28', 'source_url': 'https://themorning.lk/a',
            'summary': 'The Asia/Pacific Group on Money Laundering began the mutual evaluation of Sri Lanka’s AML/CFT framework in Colombo.'}
    later = {'headline': 'SL’s Third APG Mutual Evaluation Meeting commences', 'publication_date': '2026-10-29', 'source_url': 'https://themorning.lk/b',
             'body': 'The opening session of Sri Lanka’s Third Mutual Evaluation Meeting, conducted by the Asia/Pacific Group on Money Laundering (APG), was held in Colombo.'}
    assert prior_matches(later, [sent])[0]


def test_licensed_finance_companies_are_recognised():
    from bank_intel.publication.decisions import nonbank_institution_in
    assert nonbank_institution_in('AFC to raise Rs. 1 b via Sri Lanka’s first Orange Bond')
    assert nonbank_institution_in('Vallibel Finance posts higher profit')


def test_secondary_market_yield_move_is_not_daily_commentary():
    body = ' Dealers reported the move in Colombo this week with figures and comments from officials and analysts.' * 3
    assert decide({'headline': 'Secondary Bond market yields increase', 'body': body, 'publication_date': '2026-10-28'})['reason'] != 'DAILY_MARKET_COMMENTARY'


def test_short_publisher_posts_do_not_block_a_source_that_was_fully_reached():
    sys.path.insert(0, str(ROOT / 'scripts'))
    from run_collection import is_blocking_degraded
    urls = [f'https://ceylontoday.lk/2026/10/30/post-{i}' for i in range(4)]
    short = {'execution_status': 'SUCCESS', 'health_status': 'PARTIAL_ERROR', 'candidate_urls': 6, 'invalid_extraction': 4, 'fetch_failures': 0,
             'unresolved_fetch_urls': urls,
             'extraction_failures': [{'url': u, 'kind': 'INVALID_EXTRACTION', 'errors': ['ARTICLE_TOO_SHORT:49']} for u in urls]}
    assert not is_blocking_degraded(short)
    # Pages that could not be read at all still block at the same count.
    assert is_blocking_degraded({**short, 'extraction_failures': [{'url': u, 'kind': 'FETCH', 'errors': ['HTTP 500']} for u in urls]})


def test_island_listing_refused_with_403_is_read_from_the_official_feed(monkeypatch):
    from bank_intel.collectors.sources._public_business_base import BusinessSourceAdapter
    from bank_intel.collectors.sources.island import IslandBusinessAdapter
    collector = IslandBusinessAdapter.__new__(IslandBusinessAdapter)
    url = 'https://island.lk/category/business/page/2/'
    def refused(self, requested):
        raise RuntimeError(f'Browser fallback returned unusable page (status=403): {requested}')
    monkeypatch.setattr(BusinessSourceAdapter, 'fetch', refused)
    monkeypatch.setattr(IslandBusinessAdapter, '_read_rss_page', lambda self, section, page: [{'url': 'https://island.lk/story', 'headline': (section + str(page))}])
    result = collector.fetch(url)
    assert result.method == 'rss-listing' and 'business2' in result.html


def test_one_article_at_two_addresses_keeps_a_file_for_each_record():
    sys.path.insert(0, str(ROOT / 'scripts'))
    from run_intelligence_ml import unique_article_id
    used = set()
    first = unique_article_id('9a87a1c3c8126751', 'daily_mirror', Path('3bb86775a752f22d3ae2.json'), used)
    second = unique_article_id('9a87a1c3c8126751', 'daily_mirror', Path('49278e0c9e41f9fdca42.json'), used)
    other_source = unique_article_id('9a87a1c3c8126751', 'the_island', Path('aaaa.json'), used)
    assert first == other_source == '9a87a1c3c8126751' and second != first


def test_a_rewritten_sent_headline_still_stops_the_story_repeating():
    sent = {'headline': 'Commercial Bank introduces dedicated credit scheme for tea smallholders', 'publication_date': '2026-10-30', 'source_url': 'https://ft.lk/combank/42-799387',
            'summary': 'Commercial Bank of Ceylon has launched a dedicated loan scheme developed with the Sri Lanka Tea Factory Owners Association and participating tea factories to revitalise the tea industry.'}
    later = {'headline': 'ComBank launches dedicated loan scheme to revitalise Sri Lanka’s tea industry', 'publication_date': '2026-11-01', 'source_url': 'https://island.lk/combank-tea',
             'body': 'Commercial Bank of Ceylon has launched a dedicated loan scheme for tea smallholders with the Tea Factory Owners Association.'}
    other = {'headline': 'ComBank launches green home loan for first-time buyers', 'publication_date': '2026-11-01', 'source_url': 'https://island.lk/combank-home',
             'body': 'Commercial Bank of Ceylon has launched a home loan for first-time buyers of energy-efficient apartments.'}
    assert prior_matches(later, [sent])[0]
    assert not prior_matches(other, [sent])[0]


def test_monday_edition_shows_one_release_as_one_story():
    from bank_intel.publication.reconcile import group
    friday = {'headline': 'CCPI inflation rises to 8.3% in Oct.', 'publication_date': '2026-10-31', 'source_url': 'https://ft.lk/ccpi/44-1',
              'body': 'Colombo Consumer Price Index inflation rose to 8.3% in October from 8.0% in September, the Department of Census and Statistics said.'}
    monday = {'headline': 'Inflation rises to 8.3% in October', 'publication_date': '2026-11-02', 'source_url': 'https://themorning.lk/ccpi',
              'body': 'The overall rate of inflation, as measured by the Colombo Consumer Price Index, increased to 8.3% in October 2026, up from 8.0% in September 2026.'}
    entries = [{'article': a, 'assessment': {'event_signature': '', 'reason': ''}} for a in (friday, monday)]
    assert len(group(entries)) == 1


def test_more_routine_items_stay_out_of_the_edition():
    body = ' The statement was made in Colombo this week and details were reported with figures and comments from officials and analysts.' * 3
    def d(h): return decide({'headline': h, 'body': h + '.' + body, 'publication_date': '2026-11-02'})
    assert d('BOC marks World Thrift Day, promotes regular savings habit')['reason'] == 'LOW_MATERIALITY_PROMOTIONAL'
    assert d('Union Assurance rewards top performers of Seylan Bank with tour to Malaysia')['reason'] == 'LOW_MATERIALITY_PROMOTIONAL'
    assert d('Evidence in Yoshitha money laundering case to resume Nov. 12')['reason'] == 'COURT_PROCEDURAL_STEP'
    assert d('Moderate market activity amidst fully subscribed T-bond auction')['reason'] == 'DAILY_MARKET_COMMENTARY'


def test_the_sl20_figure_is_not_read_as_the_aspi_fall():
    from bank_intel.publication.identity import reported_aspi_fall
    ft = 'Yesterday, the ASPI ended 0.62% or 124.82 points at 20,142.33 and the S&P SL20 fell 0.48% or 27.52 points to 5,746.40.'
    en = 'The ASPI was down 124.82 points at 20,142.33, while the more liquid S&P SL20 was down 0.40 percent, or 23.28 points.'
    island = 'The All Share Price Index went down by 124.08 points, while the S and P SL20 declined by 23.28 points.'
    assert reported_aspi_fall({'body': ft}) == ''
    assert reported_aspi_fall({'body': en}) == '124.82'
    assert reported_aspi_fall({'body': island}) == '124.08'


# Corrections from the operator's final 2 November edition.
BODY = ' Sri Lanka officials reported a US$ 128 million figure in Colombo this week with comments from analysts and data from the central bank.' * 3


def test_an_undated_old_sent_record_cannot_block_a_story_weeks_later():
    old = {'headline': 'Fitch Ratings recalibrates national rating scale', 'publication_date': '', 'edition_date': '2026-10-22',
           'source_url': 'https://ft.lk/fitch/44-798000', 'summary': 'Fitch Ratings recalibrated its national rating scale for Sri Lanka.'}
    new = {'headline': 'Updated scale: Fitch takes action on 8 Sri Lankan Banks\' national ratings', 'publication_date': '2026-11-02',
           'source_url': 'https://themorning.lk/articles/fitch', 'body': 'Fitch Ratings announced bank-specific actions on the national ratings of eight banks after recalibrating the scale.'}
    assert not prior_matches(new, [old])[0]


def test_one_fitch_ratings_review_is_one_story_and_a_later_review_is_new():
    from bank_intel.publication.identity import event_signature
    def row(h, day='2026-11-02', u='a'):
        return {'headline': h, 'publication_date': day, 'source_url': f'https://example.lk/{u}',
                'body': 'Fitch Ratings changed the National Long-Term Rating to AA+(lka) with a Stable outlook after recalibrating the scale.'}
    boc = row('Fitch upgrades BOC to AA+(lka) with Stable outlook', u='boc')
    peoples = row('People’s Bank secures two-notch Fitch rating upgrade', u='peoples')
    eight = row('Updated scale: Fitch takes action on 8 Sri Lankan Banks national ratings', u='eight')
    assert event_signature(boc) and same_event(boc, peoples) and same_event(peoples, eight)
    assert same_event(row('Fitch upgrades Commercial Bank to AA(lka)', '2026-11-03', 'cb'), eight)
    assert not same_event(row('Fitch downgrades Union Bank to BB+(lka)', '2026-11-20', 'ub'), eight)


def test_external_and_fiscal_stories_are_never_silently_dropped():
    def d(h): return decide({'headline': h, 'body': h + '.' + BODY, 'publication_date': '2026-11-02'})
    current = d('Current account dips back into deficit in Sept.')
    assert current['decision'] == 'INCLUDE' and current['section'] == 'MONETARY_MARKETS_EXTERNAL'
    assert d('Rupee down 6.8% YTD by end-October; reserves at $ 6.9 b')['decision'] == 'INCLUDE'
    assert d('Foreign investors dump Sri Lanka rupee bonds for second straight week')['decision'] == 'INCLUDE'
    for headline in ('Sri Lanka likely to see record fuel bill in 2026 despite declining monthly spending',
                     'Logistics industry warns of $50m annual forex leakage over Colombo Port charges',
                     'Vehicle import bill falls 34% in Sept.',
                     'Missing records stall closure of 34 State firms'):
        assert d(headline)['decision'] == 'REVIEW', headline


def test_model_keep_on_external_subjects_reaches_an_editor():
    from bank_intel.publication.model_policy import financial_signal
    for headline in ('Current account dips back into deficit', 'Record fuel bill expected', 'State firms face closure', 'Pressure on foreign exchange'):
        assert financial_signal({'headline': headline, 'source_url': 'https://ft.lk/x/44-1'}) == '', headline


def test_daily_ft_slug_variants_are_one_article():
    from bank_intel.publication.identity import publisher_article_id
    a = 'https://www.ft.lk/news/Govt--to-finalise-disaster-risk-financing-strategy-by-mid-2027/56-799509'
    b = 'https://ft.lk/news/Govt-to-finalise-disaster-risk-financing-strategy-by-mid-2027/56-799509'
    assert publisher_article_id(a) == publisher_article_id(b) != ''


def test_the_days_rupee_close_supersedes_its_quote():
    from bank_intel.publication.workflow import supersede_rupee_quotes
    def entry(h, day='2026-10-30', decision='INCLUDE', reason='FINANCIAL_MARKET_HEADLINE'):
        return {'article': {'headline': h, 'publication_date': day}, 'assessment': {'decision': decision, 'reason': reason}}
    close = entry('Sri Lanka rupee closes at 332.50/62 to US dollar spot', decision='REVIEW')
    quote = entry('Sri Lanka rupee at 332.60/70 to US dollar spot, bond yields steady')
    monday = entry('Sri Lanka rupee at 332.55/65 to US dollar spot', day='2026-11-02')
    signed = entry('Sri Lanka rupee at 332.60/70 to US dollar', reason='SIGNED_EDITORIAL_kept')
    assert supersede_rupee_quotes([close, quote, monday, signed]) == 1
    assert quote['assessment']['reason'] == 'RUPEE_QUOTE_SUPERSEDED_BY_CLOSE' and monday['assessment']['decision'] == 'INCLUDE'
    assert signed['assessment']['decision'] == 'INCLUDE'


def test_the_edited_edition_is_registered_with_an_earliest_send_time(tmp_path):
    sys.path.insert(0, str(ROOT / 'scripts'))
    from datetime import date
    import shutil
    from news_release_gate import register_sent
    sent = tmp_path / 'edited.html'
    shutil.copyfile(ROOT / 'tests/fixtures/publication_2026_10_26_27/prior_reference.html', sent)
    root = tmp_path / 'reports'
    with pytest.raises(ValueError):
        register_sent(date(2026, 10, 26), sent, root, 'thanuka', False)
    record = register_sent(date(2026, 10, 26), sent, root, 'thanuka', True)
    entry = json.loads(record.read_text(encoding='utf-8'))
    assert entry['state'] == 'DELIVERED_CONFIRMED_BY_OPERATOR' and entry['sent_at'] and 'Earliest bound' in entry['sent_time_evidence']
    assert json.loads((record.parent / 'sent_events.json').read_text(encoding='utf-8'))['sent_at'] == entry['sent_at']
    # Registering the same file again changes nothing.
    assert register_sent(date(2026, 10, 26), sent, root, 'thanuka', True) == record


def test_an_unregistered_previous_edition_is_reported(tmp_path):
    sys.path.insert(0, str(ROOT / 'scripts'))
    from datetime import date
    from news_release_gate import previous_edition_gap
    folder = tmp_path / '2026-11-02' / 'window_1d'
    (folder / 'integrated_edition').mkdir(parents=True)
    assert previous_edition_gap(date(2026, 11, 3), tmp_path) == '2026-11-02'
    (folder / 'SENT_EDITION.json').write_text('{}', encoding='utf-8')
    assert previous_edition_gap(date(2026, 11, 3), tmp_path) == ''
    # Monday looks back to Friday, and a day with no edition is not a gap.
    assert previous_edition_gap(date(2026, 11, 2), tmp_path) == ''


def test_a_close_that_does_not_name_the_market_is_still_that_session():
    from bank_intel.publication.identity import event_signature
    report = {'headline': 'Week ends in red', 'publication_date': '2026-10-31', 'source_url': 'https://dailymirror.lk/x/273-353838',
              'body': 'The ASPI closed in red as a result of price losses yesterday in counters such as Hatton National Bank with the turnover crossing Rs. 958 Mn.'}
    assert event_signature(report) == 'CSE_DAILY_SESSION_2026-10-30'


def test_state_institution_closures_reach_an_editor():
    body = ' Sri Lanka officials reported a Rs. 40 billion figure in Colombo this week.' * 4
    d = decide({'headline': 'Govt to close 34 state institutions to cut public expenditure – President', 'body': body, 'publication_date': '2026-10-26'})
    assert d['decision'] == 'REVIEW'


def test_one_months_current_account_is_one_release_across_reports_and_days():
    from bank_intel.publication.identity import event_signature
    ft = {'headline': 'Current account dips back into deficit in Sept.', 'publication_date': '2026-11-02', 'source_url': 'https://ft.lk/ca/44-799512',
          'body': 'Sri Lanka’s current account recorded a deficit of US$ 128 million in September 2026 after an August surplus.'}
    morning = {'headline': 'External: Current account records $ 128 m deficit in Sept.', 'publication_date': '2026-11-02', 'source_url': 'https://themorning.lk/ca',
               'body': 'The current account recorded a deficit of US$ 128 million in September 2026, Central Bank data showed.'}
    next_day = {'headline': 'Sri Lanka current account returns to deficit in Sep 2026', 'publication_date': '2026-11-01', 'source_url': 'https://economynext.com/ca',
                'body': 'ECONOMYNEXT – Sri Lanka’s current account returned to deficit in September 2026.'}
    october = {'headline': 'Current account back in surplus in October', 'publication_date': '2026-12-01', 'source_url': 'https://ft.lk/ca/44-800000',
               'body': 'Sri Lanka’s current account recorded a surplus in October 2026.'}
    assert event_signature(ft) == event_signature(morning) == event_signature(next_day) == 'LK_CURRENT_ACCOUNT_2026_sep'
    assert same_event(ft, next_day) and not same_event(ft, october)


def _summary(folder, name, steps):
    (folder / name).write_text(json.dumps({'steps': steps}), encoding='utf-8')


def test_a_run_starts_correctly_on_any_day_and_after_any_result(tmp_path):
    from datetime import datetime
    from bank_intel.common.run_control import run_decision
    monday = datetime(2026, 11, 2, 9, 15)
    assert run_decision(datetime(2026, 10, 31, 9, 0), {}, tmp_path) == 'SKIP_WEEKEND'
    assert run_decision(datetime(2026, 11, 2, 7, 50), {}, tmp_path) == 'SKIP_EARLY'
    assert run_decision(datetime(2026, 11, 2, 8, 0), {}, tmp_path) == 'RUN'
    assert run_decision(monday, {}, tmp_path) == 'RUN'
    assert run_decision(monday, {'run_date': '2026-11-02', 'status': 'REVIEW_REQUIRED'}, tmp_path) == 'SKIP_DONE'
    # Yesterday's result says nothing about today.
    assert run_decision(monday, {'run_date': '2026-10-30', 'status': 'SUCCESS'}, tmp_path) == 'RUN'
    _summary(tmp_path, 'news_pipeline_20261102_083000.json', [{'name': '1/6 COLLECTION', 'status': 'FAILED'}])
    assert run_decision(monday, {'run_date': '2026-11-02', 'status': 'FAILED'}, tmp_path) == 'RUN'
    _summary(tmp_path, 'news_pipeline_20261102_090000.json', [{'name': '1/6 COLLECTION', 'status': 'SUCCESS'}, {'name': '2/6 STAGE 1', 'status': 'FAILED'}])
    assert run_decision(monday, {'run_date': '2026-11-02', 'status': 'FAILED'}, tmp_path) == 'RUN_SKIP_COLLECTION'


def test_a_blocked_or_quiet_source_is_flagged(tmp_path):
    from bank_intel.common.run_control import source_health
    def status(key, **fields):
        (tmp_path / f'{key}.json').write_text(json.dumps({'source_name': key, 'completed_at': '2026-11-02T09:00:00', **fields}), encoding='utf-8')
    for number, day in enumerate(('2026-10-28', '2026-10-29', '2026-10-30')):
        folder = tmp_path / 'history' / day
        folder.mkdir(parents=True)
        for key in ('quiet', 'normal', 'small'):
            (folder / f'{key}_{number}.json').write_text(json.dumps({'candidate_urls': 3 if key == 'small' else 60}), encoding='utf-8')
    status('quiet', candidate_urls=8)
    status('normal', candidate_urls=55)
    status('small', candidate_urls=1)
    status('empty', candidate_urls=0)
    status('refused', candidate_urls=35, fetch_failures=4, unresolved_fetch_urls=[])
    (tmp_path / 'stale.json').write_text(json.dumps({'source_name': 'stale', 'completed_at': '2026-10-30T09:00:00', 'candidate_urls': 40}), encoding='utf-8')
    report = source_health(tmp_path, '2026-11-02')
    assert sorted(report['problems']) == ['empty: NO_ARTICLES_FOUND', 'quiet: FAR_BELOW_USUAL', 'refused: PAGES_REFUSED', 'stale: NOT_COLLECTED_TODAY']


SOURCE = 'Softlogic Finance recorded a net loss of Rs. 100.6 million for the September 2026 quarter, compared with a Rs. 84.3 million profit a year earlier.'


def _draft(**changes):
    return {'headline': 'Softlogic Finance reports Rs. 100.6 million quarterly loss', 'what_happened': SOURCE,
            'why_it_matters': 'A change from profit to loss warrants attention in finance-company monitoring.', **changes}


def test_a_draft_is_used_only_when_its_facts_are_in_the_sources():
    from bank_intel.publication.writing import verify_draft
    assert verify_draft(_draft(), [SOURCE]) == []
    assert any(i.startswith('FIGURE_NOT_IN_SOURCE') for i in verify_draft(_draft(headline='Softlogic Finance reports Rs. 180.6 million quarterly loss'), [SOURCE]))
    assert any(i.startswith('NAME_NOT_IN_SOURCE') for i in verify_draft(_draft(why_it_matters='The Governor, Ahmed Zubair, commented.'), [SOURCE]))
    assert any(i.startswith('NUMBER_NOT_IN_SOURCE') for i in verify_draft(_draft(what_happened=SOURCE + ' It has 212 branches.'), [SOURCE]))
    assert any(i.startswith('CLAIM_NOT_IN_SOURCE') for i in verify_draft(_draft(headline='Softlogic Finance posts record loss'), [SOURCE]))
    assert verify_draft({'headline': '', 'what_happened': SOURCE, 'why_it_matters': 'x'}, [SOURCE]) == ['MISSING_FIELD']
    # A unit written differently is the same figure.
    assert verify_draft(_draft(headline='Softlogic Finance reports Rs. 0.1006 billion quarterly loss'), [SOURCE]) == []


def _event(extra=''):
    return {'members': [{'article': {'headline': 'Softlogic Finance Sept quarter swings to loss', 'source_name': 'EconomyNext',
                                     'publication_date': '2026-10-30', 'body': SOURCE + extra}}]}


def test_wording_falls_back_to_the_extract_and_never_stops_a_run(tmp_path):
    from bank_intel.publication.writing import story_text, write_events
    calls = []

    def good(sources, problems):
        calls.append(problems)
        return _draft()
    event = _event()
    assert write_events([event], draft=good, cache_root=tmp_path)['written'] == 1
    assert story_text(event, 'old', 'old', 'old')[0] == _draft()['headline']
    # The same sources are not paid for twice.
    assert write_events([_event()], draft=good, cache_root=tmp_path)['written'] == 1 and len(calls) == 1
    # A bad draft gets one correction, then the extract stands.
    seen = []

    def bad(sources, problems):
        seen.append(problems)
        return _draft(headline='Softlogic Finance reports Rs. 999 million loss')
    event = _event(' More.')
    outcome = write_events([event], draft=bad, cache_root=tmp_path)
    assert len(seen) == 2 and seen[1] and outcome['kept_extracted'] == 1 and 'written' not in event
    assert story_text(event, 'Old', 'kept', 'kept') == ('Old', 'kept', 'kept')

    # An outage leaves every story as it was.
    def down(sources, problems):
        raise ConnectionError('no network')
    outcome = write_events([_event(' Again.')], draft=down, cache_root=tmp_path)
    assert outcome['status'] == 'UNAVAILABLE' and outcome['kept_extracted'] == 1


def test_no_credentials_means_todays_wording(monkeypatch, tmp_path):
    from bank_intel.publication.writing import write_events
    monkeypatch.delenv('NEWS_WRITING', raising=False)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('ANTHROPIC_AUTH_TOKEN', raising=False)
    assert write_events([_event()], cache_root=tmp_path)['status'] == 'NOT_CONFIGURED'


def test_the_ranker_advises_and_never_changes_a_decision(tmp_path):
    import joblib
    from bank_intel.publication.identity import canonical
    from bank_intel.publication.ranking import advise, candidate, fit, suggestion
    def row(h, section='MONETARY_MARKETS_EXTERNAL'):
        return candidate({'headline': h, 'body': h, 'source_name': 'Daily FT', 'source_url': 'https://ft.lk/a'},
                         {'reason': 'X', 'section': section, 'decision': 'REVIEW'}, None)
    rows = [row(h) for h in ('Inflation rises to 8.3%', 'Inflation falls in September', 'Rupee reserves rise', 'Reserves and rupee stable',
                             'Bank marks World Thrift Day', 'Bank celebrates Thrift Day', 'Staff tour to Malaysia', 'Staff tour for top sellers')]
    model = tmp_path / 'ranker.joblib'
    joblib.dump(fit(rows, [1, 1, 1, 1, 0, 0, 0, 0]), model)
    work = [{'article': {'headline': 'Inflation rises again', 'body': 'Inflation rises', 'source_name': 'Daily FT', 'source_url': 'https://ft.lk/a'},
             'assessment': {'decision': 'REVIEW', 'reason': 'X', 'section': 'MONETARY_MARKETS_EXTERNAL'}},
            {'article': {'headline': 'Excluded story', 'source_name': 'Daily FT', 'source_url': 'https://ft.lk/b'},
             'assessment': {'decision': 'EXCLUDE', 'reason': 'OUT_OF_SCOPE', 'section': ''}}]
    assert advise(work, {}, canonical, model) == 'ADVISED'
    assert work[0]['assessment']['decision'] == 'REVIEW' and 0 <= work[0]['assessment']['ranker_score'] <= 1
    assert 'ranker_score' not in work[1]['assessment'] and work[1]['assessment']['decision'] == 'EXCLUDE'
    assert (suggestion(0.9), suggestion(0.3), suggestion(0.1)) == ('LIKELY_KEEP', 'UNSURE', 'LIKELY_DROP')
    # A missing or damaged model costs the advice, never the edition.
    assert advise(work, {}, canonical, tmp_path / 'absent.joblib') == 'NOT_TRAINED'
    (tmp_path / 'bad.joblib').write_text('not a model')
    assert advise(work, {}, canonical, tmp_path / 'bad.joblib').startswith('UNAVAILABLE')


def test_a_forced_run_ignores_the_clock_but_reuses_a_finished_collection(tmp_path):
    from datetime import datetime
    from bank_intel.common.run_control import run_decision
    saturday = datetime(2026, 11, 7, 7, 0)
    assert run_decision(saturday, {'run_date': '2026-11-07', 'status': 'SUCCESS'}, tmp_path, force=True) == 'RUN'
    _summary(tmp_path, 'news_pipeline_20261107_070000.json', [{'name': '1/6 COLLECTION', 'status': 'SUCCESS'}])
    assert run_decision(saturday, {}, tmp_path, force=True) == 'RUN_SKIP_COLLECTION'


def test_a_summary_starts_at_the_real_lead():
    from bank_intel.publication.evidence import clean_lead
    cases = {
        '- Stays above CBSL’s 5% ±2% target band for fourth straight month - Core accelerates to 5.6% - CBSL sees high single-digit inflation through Q1 2027 Sri Lanka’s Colombo Consumer Price Index (CCPI)-based headline inflation rose to 8.3% in October, staying above the Central Bank of Sri Lanka’s (CBSL) 5% ±2% target band.': 'Sri Lanka’s Colombo Consumer Price Index',
        '01 Nov 2026 | By Priyanwada Abeyrathna Misses target by Rs. 13.5 b Over-borrowing 91-day bills lifts yields to 9.34% The Government raised Rs. 86.48 billion at the Treasury bill auction, falling short of the Rs. 100 billion on offer.': 'The Government raised',
        '01 Nov 2026 | By Priyanwada Abeyrathna Catalyst Board an alternative if required Listing decision rests with Govt. The Colombo Stock Exchange (CSE) has said that an Initial Public Offering would be the optimal route.': 'The Colombo Stock Exchange',
        'ECONOMYNEXT – Sri Lanka’s rupee closed at 332.50/62 to the US dollar spot on Friday, dealers said.': 'Sri Lanka’s rupee closed',
        '- $ 128 m deficit follows Aug. surplus but narrows from $ 194 m a year earlier The external current account slipped back into a deficit of $ 128 million in September, the central bank said.': 'The external current account',
    }
    for raw, opening in cases.items():
        assert clean_lead(raw, '').startswith(opening), clean_lead(raw, '')[:80]
    # Ordinary opening sentences are left alone.
    for raw in ('Commercial Bank has partnered with The Tea Board to launch a scheme for tea growers across the island this year.',
                'The coming national budget will feature major proposals aimed at boosting Sri Lanka’s capital market this year.',
                'Nearly Rs 1.6 t is tied up in appeals and other disputes Sri Lanka officials said in a report released on Friday.',
                'Pan Asia Banking Corporation PLC reported a strong set of results. The Government welcomed the outcome.'):
        assert clean_lead(raw, '') == raw


def test_why_it_matters_is_specific_and_marks_what_is_not_yet_fact():
    from bank_intel.publication.decisions import why_it_matters
    markets = {'section': 'MONETARY_MARKETS_EXTERNAL'}
    assert 'foreign-exchange availability' in why_it_matters({'headline': 'Current account dips back into deficit in Sept.'}, markets)
    assert 'repayment capacity' in why_it_matters({'headline': 'CCPI inflation rises to 8.3% in Oct.'}, markets)
    assert 'not a confirmed outcome' in why_it_matters({'headline': 'Sri Lanka likely to see record fuel bill in 2026'}, markets)
    assert 'not a confirmed outcome' not in why_it_matters({'headline': 'Govt. raises Rs. 86.5 b at T-bill auction'}, markets)
    assert 'Quarterly bank performance' in why_it_matters({'headline': 'Pan Asia Bank delivers Rs 3.59 Bn 9-month PAT'}, {'section': 'KEY_BANKING_SUPERVISORY'})


def test_a_headline_loses_desk_labels_and_tabloid_verbs_but_keeps_its_facts():
    from bank_intel.publication.writing import plain_headline
    assert plain_headline('External: Current account records $ 128 m deficit in Sept.') == 'Current account records $ 128 m deficit in Sept.'
    assert plain_headline('Foreign investors dump Sri Lanka rupee bonds') == 'Foreign investors sell Sri Lanka rupee bonds'
    assert plain_headline("IMF Finds: SL's tax arrears approach Rs 2.1t") == "IMF finds SL's tax arrears approach Rs 2.1t"
    assert plain_headline('Budget 2027: COPF approves Appropriation Bill') == 'Budget 2027: COPF approves Appropriation Bill'


def test_a_current_account_report_quoting_last_year_keeps_this_years_period():
    from bank_intel.publication.identity import event_signature
    report = {'headline': 'Current account dips back into deficit in Sept.', 'publication_date': '2026-11-02',
              'body': 'The deficit was lower than in September 2025, the Central Bank said.'}
    assert event_signature(report) == 'LK_CURRENT_ACCOUNT_2026_sep'
    assert event_signature({**report, 'headline': 'Current account in surplus in December', 'publication_date': '2027-01-30'}) == 'LK_CURRENT_ACCOUNT_2026_dec'


def test_a_story_sent_under_an_edited_headline_does_not_return_from_other_papers():
    sent = {'headline': 'NDB and IFC announce advisory programme and women-led SME support', 'publication_date': '2026-11-02', 'edition_date': '2026-11-02',
            'source_url': 'https://www.ft.lk/financial-services/NDB-and-IFC-expand-partnership-to-boost-institutional-capacity-and-support-women-entrepreneurs/42-799496',
            'summary': 'NDB Bank and the International Finance Corporation announced two initiatives: IFC advisory assistance and a women-led SME skills programme.'}
    later = {'headline': 'NDB and IFC expand partnership through new initiatives', 'publication_date': '2026-11-03', 'source_url': 'https://dailymirror.lk/x/273-353900',
             'body': 'NDB Bank is building on its longstanding partnership with the International Finance Corporation (IFC) through two new initiatives.'}
    already, updates = prior_matches(later, [sent])
    # The same organisations and closely matching wording a day later: the story already sent.
    assert already and not updates
    from bank_intel.publication.identity import url_headline
    assert url_headline(sent['source_url']).startswith('NDB and IFC expand partnership')
    assert url_headline('https://www.themorning.lk/articles/ugtwJXH4zd8HZGZ3L5VU') == ''


def test_one_results_release_is_one_story():
    quarter = {'headline': 'Sri Lanka Seylan Bank Sept quarter net profit rises 6-pct to Rs3.1bn', 'publication_date': '2026-11-02', 'source_url': 'https://economynext.com/seylan',
               'body': 'Seylan Bank group recorded a net profit of 3.08 billion rupees for the quarter ended September 30.'}
    nine = {'headline': 'Seylan reports 9M PAT of Rs. 9.1 b', 'publication_date': '2026-11-03', 'source_url': 'https://ft.lk/seylan/42-799600',
            'body': 'Seylan Bank reported a profit after tax of Rs. 9.1 billion for the nine months.'}
    assert same_event(quarter, nine)
    other = {**nine, 'headline': 'Sampath Bank reports 9M PAT of Rs. 20 b', 'source_url': 'https://ft.lk/sampath/42-1', 'body': 'Sampath Bank reported a profit.'}
    assert not same_event(quarter, other)


def test_court_dates_stay_out():
    body = ' The statement was made in Colombo this week and details were reported with figures and comments from officials and analysts.' * 3
    def d(h): return decide({'headline': h, 'body': h + '.' + body, 'publication_date': '2026-11-03'})
    assert d('Bond case fixed for pre-trial conference on 28 January')['reason'] == 'COURT_PROCEDURAL_STEP'
    assert d('Permanent High Court Trial-at-Bar Fixes Central Bank Bond Case for January 28')['reason'] == 'COURT_PROCEDURAL_STEP'


def test_a_later_outcome_of_a_sent_issue_is_a_development_not_a_repeat():
    sent = {'headline': 'Fitch rates Sampath Bank’s proposed Basel III subordinated debentures ‘A(lka)’', 'publication_date': '2026-10-16',
            'edition_date': '2026-10-16', 'source_url': 'https://www.ft.lk/x/CSE-green-lights-Sampath-Bank-s-Rs-10-b-debenture-issue/42-798700',
            'summary': 'Sampath Bank will issue Rs. 10 billion of debentures, with a further amount in the event of an oversubscription.'}
    later = {'headline': 'Rs. 10 b Sampath Bank debenture issue oversubscribed', 'publication_date': '2026-10-21', 'source_url': 'https://www.ft.lk/x/42-798900',
             'body': 'Sampath Bank PLC’s Rs. 10 billion debenture issue was oversubscribed yesterday, the bank said.'}
    already, updates = prior_matches(later, [sent])
    assert not already and updates


def test_a_reworded_report_of_a_sent_story_goes_to_an_editor():
    sent = {'headline': 'Capital Alliance agrees to buy 51% of Asia Frontier Capital for approximately US$2.5 million', 'publication_date': '2026-11-03',
            'edition_date': '2026-11-03', 'source_url': 'https://ft.lk/cal/42-1',
            'summary': 'Capital Alliance Holdings agreed to acquire 51% of Hong Kong based Asia Frontier Capital, which manages US$125 million.'}
    later = {'headline': 'CAL to acquire 51% of Hong Kong fund manager AFC', 'publication_date': '2026-11-04', 'source_url': 'https://dailymirror.lk/cal/273-2',
             'body': 'Capital Alliance Holdings PLC has signed an agreement to acquire a controlling 51 percent stake in Hong Kong-based Asia Frontier Capital, with US$125 million under management.'}
    already, updates = prior_matches(later, [sent])
    assert updates and not already
    # A new story that only shares a round figure is left alone.
    other = {'headline': 'Sampath Bank allots 80% of Rs. 10 b debenture issue', 'publication_date': '2026-11-04', 'source_url': 'https://ft.lk/x/42-3',
             'body': 'Sampath Bank allotted 80% of its Rs. 10 billion debenture issue to preferential investors.'}
    sent_bond = {'headline': 'BOC to raise up to Rs. 10 b through debentures', 'publication_date': '2026-11-03', 'edition_date': '2026-11-03',
                 'source_url': 'https://ft.lk/y/42-4', 'summary': 'Bank of Ceylon will raise Rs. 10 billion through a debenture issue.'}
    assert prior_matches(other, [sent_bond]) == ([], [])


def test_a_healthy_source_with_nothing_new_is_not_an_alarm(tmp_path):
    from bank_intel.common.run_control import source_health
    (tmp_path / 'quiet.json').write_text(json.dumps({'source_name': 'quiet', 'completed_at': '2026-11-04T08:32:20', 'candidate_urls': 0,
                                                    'health_status': 'HEALTHY', 'content_status': 'NO_CURRENT_UPDATE'}), encoding='utf-8')
    (tmp_path / 'broken.json').write_text(json.dumps({'source_name': 'broken', 'completed_at': '2026-11-04T08:32:20', 'candidate_urls': 0,
                                                     'health_status': 'PARTIAL_ERROR', 'content_status': 'NO_CURRENT_UPDATE'}), encoding='utf-8')
    assert source_health(tmp_path, '2026-11-04')['problems'] == ['broken: NO_ARTICLES_FOUND']


def test_the_weekly_retrain_deploys_only_a_better_model_and_tests_honestly():
    sys.path.insert(0, str(ROOT / 'scripts'))
    from retrain_models import better, splits
    live = {'keep_recall': 0.80, 'keep_f1': 0.55}
    assert better({'keep_recall': 0.85, 'keep_f1': 0.60}, live)
    assert not better({'keep_recall': 0.75, 'keep_f1': 0.70}, live)   # finds fewer of the editor's stories
    assert not better({'keep_recall': 0.85, 'keep_f1': 0.50}, live)   # worse overall
    days = [f'2026-10-{d:02d}' for d in range(10, 22)]
    train, validation, test = splits(days)
    assert test == set(days[-2:]) and validation == set(days[-4:-2]) and not (train & (validation | test))
    with pytest.raises(SystemExit):
        splits(days[:5])


def test_the_next_days_rupee_close_is_not_the_one_already_sent():
    sent = {'headline': 'Sri Lanka rupee closes at 332.65/70 to US dollar spot, bond yields steady', 'publication_date': '2026-11-04',
            'edition_date': '2026-11-04', 'source_url': 'https://economynext.com/sri-lanka-rupee-closes-at-332-65-70-to-us-dollar-spot-bond-yields-steady-1',
            'summary': 'Sri Lanka rupee closed at 332.65/70 to the US dollar spot on Tuesday, while bond yields were steady. A bond maturing on 01.08.2030 closed at 11.55/60 percent.'}
    later = {'headline': 'Sri Lanka rupee closes at 332.75/80 to US dollar spot, bond yields drop', 'publication_date': '2026-11-04',
             'source_url': 'https://economynext.com/sri-lanka-rupee-closes-at-332-75-80-to-us-dollar-spot-bond-yields-drop-2',
             'body': 'Sri Lanka rupee closed weaker at 332.75/80 to the US dollar spot on Wednesday, from 332.65/70 the previous day. A bond maturing on 01.08.2030 closed at 11.45/50 percent, down from 11.55/60 percent.'}
    assert not same_event(later, sent)
    assert not prior_matches(later, [sent])[0]


def test_fitch_upgrades_finance_companies_separately_from_their_parent_banks():
    banks = {'headline': 'Fitch revises national ratings of eight Sri Lankan banks, including a Cargills Bank downgrade', 'publication_date': '2026-11-02',
             'edition_date': '2026-11-02', 'source_url': 'https://economynext.com/fitch-revises-national-ratings-of-eight-sri-lankan-banks-1',
             'summary': "Fitch Ratings upgraded the National Long-Term Ratings of People's Bank to 'AA+(lka)' and revised ratings of seven other banks."}
    nbfis = {'headline': 'Fitch upgrades 3 Sri Lanka bank-owned NBFIs following parent rating upgrades', 'publication_date': '2026-11-04',
             'source_url': 'https://economynext.com/fitch-upgrades-3-sri-lanka-bank-owned-nbfis-2',
             'body': "Fitch Ratings has upgraded the National Long-Term Ratings of three Sri Lankan bank-supported NBFIs following the upgrade of their parent banks' ratings. People's Leasing and Finance PLC has been upgraded to 'AA(lka)'."}
    assert not same_event(nbfis, banks)
    press = {'headline': "People's Bank secures two-notch Fitch Rating upgrade", 'publication_date': '2026-11-04', 'source_url': 'https://dailynews.lk/peoples-bank-fitch-3',
             'body': "Fitch Ratings upgraded People's Bank's National Long-Term Rating by two notches, from 'AA-(lka)' to 'AA+(lka)', with a Stable Outlook."}
    assert same_event(press, banks)


def test_a_close_stated_as_thursday_is_not_moved_to_wednesday_by_a_later_yesterday():
    sent = {'headline': 'CSE records fairly positive and buoyant share trading amid easing tensions in Iran', 'publication_date': '2026-11-05',
            'edition_date': '2026-11-05', 'source_url': 'https://island.lk/cse-records-fairly-positive-and-buoyant-share-trading',
            'summary': 'The Colombo Stock Exchange gained yesterday as the market weighed the possibility of ending the war in Iran.'}
    thursday = {'headline': 'Sri Lanka stocks surge nearly 3-pct as US election boost drives investor return', 'publication_date': '2026-11-05',
                'source_url': 'https://economynext.com/sri-lanka-stocks-surge-nearly-3-pct-289110',
                'body': 'ECONOMYNEXT – Sri Lanka’s Colombo Stock Exchange closed sharply higher on Thursday, CSE data showed, with the benchmark All Share '
                        'Price Index surging 2.87 percent. The ASPI was up 589.18 points at 21,130.30. “The upward trend that began yesterday, continued.'}
    assert not same_event(thursday, sent)


@pytest.mark.parametrize('headline,expected', [
    ('Two Arrested Over Illegal Pyramid Scheme Fraud in Negombo', 'INCLUDE'),
    ('Two suspects further remanded over Rs. 190 b illegal fund transfers', 'INCLUDE'),
    ('Trust Amendment Bill Passed, Mandating Oversight of Over 500 Trusts', 'INCLUDE'),
    ('EPF raises voting and non-voting stake in Tokyo Cement to 10%', 'INCLUDE'),
    ('Liquor excise jumps 27% to Rs.137.5bn in 1H as production rises 13%', 'INCLUDE'),
    ('Aswesuma: Rs 47 billion supplementary estimate presented in p’ment', 'INCLUDE'),
    ('Renewed GSP Framework: SL prepares for fresh EU GSP+ application', 'REVIEW'),
])
def test_stories_the_editor_wanted_on_6_november_are_not_left_out(headline, expected):
    row = {'headline': headline, 'body': (headline + '. ') * 6, 'source_url': 'https://example.lk/x', 'publication_date': '2026-11-06'}
    assert decide(row)['decision'] == expected


@pytest.mark.parametrize('headline,expected', [
    ('Union Assurance hosts ‘Union Assurance Family Fiesta 2026’', 'EXCLUDE'),
    ('DFCC Bank relocates Peradeniya Branch, enhancing banking convenience', 'EXCLUDE'),
    ('Assetline Finance opens its 64th branch in Wattala', 'KEPT'),
])
def test_staff_events_and_branch_moves_are_dropped_but_new_branches_stay(headline, expected):
    row = {'headline': headline, 'body': (headline + '. ') * 6, 'source_url': 'https://example.lk/x', 'publication_date': '2026-11-06'}
    decision = decide(row)['decision']
    assert decision == expected if expected == 'EXCLUDE' else decision in ('INCLUDE', 'REVIEW')


def test_rule_based_wording_reads_like_the_editors():
    from bank_intel.publication.writing import plain_headline, focus_label
    from bank_intel.publication.evidence import summary, SUMMARY_WORDS
    body = ('COLOMBO (News 1st) - Two suspects accused of defrauding people by operating a pyramid financial scheme were arrested by the '
            'Financial Crimes Investigation Division (FCID). The arrests were made during a raid in Negombo.')
    # Title case becomes sentence case; names the article capitalises stay.
    assert plain_headline('Two Arrested Over Illegal Pyramid Scheme Fraud in Negombo', body) == 'Two arrested over illegal pyramid scheme fraud in Negombo'
    assert focus_label('FINANCIAL_CRIME_AML') == 'Financial crime / AML'
    assert focus_label('SOME_NEW_AML_CODE') == 'Some new AML code'
    text = summary({'headline': 'Two arrested over pyramid scheme', 'body': body}, SUMMARY_WORDS)
    assert text.startswith('Two suspects') and 'News 1st' not in text
    # Courtroom logistics are left out of the summary.
    court = {'headline': 'Two suspects further remanded over Rs. 190 b illegal fund transfers',
             'body': 'The Magistrate ordered the further remand of two suspects over an alleged illegal transfer of Rs. 190 billion abroad. '
                     'The first and eighth suspects in the case were produced before the court by prison officials.'}
    assert 'prison officials' not in summary(court, SUMMARY_WORDS)
    # A deck of points becomes sentences within the length limit.
    deck = {'headline': 'Poverty rises to 16%', 'body': '- Census Department releases HIES 2025 findings - Poverty headcount index climbs to 16% from 14.3% in 2019 '
            '- About 3.42 m people live below the poverty line. ' + 'The survey covered every district in the country this year. ' * 10}
    assert len(summary(deck, SUMMARY_WORDS).split()) <= SUMMARY_WORDS


@pytest.mark.parametrize('headline', [
    'Sri Lanka secures USD 100 Mn ADB loan to transform skills development system',
    '32 Chinese Nationals Arrested in Panadura Over Suspected Online Financial Fraud',
    'PAYE Tax Relief: Government Eyes Further Increase In Tax-Free Threshold',
    'Export-oriented manufacturers eligible for TIEP tax concessions: Govt.',
])
def test_stories_missed_on_9_november_are_now_included(headline):
    row = {'headline': headline, 'body': (headline + '. ') * 6, 'source_url': 'https://example.lk/x', 'publication_date': '2026-11-09'}
    assert decide(row)['decision'] == 'INCLUDE'
    assert decide({**row, 'headline': 'Over 90% of taxpayers yet to file income tax returns'})['reason'] != 'MATERIAL_FISCAL_INDICATOR'


def test_two_reports_of_one_loan_without_a_stated_purpose_are_one_event():
    body = ('The Government of Sri Lanka and the Asian Development Bank (ADB) today signed a loan agreement for USD 100 million '
            'to implement the Skills Development System Transformation Programme, financed under the result-based modality.')
    sent = {'headline': 'Sri Lanka secures USD 100 Mn ADB loan to transform skills development system', 'publication_date': '2026-11-09',
            'edition_date': '2026-11-09', 'source_url': 'https://dailynews.lk/2026/11/09/sri-lanka-secures-usd-100-mn-adb-loan', 'body': body}
    other = {**sent, 'source_url': 'https://economynews.lk/sri-lanka-secures-usd-100-mn-adb-loan/economics', 'edition_date': ''}
    assert same_event(other, sent)


def test_a_banks_own_release_about_an_upgrade_already_sent_is_a_repeat():
    review = {'headline': 'Fitch revises national ratings of eight Sri Lankan banks, including a Cargills Bank downgrade', 'publication_date': '2026-11-02',
              'edition_date': '2026-11-02', 'source_url': 'https://ft.lk/fitch-eight-banks/42-1',
              'summary': 'Fitch Ratings announced bank-specific actions after recalibrating the national scale. Commercial Bank rose from AA−(lka) to AA(lka).'}
    release = {'headline': 'Fitch upgrades ComBank to ‘AA(lka),’ reaffirming position as highest-rated private bank', 'publication_date': '2026-11-10',
               'source_url': 'https://ft.lk/combank-fitch/42-2',
               'body': 'Fitch Ratings has upgraded Commercial Bank of Ceylon PLC’s National Long-Term Rating to ‘AA(lka)’ from ‘AA-(lka)’, effective 30 October 2026.'}
    assert same_event(release, review)
    # A different institution's later action is not.
    other = {**release, 'headline': 'Fitch affirms Sierra Cables at ‘A+(lka)’', 'body': 'Fitch Ratings has affirmed Sierra Cables PLC at A+(lka).'}
    assert not same_event(other, review)


def test_a_summary_is_never_empty_and_social_sector_budget_pieces_stay_out():
    from bank_intel.publication.evidence import summary, SUMMARY_WORDS
    body = ('It has been revealed at a meeting of COPE that the total amount to be recovered from lecturers who breached bonds '
            'could be nearly Rs. 30 billion across the entire university system and the wider public sector this year. ')
    assert summary({'headline': 'Rs. 30 Bn outstanding from academics breaching bonds – COPE', 'body': body}, SUMMARY_WORDS).startswith('It has been revealed')
    row = {'headline': 'Budget 2027 and future of the health sector', 'body': 'The health sector needs more funding. ' * 8,
           'source_url': 'https://example.lk/x', 'publication_date': '2026-11-10'}
    assert decide(row)['decision'] == 'EXCLUDE'
    # A Budget piece with a fiscal fact about the sector stays.
    # Editor, 12 Nov: of the Budget only our domain; a health allocation stays out, a tax measure stays in.
    assert decide({**row, 'headline': 'Budget 2027 allocates Rs. 600 b to health'})['decision'] == 'EXCLUDE'
    assert decide({**row, 'headline': 'Budget 2027 cuts VAT to 15% and raises the tax-free threshold'})['decision'] != 'EXCLUDE'


@pytest.mark.parametrize('body,opening', [
    # A cut that leaves a fragment: the next whole sentence opens the summary.
    ('- CBSL flags credit outpacing deposits - Bank credit up 23.6% the Central Bank of Sri Lanka (CBSL) flagging credit growth as a risk. '
     'In its Financial Stability Review 2026, the CBSL said intermediation strengthened during the year as banks lent more.', 'In its Financial Stability Review'),
    # Two writers in the byline.
    ('08 Nov 2026 | By Maheesha Mudugamuwa and Methmalie Dissanayake From the presidency and defence to education, '
     'a closer look at the changing priorities in the 2027 expenditure plan shows large shifts.', 'From the presidency'),
    # A sub-headline run into the lead without a full stop.
    ('91-day bills account for 82% of total funds raised Investors favour short-term bills as yields remain unchanged '
     'The Government raised Rs. 65.9 billion at the weekly Treasury bill auction held on Wednesday.', 'The Government raised'),
])
def test_a_summary_opens_with_a_whole_sentence(body, opening):
    from bank_intel.publication.evidence import summary, SUMMARY_WORDS
    assert summary({'headline': 'Story', 'body': body}, SUMMARY_WORDS).startswith(opening)


def test_repeats_found_on_11_november_are_caught_without_merging_separate_stories():
    review = {'headline': 'Fitch revises national ratings of eight Sri Lankan banks', 'publication_date': '2026-11-02', 'edition_date': '2026-11-02',
              'source_url': 'https://ft.lk/fitch-eight/42-1', 'summary': 'Fitch Ratings upgraded Commercial Bank of Ceylon PLC from AA−(lka) to AA(lka).'}
    tops = {'headline': 'Commercial Bank tops Fitch’s local ratings for Sri Lankan private banks', 'publication_date': '2026-11-11',
            'source_url': 'https://dailymirror.lk/combank-tops/215-1',
            'body': 'Fitch Ratings upgraded Commercial Bank of Ceylon PLC’s national long-term rating by one notch to AA(lka), from AA-(lka).'}
    fund = {'headline': 'Fitch assigns NDB Wealth Money Fund first-time ‘AA+f(lka)’', 'publication_date': '2026-11-11',
            'source_url': 'https://economynext.com/ndb-wealth-fitch-2', 'body': 'Fitch Ratings has assigned NDB Wealth Money Fund a rating of AA+f(lka).'}
    assert same_event(tops, review)
    assert not same_event(fund, tops)
    one = {'headline': 'Ravi K appeals to HSBC to consider winding down HDPL in Sri Lanka', 'publication_date': '2026-11-11',
           'source_url': 'https://ft.lk/ravi-hsbc/44-1', 'body': 'Opposition MP Ravi Karunanayake urged The Hongkong & Shanghai Banking Corporation Ltd (HSBC) to reconsider.'}
    two = {'headline': 'Ravi pleads with HSBC to retain its vast local operation', 'publication_date': '2026-11-11',
           'source_url': 'https://island.lk/ravi-pleads-hsbc', 'body': 'MP Ravi Karunanayake called on The Hongkong & Shanghai Banking Corporation Ltd (HSBC) to keep its centre.'}
    assert same_event(one, two)
    op1 = {'headline': 'Are tax concessions really best way to attract investment to Sri Lanka?', 'publication_date': '2026-11-09',
           'source_url': 'https://ft.lk/columns/tax-concessions/4-1', 'body': 'Tax concessions have long been used to attract investment.'}
    op2 = {**op1, 'headline': 'Are tax concessions really the best way to attract investment to Sri Lanka?', 'publication_date': '2026-11-11',
           'source_url': 'https://island.lk/tax-concessions'}
    assert same_event(op2, op1)
    # Two board appointments at different companies on one day stay separate.
    b1 = {'headline': 'Lanka Realty Investments bolsters growth with two British experts on Board', 'publication_date': '2026-11-09',
          'source_url': 'https://ft.lk/lri/42-2', 'body': 'Lanka Realty Investments PLC appointed two Independent Non-Executive Directors to its Board.'}
    b2 = {'headline': 'Prashan Fernando joins Colombo Investment Trust Board', 'publication_date': '2026-11-09',
          'source_url': 'https://ft.lk/cit/42-3', 'body': 'Colombo Investment Trust PLC appointed Prashan Fernando as an Independent Non-Executive Director.'}
    assert not same_event(b1, b2)


@pytest.mark.parametrize('headline,expected', [
    ('Cryptocurrency regulations in the pipeline to protect public', 'INCLUDE'),
    ('CoPF calls for independent fuel pricing regulator', 'REVIEW'),
])
def test_crypto_regulation_and_fuel_pricing_are_not_left_out(headline, expected):
    row = {'headline': headline, 'body': (headline + '. ') * 6 + 'The Central Bank of Sri Lanka Governor said so in Colombo.', 'source_url': 'https://example.lk/x', 'publication_date': '2026-11-11'}
    assert decide(row)['decision'] == expected


def test_the_general_repeat_and_duplicate_nets():
    from bank_intel.publication.identity import possible_repeat
    sent = {'headline': 'Seylan Bank reports Rs. 9.12 billion nine-month profit', 'publication_date': '2026-11-03', 'edition_date': '2026-11-03',
            'source_url': 'https://ft.lk/seylan/42-1', 'summary': 'Seylan Bank reported a profit after tax of Rs. 9.12 billion for the nine months.'}
    follow = {'headline': 'Seylan Bank crosses LKR 1 Tn in total assets', 'publication_date': '2026-11-04', 'source_url': 'https://dailynews.lk/seylan-1tn',
              'body': 'Seylan Bank PLC crossed LKR 1 trillion in total assets as at end September.'}
    # A reworded follow-up of a sent story goes to an editor rather than straight in.
    assert possible_repeat(follow, sent) and prior_matches(follow, [sent])[1]
    # Daily series are new every day however alike they read.
    r1 = {'headline': 'Sri Lanka rupee closes at 332.33/40 to US dollar spot', 'publication_date': '2026-11-10', 'edition_date': '2026-11-10',
          'source_url': 'https://economynext.com/r1', 'body': 'Sri Lanka rupee closed at 332.33/40 to the US dollar.'}
    r2 = {**r1, 'headline': 'Sri Lanka rupee closes at 332.53/59 to US dollar spot', 'publication_date': '2026-11-11', 'source_url': 'https://economynext.com/r2'}
    assert not possible_repeat(r2, r1)
    # Two papers on one development the same day are one story; two companies' results are not.
    a = {'headline': 'Central Bank names 27 entities operating illegal pyramid schemes', 'publication_date': '2026-10-29', 'source_url': 'https://ft.lk/p/1',
         'body': 'The Central Bank of Sri Lanka named 27 entities as prohibited pyramid schemes including Max App Media under the Banking Act.'}
    b = {'headline': 'CBSL expands pyramid scheme list to 27 with Max App Media', 'publication_date': '2026-10-29', 'source_url': 'https://economynext.com/p/2',
         'body': 'The Central Bank of Sri Lanka added Max App Media to its list of prohibited pyramid schemes, bringing it to 27 entities.'}
    assert same_event(a, b)
    cic = {'headline': 'CIC Holdings Sept quarter net profit rises 20-pct to Rs1.7bn', 'publication_date': '2026-11-10', 'source_url': 'https://economynext.com/cic',
           'body': 'CIC Holdings PLC said Sept quarter net profit rose 20 percent to 1.7 billion rupees.'}
    kelani = {'headline': 'Kelani Cables Sept quarter net profit up 4-pct to Rs634.5mn', 'publication_date': '2026-11-10', 'source_url': 'https://economynext.com/kc',
              'body': 'Kelani Cables PLC said Sept quarter net profit rose 4 percent to 634.5 million rupees.'}
    assert not same_event(cic, kelani)


@pytest.mark.parametrize('headline,expected', [
    ('CIC Holdings Sept quarter net profit rises 20-pct to Rs1.7bn', 'INCLUDE'),
    ('Alliance Finance plans Rs.1bn bond issue with focus on women-led enterprises', 'INCLUDE'),
    ('Standard Capital mandatory offer awaits SEC nod', 'REVIEW'),
    ('Rs 30b loss from academics bonds default: FUTA rejects COPE claims', 'INCLUDE'),
    ('Taxed at the till: why the Government must keep its word', 'REVIEW'),
    ('Budgeting for a better Sri Lanka', 'EXCLUDE'),
    ('Budget 2027 and future of the health sector', 'EXCLUDE'),
])
def test_categories_the_editor_keeps_are_not_left_out(headline, expected):
    row = {'headline': headline, 'body': (headline + '. ') * 6, 'source_url': 'https://example.lk/x', 'publication_date': '2026-11-11'}
    assert decide(row)['decision'] == expected


def test_possible_duplicates_and_repeats_go_to_an_editor_not_silently_through():
    from bank_intel.publication.identity import possible_duplicate, possible_repeat
    # The same Deputy Minister statement, written very differently by two papers.
    a = {'headline': 'Tax policy: ‘Weaknesses in tax structure are being addressed’: Dep Min', 'publication_date': '2026-11-06',
         'source_url': 'https://themorning.lk/a', 'body': 'The Government has not imposed unnecessary tax burdens, Deputy Minister of Finance Dr Anil Jayantha Fernando said.'}
    b = {'headline': 'No additional burden: Govt. explains recent tax measures', 'publication_date': '2026-11-06',
         'source_url': 'https://ft.lk/b/44-1', 'body': 'Finance Deputy Minister Dr. Anil Jayantha Fernando rebutted claims about eight new tax measures and the tax burden.'}
    assert possible_duplicate(a, b)
    # An abbreviation of a sent story's names: 'AFC' for Asia Frontier Capital.
    sent = {'headline': 'Capital Alliance agrees to buy 51% of Asia Frontier Capital', 'publication_date': '2026-11-03', 'edition_date': '2026-11-03',
            'source_url': 'https://ft.lk/cal/42-1', 'summary': 'Capital Alliance Holdings agreed to acquire 51% of Asia Frontier Capital.'}
    later = {'headline': 'CAL eyes multi-fold growth as AFC opens global capital pipeline', 'publication_date': '2026-11-05',
             'source_url': 'https://dailymirror.lk/cal/215-1', 'body': 'Capital Alliance said the acquisition opens a global pipeline.'}
    assert possible_repeat(later, sent)
    # Two different loans with stated purposes are not a repeat, whatever the lender and amount.
    social = {'headline': 'ADB approves $ 100 m to strengthen social protection', 'publication_date': '2026-10-26', 'edition_date': '2026-10-26',
              'source_url': 'https://ft.lk/adb/44-2', 'summary': 'The Asian Development Bank approved a $ 100 million loan to strengthen social protection.'}
    macro = {'headline': 'Cabinet approves $ 100 m ADB loan to support Sri Lanka’s economic recovery', 'publication_date': '2026-10-28',
             'source_url': 'https://ft.lk/adb/44-3', 'body': 'The Cabinet approved a $ 100 million Asian Development Bank loan for macroeconomic recovery.'}
    assert not possible_repeat(macro, social)
    # A Saturday round-up of Friday's session goes to an editor.
    week = {'headline': 'Week ends in green', 'publication_date': '2026-11-07', 'source_url': 'https://dailymirror.lk/w/215-2',
            'body': 'The ASPI closed in green as a result of price gains in counters such as Commercial Bank.'}
    assert decide(week)['decision'] == 'REVIEW'


def test_the_interactive_brief_shows_every_story_without_a_script(tmp_path):
    # Phones often open the attachment in a preview that runs no script.
    import re, subprocess
    report = ROOT / 'data/processed/reports/2026-11-11/window_1d/Daily Financial Sector News Brief 2026-11-11.html'
    if not report.is_file():
        pytest.skip('reference edition not available')
    out = tmp_path / 'interactive.html'
    subprocess.run([sys.executable, str(ROOT / 'scripts/build_interactive_brief.py'), '--report', str(report), '--output', str(out)], check=True,
                   capture_output=True)
    page = out.read_text(encoding='utf-8')
    without_script = re.sub(r'(?s)<script.*?</script>', '', page)
    cards = len(re.findall(r'<article class="(?:priority-card|context-card)"', report.read_text(encoding='utf-8')))
    assert without_script.count('class="story-wrap"') == cards
    # Features older phone browsers lack must not be needed to draw the page.
    assert 'replaceChildren' not in page and 'flatMap' not in page


def test_13_nov_review_findings_are_handled_automatically():
    """Editor review of 13 Nov 2026: a cricket result, a Fitch repeat, two CSE sessions,
    a dropped short front-page item and duplicate reports of one story."""
    from bank_intel.publication.reconcile import group
    from bank_intel.publication.workflow import cse_direction
    from bank_intel.extraction.validator import SHORT_FINANCIAL_ITEM
    cricket = {'headline': 'CDB and HNB ‘A’ qualify for final', 'publication_date': '2026-11-12', 'source_url': 'https://island.lk/cdb-hnb-final',
               'body': '11 TH SINGER MCA SUPER PREMIER T20 TOURNAMENT Citizens Development Business Finance and Hatton National Bank A qualified '
                       'for the final. CDB beat Hayleys Group A by 4 wickets while HNB A beat Fairfirst Insurance in a super over.'}
    assert decide(cricket)['decision'] == 'EXCLUDE'
    sent = {'headline': 'Fitch upgrades 3 Sri Lanka bank-owned NBFIs following parent rating upgrades', 'publication_date': '2026-11-04',
            'edition_date': '2026-11-05', 'source_url': 'https://economynext.com/fitch-upgrades-3-nbfis-288997',
            'summary': 'Fitch Ratings has upgraded the National Long-Term Ratings of three Sri Lankan bank-supported non-bank financial '
                       'institutions (NBFIs) following the upgrade of their parent banks ratings. National scale ratings are denoted (lka).'}
    later = {'headline': 'Fitch upgrades three Sri Lanka bank-owned NBFIs', 'publication_date': '2026-11-13', 'source_url': 'https://www.ft.lk/fitch-nbfis/42-1',
             'body': 'Fitch Ratings has upgraded the National Long-Term Ratings of three Sri Lankan bank-supported non-bank financial institutions '
                     '(NBFIs). People’s Leasing has been upgraded to AA-(lka) from A(lka).'}
    assert prior_matches(later, [sent])[0]
    # A different Fitch action on one of them is not that repeat.
    mbsl = {'headline': 'Fitch assigns ‘A(lka)’ rating to Merchant Bank of Sri Lanka Rs1bn debt', 'publication_date': '2026-11-12',
            'source_url': 'https://economynext.com/fitch-mbsl-1', 'body': 'Fitch Ratings has assigned a final A(lka) National Long-Term Rating to the debenture.'}
    assert not prior_matches(mbsl, [sent])[0]
    assert cse_direction('Indices close in red') == 'down' and cse_direction('Budget 2027 optimism lifts CSE to green close') == 'up'
    assert SHORT_FINANCIAL_ITEM.search('Sujeewa Mudalige quits NDB Board') and not SHORT_FINANCIAL_ITEM.search('Up and away')
    # An editor's "same story as" link makes another paper's report one more source.
    a = {'article': {'headline': 'Sampath Bank PAT gains 15% to Rs 24.6 b', 'source_url': 'https://ft.lk/sampath/1', 'publication_date': '2026-11-13'},
         'assessment': {'event_signature': '', 'reason': 'SIGNED_EDITORIAL_x'}}
    b = {'article': {'headline': 'Bank posts results for the quarter', 'source_url': 'https://dailynews.lk/sampath/2', 'publication_date': '2026-11-13'},
         'assessment': {'event_signature': '', 'reason': 'SIGNED_EDITORIAL_x', 'same_event_as': 'https://ft.lk/sampath/1'}}
    assert len(group([b, a])) == 1


def test_a_new_bond_session_or_the_presented_budget_is_not_already_sent():
    # 13 Nov 2026: Wednesday's bond report was hidden as Tuesday's (same maturities
    # and yields), and the Budget as presented as a 9 Nov preview of it.
    tuesday = {'headline': 'Secondary Bond market yields consolidate; decline on selected tenors', 'publication_date': '2026-11-12',
               'edition_date': '2026-11-12', 'source_url': 'https://www.ft.lk/bonds/42-1',
               'summary': 'The 01.08.30 and 15.10.30 maturities traded at 11.05% and 11.10%.'}
    wednesday = {'headline': 'Bond yields continue downward trajectory; activity robust', 'publication_date': '2026-11-13',
                 'source_url': 'https://www.ft.lk/bonds/42-2', 'body': 'The 01.08.30 and 15.10.30 traded down the ranges of 11.03%-10.95% and 11.10%-11.00%.'}
    assert not prior_matches(wednesday, [tuesday])[0]
    same_day = {'headline': 'Long-end yields see a modest pullback', 'publication_date': '2026-11-12', 'source_url': 'https://www.dailymirror.lk/bonds/215-1',
                'body': 'Within the 2030 segment, 01.08.2030 and 15.10.2030 traded from 11.05 percent to 11.10 percent.'}
    assert not same_event(wednesday, same_day)
    preview = {'headline': 'Budget 2027: How will the Government spend its money?', 'publication_date': '2026-11-09', 'edition_date': '2026-11-09',
               'source_url': 'https://www.ft.lk/budget/44-1', 'summary': 'The Budget will be presented on 12 November.'}
    presented = {'headline': 'Budget aims at economic transformation', 'publication_date': '2026-11-13', 'source_url': 'https://dailynews.lk/budget/1',
                 'body': 'The Government yesterday unveiled its third Budget, placing economic transformation at the centre.'}
    assert not prior_matches(presented, [preview])[0]


def test_editor_lead_and_standalone_story_are_respected():
    from bank_intel.publication.reconcile import group
    lead = {'article': {'headline': 'AKD unveils Rs. 4.99 trillion budget', 'source_url': 'https://island.lk/budget', 'publication_date': '2026-11-13', 'body': 'Rs. 1 b.'},
            'assessment': {'event_signature': '', 'reason': 'SIGNED_EDITORIAL_x'}}
    other = {'article': {'headline': 'Govt bets on Rs 2t capital spend', 'source_url': 'https://themorning.lk/budget', 'publication_date': '2026-11-13',
                         'body': 'Rs 162,500, Rs 131 b, Rs 95,000, Rs 3 b, Rs 25,000.'},
             'assessment': {'event_signature': '', 'reason': 'SIGNED_EDITORIAL_x', 'same_event_as': 'https://island.lk/budget'}}
    events = group([other, lead])
    assert len(events) == 1 and events[0]['members'][0]['article']['source_url'] == 'https://island.lk/budget'
    quits = {'article': {'headline': 'Sujeewa Mudalige quits NDB Board', 'source_url': 'https://ft.lk/ndb/1', 'publication_date': '2026-11-13',
                         'body': 'Sujeewa Mudalige has resigned from the Board of NDB.'},
             'assessment': {'event_signature': '', 'reason': 'SIGNED_EDITORIAL_x', 'standalone': True}}
    committees = {'article': {'headline': 'NDB reconstitutes Board Subcommittees', 'source_url': 'https://ft.lk/ndb/2', 'publication_date': '2026-11-13',
                              'body': 'The National Development Bank (NDB) has reconstituted the Board Subcommittees.'},
                  'assessment': {'event_signature': '', 'reason': 'DIRECT_BANK_HEADLINE'}}
    assert len(group([committees, quits])) == 2


def test_13_nov_summaries_start_with_the_article():
    cases = {
        # Star bullets run into the lead.
        'Bank of Ceylon delivers Rs. 94.1 Bn PBT in 3Q 2026':
            ('* Profit After Tax Rs. 58.7 Bn * Total Assets base Rs. 5.9 Tn * Total Deposit base Rs. 4.8 Tn Bank of Ceylon (BOC), reinforced its '
             'financial resilience and market leadership during the first nine months of 2026, recording a Profit Before Tax (PBT) of Rs. 94.1 billion. '
             'This performance underscores the Bank’s strong earnings capacity.', 'Bank of Ceylon (BOC), reinforced'),
        # 'claim: Speaker' sub-headlines.
        'Budget 2027: Capex surge won’t derail 2.3% target':
            ('13 Nov 2026 | By Nethmi Rajawasam Post-Ditwah reconstructions expected to push 2027 primary expenditure to 13.6% of GDP: President '
             'Govt’s decision to exceed expenditure target supported by higher revenue expectations: Economist Anushan Kapilan Despite proposed increase '
             'in capital expenditure, primary balance expected to remain within 2.3% IMF target: Economist Umesh Moramudali Even with proposed increases '
             'in capital expenditure and the likelihood of Sri Lanka exceeding its primary balance expenditure limit of 13% in 2027, Sri Lanka will likely '
             'maintain its primary balance target of 2.3%, in line with the IMF’s expectations, Economists speaking to The Daily Morning Business said, '
             'yesterday (12). Reflecting on the 5.0% allocation towards capital expenditure, he said the target holds.', 'Even with proposed increases'),
        # 'of Sri Lanka' is not a glued sub-headline.
        'Fitch assigns ‘A(lka)’ rating to Merchant Bank of Sri Lanka Rs1bn debt':
            ('ECONOMYNEXT – Fitch Ratings has assigned a final ‘A(lka)’ National Long-Term Rating to Merchant Bank of Sri Lanka & Finance PLC’s '
             '(AA-(lka)/Stable) planned 1 billion rupee subordinated listed debenture issue. MBSL’s rating is driven by support from BOC, the agency said.',
             'Fitch Ratings has assigned'),
        # A photo credit with no dash.
        'Sri Lanka’s IMF crossroads puts major-power balancing act in focus':
            ('Ravinatha Aryasinha PIC BY NIMALSIRI EDIRISINGHE Sri Lanka’s decision on whether to continue with the International Monetary Fund (IMF), '
             'after its current programme, would test its ability to navigate competition among major powers, former Foreign Secretary Ravinatha Aryasinha said. '
             'Delivering the lecture, Aryasinha said the IMF programme had brought competing powers together during Sri Lanka’s economic crisis.',
             'Sri Lanka’s decision'),
        # The article's first sentence ends 'according to JB Securities.'
        'Vehicle registrations fall 12.1% in Oct.':
            ('- JB Securities says vehicle imports could top $ 2.4 b in 2026, up 20% from 2025’s $ 1.87 b Total vehicle registrations declined 12.1% '
             'month-on-month to 47,628 units in October from 54,214 in September, reflecting a broad-based contraction across most major vehicle categories, '
             'according to JB Securities. Two-wheeler registrations fell 13% to 33,722 units.', 'Total vehicle registrations declined'),
    }
    for headline, (body, start) in cases.items():
        assert summary({'headline': headline, 'body': body}, 90).startswith(start), headline
    levy = summary({'headline': 'Sri Lanka abolishes Tourism Development Levy from next year',
                    'body': 'Sri Lanka will abolish Tourism Development Levy (TDL) Tourism Development Levy (TDL) imposed under the Finance Act with effect from April 01, 2027.'}, 90)
    assert levy.count('Tourism Development Levy (TDL)') == 1


def test_13_nov_budget_tax_trade_and_soe_stories_are_included():
    for headline in ('President asks: ‘Should the Treasury shoulder SriLankan?’', 'Sri Lanka abolishes Tourism Development Levy from next year',
                     'Sri Lanka to cut CESS on 800 import items by 25-pct, scrap PAL on newsprint', 'Govt plans to expand FTA with India',
                     'Sri Lanka to enter expanded economic partnership with UAE, Oman: President', 'SL’s Public debt to fall to 87% by 2032',
                     'Sri Lanka panel explores if another IMF deal needed: President', 'Vehicle registrations fall 12.1% in Oct.',
                     'Investment: Public-Private Partnership Bill gazetted', 'Budget 2027: Capex surge won’t derail 2.3% target',
                     'Sri Lanka to forego Rs.28.4bn in next year on income tax, CESS, VAT revisions'):
        body = ('President Anura Kumara Dissanayake, in his Budget speech, questioned whether the Treasury can continue to shoulder the '
                'financial burden of the SriLankan Airlines, disclosing that the national carrier has accumulated losses of Rs 528 billion '
                'between 2008 and last year (2025). Presenting the 2027 Budget in Parliament, he said a decision on the airline was needed.') \
            if 'SriLankan' in headline else \
            (headline.rstrip('.') + ', the Sri Lankan Government announced in Parliament yesterday while presenting the 2027 Budget. '
             'The measure forms part of the fiscal plan for next year and will be implemented under the relevant Act, officials said. '
             + headline.rstrip('.') + ' was among the proposals set out in the Budget speech, according to the official document.')
        assert decide({'headline': headline, 'body': body, 'source_url': 'https://example.lk/a'})['decision'] == 'INCLUDE', headline
    # A politician's attack on a tax is not the measure.
    assert decide({'headline': 'Namal lashes out at govt. for failing to remove VAT on school supplies, healthcare', 'body': '',
                   'source_url': 'https://example.lk/b'})['decision'] != 'INCLUDE'
    # Bank results however worded sit in the top section.
    hnb = decide({'headline': 'HNB delivers strong 9-month performance', 'body': 'Hatton National Bank PLC has recorded a solid performance.',
                  'source_url': 'https://www.ft.lk/hnb/42-1'})
    assert hnb['section'] == 'KEY_BANKING_SUPERVISORY'
