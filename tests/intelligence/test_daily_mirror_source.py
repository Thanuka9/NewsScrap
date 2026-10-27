from bank_intel.collectors.sources.daily_mirror import DailyMirrorBusinessAdapter


def test_daily_mirror_accepts_real_business_articles():
    adapter = DailyMirrorBusinessAdapter()

    good = [
        (
            "https://www.dailymirror.lk/business/"
            "CSE-ends-flat-as-early-recovery-fades/215-351912"
        ),
        (
            "https://www.dailymirror.lk/business-main/"
            "Firms-exiting-tax-holidays-exhausting-losses/"
            "245-347848"
        ),
        (
            "https://www.dailymirror.lk/business-news/"
            "Some-business-story/273-347840"
        ),
    ]

    for url in good:
        assert adapter.is_article_url(url)


def test_daily_mirror_rejects_listing_and_navigation_urls():
    adapter = DailyMirrorBusinessAdapter()

    bad = [
        "https://www.dailymirror.lk/business",
        "https://www.dailymirror.lk/business/215/22",
        "https://www.dailymirror.lk/business-news/273/577",
        "https://www.dailymirror.lk/news/155",
        "https://www.dailymirror.lk/news/342",
        "https://www.dailymirror.lk/news/110",
    ]

    for url in bad:
        assert not adapter.is_article_url(url)


def test_daily_mirror_discovery_filters_navigation():
    adapter = DailyMirrorBusinessAdapter()

    html = """
    <html><body>
      <a href="/news/155">Top Stories</a>
      <a href="/business/215/22">Business page</a>
      <a href="/business/Test-story/215-999999">Real story</a>
    </body></html>
    """

    urls = adapter.discover_article_urls(
        html,
        "https://www.dailymirror.lk/business",
    )

    assert urls == [
        "https://www.dailymirror.lk/business/Test-story/215-999999"
    ]
