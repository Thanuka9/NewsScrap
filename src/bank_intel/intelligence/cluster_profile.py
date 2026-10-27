from __future__ import annotations

from collections import Counter
import re

import numpy as np


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "bank", "banking", "be",
    "by", "for", "from", "in", "into", "is", "it", "its", "lanka",
    "of", "on", "or", "sri", "the", "to", "with", "year", "2026",
}


def _headline_terms(headline: str) -> list[str]:
    tokens = re.findall(
        r"[a-z0-9]+",
        str(headline or "").lower(),
    )
    return [
        token
        for token in tokens
        if len(token) >= 3 and token not in STOPWORDS
    ]


def _counter_dict(values) -> dict:
    return dict(
        Counter(values).most_common()
    )


def _ml(analysis: dict) -> dict:
    return (
        analysis.get(
            "ml",
            {},
        )
        or {}
    )


def _primary_entities(analysis: dict) -> list[str]:
    roles = (
        _ml(analysis)
        .get(
            "entity_roles",
            {},
        )
        or {}
    )
    return [
        str(value)
        for value in (
            roles.get(
                "primary_entities",
                [],
            )
            or []
        )
        if str(value).strip()
    ]


def _all_entities(analysis: dict) -> list[str]:
    ml = _ml(analysis)
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


def _sentiment(analysis: dict) -> str:
    return str(
        (
            _ml(analysis)
            .get(
                "finbert",
                {},
            )
            or {}
        )
        .get(
            "sentiment_label",
            "UNKNOWN",
        )
    )


def _stage1_decision(analysis: dict) -> str:
    return str(
        (
            analysis.get(
                "stage1",
                {},
            )
            or {}
        )
        .get(
            "gate_decision",
            "UNKNOWN",
        )
    )


def _relevance(analysis: dict) -> dict:
    return (
        _ml(analysis)
        .get(
            "supervisory_relevance",
            {},
        )
        or {}
    )


def _stage_a_label(analysis: dict) -> str:
    relevance = _relevance(analysis)
    stage_a = (
        relevance.get(
            "stage_a",
            {},
        )
        or {}
    )
    return str(
        stage_a.get(
            "predicted_label",
            "UNKNOWN",
        )
        or "UNKNOWN"
    )


def _stage_b_label(analysis: dict) -> str:
    relevance = _relevance(analysis)

    if not relevance.get(
        "stage_b_executed",
        False,
    ):
        return "NOT_EXECUTED"

    stage_b = (
        relevance.get(
            "stage_b",
            {},
        )
        or {}
    )
    return str(
        stage_b.get(
            "predicted_label",
            "UNKNOWN",
        )
        or "UNKNOWN"
    )


def _review_priority(analysis: dict) -> str:
    comparison = (
        analysis.get(
            "supervisory_comparison",
            {},
        )
        or {}
    )
    return str(
        comparison.get(
            "overall_review_priority",
            "UNKNOWN",
        )
        or "UNKNOWN"
    )


def build_cluster_profiles(
    *,
    articles: list[dict],
    embeddings: np.ndarray,
    hdbscan_labels: list[int],
    top_terms: int = 12,
    representative_articles: int = 5,
) -> dict:

    matrix = np.asarray(
        embeddings,
        dtype=np.float32,
    )
    labels = np.asarray(
        hdbscan_labels,
        dtype=int,
    )

    if matrix.ndim != 2:
        raise ValueError(
            "embeddings must be a 2D matrix"
        )

    if len(articles) != matrix.shape[0]:
        raise ValueError(
            "articles/embeddings row count mismatch"
        )

    if labels.shape[0] != matrix.shape[0]:
        raise ValueError(
            "HDBSCAN label count does not match article count"
        )

    outlier_count = int(
        np.sum(
            labels == -1
        )
    )

    result = {
        "cluster_count": 0,

        # Preferred semantics.
        "hdbscan_outlier_count": (
            outlier_count
        ),
        "hdbscan_outlier_articles": [],

        # Backward-compatible aliases only.
        # HDBSCAN label -1 means semantic outlier, NOT relevance noise.
        "noise_count": (
            outlier_count
        ),
        "noise_articles": [],

        "hdbscan_label_minus_one_semantics": (
            "SEMANTIC_OUTLIER_NOT_RELEVANCE_NOISE"
        ),

        "clusters": [],
    }

    unique_clusters = sorted(
        {
            int(value)
            for value in labels
            if int(value) >= 0
        }
    )

    result[
        "cluster_count"
    ] = len(
        unique_clusters
    )

    for cluster_id in (
        unique_clusters
    ):

        indexes = np.where(
            labels == cluster_id
        )[0]

        cluster_matrix = (
            matrix[
                indexes
            ]
        )

        centroid = (
            cluster_matrix.mean(
                axis=0
            )
        )

        centroid_norm = float(
            np.linalg.norm(
                centroid
            )
        )

        if centroid_norm > 0:
            centroid = (
                centroid
                / centroid_norm
            )

        row_norms = np.linalg.norm(
            cluster_matrix,
            axis=1,
        )
        row_norms[
            row_norms == 0
        ] = 1.0

        similarities = (
            cluster_matrix
            @ centroid
        ) / row_norms

        ranked_local = np.argsort(
            -similarities
        )

        representatives = []

        for local_index in (
            ranked_local[
                :representative_articles
            ]
        ):

            absolute_index = int(
                indexes[
                    local_index
                ]
            )

            article = (
                articles[
                    absolute_index
                ]
            )

            representatives.append(
                {
                    "centroid_similarity": float(
                        similarities[
                            local_index
                        ]
                    ),
                    "publication_date": (
                        article.get(
                            "publication_date"
                        )
                    ),
                    "source_name": (
                        article.get(
                            "source_name"
                        )
                    ),
                    "headline": (
                        article.get(
                            "headline"
                        )
                    ),
                    "source_url": (
                        article.get(
                            "source_url"
                        )
                    ),
                }
            )

        cluster_articles = [
            articles[
                int(
                    index
                )
            ]
            for index in indexes
        ]

        analyses = [
            item[
                "analysis"
            ]
            for item in (
                cluster_articles
            )
        ]

        terms = Counter()

        for item in (
            cluster_articles
        ):
            terms.update(
                _headline_terms(
                    str(
                        item.get(
                            "headline",
                            "",
                        )
                    )
                )
            )

        primary_entities = []
        all_entities = []

        for analysis in (
            analyses
        ):
            primary_entities.extend(
                _primary_entities(
                    analysis
                )
            )
            all_entities.extend(
                _all_entities(
                    analysis
                )
            )

        result[
            "clusters"
        ].append(
            {
                "cluster_id": (
                    cluster_id
                ),
                "article_count": len(
                    cluster_articles
                ),
                "source_distribution": (
                    _counter_dict(
                        item.get(
                            "source_name",
                            "UNKNOWN",
                        )
                        for item in (
                            cluster_articles
                        )
                    )
                ),
                "stage1_decision_distribution": (
                    _counter_dict(
                        _stage1_decision(
                            analysis
                        )
                        for analysis in (
                            analyses
                        )
                    )
                ),
                "stage_a_distribution": (
                    _counter_dict(
                        _stage_a_label(
                            analysis
                        )
                        for analysis in (
                            analyses
                        )
                    )
                ),
                "stage_b_distribution": (
                    _counter_dict(
                        _stage_b_label(
                            analysis
                        )
                        for analysis in (
                            analyses
                        )
                    )
                ),
                "review_priority_distribution": (
                    _counter_dict(
                        _review_priority(
                            analysis
                        )
                        for analysis in (
                            analyses
                        )
                    )
                ),
                "finbert_sentiment_distribution": (
                    _counter_dict(
                        _sentiment(
                            analysis
                        )
                        for analysis in (
                            analyses
                        )
                    )
                ),
                "primary_entities": (
                    _counter_dict(
                        primary_entities
                    )
                ),
                "all_entities": (
                    _counter_dict(
                        all_entities
                    )
                ),
                "top_headline_terms": [
                    {
                        "term": term,
                        "count": count,
                    }
                    for (
                        term,
                        count,
                    ) in terms.most_common(
                        top_terms
                    )
                ],
                "representative_articles": (
                    representatives
                ),
            }
        )

    for index in np.where(
        labels == -1
    )[0]:

        item = (
            articles[
                int(
                    index
                )
            ]
        )

        analysis = (
            item[
                "analysis"
            ]
        )

        outlier = {
            "publication_date": (
                item.get(
                    "publication_date"
                )
            ),
            "source_name": (
                item.get(
                    "source_name"
                )
            ),
            "headline": (
                item.get(
                    "headline"
                )
            ),
            "source_url": (
                item.get(
                    "source_url"
                )
            ),
            "stage1_gate_decision": (
                _stage1_decision(
                    analysis
                )
            ),
            "stage_a_label": (
                _stage_a_label(
                    analysis
                )
            ),
            "stage_b_label": (
                _stage_b_label(
                    analysis
                )
            ),
            "review_priority": (
                _review_priority(
                    analysis
                )
            ),
        }

        result[
            "hdbscan_outlier_articles"
        ].append(
            outlier
        )

        # Backward-compatible alias.
        result[
            "noise_articles"
        ].append(
            dict(
                outlier
            )
        )

    return result
