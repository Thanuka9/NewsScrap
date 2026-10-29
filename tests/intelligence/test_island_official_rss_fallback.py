"""Offline regression checks for The Island's full-content RSS fallback."""

from xml.sax.saxutils import escape
import pytest

from bank_intel.collectors.sources._public_business_base import (
    BusinessSourceAdapter, FetchResult,
)
from bank_intel.collectors.sources.island import IslandBusinessAdapter


URL = "https://island.lk/official-rmb-clearing-bank-story/"
HEADLINE = "Bank of China launches clearing arrangements"


def make_rss(*, words=145, suffix="", without_content=False):
    text = " ".join(["Sri Lankan banks are monitoring cross border settlement flows"] * 21)
    if words < 80:
        text = " ".join(["Brief notice about Sri Lankan finance operations."] * 7)
    body = f"<![CDATA[<p>{text}</p>]]>" if not without_content else ""
    return ("<?xml version='1.0' encoding='UTF-8'?>"
            "<rss version='2.0' xmlns:content='http://purl.org/rss/1.0/modules/content/'>"
            "<channel><item>"
            f"<title>{escape(HEADLINE+suffix)}</title>"
            f"<link>{URL}</link>"
            "<pubDate>Mon, 19 Oct 2026 23:59:46 +0000</pubDate>"
            f"<description><![CDATA[{'summary ' * 56}]]></description>"
            f"<content:encoded>{body}</content:encoded>"
            "</item></channel></rss>").encode("utf-8")


class FakeResponse:
    status_code = 200
    def __init__(self, body):
        self.content = body


class Client:
    def __init__(self, body):
        self.body = body
        self.urls = []
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def get(self, url):
        self.urls.append(url)
        return FakeResponse(self.body)


def setup_blocked(monkeypatch, rss_body):
    def blocked(self, url):
        raise RuntimeError(f"Browser fallback returned unusable page (status=307): {url}")
    monkeypatch.setattr(BusinessSourceAdapter, "fetch", blocked)
    client = Client(rss_body)
    monkeypatch.setattr("bank_intel.collectors.sources.island.httpx.Client", lambda **k: client)
    return client


def test_rss_recovers_listing_and_full_article_with_colombo_date(monkeypatch):
    client = setup_blocked(monkeypatch, make_rss())
    adapter = IslandBusinessAdapter()
    listing = adapter.fetch("https://island.lk/category/business/")
    assert listing.method == "rss-listing"
    assert adapter.discover_article_urls(listing.html, listing.final_url) == [URL.rstrip("/")]
    article = adapter.extract(URL, adapter.fetch(URL))
    assert article.headline == HEADLINE
    assert article.published_at.isoformat().startswith("2026-10-20T05:29:46+05:30")
    assert len(article.article_text.split()) >= 80
    assert article.fetched_via == "rss-full-content"
    assert len(client.urls) == 1  # No repeated web requests per article.
    diag = adapter.rss_diagnostics()
    assert diag['used'] and diag['full_articles_recovered'] == 1


def test_normal_website_remains_primary(monkeypatch):
    def success(self, url):
        return FetchResult(url, url, "<html><title>Normal site</title></html>", 200, "httpx")
    monkeypatch.setattr(BusinessSourceAdapter, "fetch", success)
    adapter = IslandBusinessAdapter()
    assert adapter.fetch("https://island.lk/category/business/").method == "httpx"
    assert adapter.rss_diagnostics()['used'] is False


def test_direct_article_uses_first_full_feed_without_reading_other_feeds(monkeypatch):
    client = setup_blocked(monkeypatch, make_rss())
    adapter = IslandBusinessAdapter()
    assert adapter.fetch(URL).method == 'rss-full-content'
    assert len(client.urls) == 1


def test_failed_feed_is_not_refetched_for_every_article(monkeypatch):
    import httpx
    client = Client(b'')
    def failed(url):
        client.urls.append(url)
        raise httpx.ReadTimeout('feed timed out')
    client.get = failed
    monkeypatch.setattr('bank_intel.collectors.sources.island.httpx.Client', lambda **kw: client)
    adapter = IslandBusinessAdapter()
    for _ in range(3):
        with pytest.raises(RuntimeError):
            adapter._read_rss_page('business', 1)
    assert len(client.urls) == 1
    assert len(adapter.rss_diagnostics()['errors']) == 1


def test_transient_fetch_retries_only_once(monkeypatch):
    from bank_intel.collectors.sources import island
    attempts = []
    def failed(self, url):
        attempts.append(url)
        raise RuntimeError('ReadTimeout: timed out')
    monkeypatch.setattr(island, '_ISLAND_ORIGINAL_FETCH', failed)
    monkeypatch.setattr(island._island_retry_time, 'sleep', lambda seconds: None)
    with pytest.raises(RuntimeError, match='ReadTimeout'):
        IslandBusinessAdapter().fetch(URL)
    assert len(attempts) == 2


def test_feed_summary_does_not_replace_incomplete_full_article(monkeypatch):
    setup_blocked(monkeypatch, make_rss(without_content=True))
    adapter = IslandBusinessAdapter()
    adapter.fetch("https://island.lk/category/business/")
    with pytest.raises(RuntimeError, match="incomplete"):
        adapter.fetch(URL)


def test_repeated_pagination_cannot_masquerade_as_coverage(monkeypatch):
    setup_blocked(monkeypatch, make_rss())
    adapter = IslandBusinessAdapter()
    adapter.fetch("https://island.lk/category/business/")
    with pytest.raises(RuntimeError, match="repeats the first page"):
        adapter.fetch("https://island.lk/category/business/page/2/")
    assert adapter.rss_diagnostics()['errors']


def test_unrelated_errors_do_not_switch_to_rss(monkeypatch):
    def timeout(self, url):
        raise RuntimeError("Article body too short")
    monkeypatch.setattr(BusinessSourceAdapter, "fetch", timeout)
    adapter = IslandBusinessAdapter()
    with pytest.raises(RuntimeError, match="too short"):
        adapter.fetch("https://island.lk/category/business/")
    assert adapter.rss_diagnostics()['used'] is False


def test_short_official_rss_notice_remains_reviewable(monkeypatch):
    setup_blocked(monkeypatch, make_rss(words=50))
    adapter = IslandBusinessAdapter()
    adapter.fetch("https://island.lk/category/business/")
    date_hint = adapter.listing_publication_date(URL)
    assert str(date_hint) == "2026-10-20"  # UTC 19 Oct 23:59 -> Colombo 20 Oct
    article = adapter.extract(URL, adapter.fetch(URL))
    assert article.fetched_via == "rss-full-content"
    assert any(w.startswith("ARTICLE_TOO_SHORT") for w in article.warnings)


def test_rss_date_hint_not_used_on_normal_website(monkeypatch):
    def success(self, url):
        return FetchResult(url, url, "<html></html>", 200, "httpx")
    monkeypatch.setattr(BusinessSourceAdapter, "fetch", success)
    adapter = IslandBusinessAdapter()
    assert adapter.listing_publication_date(URL) is None


def test_rss_date_hint_skips_old_articles_before_fetch_and_stops(monkeypatch):
    from datetime import date, datetime
    from pathlib import Path
    from types import SimpleNamespace
    from bank_intel.collectors.sources import _public_business_base as base

    staged_urls = []
    fetched_article_urls = []
    monkeypatch.setattr(base, "write_source_status", lambda *_a, **_kw: Path("dummy.json"))
    monkeypatch.setattr(base, "stage_article", lambda article, **kw: (staged_urls.append(article.headline) or "NEW", None))
    monkeypatch.setattr(base.time, "sleep", lambda *_a: None)

    class Source:
        source_key = "island"
        source_name = "The Island"
        section_name = "Business"

        def listing_url(self, page):
            return f"https://island.lk/category/business/page/{page}"

        def fetch(self, url):
            if "/article-" in url:
                fetched_article_urls.append(url)
                return SimpleNamespace(html="", final_url=url, method="rss-full-content")
            return SimpleNamespace(html=url, final_url=url, method="rss-listing")

        def discover_article_urls(self, html, final_url):
            page = int(final_url.rstrip("/").split("/")[-1])
            return [f"https://island.lk/article-{page}-{i}" for i in range(3)]

        def is_article_url(self, url):
            return "/article-" in url

        def listing_publication_date(self, url):
            return date(2026, 10, 20) if "/article-1-" in url else date(2026, 10, 17)

        def extract(self, url, fetched):
            return SimpleNamespace(
                published_at=datetime(2026, 10, 20),
                headline="Regulatory banking notice",
                article_text="Banking and financial supervision update. " * 30,
                warnings=[],
            )

    status = base.run_collection(Source(), start_date=date(2026, 10, 19),
                                 end_date=date(2026, 10, 20), max_pages=6,
                                 sleep_seconds=0)
    assert status["stop_reason"] == "TWO_CONFIRMED_OLDER_PAGES"
    assert status["last_page_attempted"] == 3
    assert status["wrong_date_skipped"] == 6
    assert status["fetch_failures"] == 0
    assert len(staged_urls) == 3
    assert len(fetched_article_urls) == 3  # No fetch for six old articles.
