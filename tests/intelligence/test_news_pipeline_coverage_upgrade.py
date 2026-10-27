from __future__ import annotations

from datetime import date
from pathlib import Path
import importlib.util
import json
import sys

from bank_intel.common.discovery_policy import OldPageStopper, publication_date_in_url


def test_pagination_stops_after_two_full_old_pages():
    gate = OldPageStopper(date(2026, 10, 19))
    assert not gate.observe([date(2026, 10, 20), date(2026, 10, 18), date(2026, 10, 18)])
    assert not gate.observe([date(2026, 10, 18)] * 5)
    assert gate.observe([date(2026, 10, 17)] * 5)


def test_pagination_does_not_stop_on_unknown_mixed_or_failed_page():
    gate = OldPageStopper(date(2026, 10, 19))
    assert not gate.observe([date(2026, 10, 18)] * 5)
    assert not gate.observe([date(2026, 10, 18)] * 5, unknown_dates=1)
    assert not gate.observe([date(2026, 10, 18)] * 5)
    assert not gate.observe([], failed=True)
    assert not gate.observe([date(2026, 10, 18)] * 5)
    assert gate.observe([date(2026, 10, 18)] * 5)


def test_url_date_parse_newsfirst_and_dailynews():
    assert publication_date_in_url('https://english.newsfirst.lk/2026/10/20/economy') == date(2026, 10, 20)
    assert publication_date_in_url('https://dailynews.lk/2026/10/19/business/story') == date(2026, 10, 19)
    assert publication_date_in_url('https://www.themorning.lk/articles/XXXX') is None


def test_ft_top_story_and_home_in_discovery():
    script = (Path(__file__).resolve().parents[2] / 'scripts' / 'run_daily_ft.py').read_text(encoding='utf-8')
    assert 'https://www.ft.lk/top-story/26/0' in script
    assert '("HOMEPAGE", "https://www.ft.lk/")' in script
    assert 'if found_older > 0:' not in script


def load_auditor():
    path = Path(__file__).resolve().parents[2] / 'scripts' / 'audit_news_flow.py'
    spec = importlib.util.spec_from_file_location('news_flow_test', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_newsfirst_material_staged_exclusion_is_explainable(tmp_path):
    m = load_auditor()
    d = '2026-10-20'
    path = tmp_path / 'data' / 'staged' / 'newsfirst' / d / 'id-1'
    path.mkdir(parents=True)
    url = 'https://english.newsfirst.lk/2026/10/20/business/cbsl-ai-risk'
    (path / 'v001.json').write_text(json.dumps({
        'source_url': url, 'headline': 'CBSL warns banks on cyber risks',
        'published_at': '2026-10-20T07:00:00+05:30', 'collection_run_date': d,
    }))
    root = tmp_path / 'data' / 'processed' / 'reports' / d / 'window_1d'
    root.mkdir(parents=True)
    (root / 'stakeholder_brief_v2_audit.json').write_text(json.dumps({
        'audit': [], 'events': [], 'qa': {'status': 'PASS'},
    }))
    # Remaining six statuses are artificially healthy so only gap is tested.
    statuses = tmp_path / 'data' / 'source_status'
    statuses.mkdir(parents=True)
    for source in m.SOURCES:
        (statuses / (source + '.json')).write_text(json.dumps({
            'completed_at': '2026-10-20T09:00:00+05:30',
            'execution_status': 'SUCCESS', 'fetch_failures': 0,
            'discovery_surfaces': (['business','homepage','breaking news','top stories','latest news'] if source=='daily_mirror' else [str(i) for i in range(m.EXPECTED_SURFACES[source])]),
        }))
    result = m.source_audit(tmp_path, date(2026, 10, 20), 1)
    assert result['status'] == 'FAIL'
    assert len(result['material_unreviewed']) == 1
    # Once review marks it excluded, omission becomes transparent, not a gate error.
    (root / 'stakeholder_brief_v2_audit.json').write_text(json.dumps({
        'audit': [{'source_url': url, 'section': 'EXCLUDE', 'reason': 'NOT_RELEVANT'}],
        'events': [], 'qa': {'status': 'PASS'},
    }))
    result = m.source_audit(tmp_path, date(2026, 10, 20), 1)
    assert result['status'] == 'PASS'
    assert result['stage_status_by_source']['newsfirst']['REVIEWED_EXCLUDED'] == 1


def test_generic_profiles_cover_homepage_without_link_skip():
    import yaml
    root = Path(__file__).resolve().parents[2]
    data = yaml.safe_load((root / 'config/sources/generic_registry.yaml').read_text(encoding='utf-8'))
    for key in ('newsfirst', 'daily_news', 'the_morning'):
        listing_urls = data['sources'][key].get('listing_urls') or []
        assert any('Homepage' in item.get('name','') for item in listing_urls)
    assert not data['sources']['the_morning'].get('skip_first_n_article_links')


def test_public_collector_retries_one_url_without_repeating_all_pages(monkeypatch):
    from types import SimpleNamespace
    from bank_intel.collectors.sources import _public_business_base as base

    monkeypatch.setattr(base, 'write_source_status', lambda *_args, **_kw: Path('status.json'))
    monkeypatch.setattr(base, 'stage_article', lambda *_args, **_kw: ('NEW', None))
    monkeypatch.setattr(base.time, 'sleep', lambda *_args: None)
    calls = []

    class FakeSource:
        source_key = 'island'
        source_name = 'The Island'
        section_name = 'Business'
        def listing_url(self, page):
            return 'https://island.lk/category/business/page/' + str(page)
        def fetch(self, url):
            calls.append(url)
            if url.endswith('/story-10') and calls.count(url) == 1:
                raise TimeoutError('transient error')
            return SimpleNamespace(html=url, final_url=url)
        def discover_article_urls(self, html, base_url):
            if 'page/' not in html:
                return []
            page = int(html.rsplit('/', 1)[-1])
            return [f'https://island.lk/story-{page*10+i}' for i in range(5)]
        def is_article_url(self, url):
            return '/story-' in url
        def extract(self, url, fetch):
            page = int(url.rsplit('-', 1)[-1]) // 10
            pub = date(2026, 10, 20) if page == 1 else date(2026, 10, 17)
            from datetime import datetime
            return SimpleNamespace(
                published_at=datetime(pub.year, pub.month, pub.day),
                headline='Sampath Bank announces balance sheet developments',
                article_text='Banking story ' * 100,
            )

    adapter = FakeSource()
    result = base.run_collection(adapter, start_date=date(2026, 10, 19),
                                 end_date=date(2026, 10, 20),
                                 max_pages=6, sleep_seconds=0)
    assert result['stop_reason'] == 'TWO_CONFIRMED_OLDER_PAGES'
    assert result['last_page_attempted'] == 3
    assert result['fetch_failures'] == 0
    # Normal pages and retry visit only the single failed article twice.
    assert calls.count('https://island.lk/story-10') == 2


def test_newsfirst_first_time_prior_day_arrival_enters_stage1(tmp_path):
    """Regression for NewsFirst's 15 LATE_UNREVIEWED items on Oct 20."""
    from bank_intel.collection.generic_source_engine import ArticleResult, stage_article

    article = ArticleResult(
        url='https://english.newsfirst.lk/2026/10/19/bank-of-china',
        valid=True,
        headline='Bank of China named Sri Lanka official RMB clearing bank',
        published_at='2026-10-19T13:20:00+05:30',
        article_text='The central bank has appointed a clearing bank for RMB settlement.',
    )
    result = stage_article(
        project_root=tmp_path,
        source_key='newsfirst',
        profile={'name': 'NewsFirst'},
        article=article,
        collection_run_date='2026-10-20',
        production=True,
    )
    payload = json.loads(Path(result['path']).read_text(encoding='utf-8'))
    assert result['status'] == 'NEW_ARTICLE'
    assert payload['intake_status'] == 'LATE_DISCOVERY'
    spec = importlib.util.spec_from_file_location(
        'stage1_late_regression',
        Path(__file__).resolve().parents[2] / 'scripts' / 'run_intelligence_stage1.py',
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    check = lambda d: module.matches_target_date(
        path=Path(result['path']), payload=d, target_date='2026-10-20'
    )
    assert check(payload) == (True, 'late_discovery')
    # Pre-fix NewsFirst records already on disk also qualify safely.
    assert check({**payload, 'intake_status': 'NEW_ARTICLE'}) == (True, 'late_discovery')
    assert check({**payload, 'intake_status': 'CHANGED_ARTICLE', 'article_version': 2}) == (False, None)
    assert check({**payload, 'collection_run_date': '2026-10-19'}) == (False, None)
    assert check({**payload, 'published_at': '2026-10-17T12:00:00+05:30'}) == (False, None)


def test_cross_publisher_same_event_is_not_reported_missing():
    auditor = load_auditor()
    reported = [
        "Bank of China Colombo branch launches as Sri Lanka's first RMB clearing bank"
    ]
    assert auditor.same_reported_event(
        'Bank of China Named Sri Lanka’s Official RMB Clearing Bank', reported
    )
    assert not auditor.same_reported_event(
        'SLPA proposes new logistics hub after previous project discontinued',
        ['China Merchants SLPA and Access discontinue logistics hub project'],
    )
    assert not auditor.same_reported_event(
        'Central Bank publishes annual banking supervision report',
        ['Central Bank issues enforcement order against a bank'],
    )
