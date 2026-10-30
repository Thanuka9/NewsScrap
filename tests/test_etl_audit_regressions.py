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
