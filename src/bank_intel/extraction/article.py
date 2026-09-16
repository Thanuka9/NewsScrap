from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
import re
from typing import Optional
import dateparser
from bs4 import BeautifulSoup
import trafilatura
from bank_intel.common.hashing import content_hash
from bank_intel.common.urls import normalize_url

@dataclass
class ExtractedArticle:
    source_name: str
    section_name: str
    source_url: str
    final_url: str
    canonical_url: str
    headline: str
    subheadline: Optional[str]
    author: Optional[str]
    published_at: Optional[str]
    article_text: str
    word_count: int
    retrieved_at: str
    content_hash: str
    fetch_method: str = "httpx"
    def to_dict(self) -> dict:
        return asdict(self)

def _meta_content(soup: BeautifulSoup, *, property_name: str | None = None, name: str | None = None) -> Optional[str]:
    if property_name:
        tag = soup.find("meta", attrs={"property": property_name})
        if tag and tag.get("content"):
            value = tag["content"].strip()
            if value: return value
    if name:
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            value = tag["content"].strip()
            if value: return value
    return None

def _extract_jsonld_values(soup: BeautifulSoup) -> list:
    results = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text(strip=True)
        if not raw: continue
        try: data = json.loads(raw)
        except Exception: continue
        if isinstance(data, list): results.extend(data)
        elif isinstance(data, dict):
            graph = data.get("@graph")
            if isinstance(graph, list): results.extend(graph)
            results.append(data)
    return results

def _clean_headline(headline: str) -> str:
    if not headline: return ""
    headline = " ".join(headline.split()).strip()
    suffixes = []
    for publisher in ["Daily FT", "Ceylon Today", "Daily Mirror", "Daily News", "The Island"]:
        suffixes.extend([f"| {publisher}", f"- {publisher}", f"– {publisher}", f"— {publisher}"])
    lower = headline.lower()
    for suffix in suffixes:
        if lower.endswith(suffix.lower()):
            return headline[:-len(suffix)].strip()
    return headline

def _extract_headline(soup: BeautifulSoup) -> str:
    headline = _meta_content(soup, property_name="og:title")
    if headline: return _clean_headline(headline)
    for item in _extract_jsonld_values(soup):
        if isinstance(item, dict) and isinstance(item.get("headline"), str) and item["headline"].strip():
            return _clean_headline(item["headline"])
    h1 = soup.find("h1")
    if h1:
        value = h1.get_text(" ", strip=True)
        if value: return _clean_headline(value)
    if soup.title:
        value = soup.title.get_text(" ", strip=True)
        if value: return _clean_headline(value)
    return ""

def _extract_subheadline(soup: BeautifulSoup) -> Optional[str]:
    description = _meta_content(soup, property_name="og:description") or _meta_content(soup, name="description")
    if not description:
        for item in _extract_jsonld_values(soup):
            if isinstance(item, dict) and isinstance(item.get("description"), str) and item["description"].strip():
                description = item["description"].strip(); break
    if not description: return None
    description = " ".join(description.split()).strip()
    return description or None

def _extract_author(soup: BeautifulSoup) -> Optional[str]:
    author = _meta_content(soup, name="author") or _meta_content(soup, property_name="article:author")
    if author: return author
    for item in _extract_jsonld_values(soup):
        if not isinstance(item, dict): continue
        data = item.get("author")
        if isinstance(data, dict):
            name = data.get("name")
            if isinstance(name, str) and name.strip(): return name.strip()
        elif isinstance(data, list):
            names = []
            for a in data:
                if isinstance(a, dict) and isinstance(a.get("name"), str) and a["name"].strip(): names.append(a["name"].strip())
                elif isinstance(a, str) and a.strip(): names.append(a.strip())
            if names: return ", ".join(names)
        elif isinstance(data, str) and data.strip(): return data.strip()
    return None

def _parse_date_candidate(value: str) -> Optional[str]:
    if not value: return None
    value = " ".join(str(value).split()).strip()
    if not value: return None
    try:
        parsed = dateparser.parse(value, settings={"RETURN_AS_TIMEZONE_AWARE": True, "TIMEZONE": "Asia/Colombo", "TO_TIMEZONE": "Asia/Colombo", "DATE_ORDER": "DMY", "PREFER_DATES_FROM": "past"})
    except Exception:
        return None
    return parsed.isoformat() if parsed else None

def _extract_published_date(soup: BeautifulSoup) -> Optional[str]:
    candidates: list[str] = []
    for prop in ["article:published_time", "og:published_time", "article:published"]:
        value = _meta_content(soup, property_name=prop)
        if value: candidates.append(value)
    for name in ["date", "datePublished", "datepublished", "publish-date", "publish_date", "publication_date", "publication-date", "pubdate", "article_date_original", "sailthru.date"]:
        value = _meta_content(soup, name=name)
        if value: candidates.append(value)
    for item in _extract_jsonld_values(soup):
        if isinstance(item, dict) and item.get("datePublished"): candidates.append(str(item["datePublished"]))
    for tag in soup.find_all("time"):
        if tag.get("datetime"): candidates.append(tag["datetime"])
        value = tag.get_text(" ", strip=True)
        if value: candidates.append(value)
    seen = set()
    for value in candidates:
        if value in seen: continue
        seen.add(value)
        parsed = _parse_date_candidate(value)
        if parsed: return parsed
    page_text = " ".join(soup.get_text(" ", strip=True).split())
    patterns = [
        (r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}(?:\s+\d{1,2}:\d{2})?\b", "DMY"),
        (r"\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}(?:\s+\d{1,2}:\d{2})?\b", "DMY"),
        (r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}(?:\s+\d{1,2}:\d{2})?\b", "MDY"),
    ]
    for pattern, order in patterns:
        match = re.search(pattern, page_text, re.IGNORECASE)
        if not match: continue
        if order == "DMY":
            parsed = _parse_date_candidate(match.group(0))
        else:
            parsed_obj = dateparser.parse(match.group(0), settings={"RETURN_AS_TIMEZONE_AWARE": True, "TIMEZONE": "Asia/Colombo", "TO_TIMEZONE": "Asia/Colombo", "DATE_ORDER": "MDY"})
            parsed = parsed_obj.isoformat() if parsed_obj else None
        if parsed: return parsed
    return None

def _extract_canonical_url(soup: BeautifulSoup, final_url: str) -> str:
    canonical = soup.find("link", attrs={"rel": "canonical"})
    if canonical and canonical.get("href") and canonical["href"].strip():
        return normalize_url(canonical["href"].strip())
    for item in _extract_jsonld_values(soup):
        if not isinstance(item, dict): continue
        value = item.get("url")
        if isinstance(value, str) and value.strip(): return normalize_url(value.strip())
        entity = item.get("mainEntityOfPage")
        if isinstance(entity, dict) and entity.get("@id"): return normalize_url(str(entity["@id"]))
        if isinstance(entity, str) and entity.strip(): return normalize_url(entity.strip())
    return normalize_url(final_url)

def _normalize_article_text(text: str) -> str:
    if not text: return ""
    return "\n\n".join(line for line in (" ".join(line.split()).strip() for line in text.splitlines()) if line)

def _extract_article_text(html: str, url: str) -> str:
    try:
        text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False, include_links=False, include_images=False, favor_precision=True, output_format="txt")
    except Exception:
        return ""
    return _normalize_article_text(text) if text else ""

def extract_article(*, html: str, source_name: str, section_name: str, source_url: str, final_url: str) -> ExtractedArticle:
    soup = BeautifulSoup(html, "lxml")
    headline = _extract_headline(soup)
    subheadline = _extract_subheadline(soup)
    author = _extract_author(soup)
    published_at = _extract_published_date(soup)
    canonical_url = _extract_canonical_url(soup, final_url)
    article_text = _extract_article_text(html, canonical_url)
    word_count = len(article_text.split())
    retrieved_at = datetime.now(timezone.utc).isoformat()
    article_content_hash = content_hash(article_text)
    return ExtractedArticle(source_name, section_name, source_url, final_url, canonical_url, headline, subheadline, author, published_at, article_text, word_count, retrieved_at, article_content_hash, "httpx")
