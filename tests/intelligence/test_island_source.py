from bank_intel.collectors.sources.island import IslandBusinessAdapter
from bank_intel.collectors.sources._public_business_base import (
    extract_generic_article,
    FetchResult,
)


def test_island_accepts_root_post_slug():
    adapter = IslandBusinessAdapter()

    assert adapter.is_article_url(
        "https://island.lk/"
        "sunshine-holdings-records-16-6-yoy-revenue-growth-in-1qfy27/"
    )


def test_island_rejects_category_and_pagination():
    adapter = IslandBusinessAdapter()

    bad = [
        "https://island.lk/",
        "https://island.lk/category/business/",
        "https://island.lk/category/business/page/2/",
        "https://island.lk/tag/banking/",
        "https://island.lk/author/editor/",
    ]

    for url in bad:
        assert not adapter.is_article_url(url)


def test_island_discovery_filters_to_posts():
    adapter = IslandBusinessAdapter()

    html = """
    <html><body>
      <article>
        <a class="post-category" href="/category/business/">
          Business
        </a>
        <h2>
          <a href="/buffers-bounce-back-official-reserves-climb/">
            Article
          </a>
        </h2>
      </article>
      <a href="/category/business/page/2/">Next</a>
    </body></html>
    """

    urls = adapter.discover_article_urls(
        html,
        "https://island.lk/category/business/",
    )

    assert urls == [
        "https://island.lk/buffers-bounce-back-official-reserves-climb"
    ]


def test_island_body_stops_before_related_content():
    html = """
    <html>
      <head>
        <script type="application/ld+json">
        {
          "@type": "NewsArticle",
          "headline": "Test Island Story",
          "datePublished": "2026-10-07T08:00:00+05:30"
        }
        </script>
      </head>
      <body>
        <article>
          <p>This is the first paragraph with enough useful article text
          for the extraction logic to keep reading and preserve evidence.</p>
          <p>This is another substantive paragraph containing more useful
          business reporting material and supporting context for the article.</p>
          <p>Continue Reading</p>
          <p>This belongs to another recommended story and must not appear.</p>
        </article>
      </body>
    </html>
    """

    result = extract_generic_article(
        source_key="island",
        source_name="The Island",
        source_url="https://island.lk/test-story/",
        fetch=FetchResult(
            requested_url="https://island.lk/test-story/",
            final_url="https://island.lk/test-story/",
            html=html,
            status_code=200,
            method="httpx",
        ),
        section="Business",
        stop_markers=("continue reading",),
    )

    assert "another recommended story" not in result.article_text.lower()


def test_publisher_server_error_recovers_page_from_official_rss(monkeypatch):
    from bank_intel.collectors.sources import _public_business_base as base
    adapter = IslandBusinessAdapter.__new__(IslandBusinessAdapter)
    def failing(self, url):
        raise RuntimeError("Browser fallback returned unusable page (status=500): " + url)
    monkeypatch.setattr(base.BusinessSourceAdapter, "fetch", failing)
    monkeypatch.setattr(IslandBusinessAdapter, "_rss_for_url", lambda self, url: "RSS:" + url)
    assert adapter.fetch("https://island.lk/a-story") == "RSS:https://island.lk/a-story"
    # Not a permanent switch: the next page still tries the publisher first.
    assert not adapter._rss_mode


def test_unrelated_fetch_error_is_not_masked_by_rss(monkeypatch):
    import pytest
    from bank_intel.collectors.sources import _public_business_base as base
    adapter = IslandBusinessAdapter.__new__(IslandBusinessAdapter)
    monkeypatch.setattr(base.BusinessSourceAdapter, "fetch", lambda self, url: (_ for _ in ()).throw(RuntimeError("getaddrinfo failed")))
    with pytest.raises(RuntimeError, match="getaddrinfo"):
        adapter.fetch("https://island.lk/a-story")
