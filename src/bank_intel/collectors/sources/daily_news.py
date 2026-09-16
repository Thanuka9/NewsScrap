from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup
import dateparser

from bank_intel.common.urls import absolute_url, normalize_url, domain_matches

SOURCE_NAME = "Daily News"
SECTION_NAME = "Business"
BASE_URL = "https://dailynews.lk"
BUSINESS_URL = "https://dailynews.lk/category/business/"

ARTICLE_PATTERN = re.compile(
    r"^/(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})/business/\d+/.+$",
    re.IGNORECASE,
)
VISIBLE_DATE_PATTERN = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b",
    re.IGNORECASE,
)

@dataclass
class DiscoveredDailyNewsArticle:
    headline: str
    url: str
    published_at_hint: str | None
    excerpt: str | None

def _parse_date_text(value: str) -> str | None:
    if not value:
        return None
    parsed = dateparser.parse(value, settings={
        "RETURN_AS_TIMEZONE_AWARE": True,
        "TIMEZONE": "Asia/Colombo",
        "TO_TIMEZONE": "Asia/Colombo",
        "DATE_ORDER": "MDY",
    })
    return parsed.isoformat() if parsed else None

def _date_from_article_url(url: str) -> str | None:
    match = ARTICLE_PATTERN.match(urlparse(url).path)
    if not match:
        return None
    try:
        dt = datetime(int(match.group("year")), int(match.group("month")), int(match.group("day")))
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%d") + "T00:00:00+05:30"

def _extract_card_information(anchor) -> tuple[str | None, str | None]:
    node = anchor
    for _ in range(7):
        node = getattr(node, "parent", None)
        if node is None:
            break
        try:
            text = " ".join(node.get_text(" ", strip=True).split())
        except AttributeError:
            continue
        if not text or len(text) > 2200:
            continue
        date_match = VISIBLE_DATE_PATTERN.search(text)
        if date_match:
            published = _parse_date_text(date_match.group(0))
            excerpt = None
            for paragraph in node.find_all("p"):
                value = " ".join(paragraph.get_text(" ", strip=True).split())
                if len(value) >= 40:
                    excerpt = value
                    break
            return published, excerpt
    return None, None

def discover_articles(html: str) -> list[DiscoveredDailyNewsArticle]:
    soup = BeautifulSoup(html, "lxml")
    discovered: dict[str, DiscoveredDailyNewsArticle] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href")
        if not href:
            continue
        full_url = normalize_url(absolute_url(BUSINESS_URL, href))
        if not domain_matches(full_url, "dailynews.lk"):
            continue
        if not ARTICLE_PATTERN.match(urlparse(full_url).path):
            continue
        headline = " ".join(anchor.get_text(" ", strip=True).split())
        if len(headline) < 8:
            continue
        published_at_hint, excerpt = _extract_card_information(anchor)
        if not published_at_hint:
            published_at_hint = _date_from_article_url(full_url)
        existing = discovered.get(full_url)
        if existing is None:
            discovered[full_url] = DiscoveredDailyNewsArticle(headline, full_url, published_at_hint, excerpt)
        else:
            if not existing.headline and headline:
                existing.headline = headline
            if existing.published_at_hint is None and published_at_hint:
                existing.published_at_hint = published_at_hint
            if existing.excerpt is None and excerpt:
                existing.excerpt = excerpt
    return list(discovered.values())
