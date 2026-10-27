from bank_intel.collectors.sources.island import (
    IslandBusinessAdapter,
)


def test_island_rejects_known_non_article_root_paths():
    adapter = IslandBusinessAdapter()

    bad = [
        "https://island.lk/classifieds",
        "https://island.lk/conatct-us",
        "https://island.lk/contact-us",
        "https://island.lk/privacy-policy",
    ]

    for url in bad:
        assert not adapter.is_article_url(
            url
        )


def test_island_forces_https_for_article_urls():
    adapter = IslandBusinessAdapter()

    value = adapter._force_https(
        "http://island.lk/sample-business-story"
    )

    assert value == (
        "https://island.lk/sample-business-story"
    )


def test_discovery_keeps_business_cards_and_drops_sports_navigation():
    adapter = IslandBusinessAdapter()

    html = """
    <html>
      <body>

        <header>
          <a href="/category/business/">Business</a>
          <a href="/ronaldo-apologizes-for-portugal-walkout-says-jesus-broke-promise">
            Ronaldo sports story
          </a>
          <a href="/nalanda-on-the-cusp-of-reaching-knockout-stage-as-unbeaten-run-continues">
            School sports story
          </a>
        </header>

        <main>
          <article class="post-card">
            <a class="post-category" href="/category/business/">Business</a>
            <h2>
              <a href="/buffers-bounce-back-official-reserves-climb">
                Buffers bounce back
              </a>
            </h2>
          </article>

          <article class="post-card">
            <a class="post-category" href="/category/business/">Business</a>
            <h2>
              <a href="http://island.lk/ceybank-celebrates-35-years-of-stability-and-growth">
                Ceybank celebrates 35 years
              </a>
            </h2>
          </article>
        </main>

        <footer>
          <a href="/classifieds">Classifieds</a>
          <a href="/conatct-us">Contact us</a>
        </footer>

      </body>
    </html>
    """

    urls = adapter.discover_article_urls(
        html,
        "https://island.lk/category/business/",
    )

    assert urls == [
        "https://island.lk/buffers-bounce-back-official-reserves-climb",
        "https://island.lk/ceybank-celebrates-35-years-of-stability-and-growth",
    ]


def test_large_wrapper_does_not_validate_unrelated_links():
    adapter = IslandBusinessAdapter()

    html = """
    <html>
      <body>
        <div class="whole-page-wrapper">
          <a href="/category/business/">Business</a>
          <a href="/sports-story-one">Sports 1</a>
          <a href="/sports-story-two">Sports 2</a>
          <a href="/sports-story-three">Sports 3</a>
          <a href="/sports-story-four">Sports 4</a>
          <a href="/sports-story-five">Sports 5</a>
        </div>
      </body>
    </html>
    """

    assert (
        adapter.discover_article_urls(
            html,
            "https://island.lk/category/business/",
        )
        == []
    )
