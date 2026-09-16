from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup
import dateparser

from bank_intel.common.urls import (
    absolute_url,
    normalize_url,
    domain_matches,
)


# ============================================================
# SOURCE CONFIGURATION
# ============================================================

SOURCE_NAME = "Daily News"

SECTION_NAME = "Business"

BASE_URL = "https://dailynews.lk"

BUSINESS_URL = (
    "https://dailynews.lk/category/business/"
)


# ============================================================
# ARTICLE URL
#
# Example:
#
# /2026/09/15/business/1039003/
# revenue-officials-urged-to-widen-tax-base
# ============================================================

ARTICLE_PATTERN = re.compile(
    r"^/"
    r"(?P<year>\d{4})/"
    r"(?P<month>\d{2})/"
    r"(?P<day>\d{2})/"
    r"business/"
    r"\d+/"
    r".+"
    r"$",
    re.IGNORECASE,
)


# ============================================================
# VISIBLE DATE
# ============================================================

VISIBLE_DATE_PATTERN = re.compile(
    r"\b"
    r"(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)"
    r"\s+"
    r"\d{1,2}"
    r",\s+"
    r"\d{4}"
    r"\b",
    re.IGNORECASE,
)


# ============================================================
# DISCOVERED ARTICLE
# ============================================================

@dataclass
class DiscoveredDailyNewsArticle:
    headline: str
    url: str

    published_at_hint: str | None

    excerpt: str | None


# ============================================================
# DATE HELPERS
# ============================================================

def _parse_date_text(
    value: str,
) -> str | None:

    if not value:
        return None

    parsed = dateparser.parse(
        value,
        settings={
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TIMEZONE": "Asia/Colombo",
            "TO_TIMEZONE": "Asia/Colombo",
            "DATE_ORDER": "MDY",
        },
    )

    if not parsed:
        return None

    return parsed.isoformat()


def _date_from_article_url(
    url: str,
) -> str | None:
    """
    Daily News includes YYYY/MM/DD directly in article URL.

    This is a very useful and reliable discovery-date hint.
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
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
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
# NEARBY CARD INFORMATION
# ============================================================

def _extract_card_information(
    anchor,
) -> tuple[
    str | None,
    str | None,
]:
    """
    Search a few levels around the headline link for:

        publication date
        article excerpt

    This intentionally avoids depending on a single CSS class.
    """

    node = anchor

    for _ in range(7):

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

        text = " ".join(
            text.split()
        )

        if not text:
            continue

        # Avoid moving all the way up into a giant page wrapper.
        if len(text) > 2200:
            continue

        date_match = (
            VISIBLE_DATE_PATTERN.search(
                text
            )
        )

        if date_match:

            published = (
                _parse_date_text(
                    date_match.group(0)
                )
            )

            # ------------------------------------------------
            # EXCERPT
            # ------------------------------------------------

            paragraphs = node.find_all(
                "p"
            )

            excerpt = None

            for paragraph in paragraphs:

                value = paragraph.get_text(
                    " ",
                    strip=True,
                )

                value = " ".join(
                    value.split()
                )

                if len(value) >= 40:

                    excerpt = value
                    break

            return (
                published,
                excerpt,
            )

    return (
        None,
        None,
    )


# ============================================================
# DISCOVERY
# ============================================================

def discover_articles(
    html: str,
) -> list[
    DiscoveredDailyNewsArticle
]:

    soup = BeautifulSoup(
        html,
        "lxml",
    )

    discovered: dict[
        str,
        DiscoveredDailyNewsArticle,
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
            BUSINESS_URL,
            href,
        )

        full_url = normalize_url(
            full_url
        )

        if not domain_matches(
            full_url,
            "dailynews.lk",
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

        # Image links can point to the same story.
        if len(headline) < 8:
            continue

        (
            published_at_hint,
            excerpt,
        ) = _extract_card_information(
            anchor
        )

        # Strong fallback:
        # Daily News article URL itself contains date.
        if not published_at_hint:

            published_at_hint = (
                _date_from_article_url(
                    full_url
                )
            )

        existing = discovered.get(
            full_url
        )

        if existing is None:

            discovered[
                full_url
            ] = (
                DiscoveredDailyNewsArticle(
                    headline=headline,
                    url=full_url,
                    published_at_hint=(
                        published_at_hint
                    ),
                    excerpt=excerpt,
                )
            )

            continue

        # Prefer richer values from duplicate anchors.

        if (
            not existing.headline
            and headline
        ):

            existing.headline = (
                headline
            )

        if (
            existing.published_at_hint
            is None
            and published_at_hint
        ):

            existing.published_at_hint = (
                published_at_hint
            )

        if (
            existing.excerpt is None
            and excerpt
        ):

            existing.excerpt = excerpt

    return list(
        discovered.values()
    )