from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from bank_intel.common.urls import absolute_url, normalize_url, domain_matches

SOURCE_NAME = "Ceylon Today"
SECTION_NAME = "Finance Today"
BASE_URL = "https://ceylontoday.lk"
FINANCE_TODAY_URL = "https://ceylontoday.lk/category/ceylon-today-daily/finance-today/"
ARTICLE_PATTERN = re.compile(r"^/(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})/(?P<slug>[^/]+)/?$", re.IGNORECASE)

@dataclass
class DiscoveredCeylonTodayArticle:
    headline: str
    url: str
    published_at_hint: str | None
    excerpt: str | None

def build_section_page_url(page_number: int) -> str:
    if page_number <= 1:
        return FINANCE_TODAY_URL
    return f"{FINANCE_TODAY_URL}page/{page_number}/"

def publication_hint_from_url(url: str) -> str | None:
    match = ARTICLE_PATTERN.match(urlparse(url).path)
    if not match:
        return None
    try:
        dt = datetime(year=int(match.group("year")), month=int(match.group("month")), day=int(match.group("day")))
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%d") + "T00:00:00+05:30"

def _find_excerpt(anchor) -> str | None:
    node = anchor
    for _ in range(6):
        node = getattr(node, "parent", None)
        if node is None:
            break
        try:
            text = node.get_text(" ", strip=True)
        except AttributeError:
            continue
        if not text or len(text) > 2500:
            continue
        for paragraph in node.find_all("p"):
            value = " ".join(paragraph.get_text(" ", strip=True).split())
            if len(value) >= 40:
                return value
    return None

def discover_articles(html: str) -> list[DiscoveredCeylonTodayArticle]:
    soup = BeautifulSoup(html, "lxml")
    discovered: dict[str, DiscoveredCeylonTodayArticle] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href")
        if not href:
            continue
        full_url = normalize_url(absolute_url(FINANCE_TODAY_URL, href))
        if not domain_matches(full_url, "ceylontoday.lk"):
            continue
        if not ARTICLE_PATTERN.match(urlparse(full_url).path):
            continue
        headline = " ".join(anchor.get_text(" ", strip=True).split())
        if len(headline) < 8:
            continue
        publication_hint = publication_hint_from_url(full_url)
        excerpt = _find_excerpt(anchor)
        existing = discovered.get(full_url)
        if existing is None:
            discovered[full_url] = DiscoveredCeylonTodayArticle(headline, full_url, publication_hint, excerpt)
        else:
            if not existing.headline and headline:
                existing.headline = headline
            if existing.excerpt is None and excerpt:
                existing.excerpt = excerpt
    return list(discovered.values())
