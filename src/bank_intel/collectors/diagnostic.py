from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from bank_intel.collectors.http_client import HttpClient
from bank_intel.collectors.browser_client import BrowserClient
from bank_intel.common.urls import absolute_url, normalize_url, domain_matches
from bank_intel.extraction.article import extract_article
from bank_intel.extraction.validator import validate_article

@dataclass
class SourceDiagnosticSpec:
    source_code: str
    name: str
    section_name: str
    section_url: str
    domain: str
    preferred_tokens: tuple[str, ...] = ()

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

SOURCE_SPECS = [
    SourceDiagnosticSpec("SRC001", "Daily News", "Business", "https://dailynews.lk/category/business/", "dailynews.lk", ("/business/",)),
    SourceDiagnosticSpec("SRC002", "Daily Mirror", "Business", "https://www.dailymirror.lk/business", "dailymirror.lk", ("/business/", "/business-news/")),
    SourceDiagnosticSpec("SRC003", "Ceylon Today", "Finance Today Daily", "https://ceylontoday.lk/category/ceylon-today-daily/finance-today/", "ceylontoday.lk"),
    SourceDiagnosticSpec("SRC004", "The Island", "Business", "https://island.lk/category/business/", "island.lk"),
    SourceDiagnosticSpec("SRC005", "Daily FT", "Financial Services", "https://www.ft.lk/financial-services/42", "ft.lk", ("/financial-services/",)),
]

EXCLUDED_PATH_FRAGMENTS = ("/category/", "/tag/", "/author/", "/page/", "/feed/", "/wp-content/", "/contact", "/privacy", "/terms", "/advert", "/login", "/register", "/search")
EXCLUDED_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".pdf", ".zip", ".mp4", ".mp3")

def _looks_like_article_url(*, url: str, spec: SourceDiagnosticSpec) -> bool:
    if not domain_matches(url, spec.domain): return False
    path = urlparse(url).path.lower()
    if not path or path == "/": return False
    if any(fragment in path for fragment in EXCLUDED_PATH_FRAGMENTS): return False
    if path.endswith(EXCLUDED_EXTENSIONS): return False
    if normalize_url(url) == normalize_url(spec.section_url): return False
    if spec.preferred_tokens and any(token.lower() in path for token in spec.preferred_tokens): return True
    return len([part for part in path.split("/") if part]) >= 2

def discover_candidate_links(*, html: str, spec: SourceDiagnosticSpec, max_links: int = 50) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    candidates: dict[str, str] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href")
        if not href: continue
        full_url = normalize_url(absolute_url(spec.section_url, href))
        if not _looks_like_article_url(url=full_url, spec=spec): continue
        headline = " ".join(anchor.get_text(" ", strip=True).split())
        if len(headline) < 8: continue
        candidates.setdefault(full_url, headline)
        if len(candidates) >= max_links: break
    return list(candidates.items())

def fetch_with_fallback(*, url: str, http_client: HttpClient, browser_client: BrowserClient):
    try:
        result = http_client.get(url)
        if len(result.html or "") >= 2000:
            return result, "httpx", False
    except Exception:
        pass
    browser_result = browser_client.get(url)
    return browser_result, "playwright", True

def diagnose_source(*, spec: SourceDiagnosticSpec, http_client: HttpClient, browser_client: BrowserClient, max_article_tests: int = 3) -> SourceDiagnosticResult:
    notes: list[str] = []
    article_results: list[ArticleDiagnostic] = []
    browser_fallback_used = False
    try:
        section_result, section_method, used_browser = fetch_with_fallback(url=spec.section_url, http_client=http_client, browser_client=browser_client)
        browser_fallback_used = browser_fallback_used or used_browser
        section_fetch_success = bool(section_result.html)
        section_status_code = section_result.status_code
    except Exception as exc:
        return SourceDiagnosticResult(spec.source_code, spec.name, spec.section_name, spec.section_url, False, None, None, 0, 0, 0, False, [], [f"SECTION_FETCH_FAILED: {type(exc).__name__}: {exc}"])
    candidates = discover_candidate_links(html=section_result.html, spec=spec)
    if not candidates: notes.append("NO_ARTICLE_LINKS_DISCOVERED")
    for article_url, listing_headline in candidates[:max_article_tests]:
        try:
            article_result, fetch_method, used_browser = fetch_with_fallback(url=article_url, http_client=http_client, browser_client=browser_client)
            browser_fallback_used = browser_fallback_used or used_browser
            article = extract_article(html=article_result.html, source_name=spec.name, section_name=spec.section_name, source_url=article_url, final_url=article_result.final_url)
            validation = validate_article(article)
            article_results.append(ArticleDiagnostic(article_url, listing_headline, fetch_method, article_result.status_code, article.headline, article.published_at, article.word_count, validation.valid, validation.errors, validation.warnings))
        except Exception as exc:
            article_results.append(ArticleDiagnostic(article_url, listing_headline, "FAILED", None, None, None, 0, False, [f"{type(exc).__name__}: {exc}"], []))
    successful_articles = sum(1 for item in article_results if item.valid)
    if browser_fallback_used: notes.append("BROWSER_FALLBACK_REQUIRED_OR_USED")
    if article_results:
        missing_dates = sum(1 for item in article_results if not item.published_at)
        if missing_dates: notes.append(f"MISSING_PUBLICATION_DATE:{missing_dates}/{len(article_results)}")
    return SourceDiagnosticResult(spec.source_code, spec.name, spec.section_name, spec.section_url, section_fetch_success, section_method, section_status_code, len(candidates), len(article_results), successful_articles, browser_fallback_used, article_results, notes)
