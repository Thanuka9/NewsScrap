"""Publisher integration, API completeness and editorial boundary regressions."""
from datetime import date, datetime
from pathlib import Path
import json
import csv
import sys

import httpx
import pytest
import yaml

from bank_intel.collection import generic_source_engine as engine
from bank_intel.publication.identity import approved, SOURCE_KEYS, sources_for_date
from bank_intel.publication.decisions import decide

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


def post(number=1, **changes):
    return {'link': f'https://economynext.com/article-{number}/',
            'date': '2026-10-28T23:30:00', 'date_gmt': '2026-10-28T20:00:00Z',
            'title': {'rendered': 'Commercial Bank reports quarterly profit growth'},
            'content': {'rendered': '<p>' + ('Bank earnings increased while capital and liquidity remained strong. ' * 12) + '</p>'},
            **changes}


def api(monkeypatch, responses):
    calls = []
    original = httpx.Client
    def handler(request):
        calls.append(request)
        rows, headers, status = responses[len(calls)-1]
        return httpx.Response(status, json=rows, headers=headers)
    monkeypatch.setattr(engine.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    return calls


PROFILE = {'name': 'EconomyNext', 'listing_url': 'https://economynext.com/wp-json/wp/v2/posts',
           'listing_urls': [{'name': 'WordPressAPI', 'url': 'https://economynext.com/wp-json/wp/v2/posts', 'type': 'wordpress_api'}],
           'discovery_start_date': '2026-10-29', 'discovery_end_date': '2026-10-29'}


@pytest.mark.parametrize('case', ['missing_totals', 'missing_row', 'repeat_page', 'page_cap', 'http_failure'])
def test_api_never_claims_incomplete_listing_is_complete(monkeypatch, case):
    hundred = [post(n) for n in range(100)]
    responses = {
        'missing_totals': [([post()], {}, 200)],
        'missing_row': [([post()], {'X-WP-Total': '2', 'X-WP-TotalPages': '1'}, 200)],
        'repeat_page': [(hundred, {'X-WP-Total': '101', 'X-WP-TotalPages': '2'}, 200),
                        ([post(0)], {'X-WP-Total': '101', 'X-WP-TotalPages': '2'}, 200)],
        'page_cap': [(hundred, {'X-WP-Total': '101', 'X-WP-TotalPages': '2'}, 200)],
        'http_failure': [([], {}, 503)],
    }[case]
    api(monkeypatch, responses)
    _, diag = engine._discover_wordpress_api(PROFILE, PROFILE['listing_url'], max_pages=1 if case == 'page_cap' else 20)
    assert diag['pagination_complete'] is False
    assert diag['listing_error']


def test_api_stages_all_pages_in_colombo_window(monkeypatch, tmp_path):
    first = [post(n) for n in range(100)]
    calls = api(monkeypatch, [(first, {'X-WP-Total': '101', 'X-WP-TotalPages': '2'}, 200),
                             ([post(100)], {'X-WP-Total': '101', 'X-WP-TotalPages': '2'}, 200)])
    result = engine.collect_and_stage(project_root=tmp_path, source_key='economynext', profile=PROFILE,
        from_date=date(2026, 10, 29), to_date=date(2026, 10, 29), production=True, limit=1)
    assert result['staged_count'] == 101  # Production ignores diagnostic limits.
    assert result['listing']['listing_pages'][0]['pagination_complete'] is True
    payload = json.loads(Path(result['staged'][0]['path']).read_text())
    assert payload['published_at'].startswith('2026-10-29T01:30:00')
    assert payload['source_name'] == 'EconomyNext'
    assert len(calls) == 2 and calls[0].url.params['before'] == '2026-10-31T00:00:00'


def test_invalid_api_article_records_failure_and_continues(monkeypatch, tmp_path):
    api(monkeypatch, [([post(1, date_gmt='invalid'), post(2)], {'X-WP-Total': '2', 'X-WP-TotalPages': '1'}, 200)])
    result = engine.collect_and_stage(project_root=tmp_path, source_key='economynext', profile=PROFILE,
        from_date=date(2026, 10, 29), to_date=date(2026, 10, 29), production=True)
    assert result['staged_count'] == 1
    assert result['missing_date'] == result['invalid_extractions'] == 1
    assert result['unresolved_fetch_urls'] == ['https://economynext.com/article-1']


def test_empty_verified_api_is_healthy_not_collection_failure(monkeypatch, tmp_path):
    import run_generic_sources as runner
    api(monkeypatch, [([], {'X-WP-Total': '0', 'X-WP-TotalPages': '0'}, 200)])
    result = engine.collect_and_stage(project_root=tmp_path, source_key='economynext', profile=PROFILE,
        from_date=date(2026, 10, 29), to_date=date(2026, 10, 29), production=True)
    monkeypatch.setattr(runner, 'current_status_path', lambda key: tmp_path / (key + '.json'))
    status = runner.write_production_status(source_key='economynext', profile=PROFILE, item=result,
        run_id='test', started_at=datetime.now(engine.SRI_LANKA_TZ), from_date=date(2026,10,29), to_date=date(2026,10,29))
    assert status['health_status'] == 'HEALTHY'
    assert status['content_status'] == 'NO_NEW_ARTICLES'


def test_new_publishers_flow_through_registry_identity_and_coverage(tmp_path):
    from news_release_gate import prepare_coverage
    from news_integrity_audit import publisher
    from audit_news_flow import source_keys_for_date
    from run_collection import REQUIRED_PRODUCTION_SOURCES
    registry = yaml.safe_load((ROOT / 'config/sources/registry.yaml').read_text())['sources']
    coverage = json.loads(prepare_coverage(date(2026,10,29), tmp_path, tmp_path/'coverage.json').read_text())
    for name, host in [('EconomyNext', 'economynext.com'), ('EconomyNews', 'economynews.lk')]:
        key = SOURCE_KEYS[name]
        assert registry[key]['enabled'] and registry[key]['acquisition_method'] == 'GENERIC'
        assert key in REQUIRED_PRODUCTION_SOURCES and key in source_keys_for_date('2026-10-29')
        assert approved(name, f'https://{host}/story')
        assert not approved(name, f'https://{host}.example.org/story')
        assert not approved('Daily FT', f'https://{host}/story')
        assert publisher(f'https://{host}/story') == name
        assert coverage['publishers'][name]['automatic_issues']
        assert name not in sources_for_date('2026-10-28')


def test_both_publishers_reach_governed_report_and_missing_coverage_holds(tmp_path):
    from bank_intel.publication.workflow import build
    from bank_intel.publication.release import audit_release
    rows = []
    for name, host, bank in [('EconomyNext', 'economynext.com', 'Commercial Bank'),
                             ('EconomyNews', 'economynews.lk', 'Sampath Bank')]:
        headline = f'{bank} reports quarterly profit growth'
        rows.append({'source_name': name, 'source_url': f'https://{host}/bank-results',
            'publication_date': '2026-10-29', 'headline': headline,
            'body': (headline + '. Earnings increased while capital and liquidity remained strong. ') * 10,
            'section': 'EXCLUDE'})
    audit = tmp_path/'audit.json'
    audit.write_text(json.dumps({'audit': rows}))
    inventory = tmp_path/'inventory.csv'
    with inventory.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    manifest, report = build(date_iso='2026-10-29', audit=audit, inventory=inventory,
        output=tmp_path/'edition', sent=[], css_file=ROOT/'src/bank_intel/publication/news_style.css')
    assert manifest['source_count'] == 9
    rendered_sources = {s['source_name'] for event in manifest['events'] for s in event['sources']}
    assert rendered_sources == {'EconomyNext', 'EconomyNews'}
    assert all(event['section'] == 'KEY_BANKING_SUPERVISORY' for event in manifest['events'])
    coverage = tmp_path/'coverage.json'
    coverage.write_text(json.dumps({'report_date': '2026-10-29', 'publishers': {}}))
    output = tmp_path/'release.json'
    assert audit_release(report=report, manifest_path=report.parent/'candidate_edition.json',
        inventory=inventory, coverage=coverage, out=output, report_date='2026-10-29') == 2
    issues = json.loads(output.read_text())['issues']
    held = {i['publisher'] for i in issues if i['kind'] == 'PUBLISHER_COVERAGE_NOT_VERIFIED'}
    assert {'EconomyNext', 'EconomyNews'} <= held


@pytest.mark.parametrize('headline,section', [
    ('BOC to raise Rs. 30 b in listed debt issue', 'KEY_BANKING_SUPERVISORY'),
    ('Fitch rates Sampath Bank Basel III subordinated debentures', 'KEY_BANKING_SUPERVISORY'),
    ('National QR payment adoption program begins', 'BANKING_SECTOR_DEVELOPMENTS'),
    ('Pine Labs and WEBXPAY bring Indian payment experience to Sri Lanka', 'BANKING_SECTOR_DEVELOPMENTS'),
    ('Commercial Bank offers new deposit rates', 'BANKING_SECTOR_DEVELOPMENTS'),
    ('HNB FINANCE and Toyota Lanka forge leasing partnership', 'OTHER_FINANCIAL_SECTOR'),
    ('CERT warns of critical vulnerabilities in government systems', 'KEY_BANKING_SUPERVISORY'),
    ('Court sets deadline for payment in medical procurement case', ''),
])
def test_editorial_section_boundaries(headline, section):
    article = {'headline': headline, 'body': (headline + '. Details were provided in a statement today. ') * 12,
               'publication_date': '2026-10-29'}
    result = decide(article)
    assert result['section'] == section
    if 'CERT' in headline:
        assert result['decision'] == 'REVIEW'
