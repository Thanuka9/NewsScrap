from __future__ import annotations

import json
import re
import time
from types import SimpleNamespace
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from bank_intel.collectors.http_client import HttpClient
from bank_intel.common.discovery_policy import OldPageStopper

from bank_intel.collectors.sources.daily_ft import (
    SOURCE_NAME,
    SECTION_NAME,
    FINANCIAL_SERVICES_URL,
    PAGE_SIZE,
    discover_articles,
)

from bank_intel.extraction.article import (
    extract_article,
)

from bank_intel.extraction.validator import (
    validate_article,
)

from bank_intel.common.hashing import (
    url_hash,
)

from bank_intel.common.state import (
    JsonArticleState,
)

from bank_intel.common.collection_window import (
    get_collection_window,
    parse_article_date,
    classify_intake_status,
)

from bank_intel.common.source_status import (
    get_run_id,
    write_source_status,
)


# ============================================================
# SOURCE
# ============================================================

SOURCE_KEY = "daily_ft"

SRI_LANKA_TZ = ZoneInfo(
    "Asia/Colombo"
)


# ============================================================
# PATHS
# ============================================================

PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

RAW_ROOT = (
    PROJECT_ROOT
    / "data"
    / "raw"
    / "daily_ft"
)

STAGED_ROOT = (
    PROJECT_ROOT
    / "data"
    / "staged"
    / "daily_ft"
)

STATE_FILE = (
    PROJECT_ROOT
    / "data"
    / "state"
    / "daily_ft.json"
)


# ============================================================
# SETTINGS
# ============================================================

MAX_SECTION_PAGES = 5

REQUEST_DELAY_SECONDS = 1.0

RECHECK_AFTER_HOURS = 6

# NEWS_MULTI_SURFACE_FT_V1
# Daily FT material banking/economic stories can appear on Financial Services
# or Front Page. Both surfaces are first-class production discovery inputs.
FRONT_PAGE_URL = "https://www.ft.lk/Front-Page/44/0"
TOP_STORY_URL = "https://www.ft.lk/top-story/26/0"
DISCOVERY_SURFACES = (
    ("FINANCIAL_SERVICES", FINANCIAL_SERVICES_URL),
    ("FRONT_PAGE", FRONT_PAGE_URL),
    ("TOP_STORY", TOP_STORY_URL),
    ("HOMEPAGE", "https://www.ft.lk/"),
)
FT_ARTICLE_PATH_RE = re.compile(r"/\d{1,4}-\d{5,}/?$")


# NEWS_FT_GENERIC_LISTING_FALLBACK_V2
# The legacy Daily FT parser is tuned to the Financial Services layout.
# Front Page uses a different HTML structure.  Scan valid FT story links directly,
# extract their visible publication timestamp, and choose the parser that discovers
# the larger valid article set.  This also protects us from future listing-layout drift.
FT_LISTING_DATE_RE = re.compile(
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*"
    r"\d{1,2}\s+[A-Za-z]+\s+20\d{2}\s+\d{1,2}:\d{2}",
    re.IGNORECASE,
)


def discover_ft_listing_articles(
    html: str,
    listing_url: str,
) -> list:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    seen = set()

    for node in soup.select("a[href]"):
        href = str(node.get("href") or "").strip()
        if not href:
            continue

        url = urljoin(listing_url, href)
        url = url.split("#", 1)[0].split("?", 1)[0].rstrip("/")

        if not is_ft_article_url(url) or url in seen:
            continue

        anchor_text = " ".join(node.get_text(" ", strip=True).split())

        # FT normally places the timestamp in the same listing block as the link.
        # Walk a few parent levels because Front Page and Financial Services use
        # slightly different markup.
        date_text = ""
        container_text = anchor_text
        current = node

        for _ in range(4):
            if current is None:
                break

            try:
                candidate_text = " ".join(
                    current.get_text(" ", strip=True).split()
                )
            except Exception:
                candidate_text = ""

            match = FT_LISTING_DATE_RE.search(candidate_text)
            if match:
                date_text = match.group(0)
                container_text = candidate_text
                break

            current = getattr(current, "parent", None)

        # A listing link without a visible publication timestamp is not trusted
        # as a current-window discovery candidate.
        if not date_text:
            continue

        headline = FT_LISTING_DATE_RE.sub("", anchor_text).strip(" -|\t\r\n")
        if not headline:
            headline = FT_LISTING_DATE_RE.sub("", container_text).strip(" -|\t\r\n")

        if not headline:
            continue

        try:
            published_dt = datetime.strptime(
                re.sub(r"\s+", " ", date_text).strip(),
                "%A, %d %B %Y %H:%M",
            ).replace(tzinfo=SRI_LANKA_TZ)
            published_hint = published_dt.isoformat()
        except ValueError:
            continue

        seen.add(url)
        out.append(
            SimpleNamespace(
                url=url,
                headline=headline,
                published_at_hint=published_hint,
            )
        )

    return out


# ============================================================
# HELPERS
# ============================================================

def utc_now() -> datetime:

    return datetime.now(
        timezone.utc
    )


def is_ft_article_url(url: str) -> bool:
    try:
        parsed = urlsplit(str(url or ""))
    except Exception:
        return False

    if parsed.netloc.lower() not in {"ft.lk", "www.ft.lk"}:
        return False

    return bool(FT_ARTICLE_PATH_RE.search(parsed.path or ""))


def build_section_page_url(
    base_url: str,
    page_number: int,
) -> str:
    base = str(base_url).rstrip("/")

    if page_number <= 1:
        return base

    offset = (page_number - 1) * PAGE_SIZE

    # Front Page's first page is explicitly /0; subsequent pages use /60, /120...
    if base.endswith("/0"):
        base = base[:-2]

    return f"{base}/{offset}"


def should_fetch_article(
    state: JsonArticleState,
    canonical_url: str,
) -> tuple[bool, str]:

    record = state.get(
        canonical_url
    )

    if record is None:
        return True, "NEW"

    last_fetched = record.get(
        "last_fetched_at"
    )

    if not last_fetched:
        return (
            True,
            "NEVER_FETCHED",
        )

    try:

        last_dt = datetime.fromisoformat(
            last_fetched
        )

    except (
        TypeError,
        ValueError,
    ):

        return (
            True,
            "INVALID_FETCH_TIME",
        )

    if last_dt.tzinfo is None:

        last_dt = last_dt.replace(
            tzinfo=timezone.utc
        )

    age_hours = (
        utc_now()
        - last_dt
    ).total_seconds() / 3600

    if (
        age_hours
        >= RECHECK_AFTER_HOURS
    ):

        return (
            True,
            "RECHECK",
        )

    return (
        False,
        "RECENTLY_FETCHED",
    )


# ============================================================
# SAVE
# ============================================================

def save_raw_html(
    *,
    html: str,
    article_id: str,
    version_number: int,
    date_folder: str,
) -> Path:

    folder = (
        RAW_ROOT
        / date_folder
        / article_id
    )

    folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = (
        folder
        / f"v{version_number:03d}.html"
    )

    path.write_text(
        html,
        encoding="utf-8",
    )

    return path


def save_article_json(
    *,
    article,
    discovered,
    validation,
    article_id: str,
    version_number: int,
    date_folder: str,
    status: str,
    intake_status: str,
    window,
) -> Path:

    folder = (
        STAGED_ROOT
        / date_folder
        / article_id
    )

    folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = (
        folder
        / f"v{version_number:03d}.json"
    )

    payload = article.to_dict()

    payload[
        "article_version"
    ] = version_number

    payload[
        "collection_status"
    ] = status

    payload[
        "intake_status"
    ] = intake_status

    payload[
        "collection_run_date"
    ] = window.run_date.isoformat()

    payload[
        "collection_window_start"
    ] = window.start_date.isoformat()

    payload[
        "collection_window_end"
    ] = window.end_date.isoformat()

    payload[
        "collection_mode"
    ] = window.mode

    payload[
        "listing_headline"
    ] = discovered.headline

    payload[
        "listing_published_at"
    ] = (
        discovered.published_at_hint
    )

    payload[
        "validation"
    ] = {
        "valid": validation.valid,
        "errors": validation.errors,
        "warnings": validation.warnings,
    }

    path.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return path


# ============================================================
# DISCOVERY
# ============================================================

def discover_required_articles(
    *,
    client: HttpClient,
    window,
) -> list:
    global _DISCOVERY_EVIDENCE
    _DISCOVERY_EVIDENCE=[]
    discovered_by_url = {}

    print()
    print("Required publication window:")
    print(f"    {window.start_date}  ->  {window.end_date}")
    print(f"Mode: {window.mode}")

    for surface_name, base_url in DISCOVERY_SURFACES:
        old_pages = OldPageStopper(window.start_date)
        surface_max_pages = 1 if surface_name == "HOMEPAGE" else MAX_SECTION_PAGES
        surface_evidence={"listing_name":surface_name,"listing_url":base_url,"pagination_complete":surface_name=="HOMEPAGE","pages":[]}
        _DISCOVERY_EVIDENCE.append(surface_evidence)
        print()
        print("-" * 72)
        print(f"DISCOVERY SURFACE: {surface_name}")
        print("-" * 72)

        for page_number in range(1, surface_max_pages + 1):
            page_url = build_section_page_url(base_url, page_number)
            surface_evidence["pages"].append({"page":page_number,"url":page_url})

            print()
            print(f"[DISCOVERY] {surface_name} page {page_number}")
            print(f"            {page_url}")

            try:
                result = client.get(page_url)
            except Exception as exc:
                raise RuntimeError(
                    "Daily FT discovery failed "
                    f"on {surface_name} page {page_number}: {page_url}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            legacy_articles = discover_articles(result.html)
            generic_articles = discover_ft_listing_articles(
                result.html,
                page_url,
            )

            legacy_valid = [
                item
                for item in legacy_articles
                if is_ft_article_url(item.url)
            ]
            generic_valid = [
                item
                for item in generic_articles
                if is_ft_article_url(item.url)
            ]

            if len(generic_valid) > len(legacy_valid):
                page_articles = generic_articles
                valid_articles = generic_valid
                parser_used = "GENERIC_LISTING_FALLBACK"
            else:
                page_articles = legacy_articles
                valid_articles = legacy_valid
                parser_used = "LEGACY_DAILY_FT"

            rejected = len(page_articles) - len(valid_articles)
            print(f"            Parser used        : {parser_used}")
            print(f"            Legacy valid URLs  : {len(legacy_valid)}")
            print(f"            Generic valid URLs : {len(generic_valid)}")
            print(f"            Articles discovered: {len(page_articles)}")
            print(f"            Valid article URLs : {len(valid_articles)}")
            print(f"            Listing URLs rejected: {rejected}")

            surface_evidence["pages"][-1]["article_count"]=len(valid_articles)
            if not valid_articles:
                break

            found_in_window = 0
            found_older = 0
            unknown_date = 0

            for item in valid_articles:
                hint_date = parse_article_date(item.published_at_hint)

                if hint_date is None:
                    unknown_date += 1
                    discovered_by_url[item.url] = item
                    continue

                if window.contains_date(hint_date):
                    found_in_window += 1
                    discovered_by_url[item.url] = item
                    continue

                if hint_date < window.start_date:
                    found_older += 1

            print(f"            In window: {found_in_window}")
            print(f"            Older: {found_older}")
            print(f"            Date unknown: {unknown_date}")

            dated = [parse_article_date(item.published_at_hint)
                     for item in valid_articles]
            if old_pages.observe(dated, unknown_dates=unknown_date):
                surface_evidence["pagination_complete"]=True
                surface_evidence["stop_reason"]="TWO_CONFIRMED_OLDER_PAGES"
                print("            Two confirmed older pages; stopping this surface.")
                break

            time.sleep(REQUEST_DELAY_SECONDS)

    return list(discovered_by_url.values())


# ============================================================
# COLLECTION
# ============================================================

def collect() -> dict:

    window = (
        get_collection_window()
    )

    date_folder = (
        window.run_date.isoformat()
    )

    state = JsonArticleState(
        STATE_FILE
    )

    print()
    print("=" * 72)

    print(
        "DAILY FT MULTI-SURFACE "
        "DAILY COLLECTOR"
    )

    print("=" * 72)

    print(
        f"Run time : "
        f"{window.run_datetime.isoformat()}"
    )

    print(
        f"Mode     : "
        f"{window.mode}"
    )

    print(
        f"From     : "
        f"{window.start_date}"
    )

    print(
        f"To       : "
        f"{window.end_date}"
    )

    new_count = 0
    changed_count = 0
    unchanged_count = 0
    wrong_date_count = 0
    missing_date_count = 0
    invalid_count = 0
    extraction_failures = []
    failure_count = 0
    late_discovery_count = 0
    skipped_recent = 0

    with HttpClient() as client:

        discovered = (
            discover_required_articles(
                client=client,
                window=window,
            )
        )

        candidate_count = len(
            discovered
        )

        print()
        print("=" * 72)

        print(
            f"Candidate URLs for "
            f"current window: "
            f"{candidate_count}"
        )

        print("=" * 72)

        for item in discovered:

            state.mark_seen(
                canonical_url=item.url,
                source_url=item.url,
                headline=item.headline,
            )

        state.save()

        fetch_queue = []

        for item in discovered:

            (
                should_fetch,
                reason,
            ) = should_fetch_article(
                state,
                item.url,
            )

            if should_fetch:

                fetch_queue.append(
                    (
                        item,
                        reason,
                    )
                )

            else:

                skipped_recent += 1

        print()
        print(
            f"Articles requiring fetch: "
            f"{len(fetch_queue)}"
        )

        print(
            f"Already processed recently: "
            f"{skipped_recent}"
        )

        for index, (
            discovered_article,
            reason,
        ) in enumerate(
            fetch_queue,
            start=1,
        ):

            print()
            print("-" * 72)

            print(
                f"[{index}/"
                f"{len(fetch_queue)}]"
            )

            print(
                f"Reason: {reason}"
            )

            print(
                discovered_article.url
            )

            try:

                result = client.get(
                    discovered_article.url
                )

                article = extract_article(
                    html=result.html,
                    source_name=SOURCE_NAME,
                    section_name=SECTION_NAME,
                    source_url=(
                        discovered_article.url
                    ),
                    final_url=result.final_url,
                )

                validation = (
                    validate_article(
                        article
                    )
                )

                actual_date = (
                    parse_article_date(
                        article.published_at
                    )
                )

                if actual_date is None:

                    missing_date_count += 1
                    extraction_failures.append({"url":discovered_article.url,"kind":"MISSING_PUBLICATION_DATE"})

                    print(
                        "Status: DATE UNKNOWN"
                    )

                    continue

                if not window.contains_date(
                    actual_date
                ):

                    wrong_date_count += 1

                    print(
                        "Status: "
                        "OUTSIDE DATE WINDOW"
                    )

                    continue

                if not validation.valid:

                    invalid_count += 1
                    extraction_failures.append({"url":discovered_article.url,"kind":"INVALID_EXTRACTION","errors":list(validation.errors)})

                    print(
                        "Status: INVALID"
                    )

                    print(
                        f"Errors: "
                        f"{validation.errors}"
                    )

                    continue

                previous_hash = (
                    state.last_content_hash(
                        article.canonical_url
                    )
                )

                if previous_hash is None:

                    status = "NEW"
                    changed = True

                elif (
                    previous_hash
                    != article.content_hash
                ):

                    status = "CHANGED"
                    changed = True

                else:

                    status = "UNCHANGED"
                    changed = False

                intake_status = (
                    classify_intake_status(
                        published_at=(
                            article.published_at
                        ),
                        collection_status=status,
                        window=window,
                    )
                )

                if not changed:

                    unchanged_count += 1

                    state.update_after_fetch(
                        canonical_url=(
                            article.canonical_url
                        ),
                        source_url=(
                            article.source_url
                        ),
                        headline=(
                            article.headline
                        ),
                        content_hash=(
                            article.content_hash
                        ),
                        published_at=(
                            article.published_at
                        ),
                        changed=False,
                    )

                    state.save()

                    print(
                        f"Headline: "
                        f"{article.headline}"
                    )

                    print(
                        "Status: UNCHANGED"
                    )

                    print(
                        f"Intake: "
                        f"{intake_status}"
                    )

                    continue

                version_number = (
                    state.update_after_fetch(
                        canonical_url=(
                            article.canonical_url
                        ),
                        source_url=(
                            article.source_url
                        ),
                        headline=(
                            article.headline
                        ),
                        content_hash=(
                            article.content_hash
                        ),
                        published_at=(
                            article.published_at
                        ),
                        changed=True,
                    )
                )

                state.save()

                article_id = url_hash(
                    article.canonical_url
                )

                raw_path = save_raw_html(
                    html=result.html,
                    article_id=article_id,
                    version_number=(
                        version_number
                    ),
                    date_folder=date_folder,
                )

                json_path = (
                    save_article_json(
                        article=article,
                        discovered=(
                            discovered_article
                        ),
                        validation=validation,
                        article_id=article_id,
                        version_number=(
                            version_number
                        ),
                        date_folder=date_folder,
                        status=status,
                        intake_status=(
                            intake_status
                        ),
                        window=window,
                    )
                )

                if status == "NEW":

                    new_count += 1

                else:

                    changed_count += 1

                if (
                    intake_status
                    == "LATE_DISCOVERY"
                ):

                    late_discovery_count += 1

                print(
                    f"Headline: "
                    f"{article.headline}"
                )

                print(
                    f"Published: "
                    f"{article.published_at}"
                )

                print(
                    f"Words: "
                    f"{article.word_count}"
                )

                print(
                    f"Status: {status}"
                )

                print(
                    f"Intake: "
                    f"{intake_status}"
                )

                print(
                    f"Version: "
                    f"{version_number}"
                )

                print(
                    f"Source Link: "
                    f"{article.source_url}"
                )

                print(
                    f"Raw HTML: "
                    f"{raw_path}"
                )

                print(
                    f"JSON: "
                    f"{json_path}"
                )

            except Exception as exc:

                failure_count += 1
                extraction_failures.append({"url":discovered_article.url,"kind":"FETCH_OR_EXTRACTION_EXCEPTION","error":str(exc)})

                print(
                    f"FAILED: "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

            finally:

                state.save()

            time.sleep(
                REQUEST_DELAY_SECONDS
            )

    # ========================================================
    # CONTENT STATUS
    # ========================================================

    if (
        new_count > 0
        or late_discovery_count > 0
    ):

        content_status = (
            "NEW_ARTICLES_FOUND"
        )

    elif changed_count > 0:

        content_status = (
            "ARTICLES_CHANGED"
        )

    elif failure_count > 0:

        content_status = (
            "ARTICLE_FETCH_FAILURES"
        )

    elif invalid_count > 0:

        content_status = (
            "ARTICLE_EXTRACTION_FAILURES"
        )

    elif candidate_count == 0:

        content_status = (
            "NO_CURRENT_UPDATE"
        )

    else:

        content_status = (
            "NO_NEW_ARTICLES"
        )

    if (
        failure_count > 0
        or invalid_count > 0
        or missing_date_count > 0
    ):

        health_status = (
            "PARTIAL_ERROR"
        )

    else:

        health_status = "HEALTHY"

    print()
    print("=" * 72)
    print("DAILY COLLECTION COMPLETE")
    print("=" * 72)

    print(
        f"Collection window   : "
        f"{window.start_date}"
        f" -> "
        f"{window.end_date}"
    )

    print(
        f"Collection mode     : "
        f"{window.mode}"
    )

    print(
        f"Content status      : "
        f"{content_status}"
    )

    print(
        f"Health status       : "
        f"{health_status}"
    )

    print(
        f"New articles        : "
        f"{new_count}"
    )

    print(
        f"Late discoveries    : "
        f"{late_discovery_count}"
    )

    print(
        f"Changed articles    : "
        f"{changed_count}"
    )

    print(
        f"Unchanged           : "
        f"{unchanged_count}"
    )

    print(
        f"Wrong date skipped  : "
        f"{wrong_date_count}"
    )

    print(
        f"Missing date        : "
        f"{missing_date_count}"
    )

    print(
        f"Invalid extraction  : "
        f"{invalid_count}"
    )

    print(
        f"Fetch failures      : "
        f"{failure_count}"
    )

    print(
        f"Recent duplicates   : "
        f"{skipped_recent}"
    )

    return {
        "source_name": SOURCE_NAME,
        "section_name": SECTION_NAME,
        "execution_status": "SUCCESS",
        "health_status": "PARTIAL_ERROR" if any(not x["pagination_complete"] for x in globals().get("_DISCOVERY_EVIDENCE",[])) else health_status,
        "listing_pages": globals().get("_DISCOVERY_EVIDENCE",[]),
        "failed_discovery_surfaces": [x["listing_name"] for x in globals().get("_DISCOVERY_EVIDENCE",[]) if not x["pagination_complete"]],
        "content_status": content_status,
        "collection_mode": window.mode,
        "window_start": (
            window.start_date.isoformat()
        ),
        "window_end": (
            window.end_date.isoformat()
        ),
        "candidate_urls": candidate_count,
        "articles_requiring_fetch": (
            len(fetch_queue)
        ),
        "new_articles": new_count,
        "late_discoveries": (
            late_discovery_count
        ),
        "changed_articles": (
            changed_count
        ),
        "unchanged_articles": (
            unchanged_count
        ),
        "wrong_date_skipped": (
            wrong_date_count
        ),
        "missing_date": (
            missing_date_count
        ),
        "extraction_failures": extraction_failures,
        "unresolved_fetch_urls": [x["url"] for x in extraction_failures],
        "invalid_extraction": (
            invalid_count
        ),
        "fetch_failures": (
            failure_count
        ),
        "recent_duplicates": (
            skipped_recent
        ),
        "discovery_surfaces": [name for name, _ in DISCOVERY_SURFACES],
    }


# ============================================================
# ENTRY POINT
# ============================================================

def main():

    run_id = get_run_id()

    started_at = datetime.now(
        SRI_LANKA_TZ
    )

    try:

        metrics = collect()

        completed_at = datetime.now(
            SRI_LANKA_TZ
        )

        write_source_status(
            source_key=SOURCE_KEY,
            payload={
                "run_id": run_id,
                "started_at": (
                    started_at.isoformat()
                ),
                "completed_at": (
                    completed_at.isoformat()
                ),
                **metrics,
            },
        )

    except Exception as exc:

        completed_at = datetime.now(
            SRI_LANKA_TZ
        )

        write_source_status(
            source_key=SOURCE_KEY,
            payload={
                "run_id": run_id,
                "source_name": SOURCE_NAME,
                "section_name": SECTION_NAME,
                "started_at": (
                    started_at.isoformat()
                ),
                "completed_at": (
                    completed_at.isoformat()
                ),
                "execution_status": "FAILED",
                "health_status": "FAILED",
                "content_status": (
                    "SOURCE_FETCH_FAILED"
                ),
                "error_type": (
                    type(exc).__name__
                ),
                "error_message": str(exc),
            },
        )

        raise


if __name__ == "__main__":
    main()