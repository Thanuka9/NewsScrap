from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from bank_intel.collectors.http_client import HttpClient

from bank_intel.collectors.sources.ceylon_today import (
    SOURCE_NAME,
    SECTION_NAME,
    build_section_page_url,
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


SOURCE_KEY = "ceylon_today"

SRI_LANKA_TZ = ZoneInfo(
    "Asia/Colombo"
)


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

RAW_ROOT = (
    PROJECT_ROOT
    / "data"
    / "raw"
    / "ceylon_today"
)

STAGED_ROOT = (
    PROJECT_ROOT
    / "data"
    / "staged"
    / "ceylon_today"
)

STATE_FILE = (
    PROJECT_ROOT
    / "data"
    / "state"
    / "ceylon_today.json"
)


MAX_SECTION_PAGES = 4

REQUEST_DELAY_SECONDS = 1.0

RECHECK_AFTER_HOURS = 6


def utc_now() -> datetime:

    return datetime.now(
        timezone.utc
    )


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

    if age_hours >= RECHECK_AFTER_HOURS:

        return (
            True,
            "RECHECK",
        )

    return (
        False,
        "RECENTLY_FETCHED",
    )


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
        "listing_excerpt"
    ] = discovered.excerpt

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


def discover_required_articles(
    *,
    client: HttpClient,
    window,
) -> list:

    discovered_by_url = {}

    print()
    print(
        "Required publication window:"
    )

    print(
        f"    {window.start_date}"
        f" -> "
        f"{window.end_date}"
    )

    print(
        f"Mode: {window.mode}"
    )

    for page_number in range(
        1,
        MAX_SECTION_PAGES + 1,
    ):

        page_url = (
            build_section_page_url(
                page_number
            )
        )

        print()
        print(
            f"[DISCOVERY] Page "
            f"{page_number}"
        )

        print(
            f"            "
            f"{page_url}"
        )

        try:

            result = client.get(
                page_url
            )

        except Exception as exc:

            print(
                f"[DISCOVERY ERROR] "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            raise RuntimeError(
                "Ceylon Today discovery "
                "failed on archive page "
                f"{page_number}: "
                f"{page_url}"
            ) from exc

        page_articles = (
            discover_articles(
                result.html
            )
        )

        print(
            f"            "
            f"Articles discovered: "
            f"{len(page_articles)}"
        )

        if not page_articles:
            break

        found_in_window = 0
        found_older = 0
        unknown_date = 0

        for item in page_articles:

            hint_date = (
                parse_article_date(
                    item.published_at_hint
                )
            )

            if hint_date is None:

                unknown_date += 1

                discovered_by_url[
                    item.url
                ] = item

                continue

            if window.contains_date(
                hint_date
            ):

                found_in_window += 1

                discovered_by_url[
                    item.url
                ] = item

                continue

            if (
                hint_date
                < window.start_date
            ):

                found_older += 1

        print(
            f"            "
            f"In window: "
            f"{found_in_window}"
        )

        print(
            f"            "
            f"Older: "
            f"{found_older}"
        )

        print(
            f"            "
            f"Unknown date: "
            f"{unknown_date}"
        )

        if found_older > 0:

            print(
                "            "
                "Reached articles older "
                "than required window."
            )

            print(
                "            "
                "Stopping pagination."
            )

            break

        time.sleep(
            REQUEST_DELAY_SECONDS
        )

    return list(
        discovered_by_url.values()
    )


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
        "CEYLON TODAY FINANCE TODAY "
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
    invalid_count = 0
    missing_date_count = 0
    wrong_date_count = 0
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
            f"Candidate URLs: "
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

    print(
        "CEYLON TODAY COLLECTION COMPLETE"
    )

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
        "health_status": health_status,
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
        "invalid_extraction": (
            invalid_count
        ),
        "fetch_failures": (
            failure_count
        ),
        "recent_duplicates": (
            skipped_recent
        ),
    }


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