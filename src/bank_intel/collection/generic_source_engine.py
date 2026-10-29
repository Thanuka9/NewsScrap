from __future__ import annotations

from dataclasses import dataclass, asdict
from bank_intel.common.discovery_policy import OldPageStopper, publication_date_in_url
from datetime import datetime, date, timedelta
from hashlib import sha1, sha256
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo
import html
import json
import re

try:
    import httpx
except Exception as exc:
    httpx = None
    HTTPX_IMPORT_ERROR = str(exc)
else:
    HTTPX_IMPORT_ERROR = ""

try:
    from bs4 import BeautifulSoup
except Exception as exc:
    BeautifulSoup = None
    BS4_IMPORT_ERROR = str(exc)
else:
    BS4_IMPORT_ERROR = ""

SRI_LANKA_TZ = ZoneInfo("Asia/Colombo")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/155.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

BLOCK_MARKERS = (
    "access denied",
    "403 forbidden",
    "request blocked",
    "captcha",
    "cf-chl-",
    "just a moment",
)

COMMON_EXCLUDE = re.compile(
    r"/(?:category|categories|tag|tags|author|authors|search|login|signin|"
    r"signup|privacy|terms|contact|about|video|videos|live)(?:/|$)",
    re.I,
)

DATE_PATH = re.compile(r"/20\d{2}/\d{1,2}/\d{1,2}/")


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int | None
    text: str
    method: str
    blocked: bool
    error: str = ""


@dataclass
class ArticleResult:
    url: str
    valid: bool
    headline: str = ""
    published_at: str = ""
    author: str = ""
    article_text: str = ""
    canonical_url: str = ""
    word_count: int = 0
    fetch_method: str = ""
    fetch_status: int | None = None
    blocked: bool = False
    errors: list[str] | None = None
    warnings: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["errors"] = value.get("errors") or []
        return value


def _require_parsers() -> None:
    missing = []
    if httpx is None:
        missing.append(f"httpx ({HTTPX_IMPORT_ERROR})")
    if BeautifulSoup is None:
        missing.append(f"beautifulsoup4 ({BS4_IMPORT_ERROR})")
    if missing:
        raise RuntimeError(
            "Missing Python package(s): "
            + ", ".join(missing)
            + "\nInstall inside .venv with:\n"
            + "python -m pip install httpx beautifulsoup4 pyyaml"
        )


def _blocked(status: int | None, text: str) -> bool:
    if status in {401, 403, 407, 429}:
        return True
    sample = (text or "")[:12000].lower()
    return any(marker in sample for marker in BLOCK_MARKERS)


def _browser_fetch(url: str, timeout_ms: int = 45000) -> FetchResult:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return FetchResult(
            url=url,
            final_url=url,
            status=None,
            text="",
            method="playwright",
            blocked=False,
            error=f"PLAYWRIGHT_UNAVAILABLE: {exc}",
        )

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=DEFAULT_HEADERS["User-Agent"],
                locale="en-US",
                timezone_id="Asia/Colombo",
                viewport={"width": 1440, "height": 1200},
            )
            page = context.new_page()
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            page.wait_for_timeout(1200)
            text = page.content()
            final_url = page.url
            status = response.status if response else None
            browser.close()

        return FetchResult(
            url=url,
            final_url=final_url,
            status=status,
            text=text,
            method="playwright",
            blocked=_blocked(status, text),
        )
    except Exception as exc:
        return FetchResult(
            url=url,
            final_url=url,
            status=None,
            text="",
            method="playwright",
            blocked=False,
            error=f"PLAYWRIGHT_FETCH_FAILED: {exc}",
        )


def _http_fetch(url: str, timeout: float = 30.0) -> FetchResult:
    _require_parsers()
    try:
        with httpx.Client(
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
            timeout=timeout,
            http2=False,
        ) as client:
            response = client.get(url)
        text = response.text or ""
        return FetchResult(
            url=url,
            final_url=str(response.url),
            status=response.status_code,
            text=text,
            method="httpx",
            blocked=_blocked(response.status_code, text),
        )
    except Exception as exc:
        return FetchResult(
            url=url,
            final_url=url,
            status=None,
            text="",
            method="httpx",
            blocked=False,
            error=f"HTTP_FETCH_FAILED: {exc}",
        )


def fetch_page(
    url: str,
    *,
    prefer_browser: bool = False,
    require_regex: str | None = None,
) -> FetchResult:
    attempts = []
    order = ("browser", "http") if prefer_browser else ("http", "browser")

    for method in order:
        result = _browser_fetch(url) if method == "browser" else _http_fetch(url)
        attempts.append(result)

        acceptable = (
            not result.error
            and not result.blocked
            and result.status is not None
            and 200 <= result.status < 400
            and len(result.text) >= 200
        )
        if acceptable and require_regex:
            acceptable = bool(re.search(require_regex, result.text, re.I | re.S))

        if acceptable:
            return result

    for result in attempts:
        if result.blocked:
            return result

    return max(attempts, key=lambda x: len(x.text or ""))


def canonicalize_url(url: str) -> str:
    p = urlparse(url.strip())
    scheme = (p.scheme or "https").lower()
    host = p.netloc.lower()
    if host.endswith(":443"):
        host = host[:-4]
    path = re.sub(r"/{2,}", "/", p.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunparse((scheme, host, path, "", "", ""))


def same_site(a: str, b: str) -> bool:
    def host(v: str) -> str:
        h = urlparse(v).netloc.lower().split(":")[0]
        return h[4:] if h.startswith("www.") else h
    return host(a) == host(b)


def looks_like_article_url(url: str, listing_url: str) -> bool:
    if not same_site(url, listing_url):
        return False
    p = urlparse(url)
    path = p.path or "/"
    if path.rstrip("/") == urlparse(listing_url).path.rstrip("/"):
        return False
    if COMMON_EXCLUDE.search(path):
        return False
    if "/articles/" in path.lower():
        return True
    if DATE_PATH.search(path):
        return True
    if re.search(r"/\d{4,}/", path):
        return True
    parts = [x for x in path.split("/") if x]
    return len(parts) >= 2 and len(parts[-1]) >= 18


def _discover_article_links_single(
    profile: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    _require_parsers()

    listing_url = str(profile["listing_url"])
    include = [
        re.compile(x, re.I)
        for x in (profile.get("article_url_patterns") or [])
    ]
    exclude = [
        re.compile(x, re.I)
        for x in (profile.get("exclude_url_patterns") or [])
    ]

    result = fetch_page(
        listing_url,
        prefer_browser=bool(profile.get("listing_prefer_browser", False)),
    )

    diagnostics = {
        "listing_url": listing_url,
        "listing_fetch_method": result.method,
        "listing_status": result.status,
        "listing_final_url": result.final_url,
        "listing_blocked": result.blocked,
        "listing_error": result.error,
        "raw_links": 0,
        "article_links": 0,
    }

    if result.error or result.blocked or not result.text:
        return [], diagnostics

    soup = BeautifulSoup(result.text, "html.parser")
    raw: list[str] = []

    for tag in soup.find_all("a", href=True):
        value = urljoin(result.final_url, str(tag.get("href") or "").strip())
        if value.startswith(("http://", "https://")):
            raw.append(value)

    for tag in soup.find_all("link"):
        if tag.get("href"):
            value = urljoin(result.final_url, str(tag.get("href")).strip())
        else:
            value = (tag.get_text(" ", strip=True) or "").strip()
        if value.startswith(("http://", "https://")):
            raw.append(value)

    raw.extend(
        re.findall(
            r'https?://[^"\'<>\s\\]+',
            result.text,
            flags=re.I,
        )
    )

    diagnostics["raw_links"] = len(raw)

    accepted: list[str] = []
    seen = set()

    for raw_url in raw:
        url = html.unescape(raw_url).replace("\\u002F", "/").replace("\\/", "/")
        url = canonicalize_url(url)

        if url in seen:
            continue
        seen.add(url)

        if not same_site(url, listing_url):
            continue
        if any(rx.search(url) for rx in exclude):
            continue

        if include:
            if not any(rx.search(url) for rx in include):
                continue
        elif not looks_like_article_url(url, listing_url):
            continue

        accepted.append(url)

    skip_prefix = int(profile.get("skip_first_n_article_links", 0) or 0)
    if skip_prefix > 0:
        diagnostics["article_links_before_prefix_filter"] = len(accepted)
        diagnostics["article_links_skipped_prefix"] = min(
            skip_prefix,
            len(accepted),
        )
        accepted = accepted[skip_prefix:]

    diagnostics["article_links"] = len(accepted)
    return accepted, diagnostics


def _pagination_date_links(html_text: str, base_url: str, profile: dict) -> list[str]:
    """Date-boundary evidence excludes explicitly configured featured panels."""
    soup = BeautifulSoup(html_text, "html.parser")
    for selector in profile.get("pagination_date_exclude_selectors") or []:
        for node in soup.select(selector):
            node.decompose()
    patterns = [re.compile(value) for value in profile.get("article_url_patterns") or []]
    links = []
    for anchor in soup.select('a[href]'):
        url = canonicalize_url(urljoin(base_url, anchor.get('href') or ''))
        if same_site(url, base_url) and (not patterns or any(p.search(url) for p in patterns)):
            links.append(url)
    return list(dict.fromkeys(links))


def _discover_browser_click_pages(
    profile: dict[str, Any],
    listing_url: str,
    max_pages: int,
) -> tuple[list[str], dict[str, Any]]:
    from urllib.parse import urljoin
    from playwright.sync_api import sync_playwright

    patterns = [re.compile(value) for value in (profile.get("article_url_patterns") or [])]
    combined: list[str] = []
    seen: set[str] = set()
    page_diagnostics: list[dict[str, Any]] = []
    date_window_start = profile.get("discovery_start_date")
    old_pages = OldPageStopper(date.fromisoformat(str(date_window_start))) if date_window_start else None
    status = None
    final_url = listing_url
    error = ""
    blocked = False

    def extract_links(html_text: str, base_url: str) -> list[str]:
        soup = BeautifulSoup(html_text, "html.parser")
        found: list[str] = []
        local_seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            absolute = canonicalize_url(urljoin(base_url, anchor.get("href") or ""))
            if not absolute or absolute in local_seen:
                continue
            if patterns and not any(pattern.search(absolute) for pattern in patterns):
                continue
            local_seen.add(absolute)
            found.append(absolute)
        return found

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/155.0.0.0 Safari/537.36"
                )
            )
            response = page.goto(listing_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1200)
            status = response.status if response is not None else None
            final_url = page.url

            for page_number in range(1, max(1, max_pages) + 1):
                current_html = page.content()
                current_links = extract_links(current_html, page.url)
                added = 0
                for link in current_links:
                    if link in seen:
                        continue
                    seen.add(link)
                    combined.append(link)
                    added += 1

                page_diagnostics.append({
                    "page": page_number,
                    "url": page.url,
                    "article_links_on_page": len(current_links),
                    "new_unique_links": added,
                })

                if old_pages is not None:
                    boundary_links = _pagination_date_links(current_html, page.url, profile)
                    page_diagnostics[-1]["date_boundary_links"] = boundary_links
                    known_dates = [publication_date_in_url(url) for url in boundary_links]
                    unknown_count = sum(value is None for value in known_dates)
                    if old_pages.observe(known_dates, unknown_dates=unknown_count):
                        page_diagnostics[-1]["stop_reason"] = "TWO_OLDER_URL_DATED_PAGES"
                        break

                if page_number >= max_pages:
                    page_diagnostics[-1]["stop_reason"] = "PAGE_LIMIT_REACHED"
                    break

                before = set(current_links)
                clicked = False

                selectors = [
                    "button[aria-label*=\"next page\" i]",
                    "button[aria-label*=\"next\" i]",
                    "[role=\"button\"][aria-label*=\"next page\" i]",
                    ".MuiPaginationItem-next",
                    "button:has-text(\"Next page\")",
                    "button:has-text(\"Next\")",
                ]
                for selector in selectors:
                    locator = page.locator(selector)
                    if locator.count() <= 0:
                        continue
                    candidate = locator.first
                    try:
                        if not candidate.is_visible() or not candidate.is_enabled():
                            continue
                        candidate.click(timeout=5000)
                        clicked = True
                        break
                    except Exception:
                        continue

                if not clicked:
                    # Material-UI pagination often exposes only numbered buttons.
                    next_number = str(page_number + 1)
                    candidates = [
                        page.get_by_role("button", name=next_number, exact=True),
                        page.locator(f"button:has-text(\"{next_number}\")"),
                    ]
                    for candidate in candidates:
                        try:
                            if candidate.count() > 0 and candidate.first.is_visible() and candidate.first.is_enabled():
                                candidate.first.click(timeout=5000)
                                clicked = True
                                break
                        except Exception:
                            continue

                if not clicked:
                    # NEWSFIRST_TEXT_PAGINATION_FALLBACK_V3
                    labels = [
                        "Next page",
                        "Next",
                        str(page_number + 1),
                    ]

                    for label in labels:
                        try:
                            text_locator = page.get_by_text(
                                label,
                                exact=True,
                            )
                            if text_locator.count() <= 0:
                                continue

                            text_node = text_locator.first
                            ancestor = text_node.locator(
                                "xpath=ancestor-or-self::*["
                                "@role=\'button\' or self::button or self::a"
                                "][1]"
                            )

                            if ancestor.count() > 0:
                                try:
                                    ancestor.first.scroll_into_view_if_needed()
                                    ancestor.first.click(
                                        timeout=5000,
                                        force=True,
                                    )
                                    clicked = True
                                    page_diagnostics[-1][
                                        "pagination_click_method"
                                    ] = "TEXT_CLICKABLE_ANCESTOR:" + label
                                    break
                                except Exception:
                                    pass

                            try:
                                text_node.scroll_into_view_if_needed()
                                text_node.click(
                                    timeout=5000,
                                    force=True,
                                )
                                clicked = True
                                page_diagnostics[-1][
                                    "pagination_click_method"
                                ] = "TEXT_NODE:" + label
                                break
                            except Exception:
                                pass
                        except Exception:
                            continue

                if not clicked:
                    page_diagnostics[-1]["stop_reason"] = "NEXT_CONTROL_NOT_FOUND"
                    break

                page.wait_for_timeout(1200)
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass

                after_links = set(extract_links(page.content(), page.url))
                if after_links == before:
                    page.wait_for_timeout(1500)
                    after_links = set(extract_links(page.content(), page.url))
                if after_links == before:
                    page_diagnostics[-1]["stop_reason"] = "PAGE_DID_NOT_CHANGE"
                    break

            final_url = page.url
            browser.close()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    diagnostics = {
        "listing_url": listing_url,
        "listing_fetch_method": "playwright_paginated",
        "listing_status": status,
        "listing_final_url": final_url,
        "listing_blocked": blocked,
        "listing_error": error,
        "raw_links": sum(item.get("article_links_on_page", 0) for item in page_diagnostics),
        "article_links": len(combined),
        "pagination_pages_requested": max_pages,
        "pagination_pages_visited": len(page_diagnostics),
        "pagination_pages": page_diagnostics,
        "pagination_complete": bool(page_diagnostics and page_diagnostics[-1].get("stop_reason")=="TWO_OLDER_URL_DATED_PAGES" and not error),
    }
    return combined, diagnostics


# GENERIC_MULTI_SECTION_DISCOVERY_V1
def _discover_wordpress_api(profile: dict[str, Any], api_url: str, max_pages: int = 20) -> tuple[list[str], dict[str, Any]]:
    """Every post in the collection window from a public WordPress REST API.

    Completeness is proven, not inferred: the API reports X-WP-Total and
    X-WP-TotalPages for the date-filtered query, and every page is read.
    Each post's own date is returned so out-of-window posts are never fetched.
    """
    import httpx
    start = date.fromisoformat(str(profile.get("discovery_start_date") or datetime.now(SRI_LANKA_TZ).date().isoformat()))
    # WordPress compares in site-local time; a day's margin covers offsets,
    # and the article's own date still decides membership of the window.
    after = (start - timedelta(days=1)).isoformat() + "T00:00:00"
    end = date.fromisoformat(str(profile.get("discovery_end_date") or datetime.now(SRI_LANKA_TZ).date().isoformat()))
    before = (end + timedelta(days=2)).isoformat() + "T00:00:00"
    links: list[str] = []
    link_dates: dict[str, str] = {}
    records: dict[str, dict] = {}
    observed_links: set[str] = set()
    expected_totals = None
    diag: dict[str, Any] = {"listing_url": api_url, "listing_fetch_method": "wordpress_api", "listing_status": None,
                            "listing_final_url": api_url, "listing_blocked": False, "listing_error": "",
                            "wp_total": None, "wp_total_pages": None, "wp_pages_read": 0, "pagination_complete": False}
    excludes = [re.compile(p) for p in profile.get("exclude_url_patterns") or []]
    try:
        with httpx.Client(headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                   "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36", "Accept": "application/json"},
                          timeout=30.0, follow_redirects=True) as client:
            page = 1
            while page <= max_pages:
                response = client.get(api_url, params={"per_page": 100, "page": page, "after": after, "before": before,
                                                       "orderby": "date", "order": "desc",
                                                       "_fields": "date,date_gmt,link,title,content"})
                diag["listing_status"] = response.status_code
                if response.status_code != 200:
                    diag["listing_blocked"] = _blocked(response.status_code, response.text)
                    raise RuntimeError(f"WORDPRESS_API_HTTP_{response.status_code}: page {page}")
                if "X-WP-TotalPages" not in response.headers or "X-WP-Total" not in response.headers:
                    raise RuntimeError("WORDPRESS_API_MISSING_TOTALS")
                total_pages = int(response.headers["X-WP-TotalPages"])
                diag["wp_total"] = int(response.headers["X-WP-Total"])
                diag["wp_total_pages"] = total_pages
                totals = (diag["wp_total"], total_pages)
                if min(totals) < 0 or total_pages != (diag["wp_total"] + 99) // 100:
                    raise RuntimeError("WORDPRESS_API_INVALID_TOTALS")
                if expected_totals is not None and expected_totals != totals:
                    raise RuntimeError("WORDPRESS_API_TOTALS_CHANGED_DURING_PAGINATION")
                expected_totals = totals
                rows = response.json()
                if not isinstance(rows, list):
                    raise RuntimeError("WORDPRESS_API_INVALID_JSON")
                diag["wp_pages_read"] = page
                for row in rows:
                    if not isinstance(row, dict):
                        raise RuntimeError("WORDPRESS_API_INVALID_RECORD")
                    link = canonicalize_url(str(row.get("link") or ""))
                    if not link or not same_site(link, api_url):
                        raise RuntimeError("WORDPRESS_API_INVALID_LINK")
                    if link in observed_links:
                        raise RuntimeError("WORDPRESS_API_REPEATED_RECORD")
                    observed_links.add(link)
                    if any(p.search(link) for p in excludes):
                        continue
                    if link not in link_dates:
                        links.append(link)
                    # Membership uses Colombo time, matching the staged record.
                    # The site's local date may differ near midnight.
                    link_dates[link] = _wordpress_published_at(row)[:10]
                    records[link] = row
                if page >= total_pages:
                    break
                page += 1
        diag["pagination_complete"] = (diag["wp_pages_read"] >= diag["wp_total_pages"]
                                       and len(observed_links) == diag["wp_total"])
        if not diag["pagination_complete"]:
            raise RuntimeError("WORDPRESS_API_INCOMPLETE_RESULTS")
    except Exception as exc:
        diag["listing_error"] = f"{type(exc).__name__}: {exc}"
    diag.update(raw_links=len(link_dates), article_links=len(links), link_dates=link_dates, api_records=records)
    return links, diag


def _wordpress_published_at(row: dict) -> str:
    raw = str(row.get("date_gmt") or "")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            from datetime import timezone
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(SRI_LANKA_TZ).isoformat()
    except (TypeError, ValueError):
        return ""


def article_from_wordpress(url: str, row: dict, profile: dict[str, Any]) -> ArticleResult:
    """The publisher's own record: title, exact GMT time and full content.

    Page scraping can pick up a site-wide widget instead of the article (as on
    EconomyNext); the API record is the article itself.
    """
    title = BeautifulSoup(str((row.get("title") or {}).get("rendered") or ""), "html.parser").get_text(" ", strip=True)
    html = str((row.get("content") or {}).get("rendered") or "")
    text = _clean_paragraphs(BeautifulSoup(html, "html.parser"))
    published = _wordpress_published_at(row)
    words = len(text.split())
    minimum = int(profile.get("minimum_article_words") or 80)
    errors = []
    if not title:errors.append("MISSING_HEADLINE")
    if not published:errors.append("PUBLISHED_DATE_MISSING")
    if words < minimum:errors.append(f"ARTICLE_TOO_SHORT:{words}")
    return ArticleResult(url=url, valid=not errors, headline=title, published_at=published, article_text=text,
                         canonical_url=url, word_count=words, fetch_method="wordpress_api", fetch_status=200,
                         errors=errors or None)


def discover_article_links(
    profile: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    configured = profile.get("listing_urls")
    if not configured:
        return _discover_article_links_single(profile)

    combined: list[str] = []
    seen: set[str] = set()
    pages: list[dict[str, Any]] = []

    for index, entry in enumerate(configured, start=1):
        if isinstance(entry, str):
            url = entry
            label = f"listing_{index}"
            overrides: dict[str, Any] = {}
        elif isinstance(entry, dict):
            url = str(entry.get("url") or "").strip()
            label = str(entry.get("name") or f"listing_{index}").strip()
            overrides = entry
        else:
            continue

        if not url:
            continue

        page_profile = dict(profile)
        page_profile["listing_url"] = url
        if "listing_prefer_browser" in overrides:
            page_profile["listing_prefer_browser"] = bool(overrides.get("listing_prefer_browser"))
        if "pagination_date_exclude_selectors" in overrides:
            page_profile["pagination_date_exclude_selectors"] = overrides["pagination_date_exclude_selectors"]
        if "skip_first_n_article_links" in overrides:
            page_profile["skip_first_n_article_links"] = int(overrides.get("skip_first_n_article_links") or 0)
        elif len(configured) > 1:
            page_profile.pop("skip_first_n_article_links", None)

        pagination_pages = int(overrides.get("pagination_click_pages") or 0)
        if str(overrides.get("type") or "") == "wordpress_api":
            links, diag = _discover_wordpress_api(page_profile, url)
        elif pagination_pages > 1:
            links, diag = _discover_browser_click_pages(
                page_profile,
                url,
                pagination_pages,
            )
        else:
            links, diag = _discover_article_links_single(page_profile)
        diag = dict(diag)
        diag["listing_name"] = label
        pages.append(diag)

        for url_value in links:
            canonical = canonicalize_url(url_value)
            if canonical in seen:
                continue
            seen.add(canonical)
            combined.append(canonical)

    successful_pages = [
        page for page in pages
        if not page.get("listing_blocked")
        and not page.get("listing_error")
        and page.get("pagination_complete") is not False
        and page.get("listing_status") is not None
        and 200 <= int(page.get("listing_status")) < 400
    ]
    failed_pages = [page for page in pages if page not in successful_pages]
    first_success = successful_pages[0] if successful_pages else {}
    errors = [str(page.get("listing_error") or "") for page in failed_pages if page.get("listing_error")]

    diagnostics = {
        "listing_url": str(profile.get("listing_url") or ""),
        "listing_urls": [str(entry if isinstance(entry, str) else entry.get("url", "")) for entry in configured],
        "listing_fetch_method": "multi:" + ",".join(sorted({str(page.get("listing_fetch_method") or "unknown") for page in pages})),
        "listing_status": first_success.get("listing_status"),
        "listing_final_url": first_success.get("listing_final_url", ""),
        "listing_blocked": bool(pages) and not successful_pages and all(bool(page.get("listing_blocked")) for page in pages),
        "listing_error": "; ".join(errors) if not successful_pages else "",
        "listing_partial_error": bool(successful_pages and failed_pages),
        "listing_pages_total": len(pages),
        "listing_pages_successful": len(successful_pages),
        "listing_pages_failed": len(failed_pages),
        "raw_links": sum(int(page.get("raw_links", 0) or 0) for page in pages),
        "article_links": len(combined),
        "listing_pages": [{k: v for k, v in page.items() if k not in ("link_dates", "api_records")} for page in pages],
        # Publisher-supplied post dates (WordPress API) for pre-fetch window checks.
        "link_dates": {url: day for page in pages for url, day in (page.get("link_dates") or {}).items()},
        "api_records": {url: row for page in pages for url, row in (page.get("api_records") or {}).items()},
    }
    return combined, diagnostics


def _json_ld_objects(soup) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for node in soup.find_all(
        "script",
        attrs={"type": re.compile(r"ld\+json", re.I)},
    ):
        raw = node.string or node.get_text() or ""
        try:
            value = json.loads(raw)
        except Exception:
            continue

        stack = value if isinstance(value, list) else [value]
        while stack:
            obj = stack.pop()
            if isinstance(obj, dict):
                objects.append(obj)
                graph = obj.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
            elif isinstance(obj, list):
                stack.extend(obj)
    return objects


def _article_ld(soup) -> dict[str, Any]:
    for obj in _json_ld_objects(soup):
        typ = obj.get("@type")
        types = typ if isinstance(typ, list) else [typ]
        normalized = {str(x).lower() for x in types if x}
        if normalized & {
            "newsarticle",
            "article",
            "reportagenewsarticle",
            "analysisnewsarticle",
            "blogposting",
        }:
            return obj
    return {}


def _meta(soup, *names: str) -> str:
    for name in names:
        node = (
            soup.find("meta", attrs={"property": name})
            or soup.find("meta", attrs={"name": name})
            or soup.find("meta", attrs={"itemprop": name})
        )
        if node and node.get("content"):
            return str(node.get("content")).strip()
    return ""


def _author_from_ld(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("name") or "").strip()
    if isinstance(value, list):
        vals = [_author_from_ld(v) for v in value]
        return "; ".join(v for v in vals if v)
    return ""


def parse_published_at(value: str) -> str:
    value = re.sub(r"\s+", " ", (value or "").strip())
    if not value:
        return ""

    iso_candidate = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_candidate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=SRI_LANKA_TZ)
        else:
            dt = dt.astimezone(SRI_LANKA_TZ)
        return dt.isoformat()
    except Exception:
        pass

    patterns = (
        "%d-%m-%Y | %I:%M %p",
        "%d-%m-%Y|%I:%M %p",
        "%d-%m-%Y %I:%M %p",
        "%d %b %Y | %I:%M %p",
        "%d %b %Y",
        "%d %B %Y",
        "%B %d, %Y %I:%M %p",
        "%B %d, %Y %H:%M",
        "%B %d, %Y",
        "%b %d, %Y %I:%M %p",
        "%b %d, %Y",
    )
    for fmt in patterns:
        try:
            dt = datetime.strptime(value, fmt).replace(tzinfo=SRI_LANKA_TZ)
            return dt.isoformat()
        except Exception:
            pass

    candidates = [
        r"\b\d{2}-\d{2}-20\d{2}\s*\|\s*\d{1,2}:\d{2}\s*[AP]M\b",
        r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+20\d{2}\b",
        r"\b[A-Za-z]{3,9}\s+\d{1,2},\s+20\d{2}(?:\s+\d{1,2}:\d{2}\s*[AP]M)?\b",
    ]
    for rx in candidates:
        m = re.search(rx, value, re.I)
        if m:
            parsed = parse_published_at(m.group(0))
            if parsed:
                return parsed
    return ""


def _extract_visible_date(soup, profile: dict[str, Any]) -> str:
    text = soup.get_text(" ", strip=True)
    for rx_text in (profile.get("date_regexes") or []):
        m = re.search(rx_text, text, re.I)
        if m:
            parsed = parse_published_at(m.group(1) if m.groups() else m.group(0))
            if parsed:
                return parsed

    generic = [
        r"(\d{2}-\d{2}-20\d{2}\s*\|\s*\d{1,2}:\d{2}\s*[AP]M)",
        r"(\d{1,2}\s+[A-Za-z]{3,9}\s+20\d{2})",
        r"([A-Za-z]{3,9}\s+\d{1,2},\s+20\d{2}(?:\s+\d{1,2}:\d{2}\s*[AP]M)?)",
    ]
    for rx in generic:
        m = re.search(rx, text, re.I)
        if m:
            parsed = parse_published_at(m.group(1))
            if parsed:
                return parsed
    return ""


def _clean_paragraphs(container) -> str:
    if container is None:
        return ""

    stop_markers = {
        "other articles",
        "featured news",
        "related articles",
        "more news",
        "recommended",
        "most read",
    }

    chunks: list[str] = []
    seen = set()

    for node in container.find_all(["p", "li"], recursive=True):
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
        if not text:
            continue
        low = text.lower().strip(" :")
        if low in stop_markers:
            break
        if len(text.split()) < 5:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        chunks.append(text)

    return "\n\n".join(chunks)


def _best_text_container(soup):
    for selector in (
        "article",
        "main article",
        "[itemprop='articleBody']",
        ".article-content",
        ".article-body",
        ".entry-content",
        ".post-content",
        ".story-content",
        ".news-content",
        "main",
    ):
        node = soup.select_one(selector)
        if node:
            text = _clean_paragraphs(node)
            if len(text.split()) >= 80:
                return node

    candidates = []
    for node in soup.find_all(["div", "section"]):
        if len(node.find_all("p")) < 2:
            continue
        text = _clean_paragraphs(node)
        wc = len(text.split())
        if wc >= 80:
            candidates.append((wc, node))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    return soup.body or soup


def extract_article(
    url: str,
    profile: dict[str, Any],
) -> ArticleResult:
    """
    Extract one article.

    Normal route:
        HTTP -> parse/validate.

    Some modern sites return HTTP 200 with only an application shell.
    If the parsed result is structurally invalid, retry the same public
    article in a normal headless browser and parse the rendered HTML.

    This is a rendering fallback, not an access-control bypass.
    """
    _require_parsers()

    def parse_result(result: FetchResult) -> ArticleResult:
        errors: list[str] = []

        if result.error:
            errors.append(result.error)
        if result.blocked:
            errors.append("FETCH_BLOCKED")
        if not result.text:
            errors.append("EMPTY_PAGE")

        if errors:
            return ArticleResult(
                url=url,
                valid=False,
                fetch_method=result.method,
                fetch_status=result.status,
                blocked=result.blocked,
                errors=errors,
            )

        try:
            soup = BeautifulSoup(result.text, "html.parser")
        except Exception as exc:
            return ArticleResult(
                url=url,
                valid=False,
                fetch_method=result.method,
                fetch_status=result.status,
                blocked=result.blocked,
                errors=[f"PARSER_REJECTED:{type(exc).__name__}"],
            )
        ld = _article_ld(soup)

        h1 = soup.find("h1")
        headline = str(
            ld.get("headline")
            or _meta(soup, "og:title", "twitter:title")
            or (h1.get_text(" ", strip=True) if h1 else "")
        ).strip()

        headline = html.unescape(headline)
        headline = re.sub(r"\s+", " ", headline).strip()
        headline = re.sub(r"&(?=[A-Za-z])", "& ", headline)

        canonical_node = soup.find(
            "link",
            rel=lambda v: v and "canonical" in str(v).lower(),
        )
        canonical = str(
            ld.get("url")
            or (canonical_node.get("href") if canonical_node else "")
            or result.final_url
            or url
        ).strip()
        canonical = canonicalize_url(
            urljoin(result.final_url or url, canonical)
        )

        raw_date = str(
            ld.get("datePublished")
            or _meta(
                soup,
                "article:published_time",
                "datePublished",
                "date",
                "pubdate",
                "publish-date",
            )
            or ""
        ).strip()

        published_at = parse_published_at(raw_date)

        if not published_at:
            time_node = soup.find("time")
            if time_node:
                published_at = parse_published_at(
                    str(
                        time_node.get("datetime")
                        or time_node.get_text(" ", strip=True)
                    )
                )

        if not published_at:
            published_at = _extract_visible_date(
                soup,
                profile,
            )

        author = (
            _author_from_ld(ld.get("author"))
            or _meta(soup, "author", "article:author")
        )
        author = html.unescape(author)
        author = re.sub(r"\s+", " ", author).strip()

        article_text = str(
            ld.get("articleBody")
            or ""
        ).strip()
        explicit_body = bool(article_text)
        body_selector = profile.get('article_body_selector')
        if body_selector and soup.select_one(body_selector):
            article_text = _clean_paragraphs(soup.select_one(body_selector))
            explicit_body = True
        elif len(article_text.split()) < 25:
            container = _best_text_container(soup)
            article_text = _clean_paragraphs(container)
            explicit_body = False

        article_text = html.unescape(article_text)
        article_text = re.sub(r"[ \t]+", " ", article_text)
        article_text = re.sub(r"\n{3,}", "\n\n", article_text).strip()

        word_count = len(article_text.split())
        min_words = int(
            profile.get(
                "minimum_article_words",
                80,
            )
        )

        if len(headline) < 8:
            errors.append("HEADLINE_MISSING_OR_TOO_SHORT")

        if not published_at:
            errors.append("PUBLISHED_DATE_MISSING")

        warnings = []
        if word_count < min_words:
            from bank_intel.collectors.sources._public_business_base import short_article_is_valid
            if explicit_body and published_at and short_article_is_valid(headline, article_text, published_at):
                warnings.append(f"SHORT_PUBLISHER_NOTICE:{word_count}")
            else:
                errors.append(f"ARTICLE_TOO_SHORT:{word_count}")

        return ArticleResult(
            url=url,
            valid=not errors,
            headline=headline,
            published_at=published_at,
            author=author,
            article_text=article_text,
            canonical_url=canonical,
            word_count=word_count,
            fetch_method=result.method,
            fetch_status=result.status,
            blocked=result.blocked,
            errors=errors,
            warnings=warnings,
        )

    first = fetch_page(
        url,
        prefer_browser=bool(
            profile.get(
                "article_prefer_browser",
                False,
            )
        ),
    )
    article = parse_result(first)

    retry_on_invalid = bool(
        profile.get(
            "browser_retry_on_invalid",
            True,
        )
    )

    if (
        not article.valid
        and retry_on_invalid
        and first.method != "playwright"
        and not first.blocked
    ):
        browser = _browser_fetch(url)

        if (
            not browser.error
            and not browser.blocked
            and browser.text
        ):
            browser_article = parse_result(browser)

            if (
                browser_article.valid
                or browser_article.word_count > article.word_count
            ):
                article = browser_article

    return article

def safe_extract_article(
    url: str,
    profile: dict[str, Any],
) -> ArticleResult:
    """Fail one article, not the whole source."""
    try:
        return extract_article(url, profile)
    except Exception as exc:
        message = str(exc).replace("\r", " ").replace("\n", " ").strip()
        if len(message) > 180:
            message = message[:177] + "..."
        return ArticleResult(
            url=url,
            valid=False,
            fetch_method="exception_guard",
            fetch_status=None,
            blocked=False,
            errors=[
                f"EXTRACTION_EXCEPTION:{type(exc).__name__}"
                + (f":{message}" if message else "")
            ],
        )


def diagnose_source(
    source_key: str,
    profile: dict[str, Any],
    *,
    limit: int = 5,
) -> dict[str, Any]:
    started = datetime.now(SRI_LANKA_TZ)

    links, listing = discover_article_links(profile)
    sample = links[: max(1, limit)]

    articles = []
    for index, url in enumerate(sample, start=1):
        print(f"    [{index}/{len(sample)}] {url}")
        article = safe_extract_article(url, profile)
        articles.append(article.to_dict())
        state = "PASS" if article.valid else "FAIL"
        print(
            f"        {state} | {article.fetch_method} | "
            f"status={article.fetch_status} | words={article.word_count} | "
            f"date={article.published_at or 'MISSING'}"
        )
        if article.errors:
            print(f"        errors={article.errors}")

    valid = sum(1 for x in articles if x["valid"])
    blocked = sum(1 for x in articles if x["blocked"])
    date_missing = sum(
        1
        for x in articles
        if "PUBLISHED_DATE_MISSING" in (x.get("errors") or [])
    )

    if listing.get("listing_blocked"):
        status = "BLOCKED_LISTING"
    elif not links:
        status = "NO_ARTICLE_LINKS"
    elif articles and valid == len(articles):
        status = "PASS"
    elif valid >= max(1, min(2, len(articles))):
        status = "PARTIAL_PASS"
    elif blocked:
        status = "BLOCKED_ARTICLE_FETCH"
    else:
        status = "FAIL"

    return {
        "source_key": source_key,
        "source_name": profile.get("name", source_key),
        "listing_url": profile.get("listing_url"),
        "status": status,
        "started_at": started.isoformat(),
        "completed_at": datetime.now(SRI_LANKA_TZ).isoformat(),
        "listing": listing,
        "candidate_article_links": len(links),
        "articles_tested": len(articles),
        "articles_valid": valid,
        "articles_blocked": blocked,
        "missing_dates": date_missing,
        "articles": articles,
    }


def _next_version(folder: Path, content_hash: str) -> tuple[int, str]:
    existing = sorted(folder.glob("v*.json"))
    if not existing:
        return 1, "NEW_ARTICLE"

    latest = existing[-1]
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except Exception:
        payload = {}

    if payload.get("content_hash") == content_hash:
        return int(payload.get("article_version") or len(existing)), "UNCHANGED"

    return len(existing) + 1, "CHANGED_ARTICLE"


def stage_article(
    *,
    project_root: Path,
    source_key: str,
    profile: dict[str, Any],
    article: ArticleResult,
    collection_run_date: str,
    production: bool = False,
) -> dict[str, Any]:
    if not article.valid:
        raise ValueError("Cannot stage an invalid article.")

    pub_date = datetime.fromisoformat(article.published_at).date().isoformat()
    stable_url = article.canonical_url or article.url
    article_id = sha1(stable_url.encode("utf-8")).hexdigest()[:20]

    text_for_hash = article.headline.strip() + "\n" + article.article_text.strip()
    content_hash = sha256(text_for_hash.encode("utf-8")).hexdigest()

    staged_root = project_root / "data" / "staged"
    if not production:
        staged_root = staged_root / "generic_sources"

    folder = (
        staged_root
        / source_key
        / pub_date
        / article_id
    )
    folder.mkdir(parents=True, exist_ok=True)

    version, intake = _next_version(folder, content_hash)

    if intake == "UNCHANGED":
        return {
            "status": "UNCHANGED",
            "source_key": source_key,
            "headline": article.headline,
            "path": str(folder / f"v{version:03d}.json"),
        }

    payload = {
        "source_key": source_key,
        "source_name": profile.get("name", source_key),
        "section_name": profile.get("section_name", "Business"),
        "headline": article.headline,
        "published_at": article.published_at,
        "author": article.author,
        "source_url": article.url,
        "canonical_url": article.canonical_url or article.url,
        "article_text": article.article_text,
        "extraction_warnings": article.warnings or [],
        "content_hash": content_hash,
        "article_version": version,
        "collection_status": "SUCCESS",
        "intake_status": (
            "LATE_DISCOVERY"
            if intake == "NEW_ARTICLE" and pub_date < collection_run_date
            else intake
        ),
        "collection_run_date": collection_run_date,
        "collected_at": datetime.now(SRI_LANKA_TZ).isoformat(),
        "collector": "generic-source-v2-production" if production else "generic-source-v2-test",
    }

    output = folder / f"v{version:03d}.json"
    output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return {
        "status": intake,
        "source_key": source_key,
        "headline": article.headline,
        "path": str(output),
    }


def collect_and_stage(
    *,
    project_root: Path,
    source_key: str,
    profile: dict[str, Any],
    from_date: date,
    to_date: date,
    limit: int = 30,
    production: bool = False,
) -> dict[str, Any]:
    profile = dict(profile)
    profile["discovery_start_date"] = from_date.isoformat()
    profile["discovery_end_date"] = to_date.isoformat()
    links, listing = discover_article_links(profile)
    total_discovered_links = len(links)
    # Production must assess every discovered article.
    # Candidate limits are only for diagnostic/test runs.
    if not production:
        links = links[:limit]

    staged = []
    extraction_failures = []
    recovered_urls = []
    valid = 0
    invalid = 0
    outside = 0
    blocked = 0
    missing_date = 0
    new_articles = 0
    late_discoveries = 0
    changed_articles = 0
    unchanged_articles = 0

    run_date = datetime.now(SRI_LANKA_TZ).date().isoformat()

    link_dates = listing.pop("link_dates", {}) or {}
    api_records = listing.pop("api_records", {}) or {}
    for article_number, url in enumerate(links, 1):
        print(f'[{source_key} {article_number}/{len(links)}] {url}', flush=True)
        # A publisher API date outside the window: skip without fetching.
        api_day = link_dates.get(url)
        if api_day:
            try:
                api_date = date.fromisoformat(api_day)
            except ValueError:
                api_date = None
            if api_date is not None and (api_date < from_date or api_date > to_date):
                outside += 1
                continue
        # URL_DATE_PREFILTER_V1
        url_date_match = re.search(
            r"/(20\d{2})/(\d{1,2})/(\d{1,2})/",
            urlparse(url).path,
        )
        if url_date_match:
            try:
                url_pub_date = date(
                    int(url_date_match.group(1)),
                    int(url_date_match.group(2)),
                    int(url_date_match.group(3)),
                )
            except ValueError:
                url_pub_date = None
            if url_pub_date is not None and (url_pub_date < from_date or url_pub_date > to_date):
                outside += 1
                continue

        if url in api_records:
            article = article_from_wordpress(url, api_records[url], profile)
        else:
            article = safe_extract_article(url, profile)
        if not article.valid and url not in api_records:
            first_errors=list(article.errors or [])
            article=safe_extract_article(url, profile)
            if article.valid:recovered_urls.append(url)
            else:extraction_failures.append({"url":url,"errors":list(article.errors or []),"first_attempt_errors":first_errors,"attempts":2})
        elif not article.valid:
            extraction_failures.append({"url": url, "errors": list(article.errors or []), "attempts": 1})

        if article.blocked:
            blocked += 1

        if not article.valid:
            invalid += 1
            if "PUBLISHED_DATE_MISSING" in (article.errors or []):
                missing_date += 1
            continue

        valid += 1
        pub = datetime.fromisoformat(article.published_at).date()

        if pub < from_date or pub > to_date:
            outside += 1
            continue

        stage_result = stage_article(
            project_root=project_root,
            source_key=source_key,
            profile=profile,
            article=article,
            collection_run_date=run_date,
            production=production,
        )
        staged.append(stage_result)

        status = str(stage_result.get("status") or "").upper()
        if status == "NEW_ARTICLE":
            new_articles += 1
            if pub.isoformat() < run_date:
                late_discoveries += 1
        elif status == "CHANGED_ARTICLE":
            changed_articles += 1
        elif status == "UNCHANGED":
            unchanged_articles += 1

    return {
        "source_key": source_key,
        "source_name": profile.get("name", source_key),
        "production": production,
        "listing": listing,
        "candidate_urls": len(links),
        "all_discovered_urls": total_discovered_links,
        "truncated_discovery_urls": max(0, total_discovered_links - len(links)),
        "valid_extractions": valid,
        "invalid_extractions": invalid,
        "extraction_failures": extraction_failures,
        "unresolved_fetch_urls": [x["url"] for x in extraction_failures],
        "recovered_urls": recovered_urls,
        "blocked_fetches": blocked,
        "outside_date_window": outside,
        "missing_date": missing_date,
        "new_articles": new_articles,
        "late_discoveries": late_discoveries,
        "changed_articles": changed_articles,
        "unchanged_articles": unchanged_articles,
        "wrong_date_skipped": outside,
        "fetch_failures": blocked,
        "recent_duplicates": 0,
        "staged": staged,
        "discovered_urls": links,
        "staged_count": len(staged),
    }
