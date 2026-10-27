"""Install Ceylon Today WordPress API discovery in the existing News Scrap collector.
Run from the News Scrap project root.
Does NOT run collection or alter any published reports.
"""
from pathlib import Path
from datetime import datetime
import shutil
import sys

ROOT = Path.cwd()
RUNNER = ROOT / 'scripts' / 'run_ceylon_today.py'
MODULE = ROOT / 'src' / 'bank_intel' / 'collectors' / 'sources' / 'ceylon_today_wp_api.py'
MARKER = 'CEYLON_TODAY_WP_API_V1'

MODULE_SOURCE = r'''"""Ceylon Today public WordPress REST API discovery, with date-boundary audit."""
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
'''

TEST_SOURCE = r'''"""Offline Ceylon Today API discovery regression; no network."""
from datetime import date
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import json
import pytest
from bank_intel.collectors.sources.ceylon_today_wp_api import discover_wp_posts

class Window:
    start_date = date(2026, 10, 19)
    end_date = date(2026, 10, 21)
    def contains_date(self, x):
        return self.start_date <= x <= self.end_date

class Client:
    def __init__(self, rows, *, broken=False):
        self.rows = rows
        self.calls = []
        self.broken = broken
    def get(self, url):
        self.calls.append(url)
        params = parse_qs(urlsplit(url).query)
        offset = (int(params["page"][0]) - 1) * int(params["per_page"][0])
        data = self.rows[offset:offset+100]
        if self.broken: data = "not-json"
        return SimpleNamespace(status_code=200, final_url=url,
                               html=json.dumps(data) if not self.broken else data)

def post(i, day):
    return {"date": f"2026-10-{day:02d}T12:00:00",
            "link": f"https://ceylontoday.lk/2026/10/{day:02d}/bank-story-{i}/",
            "title": {"rendered": f"Bank &amp; finance story {i}"},
            "excerpt": {"rendered": "Funding &amp; credit"}}

def test_recent_wp_articles_discovered_from_current_api():
    client = Client([post(i,21) for i in range(5)]+[post(i,17) for i in range(5)])
    found, audit = discover_wp_posts(client=client, window=Window())
    assert len(found) == 5
    assert all("Bank & finance" in x.headline for x in found)
    assert audit["api_status"] == "VERIFIED"
    assert audit["api_pagination_complete"] and audit["api_pages"] == 1

def test_more_than_100_recent_posts_not_truncated():
    rows = [post(i,21) for i in range(121)] + [post(200+i,17) for i in range(20)]
    client=Client(rows)
    found,audit=discover_wp_posts(client=client,window=Window())
    assert len(found)==121 and audit["api_pages"]==2
    assert audit["api_pagination_complete"]

def test_invalid_api_does_not_report_healthy_no_update():
    with pytest.raises(RuntimeError,match="CEYLON_API_INVALID_JSON"):
        discover_wp_posts(client=Client([post(1,21)],broken=True),window=Window())

def test_no_window_articles_is_audited_not_silent():
    found,audit=discover_wp_posts(client=Client([post(1,17)]),window=Window())
    assert not found and audit["api_posts_checked"]==1
    assert audit["api_pagination_complete"]
'''

TEST = ROOT / 'tests' / 'intelligence' / 'test_ceylon_today_wp_api.py'

if not RUNNER.is_file():
    raise SystemExit('Run this installer from the News Scrap project root; scripts/run_ceylon_today.py not found.')

source = RUNNER.read_text(encoding='utf-8-sig')
if MARKER in source:
    print('[PASS] Ceylon Today WordPress API integration already installed; no changes.')
    raise SystemExit(0)

old_def = 'def discover_required_articles(\n'
old_collect = 'def collect() -> dict:\n'
if source.count(old_def) != 1 or source.count(old_collect) != 1:
    raise RuntimeError('Unexpected Ceylon Today collector layout. Nothing changed.')

# Do not replace existing archive discovery logic. Wrap it and add API results.
source = source.replace(old_def, 'def _discover_required_articles_archives(\n', 1)
wrapper = '''# CEYLON_TODAY_WP_API_V1
_CEYLON_WP_API_AUDIT = {}


def discover_required_articles(*, client, window):
    global _CEYLON_WP_API_AUDIT
    from bank_intel.collectors.sources.ceylon_today_wp_api import discover_wp_posts
    wp_articles, _CEYLON_WP_API_AUDIT = discover_wp_posts(client=client, window=window)
    print("[CEYLON WORDPRESS API]", _CEYLON_WP_API_AUDIT)
    archive_articles = _discover_required_articles_archives(client=client, window=window)
    unique = {item.url: item for item in archive_articles}
    for item in wp_articles:
        unique[item.url] = item
    print(f"[CEYLON DISCOVERY] Archive={len(archive_articles)} API={len(wp_articles)} Unique={len(unique)}")
    return list(unique.values())


'''
source = source.replace(old_collect, wrapper + old_collect, 1)

# Record actual API evidence in the existing source status, including zero-current cases.
if '"discovery_surfaces": [name for name, _ in CEYLON_SURFACES],' in source:
    source = source.replace(
        '"discovery_surfaces": [name for name, _ in CEYLON_SURFACES],',
        '"discovery_surfaces": [name for name, _ in CEYLON_SURFACES] + ["WORDPRESS_API"],\n'
        '        "wp_api_discovery": dict(_CEYLON_WP_API_AUDIT),', 1)
elif '"discovery_surfaces"' not in source:
    anchor = '    return {\n        "source_name": SOURCE_NAME,\n'
    if source.count(anchor) != 1:
        raise RuntimeError('Unknown source status structure; nothing changed.')
    source = source.replace(
        anchor,
        anchor + '        "discovery_surfaces": ["DAILY_FINANCE", "SUNDAY_FINANCE", "HOMEPAGE", "WORDPRESS_API"],\n'
        '        "wp_api_discovery": dict(_CEYLON_WP_API_AUDIT),\n', 1)
else:
    raise RuntimeError('Custom discovery surface declaration detected; nothing changed.')

compile(source, str(RUNNER), 'exec')
compile(MODULE_SOURCE, str(MODULE), 'exec')
compile(TEST_SOURCE, str(TEST), 'exec')

stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
backup = ROOT / 'data' / 'integration_backups' / ('ceylon_today_api_' + stamp)
backup.mkdir(parents=True, exist_ok=True)
shutil.copy2(RUNNER, backup / RUNNER.name)
if MODULE.exists(): shutil.copy2(MODULE, backup / MODULE.name)
if TEST.exists(): shutil.copy2(TEST, backup / TEST.name)

try:
    MODULE.write_text(MODULE_SOURCE, encoding='utf-8')
    TEST.parent.mkdir(parents=True, exist_ok=True)
    TEST.write_text(TEST_SOURCE, encoding='utf-8')
    RUNNER.write_text(source, encoding='utf-8')
    import subprocess
    checked = subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', str(TEST)],
        cwd=ROOT, timeout=90, check=False)
    if checked.returncode != 0:
        raise RuntimeError(f'WordPress API regression tests failed: {checked.returncode}')
except Exception:
    shutil.copy2(backup / RUNNER.name, RUNNER)
    if (backup / MODULE.name).exists(): shutil.copy2(backup / MODULE.name, MODULE)
    elif MODULE.exists(): MODULE.unlink()
    if (backup / TEST.name).exists(): shutil.copy2(backup / TEST.name, TEST)
    elif TEST.exists(): TEST.unlink()
    print('[ROLLBACK] Restored previous Ceylon Today collector.')
    raise

print('[PASS] Ceylon Today official WordPress API discovery installed')
print('[PASS] API pagination, in-window dates, HTML entities, invalid-feed rejection')
print('[BACKUP]', backup)
print('[UNCHANGED] All other collectors, ML, daily HTML and CSV reports')
print('NEXT: python .\\scripts\\run_ceylon_today.py')
