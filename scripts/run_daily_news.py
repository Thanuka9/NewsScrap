from __future__ import annotations

from _runtime import locked_main
import json
import time
from datetime import datetime
from pathlib import Path

from bank_intel.collectors.browser_client import (
    BrowserClient,
)

from bank_intel.collectors.sources.daily_news import (
    SOURCE_NAME,
    SECTION_NAME,
    BUSINESS_URL,
    discover_articles,
)

from bank_intel.common.collection_window import (
    get_collection_window,
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
    / "daily_news"
)

STAGED_ROOT = (
    PROJECT_ROOT
    / "data"
    / "staged"
    / "daily_news"
)


# ============================================================
# SETTINGS
# ============================================================

# Keep browser visible for this first access test.
HEADLESS = False

# Only test the first 5 qualifying articles initially.
MAX_ARTICLE_TESTS = 5

REQUEST_DELAY_SECONDS = 1.0


# ============================================================
# SAVE
# ============================================================

def save_raw_html(
    *,
    html: str,
    article_id: str,
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

    path = folder / "v001.html"

    path.write_text(
        html,
        encoding="utf-8",
    )

    return path


def save_json(
    *,
    article,
    validation,
    discovered,
    article_id: str,
    date_folder: str,
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

    path = folder / "v001.json"

    payload = article.to_dict()

    payload[
        "listing_excerpt"
    ] = discovered.excerpt

    payload[
        "listing_published_at"
    ] = discovered.published_at_hint

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
# MAIN
# ============================================================

def main():

    window = (
        get_collection_window()
    )

    date_folder = (
        window.end_date.isoformat()
    )

    print()
    print("=" * 76)

    print(
        "DAILY NEWS BUSINESS "
        "BROWSER EXTRACTION TEST"
    )

    print("=" * 76)

    print(
        f"Mode : {window.mode}"
    )

    print(
        f"From : {window.start_date}"
    )

    print(
        f"To   : {window.end_date}"
    )

    print()
    print(
        f"Business page:\n"
        f"{BUSINESS_URL}"
    )

    with BrowserClient(
        headless=HEADLESS
    ) as browser:

        # ====================================================
        # 1. OPEN BUSINESS PAGE
        # ====================================================

        print()
        print(
            "[1] Opening Daily News Business page..."
        )

        listing = browser.get(
            BUSINESS_URL
        )

        print(
            f"    Status: "
            f"{listing.status_code}"
        )

        print(
            f"    Final URL: "
            f"{listing.final_url}"
        )

        # ====================================================
        # 2. DISCOVER
        # ====================================================

        discovered = (
            discover_articles(
                listing.html
            )
        )

        print()
        print(
            f"[2] Discovered "
            f"{len(discovered)} "
            f"Business article URLs."
        )

        # ====================================================
        # 3. DATE FILTER
        # ====================================================

        candidates = []

        for item in discovered:

            article_date = (
                parse_article_date(
                    item.published_at_hint
                )
            )

            if article_date is None:

                continue

            if window.contains_date(
                article_date
            ):

                candidates.append(
                    item
                )

        print(
            f"[3] Articles in required "
            f"date window: "
            f"{len(candidates)}"
        )

        print()

        for index, item in enumerate(
            candidates,
            start=1,
        ):

            print(
                f"{index:02d}. "
                f"{item.headline}"
            )

            print(
                f"    Date: "
                f"{item.published_at_hint}"
            )

            print(
                f"    URL : "
                f"{item.url}"
            )

            if item.excerpt:

                print(
                    f"    Excerpt: "
                    f"{item.excerpt[:160]}"
                )

        # ====================================================
        # 4. OPEN INDIVIDUAL ARTICLE PAGES
        # ====================================================

        test_items = candidates[
            :MAX_ARTICLE_TESTS
        ]

        print()
        print(
            f"[4] Testing "
            f"{len(test_items)} article pages..."
        )

        valid_count = 0

        blocked_count = 0

        invalid_count = 0

        for index, item in enumerate(
            test_items,
            start=1,
        ):

            print()
            print("-" * 76)

            print(
                f"[{index}/"
                f"{len(test_items)}]"
            )

            print(
                item.headline
            )

            print(
                item.url
            )

            try:

                result = (
                    browser.get_via_listing(
                        listing_url=(
                            BUSINESS_URL
                        ),
                        target_url=(
                            item.url
                        ),
                    )
                )

                print(
                    f"Browser status: "
                    f"{result.status_code}"
                )

                print(
                    f"Final URL: "
                    f"{result.final_url}"
                )

                # --------------------------------------------
                # ACCESS CHECK
                # --------------------------------------------

                if (
                    result.status_code
                    in {
                        401,
                        403,
                        429,
                    }
                ):

                    blocked_count += 1

                    print(
                        "STATUS: "
                        "ARTICLE_ACCESS_BLOCKED"
                    )

                    continue

                # --------------------------------------------
                # EXTRACT
                # --------------------------------------------

                article = extract_article(
                    html=result.html,
                    source_name=(
                        SOURCE_NAME
                    ),
                    section_name=(
                        SECTION_NAME
                    ),
                    source_url=(
                        item.url
                    ),
                    final_url=(
                        result.final_url
                    ),
                )

                validation = (
                    validate_article(
                        article
                    )
                )

                print(
                    f"Extracted headline: "
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
                    f"Valid: "
                    f"{validation.valid}"
                )

                if validation.errors:

                    print(
                        f"Errors: "
                        f"{validation.errors}"
                    )

                if validation.warnings:

                    print(
                        f"Warnings: "
                        f"{validation.warnings}"
                    )

                # --------------------------------------------
                # SAVE ONLY VALID FULL ARTICLES
                # --------------------------------------------

                if validation.valid:

                    article_id = (
                        url_hash(
                            article.canonical_url
                        )
                    )

                    raw_path = (
                        save_raw_html(
                            html=result.html,
                            article_id=(
                                article_id
                            ),
                            date_folder=(
                                date_folder
                            ),
                        )
                    )

                    json_path = (
                        save_json(
                            article=article,
                            validation=(
                                validation
                            ),
                            discovered=item,
                            article_id=(
                                article_id
                            ),
                            date_folder=(
                                date_folder
                            ),
                        )
                    )

                    valid_count += 1

                    print(
                        "STATUS: VALID"
                    )

                    print(
                        f"Source Link: "
                        f"{article.source_url}"
                    )

                    print(
                        f"Raw: {raw_path}"
                    )

                    print(
                        f"JSON: {json_path}"
                    )

                else:

                    invalid_count += 1

                    print(
                        "STATUS: "
                        "EXTRACTION_INVALID"
                    )

            except Exception as exc:

                invalid_count += 1

                print(
                    "STATUS: ERROR"
                )

                print(
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

            time.sleep(
                REQUEST_DELAY_SECONDS
            )

    # ========================================================
    # SUMMARY
    # ========================================================

    print()
    print("=" * 76)

    print(
        "DAILY NEWS TEST COMPLETE"
    )

    print("=" * 76)

    print(
        f"Current-date candidates : "
        f"{len(candidates)}"
    )

    print(
        f"Full valid articles     : "
        f"{valid_count}"
    )

    print(
        f"Blocked article pages   : "
        f"{blocked_count}"
    )

    print(
        f"Invalid/errors          : "
        f"{invalid_count}"
    )


if __name__ == "__main__":
    main()