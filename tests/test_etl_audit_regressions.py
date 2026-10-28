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
