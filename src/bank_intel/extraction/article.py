from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
import re
from typing import Optional

import dateparser
from bs4 import BeautifulSoup
import trafilatura

from bank_intel.common.hashing import (
    content_hash,
)

from bank_intel.common.urls import (
    normalize_url,
)


# ============================================================
# DATA MODEL
# ============================================================


@dataclass
class ExtractedArticle:
    """
    Standard normalized article object used by all newspaper
    source adapters.

    IMPORTANT:
    source_url must always remain attached to the article
    because it becomes the source/citation link in reports.
    """

    source_name: str
    section_name: str

    # Original article URL discovered from the newspaper.
    source_url: str

    # URL after HTTP/browser redirects.
    final_url: str

    # Normalized canonical article URL.
    canonical_url: str

    # Article metadata.
    headline: str
    subheadline: Optional[str]

    author: Optional[str]

    # ISO-8601 publication timestamp.
    #
    # Example:
    # 2026-09-15T05:31:00+05:30
    published_at: Optional[str]

    # Clean extracted body text.
    article_text: str

    word_count: int

    # When our system retrieved the page.
    #
    # Stored in UTC.
    retrieved_at: str

    # Hash of normalized article body.
    content_hash: str

    # Current acquisition method.
    #
    # Other future values may include:
    # rss
    # pdf
    # api
    # manual_import
    fetch_method: str = "httpx"

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================
# META HELPERS
# ============================================================


def _meta_content(
    soup: BeautifulSoup,
    *,
    property_name: str | None = None,
    name: str | None = None,
) -> Optional[str]:
    """
    Extract the content attribute from a <meta> tag.

    Supports:

        <meta property="og:title" content="...">

    and:

        <meta name="author" content="...">
    """

    if property_name:

        tag = soup.find(
            "meta",
            attrs={
                "property": property_name
            },
        )

        if (
            tag
            and tag.get("content")
        ):

            value = (
                tag["content"]
                .strip()
            )

            if value:
                return value

    if name:

        tag = soup.find(
            "meta",
            attrs={
                "name": name
            },
        )

        if (
            tag
            and tag.get("content")
        ):

            value = (
                tag["content"]
                .strip()
            )

            if value:
                return value

    return None


# ============================================================
# JSON-LD
# ============================================================


def _extract_jsonld_values(
    soup: BeautifulSoup,
) -> list:
    """
    Extract JSON-LD objects from the page.

    Newspaper websites frequently expose:

        headline
        author
        datePublished
        dateModified
        URL
        mainEntityOfPage

    through JSON-LD.
    """

    results = []

    scripts = soup.find_all(
        "script",
        attrs={
            "type": "application/ld+json"
        },
    )

    for script in scripts:

        raw = script.string

        if not raw:

            raw = script.get_text(
                strip=True
            )

        if not raw:
            continue

        try:

            data = json.loads(
                raw
            )

        except Exception:

            # Malformed JSON-LD must never
            # terminate article extraction.
            continue

        if isinstance(
            data,
            list,
        ):

            results.extend(
                data
            )

        elif isinstance(
            data,
            dict,
        ):

            # Some sites wrap objects inside:
            #
            # {
            #   "@graph": [...]
            # }

            graph = data.get(
                "@graph"
            )

            if isinstance(
                graph,
                list,
            ):

                results.extend(
                    graph
                )

            results.append(
                data
            )

    return results


# ============================================================
# HEADLINE
# ============================================================


def _clean_headline(
    headline: str,
) -> str:
    """
    Normalize article headline and remove known publisher
    branding suffixes.

    Examples:

        Something happened | Daily FT

    becomes:

        Something happened

    and:

        Something happened - Ceylon Today

    becomes:

        Something happened
    """

    if not headline:
        return ""

    # Collapse duplicate spaces/newlines.
    headline = " ".join(
        headline.split()
    ).strip()

    # --------------------------------------------------------
    # KNOWN PUBLISHER BRANDING
    # --------------------------------------------------------

    suffixes = [
        # Daily FT
        "| Daily FT",
        "- Daily FT",
        "– Daily FT",
        "— Daily FT",

        # Ceylon Today
        "| Ceylon Today",
        "- Ceylon Today",
        "– Ceylon Today",
        "— Ceylon Today",

        # Useful future-safe entries
        "| Daily Mirror",
        "- Daily Mirror",
        "– Daily Mirror",
        "— Daily Mirror",

        "| Daily News",
        "- Daily News",
        "– Daily News",
        "— Daily News",

        "| The Island",
        "- The Island",
        "– The Island",
        "— The Island",
    ]

    headline_lower = (
        headline.lower()
    )

    for suffix in suffixes:

        if headline_lower.endswith(
            suffix.lower()
        ):

            headline = (
                headline[
                    : -len(suffix)
                ]
                .strip()
            )

            break

    return headline


def _extract_headline(
    soup: BeautifulSoup,
) -> str:
    """
    Extract headline using multiple strategies.

    Priority:

        1. OpenGraph title
        2. JSON-LD headline
        3. <h1>
        4. <title>
    """

    # ========================================================
    # 1. OPEN GRAPH
    # ========================================================

    headline = _meta_content(
        soup,
        property_name="og:title",
    )

    if headline:

        return _clean_headline(
            headline
        )

    # ========================================================
    # 2. JSON-LD
    # ========================================================

    for item in _extract_jsonld_values(
        soup
    ):

        if not isinstance(
            item,
            dict,
        ):

            continue

        value = item.get(
            "headline"
        )

        if (
            isinstance(
                value,
                str,
            )
            and value.strip()
        ):

            return _clean_headline(
                value
            )

    # ========================================================
    # 3. H1
    # ========================================================

    h1 = soup.find(
        "h1"
    )

    if h1:

        value = h1.get_text(
            " ",
            strip=True,
        )

        if value:

            return _clean_headline(
                value
            )

    # ========================================================
    # 4. PAGE TITLE
    # ========================================================

    if soup.title:

        value = soup.title.get_text(
            " ",
            strip=True,
        )

        if value:

            return _clean_headline(
                value
            )

    return ""


# ============================================================
# SUBHEADLINE / DESCRIPTION
# ============================================================


def _extract_subheadline(
    soup: BeautifulSoup,
) -> Optional[str]:
    """
    Extract a short description/subheadline.

    Priority:

        1. og:description
        2. meta description
        3. JSON-LD description
    """

    description = _meta_content(
        soup,
        property_name="og:description",
    )

    if not description:

        description = _meta_content(
            soup,
            name="description",
        )

    if not description:

        for item in (
            _extract_jsonld_values(
                soup
            )
        ):

            if not isinstance(
                item,
                dict,
            ):

                continue

            value = item.get(
                "description"
            )

            if (
                isinstance(
                    value,
                    str,
                )
                and value.strip()
            ):

                description = (
                    value.strip()
                )

                break

    if not description:
        return None

    description = " ".join(
        description.split()
    ).strip()

    if not description:
        return None

    return description


# ============================================================
# AUTHOR
# ============================================================


def _extract_author(
    soup: BeautifulSoup,
) -> Optional[str]:
    """
    Extract author only when explicitly supplied.

    We never infer or invent an author.
    """

    # ========================================================
    # META AUTHOR
    # ========================================================

    author = _meta_content(
        soup,
        name="author",
    )

    if author:
        return author

    # ========================================================
    # OPEN GRAPH AUTHOR
    # ========================================================

    author = _meta_content(
        soup,
        property_name="article:author",
    )

    if author:
        return author

    # ========================================================
    # JSON-LD AUTHOR
    # ========================================================

    for item in (
        _extract_jsonld_values(
            soup
        )
    ):

        if not isinstance(
            item,
            dict,
        ):

            continue

        author_data = item.get(
            "author"
        )

        # ----------------------------------------------------
        # SINGLE OBJECT
        # ----------------------------------------------------

        if isinstance(
            author_data,
            dict,
        ):

            name = author_data.get(
                "name"
            )

            if (
                isinstance(
                    name,
                    str,
                )
                and name.strip()
            ):

                return name.strip()

        # ----------------------------------------------------
        # LIST OF AUTHORS
        # ----------------------------------------------------

        elif isinstance(
            author_data,
            list,
        ):

            names = []

            for author_item in (
                author_data
            ):

                if isinstance(
                    author_item,
                    dict,
                ):

                    name = (
                        author_item.get(
                            "name"
                        )
                    )

                    if (
                        isinstance(
                            name,
                            str,
                        )
                        and name.strip()
                    ):

                        names.append(
                            name.strip()
                        )

                elif isinstance(
                    author_item,
                    str,
                ):

                    if (
                        author_item.strip()
                    ):

                        names.append(
                            author_item.strip()
                        )

            if names:

                return ", ".join(
                    names
                )

        # ----------------------------------------------------
        # STRING AUTHOR
        # ----------------------------------------------------

        elif isinstance(
            author_data,
            str,
        ):

            if author_data.strip():

                return (
                    author_data.strip()
                )

    return None


# ============================================================
# DATE PARSING
# ============================================================


def _parse_date_candidate(
    value: str,
) -> Optional[str]:
    """
    Parse candidate publication date and normalize it
    to Asia/Colombo timezone.

    If the original date does not include a timezone,
    we interpret it as Sri Lanka time.
    """

    if not value:
        return None

    value = " ".join(
        str(value).split()
    ).strip()

    if not value:
        return None

    try:

        parsed = dateparser.parse(
            value,
            settings={
                "RETURN_AS_TIMEZONE_AWARE": True,
                "TIMEZONE": "Asia/Colombo",
                "TO_TIMEZONE": "Asia/Colombo",
                "DATE_ORDER": "DMY",
                "PREFER_DATES_FROM": "past",
            },
        )

    except Exception:

        return None

    if not parsed:
        return None

    return parsed.isoformat()


# ============================================================
# PUBLICATION DATE
# ============================================================


def _extract_published_date(
    soup: BeautifulSoup,
) -> Optional[str]:
    """
    Extract article publication timestamp.

    Priority:

        1. OpenGraph / article metadata
        2. normal meta fields
        3. JSON-LD datePublished
        4. HTML <time> elements
        5. visible page text

    Visible text fallback supports formats such as:

        Monday, 14 September 2026 00:39

    and:

        14 September 2026 00:39
    """

    candidates: list[str] = []

    # ========================================================
    # 1. OPEN GRAPH / ARTICLE META
    # ========================================================

    property_names = [
        "article:published_time",
        "og:published_time",
        "article:published",
    ]

    for property_name in (
        property_names
    ):

        value = _meta_content(
            soup,
            property_name=(
                property_name
            ),
        )

        if value:

            candidates.append(
                value
            )

    # ========================================================
    # 2. META NAME FIELDS
    # ========================================================

    meta_names = [
        "date",
        "datePublished",
        "datepublished",
        "publish-date",
        "publish_date",
        "publication_date",
        "publication-date",
        "pubdate",
        "article_date_original",
        "sailthru.date",
    ]

    for name in meta_names:

        value = _meta_content(
            soup,
            name=name,
        )

        if value:

            candidates.append(
                value
            )

    # ========================================================
    # 3. JSON-LD
    # ========================================================

    for item in (
        _extract_jsonld_values(
            soup
        )
    ):

        if not isinstance(
            item,
            dict,
        ):

            continue

        value = item.get(
            "datePublished"
        )

        if value:

            candidates.append(
                str(value)
            )

    # ========================================================
    # 4. <time> TAGS
    # ========================================================

    for time_tag in soup.find_all(
        "time"
    ):

        datetime_value = (
            time_tag.get(
                "datetime"
            )
        )

        if datetime_value:

            candidates.append(
                datetime_value
            )

        visible_value = (
            time_tag.get_text(
                " ",
                strip=True,
            )
        )

        if visible_value:

            candidates.append(
                visible_value
            )

    # ========================================================
    # TRY STRUCTURED CANDIDATES FIRST
    # ========================================================

    seen = set()

    for value in candidates:

        if value in seen:
            continue

        seen.add(
            value
        )

        parsed = (
            _parse_date_candidate(
                value
            )
        )

        if parsed:
            return parsed

    # ========================================================
    # 5. VISIBLE PAGE TEXT FALLBACK
    # ========================================================

    page_text = soup.get_text(
        " ",
        strip=True,
    )

    page_text = " ".join(
        page_text.split()
    )

    # --------------------------------------------------------
    # Example:
    #
    # Monday, 14 September 2026 00:39
    # --------------------------------------------------------

    day_name_date_pattern = re.compile(
        r"\b"
        r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
        r",?\s+"
        r"\d{1,2}\s+"
        r"(?:"
        r"January|February|March|April|May|June|July|August|"
        r"September|October|November|December"
        r")"
        r"\s+\d{4}"
        r"(?:\s+\d{1,2}:\d{2})?"
        r"\b",
        re.IGNORECASE,
    )

    match = (
        day_name_date_pattern.search(
            page_text
        )
    )

    if match:

        parsed = (
            _parse_date_candidate(
                match.group(0)
            )
        )

        if parsed:
            return parsed

    # --------------------------------------------------------
    # Example:
    #
    # 14 September 2026 00:39
    # --------------------------------------------------------

    simple_date_pattern = re.compile(
        r"\b"
        r"\d{1,2}\s+"
        r"(?:"
        r"January|February|March|April|May|June|July|August|"
        r"September|October|November|December"
        r")"
        r"\s+\d{4}"
        r"(?:\s+\d{1,2}:\d{2})?"
        r"\b",
        re.IGNORECASE,
    )

    match = (
        simple_date_pattern.search(
            page_text
        )
    )

    if match:

        parsed = (
            _parse_date_candidate(
                match.group(0)
            )
        )

        if parsed:
            return parsed

    # --------------------------------------------------------
    # Example:
    #
    # September 15, 2026
    #
    # Useful for Daily News-style pages later.
    # --------------------------------------------------------

    month_first_pattern = re.compile(
        r"\b"
        r"(?:"
        r"January|February|March|April|May|June|July|August|"
        r"September|October|November|December"
        r")"
        r"\s+\d{1,2}"
        r",\s+\d{4}"
        r"(?:\s+\d{1,2}:\d{2})?"
        r"\b",
        re.IGNORECASE,
    )

    match = (
        month_first_pattern.search(
            page_text
        )
    )

    if match:

        parsed = dateparser.parse(
            match.group(0),
            settings={
                "RETURN_AS_TIMEZONE_AWARE": True,
                "TIMEZONE": "Asia/Colombo",
                "TO_TIMEZONE": "Asia/Colombo",
                "DATE_ORDER": "MDY",
            },
        )

        if parsed:

            return (
                parsed.isoformat()
            )

    return None


# ============================================================
# CANONICAL URL
# ============================================================


def _extract_canonical_url(
    soup: BeautifulSoup,
    final_url: str,
) -> str:
    """
    Extract canonical URL.

    Priority:

        1. <link rel="canonical">
        2. JSON-LD url
        3. JSON-LD mainEntityOfPage
        4. normalized final URL
    """

    # ========================================================
    # 1. CANONICAL LINK
    # ========================================================

    canonical = soup.find(
        "link",
        attrs={
            "rel": "canonical"
        },
    )

    if (
        canonical
        and canonical.get(
            "href"
        )
    ):

        value = (
            canonical[
                "href"
            ]
            .strip()
        )

        if value:

            return normalize_url(
                value
            )

    # ========================================================
    # 2. JSON-LD
    # ========================================================

    for item in (
        _extract_jsonld_values(
            soup
        )
    ):

        if not isinstance(
            item,
            dict,
        ):

            continue

        # ----------------------------------------------------
        # URL FIELD
        # ----------------------------------------------------

        value = item.get(
            "url"
        )

        if (
            isinstance(
                value,
                str,
            )
            and value.strip()
        ):

            return normalize_url(
                value.strip()
            )

        # ----------------------------------------------------
        # MAIN ENTITY OF PAGE
        # ----------------------------------------------------

        main_entity = (
            item.get(
                "mainEntityOfPage"
            )
        )

        if isinstance(
            main_entity,
            dict,
        ):

            entity_url = (
                main_entity.get(
                    "@id"
                )
            )

            if entity_url:

                return normalize_url(
                    str(
                        entity_url
                    )
                )

        elif isinstance(
            main_entity,
            str,
        ):

            if main_entity.strip():

                return normalize_url(
                    main_entity.strip()
                )

    # ========================================================
    # 3. FALLBACK
    # ========================================================

    return normalize_url(
        final_url
    )


# ============================================================
# ARTICLE TEXT NORMALIZATION
# ============================================================


def _normalize_article_text(
    text: str,
) -> str:
    """
    Normalize extracted article text while preserving
    paragraph separation.

    We do not aggressively clean the text here because
    regulatory evidence should preserve original content
    wherever possible.
    """

    if not text:
        return ""

    clean_lines = []

    for line in text.splitlines():

        line = " ".join(
            line.split()
        ).strip()

        if not line:
            continue

        clean_lines.append(
            line
        )

    return "\n\n".join(
        clean_lines
    )


# ============================================================
# ARTICLE BODY
# ============================================================


def _extract_article_text(
    html: str,
    url: str,
) -> str:
    """
    Extract main article body using Trafilatura.

    favor_precision=True reduces:

        navigation
        related-news links
        footer text
        menus
        advertisements
        site controls
    """

    try:

        text = trafilatura.extract(
            html,
            url=url,

            include_comments=False,
            include_tables=False,
            include_links=False,
            include_images=False,

            favor_precision=True,

            output_format="txt",
        )

    except Exception:

        return ""

    if not text:
        return ""

    return (
        _normalize_article_text(
            text
        )
    )


# ============================================================
# MAIN EXTRACTION FUNCTION
# ============================================================


def extract_article(
    *,
    html: str,
    source_name: str,
    section_name: str,
    source_url: str,
    final_url: str,
) -> ExtractedArticle:
    """
    Convert downloaded newspaper HTML into our normalized
    ExtractedArticle format.

    This function is intentionally source-independent.

    Daily FT, Ceylon Today and future source adapters
    all feed their HTML through this same normalization layer.
    """

    soup = BeautifulSoup(
        html,
        "lxml",
    )

    # ========================================================
    # HEADLINE
    # ========================================================

    headline = (
        _extract_headline(
            soup
        )
    )

    # ========================================================
    # SUBHEADLINE
    # ========================================================

    subheadline = (
        _extract_subheadline(
            soup
        )
    )

    # ========================================================
    # AUTHOR
    # ========================================================

    author = (
        _extract_author(
            soup
        )
    )

    # ========================================================
    # PUBLICATION DATE
    # ========================================================

    published_at = (
        _extract_published_date(
            soup
        )
    )

    # ========================================================
    # CANONICAL URL
    # ========================================================

    canonical_url = (
        _extract_canonical_url(
            soup,
            final_url,
        )
    )

    # ========================================================
    # ARTICLE BODY
    # ========================================================

    article_text = (
        _extract_article_text(
            html,
            canonical_url,
        )
    )

    word_count = len(
        article_text.split()
    )

    # ========================================================
    # RETRIEVAL TIMESTAMP
    # ========================================================

    retrieved_at = (
        datetime.now(
            timezone.utc
        )
        .isoformat()
    )

    # ========================================================
    # CONTENT HASH
    # ========================================================

    article_content_hash = (
        content_hash(
            article_text
        )
    )

    # ========================================================
    # RETURN NORMALIZED ARTICLE
    # ========================================================

    return ExtractedArticle(
        source_name=(
            source_name
        ),

        section_name=(
            section_name
        ),

        # ----------------------------------------------------
        # NEVER REMOVE OR RECONSTRUCT THIS.
        #
        # This becomes the final report/source citation.
        # ----------------------------------------------------

        source_url=(
            source_url
        ),

        final_url=(
            final_url
        ),

        canonical_url=(
            canonical_url
        ),

        headline=(
            headline
        ),

        subheadline=(
            subheadline
        ),

        author=(
            author
        ),

        published_at=(
            published_at
        ),

        article_text=(
            article_text
        ),

        word_count=(
            word_count
        ),

        retrieved_at=(
            retrieved_at
        ),

        content_hash=(
            article_content_hash
        ),

        fetch_method="httpx",
    )