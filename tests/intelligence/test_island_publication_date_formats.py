from bs4 import BeautifulSoup

from bank_intel.collectors.sources._public_business_base import (
    extract_published_at,
    parse_datetime_text,
)


def test_slash_publication_date_parses():
    value = parse_datetime_text(
        "2026/10/4"
    )

    assert value is not None
    assert value.date().isoformat() == "2026-10-04"


def test_island_published_on_slash_date_is_extracted():
    html = """
    <html>
      <body>
        <div>
          Published
          2 hours ago
          on
          2026/10/4
        </div>
      </body>
    </html>
    """

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    value = extract_published_at(
        soup,
        [],
    )

    assert value is not None
    assert value.date().isoformat() == "2026-10-04"


def test_island_published_window_tolerates_extra_markup_text():
    html = """
    <html>
      <body>
        <div class="meta">
          Published about 2 hours ago | Updated later | 2026/10/4
        </div>
      </body>
    </html>
    """

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    value = extract_published_at(
        soup,
        [],
    )

    assert value is not None
    assert value.date().isoformat() == "2026-10-04"


def test_unrelated_slash_date_without_published_marker_is_not_used():
    html = """
    <html>
      <body>
        <p>
          The programme began on 2026/10/4 and continued during the week.
        </p>
      </body>
    </html>
    """

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    assert extract_published_at(
        soup,
        [],
    ) is None


def test_newlines_between_published_tokens_are_normalized():
    html = (
        "<html><body><div>"
        "Published\n"
        "2 hours ago\n"
        "on\n"
        "2026/10/4"
        "</div></body></html>"
    )

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    value = extract_published_at(
        soup,
        [],
    )

    assert value is not None
    assert value.date().isoformat() == "2026-10-04"
