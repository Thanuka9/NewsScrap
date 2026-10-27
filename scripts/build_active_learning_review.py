from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
from datetime import (
    date,
    datetime,
    timedelta,
)
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from bank_intel.intelligence.review_selection import (
    build_cluster_maps,
    build_pair_reason_map,
    load_review_config,
    merge_reason_maps,
    score_review_candidate,
    select_diverse_batch,
)


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

ML_ROOT = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "ml"
)

STAGED_ROOT = (
    PROJECT_ROOT
    / "data"
    / "staged"
)

CORPUS_ROOT = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "ml_corpus"
)

REVIEW_ROOT = (
    PROJECT_ROOT
    / "data"
    / "review"
    / "active_learning"
)

SRI_LANKA_TZ = ZoneInfo(
    "Asia/Colombo"
)


def load_json(
    path: Path,
) -> dict:

    return json.loads(
        path.read_text(
            encoding="utf-8-sig"
        )
    )


def resolve_project_path(
    value: str,
) -> Path:

    path = Path(
        value
    )

    if not path.is_absolute():

        path = (
            PROJECT_ROOT
            / path
        )

    return path


def parse_date(
    value: str,
) -> date:

    return (
        datetime.strptime(
            value,
            "%Y-%m-%d",
        )
        .date()
    )


def load_csv_rows(
    path: Path,
) -> list[
    dict
]:

    if not path.exists():

        return []

    with path.open(
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as file:

        return list(
            csv.DictReader(
                file
            )
        )


def corpus_articles(
    *,
    end_date: date,
    days: int,
) -> list[
    dict
]:

    start_date = (
        end_date
        - timedelta(
            days=(
                days
                - 1
            )
        )
    )

    current = (
        start_date
    )

    articles = []

    while (
        current
        <= end_date
    ):

        publication_date = (
            current.isoformat()
        )

        manifest_path = (
            ML_ROOT
            / publication_date
            / "ml_manifest.json"
        )

        if not manifest_path.exists():

            current += timedelta(
                days=1
            )
            continue

        manifest = (
            load_json(
                manifest_path
            )
        )

        if (
            manifest.get(
                "status"
            )
            != "SUCCESS"
        ):

            current += timedelta(
                days=1
            )
            continue

        # Review queue is allowed to contain all current Phase-2
        # articles, including Stage-A NOISE disagreements.
        if (
            str(
                manifest.get(
                    "supervisory_relevance_architecture",
                    "",
                )
                or ""
            ).upper()
            != "TWO_STAGE"
        ):

            raise RuntimeError(
                "Review window contains legacy non-two-stage ML "
                f"output on {publication_date}. Rebuild Phase-2 ML "
                "for the intended review dates before mixing them."
            )

        for item in (
            manifest.get(
                "articles",
                [],
            )
            or []
        ):

            analysis_path = (
                resolve_project_path(
                    str(
                        item[
                            "analysis_file"
                        ]
                    )
                )
            )

            if not analysis_path.exists():

                raise FileNotFoundError(
                    "ML analysis file listed in manifest "
                    "is missing: "
                    f"{analysis_path}"
                )

            analysis = (
                load_json(
                    analysis_path
                )
            )

            articles.append(
                {
                    "publication_date": (
                        publication_date
                    ),
                    "analysis": (
                        analysis
                    ),
                    "analysis_file": str(
                        item[
                            "analysis_file"
                        ]
                    ),
                }
            )

        current += timedelta(
            days=1
        )

    return articles


def build_staged_article_index() -> dict[
    str,
    dict
]:
    """
    Index newest staged version by source_url and canonical_url.
    """

    index: dict[
        str,
        dict
    ] = {}

    if not STAGED_ROOT.exists():
        return index

    for path in (
        STAGED_ROOT.rglob(
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

        source_url = str(
            payload.get(
                "source_url",
                "",
            )
            or ""
        ).strip()

        canonical_url = str(
            payload.get(
                "canonical_url",
                "",
            )
            or ""
        ).strip()

        try:

            version_number = int(
                payload.get(
                    "article_version",
                    0,
                )
                or 0
            )

        except (
            TypeError,
            ValueError,
        ):

            version_number = 0

        record = {
            "payload": (
                payload
            ),
            "path": (
                path
            ),
            "version_number": (
                version_number
            ),
        }

        for key in {
            source_url,
            canonical_url,
        }:

            if not key:
                continue

            previous = (
                index.get(
                    key
                )
            )

            if (
                previous is None
                or version_number
                >= previous[
                    "version_number"
                ]
            ):

                index[
                    key
                ] = (
                    record
                )

    return index


def stable_review_id(
    source_url: str,
) -> str:

    digest = hashlib.sha256(
        str(
            source_url
        ).encode(
            "utf-8"
        )
    ).hexdigest()[
        :12
    ]

    return (
        f"rvw_{digest}"
    )


def relative_project_path(
    path: Path,
) -> str:

    try:

        return str(
            path.relative_to(
                PROJECT_ROOT
            )
        )

    except ValueError:

        return str(
            path
        )


def primary_entities(
    analysis: dict,
) -> list[
    str
]:

    return [
        str(
            value
        )
        for value in (
            (
                (
                    analysis.get(
                        "ml",
                        {},
                    )
                    or {}
                )
                .get(
                    "entity_roles",
                    {},
                )
                or {}
            )
            .get(
                "primary_entities",
                [],
            )
            or []
        )
        if str(
            value
        ).strip()
    ]


def all_entities(
    analysis: dict,
) -> list[
    str
]:

    ml = (
        analysis.get(
            "ml",
            {},
        )
        or {}
    )

    roles = (
        ml.get(
            "entity_roles",
            {},
        )
        or {}
    )

    values = [
        str(
            item.get(
                "canonical_name",
                "",
            )
        )
        for item in (
            roles.get(
                "entities",
                [],
            )
            or []
        )
        if str(
            item.get(
                "canonical_name",
                "",
            )
        ).strip()
    ]

    if values:

        return values

    return [
        str(
            item.get(
                "canonical_name",
                "",
            )
        )
        for item in (
            ml.get(
                "cbsl_entity_matches",
                [],
            )
            or []
        )
        if str(
            item.get(
                "canonical_name",
                "",
            )
        ).strip()
    ]


def read_existing_annotations(
    path: Path,
) -> dict[
    str,
    dict
]:

    result = {}

    for row in (
        load_csv_rows(
            path
        )
    ):

        key = str(
            row.get(
                "source_url",
                "",
            )
            or ""
        ).strip()

        if not key:
            continue

        result[
            key
        ] = {
            "human_relevance_label": (
                row.get(
                    "human_relevance_label",
                    "",
                )
            ),
            "human_topic_labels": (
                row.get(
                    "human_topic_labels",
                    "",
                )
            ),
            "review_status": (
                row.get(
                    "review_status",
                    "",
                )
            ),
            "human_notes": (
                row.get(
                    "human_notes",
                    "",
                )
            ),
        }

    return result


def _two_stage_fields(
    analysis: dict,
) -> dict:

    ml = (
        analysis.get(
            "ml",
            {},
        )
        or {}
    )

    relevance = (
        ml.get(
            "supervisory_relevance",
            {},
        )
        or {}
    )

    stage_a = (
        relevance.get(
            "stage_a",
            {},
        )
        or {}
    )

    stage_b = (
        relevance.get(
            "stage_b",
            {},
        )
        or {}
    )

    comparison = (
        analysis.get(
            "supervisory_comparison",
            {},
        )
        or {}
    )

    stage_a_comparison = (
        comparison.get(
            "stage_a",
            {},
        )
        or {}
    )

    stage_b_comparison = (
        comparison.get(
            "stage_b",
            {},
        )
        or {}
    )

    return {
        "stage_a_label": (
            stage_a.get(
                "predicted_label"
            )
        ),
        "stage_a_confidence": (
            stage_a.get(
                "confidence"
            )
        ),
        "operational_keep": (
            relevance.get(
                "operational_keep"
            )
        ),
        "operational_filter_action": (
            relevance.get(
                "operational_filter_action"
            )
        ),
        "stage_b_executed": (
            relevance.get(
                "stage_b_executed"
            )
        ),
        "stage_b_label": (
            stage_b.get(
                "predicted_label"
            )
        ),
        "stage_b_confidence": (
            stage_b.get(
                "confidence"
            )
        ),
        "stage1_stage_a_status": (
            stage_a_comparison.get(
                "comparison_status"
            )
        ),
        "stage1_stage_a_agreement": (
            stage_a_comparison.get(
                "agreement"
            )
        ),
        "stage1_stage_b_status": (
            stage_b_comparison.get(
                "comparison_status"
            )
        ),
        "stage1_stage_b_agreement": (
            stage_b_comparison.get(
                "agreement"
            )
        ),
        "phase2_review_priority": (
            comparison.get(
                "overall_review_priority"
            )
        ),
    }


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Build a diversity-aware analyst validation queue "
            "from frozen two-stage relevance and Phase-3 corpus signals."
        )
    )

    parser.add_argument(
        "--end-date",
        required=True,
        help="YYYY-MM-DD",
    )

    parser.add_argument(
        "--days",
        type=int,
        default=30,
    )

    parser.add_argument(
        "--target",
        type=int,
        default=None,
        help=(
            "Number of articles to export. Defaults to "
            "review.yaml selection.default_target."
        ),
    )

    args = (
        parser.parse_args()
    )

    end_date = (
        parse_date(
            args.end_date
        )
    )

    days = int(
        args.days
    )

    if days < 1:

        raise ValueError(
            "--days must be >= 1"
        )

    start_date = (
        end_date
        - timedelta(
            days=(
                days
                - 1
            )
        )
    )

    config = (
        load_review_config()
    )

    target = (
        int(
            args.target
        )
        if args.target
        is not None
        else int(
            (
                config.get(
                    "selection",
                    {},
                )
                or {}
            )
            .get(
                "default_target",
                100,
            )
        )
    )

    corpus_root = (
        CORPUS_ROOT
        / end_date.isoformat()
        / f"window_{days}d"
    )

    cluster_path = (
        corpus_root
        / "clusters.json"
    )

    profile_path = (
        corpus_root
        / "cluster_profiles.json"
    )

    if (
        not cluster_path.exists()
        or not profile_path.exists()
    ):

        raise RuntimeError(
            "Phase-3 corpus outputs are missing. Run "
            "scripts/run_ml_corpus_analysis.py first."
        )

    cluster_payload = (
        load_json(
            cluster_path
        )
    )

    profile_payload = (
        load_json(
            profile_path
        )
    )

    (
        cluster_map,
        representative_map,
    ) = (
        build_cluster_maps(
            cluster_payload,
            profile_payload,
        )
    )

    pair_reason_map = (
        merge_reason_maps(
            build_pair_reason_map(
                load_csv_rows(
                    corpus_root
                    / "story_continuations.csv"
                ),
                "STORY_CONTINUATION",
            ),
            build_pair_reason_map(
                load_csv_rows(
                    corpus_root
                    / "shared_event_context.csv"
                ),
                "SHARED_EVENT_CONTEXT",
            ),
            build_pair_reason_map(
                load_csv_rows(
                    corpus_root
                    / "same_event_candidates.csv"
                ),
                "SAME_EVENT_CANDIDATE",
            ),
            build_pair_reason_map(
                load_csv_rows(
                    corpus_root
                    / "near_duplicate_candidates.csv"
                ),
                "NEAR_DUPLICATE_CANDIDATE",
            ),
        )
    )

    articles = (
        corpus_articles(
            end_date=(
                end_date
            ),
            days=(
                days
            ),
        )
    )

    staged_index = (
        build_staged_article_index()
    )

    rows = []

    preview_characters = int(
        (
            config.get(
                "review",
                {},
            )
            or {}
        )
        .get(
            "article_preview_characters",
            1200,
        )
    )

    for item in (
        articles
    ):

        analysis = (
            item[
                "analysis"
            ]
        )

        source_url = str(
            analysis.get(
                "source_url",
                "",
            )
            or ""
        ).strip()

        stage1 = (
            analysis.get(
                "stage1",
                {},
            )
            or {}
        )

        finbert = (
            (
                analysis.get(
                    "ml",
                    {},
                )
                or {}
            )
            .get(
                "finbert",
                {},
            )
            or {}
        )

        two_stage = (
            _two_stage_fields(
                analysis
            )
        )

        cluster = (
            cluster_map.get(
                source_url
            )
        )

        selection = (
            score_review_candidate(
                analysis=(
                    analysis
                ),
                source_url=(
                    source_url
                ),
                hdbscan_cluster=(
                    cluster
                ),
                representative_rank=(
                    representative_map.get(
                        source_url
                    )
                ),
                pair_reasons=(
                    pair_reason_map.get(
                        source_url,
                        set(),
                    )
                ),
                config=(
                    config
                ),
            )
        )

        staged_record = (
            staged_index.get(
                source_url
            )
            or staged_index.get(
                str(
                    analysis.get(
                        "canonical_url",
                        "",
                    )
                    or ""
                ).strip()
            )
        )

        staged_payload = (
            staged_record[
                "payload"
            ]
            if staged_record
            else {}
        )

        article_text = str(
            staged_payload.get(
                "article_text",
                "",
            )
            or ""
        )

        staged_file = (
            relative_project_path(
                staged_record[
                    "path"
                ]
            )
            if staged_record
            else ""
        )

        row = {
            "review_id": (
                stable_review_id(
                    source_url
                )
            ),

            "priority_score": round(
                selection.priority_score,
                3,
            ),

            "selection_reasons": (
                "; ".join(
                    selection.selection_reasons
                )
            ),

            "publication_date": (
                item[
                    "publication_date"
                ]
            ),

            "source_name": (
                analysis.get(
                    "source_name"
                )
            ),

            "headline": (
                analysis.get(
                    "headline"
                )
            ),

            "source_url": (
                source_url
            ),

            "stage1_gate_decision": (
                stage1.get(
                    "gate_decision"
                )
            ),

            "stage1_financial_score": (
                stage1.get(
                    "financial_relevance_score"
                )
            ),

            "stage1_pr_score": (
                stage1.get(
                    "pr_noise_score"
                )
            ),

            **two_stage,

            "finbert_sentiment": (
                finbert.get(
                    "sentiment_label"
                )
            ),

            "finbert_sentiment_confidence": (
                finbert.get(
                    "sentiment_confidence"
                )
            ),

            "hdbscan_cluster": (
                selection.hdbscan_cluster
            ),

            "is_hdbscan_outlier": (
                selection.is_hdbscan_outlier
            ),

            # Backward-compatible export only.
            "is_hdbscan_noise": (
                selection.is_hdbscan_noise
            ),

            "cluster_representative_rank": (
                selection
                .cluster_representative_rank
            ),

            "primary_entities": (
                "; ".join(
                    primary_entities(
                        analysis
                    )
                )
            ),

            "all_entities": (
                "; ".join(
                    all_entities(
                        analysis
                    )
                )
            ),

            "article_preview": (
                article_text[
                    :preview_characters
                ]
            ),

            "staged_file": (
                staged_file
            ),

            "analysis_file": (
                item[
                    "analysis_file"
                ]
            ),

            # Human fields. Never auto-fill these.
            "human_relevance_label": "",
            "human_topic_labels": "",
            "review_status": "",
            "human_notes": "",
        }

        rows.append(
            row
        )

    selected = (
        select_diverse_batch(
            rows,
            target=(
                target
            ),
        )
    )

    REVIEW_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path = (
        REVIEW_ROOT
        / (
            f"review_batch_"
            f"{end_date.isoformat()}_"
            f"{days}d.csv"
        )
    )

    existing_annotations = (
        read_existing_annotations(
            output_path
        )
    )

    for row in (
        selected
    ):

        existing = (
            existing_annotations.get(
                str(
                    row[
                        "source_url"
                    ]
                )
            )
        )

        if existing:

            row.update(
                existing
            )

    fieldnames = [
        "review_id",
        "priority_score",
        "selection_reasons",
        "publication_date",
        "source_name",
        "headline",
        "source_url",

        "stage1_gate_decision",
        "stage1_financial_score",
        "stage1_pr_score",

        "stage_a_label",
        "stage_a_confidence",
        "operational_keep",
        "operational_filter_action",

        "stage_b_executed",
        "stage_b_label",
        "stage_b_confidence",

        "stage1_stage_a_status",
        "stage1_stage_a_agreement",
        "stage1_stage_b_status",
        "stage1_stage_b_agreement",
        "phase2_review_priority",

        "finbert_sentiment",
        "finbert_sentiment_confidence",

        "hdbscan_cluster",
        "is_hdbscan_outlier",
        "is_hdbscan_noise",
        "cluster_representative_rank",

        "primary_entities",
        "all_entities",
        "article_preview",
        "staged_file",
        "analysis_file",

        "human_relevance_label",
        "human_topic_labels",
        "review_status",
        "human_notes",
    ]

    with output_path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=(
                fieldnames
            ),
            extrasaction=(
                "ignore"
            ),
        )

        writer.writeheader()

        writer.writerows(
            selected
        )

    manifest_path = (
        output_path.with_suffix(
            ".json"
        )
    )

    stage_a_distribution = dict(
        Counter(
            str(
                row.get(
                    "stage_a_label",
                    "UNKNOWN",
                )
                or "UNKNOWN"
            )
            for row in (
                selected
            )
        )
    )

    stage_b_distribution = dict(
        Counter(
            (
                str(
                    row.get(
                        "stage_b_label"
                    )
                    or "NOT_EXECUTED"
                )
            )
            for row in (
                selected
            )
        )
    )

    manifest = {
        "status": "SUCCESS",

        "created_at": (
            datetime.now(
                SRI_LANKA_TZ
            )
            .isoformat()
        ),

        "review_version": (
            config.get(
                "version"
            )
        ),

        "purpose": (
            "ANALYST_VALIDATION_QUEUE"
        ),

        "automatic_retraining": (
            False
        ),

        "window_start": (
            start_date.isoformat()
        ),

        "window_end": (
            end_date.isoformat()
        ),

        "days": (
            days
        ),

        "available_article_count": len(
            rows
        ),

        "target": (
            target
        ),

        "selected_count": len(
            selected
        ),

        "selected_stage_a_distribution": (
            stage_a_distribution
        ),

        "selected_stage_b_distribution": (
            stage_b_distribution
        ),

        "selected_hdbscan_outlier_count": sum(
            bool(
                row.get(
                    "is_hdbscan_outlier"
                )
            )
            for row in (
                selected
            )
        ),

        "review_file": str(
            output_path.relative_to(
                PROJECT_ROOT
            )
        ),

        "relevance_labels": (
            (
                config.get(
                    "labels",
                    {},
                )
                or {}
            )
            .get(
                "relevance",
                [],
            )
        ),

        "supervisory_topics": (
            (
                config.get(
                    "labels",
                    {},
                )
                or {}
            )
            .get(
                "supervisory_topics",
                [],
            )
        ),
    }

    manifest_path.write_text(
        json.dumps(
            manifest,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print()
    print(
        "=" * 88
    )
    print(
        "PHASE 3 ANALYST REVIEW QUEUE COMPLETE"
    )
    print(
        "=" * 88
    )
    print(
        f"Window start        : {start_date}"
    )
    print(
        f"Window end          : {end_date}"
    )
    print(
        f"Available articles  : {len(rows)}"
    )
    print(
        f"Target              : {target}"
    )
    print(
        f"Selected            : {len(selected)}"
    )
    print(
        f"Stage A selected    : {stage_a_distribution}"
    )
    print(
        f"Stage B selected    : {stage_b_distribution}"
    )
    print(
        "HDBSCAN outliers    : "
        f"{manifest['selected_hdbscan_outlier_count']}"
    )
    print(
        "Preserved labels    : "
        f"{sum(1 for row in selected if row['human_relevance_label'])}"
    )
    print(
        f"Review CSV          : {output_path}"
    )
    print(
        f"Manifest            : {manifest_path}"
    )


if __name__ == "__main__":

    main()
