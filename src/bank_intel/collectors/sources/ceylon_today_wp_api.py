"""Ceylon Today public WordPress REST API discovery, with date-boundary audit."""
from __future__ import annotations
import json
import re
import time
from datetime import date
from html import unescape
from urllib.parse import urlencode, urlparse
from bs4 import BeautifulSoup
from bank_intel.collectors.sources.ceylon_today import (
    DiscoveredCeylonTodayArticle, publication_hint_from_url,
)
from bank_intel.common.collection_window import parse_article_date
from bank_intel.common.urls import normalize_url, domain_matches

HOST = "https://ceylontoday.lk/wp-json/wp/v2/posts"
PAGE_SIZE = 100
MAX_PAGES = 20


def _plain(value):
    if isinstance(value, dict):
        value = value.get("rendered", "")
    return " ".join(unescape(BeautifulSoup(str(value or ""), "html.parser")
                              .get_text(" ", strip=True)).split())


def discover_wp_posts(*, client, window):
    """Return (discovered articles, audit). Raise on stale/invalid/truncated API.

    API is site-wide: editorial ML decides which articles are finance-relevant.
    WordPress `date` is not presumed UTC; URL calendar dates are used for
    discovery, while the article extractor independently verifies publication.
    """
    found = {}
    audit = {"api_status": "IN_PROGRESS", "api_pages": 0,
             "api_posts_checked": 0, "api_window_candidates": 0,
             "api_invalid_links": 0, "api_date_mismatches": 0,
             "api_pagination_complete": False,
             "api_oldest_date": None, "api_newest_date": None}
    previous_last = None
    for page in range(1, MAX_PAGES + 1):
        url = HOST + "?" + urlencode({
            "per_page": PAGE_SIZE, "page": page,
            "orderby": "date", "order": "desc",
            "_fields": "date,link,title,excerpt",
        })
        response = client.get(url)
        if getattr(response, "status_code", None) != 200:
            raise RuntimeError(f"CEYLON_API_HTTP_ERROR: page={page} status={getattr(response, 'status_code', 'unknown')}")
        if "/wp-json/wp/v2/posts" not in str(getattr(response, "final_url", url)):
            raise RuntimeError(f"CEYLON_API_UNEXPECTED_REDIRECT: page={page}")
        try:
            rows = json.loads(response.html)
        except (ValueError, TypeError) as exc:
            raise RuntimeError(f"CEYLON_API_INVALID_JSON: page={page}") from exc
        if not isinstance(rows, list) or (page == 1 and not rows):
            raise RuntimeError(f"CEYLON_API_INVALID_OR_EMPTY_FEED: page={page}")
        audit["api_pages"] += 1
        audit["api_posts_checked"] += len(rows)
        page_dates = []
        for item in rows:
            if not isinstance(item, dict):
                raise RuntimeError("CEYLON_API_BAD_POST_RECORD")
            raw_date = str(item.get("date") or "")[:10]
            try:
                api_date = date.fromisoformat(raw_date)
            except ValueError as exc:
                raise RuntimeError(f"CEYLON_API_MISSING_POST_DATE: page={page}") from exc
            page_dates.append(api_date)
            full_url = str(item.get("link") or "")
            if not full_url.startswith(("http://", "https://")) or not domain_matches(full_url, "ceylontoday.lk"):
                audit["api_invalid_links"] += 1
                continue
            full_url = normalize_url(full_url)
            hint = publication_hint_from_url(full_url)
            if not hint:
                audit["api_invalid_links"] += 1
                continue
            article_date = parse_article_date(hint)
            if article_date != api_date:
                audit["api_date_mismatches"] += 1
                # Defer to article extraction for a definitive publication date.
                # Include either in-window date to avoid silent omissions.
            if not (window.contains_date(api_date) or window.contains_date(article_date)):
                continue
            headline = _plain(item.get("title"))
            if not headline:
                audit["api_invalid_links"] += 1
                continue
            excerpt = _plain(item.get("excerpt")) or None
            found[full_url] = DiscoveredCeylonTodayArticle(
                headline=headline, url=full_url, published_at_hint=hint,
                excerpt=excerpt)
        if page_dates:
            if page_dates != sorted(page_dates, reverse=True):
                raise RuntimeError(f"CEYLON_API_OUT_OF_ORDER_POSTS: page={page}")
            if previous_last is not None and page_dates[0] > previous_last:
                raise RuntimeError(f"CEYLON_API_CROSS_PAGE_ORDER_ERROR: page={page}")
            previous_last = page_dates[-1]
            if audit["api_newest_date"] is None:
                audit["api_newest_date"] = page_dates[0].isoformat()
            audit["api_oldest_date"] = page_dates[-1].isoformat()
        if len(rows) < PAGE_SIZE or (page_dates and page_dates[-1] < window.start_date):
            audit["api_pagination_complete"] = True
            break
        time.sleep(0.25)
    if not audit["api_pagination_complete"]:
        raise RuntimeError("CEYLON_API_DISCOVERY_TRUNCATED: raise limit only after inspecting coverage")
    audit["api_status"] = "VERIFIED"
    audit["api_window_candidates"] = len(found)
    return list(found.values()), audit
