from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
import re
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from bank_intel.collectors.http_client import (
    HttpClient,
)

from bank_intel.collectors.browser_client import (
    BrowserClient,
)

from bank_intel.common.urls import (
    absolute_url,
    normalize_url,
    domain_matches,
)

from bank_intel.extraction.article import (
    extract_article,
)

from bank_intel.extraction.validator import (
    validate_article,
)


# ============================================================
# SOURCE SPECIFICATION
# ============================================================


@dataclass
class SourceDiagnosticSpec:
    source_code: str
    name: str
    section_name: str
    section_url: str
    domain: str

    # Optional URL tokens that make a link more likely
    # to be an article from the relevant section.
    preferred_tokens: tuple[str, ...] = ()


# ============================================================
# DIAGNOSTIC RESULT
# ============================================================


@dataclass
class ArticleDiagnostic:
    url: str
    headline: str | None

    fetch_method: str
    status_code: int | None

    extracted_headline: str | None
    published_at: str | None

    word_count: int

    valid: bool

    errors: list[str]
    warnings: list[str]


@dataclass
class SourceDiagnosticResult:
    source_code: str
    source_name: str

    section_name: str
    section_url: str

    section_fetch_success: bool

    section_fetch_method: str | None
    section_status_code: int | None

    candidate_links_found: int

    tested_articles: int

    successful_articles: int

    browser_fallback_used: bool

    article_results: list[ArticleDiagnostic]

    notes: list[str]


# ============================================================
# CURRENT INITIAL SOURCE CONFIGURATION
# ============================================================


SOURCE_SPECS = [

    SourceDiagnosticSpec(
        source_code="SRC001",
        name="Daily News",
        section_name="Business",
        section_url=(
            "https://dailynews.lk/category/business/"
        ),
        domain="dailynews.lk",
        preferred_tokens=(
            "/business/",
        ),
    ),

    SourceDiagnosticSpec(
        source_code="SRC002",
        name="Daily Mirror",
        section_name="Business",
        section_url=(
            "https://www.dailymirror.lk/business"
        ),
        domain="dailymirror.lk",
        preferred_tokens=(
            "/business/",
            "/business-news/",
        ),
    ),

    SourceDiagnosticSpec(
        source_code="SRC003",
        name="Ceylon Today",
        section_name="Finance Today Daily",
        section_url=(
            "https://ceylontoday.lk/"
            "category/ceylon-today-daily/"
            "finance-today/"
        ),
        domain="ceylontoday.lk",
        preferred_tokens=(),
    ),

    SourceDiagnosticSpec(
        source_code="SRC004",
        name="The Island",
        section_name="Business",
        section_url=(
            "https://island.lk/category/business/"
        ),
        domain="island.lk",
        preferred_tokens=(),
    ),

    SourceDiagnosticSpec(
        source_code="SRC005",
        name="Daily FT",
        section_name="Financial Services",
        section_url=(
            "https://www.ft.lk/"
            "financial-services/42"
        ),
        domain="ft.lk",
        preferred_tokens=(
            "/financial-services/",
        ),
    ),
]


# ============================================================
# URL FILTERING
# ============================================================


EXCLUDED_PATH_FRAGMENTS = (
    "/category/",
    "/tag/",
    "/author/",
    "/page/",
    "/feed/",
    "/wp-content/",
    "/contact",
    "/privacy",
    "/terms",
    "/advert",
    "/login",
    "/register",
    "/search",
)


EXCLUDED_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".pdf",
    ".zip",
    ".mp4",
    ".mp3",
)


def _looks_like_article_url(
    *,
    url: str,
    spec: SourceDiagnosticSpec,
) -> bool:
    """
    Generic article-link heuristic.

    This is intentionally broad because the diagnostic
    is meant to discover how each site behaves.
    """

    if not domain_matches(
        url,
        spec.domain,
    ):
        return False

    parsed = urlparse(
        url
    )

    path = parsed.path.lower()

    if not path or path == "/":
        return False

    for fragment in (
        EXCLUDED_PATH_FRAGMENTS
    ):

        if fragment in path:
            return False

    if path.endswith(
        EXCLUDED_EXTENSIONS
    ):
        return False

    # Avoid the section URL itself.
    if (
        normalize_url(url)
        == normalize_url(
            spec.section_url
        )
    ):
        return False

    # Where we know a useful section token,
    # give it priority.
    if spec.preferred_tokens:

        if any(
            token.lower() in path
            for token
            in spec.preferred_tokens
        ):
            return True

    # Generic fallback:
    #
    # Real article URLs usually have more than
    # one meaningful path component.
    components = [
        part
        for part in path.split("/")
        if part
    ]

    return len(
        components
    ) >= 2


# ============================================================
# DISCOVER LINKS
# ============================================================


def discover_candidate_links(
    *,
    html: str,
    spec: SourceDiagnosticSpec,
    max_links: int = 50,
) -> list[tuple[str, str]]:
    """
    Extract likely article links from a section page.
    """

    soup = BeautifulSoup(
        html,
        "lxml",
    )

    candidates: dict[
        str,
        str,
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
            spec.section_url,
            href,
        )

        full_url = normalize_url(
            full_url
        )

        if not _looks_like_article_url(
            url=full_url,
            spec=spec,
        ):
            continue

        headline = anchor.get_text(
            " ",
            strip=True,
        )

        headline = " ".join(
            headline.split()
        )

        # Require some visible anchor text for diagnostics.
        # Image-only anchors may be duplicated by title links.
        if len(headline) < 8:
            continue

        if full_url not in candidates:

            candidates[
                full_url
            ] = headline

        if (
            len(candidates)
            >= max_links
        ):
            break

    return [
        (url, headline)
        for url, headline
        in candidates.items()
    ]


# ============================================================
# FETCH WITH FALLBACK
# ============================================================


def fetch_with_fallback(
    *,
    url: str,
    http_client: HttpClient,
    browser_client: BrowserClient,
):
    """
    Try ordinary HTTP first.

    Use Playwright if HTTP:
    - fails
    - is blocked
    - returns suspiciously little HTML
    """

    try:

        result = http_client.get(
            url
        )

        html_length = len(
            result.html or ""
        )

        if html_length >= 2000:

            return (
                result,
                "httpx",
                False,
            )

    except Exception:
        pass

    browser_result = (
        browser_client.get(
            url
        )
    )

    return (
        browser_result,
        "playwright",
        True,
    )


# ============================================================
# RUN ONE SOURCE
# ============================================================


def diagnose_source(
    *,
    spec: SourceDiagnosticSpec,
    http_client: HttpClient,
    browser_client: BrowserClient,
    max_article_tests: int = 3,
) -> SourceDiagnosticResult:

    notes: list[str] = []

    article_results: list[
        ArticleDiagnostic
    ] = []

    browser_fallback_used = False

    # --------------------------------------------------------
    # FETCH SECTION
    # --------------------------------------------------------

    try:

        (
            section_result,
            section_method,
            used_browser,
        ) = fetch_with_fallback(
            url=spec.section_url,
            http_client=http_client,
            browser_client=browser_client,
        )

        browser_fallback_used = (
            browser_fallback_used
            or used_browser
        )

        section_fetch_success = (
            bool(
                section_result.html
            )
        )

        section_status_code = (
            section_result.status_code
        )

    except Exception as exc:

        return SourceDiagnosticResult(
            source_code=(
                spec.source_code
            ),
            source_name=(
                spec.name
            ),
            section_name=(
                spec.section_name
            ),
            section_url=(
                spec.section_url
            ),
            section_fetch_success=False,
            section_fetch_method=None,
            section_status_code=None,
            candidate_links_found=0,
            tested_articles=0,
            successful_articles=0,
            browser_fallback_used=False,
            article_results=[],
            notes=[
                (
                    f"SECTION_FETCH_FAILED: "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )
            ],
        )

    # --------------------------------------------------------
    # DISCOVER LINKS
    # --------------------------------------------------------

    candidates = (
        discover_candidate_links(
            html=section_result.html,
            spec=spec,
        )
    )

    if not candidates:

        notes.append(
            "NO_ARTICLE_LINKS_DISCOVERED"
        )

    # --------------------------------------------------------
    # TEST FIRST FEW INDIVIDUAL ARTICLES
    # --------------------------------------------------------

    for (
        article_url,
        listing_headline,
    ) in candidates[
        :max_article_tests
    ]:

        try:

            (
                article_result,
                fetch_method,
                used_browser,
            ) = fetch_with_fallback(
                url=article_url,
                http_client=http_client,
                browser_client=browser_client,
            )

            browser_fallback_used = (
                browser_fallback_used
                or used_browser
            )

            article = extract_article(
                html=article_result.html,
                source_name=spec.name,
                section_name=(
                    spec.section_name
                ),
                source_url=article_url,
                final_url=(
                    article_result.final_url
                ),
            )

            validation = (
                validate_article(
                    article
                )
            )

            article_results.append(
                ArticleDiagnostic(
                    url=article_url,
                    headline=(
                        listing_headline
                    ),
                    fetch_method=(
                        fetch_method
                    ),
                    status_code=(
                        article_result.status_code
                    ),
                    extracted_headline=(
                        article.headline
                    ),
                    published_at=(
                        article.published_at
                    ),
                    word_count=(
                        article.word_count
                    ),
                    valid=(
                        validation.valid
                    ),
                    errors=(
                        validation.errors
                    ),
                    warnings=(
                        validation.warnings
                    ),
                )
            )

        except Exception as exc:

            article_results.append(
                ArticleDiagnostic(
                    url=article_url,
                    headline=(
                        listing_headline
                    ),
                    fetch_method="FAILED",
                    status_code=None,
                    extracted_headline=None,
                    published_at=None,
                    word_count=0,
                    valid=False,
                    errors=[
                        (
                            f"{type(exc).__name__}: "
                            f"{exc}"
                        )
                    ],
                    warnings=[],
                )
            )

    successful_articles = sum(
        1
        for item in article_results
        if item.valid
    )

    # --------------------------------------------------------
    # ADD INTERPRETIVE NOTES
    # --------------------------------------------------------

    if browser_fallback_used:

        notes.append(
            "BROWSER_FALLBACK_REQUIRED_OR_USED"
        )

    if article_results:

        missing_dates = sum(
            1
            for item in article_results
            if not item.published_at
        )

        if missing_dates:

            notes.append(
                (
                    f"MISSING_PUBLICATION_DATE:"
                    f"{missing_dates}/"
                    f"{len(article_results)}"
                )
            )

    return SourceDiagnosticResult(
        source_code=(
            spec.source_code
        ),
        source_name=(
            spec.name
        ),
        section_name=(
            spec.section_name
        ),
        section_url=(
            spec.section_url
        ),
        section_fetch_success=(
            section_fetch_success
        ),
        section_fetch_method=(
            section_method
        ),
        section_status_code=(
            section_status_code
        ),
        candidate_links_found=(
            len(candidates)
        ),
        tested_articles=(
            len(article_results)
        ),
        successful_articles=(
            successful_articles
        ),
        browser_fallback_used=(
            browser_fallback_used
        ),
        article_results=(
            article_results
        ),
        notes=notes,
    )