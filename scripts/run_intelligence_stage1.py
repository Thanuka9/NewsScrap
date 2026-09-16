from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from bank_intel.intelligence.stage1_gate import (
    analyse_stage1,
    load_stage1_config,
)


# ============================================================
# PROJECT
# ============================================================

PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

STAGED_ROOT = (
    PROJECT_ROOT
    / "data"
    / "staged"
)

PROCESSED_ROOT = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "stage1"
)

REVIEW_ROOT = (
    PROJECT_ROOT
    / "data"
    / "review"
)

SRI_LANKA_TZ = ZoneInfo(
    "Asia/Colombo"
)


# ============================================================
# ACTIVE INGESTION SOURCES
#
# Future sources can simply be added here when activated.
# ============================================================

ACTIVE_SOURCE_FOLDERS = [
    "daily_ft",
    "ceylon_today",
]


# ============================================================
# DATE PATTERN
# ============================================================

DATE_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}$"
)

VERSION_PATTERN = re.compile(
    r"v(\d+)\.json$",
    re.IGNORECASE,
)


# ============================================================
# LOAD JSON
# ============================================================


def load_json(
    path: Path,
) -> dict:

    return json.loads(
        path.read_text(
            encoding="utf-8",
        )
    )


# ============================================================
# VERSION
# ============================================================


def version_number(
    path: Path,
    payload: dict,
) -> int:
    """
    Determine article version.

    Priority:
        1. article_version field
        2. filename such as v001.json
        3. default 0
    """

    value = payload.get(
        "article_version"
    )

    try:

        if value is not None:

            return int(
                value
            )

    except (
        TypeError,
        ValueError,
    ):

        pass

    match = VERSION_PATTERN.search(
        path.name
    )

    if match:

        return int(
            match.group(1)
        )

    return 0


# ============================================================
# PUBLISHED DATE
# ============================================================


def published_date(
    payload: dict,
) -> str | None:

    value = payload.get(
        "published_at"
    )

    if not value:

        return None

    try:

        parsed = datetime.fromisoformat(
            str(value)
        )

    except (
        TypeError,
        ValueError,
    ):

        return None

    if parsed.tzinfo is None:

        parsed = parsed.replace(
            tzinfo=SRI_LANKA_TZ
        )

    else:

        parsed = parsed.astimezone(
            SRI_LANKA_TZ
        )

    return (
        parsed.date()
        .isoformat()
    )


# ============================================================
# DATE FROM FILE PATH
# ============================================================


def path_date(
    path: Path,
) -> str | None:
    """
    Find YYYY-MM-DD anywhere in the staged article path.

    Supports layouts such as:

        staged/daily_ft/2026-09-15/<id>/v001.json

    as well as older layouts.
    """

    for part in path.parts:

        if DATE_PATTERN.match(
            part
        ):

            return part

    return None


# ============================================================
# TARGET DATE MATCHING
# ============================================================


def matches_target_date(
    *,
    path: Path,
    payload: dict,
    target_date: str,
) -> tuple[
    bool,
    str | None,
]:
    """
    Determine whether an article belongs in this Stage-1 run.

    Priority evidence:

        collection_run_date
        path date
        published_at

    We deliberately support old files created before
    collection_run_date existed.
    """

    collection_date = payload.get(
        "collection_run_date"
    )

    if (
        collection_date
        and str(
            collection_date
        ) == target_date
    ):

        return (
            True,
            "collection_run_date",
        )

    file_date = path_date(
        path
    )

    if (
        file_date
        == target_date
    ):

        return (
            True,
            "path_date",
        )

    article_pub_date = (
        published_date(
            payload
        )
    )

    if (
        article_pub_date
        == target_date
    ):

        return (
            True,
            "published_at",
        )

    return (
        False,
        None,
    )


# ============================================================
# ARTICLE IDENTITY
# ============================================================


def article_identity(
    *,
    path: Path,
    payload: dict,
    source_folder: str,
) -> str:
    """
    Stable article identity for deduplication.

    Priority:
        canonical_url
        source_url
        article folder
        complete path
    """

    canonical_url = payload.get(
        "canonical_url"
    )

    if canonical_url:

        return (
            f"{source_folder}|"
            f"{canonical_url}"
        )

    source_url = payload.get(
        "source_url"
    )

    if source_url:

        return (
            f"{source_folder}|"
            f"{source_url}"
        )

    if path.parent.name:

        return (
            f"{source_folder}|"
            f"{path.parent.name}"
        )

    return (
        f"{source_folder}|"
        f"{path}"
    )


# ============================================================
# FIND STAGED ARTICLE FILES
# ============================================================


def find_latest_article_files(
    *,
    target_date: str,
) -> tuple[
    list[Path],
    dict,
]:
    """
    Recursively scan staged article storage.

    This does NOT assume one exact historical directory layout.

    It also chooses only the latest version of each article.
    """

    latest_by_article = {}

    scanned_files = 0

    valid_json_files = 0

    matched_files = 0

    source_counts = {}

    match_methods = {
        "collection_run_date": 0,
        "path_date": 0,
        "published_at": 0,
    }

    load_failures = 0

    for source_folder in (
        ACTIVE_SOURCE_FOLDERS
    ):

        source_root = (
            STAGED_ROOT
            / source_folder
        )

        source_counts[
            source_folder
        ] = {
            "scanned": 0,
            "matched": 0,
        }

        if not source_root.exists():

            continue

        for path in source_root.rglob(
            "*.json"
        ):

            scanned_files += 1

            source_counts[
                source_folder
            ][
                "scanned"
            ] += 1

            try:

                payload = load_json(
                    path
                )

            except Exception:

                load_failures += 1

                continue

            if not isinstance(
                payload,
                dict,
            ):

                continue

            valid_json_files += 1

            (
                matched,
                match_method,
            ) = matches_target_date(
                path=path,
                payload=payload,
                target_date=(
                    target_date
                ),
            )

            if not matched:

                continue

            matched_files += 1

            source_counts[
                source_folder
            ][
                "matched"
            ] += 1

            if match_method:

                match_methods[
                    match_method
                ] += 1

            identity = article_identity(
                path=path,
                payload=payload,
                source_folder=(
                    source_folder
                ),
            )

            candidate_version = (
                version_number(
                    path,
                    payload,
                )
            )

            existing = (
                latest_by_article.get(
                    identity
                )
            )

            if existing is None:

                latest_by_article[
                    identity
                ] = {
                    "path": path,
                    "version": (
                        candidate_version
                    ),
                    "mtime": (
                        path.stat()
                        .st_mtime
                    ),
                }

                continue

            # ------------------------------------------------
            # PREFER HIGHER VERSION
            # ------------------------------------------------

            if (
                candidate_version
                > existing[
                    "version"
                ]
            ):

                latest_by_article[
                    identity
                ] = {
                    "path": path,
                    "version": (
                        candidate_version
                    ),
                    "mtime": (
                        path.stat()
                        .st_mtime
                    ),
                }

                continue

            # ------------------------------------------------
            # SAME VERSION:
            # PREFER MOST RECENT FILE
            # ------------------------------------------------

            if (
                candidate_version
                == existing[
                    "version"
                ]
                and (
                    path.stat()
                    .st_mtime
                    > existing[
                        "mtime"
                    ]
                )
            ):

                latest_by_article[
                    identity
                ] = {
                    "path": path,
                    "version": (
                        candidate_version
                    ),
                    "mtime": (
                        path.stat()
                        .st_mtime
                    ),
                }

    selected = sorted(
        (
            item[
                "path"
            ]
            for item
            in latest_by_article.values()
        ),
        key=lambda value: str(
            value
        ),
    )

    diagnostics = {
        "scanned_json_files": (
            scanned_files
        ),

        "valid_json_files": (
            valid_json_files
        ),

        "matched_json_files": (
            matched_files
        ),

        "unique_articles_selected": (
            len(selected)
        ),

        "load_failures": (
            load_failures
        ),

        "match_methods": (
            match_methods
        ),

        "source_counts": (
            source_counts
        ),
    }

    return (
        selected,
        diagnostics,
    )


# ============================================================
# AVAILABLE STAGED DATES
# ============================================================


def find_available_dates() -> dict:
    """
    Helpful diagnostic when the requested date has no files.
    """

    dates = {}

    for source_folder in (
        ACTIVE_SOURCE_FOLDERS
    ):

        source_root = (
            STAGED_ROOT
            / source_folder
        )

        source_dates = {}

        if not source_root.exists():

            dates[
                source_folder
            ] = {}

            continue

        for path in source_root.rglob(
            "*.json"
        ):

            try:

                payload = load_json(
                    path
                )

            except Exception:

                continue

            candidates = set()

            collection_date = (
                payload.get(
                    "collection_run_date"
                )
            )

            if (
                collection_date
                and DATE_PATTERN.match(
                    str(
                        collection_date
                    )
                )
            ):

                candidates.add(
                    str(
                        collection_date
                    )
                )

            file_date = path_date(
                path
            )

            if file_date:

                candidates.add(
                    file_date
                )

            pub_date = published_date(
                payload
            )

            if pub_date:

                candidates.add(
                    pub_date
                )

            for value in candidates:

                source_dates[
                    value
                ] = (
                    source_dates.get(
                        value,
                        0,
                    )
                    + 1
                )

        dates[
            source_folder
        ] = dict(
            sorted(
                source_dates.items()
            )
        )

    return dates


# ============================================================
# SOURCE SLUG
# ============================================================


def source_slug(
    source_name: str,
) -> str:

    value = (
        source_name
        .lower()
        .strip()
    )

    value = re.sub(
        r"[^a-z0-9]+",
        "_",
        value,
    )

    return value.strip(
        "_"
    )


# ============================================================
# ARTICLE ID
# ============================================================


def article_id_from_path(
    path: Path,
    article: dict,
) -> str:

    # Existing folder ID is normally ideal.
    if (
        path.parent
        and path.parent.name
        and path.parent.name
        not in {
            "daily_ft",
            "ceylon_today",
        }
        and not DATE_PATTERN.match(
            path.parent.name
        )
    ):

        return path.parent.name

    # Fallback using content hash.
    content_hash = article.get(
        "content_hash"
    )

    if content_hash:

        return str(
            content_hash
        )[:16]

    # Last fallback.
    return re.sub(
        r"[^a-zA-Z0-9]+",
        "_",
        path.stem,
    )


# ============================================================
# SAVE ANALYSIS
# ============================================================


def save_analysis(
    *,
    run_date: str,
    input_path: Path,
    article: dict,
    analysis,
) -> Path:

    source_name = str(
        article.get(
            "source_name",
            "unknown",
        )
        or "unknown"
    )

    article_id = (
        article_id_from_path(
            input_path,
            article,
        )
    )

    folder = (
        PROCESSED_ROOT
        / run_date
        / source_slug(
            source_name
        )
    )

    folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path = (
        folder
        / f"{article_id}.json"
    )

    try:

        input_file_value = str(
            input_path.relative_to(
                PROJECT_ROOT
            )
        )

    except ValueError:

        input_file_value = str(
            input_path
        )

    payload = {
        "processed_at": (
            datetime.now(
                SRI_LANKA_TZ
            ).isoformat()
        ),

        "analysis_date": (
            run_date
        ),

        "input_file": (
            input_file_value
        ),

        "source_name": (
            article.get(
                "source_name"
            )
        ),

        "section_name": (
            article.get(
                "section_name"
            )
        ),

        "headline": (
            article.get(
                "headline"
            )
        ),

        "published_at": (
            article.get(
                "published_at"
            )
        ),

        # ----------------------------------------------------
        # ORIGINAL CITATION
        # ----------------------------------------------------

        "source_url": (
            article.get(
                "source_url"
            )
        ),

        "canonical_url": (
            article.get(
                "canonical_url"
            )
        ),

        "content_hash": (
            article.get(
                "content_hash"
            )
        ),

        "article_version": (
            article.get(
                "article_version"
            )
        ),

        "collection_status": (
            article.get(
                "collection_status"
            )
        ),

        "intake_status": (
            article.get(
                "intake_status"
            )
        ),

        "collection_run_date": (
            article.get(
                "collection_run_date"
            )
        ),

        "stage1": (
            analysis.to_dict()
        ),
    }

    output_path.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return output_path


# ============================================================
# MAIN
# ============================================================


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Stage-1 financial relevance "
            "and PR/noise analysis."
        )
    )

    parser.add_argument(
        "--date",
        help=(
            "Target date YYYY-MM-DD. "
            "Matches collection date, staged path date, "
            "or publication date."
        ),
    )

    args = parser.parse_args()

    if args.date:

        run_date = args.date

    else:

        run_date = (
            datetime.now(
                SRI_LANKA_TZ
            )
            .date()
            .isoformat()
        )

    # ========================================================
    # VALIDATE TARGET DATE
    # ========================================================

    try:

        datetime.strptime(
            run_date,
            "%Y-%m-%d",
        )

    except ValueError:

        raise ValueError(
            "--date must use YYYY-MM-DD format."
        )

    # ========================================================
    # CONFIG
    # ========================================================

    config = (
        load_stage1_config()
    )

    # ========================================================
    # DISCOVER ARTICLES
    # ========================================================

    (
        article_files,
        discovery_diagnostics,
    ) = find_latest_article_files(
        target_date=run_date
    )

    print()
    print("=" * 80)

    print(
        "BANK SUPERVISION NEWS "
        "INTELLIGENCE — STAGE 1"
    )

    print("=" * 80)

    print(
        f"Target date       : "
        f"{run_date}"
    )

    print(
        f"Rules version     : "
        f"{config.get('version')}"
    )

    print(
        f"JSON files scanned: "
        f"{discovery_diagnostics['scanned_json_files']}"
    )

    print(
        f"Date matches      : "
        f"{discovery_diagnostics['matched_json_files']}"
    )

    print(
        f"Unique articles   : "
        f"{discovery_diagnostics['unique_articles_selected']}"
    )

    print()

    print(
        "Match methods:"
    )

    for (
        method,
        count,
    ) in discovery_diagnostics[
        "match_methods"
    ].items():

        print(
            f"  {method:20} "
            f"{count}"
        )

    print()

    print(
        "Sources:"
    )

    for (
        source,
        values,
    ) in discovery_diagnostics[
        "source_counts"
    ].items():

        print(
            f"  {source:20} "
            f"scanned="
            f"{values['scanned']} "
            f"matched="
            f"{values['matched']}"
        )

    # ========================================================
    # NOTHING FOUND
    # ========================================================

    if not article_files:

        print()
        print(
            "No staged articles matched "
            "the requested date."
        )

        print()
        print(
            "Available staged dates:"
        )

        available_dates = (
            find_available_dates()
        )

        found_any = False

        for (
            source,
            date_counts,
        ) in available_dates.items():

            print()

            print(
                f"{source}:"
            )

            if not date_counts:

                print(
                    "  No staged JSON files."
                )

                continue

            found_any = True

            for (
                value,
                count,
            ) in date_counts.items():

                print(
                    f"  {value}: "
                    f"{count} file(s)"
                )

        if not found_any:

            print()
            print(
                "No staged article JSON files "
                "exist under data/staged."
            )

        return

    # ========================================================
    # REVIEW OUTPUT
    # ========================================================

    REVIEW_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    review_path = (
        REVIEW_ROOT
        / (
            f"stage1_review_"
            f"{run_date}.csv"
        )
    )

    review_rows = []

    include_count = 0

    watch_count = 0

    exclude_count = 0

    failed_count = 0

    # ========================================================
    # ANALYSE
    # ========================================================

    for index, path in enumerate(
        article_files,
        start=1,
    ):

        print()
        print("-" * 80)

        print(
            f"[{index}/"
            f"{len(article_files)}]"
        )

        try:

            article = load_json(
                path
            )

            headline = str(
                article.get(
                    "headline",
                    "",
                )
                or ""
            ).strip()

            article_text = str(
                article.get(
                    "article_text",
                    "",
                )
                or ""
            ).strip()

            source_url = str(
                article.get(
                    "source_url",
                    "",
                )
                or ""
            ).strip()

            if not headline:

                raise ValueError(
                    "HEADLINE_MISSING"
                )

            if not article_text:

                raise ValueError(
                    "ARTICLE_TEXT_MISSING"
                )

            if not source_url:

                raise ValueError(
                    "SOURCE_URL_MISSING"
                )

            analysis = (
                analyse_stage1(
                    headline=headline,
                    article_text=(
                        article_text
                    ),
                    config=config,
                )
            )

            output_path = (
                save_analysis(
                    run_date=run_date,
                    input_path=path,
                    article=article,
                    analysis=analysis,
                )
            )

            # ================================================
            # COUNTERS
            # ================================================

            if (
                analysis.gate_decision
                == "INCLUDE_CANDIDATE"
            ):

                include_count += 1

            elif (
                analysis.gate_decision
                == "WATCH"
            ):

                watch_count += 1

            else:

                exclude_count += 1

            # ================================================
            # REVIEW VALUES
            # ================================================

            institutions_text = (
                "; ".join(
                    analysis.institutions
                )
            )

            financial_terms = []

            for (
                category,
                terms,
            ) in (
                analysis
                .matched_financial_terms
                .items()
            ):

                financial_terms.append(
                    f"{category}:"
                    + "|".join(
                        terms
                    )
                )

            pr_terms = []

            for (
                category,
                terms,
            ) in (
                analysis
                .matched_pr_terms
                .items()
            ):

                pr_terms.append(
                    f"{category}:"
                    + "|".join(
                        terms
                    )
                )

            review_rows.append(
                {
                    "source_name": (
                        article.get(
                            "source_name"
                        )
                    ),

                    "headline": (
                        headline
                    ),

                    "published_at": (
                        article.get(
                            "published_at"
                        )
                    ),

                    "source_url": (
                        source_url
                    ),

                    "financial_score": (
                        analysis
                        .financial_relevance_score
                    ),

                    "financial_label": (
                        analysis
                        .financial_relevance_label
                    ),

                    "pr_score": (
                        analysis
                        .pr_noise_score
                    ),

                    "pr_label": (
                        analysis
                        .pr_noise_label
                    ),

                    "article_type_hint": (
                        analysis
                        .article_type_hint
                    ),

                    "gate_decision": (
                        analysis
                        .gate_decision
                    ),

                    "institutions": (
                        institutions_text
                    ),

                    "matched_financial_terms": (
                        "; ".join(
                            financial_terms
                        )
                    ),

                    "matched_pr_terms": (
                        "; ".join(
                            pr_terms
                        )
                    ),

                    "pr_counter_signals": (
                        "; ".join(
                            analysis
                            .pr_counter_signals
                        )
                    ),

                    # ----------------------------------------
                    # HUMAN SUPERVISORY LABEL
                    # ----------------------------------------

                    "human_label": "",

                    "review_notes": "",

                    "analysis_file": (
                        str(
                            output_path
                        )
                    ),
                }
            )

            # ================================================
            # TERMINAL
            # ================================================

            print(
                f"Source      : "
                f"{article.get('source_name')}"
            )

            print(
                f"Headline    : "
                f"{headline}"
            )

            print(
                f"Published   : "
                f"{article.get('published_at')}"
            )

            print(
                f"Financial   : "
                f"{analysis.financial_relevance_score}/100 "
                f"({analysis.financial_relevance_label})"
            )

            print(
                f"PR/noise    : "
                f"{analysis.pr_noise_score}/100 "
                f"({analysis.pr_noise_label})"
            )

            print(
                f"Type hint   : "
                f"{analysis.article_type_hint}"
            )

            print(
                f"Decision    : "
                f"{analysis.gate_decision}"
            )

            if analysis.institutions:

                print(
                    f"Institutions: "
                    f"{institutions_text}"
                )

            print(
                f"Source URL  : "
                f"{source_url}"
            )

        except Exception as exc:

            failed_count += 1

            print(
                f"FAILED: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

    # ========================================================
    # REVIEW CSV
    # ========================================================

    fieldnames = [
        "source_name",
        "headline",
        "published_at",
        "source_url",

        "financial_score",
        "financial_label",

        "pr_score",
        "pr_label",

        "article_type_hint",
        "gate_decision",

        "institutions",

        "matched_financial_terms",
        "matched_pr_terms",
        "pr_counter_signals",

        "human_label",
        "review_notes",

        "analysis_file",
    ]

    with review_path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )

        writer.writeheader()

        writer.writerows(
            review_rows
        )

    # ========================================================
    # SUMMARY
    # ========================================================

    print()
    print("=" * 80)

    print(
        "STAGE-1 INTELLIGENCE COMPLETE"
    )

    print("=" * 80)

    print(
        f"Articles analysed   : "
        f"{len(review_rows)}"
    )

    print(
        f"Include candidates  : "
        f"{include_count}"
    )

    print(
        f"Watch               : "
        f"{watch_count}"
    )

    print(
        f"Excluded as noise   : "
        f"{exclude_count}"
    )

    print(
        f"Failures            : "
        f"{failed_count}"
    )

    print()

    print(
        f"Human review file:\n"
        f"{review_path}"
    )

    print()

    print(
        f"Analysis JSON root:\n"
        f"{PROCESSED_ROOT / run_date}"
    )


if __name__ == "__main__":
    main()