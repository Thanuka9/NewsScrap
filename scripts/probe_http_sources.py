from __future__ import annotations

import httpx
import trafilatura
from bs4 import BeautifulSoup

TESTS = [
    {"name": "Daily News", "url": "https://dailynews.lk/category/business/"},
    {"name": "Daily Mirror", "url": "https://www.dailymirror.lk/business"},
    {"name": "Ceylon Today", "url": "https://ceylontoday.lk/category/ceylon-today-daily/finance-today/"},
    {"name": "The Island", "url": "https://island.lk/category/business/"},
    {"name": "Daily FT", "url": "https://www.ft.lk/financial-services/42"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def probe(client, name, url):
    print()
    print("=" * 78)
    print(name)
    print("=" * 78)
    print(f"URL: {url}")
    try:
        response = client.get(url)
    except Exception as exc:
        print(f"REQUEST FAILED: {type(exc).__name__}: {exc}")
        return
    print(f"Status       : {response.status_code}")
    print(f"Final URL    : {response.url}")
    print(f"Content-Type : {response.headers.get('content-type')}")
    print(f"HTML bytes   : {len(response.content)}")
    html = response.text
    soup = BeautifulSoup(html, "lxml")
    print(f"Page title   : {soup.title.get_text(' ', strip=True) if soup.title else None}")
    links = soup.find_all("a", href=True)
    print(f"Links        : {len(links)}")
    extracted = trafilatura.extract(html, include_comments=False, include_tables=False, output_format="txt")
    if extracted:
        print(f"Trafilatura  : {len(extracted.split())} words")
    else:
        print("Trafilatura  : NO TEXT")
    if response.status_code == 200:
        print("RESULT       : HTTP_ACCESS_OK")
    elif response.status_code == 403:
        print("RESULT       : HTTP_ACCESS_BLOCKED")
    elif 300 <= response.status_code < 400:
        print("RESULT       : REDIRECT")
    else:
        print(f"RESULT       : HTTP_{response.status_code}")


def main():
    print()
    print("=" * 78)
    print("HTTP-ONLY SOURCE ACCESS TEST")
    print("=" * 78)
    print("No Playwright or automated browser will be used.")
    with httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True) as client:
        for source in TESTS:
            probe(client, source["name"], source["url"])


if __name__ == "__main__":
    main()