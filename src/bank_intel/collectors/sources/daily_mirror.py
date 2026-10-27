from __future__ import annotations

from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin

from bank_intel.collectors.sources._public_business_base import (
    BusinessSourceAdapter,
    canonicalize_url,
)


class DailyMirrorBusinessAdapter(BusinessSourceAdapter):
    source_key = "daily_mirror"
    source_name = "Daily Mirror"
    section_name = "Business"
    section_url = "https://www.dailymirror.lk/business"
    allow_browser_fallback = True

    # Current Daily Mirror article forms include:
    # /business/<slug>/215-351912
    # /business-main/<slug>/245-347848
    # /business-news/<slug>/273-347840
    ARTICLE_RE = re.compile(
        r"^https?://(?:www\.)?dailymirror\.lk/"
        r"(?:business|business-main|business-news)/"
        r"[^/?#]+/\d+-\d+/?$",
        re.IGNORECASE,
    )

    REJECT_PATHS = (
        "/business/215/",
        "/business-news/273/",
        "/news/",
        "/archive",
        "/search",
        "/rss/",
        "/tag/",
        "/author/",
    )

    def listing_url(self, page_number: int) -> str:
        if page_number <= 1:
            return self.section_url

        # Daily Mirror's Business listing uses an offset-like final
        # path component. Five items per step is the most stable public
        # pagination form observed in the live section.
        offset = (page_number - 1) * 5
        return f"https://www.dailymirror.lk/business/215/{offset}"

    def is_article_url(self, url: str) -> bool:
        return bool(
            self.ARTICLE_RE.match(
                canonicalize_url(url)
            )
        )

    def discover_article_urls(
        self,
        html: str,
        base_url: str,
    ) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        found: list[str] = []

        for anchor in soup.select("a[href]"):
            href = (anchor.get("href") or "").strip()
            if not href:
                continue

            absolute = canonicalize_url(
                urljoin(base_url, href)
            )

            if self.is_article_url(absolute):
                found.append(absolute)

        # Preserve page order while removing duplicates.
        return list(dict.fromkeys(found))
