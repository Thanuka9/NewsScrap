from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from bank_intel.collectors.http_client import HttpClient
from bank_intel.collectors.sources.daily_ft import SOURCE_NAME, SECTION_NAME, FINANCIAL_SERVICES_URL, PAGE_SIZE, discover_articles
from bank_intel.extraction.article import extract_article
from bank_intel.extraction.validator import validate_article
from bank_intel.common.hashing import url_hash
from bank_intel.common.state import JsonArticleState
from bank_intel.common.collection_window import get_collection_window, parse_article_date, classify_intake_status
from bank_intel.common.source_status import get_run_id, write_source_status

SOURCE_KEY = "daily_ft"
SRI_LANKA_TZ = ZoneInfo("Asia/Colombo")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = PROJECT_ROOT / "data" / "raw" / "daily_ft"
STAGED_ROOT = PROJECT_ROOT / "data" / "staged" / "daily_ft"
STATE_FILE = PROJECT_ROOT / "data" / "state" / "daily_ft.json"
MAX_SECTION_PAGES = 5
REQUEST_DELAY_SECONDS = 1.0
RECHECK_AFTER_HOURS = 6

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def build_section_page_url(page_number: int) -> str:
    if page_number <= 1:
        return FINANCIAL_SERVICES_URL
    return f"{FINANCIAL_SERVICES_URL}/{(page_number - 1) * PAGE_SIZE}"

def should_fetch_article(state: JsonArticleState, canonical_url: str) -> tuple[bool, str]:
    record = state.get(canonical_url)
    if record is None:
        return True, "NEW"
    last_fetched = record.get("last_fetched_at")
    if not last_fetched:
        return True, "NEVER_FETCHED"
    try:
        last_dt = datetime.fromisoformat(last_fetched)
    except (TypeError, ValueError):
        return True, "INVALID_FETCH_TIME"
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    if (utc_now() - last_dt).total_seconds() / 3600 >= RECHECK_AFTER_HOURS:
        return True, "RECHECK"
    return False, "RECENTLY_FETCHED"

def save_raw_html(*, html: str, article_id: str, version_number: int, date_folder: str) -> Path:
    folder = RAW_ROOT / date_folder / article_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"v{version_number:03d}.html"
    path.write_text(html, encoding="utf-8")
    return path

def save_article_json(*, article, discovered, validation, article_id: str, version_number: int, date_folder: str, status: str, intake_status: str, window) -> Path:
    folder = STAGED_ROOT / date_folder / article_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"v{version_number:03d}.json"
    payload = article.to_dict()
    payload.update({
        "article_version": version_number,
        "collection_status": status,
        "intake_status": intake_status,
        "collection_run_date": window.run_date.isoformat(),
        "collection_window_start": window.start_date.isoformat(),
        "collection_window_end": window.end_date.isoformat(),
        "collection_mode": window.mode,
        "listing_headline": discovered.headline,
        "listing_published_at": discovered.published_at_hint,
        "validation": {"valid": validation.valid, "errors": validation.errors, "warnings": validation.warnings},
    })
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path

def discover_required_articles(*, client: HttpClient, window) -> list:
    discovered_by_url = {}
    print(f"Required publication window: {window.start_date} -> {window.end_date}")
    print(f"Mode: {window.mode}")
    for page_number in range(1, MAX_SECTION_PAGES + 1):
        page_url = build_section_page_url(page_number)
        print(f"[DISCOVERY] Page {page_number}: {page_url}")
        try:
            result = client.get(page_url)
        except Exception as exc:
            raise RuntimeError(f"Daily FT discovery failed on archive page {page_number}: {page_url}") from exc
        page_articles = discover_articles(result.html)
        print(f"Articles discovered: {len(page_articles)}")
        if not page_articles:
            break
        found_older = 0
        for item in page_articles:
            hint_date = parse_article_date(item.published_at_hint)
            if hint_date is None or window.contains_date(hint_date):
                discovered_by_url[item.url] = item
            elif hint_date < window.start_date:
                found_older += 1
        if found_older > 0:
            break
        time.sleep(REQUEST_DELAY_SECONDS)
    return list(discovered_by_url.values())

def collect() -> dict:
    window = get_collection_window()
    date_folder = window.run_date.isoformat()
    state = JsonArticleState(STATE_FILE)
    new_count = changed_count = unchanged_count = wrong_date_count = 0
    missing_date_count = invalid_count = failure_count = late_discovery_count = skipped_recent = 0
    with HttpClient() as client:
        discovered = discover_required_articles(client=client, window=window)
        candidate_count = len(discovered)
        for item in discovered:
            state.mark_seen(canonical_url=item.url, source_url=item.url, headline=item.headline)
        state.save()
        fetch_queue = []
        for item in discovered:
            should_fetch, reason = should_fetch_article(state, item.url)
            if should_fetch:
                fetch_queue.append((item, reason))
            else:
                skipped_recent += 1
        for discovered_article, reason in fetch_queue:
            try:
                result = client.get(discovered_article.url)
                article = extract_article(html=result.html, source_name=SOURCE_NAME, section_name=SECTION_NAME, source_url=discovered_article.url, final_url=result.final_url)
                validation = validate_article(article)
                actual_date = parse_article_date(article.published_at)
                if actual_date is None:
                    missing_date_count += 1
                    continue
                if not window.contains_date(actual_date):
                    wrong_date_count += 1
                    continue
                if not validation.valid:
                    invalid_count += 1
                    continue
                previous_hash = state.last_content_hash(article.canonical_url)
                if previous_hash is None:
                    status, changed = "NEW", True
                elif previous_hash != article.content_hash:
                    status, changed = "CHANGED", True
                else:
                    status, changed = "UNCHANGED", False
                intake_status = classify_intake_status(published_at=article.published_at, collection_status=status, window=window)
                if not changed:
                    unchanged_count += 1
                    state.update_after_fetch(canonical_url=article.canonical_url, source_url=article.source_url, headline=article.headline, content_hash=article.content_hash, published_at=article.published_at, changed=False)
                    state.save()
                    continue
                version_number = state.update_after_fetch(canonical_url=article.canonical_url, source_url=article.source_url, headline=article.headline, content_hash=article.content_hash, published_at=article.published_at, changed=True)
                state.save()
                article_id = url_hash(article.canonical_url)
                save_raw_html(html=result.html, article_id=article_id, version_number=version_number, date_folder=date_folder)
                save_article_json(article=article, discovered=discovered_article, validation=validation, article_id=article_id, version_number=version_number, date_folder=date_folder, status=status, intake_status=intake_status, window=window)
                if status == "NEW": new_count += 1
                else: changed_count += 1
                if intake_status == "LATE_DISCOVERY": late_discovery_count += 1
            except Exception as exc:
                failure_count += 1
                print(f"FAILED: {type(exc).__name__}: {exc}")
            finally:
                state.save()
            time.sleep(REQUEST_DELAY_SECONDS)
    if new_count > 0 or late_discovery_count > 0:
        content_status = "NEW_ARTICLES_FOUND"
    elif changed_count > 0:
        content_status = "ARTICLES_CHANGED"
    elif failure_count > 0:
        content_status = "ARTICLE_FETCH_FAILURES"
    elif invalid_count > 0:
        content_status = "ARTICLE_EXTRACTION_FAILURES"
    elif candidate_count == 0:
        content_status = "NO_CURRENT_UPDATE"
    else:
        content_status = "NO_NEW_ARTICLES"
    health_status = "PARTIAL_ERROR" if failure_count or invalid_count or missing_date_count else "HEALTHY"
    return {
        "source_name": SOURCE_NAME,
        "section_name": SECTION_NAME,
        "execution_status": "SUCCESS",
        "health_status": health_status,
        "content_status": content_status,
        "collection_mode": window.mode,
        "window_start": window.start_date.isoformat(),
        "window_end": window.end_date.isoformat(),
        "candidate_urls": candidate_count,
        "articles_requiring_fetch": len(fetch_queue),
        "new_articles": new_count,
        "late_discoveries": late_discovery_count,
        "changed_articles": changed_count,
        "unchanged_articles": unchanged_count,
        "wrong_date_skipped": wrong_date_count,
        "missing_date": missing_date_count,
        "invalid_extraction": invalid_count,
        "fetch_failures": failure_count,
        "recent_duplicates": skipped_recent,
    }

def main():
    run_id = get_run_id()
    started_at = datetime.now(SRI_LANKA_TZ)
    try:
        metrics = collect()
        completed_at = datetime.now(SRI_LANKA_TZ)
        write_source_status(source_key=SOURCE_KEY, payload={"run_id": run_id, "started_at": started_at.isoformat(), "completed_at": completed_at.isoformat(), **metrics})
    except Exception as exc:
        completed_at = datetime.now(SRI_LANKA_TZ)
        write_source_status(source_key=SOURCE_KEY, payload={"run_id": run_id, "source_name": SOURCE_NAME, "section_name": SECTION_NAME, "started_at": started_at.isoformat(), "completed_at": completed_at.isoformat(), "execution_status": "FAILED", "health_status": "FAILED", "content_status": "SOURCE_FETCH_FAILED", "error_type": type(exc).__name__, "error_message": str(exc)})
        raise

if __name__ == "__main__":
    main()
