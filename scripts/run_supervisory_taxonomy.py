from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import (
    date,
    datetime,
    timedelta,
)
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from bank_intel.intelligence.finbert_engine import (
    load_ml_config,
)
from bank_intel.intelligence.supervisory_taxonomy import (
    SupervisoryTaxonomyEngine,
    load_taxonomy_config,
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

TAXONOMY_ROOT = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "taxonomy"
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


def write_csv(
    path: Path,
    rows: list[
        dict
    ],
    fieldnames: list[
        str
    ],
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=(
                fieldnames
            ),
            extrasaction="ignore",
        )

        writer.writeheader()

        writer.writerows(
            rows
        )


def build_staged_article_index() -> dict[
    str,
    dict
]:

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
            "path": (
                path
            ),
            "payload": (
                payload
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


def load_cluster_map(
    path: Path,
) -> dict[
    str,
    int | None
]:

    payload = (
        load_json(
            path
        )
    )

    result = {}

    for item in (
        payload.get(
            "articles",
            [],
        )
        or []
    ):

        url = str(
            item.get(
                "source_url",
                "",
            )
            or ""
        ).strip()

        if not url:
            continue

        value = (
            item.get(
                "hdbscan_cluster"
            )
        )

        result[
            url
        ] = (
            int(
                value
            )
            if value is not None
            else None
        )

    return result


def current_keep_articles(
    *,
    end_date: date,
    days: int,
    expected_ml_version: str,
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

    rows = []

    while (
        current
        <= end_date
    ):

        day = (
            current.isoformat()
        )

        manifest_path = (
            ML_ROOT
            / day
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

        if (
            str(
                manifest.get(
                    "ml_version",
                    "",
                )
                or ""
            )
            != expected_ml_version
        ):

            raise RuntimeError(
                "Taxonomy window contains a different ML version "
                f"on {day}. Expected {expected_ml_version!r}."
            )

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
                "Taxonomy window contains legacy non-two-stage "
                f"ML output on {day}."
            )

        for item in (
            manifest.get(
                "articles",
                [],
            )
            or []
        ):

            if not bool(
                item.get(
                    "operational_keep",
                    False,
                )
            ):

                continue

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
                    analysis_path
                )

            analysis = (
                load_json(
                    analysis_path
                )
            )

            rows.append(
                {
                    "publication_date": (
                        day
                    ),
                    "analysis_path": (
                        analysis_path
                    ),
                    "analysis": (
                        analysis
                    ),
                }
            )

        current += timedelta(
            days=1
        )

    return rows


def validate_topics(
    *,
    ml_config: dict,
    taxonomy_config: dict,
) -> None:

    expected = set(
        (
            ml_config.get(
                "taxonomy",
                {},
            )
            or {}
        )
        .get(
            "supervisory_topics",
            [],
        )
        or []
    )

    actual = set(
        (
            taxonomy_config.get(
                "topics",
                {},
            )
            or {}
        )
        .keys()
    )

    if expected != actual:

        raise RuntimeError(
            "ml.yaml taxonomy labels and taxonomy.yaml rules differ. "
            f"Only in ml.yaml={sorted(expected - actual)}; "
            f"only in taxonomy.yaml={sorted(actual - expected)}"
        )


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Assign transparent provisional supervisory-topic "
            "candidates to Stage-A KEEP articles."
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

    corpus_root = (
        CORPUS_ROOT
        / end_date.isoformat()
        / f"window_{days}d"
    )

    corpus_manifest_path = (
        corpus_root
        / "corpus_manifest.json"
    )

    cluster_path = (
        corpus_root
        / "clusters.json"
    )

    if (
        not corpus_manifest_path.exists()
        or not cluster_path.exists()
    ):

        raise RuntimeError(
            "Phase-3 corpus outputs are missing for this exact window. "
            "Run scripts/run_ml_corpus_analysis.py first."
        )

    corpus_manifest = (
        load_json(
            corpus_manifest_path
        )
    )

    if (
        corpus_manifest.get(
            "status"
        )
        != "SUCCESS"
    ):

        raise RuntimeError(
            "Phase-3 corpus manifest is not SUCCESS."
        )

    if (
        corpus_manifest.get(
            "semantic_corpus_policy"
        )
        != "SIMILARITY_AND_CLUSTERING_USE_STAGE_A_KEEP_ONLY"
    ):

        raise RuntimeError(
            "Phase-3 corpus policy is not the frozen Stage-A KEEP policy."
        )

    ml_config = (
        load_ml_config()
    )

    taxonomy_config = (
        load_taxonomy_config()
    )

    validate_topics(
        ml_config=(
            ml_config
        ),
        taxonomy_config=(
            taxonomy_config
        ),
    )

    engine = (
        SupervisoryTaxonomyEngine(
            config=(
                taxonomy_config
            )
        )
    )

    articles = (
        current_keep_articles(
            end_date=(
                end_date
            ),
            days=(
                days
            ),
            expected_ml_version=str(
                ml_config.get(
                    "version",
                    "",
                )
                or ""
            ),
        )
    )

    if (
        len(
            articles
        )
        != int(
            corpus_manifest.get(
                "stage_a_keep_article_count",
                -1,
            )
        )
    ):

        raise RuntimeError(
            "Stage-A KEEP count does not reconcile with Phase-3 manifest."
        )

    staged_index = (
        build_staged_article_index()
    )

    cluster_map = (
        load_cluster_map(
            cluster_path
        )
    )

    output_root = (
        TAXONOMY_ROOT
        / end_date.isoformat()
        / f"window_{days}d"
    )

    output_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    article_rows = []
    article_json = []

    topic_distribution = (
        Counter()
    )

    primary_distribution = (
        Counter()
    )

    cluster_topics = defaultdict(
        Counter
    )

    cluster_primary = defaultdict(
        Counter
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

        canonical_url = str(
            analysis.get(
                "canonical_url",
                "",
            )
            or ""
        ).strip()

        staged_record = (
            staged_index.get(
                source_url
            )
            or staged_index.get(
                canonical_url
            )
        )

        if not staged_record:

            raise RuntimeError(
                "Could not resolve staged article text for taxonomy: "
                f"{source_url}"
            )

        staged_payload = (
            staged_record[
                "payload"
            ]
        )

        headline = str(
            staged_payload.get(
                "headline",
                "",
            )
            or analysis.get(
                "headline",
                "",
            )
            or ""
        ).strip()

        article_text = str(
            staged_payload.get(
                "article_text",
                "",
            )
            or ""
        ).strip()

        if not article_text:

            raise RuntimeError(
                "Blank staged article text for taxonomy: "
                f"{source_url}"
            )

        result = (
            engine.analyse(
                headline=(
                    headline
                ),
                article_text=(
                    article_text
                ),
            )
        )

        payload = (
            result.to_dict()
        )

        cluster = (
            cluster_map.get(
                source_url
            )
        )

        relevance = (
            (
                analysis.get(
                    "ml",
                    {},
                )
                or {}
            )
            .get(
                "supervisory_relevance",
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

        for topic in (
            result.topic_labels
        ):

            topic_distribution[
                topic
            ] += 1

            cluster_topics[
                cluster
            ][
                topic
            ] += 1

        primary_distribution[
            result.primary_topic
        ] += 1

        cluster_primary[
            cluster
        ][
            result.primary_topic
        ] += 1

        row = {
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
            "hdbscan_cluster": (
                cluster
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
            "primary_topic": (
                result.primary_topic
            ),
            "topic_labels": (
                "; ".join(
                    result.topic_labels
                )
            ),
            "topic_scores_json": (
                json.dumps(
                    result.topic_scores,
                    sort_keys=True,
                )
            ),
            "topic_evidence_json": (
                json.dumps(
                    result.topic_evidence,
                    sort_keys=True,
                    ensure_ascii=False,
                )
            ),
            "taxonomy_version": (
                result.taxonomy_version
            ),
            "provenance_status": (
                result.provenance_status
            ),
            "staged_file": (
                relative_project_path(
                    staged_record[
                        "path"
                    ]
                )
            ),
            "analysis_file": (
                relative_project_path(
                    item[
                        "analysis_path"
                    ]
                )
            ),
        }

        article_rows.append(
            row
        )

        article_json.append(
            {
                **row,
                "topic_labels": list(
                    result.topic_labels
                ),
                "topic_scores": (
                    result.topic_scores
                ),
                "topic_evidence": (
                    result.topic_evidence
                ),
            }
        )

    article_csv_path = (
        output_root
        / "article_taxonomy.csv"
    )

    write_csv(
        article_csv_path,
        article_rows,
        [
            "publication_date",
            "source_name",
            "headline",
            "source_url",
            "hdbscan_cluster",
            "stage_b_label",
            "stage_b_confidence",
            "primary_topic",
            "topic_labels",
            "topic_scores_json",
            "topic_evidence_json",
            "taxonomy_version",
            "provenance_status",
            "staged_file",
            "analysis_file",
        ],
    )

    article_json_path = (
        output_root
        / "article_taxonomy.json"
    )

    article_json_path.write_text(
        json.dumps(
            {
                "status": "SUCCESS",
                "article_count": len(
                    article_json
                ),
                "articles": (
                    article_json
                ),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    cluster_rows = []

    for cluster in sorted(
        cluster_topics,
        key=lambda value: (
            value is None,
            value
            if value is not None
            else 999999,
        ),
    ):

        cluster_rows.append(
            {
                "hdbscan_cluster": (
                    cluster
                ),
                "semantics": (
                    "SEMANTIC_OUTLIER"
                    if cluster == -1
                    else (
                        "NO_CLUSTER_LABEL"
                        if cluster is None
                        else "CLUSTER"
                    )
                ),
                "topic_distribution": dict(
                    cluster_topics[
                        cluster
                    ].most_common()
                ),
                "primary_topic_distribution": dict(
                    cluster_primary[
                        cluster
                    ].most_common()
                ),
            }
        )

    cluster_taxonomy_path = (
        output_root
        / "cluster_taxonomy.json"
    )

    cluster_taxonomy_path.write_text(
        json.dumps(
            {
                "status": "SUCCESS",
                "clusters": (
                    cluster_rows
                ),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    manifest = {
        "status": "SUCCESS",
        "created_at": (
            datetime.now(
                SRI_LANKA_TZ
            )
            .isoformat()
        ),
        "taxonomy_version": (
            engine.version
        ),
        "provenance_status": (
            engine.provenance_status
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
        "input_policy": (
            "STAGE_A_KEEP_ONLY"
        ),
        "article_count": len(
            article_rows
        ),
        "primary_topic_distribution": dict(
            primary_distribution.most_common()
        ),
        "multilabel_topic_distribution": dict(
            topic_distribution.most_common()
        ),
        "other_supervisory_fallback_count": int(
            primary_distribution[
                "OTHER_SUPERVISORY"
            ]
        ),
        "human_gold_labels_created": (
            0
        ),
        "article_taxonomy_csv": (
            relative_project_path(
                article_csv_path
            )
        ),
        "article_taxonomy_json": (
            relative_project_path(
                article_json_path
            )
        ),
        "cluster_taxonomy_json": (
            relative_project_path(
                cluster_taxonomy_path
            )
        ),
    }

    manifest_path = (
        output_root
        / "taxonomy_manifest.json"
    )

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
        "SUPERVISORY TAXONOMY COMPLETE"
    )
    print(
        "=" * 88
    )
    print(
        f"Window start             : {start_date}"
    )
    print(
        f"Window end               : {end_date}"
    )
    print(
        f"Stage-A KEEP articles    : {len(article_rows)}"
    )
    print(
        f"Taxonomy version         : {engine.version}"
    )
    print(
        "Primary topics           : "
        f"{dict(primary_distribution.most_common())}"
    )
    print(
        "Multilabel topics        : "
        f"{dict(topic_distribution.most_common())}"
    )
    print(
        "OTHER_SUPERVISORY fallback: "
        f"{primary_distribution['OTHER_SUPERVISORY']}"
    )
    print(
        "Human gold created       : 0"
    )
    print(
        f"Article CSV              : {article_csv_path}"
    )
    print(
        f"Cluster taxonomy         : {cluster_taxonomy_path}"
    )
    print(
        f"Manifest                 : {manifest_path}"
    )


if __name__ == "__main__":

    main()
