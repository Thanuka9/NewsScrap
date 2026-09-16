from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from bank_intel.common.urls import (
    absolute_url,
    normalize_url,
    domain_matches,
)


# ============================================================
# SOURCE INFORMATION
# ============================================================

SOURCE_NAME = "Ceylon Today"

SECTION_NAME = "Finance Today"

BASE_URL = "https://ceylontoday.lk"

FINANCE_TODAY_URL = (
    "https://ceylontoday.lk/"
    "category/ceylon-today-daily/"
    "finance-today/"
)


# ============================================================
# ARTICLE URL
#
# Example:
#
# https://ceylontoday.lk/
# 2026/08/17/
# cdb-monthly-medal-at-rcgc-in-its-third-consecutive-year
#
# ============================================================

ARTICLE_PATTERN = re.compile(
    r"^/"
    r"(?P<year>\d{4})/"
    r"(?P<month>\d{2})/"
    r"(?P<day>\d{2})/"
    r"(?P<slug>[^/]+)"
    r"/?$",
    re.IGNORECASE,
)


# ============================================================
# DISCOVERED ARTICLE
# ============================================================


@dataclass
class DiscoveredCeylonTodayArticle:
    headline: str
    url: str

    published_at_hint: str | None

    excerpt: str | None


# ============================================================
# PAGINATION
# ============================================================


def build_section_page_url(
    page_number: int,
) -> str:
    """
    WordPress-style archive pagination.

    Page 1:
        /finance-today/

    Page 2:
        /finance-today/page/2/

    Page 3:
        /finance-today/page/3/
    """

    if page_number <= 1:

        return FINANCE_TODAY_URL

    return (
        f"{FINANCE_TODAY_URL}"
        f"page/{page_number}/"
    )


# ============================================================
# ARTICLE DATE FROM URL
# ============================================================


def publication_hint_from_url(
    url: str,
) -> str | None:
    """
    Ceylon Today embeds YYYY/MM/DD directly in article URLs.

    That is ideal for discovery filtering because we do not
    need to open every article simply to learn its date.
    """

    path = urlparse(
        url
    ).path

    match = ARTICLE_PATTERN.match(
        path
    )

    if not match:
        return None

    try:

        dt = datetime(
            year=int(
                match.group("year")
            ),
            month=int(
                match.group("month")
            ),
            day=int(
                match.group("day")
            ),
        )

    except ValueError:

        return None

    return (
        dt.strftime(
            "%Y-%m-%d"
        )
        + "T00:00:00+05:30"
    )


# ============================================================
# EXCERPT
# ============================================================


def _find_excerpt(
    anchor,
) -> str | None:
    """
    Look around the article anchor for a nearby summary/excerpt.

    This is optional metadata only.
    """

    node = anchor

    for _ in range(6):

        node = getattr(
            node,
            "parent",
            None,
        )

        if node is None:
            break

        try:

            text = node.get_text(
                " ",
                strip=True,
            )

        except AttributeError:

            continue

        if not text:
            continue

        # Avoid reaching a massive page wrapper.
        if len(text) > 2500:
            continue

        paragraphs = node.find_all(
            "p"
        )

        for paragraph in paragraphs:

            value = paragraph.get_text(
                " ",
                strip=True,
            )

            value = " ".join(
                value.split()
            )

            if len(value) >= 40:

                return value

    return None


# ============================================================
# DISCOVER ARTICLES
# ============================================================


def discover_articles(
    html: str,
) -> list[
    DiscoveredCeylonTodayArticle
]:

    soup = BeautifulSoup(
        html,
        "lxml",
    )

    discovered: dict[
        str,
        DiscoveredCeylonTodayArticle,
    ] = {}

    for anchor in soup.find_all(
        "a",
        href=True,
    ):

        href = anchor.get(
            "href"
        )

        if not href:
            continue

        full_url = absolute_url(
            FINANCE_TODAY_URL,
            href,
        )

        full_url = normalize_url(
            full_url
        )

        if not domain_matches(
            full_url,
            "ceylontoday.lk",
        ):

            continue

        path = urlparse(
            full_url
        ).path

        if not ARTICLE_PATTERN.match(
            path
        ):

            continue

        headline = anchor.get_text(
            " ",
            strip=True,
        )

        headline = " ".join(
            headline.split()
        )

        # Ignore image-only/empty anchors.
        if len(headline) < 8:

            continue

        publication_hint = (
            publication_hint_from_url(
                full_url
            )
        )

        excerpt = _find_excerpt(
            anchor
        )

        existing = discovered.get(
            full_url
        )

        if existing is None:

            discovered[
                full_url
            ] = (
                DiscoveredCeylonTodayArticle(
                    headline=headline,
                    url=full_url,
                    published_at_hint=(
                        publication_hint
                    ),
                    excerpt=excerpt,
                )
            )

            continue

        # Prefer richer duplicate anchor data.

        if (
            not existing.headline
            and headline
        ):

            existing.headline = (
                headline
            )

        if (
            existing.excerpt is None
            and excerpt
        ):

            existing.excerpt = (
                excerpt
            )

    return list(
        discovered.values()
    )