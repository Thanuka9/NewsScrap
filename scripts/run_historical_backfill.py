from __future__ import annotations

import argparse

from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)

from dataclasses import (
    dataclass,
)

from datetime import (
    date,
    datetime,
)

import json

from pathlib import Path

import time

from typing import Callable

from zoneinfo import ZoneInfo


from bank_intel.collectors.http_client import (
    HttpClient,
)

from bank_intel.collectors.sources import (
    ceylon_today,
    daily_ft,
)

from bank_intel.collectors.sources.daily_mirror import (
    DailyMirrorBusinessAdapter,
)

from bank_intel.collectors.sources.island import (
    IslandBusinessAdapter,
)

from bank_intel.collectors.sources._public_business_base import (
    canonicalize_url,
    fetch_with_httpx,
    fetch_with_playwright,
    is_block_page,
    stage_article,
)

from bank_intel.common.collection_window import (
    parse_article_date,
)

from bank_intel.common.hashing import (
    url_hash,
)

from bank_intel.extraction.article import (
    extract_article,
)

from bank_intel.extraction.validator import (
    validate_article,
)


# ============================================================
# PROJECT
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
)


STAGED_ROOT = (
    PROJECT_ROOT
    / "data"
    / "staged"
)


BACKFILL_RUN_ROOT = (
    PROJECT_ROOT
    / "data"
    / "backfill_runs"
)

BACKFILL_CHECKPOINT_ROOT = (
    BACKFILL_RUN_ROOT
    / "checkpoints"
)

BACKFILL_DIAGNOSTIC_ROOT = (
    BACKFILL_RUN_ROOT
    / "diagnostics"
)


SRI_LANKA_TZ = ZoneInfo(
    "Asia/Colombo"
)


# ============================================================
# SOURCE ADAPTER
# ============================================================


@dataclass(
    frozen=True
)
class BackfillSource:

    source_key: str

    source_name: str

    section_name: str

    build_page_url: Callable[
        [int],
        str,
    ]

    discover_articles: Callable[
        [str],
        list,
    ]


SOURCES = {

    "daily_ft": (
        BackfillSource(

            source_key=(
                "daily_ft"
            ),

            source_name=(
                daily_ft
                .SOURCE_NAME
            ),

            section_name=(
                daily_ft
                .SECTION_NAME
            ),

            build_page_url=(
                daily_ft
                .build_section_page_url
            ),

            discover_articles=(
                daily_ft
                .discover_articles
            ),
        )
    ),


    "ceylon_today": (
        BackfillSource(

            source_key=(
                "ceylon_today"
            ),

            source_name=(
                ceylon_today
                .SOURCE_NAME
            ),

            section_name=(
                ceylon_today
                .SECTION_NAME
            ),

            build_page_url=(
                ceylon_today
                .build_section_page_url
            ),

            discover_articles=(
                ceylon_today
                .discover_articles
            ),
        )
    ),
}


PUBLIC_BUSINESS_SOURCES = {
    "daily_mirror": (
        DailyMirrorBusinessAdapter
    ),
    "island": (
        IslandBusinessAdapter
    ),
}


# ============================================================
# EXISTING ARTICLE
# ============================================================


@dataclass
class ExistingArticle:

    canonical_url: str

    source_url: str

    content_hash: str | None

    published_date: (
        date
        | None
    )

    version_number: int

    path: Path


# ============================================================
# DISCOVERY RESULT
# ============================================================


@dataclass
class DiscoveryResult:

    articles: list

    pages_scanned: int

    newer_than_window: int

    older_than_window: int

    unknown_date: int

    reached_older_boundary: bool

    max_pages_reached: bool


# ============================================================
# JSON
# ============================================================


def load_json(
    path: Path,
) -> dict:

    return json.loads(
        path.read_text(
            encoding="utf-8-sig"
        )
    )


# ============================================================
# EXISTING STAGED ARTICLE INDEX
# ============================================================


def build_existing_index(
    source_key: str,
) -> dict[
    str,
    ExistingArticle,
]:
    """
    Read already-staged articles.

    IMPORTANT:

    This does NOT touch:

        data/state/daily_ft.json

        data/state/ceylon_today.json

    Historical backfill therefore cannot alter the live
    collector's scheduler/recheck state.
    """

    root = (
        STAGED_ROOT
        / source_key
    )

    index: dict[
        str,
        ExistingArticle,
    ] = {}

    if not root.exists():

        return index


    for path in (
        root.rglob(
            "*.json"
        )
    ):

        try:

            payload = (
                load_json(
                    path
                )
            )

        except Exception:

            continue


        canonical_url = str(
            payload.get(
                "canonical_url"
            )
            or payload.get(
                "source_url"
            )
            or ""
        ).strip()


        source_url = str(
            payload.get(
                "source_url"
            )
            or canonical_url
        ).strip()


        if not canonical_url:

            continue


        try:

            version_number = int(
                payload.get(
                    "article_version"
                )
                or 0
            )

        except (
            TypeError,
            ValueError,
        ):

            version_number = 0


        candidate = (
            ExistingArticle(

                canonical_url=(
                    canonical_url
                ),

                source_url=(
                    source_url
                ),

                content_hash=(
                    str(
                        payload[
                            "content_hash"
                        ]
                    )

                    if payload.get(
                        "content_hash"
                    )

                    else None
                ),

                published_date=(
                    parse_article_date(
                        payload.get(
                            "published_at"
                        )
                    )
                ),

                version_number=(
                    version_number
                ),

                path=(
                    path
                ),
            )
        )


        # Both URL forms point to the same record.

        for key in {
            canonical_url,
            source_url,
        }:

            if not key:

                continue


            existing = (
                index.get(
                    key
                )
            )


            if (
                existing is None
                or candidate
                .version_number
                > existing
                .version_number
            ):

                index[
                    key
                ] = candidate


    return index


# ============================================================
# HISTORICAL DISCOVERY
# ============================================================


def discover_historical_articles(
    *,
    client: HttpClient,
    source: BackfillSource,
    start_date: date,
    end_date: date,
    max_pages: int,
    request_delay_seconds: float,
) -> DiscoveryResult:
    """
    Scan a newest-to-oldest archive.

    Newer than end_date:
        skip, but continue pagination.

    start_date <= date <= end_date:
        retain article.

    Older than start_date:
        stop pagination after that page.

    This follows the same descending archive assumption already
    used by the live collectors.
    """

    discovered_by_url = {}

    pages_scanned = 0

    total_newer = 0

    total_older = 0

    total_unknown = 0

    reached_older_boundary = (
        False
    )


    print()

    print(
        f"Historical discovery: "
        f"{source.source_name}"
    )

    print(
        f"Window: "
        f"{start_date} "
        f"-> "
        f"{end_date}"
    )


    for page_number in range(
        1,
        max_pages + 1,
    ):

        page_url = (
            source.build_page_url(
                page_number
            )
        )


        print()

        print(
            f"[DISCOVERY] "
            f"{source.source_key} "
            f"page {page_number}"
        )

        print(
            f"            "
            f"{page_url}"
        )


        result = (
            client.get(
                page_url
            )
        )


        page_articles = (
            source.discover_articles(
                result.html
            )
        )


        pages_scanned += 1


        print(
            f"            "
            f"Articles discovered: "
            f"{len(page_articles)}"
        )


        if not page_articles:

            break


        page_in_window = 0

        page_newer = 0

        page_older = 0

        page_unknown = 0


        for item in (
            page_articles
        ):

            hint_date = (
                parse_article_date(
                    item
                    .published_at_hint
                )
            )


            if hint_date is None:

                page_unknown += 1

                total_unknown += 1

                continue


            if (
                hint_date
                > end_date
            ):

                page_newer += 1

                total_newer += 1

                continue


            if (
                hint_date
                < start_date
            ):

                page_older += 1

                total_older += 1

                continue


            page_in_window += 1


            discovered_by_url[
                item.url
            ] = item


        print(
            f"            "
            f"In window: "
            f"{page_in_window}"
        )

        print(
            f"            "
            f"Newer: "
            f"{page_newer}"
        )

        print(
            f"            "
            f"Older: "
            f"{page_older}"
        )

        print(
            f"            "
            f"Unknown date: "
            f"{page_unknown}"
        )


        if (
            page_older
            > 0
        ):

            reached_older_boundary = (
                True
            )

            print(
                "            "
                "Historical start "
                "boundary reached."
            )

            break


        if (
            request_delay_seconds
            > 0
        ):

            time.sleep(
                request_delay_seconds
            )


    max_pages_reached = (
        pages_scanned
        >= max_pages
        and not reached_older_boundary
    )


    return (
        DiscoveryResult(

            articles=list(
                discovered_by_url
                .values()
            ),

            pages_scanned=(
                pages_scanned
            ),

            newer_than_window=(
                total_newer
            ),

            older_than_window=(
                total_older
            ),

            unknown_date=(
                total_unknown
            ),

            reached_older_boundary=(
                reached_older_boundary
            ),

            max_pages_reached=(
                max_pages_reached
            ),
        )
    )


# ============================================================
# SAVE RAW HTML
# ============================================================


def save_raw_html(
    *,
    source_key: str,
    html: str,
    article_id: str,
    version_number: int,
    publication_date: date,
) -> Path:

    folder = (
        RAW_ROOT
        / source_key
        / publication_date
        .isoformat()
        / article_id
    )


    folder.mkdir(
        parents=True,
        exist_ok=True,
    )


    path = (
        folder
        / (
            f"v"
            f"{version_number:03d}"
            f".html"
        )
    )


    path.write_text(
        html,
        encoding="utf-8",
    )


    return path


# ============================================================
# SAVE STAGED JSON
# ============================================================


def save_article_json(
    *,
    source: BackfillSource,
    article,
    discovered,
    validation,
    article_id: str,
    version_number: int,
    publication_date: date,
    status: str,
    backfill_start_date: date,
    backfill_end_date: date,
) -> Path:
    """
    Historical records are stored under their ACTUAL
    publication date.

    collection_run_date still records the date on which we
    performed the historical retrieval.
    """

    folder = (
        STAGED_ROOT
        / source.source_key
        / publication_date
        .isoformat()
        / article_id
    )


    folder.mkdir(
        parents=True,
        exist_ok=True,
    )


    path = (
        folder
        / (
            f"v"
            f"{version_number:03d}"
            f".json"
        )
    )


    payload = (
        article.to_dict()
    )


    payload[
        "article_version"
    ] = version_number


    payload[
        "collection_status"
    ] = status


    payload[
        "intake_status"
    ] = (
        "HISTORICAL_BACKFILL"
    )


    payload[
        "collection_run_date"
    ] = (
        datetime.now(
            SRI_LANKA_TZ
        )
        .date()
        .isoformat()
    )


    payload[
        "collection_window_start"
    ] = (
        backfill_start_date
        .isoformat()
    )


    payload[
        "collection_window_end"
    ] = (
        backfill_end_date
        .isoformat()
    )


    payload[
        "collection_mode"
    ] = (
        "HISTORICAL_BACKFILL"
    )


    payload[
        "listing_headline"
    ] = getattr(
        discovered,
        "headline",
        None,
    )


    payload[
        "listing_excerpt"
    ] = getattr(
        discovered,
        "excerpt",
        None,
    )


    payload[
        "listing_published_at"
    ] = getattr(
        discovered,
        "published_at_hint",
        None,
    )


    payload[
        "validation"
    ] = {

        "valid": (
            validation.valid
        ),

        "errors": (
            validation.errors
        ),

        "warnings": (
            validation.warnings
        ),
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
# BACKFILL ONE SOURCE
# ============================================================


def backfill_source(
    *,
    source: BackfillSource,
    start_date: date,
    end_date: date,
    max_pages: int,
    request_delay_seconds: float,
    refresh_existing: bool,
) -> dict:

    existing_index = (
        build_existing_index(
            source.source_key
        )
    )


    publication_dates: set[
        date
    ] = set()


    summary = {

        "source_key": (
            source.source_key
        ),

        "source_name": (
            source.source_name
        ),

        "discovered_in_window": 0,

        "skipped_existing": 0,

        "new_articles": 0,

        "changed_articles": 0,

        "unchanged_after_refresh": 0,

        "invalid_articles": 0,

        "date_mismatches": 0,

        "missing_dates": 0,

        "fetch_failures": 0,

        "pages_scanned": 0,

        "max_pages_reached": False,

        "publication_dates": [],
    }


    with HttpClient() as client:

        discovery = (
            discover_historical_articles(

                client=(
                    client
                ),

                source=(
                    source
                ),

                start_date=(
                    start_date
                ),

                end_date=(
                    end_date
                ),

                max_pages=(
                    max_pages
                ),

                request_delay_seconds=(
                    request_delay_seconds
                ),
            )
        )


        summary[
            "discovered_in_window"
        ] = len(
            discovery.articles
        )


        summary[
            "pages_scanned"
        ] = (
            discovery
            .pages_scanned
        )


        summary[
            "max_pages_reached"
        ] = (
            discovery
            .max_pages_reached
        )


        for (
            index,
            discovered,
        ) in enumerate(
            discovery.articles,
            start=1,
        ):

            existing = (
                existing_index.get(
                    discovered.url
                )
            )


            # ================================================
            # ALREADY STAGED
            # ================================================

            if (
                existing is not None
                and not refresh_existing
            ):

                summary[
                    "skipped_existing"
                ] += 1


                if (
                    existing
                    .published_date
                ):

                    publication_dates.add(
                        existing
                        .published_date
                    )


                continue


            print()

            print(
                "-" * 80
            )


            print(
                f"["
                f"{source.source_key} "
                f"{index}/"
                f"{len(discovery.articles)}"
                f"]"
            )


            print(
                discovered.url
            )


            try:

                result = (
                    client.get(
                        discovered.url
                    )
                )


                article = (
                    extract_article(

                        html=(
                            result.html
                        ),

                        source_name=(
                            source
                            .source_name
                        ),

                        section_name=(
                            source
                            .section_name
                        ),

                        source_url=(
                            discovered.url
                        ),

                        final_url=(
                            result.final_url
                        ),
                    )
                )


                validation = (
                    validate_article(
                        article
                    )
                )


                actual_date = (
                    parse_article_date(
                        article
                        .published_at
                    )
                )


                if (
                    actual_date
                    is None
                ):

                    summary[
                        "missing_dates"
                    ] += 1


                    print(
                        "Status: DATE UNKNOWN"
                    )


                    continue


                if not (
                    start_date
                    <= actual_date
                    <= end_date
                ):

                    summary[
                        "date_mismatches"
                    ] += 1


                    print(
                        "Status: OUTSIDE "
                        "BACKFILL WINDOW"
                    )


                    continue


                if not (
                    validation.valid
                ):

                    summary[
                        "invalid_articles"
                    ] += 1


                    print(
                        "Status: INVALID"
                    )


                    print(
                        f"Errors: "
                        f"{validation.errors}"
                    )


                    continue


                canonical_url = (
                    article.canonical_url
                    or article.source_url
                )


                existing = (
                    existing_index.get(
                        canonical_url
                    )
                    or existing_index.get(
                        article.source_url
                    )
                )


                # ============================================
                # VERSION
                # ============================================

                if (
                    existing is None
                ):

                    status = (
                        "NEW"
                    )


                    version_number = (
                        1
                    )


                elif (
                    existing
                    .content_hash
                    == article
                    .content_hash
                ):

                    summary[
                        "unchanged_after_refresh"
                    ] += 1


                    publication_dates.add(
                        actual_date
                    )


                    print(
                        f"Headline: "
                        f"{article.headline}"
                    )


                    print(
                        "Status: UNCHANGED"
                    )


                    continue


                else:

                    status = (
                        "CHANGED"
                    )


                    version_number = (
                        existing
                        .version_number
                        + 1
                    )


                article_id = (
                    url_hash(
                        canonical_url
                    )
                )


                # ============================================
                # SAVE
                # ============================================

                raw_path = (
                    save_raw_html(

                        source_key=(
                            source
                            .source_key
                        ),

                        html=(
                            result.html
                        ),

                        article_id=(
                            article_id
                        ),

                        version_number=(
                            version_number
                        ),

                        publication_date=(
                            actual_date
                        ),
                    )
                )


                json_path = (
                    save_article_json(

                        source=(
                            source
                        ),

                        article=(
                            article
                        ),

                        discovered=(
                            discovered
                        ),

                        validation=(
                            validation
                        ),

                        article_id=(
                            article_id
                        ),

                        version_number=(
                            version_number
                        ),

                        publication_date=(
                            actual_date
                        ),

                        status=(
                            status
                        ),

                        backfill_start_date=(
                            start_date
                        ),

                        backfill_end_date=(
                            end_date
                        ),
                    )
                )


                stored = (
                    ExistingArticle(

                        canonical_url=(
                            canonical_url
                        ),

                        source_url=(
                            article.source_url
                        ),

                        content_hash=(
                            article.content_hash
                        ),

                        published_date=(
                            actual_date
                        ),

                        version_number=(
                            version_number
                        ),

                        path=(
                            json_path
                        ),
                    )
                )


                existing_index[
                    canonical_url
                ] = stored


                existing_index[
                    article.source_url
                ] = stored


                publication_dates.add(
                    actual_date
                )


                if (
                    status
                    == "NEW"
                ):

                    summary[
                        "new_articles"
                    ] += 1


                else:

                    summary[
                        "changed_articles"
                    ] += 1


                print(
                    f"Headline: "
                    f"{article.headline}"
                )


                print(
                    f"Published: "
                    f"{article.published_at}"
                )


                print(
                    f"Status: "
                    f"{status}"
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

                summary[
                    "fetch_failures"
                ] += 1


                print(
                    f"FAILED: "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )


            if (
                request_delay_seconds
                > 0
            ):

                time.sleep(
                    request_delay_seconds
                )


    summary[
        "publication_dates"
    ] = [

        value.isoformat()

        for value in sorted(
            publication_dates
        )
    ]


    return summary



# ============================================================
# PUBLIC BUSINESS SOURCE BACKFILL
# Daily Mirror + The Island
# ============================================================


def checkpoint_path(
    source_key: str,
    start_date: date,
    end_date: date,
) -> Path:

    return (
        BACKFILL_CHECKPOINT_ROOT
        / (
            f"{source_key}_"
            f"{start_date.isoformat()}_"
            f"{end_date.isoformat()}.json"
        )
    )


def load_checkpoint(
    path: Path,
) -> dict:

    backup_path = (
        path.with_suffix(
            path.suffix + ".bak"
        )
    )

    for candidate in (
        path,
        backup_path,
    ):

        if not candidate.exists():

            continue

        try:

            return json.loads(
                candidate.read_text(
                    encoding="utf-8"
                )
            )

        except Exception:

            continue

    return {}


def write_checkpoint(
    path: Path,
    payload: dict,
) -> None:
    """
    Windows-safe checkpoint writer.

    Some Windows/AV/sync combinations can temporarily deny
    os.replace/Path.replace even when both files are in the same
    directory. Historical backfill is already idempotent because
    staged URLs are re-indexed on resume, so a retrying direct write
    is safer here than aborting a long run on an atomic rename.

    A .bak copy of the previous good checkpoint is retained. If a
    direct write is ever interrupted, load_checkpoint() falls back
    to that backup.
    """

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    serialized = json.dumps(
        payload,
        indent=2,
        ensure_ascii=False,
    )

    backup_path = (
        path.with_suffix(
            path.suffix + ".bak"
        )
    )

    # Preserve the last readable checkpoint before overwriting it.
    if path.exists():

        try:

            previous_text = (
                path.read_text(
                    encoding="utf-8"
                )
            )

            # Only back up a checkpoint that is valid JSON.
            json.loads(
                previous_text
            )

            backup_path.write_text(
                previous_text,
                encoding="utf-8",
            )

        except Exception:

            # Do not block a new checkpoint merely because the old
            # checkpoint/backup cannot be copied.
            pass

    last_error = None

    # Retry direct writes to tolerate brief Windows Defender,
    # indexing, or sync-process file locks.
    for attempt in range(
        1,
        11,
    ):

        try:

            path.write_text(
                serialized,
                encoding="utf-8",
            )

            return

        except (
            PermissionError,
            OSError,
        ) as exc:

            last_error = exc

            time.sleep(
                min(
                    0.25 * attempt,
                    2.0,
                )
            )

    # Preserve the new checkpoint payload even if the primary
    # checkpoint remains locked, so progress is never silently lost.
    recovery_path = (
        path.parent
        / (
            f"{path.stem}.recovery_"
            f"{datetime.now(SRI_LANKA_TZ).strftime('%Y%m%d_%H%M%S_%f')}"
            f"{path.suffix}"
        )
    )

    recovery_path.write_text(
        serialized,
        encoding="utf-8",
    )

    raise RuntimeError(
        "Checkpoint file remained locked after 10 retries. "
        f"Recovery checkpoint written to: {recovery_path}"
    ) from last_error


def mark_historical_payload(
    *,
    output_path: Path,
    start_date: date,
    end_date: date,
    listing_page: int,
) -> None:
    """
    Add historical-backfill provenance to newly written
    Daily Mirror / Island staged JSON records.

    The live collector schema is retained; these fields only add
    provenance and do not alter article content.
    """

    payload = load_json(
        output_path
    )

    payload[
        "intake_status"
    ] = "HISTORICAL_BACKFILL"

    payload[
        "collection_mode"
    ] = "HISTORICAL_BACKFILL"

    payload[
        "collection_window_start"
    ] = start_date.isoformat()

    payload[
        "collection_window_end"
    ] = end_date.isoformat()

    payload[
        "backfill_listing_page"
    ] = listing_page

    output_path.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def empty_public_summary(
    *,
    source_key: str,
    source_name: str,
) -> dict:

    return {
        "source_key": (
            source_key
        ),
        "source_name": (
            source_name
        ),
        "discovered_in_window": 0,
        "skipped_existing": 0,
        "new_articles": 0,
        "changed_articles": 0,
        "unchanged_after_refresh": 0,
        "invalid_articles": 0,
        "date_mismatches": 0,
        "missing_dates": 0,
        "fetch_failures": 0,
        "pages_scanned": 0,
        "max_pages_reached": False,
        "backfill_complete": False,
        "stop_reason": None,
        "publication_dates": [],
    }


def public_backfill_listing_url(
    *,
    source_key: str,
    adapter,
    page_number: int,
) -> tuple[str, str]:
    """
    Resolve the listing URL used only by historical backfill.

    Daily Mirror's live /business/215/<offset> route becomes unreliable
    around offset 3000. Its older public Business archive is also exposed
    through /news/215/<offset>, but that archive advances in 30-item steps
    rather than the live route's 5-item steps.

    Keep the normal live adapter untouched. For historical backfill only:
      logical page 601 -> /news/215/3000
      logical page 602 -> /news/215/3030
      logical page 603 -> /news/215/3060
      ...

    This intentionally overlaps the last live pages slightly. Existing URL
    indexing/deduplication makes that overlap safe and helps avoid gaps.
    """

    if (
        source_key
        == "daily_mirror"
        and page_number
        >= 601
    ):

        news_offset = (
            3000
            + (
                page_number
                - 601
            )
            * 30
        )

        return (
            (
                "https://www.dailymirror.lk/"
                f"news/215/{news_offset}"
            ),
            "DAILY_MIRROR_NEWS_ARCHIVE",
        )

    return (
        adapter.listing_url(
            page_number
        ),
        "PRIMARY",
    )


def normalize_public_listing_urls(
    *,
    adapter,
    listing,
    page_url: str,
    seen_urls: set[str],
) -> list[str]:
    """
    Apply the adapter's listing discovery and URL guards while
    preserving page order and avoiding duplicates already seen in
    this backfill process.
    """

    urls = (
        adapter.discover_article_urls(
            listing.html,
            (
                listing.final_url
                or page_url
            ),
        )
    )

    normalized_urls = []

    for url in urls:

        if not (
            adapter.is_article_url(
                url
            )
        ):

            continue

        canonical_url = (
            canonicalize_url(
                url
            )
        )

        if (
            canonical_url
            in seen_urls
        ):

            continue

        normalized_urls.append(
            canonical_url
        )

    return list(
        dict.fromkeys(
            normalized_urls
        )
    )


def save_empty_listing_diagnostic(
    *,
    source_key: str,
    page_number: int,
    listing,
) -> Path:

    BACKFILL_DIAGNOSTIC_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = (
        BACKFILL_DIAGNOSTIC_ROOT
        / (
            f"{source_key}_"
            f"page_{page_number}_"
            f"{datetime.now(SRI_LANKA_TZ).strftime('%Y%m%d_%H%M%S')}"
            f".html"
        )
    )

    path.write_text(
        listing.html
        or "",
        encoding="utf-8",
    )

    return path


def fetch_extract_public_article(
    *,
    adapter,
    url: str,
):
    """
    Fetch + extract one article.

    This function does NOT stage/write anything. It is therefore safe
    to run in worker threads; staging remains serialized in the main
    thread so versioning and file writes stay deterministic.
    """

    fetched = (
        adapter.fetch(
            url
        )
    )

    return (
        adapter.extract(
            url,
            fetched,
        )
    )


def fetch_listing_with_backoff(
    *,
    adapter,
    url: str,
    source_key: str,
):
    """
    Polite listing-page retry policy.

    Important:
      - HTTP 429 is treated as a rate-limit signal, not as a cue to
        hammer the browser fallback.
      - retries use progressively longer waits
      - browser fallback is attempted only after non-429 HTTP failures
        or after HTTP retries no longer indicate rate limiting
      - if the source still refuses access, the caller keeps the
        checkpoint on the same page and exits cleanly
    """

    # Keep this deliberately conservative. Historical collection does
    # not need to be fast enough to justify repeatedly hitting a site
    # that is explicitly rate-limiting us.
    waits = (
        30,
        60,
        120,
        240,
    )

    last_error = None

    for attempt in range(
        1,
        len(waits) + 2,
    ):

        try:

            result = (
                fetch_with_httpx(
                    url,
                    timeout=45.0,
                    follow_redirects=True,
                )
            )

            status = (
                result.status_code
            )

            if (
                status is not None
                and status == 429
            ):

                if (
                    attempt
                    <= len(waits)
                ):

                    wait_seconds = (
                        waits[
                            attempt - 1
                        ]
                    )

                    print(
                        "            "
                        f"HTTP 429 rate limit; "
                        f"waiting {wait_seconds}s "
                        f"before retry "
                        f"{attempt + 1}/"
                        f"{len(waits) + 1}..."
                    )

                    time.sleep(
                        wait_seconds
                    )

                    continue

                raise RuntimeError(
                    "HTTP 429 rate limit persisted "
                    "after backoff retries"
                )

            if (
                status is not None
                and status < 400
                and not is_block_page(
                    result.html,
                    status,
                )
                and len(
                    result.html
                    or ""
                )
                >= 500
            ):

                return result

            last_error = (
                RuntimeError(
                    "HTTP listing returned unusable "
                    f"response status={status}"
                )
            )

        except KeyboardInterrupt:

            raise

        except Exception as exc:

            last_error = exc

        # For ordinary network/HTTP failures (not 429), give the
        # source a short cooldown before another HTTP attempt.
        if (
            attempt
            <= len(waits)
        ):

            wait_seconds = min(
                5 * attempt,
                20,
            )

            print(
                "            "
                f"Listing fetch attempt {attempt} failed; "
                f"waiting {wait_seconds}s before retry..."
            )

            time.sleep(
                wait_seconds
            )

    # Only after the polite HTTP retry sequence do we try one browser
    # render. This avoids launching Chromium repeatedly during 429s.
    if getattr(
        adapter,
        "allow_browser_fallback",
        False,
    ):

        try:

            result = (
                fetch_with_playwright(
                    url,
                    timeout_ms=60_000,
                )
            )

            status = (
                result.status_code
            )

            if (
                (
                    status is None
                    or status < 400
                )
                and not is_block_page(
                    result.html,
                    status,
                )
                and len(
                    result.html
                    or ""
                )
                >= 500
            ):

                return result

            raise RuntimeError(
                "Browser fallback returned unusable page "
                f"(status={status})"
            )

        except KeyboardInterrupt:

            raise

        except Exception as exc:

            last_error = exc

    raise RuntimeError(
        f"Listing fetch failed after polite retries "
        f"for {url}: {last_error}"
    )


def backfill_public_business_source(
    *,
    source_key: str,
    adapter_class,
    start_date: date,
    end_date: date,
    max_pages: int,
    request_delay_seconds: float,
    refresh_existing: bool,
    reset_checkpoint: bool,
    workers: int,
) -> dict:
    """
    Historical backfill for Daily Mirror and The Island.

    Important differences from live collection:
      - does not touch data/source_status/*.json
      - resumes from a page checkpoint
      - uses the source-specific extraction adapter, including
        The Island body-cleanup logic
      - stages under the actual publication date
      - annotates new/changed records as HISTORICAL_BACKFILL
    """

    if workers < 1:

        raise ValueError(
            "--workers must be >= 1"
        )

    adapter = (
        adapter_class()
    )

    checkpoint = (
        checkpoint_path(
            source_key,
            start_date,
            end_date,
        )
    )

    if (
        reset_checkpoint
        and checkpoint.exists()
    ):

        checkpoint.unlink()

    previous = (
        load_checkpoint(
            checkpoint
        )
    )

    if (
        previous.get(
            "completed"
        )
        and not refresh_existing
    ):

        stored_summary = (
            previous.get(
                "summary"
            )
        )

        if isinstance(
            stored_summary,
            dict,
        ):

            print()

            print(
                f"{adapter.source_name}: "
                "historical backfill already complete."
            )

            print(
                f"Checkpoint: "
                f"{checkpoint}"
            )

            return (
                stored_summary
            )

    current_page = int(
        previous.get(
            "next_page",
            1,
        )
        or 1
    )

    if refresh_existing:
        current_page = 1

    existing_index = (
        build_existing_index(
            source_key
        )
    )

    publication_dates: set[
        date
    ] = set()

    summary = (
        empty_public_summary(
            source_key=(
                source_key
            ),
            source_name=(
                adapter.source_name
            ),
        )
    )

    seen_urls: set[
        str
    ] = set()

    reached_older_boundary = (
        False
    )

    print()

    print(
        "=" * 80
    )

    print(
        "BANK SUPERVISION NEWS "
        "PUBLIC-SOURCE HISTORICAL BACKFILL"
    )

    print(
        "=" * 80
    )

    print(
        f"Source           : "
        f"{adapter.source_name}"
    )

    print(
        f"Window           : "
        f"{start_date} -> {end_date}"
    )

    print(
        f"Resume page      : "
        f"{current_page}"
    )

    print(
        f"Max page         : "
        f"{max_pages}"
    )

    print(
        f"Article workers  : "
        f"{workers}"
    )

    print(
        f"Checkpoint       : "
        f"{checkpoint}"
    )

    for page_number in range(
        current_page,
        max_pages + 1,
    ):

        (
            page_url,
            listing_route,
        ) = (
            public_backfill_listing_url(
                source_key=(
                    source_key
                ),
                adapter=(
                    adapter
                ),
                page_number=(
                    page_number
                ),
            )
        )

        print()

        print(
            f"[DISCOVERY] "
            f"{source_key} "
            f"page {page_number}"
        )

        print(
            f"            "
            f"{page_url}"
        )

        if (
            listing_route
            != "PRIMARY"
        ):

            print(
                f"            "
                f"Archive route: "
                f"{listing_route}"
            )

        try:

            listing = (
                fetch_listing_with_backoff(
                    adapter=(
                        adapter
                    ),
                    url=(
                        page_url
                    ),
                    source_key=(
                        source_key
                    ),
                )
            )

        except Exception as exc:

            summary[
                "fetch_failures"
            ] += 1

            checkpoint_payload = {
                "source_key": (
                    source_key
                ),
                "source_name": (
                    adapter.source_name
                ),
                "from_date": (
                    start_date.isoformat()
                ),
                "to_date": (
                    end_date.isoformat()
                ),
                "completed": False,
                "next_page": (
                    page_number
                ),
                "failed_at": (
                    datetime.now(
                        SRI_LANKA_TZ
                    ).isoformat()
                ),
                "last_error": (
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
                "listing_route": (
                    listing_route
                ),
                "listing_url": (
                    page_url
                ),
                "summary": (
                    summary
                ),
            }

            write_checkpoint(
                checkpoint,
                checkpoint_payload,
            )

            print(
                "            "
                "LISTING FETCH FAILED"
            )

            print(
                f"            "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            break

        normalized_urls = (
            normalize_public_listing_urls(
                adapter=(
                    adapter
                ),
                listing=(
                    listing
                ),
                page_url=(
                    page_url
                ),
                seen_urls=(
                    seen_urls
                ),
            )
        )

        # A listing page can occasionally look like usable HTML to
        # httpx while still being incomplete/challenged. If normal
        # discovery returns zero URLs, force one browser-rendered retry
        # before deciding the page is genuinely unusable.
        if (
            not normalized_urls
            and getattr(
                adapter,
                "allow_browser_fallback",
                False,
            )
            and getattr(
                listing,
                "method",
                "",
            )
            != "playwright"
        ):

            print(
                "            "
                "0 URLs from HTTP listing; "
                "forcing Playwright retry..."
            )

            try:

                browser_listing = (
                    fetch_with_playwright(
                        page_url
                    )
                )

                browser_urls = (
                    normalize_public_listing_urls(
                        adapter=(
                            adapter
                        ),
                        listing=(
                            browser_listing
                        ),
                        page_url=(
                            page_url
                        ),
                        seen_urls=(
                            seen_urls
                        ),
                    )
                )

                if browser_urls:

                    listing = (
                        browser_listing
                    )

                    normalized_urls = (
                        browser_urls
                    )

                    print(
                        "            "
                        f"Browser retry recovered "
                        f"{len(normalized_urls)} article URLs."
                    )

            except Exception as exc:

                print(
                    "            "
                    "Browser retry failed: "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

        summary[
            "pages_scanned"
        ] += 1

        print(
            f"            "
            f"Articles discovered: "
            f"{len(normalized_urls)}"
        )

        if not normalized_urls:

            diagnostic_path = (
                save_empty_listing_diagnostic(
                    source_key=(
                        source_key
                    ),
                    page_number=(
                        page_number
                    ),
                    listing=(
                        listing
                    ),
                )
            )

            summary[
                "stop_reason"
            ] = (
                "NO_ARTICLES_DISCOVERED"
            )

            summary[
                "backfill_complete"
            ] = False

            checkpoint_payload = {
                "source_key": (
                    source_key
                ),
                "source_name": (
                    adapter.source_name
                ),
                "from_date": (
                    start_date.isoformat()
                ),
                "to_date": (
                    end_date.isoformat()
                ),
                "completed": False,
                "next_page": (
                    page_number
                ),
                "updated_at": (
                    datetime.now(
                        SRI_LANKA_TZ
                    ).isoformat()
                ),
                "last_stop_reason": (
                    "NO_ARTICLES_DISCOVERED"
                ),
                "diagnostic_html": (
                    str(
                        diagnostic_path
                    )
                ),
                "listing_route": (
                    listing_route
                ),
                "listing_url": (
                    page_url
                ),
                "summary": (
                    summary
                ),
            }

            write_checkpoint(
                checkpoint,
                checkpoint_payload,
            )

            print(
                "            "
                "No article URLs discovered "
                "after browser retry."
            )

            print(
                "            "
                "Checkpoint NOT advanced."
            )

            print(
                "            "
                f"Diagnostic HTML: "
                f"{diagnostic_path}"
            )

            break

        # Mark only URLs that actually survived discovery for this page.
        seen_urls.update(
            normalized_urls
        )

        page_dates: list[
            date
        ] = []

        page_in_window = 0

        page_newer = 0

        page_older = 0

        # ----------------------------------------------------
        # Parallel ARTICLE fetching, serialized staging.
        #
        # Listing pages remain sequential so archive ordering,
        # date-boundary detection and checkpointing stay safe.
        # Only article HTTP/extraction work is parallelized.
        # ----------------------------------------------------
        prefetched_articles = {}

        fetch_urls = [

            url

            for url in normalized_urls

            if (
                refresh_existing
                or existing_index.get(
                    url
                )
                is None
            )
        ]

        if (
            workers > 1
            and len(fetch_urls) > 1
        ):

            print(
                f"            "
                f"Parallel article fetch: "
                f"{len(fetch_urls)} URLs "
                f"with {workers} workers"
            )

            executor = (
                ThreadPoolExecutor(
                    max_workers=(
                        workers
                    ),
                    thread_name_prefix=(
                        f"{source_key}_backfill"
                    ),
                )
            )

            future_to_url = {}

            try:

                for url in fetch_urls:

                    future = (
                        executor.submit(
                            fetch_extract_public_article,
                            adapter=(
                                adapter
                            ),
                            url=(
                                url
                            ),
                        )
                    )

                    future_to_url[
                        future
                    ] = url

                    # Stagger request starts slightly. This preserves
                    # politeness while still allowing overlap.
                    if (
                        request_delay_seconds
                        > 0
                    ):

                        time.sleep(
                            request_delay_seconds
                        )

                for future in (
                    as_completed(
                        future_to_url
                    )
                ):

                    url = (
                        future_to_url[
                            future
                        ]
                    )

                    try:

                        prefetched_articles[
                            url
                        ] = (
                            future.result(),
                            None,
                        )

                    except Exception as exc:

                        prefetched_articles[
                            url
                        ] = (
                            None,
                            exc,
                        )

            except KeyboardInterrupt:

                executor.shutdown(
                    wait=False,
                    cancel_futures=True,
                )

                raise

            else:

                executor.shutdown(
                    wait=True
                )

        for (
            index,
            url,
        ) in enumerate(
            normalized_urls,
            start=1,
        ):

            print(
                f"            "
                f"[{index}/"
                f"{len(normalized_urls)}] "
                f"{url}"
            )

            existing = (
                existing_index.get(
                    url
                )
            )

            if (
                existing is not None
                and not refresh_existing
            ):

                if (
                    existing.published_date
                    is not None
                ):

                    actual_date = (
                        existing
                        .published_date
                    )

                    page_dates.append(
                        actual_date
                    )

                    if (
                        actual_date
                        < start_date
                    ):

                        page_older += 1

                    elif (
                        actual_date
                        > end_date
                    ):

                        page_newer += 1

                    else:

                        page_in_window += 1

                        summary[
                            "discovered_in_window"
                        ] += 1

                        summary[
                            "skipped_existing"
                        ] += 1

                        publication_dates.add(
                            actual_date
                        )

                print(
                    "              "
                    "SKIP: already staged"
                )

                continue

            prefetched = (
                prefetched_articles.get(
                    url
                )
            )

            if (
                prefetched
                is not None
            ):

                article, fetch_error = (
                    prefetched
                )

                if (
                    fetch_error
                    is not None
                ):

                    summary[
                        "fetch_failures"
                    ] += 1

                    print(
                        "              "
                        f"FAILED: "
                        f"{type(fetch_error).__name__}: "
                        f"{fetch_error}"
                    )

                    continue

            else:

                try:

                    article = (
                        fetch_extract_public_article(
                            adapter=(
                                adapter
                            ),
                            url=(
                                url
                            ),
                        )
                    )

                except Exception as exc:

                    summary[
                        "fetch_failures"
                    ] += 1

                    print(
                        "              "
                        f"FAILED: "
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    )

                    continue

            if (
                article.published_at
                is None
            ):

                summary[
                    "missing_dates"
                ] += 1

                print(
                    "              "
                    "Status: DATE UNKNOWN"
                )

                continue

            actual_date = (
                article
                .published_at
                .date()
            )

            page_dates.append(
                actual_date
            )

            if (
                actual_date
                < start_date
            ):

                page_older += 1

                summary[
                    "date_mismatches"
                ] += 1

                print(
                    "              "
                    f"SKIP: {actual_date} "
                    "older than backfill window"
                )

                continue

            if (
                actual_date
                > end_date
            ):

                page_newer += 1

                summary[
                    "date_mismatches"
                ] += 1

                print(
                    "              "
                    f"SKIP: {actual_date} "
                    "newer than backfill window"
                )

                continue

            page_in_window += 1

            summary[
                "discovered_in_window"
            ] += 1

            if (
                not article.headline
                or len(
                    article
                    .article_text
                    .split()
                )
                < 80
            ):

                summary[
                    "invalid_articles"
                ] += 1

                print(
                    "              "
                    "Status: INVALID"
                )

                print(
                    f"              "
                    f"Warnings: "
                    f"{article.warnings}"
                )

                continue

            stage_status, output_path = (
                stage_article(
                    article,
                    collected_at=(
                        datetime.now(
                            SRI_LANKA_TZ
                        )
                    ),
                )
            )

            if (
                stage_status
                == "NEW"
            ):

                summary[
                    "new_articles"
                ] += 1

            elif (
                stage_status
                == "CHANGED"
            ):

                summary[
                    "changed_articles"
                ] += 1

            elif (
                stage_status
                == "UNCHANGED"
            ):

                summary[
                    "unchanged_after_refresh"
                ] += 1

            publication_dates.add(
                actual_date
            )

            if (
                output_path
                is not None
            ):

                mark_historical_payload(
                    output_path=(
                        output_path
                    ),
                    start_date=(
                        start_date
                    ),
                    end_date=(
                        end_date
                    ),
                    listing_page=(
                        page_number
                    ),
                )

                stored_payload = (
                    load_json(
                        output_path
                    )
                )

                stored = (
                    ExistingArticle(
                        canonical_url=(
                            canonicalize_url(
                                stored_payload.get(
                                    "source_url",
                                    url,
                                )
                            )
                        ),
                        source_url=(
                            canonicalize_url(
                                stored_payload.get(
                                    "source_url",
                                    url,
                                )
                            )
                        ),
                        content_hash=(
                            stored_payload.get(
                                "content_hash"
                            )
                        ),
                        published_date=(
                            actual_date
                        ),
                        version_number=(
                            int(
                                stored_payload.get(
                                    "article_version",
                                    1,
                                )
                                or 1
                            )
                        ),
                        path=(
                            output_path
                        ),
                    )
                )

                existing_index[
                    stored.canonical_url
                ] = stored

                existing_index[
                    stored.source_url
                ] = stored

            print(
                "              "
                f"{stage_status}: "
                f"{article.headline[:110]}"
            )

            if (
                request_delay_seconds
                > 0
                and workers == 1
            ):

                time.sleep(
                    request_delay_seconds
                )

        print(
            f"            "
            f"In window: "
            f"{page_in_window}"
        )

        print(
            f"            "
            f"Newer: "
            f"{page_newer}"
        )

        print(
            f"            "
            f"Older: "
            f"{page_older}"
        )

        # Same safe descending-archive boundary used by the
        # live public-source collector.
        if (
            len(page_dates)
            >= 3
            and max(page_dates)
            < start_date
        ):

            reached_older_boundary = (
                True
            )

            summary[
                "backfill_complete"
            ] = True

            summary[
                "stop_reason"
            ] = (
                "REACHED_OLDER_THAN_WINDOW"
            )

            checkpoint_payload = {
                "source_key": (
                    source_key
                ),
                "source_name": (
                    adapter.source_name
                ),
                "from_date": (
                    start_date.isoformat()
                ),
                "to_date": (
                    end_date.isoformat()
                ),
                "completed": True,
                "next_page": (
                    page_number + 1
                ),
                "completed_at": (
                    datetime.now(
                        SRI_LANKA_TZ
                    ).isoformat()
                ),
                "last_stop_reason": (
                    "REACHED_OLDER_THAN_WINDOW"
                ),
                "listing_route": (
                    listing_route
                ),
                "listing_url": (
                    page_url
                ),
                "summary": (
                    summary
                ),
            }

            write_checkpoint(
                checkpoint,
                checkpoint_payload,
            )

            print(
                "            "
                "Historical start boundary reached."
            )

            break

        checkpoint_payload = {
            "source_key": (
                source_key
            ),
            "source_name": (
                adapter.source_name
            ),
            "from_date": (
                start_date.isoformat()
            ),
            "to_date": (
                end_date.isoformat()
            ),
            "completed": False,
            "next_page": (
                page_number + 1
            ),
            "updated_at": (
                datetime.now(
                    SRI_LANKA_TZ
                ).isoformat()
            ),
            "last_stop_reason": (
                "PAGE_COMPLETE"
            ),
            "listing_route": (
                listing_route
            ),
            "listing_url": (
                page_url
            ),
            "summary": (
                summary
            ),
        }

        write_checkpoint(
            checkpoint,
            checkpoint_payload,
        )

        if (
            request_delay_seconds
            > 0
        ):

            time.sleep(
                request_delay_seconds
            )

    summary[
        "publication_dates"
    ] = [

        value.isoformat()

        for value in sorted(
            publication_dates
        )
    ]

    summary[
        "max_pages_reached"
    ] = (
        not reached_older_boundary
        and summary[
            "pages_scanned"
        ] > 0
        and (
            current_page
            + summary[
                "pages_scanned"
            ]
            - 1
        )
        >= max_pages
    )

    if (
        summary[
            "max_pages_reached"
        ]
        and not summary.get(
            "stop_reason"
        )
    ):

        summary[
            "stop_reason"
        ] = (
            "MAX_PAGES_REACHED"
        )

    # Persist the final summary so a completed checkpoint can be reused.
    final_checkpoint = (
        load_checkpoint(
            checkpoint
        )
    )

    final_checkpoint[
        "summary"
    ] = summary

    if reached_older_boundary:

        final_checkpoint[
            "completed"
        ] = True

    write_checkpoint(
        checkpoint,
        final_checkpoint,
    )

    return summary


# ============================================================
# CLI
# ============================================================


def parse_cli_date(
    value: str,
) -> date:

    return (
        datetime.strptime(
            value,
            "%Y-%m-%d",
        )
        .date()
    )


def main() -> None:

    parser = (
        argparse.ArgumentParser(

            description=(
                "Historical backfill for "
                "the bank-supervision "
                "news corpus."
            )
        )
    )


    parser.add_argument(

        "--source",

        choices=[
            "daily_ft",
            "ceylon_today",
            "daily_mirror",
            "island",
            "all",
        ],

        default="all",
    )


    parser.add_argument(

        "--start-date",

        required=True,

        help="YYYY-MM-DD",
    )


    parser.add_argument(

        "--end-date",

        required=True,

        help="YYYY-MM-DD",
    )


    parser.add_argument(

        "--max-pages",

        type=int,

        default=100,

        help=(
            "Maximum archive pages "
            "per source. "
            "Default: 100."
        ),
    )


    parser.add_argument(

        "--request-delay",

        type=float,

        default=0.75,

        help=(
            "Delay between HTTP requests "
            "in seconds. Default: 0.75."
        ),
    )


    parser.add_argument(

        "--refresh-existing",

        action="store_true",

        help=(
            "Re-fetch already staged URLs "
            "and detect historical changes."
        ),
    )


    parser.add_argument(

        "--reset-checkpoint",

        action="store_true",

        help=(
            "For Daily Mirror / Island only: "
            "restart historical pagination from page 1. "
            "Existing staged articles are not deleted."
        ),
    )


    parser.add_argument(

        "--workers",

        type=int,

        default=1,

        help=(
            "Daily Mirror / Island only: number of article "
            "fetch/extraction workers per listing page. "
            "Listing pagination and staging remain sequential. "
            "Recommended: 2-3. Default: 1."
        ),
    )


    args = (
        parser.parse_args()
    )


    start_date = (
        parse_cli_date(
            args.start_date
        )
    )


    end_date = (
        parse_cli_date(
            args.end_date
        )
    )


    if (
        start_date
        > end_date
    ):

        raise ValueError(
            "--start-date must be "
            "<= --end-date"
        )


    if (
        args.max_pages
        < 1
    ):

        raise ValueError(
            "--max-pages must be >= 1"
        )


    if (
        args.request_delay
        < 0
    ):

        raise ValueError(
            "--request-delay must be >= 0"
        )


    if (
        args.workers
        < 1
    ):

        raise ValueError(
            "--workers must be >= 1"
        )


    selected_source_keys = (

        [
            *SOURCES.keys(),
            *PUBLIC_BUSINESS_SOURCES.keys(),
        ]

        if args.source
        == "all"

        else [
            args.source
        ]
    )


    started_at = (
        datetime.now(
            SRI_LANKA_TZ
        )
    )


    print()

    print(
        "=" * 80
    )


    print(
        "BANK SUPERVISION NEWS "
        "HISTORICAL BACKFILL"
    )


    print(
        "=" * 80
    )


    print(
        f"Start date       : "
        f"{start_date}"
    )


    print(
        f"End date         : "
        f"{end_date}"
    )


    print(
        "Sources          : "
        + ", ".join(
            selected_source_keys
        )
    )


    print(
        "Live state files : "
        "UNCHANGED"
    )


    summaries = []

    for source_key in (
        selected_source_keys
    ):

        if (
            source_key
            in SOURCES
        ):

            summary = (
                backfill_source(

                    source=(
                        SOURCES[
                            source_key
                        ]
                    ),

                    start_date=(
                        start_date
                    ),

                    end_date=(
                        end_date
                    ),

                    max_pages=(
                        args.max_pages
                    ),

                    request_delay_seconds=(
                        args.request_delay
                    ),

                    refresh_existing=(
                        args.refresh_existing
                    ),
                )
            )

        else:

            summary = (
                backfill_public_business_source(

                    source_key=(
                        source_key
                    ),

                    adapter_class=(
                        PUBLIC_BUSINESS_SOURCES[
                            source_key
                        ]
                    ),

                    start_date=(
                        start_date
                    ),

                    end_date=(
                        end_date
                    ),

                    max_pages=(
                        args.max_pages
                    ),

                    request_delay_seconds=(
                        args.request_delay
                    ),

                    refresh_existing=(
                        args.refresh_existing
                    ),

                    reset_checkpoint=(
                        args.reset_checkpoint
                    ),

                    workers=(
                        args.workers
                    ),
                )
            )

        summaries.append(
            summary
        )


    completed_at = (
        datetime.now(
            SRI_LANKA_TZ
        )
    )


    BACKFILL_RUN_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )


    run_path = (
        BACKFILL_RUN_ROOT
        / (
            f"backfill_"
            f"{started_at.strftime('%Y%m%d_%H%M%S')}"
            f".json"
        )
    )


    payload = {

        "started_at": (
            started_at
            .isoformat()
        ),

        "completed_at": (
            completed_at
            .isoformat()
        ),

        "start_date": (
            start_date
            .isoformat()
        ),

        "end_date": (
            end_date
            .isoformat()
        ),

        "refresh_existing": (
            args.refresh_existing
        ),

        "reset_checkpoint": (
            args.reset_checkpoint
        ),

        "workers": (
            args.workers
        ),

        "sources": (
            summaries
        ),

        "publication_dates": sorted(
            {
                value

                for summary in (
                    summaries
                )

                for value in summary[
                    "publication_dates"
                ]
            }
        ),
    }


    run_path.write_text(

        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        ),

        encoding="utf-8",
    )


    print()

    print(
        "=" * 80
    )


    print(
        "HISTORICAL BACKFILL RUN FINISHED"
    )


    print(
        "=" * 80
    )


    for summary in (
        summaries
    ):

        print()

        print(
            summary[
                "source_name"
            ]
        )


        print(
            f"  Discovered : "
            f"{summary['discovered_in_window']}"
        )


        print(
            f"  Existing   : "
            f"{summary['skipped_existing']}"
        )


        print(
            f"  New        : "
            f"{summary['new_articles']}"
        )


        print(
            f"  Changed    : "
            f"{summary['changed_articles']}"
        )


        print(
            f"  Invalid    : "
            f"{summary['invalid_articles']}"
        )


        print(
            f"  Missing dt : "
            f"{summary['missing_dates']}"
        )


        print(
            f"  Date skip  : "
            f"{summary['date_mismatches']}"
        )


        print(
            f"  Failures   : "
            f"{summary['fetch_failures']}"
        )


        print(
            f"  Pages      : "
            f"{summary['pages_scanned']}"
        )


        if (
            "backfill_complete"
            in summary
        ):

            print(
                f"  Complete   : "
                f"{summary['backfill_complete']}"
            )

            print(
                f"  Stop reason: "
                f"{summary.get('stop_reason')}"
            )


        if (
            summary[
                "max_pages_reached"
            ]
        ):

            print(
                "  WARNING    : "
                "max-pages reached "
                "before older boundary"
            )


    print()

    print(
        f"Backfill summary: "
        f"{run_path}"
    )


    if any(
        summary[
            "fetch_failures"
        ] > 0

        for summary in (
            summaries
        )
    ):

        raise SystemExit(
            1
        )


if __name__ == "__main__":

    main()