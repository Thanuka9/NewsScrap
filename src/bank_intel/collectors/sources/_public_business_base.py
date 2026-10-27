from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Iterable
from urllib.parse import urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import httpx

from bank_intel.common.source_status import current_status_path
from bank_intel.common.discovery_policy import OldPageStopper
from bank_intel.common.collection_window import (
    get_collection_window,
)


def find_project_root() -> Path:
    """
    Locate the repository root from this module.

    This file lives under:
        <project>/src/bank_intel/collectors/sources/

    Do not rely on a fixed parents[N] index alone, because moving the
    package layout can silently redirect staged data into src/bank_intel.
    """

    start = Path(__file__).resolve()

    for candidate in start.parents:
        if (
            (candidate / "scripts").is_dir()
            and (candidate / "config").is_dir()
            and (candidate / "src").is_dir()
        ):
            return candidate

    raise RuntimeError(
        "Could not locate News Scrap project root from:\n"
        f"{start}"
    )


PROJECT_ROOT = find_project_root()
STAGED_ROOT = PROJECT_ROOT / "data" / "staged"
SRI_LANKA_TZ = ZoneInfo("Asia/Colombo")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# Do not treat the mere presence of the word "cloudflare" as a block.
# Real Daily Mirror pages legitimately include Cloudflare-related scripts.
# Challenge detection therefore uses specific challenge-page signatures.
BLOCK_MARKERS = (
    "checking your browser before accessing",
    "checking if the site connection is secure",
    "performing security verification",
    "enable javascript and cookies to continue",
    "javascript is required. please enable javascript before you are allowed to see this page",
    "you are being redirected",
    "cf-browser-verification",
)

BLOCK_TITLE_MARKERS = (
    "just a moment",
    "attention required",
    "access denied",
)

BODY_SELECTORS = (
    "[itemprop='articleBody']",
    ".article-body",
    ".article_body",
    ".article-content",
    ".article_content",
    ".article-detail",
    ".article_detail",
    ".entry-content",
    ".post-content",
    ".td-post-content",
    ".story-content",
    ".news-content",
    ".detail-content",
    "article",
)

NOISE_SELECTORS = (
    "script",
    "style",
    "noscript",
    "iframe",
    "form",
    "nav",
    "footer",
    "aside",
    ".advertisement",
    ".ads",
    ".ad",
    ".social-share",
    ".share",
    ".related",
    ".recommended",
    ".comments",
    ".comment",
)


@dataclass(frozen=True)
class FetchResult:
    requested_url: str
    final_url: str
    html: str
    status_code: int | None
    method: str


@dataclass
class ExtractedArticle:
    source_key: str
    source_name: str
    source_url: str
    headline: str
    published_at: datetime | None
    article_text: str
    author: str | None = None
    section: str | None = None
    fetched_via: str = "httpx"
    warnings: list[str] | None = None

    def published_date(self) -> date | None:
        return self.published_at.date() if self.published_at else None


def normalize_space(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    path = re.sub(r"/+", "/", parsed.path or "/")
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    return urlunparse((scheme, netloc, path, "", "", ""))


def is_block_page(html: str, status_code: int | None) -> bool:
    """
    Detect an actual access/challenge page without false-positive
    blocking legitimate pages that happen to load Cloudflare assets.
    """

    if status_code in {401, 403, 429, 503}:
        return True

    raw = html or ""
    low = raw.lower()

    if any(
        marker in low
        for marker in BLOCK_MARKERS
    ):
        return True

    # Challenge pages commonly expose their message in <title>.
    title_match = re.search(
        r"<title[^>]*>(.*?)</title>",
        raw,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if title_match:
        title = normalize_space(
            BeautifulSoup(
                title_match.group(1),
                "html.parser",
            ).get_text(
                " ",
                strip=True,
            )
        ).lower()

        if any(
            marker in title
            for marker in BLOCK_TITLE_MARKERS
        ):
            return True

    return False


def fetch_with_httpx(
    url: str,
    *,
    timeout: float = 30.0,
    follow_redirects: bool = True,
) -> FetchResult:
    with httpx.Client(
        headers=DEFAULT_HEADERS,
        timeout=timeout,
        follow_redirects=follow_redirects,
    ) as client:
        response = client.get(url)
        return FetchResult(
            requested_url=url,
            final_url=str(response.url),
            html=response.text,
            status_code=response.status_code,
            method="httpx",
        )


def fetch_with_playwright(
    url: str,
    *,
    timeout_ms: int = 45_000,
) -> FetchResult:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - optional runtime dependency
        raise RuntimeError(
            "Playwright fallback requested but playwright is not available."
        ) from exc

    with sync_playwright() as p:  # pragma: no cover - browser runtime
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=DEFAULT_HEADERS["User-Agent"],
            locale="en-US",
        )
        page = context.new_page()
        response = page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )
        try:
            page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass

        html = page.content()
        final_url = page.url
        status = response.status if response else None

        context.close()
        browser.close()

        return FetchResult(
            requested_url=url,
            final_url=final_url,
            html=html,
            status_code=status,
            method="playwright",
        )


def fetch_html(
    url: str,
    *,
    allow_browser_fallback: bool = True,
    timeout: float = 30.0,
) -> FetchResult:
    first_error: Exception | None = None
    try:
        result = fetch_with_httpx(
            url,
            timeout=timeout,
            follow_redirects=True,
        )
        if (
            result.status_code is not None
            and 200 <= result.status_code < 300
            and not is_block_page(result.html, result.status_code)
            and len(result.html or "") >= 500
        ):
            return result
    except Exception as exc:
        first_error = exc

    if not allow_browser_fallback:
        if first_error:
            raise first_error
        raise RuntimeError(f"HTTP fetch did not return usable HTML: {url}")

    try:
        result = fetch_with_playwright(url)
        if (
            result.status_code is not None
            and 200 <= result.status_code < 300
        ) and not is_block_page(result.html, result.status_code):
            return result
        raise RuntimeError(
            f"Browser fallback returned unusable page "
            f"(status={result.status_code}): {url}"
        )
    except Exception:
        if first_error:
            raise RuntimeError(
                f"Both HTTP and browser fetch failed for {url}: {first_error}"
            )
        raise


def _iter_json_objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_json_objects(child)


def _json_string_field(
    raw: str,
    field_name: str,
) -> str | None:
    """
    Best-effort extraction of one JSON string field from malformed
    JSON-LD. This is only a fallback when normal json.loads() fails.
    """

    pattern = re.compile(
        rf'"{re.escape(field_name)}"\s*:\s*"'
        rf'((?:\\.|[^"\\])*)"',
        flags=re.IGNORECASE | re.DOTALL,
    )

    match = pattern.search(raw)
    if not match:
        return None

    escaped_value = match.group(1)

    try:
        return json.loads(
            f'"{escaped_value}"'
        )
    except Exception:
        return (
            escaped_value
            .replace(r"\/", "/")
            .replace(r"\"", '"')
            .replace(r"\n", "\n")
            .replace(r"\r", "\r")
            .replace(r"\t", "\t")
        )


def _fallback_jsonld_article(
    raw: str,
) -> dict | None:
    """
    Recover the key article fields from JSON-LD that is present in the
    page but cannot be decoded as strict JSON.

    Daily Mirror has been observed by third-party scrapers to expose
    articleBody through JSON-LD, so losing a whole article because of a
    minor JSON-LD formatting issue is unnecessary.
    """

    article_body = _json_string_field(
        raw,
        "articleBody",
    )

    if not article_body:
        return None

    return {
        "@type": "NewsArticle",
        "headline": _json_string_field(
            raw,
            "headline",
        ),
        "datePublished": _json_string_field(
            raw,
            "datePublished",
        ),
        "articleBody": article_body,
    }


def jsonld_candidates(soup: BeautifulSoup) -> list[dict]:
    items: list[dict] = []

    # Use *= instead of exact equality. Some publishers append charset
    # or other parameters to the content type.
    for node in soup.select(
        "script[type*='ld+json' i]"
    ):
        raw = (
            node.string
            or node.get_text(
                "\n",
                strip=False,
            )
            or ""
        ).strip()

        if not raw:
            continue

        # Strip common wrappers that are legal in HTML but not JSON.
        cleaned = raw
        cleaned = re.sub(
            r"^\s*<!--",
            "",
            cleaned,
        )
        cleaned = re.sub(
            r"-->\s*$",
            "",
            cleaned,
        )
        cleaned = re.sub(
            r"^\s*<!\[CDATA\[",
            "",
            cleaned,
        )
        cleaned = re.sub(
            r"\]\]>\s*$",
            "",
            cleaned,
        )
        cleaned = cleaned.strip().rstrip(";").strip()

        payload = None

        try:
            payload = json.loads(cleaned)
        except Exception:
            fallback = _fallback_jsonld_article(
                cleaned
            )
            if fallback:
                items.append(fallback)
            continue

        for obj in _iter_json_objects(payload):
            if not isinstance(obj, dict):
                continue

            typ = obj.get("@type")
            types = (
                {typ}
                if isinstance(typ, str)
                else set(typ or [])
            )

            # Some sites omit @type on a nested object but still expose
            # articleBody. Keep that object as an article candidate.
            is_article_type = bool(
                {
                    "NewsArticle",
                    "Article",
                    "ReportageNewsArticle",
                    "BlogPosting",
                }
                & types
            )

            has_article_body = bool(
                normalize_space(
                    obj.get("articleBody")
                )
            )

            if (
                is_article_type
                or has_article_body
            ):
                items.append(obj)

    return items


def parse_datetime_text(value: str | None) -> datetime | None:
    text = normalize_space(value)
    if not text:
        return None

    iso = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=SRI_LANKA_TZ)
        else:
            parsed = parsed.astimezone(SRI_LANKA_TZ)
        return parsed
    except Exception:
        pass

    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=SRI_LANKA_TZ)
        else:
            parsed = parsed.astimezone(SRI_LANKA_TZ)
        return parsed
    except Exception:
        pass

    cleaned = re.sub(
        r"^(published|updated|date)\s*[:\-]\s*",
        "",
        text,
        flags=re.I,
    ).strip()

    formats = (
        "%d %B %Y %I:%M %p",
        "%d %b %Y %I:%M %p",
        "%d %B %Y %H:%M",
        "%d %b %Y %H:%M",
        "%d %B %Y",
        "%d %b %Y",
        "%B %d, %Y %I:%M %p",
        "%B %d, %Y",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d %I:%M %p",
    )

    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt).replace(
                tzinfo=SRI_LANKA_TZ
            )
        except ValueError:
            continue

    # Search inside strings such as:
    # "Published : 6 October 2026 06:15 pm Views - ..."
    match = re.search(
        r"(\d{1,2}\s+[A-Za-z]+\s+\d{4}"
        r"(?:\s+\d{1,2}:\d{2}\s*(?:am|pm))?)",
        text,
        flags=re.I,
    )
    if match:
        return parse_datetime_text(match.group(1))

    # The Island can render publication dates in strings such as:
    # "Published 2 hours ago on 2026/10/4".
    slash_date = re.search(
        r"\b(\d{4}/\d{1,2}/\d{1,2}"
        r"(?:\s+\d{1,2}:\d{2}"
        r"(?:\s*(?:am|pm))?)?)\b",
        text,
        flags=re.I,
    )
    if slash_date:
        return parse_datetime_text(
            slash_date.group(1)
        )

    return None


def extract_headline(soup: BeautifulSoup, jsonld: list[dict]) -> str:
    for obj in jsonld:
        value = normalize_space(obj.get("headline"))
        if value:
            return value

    for selector, attr in (
        ("meta[property='og:title']", "content"),
        ("meta[name='twitter:title']", "content"),
    ):
        node = soup.select_one(selector)
        if node:
            value = normalize_space(node.get(attr))
            if value:
                return value

    for selector in ("h1", ".entry-title", ".article-title", ".story-title"):
        node = soup.select_one(selector)
        if node:
            value = normalize_space(node.get_text(" ", strip=True))
            if value:
                return value

    return ""


def extract_author(soup: BeautifulSoup, jsonld: list[dict]) -> str | None:
    for obj in jsonld:
        author = obj.get("author")
        if isinstance(author, dict):
            value = normalize_space(author.get("name"))
            if value:
                return value
        if isinstance(author, list):
            names = []
            for item in author:
                if isinstance(item, dict):
                    name = normalize_space(item.get("name"))
                    if name:
                        names.append(name)
            if names:
                return "; ".join(names)

    node = soup.select_one(
        "meta[name='author'], meta[property='article:author']"
    )
    if node:
        value = normalize_space(node.get("content"))
        if value:
            return value

    for selector in (
        ".author",
        ".byline",
        "[rel='author']",
        ".entry-author",
    ):
        node = soup.select_one(selector)
        if node:
            value = normalize_space(node.get_text(" ", strip=True))
            if value:
                return value

    return None


def extract_published_at(
    soup: BeautifulSoup,
    jsonld: list[dict],
) -> datetime | None:
    for obj in jsonld:
        parsed = parse_datetime_text(
            obj.get("datePublished") or obj.get("dateCreated")
        )
        if parsed:
            return parsed

    meta_selectors = (
        "meta[property='article:published_time']",
        "meta[name='article:published_time']",
        "meta[name='date']",
        "meta[name='pubdate']",
        "meta[itemprop='datePublished']",
    )
    for selector in meta_selectors:
        node = soup.select_one(selector)
        if node:
            parsed = parse_datetime_text(
                node.get("content") or node.get("datetime")
            )
            if parsed:
                return parsed

    for selector in (
        "time[datetime]",
        "time",
        ".published",
        ".publish-date",
        ".post-date",
        ".entry-date",
        ".date",
    ):
        for node in soup.select(selector):
            parsed = parse_datetime_text(
                node.get("datetime") or node.get_text(" ", strip=True)
            )
            if parsed:
                return parsed

    # Normalize whitespace before running publication-date regexes.
    # BeautifulSoup can preserve embedded newlines between text nodes even
    # when a separator is supplied, which breaks regexes using "." across
    # strings such as:
    #   Published\n2 hours ago\non\n2026/10/4
    body_text = normalize_space(
        soup.get_text(
            " ",
            strip=True,
        )
    )
    published_match = re.search(
        r"Published\s*:\s*"
        r"(\d{1,2}\s+[A-Za-z]+\s+\d{4}"
        r"(?:\s+\d{1,2}:\d{2}\s*(?:am|pm))?)",
        body_text,
        flags=re.I,
    )
    if published_match:
        return parse_datetime_text(published_match.group(1))

    # Final source-specific fallback for The Island.
    #
    # Do not depend on one exact phrase such as
    # "Published ... on YYYY/M/D". The live markup can insert relative
    # time text, spans, or punctuation between "Published" and the
    # calendar date. Instead, inspect only a short window immediately
    # after the word "Published" and let parse_datetime_text() recover
    # any supported date representation from that window.
    published_window_match = re.search(
        r"\bPublished\b(.{0,180})",
        body_text,
        flags=re.I,
    )

    if published_window_match:
        parsed = parse_datetime_text(
            published_window_match.group(0)
        )
        if parsed:
            return parsed

    return None


def _clean_body_node(node, stop_markers: Iterable[str]) -> str:
    fragment = BeautifulSoup(str(node), "html.parser")

    for selector in NOISE_SELECTORS:
        for bad in fragment.select(selector):
            bad.decompose()

    markers = tuple(x.lower() for x in stop_markers)

    blocks = []
    for element in fragment.select("p, h2, h3, li"):
        text = normalize_space(element.get_text(" ", strip=True))
        if not text:
            continue

        low = text.lower()
        if any(low.startswith(marker) for marker in markers):
            break

        # Remove obvious boilerplate / tiny labels.
        if len(text) < 2:
            continue

        blocks.append(text)

    if blocks:
        return "\n\n".join(blocks)

    return normalize_space(fragment.get_text(" ", strip=True))


def _paragraph_cluster_fallback(
    soup: BeautifulSoup,
    *,
    stop_markers: Iterable[str] = (),
) -> str:
    """
    Generic last-resort article-body recovery.

    Scores containers by substantive paragraph content while penalising
    navigation/link-heavy blocks. This is safer than returning the whole
    page and is useful for publishers whose article wrapper has an
    unstable or non-semantic CSS class.
    """

    bad_tokens = (
        "related",
        "recommend",
        "comment",
        "footer",
        "header",
        "navbar",
        "navigation",
        "sidebar",
        "social",
        "share",
        "advert",
        "latest",
        "popular",
        "trending",
    )

    candidates: list[tuple[float, str]] = []

    for node in soup.select(
        "article, main, section, div"
    ):
        classes = " ".join(
            node.get("class") or []
        ).lower()
        node_id = str(
            node.get("id") or ""
        ).lower()
        signature = (
            classes
            + " "
            + node_id
        )

        if any(
            token in signature
            for token in bad_tokens
        ):
            continue

        paragraphs = []

        for paragraph in node.find_all(
            "p",
            recursive=True,
        ):
            text = normalize_space(
                paragraph.get_text(
                    " ",
                    strip=True,
                )
            )

            # Ignore tiny labels/captions and keep real prose.
            if len(text.split()) < 12:
                continue

            paragraphs.append(text)

        if not paragraphs:
            continue

        # De-duplicate repeated nested text while preserving order.
        unique_paragraphs = list(
            dict.fromkeys(paragraphs)
        )

        body = "\n\n".join(
            unique_paragraphs
        )

        body_words = len(
            body.split()
        )

        if body_words < 80:
            continue

        link_words = 0
        for link in node.find_all(
            "a",
            recursive=True,
        ):
            link_words += len(
                normalize_space(
                    link.get_text(
                        " ",
                        strip=True,
                    )
                ).split()
            )

        link_density = (
            link_words
            / max(
                body_words,
                1,
            )
        )

        if link_density > 0.35:
            continue

        # Prefer lots of prose and low link density.
        score = (
            body_words
            * (
                1.0
                - min(
                    link_density,
                    0.9,
                )
            )
        )

        cleaned = _clean_body_node(
            node,
            stop_markers,
        )

        if len(
            cleaned.split()
        ) < 80:
            continue

        candidates.append(
            (
                score,
                cleaned,
            )
        )

    if not candidates:
        return ""

    candidates.sort(
        key=lambda item: (
            item[0],
            len(item[1]),
        ),
        reverse=True,
    )

    return candidates[0][1]


def extract_article_text(
    soup: BeautifulSoup,
    jsonld: list[dict],
    *,
    stop_markers: Iterable[str] = (),
) -> str:
    # 1. Structured article text is the cleanest source.
    for obj in jsonld:
        body = normalize_space(
            obj.get("articleBody")
        )

        if len(
            body.split()
        ) >= 80:
            return body

    # 2. Known semantic article wrappers.
    best = ""

    for selector in BODY_SELECTORS:
        for node in soup.select(selector):
            text = _clean_body_node(
                node,
                stop_markers,
            )

            if len(text) > len(best):
                best = text

    if len(
        best.split()
    ) >= 80:
        return best

    # 3. Last resort: score prose-heavy paragraph containers.
    fallback = _paragraph_cluster_fallback(
        soup,
        stop_markers=stop_markers,
    )

    if len(
        fallback.split()
    ) > len(
        best.split()
    ):
        return fallback

    return best


def extract_generic_article(
    *,
    source_key: str,
    source_name: str,
    source_url: str,
    fetch: FetchResult,
    section: str,
    stop_markers: Iterable[str] = (),
) -> ExtractedArticle:
    soup = BeautifulSoup(fetch.html, "html.parser")
    jsonld = jsonld_candidates(soup)

    headline = extract_headline(soup, jsonld)
    published_at = extract_published_at(soup, jsonld)
    author = extract_author(soup, jsonld)
    article_text = extract_article_text(
        soup,
        jsonld,
        stop_markers=stop_markers,
    )

    warnings: list[str] = []
    if not headline:
        warnings.append("HEADLINE_MISSING")
    if published_at is None:
        warnings.append("PUBLISHED_DATE_MISSING")
    if len(article_text.split()) < 80:
        warnings.append(
            f"ARTICLE_TOO_SHORT:{len(article_text.split())}"
        )

    return ExtractedArticle(
        source_key=source_key,
        source_name=source_name,
        source_url=canonicalize_url(fetch.final_url or source_url),
        headline=headline,
        published_at=published_at,
        article_text=article_text,
        author=author,
        section=section,
        fetched_via=fetch.method,
        warnings=warnings,
    )


def article_id_for_url(url: str) -> str:
    return hashlib.sha256(
        canonicalize_url(url).encode("utf-8")
    ).hexdigest()[:20]


def content_hash(article: ExtractedArticle) -> str:
    payload = "\n".join(
        (
            normalize_space(article.headline),
            article.published_at.isoformat() if article.published_at else "",
            normalize_space(article.article_text),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _latest_version(article_dir: Path) -> tuple[int, dict | None]:
    versions = sorted(article_dir.glob("v*.json"))
    if not versions:
        return 0, None

    latest_path = versions[-1]
    try:
        payload = json.loads(latest_path.read_text(encoding="utf-8"))
    except Exception:
        return len(versions), None

    try:
        version = int(payload.get("article_version") or 0)
    except Exception:
        version = 0

    if version <= 0:
        match = re.search(r"v(\d+)\.json$", latest_path.name, re.I)
        version = int(match.group(1)) if match else len(versions)

    return version, payload


def stage_article(
    article: ExtractedArticle,
    *,
    collected_at: datetime,
) -> tuple[str, Path | None]:
    if article.published_at is None:
        return "MISSING_DATE", None

    publication_date = article.published_at.date().isoformat()
    article_id = article_id_for_url(article.source_url)
    article_dir = (
        STAGED_ROOT
        / article.source_key
        / publication_date
        / article_id
    )
    article_dir.mkdir(parents=True, exist_ok=True)

    new_hash = content_hash(article)
    latest_version, latest_payload = _latest_version(article_dir)

    if latest_payload and latest_payload.get("content_hash") == new_hash:
        return "UNCHANGED", None

    next_version = latest_version + 1
    status = "NEW" if latest_version == 0 else "CHANGED"

    payload = {
        "source_key": article.source_key,
        "source_name": article.source_name,
        "section": article.section,
        "article_id": article_id,
        "article_version": next_version,
        "source_url": canonicalize_url(article.source_url),
        "headline": article.headline,
        "published_at": article.published_at.isoformat(),
        "author": article.author,
        "article_text": article.article_text,
        # NEWS_LATE_DISCOVERY_METADATA_V3
        "collection_status": status,
        "intake_status": (
            "LATE_DISCOVERY"
            if status == "NEW"
            and article.published_at.date() < collected_at.date()
            else (
                "UPDATED_ARTICLE"
                if status == "CHANGED"
                else "NEW_TODAY"
            )
        ),
        "collection_run_date": collected_at.date().isoformat(),
        "collected_at": collected_at.isoformat(),
        "fetched_via": article.fetched_via,
        "content_hash": new_hash,
        "extraction_warnings": list(article.warnings or []),
    }

    output = article_dir / f"v{next_version:03d}.json"
    output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return status, output


def parse_cli_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def default_window() -> tuple[date, date]:
    window = get_collection_window()

    return (
        window.start_date,
        window.end_date,
    )


def write_source_status(source_key: str, payload: dict) -> Path:
    path = current_status_path(source_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


class BusinessSourceAdapter:
    source_key: str
    source_name: str
    section_name: str
    section_url: str
    allow_browser_fallback: bool = True
    stop_markers: tuple[str, ...] = ()

    def listing_url(self, page_number: int) -> str:
        raise NotImplementedError

    def discover_article_urls(self, html: str, base_url: str) -> list[str]:
        raise NotImplementedError

    def is_article_url(self, url: str) -> bool:
        raise NotImplementedError

    def fetch(self, url: str) -> FetchResult:
        return fetch_html(
            url,
            allow_browser_fallback=self.allow_browser_fallback,
        )

    def extract(self, url: str, fetch: FetchResult) -> ExtractedArticle:
        return extract_generic_article(
            source_key=self.source_key,
            source_name=self.source_name,
            source_url=url,
            fetch=fetch,
            section=self.section_name,
            stop_markers=self.stop_markers,
        )


def run_collection(
    adapter: BusinessSourceAdapter,
    *,
    start_date: date,
    end_date: date,
    max_pages: int,
    sleep_seconds: float = 0.2,
    start_page: int = 1,
    extra_surfaces: list[tuple[str, object, int]] | None = None,
) -> dict:
    if start_date > end_date:
        raise ValueError("start_date cannot be later than end_date")
    if max_pages < 1:
        raise ValueError("max_pages must be >= 1")
    if start_page < 1:
        raise ValueError("start_page must be >= 1")

    started_at = datetime.now(SRI_LANKA_TZ)
    run_id = os.environ.get("BANK_INTEL_RUN_ID") or (
        started_at.strftime("%Y%m%d_%H%M%S") + "_standalone"
    )

    seen_urls: set[str] = set()

    counts = {
        "candidate_urls": 0,
        "new_articles": 0,
        "late_discoveries": 0,
        "changed_articles": 0,
        "unchanged_articles": 0,
        "wrong_date_skipped": 0,
        "missing_date": 0,
        "invalid_extraction": 0,
        "short_publisher_notices": 0,
        "fetch_failures": 0,
        "recent_duplicates": 0,
    }

    consecutive_empty_pages = 0
    old_pages = OldPageStopper(start_date)
    surfaces_report: list[dict] = []
    failed_article_urls: list[str] = []
    stop_reason = "MAX_PAGES_REACHED"
    last_page_attempted = start_page - 1

    print()
    print("=" * 72)
    print(f"{adapter.source_name.upper()} BUSINESS COLLECTOR")
    print("=" * 72)
    print(f"Run time : {started_at.isoformat()}")
    print(f"From     : {start_date}")
    print(f"To       : {end_date}")
    print(f"Start page: {start_page}")
    print(f"Page count: {max_pages}")

    final_page = (
        start_page
        + max_pages
        - 1
    )

    surfaces = [(adapter.section_name, adapter.listing_url, max_pages)]
    if extra_surfaces:
        surfaces.extend(extra_surfaces)
    for surface_name, make_page_url, page_count in surfaces:
        consecutive_empty_pages = 0
        old_pages = OldPageStopper(start_date)
        before_count = counts["candidate_urls"]
        surface_start_page = start_page if surface_name == adapter.section_name else 1
        final_page = surface_start_page + page_count - 1
        stop_reason = "MAX_PAGES_REACHED"
        print(f"[COLLECTION SURFACE] {surface_name}")
        for page_number in range(
            surface_start_page,
            final_page + 1,
        ):
            last_page_attempted = page_number
            listing_url = make_page_url(page_number)
            print()
            print(f"[DISCOVERY] Page {page_number}")
            print(f"            {listing_url}")

            try:
                listing = adapter.fetch(listing_url)
            except Exception as exc:
                print(f"            LISTING FETCH FAILED: {exc}")
                counts["fetch_failures"] += 1
                consecutive_empty_pages += 1
                if consecutive_empty_pages >= 2:
                    stop_reason = "CONSECUTIVE_LISTING_FAILURES"
                    break
                continue

            urls = adapter.discover_article_urls(
                listing.html,
                listing.final_url or listing_url,
            )
            urls = [
                canonicalize_url(url)
                for url in urls
                if adapter.is_article_url(url)
            ]

            new_urls = []
            for url in urls:
                if url in seen_urls:
                    counts["recent_duplicates"] += 1
                    continue
                seen_urls.add(url)
                new_urls.append(url)

            counts["candidate_urls"] += len(new_urls)
            print(f"            Articles discovered: {len(new_urls)}")

            if not new_urls:
                consecutive_empty_pages += 1
                if consecutive_empty_pages >= 2:
                    stop_reason = "NO_NEW_URLS"
                    break
                continue

            consecutive_empty_pages = 0
            page_dates: list[date] = []

            for index, url in enumerate(new_urls, start=1):
                print(
                    f"            [{index}/{len(new_urls)}] "
                    f"{url}"
                )

                # Island's official RSS provides dated URLs in the feed. Use
                # that date BEFORE fetching full content; otherwise dozens
                # of old short notices trigger false fetch failures and stop
                # safe early pagination. Normal website adapters are unchanged.
                get_date = getattr(adapter, "listing_publication_date", None)
                hint_date = get_date(url) if callable(get_date) else None
                if hint_date is not None:
                    page_dates.append(hint_date)
                    if hint_date < start_date or hint_date > end_date:
                        counts["wrong_date_skipped"] += 1
                        print(f"              SKIP: {hint_date} outside requested window (RSS date)")
                        continue

                try:
                    fetched = adapter.fetch(url)
                    article = adapter.extract(url, fetched)
                except Exception as exc:
                    failed_article_urls.append(url)
                    print(f"              FETCH/EXTRACT FAILED (queued retry): {exc}")
                    continue

                if article.published_at is None:
                    counts["missing_date"] += 1
                    print("              SKIP: publication date missing")
                    continue

                publication_date = article.published_at.date()
                if hint_date is None:
                    page_dates.append(publication_date)
                elif publication_date != hint_date:
                    # A feed/body date conflict must not support an early stop.
                    page_dates.pop()
                    page_dates.append(publication_date)

                if publication_date < start_date or publication_date > end_date:
                    counts["wrong_date_skipped"] += 1
                    print(
                        f"              SKIP: {publication_date} "
                        f"outside requested window"
                    )
                    continue

                word_count = len(article.article_text.split())
                short_official_notice = (
                    getattr(fetched, "method", None) == "rss-full-content"
                    and article.published_at is not None
                    and bool(article.headline)
                    and 25 <= word_count < 80
                )
                if not article.headline or (word_count < 80 and not short_official_notice):
                    counts["invalid_extraction"] += 1
                    print(
                        "              SKIP: invalid extraction "
                        f"{article.warnings}"
                    )
                    continue

                if short_official_notice:
                    counts["short_publisher_notices"] += 1
                    if "SHORT_OFFICIAL_RSS_NOTICE_REVIEW" not in article.warnings:
                        article.warnings.append("SHORT_OFFICIAL_RSS_NOTICE_REVIEW")
                    print(f"              REVIEW: short official RSS notice ({word_count} words)")

                stage_status, output = stage_article(
                    article,
                    collected_at=datetime.now(SRI_LANKA_TZ),
                )

                if stage_status == "NEW":
                    counts["new_articles"] += 1
                    if publication_date < started_at.date():
                        counts["late_discoveries"] += 1
                elif stage_status == "CHANGED":
                    counts["changed_articles"] += 1
                elif stage_status == "UNCHANGED":
                    counts["unchanged_articles"] += 1

                print(
                    f"              {stage_status}: "
                    f"{article.headline[:110]}"
                )
                if output:
                    print(f"              {output}")

                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)

            # Listings are normally newest -> oldest. Once a page has
            # several dated articles and even its newest article predates
            # the requested window, historical pagination can stop safely.
            # Never stop merely because one old article appears among new ones.
            # An extraction failure/unknown date leaves the page incomplete.
            page_unknown = len(new_urls) - len(page_dates)
            if old_pages.observe(page_dates, unknown_dates=page_unknown):
                stop_reason = "TWO_CONFIRMED_OLDER_PAGES"
                break

        surfaces_report.append({
            "name": surface_name,
            "discovered_unique_urls": counts["candidate_urls"] - before_count,
            "stop_reason": stop_reason,
            "last_page_attempted": last_page_attempted,
        })

    # Focused final retry: only URLs that failed, never the complete listing.
    # Persist unresolved URLs in the source status for targeted recovery.
    unresolved: list[str] = []
    for url in dict.fromkeys(failed_article_urls):
        if sleep_seconds >= 0:
            time.sleep(max(2.0, sleep_seconds))
        try:
            fetched = adapter.fetch(url)
            article = adapter.extract(url, fetched)
            if article.published_at is None:
                counts["missing_date"] += 1
                continue
            publication_date = article.published_at.date()
            if not (start_date <= publication_date <= end_date):
                counts["wrong_date_skipped"] += 1
                continue
            word_count = len(article.article_text.split())
            short_official_notice = (
                getattr(fetched, "method", None) == "rss-full-content"
                and bool(article.headline)
                and 25 <= word_count < 80
            )
            if not article.headline or (word_count < 80 and not short_official_notice):
                counts["invalid_extraction"] += 1
                continue
            if short_official_notice:
                counts["short_publisher_notices"] += 1
                if "SHORT_OFFICIAL_RSS_NOTICE_REVIEW" not in article.warnings:
                    article.warnings.append("SHORT_OFFICIAL_RSS_NOTICE_REVIEW")
            stage_status, _output = stage_article(
                article, collected_at=datetime.now(SRI_LANKA_TZ),
            )
            if stage_status == "NEW":
                counts["new_articles"] += 1
                if publication_date < started_at.date():
                    counts["late_discoveries"] += 1
            elif stage_status == "CHANGED":
                counts["changed_articles"] += 1
            elif stage_status == "UNCHANGED":
                counts["unchanged_articles"] += 1
            print(f"[RECOVERED URL] {url}: {stage_status}")
        except Exception as exc:
            unresolved.append(url)
            print(f"[UNRESOLVED URL] {url}: {exc}")
    counts["fetch_failures"] += len(unresolved)

    completed_at = datetime.now(SRI_LANKA_TZ)

    # If a source produced candidates but every candidate failed to
    # fetch/extract, treat it as failed rather than silently healthy.
    accepted_articles = (
        counts["new_articles"]
        + counts["changed_articles"]
        + counts["unchanged_articles"]
    )

    all_content_fail = (
        counts["candidate_urls"] > 0
        and accepted_articles == 0
        and (
            counts["fetch_failures"]
            + counts["invalid_extraction"]
            + counts["missing_date"]
        ) > 0
    )

    listing_failure = (
        counts["candidate_urls"] == 0
        and stop_reason
        in {
            "CONSECUTIVE_LISTING_FAILURES",
            "NO_NEW_URLS",
        }
    )

    hard_failure = (
        all_content_fail
        or listing_failure
    )

    execution_status = (
        "FAILED"
        if hard_failure
        else "SUCCESS"
    )

    if hard_failure:
        health_status = "FAILED"
    elif (
        counts["fetch_failures"]
        or counts["invalid_extraction"]
        or counts["missing_date"]
    ):
        health_status = "PARTIAL_ERROR"
    else:
        health_status = "HEALTHY"

    if listing_failure:
        content_status = "LISTING_DISCOVERY_FAILED"
    elif counts["new_articles"] or counts["changed_articles"]:
        content_status = "NEW_OR_CHANGED_ARTICLES"
    elif counts["candidate_urls"] == 0:
        content_status = "NO_CANDIDATES"
    else:
        content_status = "NO_NEW_ARTICLES"

    status = {
        "run_id": run_id,
        "source_key": adapter.source_key,
        "source_name": adapter.source_name,
        "execution_status": execution_status,
        "health_status": health_status,
        "content_status": content_status,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "collection_mode": "DATE_WINDOW",
        "from_date": start_date.isoformat(),
        "to_date": end_date.isoformat(),
        "start_page": start_page,
        "max_pages": max_pages,
        "last_page_attempted": last_page_attempted,
        "stop_reason": stop_reason,
        "discovery_surfaces": surfaces_report,
        "discovered_urls": sorted(seen_urls),
        "unresolved_fetch_urls": unresolved,
        "recovered_failed_urls": len(set(failed_article_urls)) - len(unresolved),
        **counts,
    }

    status_path = write_source_status(
        adapter.source_key,
        status,
    )

    print()
    print("=" * 72)
    print(f"{adapter.source_name.upper()} COLLECTION COMPLETE")
    print("=" * 72)
    for key, value in status.items():
        if key in {
            "run_id",
            "source_key",
            "source_name",
            "started_at",
            "completed_at",
        }:
            continue
        print(f"{key:24}: {value}")
    print(f"status_file             : {status_path}")

    return status
