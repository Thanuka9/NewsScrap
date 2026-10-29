from datetime import date
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from bank_intel.collection import generic_source_engine as engine
from bank_intel.common.discovery_policy import OldPageStopper, publication_date_in_url


def test_featured_recent_news_does_not_prevent_archive_boundary():
    profile = {'article_url_patterns': [r'https://english\.newsfirst\.lk/20\d\d/'],
               'pagination_date_exclude_selectors': ['.local_news', '.border-right-local']}
    pinned = '<div class="local_news"><a href="/2026/10/29/today">today</a></div>'
    pinned += '<div class="border-right-local"><a href="/2026/10/29/featured">featured</a></div>'
    stopper = OldPageStopper(date(2026,10,28))
    for page in (1,2):
        body = pinned + ''.join(f'<a href="/2026/10/27/old-{page}-{n}">old</a>' for n in range(5))
        links = engine._pagination_date_links(body, 'https://english.newsfirst.lk/latest', profile)
        assert len(links) == 5
        assert stopper.observe([publication_date_in_url(u) for u in links]) is (page == 2)
    # Without a configured exclusion, a mixed page must still prevent stopping.
    all_links = engine._pagination_date_links(body, 'https://english.newsfirst.lk/latest',
                                             {'article_url_patterns': profile['article_url_patterns']})
    assert not stopper.observe([publication_date_in_url(u) for u in all_links])


def test_short_article_requires_explicit_body_date_and_headline_agreement(monkeypatch):
    body = ('The Central Bank of Sri Lanka has named twenty seven entities operating prohibited pyramid schemes. '
            'Following its investigation, the Central Bank published the full list for public awareness on its website.')
    url = 'https://english.newsfirst.lk/2026/10/29/bank-notice'
    def make_page(text=body, dated=True):
        return ('<html><head><meta property="og:title" content="Central Bank names prohibited pyramid schemes">'
                + ('<meta property="article:published_time" content="2026-10-29T09:15:00+05:30">' if dated else '')
                + '</head><body><div class="new_details"><p>'+text+'</p></div></body></html>')
    def fetch(text):
        return engine.FetchResult(url=url, final_url=url, status=200, text=text, method='httpx', blocked=False)
    profile = {'article_body_selector': '.new_details', 'browser_retry_on_invalid': False}
    monkeypatch.setattr(engine, 'fetch_page', lambda *a, **kw: fetch(make_page()))
    article = engine.extract_article(url, profile)
    assert article.valid and article.warnings[0].startswith('SHORT_PUBLISHER_NOTICE:')
    for text in (make_page(dated=False), make_page('A sports team played a match in the city. '*4)):
        monkeypatch.setattr(engine, 'fetch_page', lambda *a, **kw: fetch(text))
        assert not engine.extract_article(url, profile).valid


def test_timeout_records_failed_stage_and_passes_explicit_budget(monkeypatch, tmp_path):
    import run_pipeline as pipeline
    calls = []
    def timeout(command, **kwargs):
        calls.append(kwargs)
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])
    monkeypatch.setattr(pipeline, 'run_process', timeout)
    monkeypatch.setattr(pipeline, 'PROJECT_ROOT', tmp_path)
    steps = []
    with pytest.raises(subprocess.TimeoutExpired):
        pipeline.run_step(name='COLLECTION', command=['collector'], steps=steps, timeout=7200)
    assert calls[0]['timeout'] == 7200
    assert steps[0]['status'] == 'TIMED_OUT' and steps[0]['timeout_seconds'] == 7200


def test_generic_status_keeps_completed_attempt_history(monkeypatch, tmp_path):
    import run_generic_sources as runner
    from datetime import datetime
    monkeypatch.setattr(runner, 'current_status_path', lambda key: tmp_path/(key+'.json'))
    result = runner.write_production_status(source_key='newsfirst', profile={'name':'NewsFirst'},
        item={'candidate_urls':1,'valid_extractions':1,'listing':{'listing_status':200}},
        run_id='completed-attempt', started_at=datetime.now(runner.TZ),
        from_date=date(2026,10,28), to_date=date(2026,10,29))
    (tmp_path/'newsfirst.json').unlink()  # A new attempt starts and is killed.
    history = list((tmp_path/'history').rglob('*.json'))
    assert len(history) == 1
    assert json.loads(history[0].read_text()) == result
