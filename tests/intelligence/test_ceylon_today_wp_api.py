"""Offline Ceylon Today API discovery regression; no network."""
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
