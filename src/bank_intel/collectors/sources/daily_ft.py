from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlparse

import dateparser
from bs4 import BeautifulSoup

from bank_intel.common.urls import (
    absolute_url,
    normalize_url,
    domain_matches,
)

SOURCE_NAME = "Daily FT"
BASE_URL = "https://www.ft.lk"
SECTION_NAME = "Financial Services"
FINANCIAL_SERVICES_URL = "https://www.ft.lk/financial-services/42"
PAGE_SIZE = 30

@dataclass
class DiscoveredArticle:
    headline: str
    url: str
    published_at_hint: str | None = None

ARTICLE_PATTERN = re.compile(r"^/financial-services/.+/\d+-\d+$", re.IGNORECASE)
DATE_PATTERN = re.compile(
    r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+"
    r"\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{4}(?:\s+\d{1,2}:\d{2})?\b",
    re.IGNORECASE,
)

def build_section_page_url(page_number: int) -> str:
    if page_number <= 1:
        return FINANCIAL_SERVICES_URL
    offset = (page_number - 1) * PAGE_SIZE
    return f"{FINANCIAL_SERVICES_URL}/{offset}"

def _parse_listing_date(value: str) -> str | None:
    parsed = dateparser.parse(value, settings={
        "RETURN_AS_TIMEZONE_AWARE": True,
        "TIMEZONE": "Asia/Colombo",
        "TO_TIMEZONE": "Asia/Colombo",
        "DATE_ORDER": "DMY",
    })
    return parsed.isoformat() if parsed else None

def _find_date_near_anchor(anchor) -> str | None:
    node = anchor
    for _ in range(6):
        node = getattr(node, "parent", None)
        if node is None:
            break
        try:
            text = node.get_text(" ", strip=True)
        except AttributeError:
            continue
        if not text or len(text) > 1800:
            continue
        match = DATE_PATTERN.search(text)
        if match:
            return _parse_listing_date(match.group(0))
    return None

def discover_articles(html: str) -> list[DiscoveredArticle]:
    soup = BeautifulSoup(html, "lxml")
    discovered: dict[str, DiscoveredArticle] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href")
        if not href:
            continue
        full_url = normalize_url(absolute_url(BASE_URL, href))
        if not domain_matches(full_url, "ft.lk"):
            continue
        if not ARTICLE_PATTERN.match(urlparse(full_url).path):
            continue
        headline = " ".join(anchor.get_text(" ", strip=True).split())
        published_at_hint = _find_date_near_anchor(anchor)
        existing = discovered.get(full_url)
        if existing is None:
            discovered[full_url] = DiscoveredArticle(headline=headline, url=full_url, published_at_hint=published_at_hint)
        else:
            if not existing.headline and headline:
                existing.headline = headline
            if existing.published_at_hint is None and published_at_hint is not None:
                existing.published_at_hint = published_at_hint
    return list(discovered.values())
