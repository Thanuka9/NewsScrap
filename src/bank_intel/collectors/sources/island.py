from __future__ import annotations

from bs4 import BeautifulSoup
from datetime import date
from email.utils import parsedate_to_datetime
from html import escape
import json
import re
from urllib.parse import urljoin, urlparse, urlunparse
import xml.etree.ElementTree as ET

import httpx

from bank_intel.collectors.sources._public_business_base import (
    BusinessSourceAdapter,
    DEFAULT_HEADERS,
    FetchResult,
    SRI_LANKA_TZ,
    canonicalize_url,
)


class IslandBusinessAdapter(BusinessSourceAdapter):
    source_key = "island"
    source_name = "The Island"
    section_name = "Business"
    section_url = "https://island.lk/category/business/"
    allow_browser_fallback = True

    stop_markers = (
        "continue reading",
        "related topics",
        "you may like",
        "recommended",
        "comments",
        "all news advertisement",
    )

    ARTICLE_TAIL_MARKERS = (
        r"\ball\s+news\s+advertisement\b",
    )

    ARTICLE_PREFIX_LABELS = {
        "business",
        "published",
        "on",
        "by",
        "share",
        "tweet",
    }

    REJECT_PREFIXES = (
        "/category/",
        "/tag/",
        "/author/",
        "/page/",
        "/archives",
        "/contact-us",
        "/conatct-us",
        "/privacy-policy",
    )

    REJECT_SLUGS = {
        "classifieds",
        "contact-us",
        "conatct-us",
        "archives",
        "privacy-policy",
        "about-us",
        "advertise",
        "advertising",
        "subscriptions",
        "subscription",
    }

    GLOBAL_NOISE_SELECTORS = (
        "nav",
        "header",
        "footer",
        "aside",
        ".menu",
        ".navbar",
        ".navigation",
        ".site-header",
        ".site-footer",
        ".widget-area",
    )

    POST_LIKE_SELECTORS = (
        "article",
        ".post",
        ".post-item",
        ".post-card",
        ".blog-post",
        ".blog-item",
        ".article",
        ".article-item",
        "[class*='post-']",
        "[class*='post_']",
        "[class*='blog-']",
        "[class*='article-']",
    )

    @classmethod
    def _clean_island_article_text(
        cls,
        value: str,
    ) -> str:
        """
        Remove The Island's article-page recommendation/feed tail.

        Live article pages append an "All News Advertisement" section
        containing other stories. The generic extractor can otherwise
        treat that feed as part of the current article, contaminating
        entity detection and Stage-1 relevance scoring.
        """

        text = str(
            value
            or ""
        ).strip()

        if not text:
            return ""

        # Hard boundary: everything after this belongs to the site's
        # recommendation/news feed, not the current article.
        cut_positions = []

        for pattern in cls.ARTICLE_TAIL_MARKERS:
            match = re.search(
                pattern,
                text,
                flags=re.I,
            )
            if match:
                cut_positions.append(
                    match.start()
                )

        if cut_positions:
            text = text[
                : min(cut_positions)
            ].rstrip()

        # Remove only the exact metadata/share labels that The Island
        # places before the first real paragraph. Do not remove a real
        # byline such as "By Hiran H. Senewiratne".
        lines = [
            line.strip()
            for line in text.splitlines()
        ]

        while (
            lines
            and (
                not lines[0]
                or lines[0].lower()
                in cls.ARTICLE_PREFIX_LABELS
            )
        ):
            lines.pop(0)

        return "\\n\\n".join(
            line
            for line in lines
            if line
        ).strip()

    def extract(self, url, fetch):
        article = super().extract(
            url,
            fetch,
        )

        article.article_text = (
            self._clean_island_article_text(
                article.article_text
            )
        )

        # Recompute only the length-derived warning after the
        # Island-specific cleanup.
        warnings = [
            warning
            for warning in (
                article.warnings
                or []
            )
            if not warning.startswith(
                "ARTICLE_TOO_SHORT:"
            )
        ]

        word_count = len(
            article.article_text.split()
        )

        if word_count < 80:
            warnings.append(
                f"ARTICLE_TOO_SHORT:{word_count}"
            )

        article.warnings = warnings

        return article

    def listing_url(self, page_number: int) -> str:
        if page_number <= 1:
            return self.section_url

        return (
            "https://island.lk/category/business/"
            f"page/{page_number}/"
        )

    # NEWS_ISLAND_OFFICIAL_RSS_FALLBACK_V1
    # Live article/category pages intermittently return a Sucuri 307 challenge.
    # Official WordPress RSS endpoints supply full content:encoded bodies.
    # Do not treat the 56-word RSS description as a full article.
    RSS_ENDPOINTS = {
        "business": "https://island.lk/category/business/feed/",
        "news": "https://island.lk/category/news/feed/",
        "homepage": "https://island.lk/feed/",
    }
    RSS_CONTENT_TAG = "{http://purl.org/rss/1.0/modules/content/}encoded"

    def _rss_state(self):
        # Per-instance, per-run state; a fresh collector tries the normal
        # website first again on every new run.
        if not hasattr(self, "_rss_item_cache"):
            self._rss_item_cache = {}
            self._rss_pages = {}
            self._rss_mode = False
            self._rss_recovered_urls = set()
            self._rss_fallback_errors = []
            self._rss_failed_pages = {}

    def rss_diagnostics(self) -> dict:
        self._rss_state()
        return {
            "used": bool(self._rss_mode),
            "feed_pages": dict(self._rss_pages),
            "full_articles_recovered": len(self._rss_recovered_urls),
            "errors": list(self._rss_fallback_errors),
        }

    def listing_publication_date(self, url: str) -> date | None:
        """A known publication date for a discovered URL, before fetching it.

        The Island's URLs carry no date and its origin server is slow, so
        fetching an old article only to learn it is outside the window costs
        minutes. Use the official feed's pubDate, else the date of the same
        article already collected on an earlier day. Unknown URLs are fetched
        normally; dates are never guessed.
        """
        self._rss_state()
        key = canonicalize_url(url)
        record = self._rss_item_cache.get(key)
        raw = record.get("published_at") if record else None
        if not raw:
            raw = self._collected_dates().get(key)
        if not raw:
            return None
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None

    def _collected_dates(self) -> dict:
        """Publication dates of Island articles staged in the last ten days."""
        if getattr(self, "_collected_date_index", None) is None:
            from datetime import timedelta
            from pathlib import Path
            index = {}
            root = Path(__file__).resolve().parents[4] / "data" / "staged" / "island"
            oldest = (date.today() - timedelta(days=10)).isoformat()
            for folder in sorted(root.glob("20*")) if root.is_dir() else []:
                if folder.name < oldest:
                    continue
                for path in folder.glob("*/v001.json"):
                    try:
                        entry = json.loads(path.read_text(encoding="utf-8-sig"))
                    except (OSError, ValueError):
                        continue
                    if entry.get("source_url") and entry.get("published_at"):
                        index[canonicalize_url(entry["source_url"])] = str(entry["published_at"])
            self._collected_date_index = index
        return self._collected_date_index

    def _rss_listing_location(self, url: str):
        parsed = urlparse(url)
        if parsed.hostname not in ("island.lk", "www.island.lk"):
            return None
        path = parsed.path.strip("/").lower()
        if path == "":
            return ("homepage", 1)
        if path.startswith("category/business"):
            section, remaining = "business", path.removeprefix("category/business").strip("/")
        elif path.startswith("category/news"):
            section, remaining = "news", path.removeprefix("category/news").strip("/")
        else:
            return None
        if not remaining:
            return section, 1
        match = re.fullmatch(r"page/(\d+)", remaining)
        if match:
            return section, int(match.group(1))
        return None

    def _read_rss_page(self, section: str, page: int) -> list[dict]:
        self._rss_state()
        if section not in self.RSS_ENDPOINTS or not 1 <= page <= 12:
            raise RuntimeError(f"Island RSS unsupported section/page: {section}/{page}")
        key = f"{section}:{page}"
        if key in self._rss_pages:
            return self._rss_pages[key]["items"]
        if key in self._rss_failed_pages:
            raise RuntimeError(self._rss_failed_pages[key])
        feed_url = self.RSS_ENDPOINTS[section]
        if page > 1:
            feed_url += f"?paged={page}"
        try:
            with httpx.Client(
                follow_redirects=True, timeout=25.0,
                headers={**DEFAULT_HEADERS,
                         "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8"},
            ) as client:
                response = client.get(feed_url)
            if response.status_code != 200 or len(response.content) > 2_000_000:
                raise RuntimeError(f"Island RSS HTTP {response.status_code} or oversized response")
            xml_root = ET.fromstring(response.content)
            item_nodes = xml_root.findall(".//channel/item")
            if not item_nodes:
                raise RuntimeError("Island RSS returned no items")
            items = []
            for item in item_nodes:
                source_url = self._force_https((item.findtext("link") or "").strip())
                if not self.is_article_url(source_url):
                    continue
                title = (item.findtext("title") or "").strip()
                published_raw = (item.findtext("pubDate") or "").strip()
                try:
                    published = parsedate_to_datetime(published_raw)
                    if published.tzinfo is None:
                        raise ValueError("RSS date missing timezone")
                    published_at = published.astimezone(SRI_LANKA_TZ).isoformat()
                except (ValueError, TypeError):
                    published_at = None
                full_html = item.findtext(self.RSS_CONTENT_TAG) or ""
                body_soup = BeautifulSoup(full_html, "html.parser")
                for bad in body_soup.select("script, style, noscript, iframe, form, nav, aside, footer"):
                    bad.decompose()
                # Preserve factual text while stripping HTML and most CMS widgets.
                full_text = "\n\n".join(
                    p.get_text(" ", strip=True)
                    for p in body_soup.find_all(("p", "h2"))
                    if p.get_text(" ", strip=True)
                )
                if not full_text:
                    full_text = body_soup.get_text(" ", strip=True)
                full_text = self._clean_island_article_text(full_text)
                record = {"url": source_url, "headline": title,
                          "published_at": published_at, "article_text": full_text}
                items.append(record)
            if not items:
                raise RuntimeError("Island RSS contains no valid article URLs")
            # Some RSS implementations return page 1 when asked for page 2.
            # Treat that as a coverage gap, not a successful archive page.
            first = self._rss_pages.get(f"{section}:1")
            if page > 1 and first and (
                {it["url"] for it in items}.issubset(
                    {it["url"] for it in first["items"]}
                )
            ):
                raise RuntimeError(f"Island RSS page {page} repeats the first page")
            self._rss_pages[key] = {"url": feed_url, "count": len(items), "items": items}
            for record in items:
                # All sources share canonical URL keys; duplicates across feeds
                # are resolved by run_collection's existing URL de-duplication.
                self._rss_item_cache[canonicalize_url(record["url"])] = record
            return items
        except (httpx.HTTPError, ET.ParseError, RuntimeError, ValueError) as exc:
            self._rss_fallback_errors.append(f"{feed_url}: {exc}")
            # Retry in the next collection/recovery run, not for every article.
            self._rss_failed_pages[key] = str(exc)
            raise RuntimeError(f"Island official RSS unavailable: {exc}") from exc

    def _rss_for_url(self, url: str) -> FetchResult:
        self._rss_state()
        requested = canonicalize_url(url)
        listing = self._rss_listing_location(requested)
        if listing is not None:
            section, page = listing
            items = self._read_rss_page(section, page)
            cards = "".join(
                f'<article><h2><a href="{escape(it["url"], quote=True)}">'
                f'{escape(it["headline"])}</a></h2></article>'
                for it in items
            )
            return FetchResult(requested_url=url, final_url=requested,
                               html=f"<html><body>{cards}</body></html>",
                               status_code=200, method="rss-listing")
        if not self.is_article_url(requested):
            raise RuntimeError(f"Island RSS cannot resolve non-article URL: {url}")
        record = self._rss_item_cache.get(requested)
        if not record:
            # Direct article calls (without a listing fetch) can consult the
            # latest official feeds. Historical URL recovery is not guaranteed.
            # A collection window spans about two days, deeper than one
            # ten-item feed page. Deeper feed pages are uncached and take
            # ~15 s each, so search only the second page of the main feeds.
            for page in (1, 2):
                for section in (("business", "news", "homepage") if page == 1 else ("business", "news")):
                    try:
                        self._read_rss_page(section, page)
                    except RuntimeError:
                        # One feed may be unavailable while another is live.
                        # The failed feed remains visible in RSS diagnostics.
                        continue
                    record = self._rss_item_cache.get(requested)
                    if record:
                        break
                if record:
                    break
        if not record:
            raise RuntimeError(f"Island RSS full content not present for article: {url}")
        word_count = len(record["article_text"].split())
        # Official full-content fields may contain genuine short notices.
        # Preserve 25-79 word source notices for ML/editorial review with a
        # visible warning. Never replace missing content with RSS descriptions.
        if word_count < 25 or not record["published_at"] or not record["headline"]:
            raise RuntimeError(
                f"Island RSS article incomplete: {word_count} words; "
                f"dated={bool(record['published_at'])}: {url}"
            )
        structured = json.dumps({"@context": "https://schema.org",
                                 "@type": "NewsArticle",
                                 "headline": record["headline"],
                                 "datePublished": record["published_at"],
                                 "articleBody": record["article_text"]}, ensure_ascii=False)
        synthetic = ("<html><head><script type=\"application/ld+json\">"
                     + structured.replace("</", "<\\/")
                     + "</script></head><body><article></article></body></html>")
        self._rss_recovered_urls.add(requested)
        return FetchResult(requested_url=url, final_url=requested, html=synthetic,
                           status_code=200, method="rss-full-content")

    def fetch(self, url: str) -> FetchResult:
        self._rss_state()
        if self._rss_mode:
            return self._rss_for_url(url)
        # The Island's origin server is slow or erroring for uncached pages,
        # while its first feed pages are served from cache in under a second
        # and carry the full article. Use the feed copy when it has one.
        if self.is_article_url(url):
            for section in ("business", "news", "homepage"):
                try:
                    self._read_rss_page(section, 1)
                except RuntimeError:
                    continue
                record = self._rss_item_cache.get(canonicalize_url(url))
                if record and record["published_at"] and len(record["article_text"].split()) >= 25:
                    return self._rss_for_url(url)
            record = self._rss_item_cache.get(canonicalize_url(url))
            if record and record["published_at"] and len(record["article_text"].split()) >= 25:
                return self._rss_for_url(url)
        try:
            # Primary route: keep the normal publisher page and browser
            # fallback unchanged while they are available.
            return super().fetch(url)
        except Exception as exc:
            detail = str(exc).lower()
            # The publisher's own server errors (HTTP 5xx) on a page that the
            # official feed carries in full: recover that page only.
            if re.search(r"status[= ]5\d\d|http 5\d\d|\b5\d\d server error", detail):
                try:
                    recovered = self._rss_for_url(url)
                except RuntimeError:
                    raise exc
                print(f"[ISLAND] Publisher server error; recovered from official RSS: {url}", flush=True)
                return recovered
            # Only switch to RSS for a recognised Sucuri redirect/challenge;
            # unrelated network errors are handled by existing retry logic.
            if not ("307" in detail or "you are being redirected" in detail):
                raise
            recovered = self._rss_for_url(url)
            self._rss_mode = True
            print("[ISLAND] Sucuri redirect detected; using full-content official RSS feed", flush=True)
            return recovered

    @staticmethod
    def _force_https(url: str) -> str:
        canonical = canonicalize_url(url)
        parsed = urlparse(canonical)

        if parsed.netloc in {
            "island.lk",
            "www.island.lk",
        }:
            return urlunparse(
                (
                    "https",
                    parsed.netloc,
                    parsed.path,
                    "",
                    "",
                    "",
                )
            )

        return canonical

    def is_article_url(self, url: str) -> bool:
        canonical = self._force_https(url)
        parsed = urlparse(canonical)

        if parsed.netloc not in {
            "island.lk",
            "www.island.lk",
        }:
            return False

        path = parsed.path or "/"
        low = path.lower()

        if path == "/":
            return False

        if any(
            low.startswith(prefix)
            for prefix in self.REJECT_PREFIXES
        ):
            return False

        # Current Island posts are root-level slugs:
        # https://island.lk/example-story/
        segments = [
            item
            for item in path.split("/")
            if item
        ]

        if len(segments) != 1:
            return False

        slug = segments[0].lower()

        if slug in self.REJECT_SLUGS:
            return False

        # Homepage advertisement slots are posts too ('homepage-advertiesment-left-1').
        if re.search(r"advertie?se?ment", slug):
            return False

        if len(slug) < 8:
            return False

        return bool(
            re.fullmatch(
                r"[a-z0-9][a-z0-9\-]*",
                slug,
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def _anchor_text(anchor) -> str:
        return " ".join(
            (
                anchor.get_text(
                    " ",
                    strip=True,
                )
                or ""
            ).split()
        )

    def _looks_like_listing_story_anchor(
        self,
        anchor,
    ) -> bool:
        """
        The Island category page does not consistently expose the
        category marker inside each local article card. Therefore:
        - first remove global header/nav/footer/aside areas;
        - then accept headline/card anchors, not arbitrary root links.

        This avoids the sports/menu contamination seen in the first
        live collector run without requiring a Business tag that the
        live card markup does not reliably contain.
        """

        text = self._anchor_text(
            anchor
        )

        if not text:
            return False

        low_text = text.lower()

        # The Island's rendered category cards often expose one large
        # anchor whose text begins with "Business" followed by time,
        # headline and excerpt.
        if low_text.startswith(
            "business"
        ):
            return True

        # Headline links commonly sit inside h1-h4 elements.
        parent = getattr(
            anchor,
            "parent",
            None,
        )

        if (
            parent is not None
            and str(
                getattr(
                    parent,
                    "name",
                    "",
                )
                or ""
            ).lower()
            in {
                "h1",
                "h2",
                "h3",
                "h4",
            }
        ):
            return True

        # Or they sit inside a local article/post card.
        for ancestor in anchor.parents:
            tag_name = str(
                getattr(
                    ancestor,
                    "name",
                    "",
                )
                or ""
            ).lower()

            if tag_name in {
                "nav",
                "header",
                "footer",
                "aside",
                "body",
                "html",
            }:
                break

            if tag_name == "article":
                return True

            classes = " ".join(
                ancestor.get(
                    "class"
                )
                or []
            ).lower()

            if any(
                token in classes
                for token in (
                    "post",
                    "article",
                    "blog",
                    "story",
                    "entry",
                )
            ):
                return True

        return False

    def discover_article_urls(
        self,
        html: str,
        base_url: str,
    ) -> list[str]:
        soup = BeautifulSoup(
            html,
            "html.parser",
        )

        # The live Island Business page includes sports and other
        # navigation article URLs before the Business listing.
        for selector in self.GLOBAL_NOISE_SELECTORS:
            for node in soup.select(
                selector
            ):
                node.decompose()

        found: list[str] = []

        for anchor in soup.select(
            "a[href]"
        ):
            href = (
                anchor.get("href")
                or ""
            ).strip()

            if not href:
                continue

            absolute = self._force_https(
                urljoin(
                    base_url,
                    href,
                )
            )

            if not self.is_article_url(
                absolute
            ):
                continue

            if not self._looks_like_listing_story_anchor(
                anchor
            ):
                continue

            found.append(
                absolute
            )

        # Preserve page order while removing duplicate image/headline
        # links to the same article.
        return list(
            dict.fromkeys(
                found
            )
        )


# NEWS_ISLAND_TRANSIENT_FETCH_RETRY_V1
# Retry temporary network failures only. Never suppress permanent failures.

import time as _island_retry_time

_ISLAND_ORIGINAL_FETCH = IslandBusinessAdapter.fetch

_ISLAND_TRANSIENT_ERRORS = (
    "err_socket_not_connected",
    "err_connection_reset",
    "err_connection_closed",
    "err_connection_aborted",
    "err_network_changed",
    "err_timed_out",
    "connectionreseterror",
    "connectionabortederror",
    "connecttimeout",
    "readtimeout",
    "timeouterror",
    "remoteprotocolerror",
    "timed out",
    "connection reset by peer",
    "network is unreachable",
)

def _island_fetch_retry(self, url):
    delays = (2,)
    # Each attempt includes HTTP and browser fallback. Allow only one retry;
    # this elapsed check prevents another attempt, not a hard wall-clock cap.
    started = _island_retry_time.monotonic()

    for attempt in range(2):
        try:
            return _ISLAND_ORIGINAL_FETCH(self, url)

        except Exception as exc:
            message = str(exc).lower()

            transient = any(
                signal in message
                for signal in _ISLAND_TRANSIENT_ERRORS
            )

            if not transient or attempt == 1 or _island_retry_time.monotonic() - started > 90:
                raise

            delay = delays[attempt]

            print(
                f"[ISLAND RETRY {attempt + 2}/2] "
                f"Waiting {delay}s: {url}",
                flush=True,
            )

            _island_retry_time.sleep(delay)

IslandBusinessAdapter.fetch = _island_fetch_retry
