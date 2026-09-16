from __future__ import annotations

import json
import time
from pathlib import Path
from bank_intel.collectors.browser_client import BrowserClient
from bank_intel.collectors.sources.daily_news import SOURCE_NAME, SECTION_NAME, BUSINESS_URL, discover_articles
from bank_intel.common.collection_window import get_collection_window, parse_article_date
from bank_intel.common.hashing import url_hash
from bank_intel.extraction.article import extract_article
from bank_intel.extraction.validator import validate_article

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = PROJECT_ROOT / "data" / "raw" / "daily_news"
STAGED_ROOT = PROJECT_ROOT / "data" / "staged" / "daily_news"
HEADLESS = False
MAX_ARTICLE_TESTS = 5
REQUEST_DELAY_SECONDS = 1.0

def save_raw_html(*, html: str, article_id: str, date_folder: str) -> Path:
    folder = RAW_ROOT / date_folder / article_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "v001.html"
    path.write_text(html, encoding="utf-8")
    return path

def save_json(*, article, validation, discovered, article_id: str, date_folder: str) -> Path:
    folder = STAGED_ROOT / date_folder / article_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "v001.json"
    payload = article.to_dict()
    payload["listing_excerpt"] = discovered.excerpt
    payload["listing_published_at"] = discovered.published_at_hint
    payload["validation"] = {"valid": validation.valid, "errors": validation.errors, "warnings": validation.warnings}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path

def main():
    window = get_collection_window()
    date_folder = window.end_date.isoformat()
    print("=" * 76)
    print("DAILY NEWS BUSINESS BROWSER EXTRACTION TEST")
    print("=" * 76)
    print(f"Mode : {window.mode}\nFrom : {window.start_date}\nTo   : {window.end_date}\nBusiness page:\n{BUSINESS_URL}")
    with BrowserClient(headless=HEADLESS) as browser:
        print("[1] Opening Daily News Business page...")
        listing = browser.get(BUSINESS_URL)
        print(f"Status: {listing.status_code}\nFinal URL: {listing.final_url}")
        discovered = discover_articles(listing.html)
        print(f"[2] Discovered {len(discovered)} Business article URLs.")
        candidates = []
        for item in discovered:
            article_date = parse_article_date(item.published_at_hint)
            if article_date is not None and window.contains_date(article_date):
                candidates.append(item)
        print(f"[3] Articles in required date window: {len(candidates)}")
        for index, item in enumerate(candidates, start=1):
            print(f"{index:02d}. {item.headline}\n    Date: {item.published_at_hint}\n    URL : {item.url}")
            if item.excerpt:
                print(f"    Excerpt: {item.excerpt[:160]}")
        test_items = candidates[:MAX_ARTICLE_TESTS]
        print(f"[4] Testing {len(test_items)} article pages...")
        valid_count = blocked_count = invalid_count = 0
        for index, item in enumerate(test_items, start=1):
            print("-" * 76)
            print(f"[{index}/{len(test_items)}]\n{item.headline}\n{item.url}")
            try:
                result = browser.get_via_listing(listing_url=BUSINESS_URL, target_url=item.url)
                print(f"Browser status: {result.status_code}\nFinal URL: {result.final_url}")
                if result.status_code in {401, 403, 429}:
                    blocked_count += 1
                    print("STATUS: ARTICLE_ACCESS_BLOCKED")
                    continue
                article = extract_article(html=result.html, source_name=SOURCE_NAME, section_name=SECTION_NAME, source_url=item.url, final_url=result.final_url)
                validation = validate_article(article)
                print(f"Extracted headline: {article.headline}\nPublished: {article.published_at}\nWords: {article.word_count}\nValid: {validation.valid}")
                if validation.errors: print(f"Errors: {validation.errors}")
                if validation.warnings: print(f"Warnings: {validation.warnings}")
                if validation.valid:
                    article_id = url_hash(article.canonical_url)
                    raw_path = save_raw_html(html=result.html, article_id=article_id, date_folder=date_folder)
                    json_path = save_json(article=article, validation=validation, discovered=item, article_id=article_id, date_folder=date_folder)
                    valid_count += 1
                    print(f"STATUS: VALID\nSource Link: {article.source_url}\nRaw: {raw_path}\nJSON: {json_path}")
                else:
                    invalid_count += 1
                    print("STATUS: EXTRACTION_INVALID")
            except Exception as exc:
                invalid_count += 1
                print(f"STATUS: ERROR\n{type(exc).__name__}: {exc}")
            time.sleep(REQUEST_DELAY_SECONDS)
    print("=" * 76)
    print("DAILY NEWS TEST COMPLETE")
    print("=" * 76)
    print(f"Current-date candidates : {len(candidates)}\nFull valid articles     : {valid_count}\nBlocked article pages   : {blocked_count}\nInvalid/errors          : {invalid_count}")

if __name__ == "__main__":
    main()
