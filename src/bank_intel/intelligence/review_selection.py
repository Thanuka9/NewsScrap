from __future__ import annotations

from dataclasses import (
    asdict,
    dataclass,
)
from pathlib import Path
import re
from typing import Iterable

import yaml


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[3]
)

DEFAULT_REVIEW_CONFIG = (
    PROJECT_ROOT
    / "config"
    / "intelligence"
    / "review.yaml"
)


def load_review_config(
    path: Path | str = DEFAULT_REVIEW_CONFIG,
) -> dict:

    payload = yaml.safe_load(
        Path(path).read_text(
            encoding="utf-8-sig"
        )
    )

    if not isinstance(
        payload,
        dict,
    ):
        raise RuntimeError(
            f"Invalid review configuration: {path}"
        )

    return payload


@dataclass(
    frozen=True
)
class ReviewSelection:

    priority_score: float
    selection_reasons: tuple[
        str,
        ...
    ]
    hdbscan_cluster: (
        int
        | None
    )

    # Preferred terminology.
    is_hdbscan_outlier: bool

    # Backward-compatible alias. In this project HDBSCAN label -1
    # means semantic outlier, NOT supervisory/relevance NOISE.
    is_hdbscan_noise: bool

    cluster_representative_rank: (
        int
        | None
    )

    def to_dict(
        self,
    ) -> dict:

        return asdict(
            self
        )


def _safe_float(
    value,
    default: float = 0.0,
) -> float:

    try:
        return float(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        return default


def _headline_contains_term(
    headline: str,
    term: str,
) -> bool:

    headline = str(
        headline
        or ""
    ).lower()

    term = str(
        term
        or ""
    ).lower().strip()

    if not term:
        return False

    if " " in term:
        return term in headline

    return bool(
        re.search(
            rf"\b{re.escape(term)}\b",
            headline,
        )
    )


def _stage1(
    analysis: dict,
) -> dict:

    return (
        analysis.get(
            "stage1",
            {},
        )
        or {}
    )


def _ml(
    analysis: dict,
) -> dict:

    return (
        analysis.get(
            "ml",
            {},
        )
        or {}
    )


def _finbert(
    analysis: dict,
) -> dict:

    return (
        _ml(
            analysis
        )
        .get(
            "finbert",
            {},
        )
        or {}
    )


def _entity_roles(
    analysis: dict,
) -> dict:

    return (
        _ml(
            analysis
        )
        .get(
            "entity_roles",
            {},
        )
        or {}
    )


def _relevance(
    analysis: dict,
) -> dict:

    return (
        _ml(
            analysis
        )
        .get(
            "supervisory_relevance",
            {},
        )
        or {}
    )


def _stage_a(
    analysis: dict,
) -> dict:

    return (
        _relevance(
            analysis
        )
        .get(
            "stage_a",
            {},
        )
        or {}
    )


def _stage_b(
    analysis: dict,
) -> dict:

    return (
        _relevance(
            analysis
        )
        .get(
            "stage_b",
            {},
        )
        or {}
    )


def _comparison(
    analysis: dict,
) -> dict:

    return (
        analysis.get(
            "supervisory_comparison",
            {},
        )
        or {}
    )


def _has_primary_regulated_entity(
    analysis: dict,
) -> bool:

    roles = (
        _entity_roles(
            analysis
        )
    )

    primary = {
        str(
            value
        )
        for value in (
            roles.get(
                "primary_entities",
                [],
            )
            or []
        )
        if str(
            value
        ).strip()
    }

    if not primary:
        return False

    for entity in (
        roles.get(
            "entities",
            [],
        )
        or []
    ):

        name = str(
            entity.get(
                "canonical_name",
                "",
            )
        ).strip()

        if name not in primary:
            continue

        if (
            entity.get(
                "institution_type"
            )
            in {
                "LICENSED_COMMERCIAL_BANK",
                "LICENSED_SPECIALISED_BANK",
                "LICENSED_FINANCE_COMPANY",
                "SPECIALISED_LEASING_COMPANY",
                "REGULATOR",
                "REGULATOR_UNIT",
            }
        ):

            return True

    return False


def build_cluster_maps(
    cluster_payload: dict,
    profile_payload: dict,
) -> tuple[
    dict[
        str,
        int | None,
    ],
    dict[
        str,
        int,
    ],
]:
    """
    Return:
        source_url -> HDBSCAN cluster
        source_url -> representative rank

    None means clustering was not available for that article.
    -1 means HDBSCAN explicitly labelled it a semantic outlier.
    """

    clusters: dict[
        str,
        int | None,
    ] = {}

    for item in (
        cluster_payload.get(
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

        clusters[
            url
        ] = (
            int(
                value
            )
            if value is not None
            else None
        )

    representatives: dict[
        str,
        int,
    ] = {}

    for cluster in (
        profile_payload.get(
            "clusters",
            [],
        )
        or []
    ):

        for (
            rank,
            item,
        ) in enumerate(
            (
                cluster.get(
                    "representative_articles",
                    [],
                )
                or []
            ),
            start=1,
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

            previous = (
                representatives.get(
                    url
                )
            )

            if (
                previous is None
                or rank < previous
            ):

                representatives[
                    url
                ] = (
                    rank
                )

    return (
        clusters,
        representatives,
    )


def build_pair_reason_map(
    rows: Iterable[
        dict
    ],
    reason: str,
) -> dict[
    str,
    set[
        str
    ],
]:

    result: dict[
        str,
        set[
            str
        ],
    ] = {}

    for row in (
        rows
    ):

        for key in (
            "left_url",
            "right_url",
        ):

            url = str(
                row.get(
                    key,
                    "",
                )
                or ""
            ).strip()

            if not url:
                continue

            result.setdefault(
                url,
                set(),
            ).add(
                reason
            )

    return result


def merge_reason_maps(
    *maps: dict[
        str,
        set[
            str
        ],
    ],
) -> dict[
    str,
    set[
        str
    ],
]:

    merged: dict[
        str,
        set[
            str
        ],
    ] = {}

    for mapping in (
        maps
    ):

        for (
            url,
            values,
        ) in mapping.items():

            merged.setdefault(
                url,
                set(),
            ).update(
                values
            )

    return merged


def score_review_candidate(
    *,
    analysis: dict,
    source_url: str,
    hdbscan_cluster: (
        int
        | None
    ),
    representative_rank: (
        int
        | None
    ),
    pair_reasons: (
        set[
            str
        ]
        | None
    ) = None,
    config: (
        dict
        | None
    ) = None,
) -> ReviewSelection:

    # Kept in the interface for stable call sites / audit exports.
    _ = source_url

    config = (
        config
        or load_review_config()
    )

    selection = (
        config.get(
            "selection",
            {},
        )
        or {}
    )

    weights = (
        selection.get(
            "weights",
            {},
        )
        or {}
    )

    thresholds = (
        selection.get(
            "thresholds",
            {},
        )
        or {}
    )

    structural_terms = (
        selection.get(
            "structural_review_terms",
            [],
        )
        or []
    )

    reasons = set(
        pair_reasons
        or set()
    )

    score = 0.0

    # ========================================================
    # CORPUS STRUCTURE
    # ========================================================

    is_outlier = (
        hdbscan_cluster
        == -1
    )

    if is_outlier:

        score += _safe_float(
            weights.get(
                "hdbscan_outlier",
                weights.get(
                    "hdbscan_noise",
                    50,
                ),
            )
        )

        reasons.add(
            "HDBSCAN_OUTLIER"
        )

    if (
        representative_rank
        is not None
    ):

        base = _safe_float(
            weights.get(
                "cluster_representative",
                35,
            )
        )

        factor = max(
            0.5,
            1.0
            - (
                0.10
                * (
                    representative_rank
                    - 1
                )
            ),
        )

        score += (
            base
            * factor
        )

        reasons.add(
            f"CLUSTER_REPRESENTATIVE_RANK_{representative_rank}"
        )

    # ========================================================
    # STAGE-1 RULE SIGNALS
    # ========================================================

    stage1 = (
        _stage1(
            analysis
        )
    )

    decision = str(
        stage1.get(
            "gate_decision",
            "",
        )
        or ""
    ).upper()

    financial_score = _safe_float(
        stage1.get(
            "financial_relevance_score"
        )
    )

    pr_score = _safe_float(
        stage1.get(
            "pr_noise_score"
        )
    )

    if decision == "WATCH":

        score += _safe_float(
            weights.get(
                "stage1_watch",
                25,
            )
        )

        reasons.add(
            "STAGE1_WATCH"
        )

    elif decision == "INCLUDE_CANDIDATE":

        score += _safe_float(
            weights.get(
                "stage1_include",
                10,
            )
        )

        reasons.add(
            "STAGE1_INCLUDE_CANDIDATE"
        )

    if (
        pr_score
        >= _safe_float(
            thresholds.get(
                "pr_score_conflict",
                40,
            )
        )
        and financial_score
        >= _safe_float(
            thresholds.get(
                "financial_score_conflict",
                35,
            )
        )
    ):

        score += _safe_float(
            weights.get(
                "pr_relevance_conflict",
                30,
            )
        )

        reasons.add(
            "PR_RELEVANCE_CONFLICT"
        )

    headline = str(
        analysis.get(
            "headline",
            "",
        )
        or ""
    )

    structural_match = any(
        _headline_contains_term(
            headline,
            term,
        )
        for term in (
            structural_terms
        )
    )

    if structural_match:

        score += _safe_float(
            weights.get(
                "structural_topic_signal",
                10,
            )
        )

        reasons.add(
            "STRUCTURAL_TOPIC_SIGNAL"
        )

    if (
        decision
        == "EXCLUDE_NOISE"
        and structural_match
    ):

        score += _safe_float(
            weights.get(
                "structural_stage1_exclude",
                45,
            )
        )

        reasons.add(
            "STRUCTURAL_TOPIC_EXCLUDED_BY_STAGE1"
        )

    if (
        financial_score
        >= _safe_float(
            thresholds.get(
                "strong_financial_score",
                80,
            )
        )
        and pr_score
        <= _safe_float(
            thresholds.get(
                "strong_financial_max_pr_score",
                39,
            )
        )
    ):

        score += _safe_float(
            weights.get(
                "strong_financial_low_pr_signal",
                15,
            )
        )

        reasons.add(
            "STRONG_FINANCIAL_LOW_PR_SIGNAL"
        )

    # ========================================================
    # FROZEN TWO-STAGE RELEVANCE
    # ========================================================

    relevance = (
        _relevance(
            analysis
        )
    )

    stage_a = (
        _stage_a(
            analysis
        )
    )

    stage_b = (
        _stage_b(
            analysis
        )
    )

    comparison = (
        _comparison(
            analysis
        )
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

    stage_a_label = str(
        stage_a.get(
            "predicted_label",
            "",
        )
        or ""
    ).upper()

    stage_a_confidence = _safe_float(
        stage_a.get(
            "confidence",
            1.0,
        ),
        default=1.0,
    )

    if (
        stage_a_comparison.get(
            "comparison_status"
        )
        == "DISAGREE"
    ):

        score += _safe_float(
            weights.get(
                "stage_a_disagreement",
                60,
            )
        )

        reasons.add(
            "STAGE1_STAGE_A_DISAGREEMENT"
        )

    if (
        stage_a_label
        == "NOISE"
        and decision in {
            "WATCH",
            "INCLUDE_CANDIDATE",
        }
    ):

        score += _safe_float(
            weights.get(
                "stage_a_noise_stage1_keep",
                20,
            )
        )

        reasons.add(
            "STAGE_A_NOISE_STAGE1_KEEP_CANDIDATE"
        )

    if (
        stage_a
        and stage_a_confidence
        < _safe_float(
            thresholds.get(
                "stage_a_low_confidence",
                0.65,
            )
        )
    ):

        score += _safe_float(
            weights.get(
                "stage_a_low_confidence",
                15,
            )
        )

        reasons.add(
            "STAGE_A_LOW_CONFIDENCE"
        )

    stage_b_executed = bool(
        relevance.get(
            "stage_b_executed",
            False,
        )
    )

    if stage_b_executed:

        stage_b_label = str(
            stage_b.get(
                "predicted_label",
                "",
            )
            or ""
        ).upper()

        stage_b_confidence = _safe_float(
            stage_b.get(
                "confidence",
                1.0,
            ),
            default=1.0,
        )

        if (
            stage_b_label
            == "RELEVANT"
        ):

            score += _safe_float(
                weights.get(
                    "stage_b_relevant_auxiliary",
                    15,
                )
            )

            reasons.add(
                "STAGE_B_RELEVANT_AUXILIARY"
            )

        if (
            stage_b_comparison.get(
                "comparison_status"
            )
            == "DISAGREE"
        ):

            score += _safe_float(
                weights.get(
                    "stage_b_disagreement_auxiliary",
                    10,
                )
            )

            reasons.add(
                "STAGE1_STAGE_B_DISAGREEMENT_AUXILIARY"
            )

        if (
            stage_b_confidence
            < _safe_float(
                thresholds.get(
                    "stage_b_low_confidence",
                    0.60,
                )
            )
        ):

            score += _safe_float(
                weights.get(
                    "stage_b_low_confidence",
                    10,
                )
            )

            reasons.add(
                "STAGE_B_LOW_CONFIDENCE_AUXILIARY"
            )

    # ========================================================
    # BASE FINBERT / ENTITY SIGNALS
    # ========================================================

    finbert = (
        _finbert(
            analysis
        )
    )

    sentiment_confidence = _safe_float(
        finbert.get(
            "sentiment_confidence",
            1.0,
        ),
        default=1.0,
    )

    if (
        sentiment_confidence
        < _safe_float(
            thresholds.get(
                "low_sentiment_confidence",
                0.60,
            )
        )
    ):

        score += _safe_float(
            weights.get(
                "low_sentiment_confidence",
                5,
            )
        )

        reasons.add(
            "LOW_FINBERT_SENTIMENT_CONFIDENCE"
        )

    if (
        _has_primary_regulated_entity(
            analysis
        )
    ):

        score += _safe_float(
            weights.get(
                "primary_regulated_entity",
                10,
            )
        )

        reasons.add(
            "PRIMARY_REGULATED_ENTITY"
        )

    # ========================================================
    # STORY / EVENT SIGNALS
    # ========================================================

    if (
        "STORY_CONTINUATION"
        in reasons
    ):

        score += _safe_float(
            weights.get(
                "story_continuation",
                15,
            )
        )

    if (
        "SHARED_EVENT_CONTEXT"
        in reasons
    ):

        score += _safe_float(
            weights.get(
                "shared_event_context",
                10,
            )
        )

    if (
        "SAME_EVENT_CANDIDATE"
        in reasons
    ):

        score += _safe_float(
            weights.get(
                "same_event_candidate",
                5,
            )
        )

    return (
        ReviewSelection(
            priority_score=float(
                score
            ),
            selection_reasons=tuple(
                sorted(
                    reasons
                )
            ),
            hdbscan_cluster=(
                hdbscan_cluster
            ),
            is_hdbscan_outlier=(
                is_outlier
            ),
            is_hdbscan_noise=(
                is_outlier
            ),
            cluster_representative_rank=(
                representative_rank
            ),
        )
    )


def select_diverse_batch(
    candidates: list[
        dict
    ],
    *,
    target: int,
) -> list[
    dict
]:
    """
    Transparent diversity-aware ranking.

    Diversity dimensions:
      - HDBSCAN cluster/outlier status
      - Stage-1 decision
      - Stage-A operational decision
      - source
    """

    if (
        target <= 0
        or target
        >= len(
            candidates
        )
    ):

        return sorted(
            candidates,
            key=lambda item: (
                -float(
                    item.get(
                        "priority_score",
                        0.0,
                    )
                ),
                str(
                    item.get(
                        "publication_date",
                        "",
                    )
                ),
                str(
                    item.get(
                        "headline",
                        "",
                    )
                ),
            ),
        )

    remaining = list(
        candidates
    )

    selected = []

    seen_clusters = set()
    seen_stage1 = set()
    seen_stage_a = set()
    seen_sources = set()

    while (
        remaining
        and len(
            selected
        )
        < target
    ):

        def key(
            item: dict,
        ) -> float:

            diversity_bonus = 0.0

            cluster = (
                item.get(
                    "hdbscan_cluster"
                )
            )

            stage1_decision = str(
                item.get(
                    "stage1_gate_decision",
                    "",
                )
            )

            stage_a_label = str(
                item.get(
                    "stage_a_label",
                    "",
                )
            )

            source = str(
                item.get(
                    "source_name",
                    "",
                )
            )

            if (
                cluster
                not in seen_clusters
            ):
                diversity_bonus += 12

            if (
                stage1_decision
                not in seen_stage1
            ):
                diversity_bonus += 8

            if (
                stage_a_label
                not in seen_stage_a
            ):
                diversity_bonus += 6

            if (
                source
                not in seen_sources
            ):
                diversity_bonus += 5

            return (
                float(
                    item.get(
                        "priority_score",
                        0.0,
                    )
                )
                + diversity_bonus
            )

        best = max(
            remaining,
            key=key,
        )

        remaining.remove(
            best
        )

        selected.append(
            best
        )

        seen_clusters.add(
            best.get(
                "hdbscan_cluster"
            )
        )

        seen_stage1.add(
            str(
                best.get(
                    "stage1_gate_decision",
                    "",
                )
            )
        )

        seen_stage_a.add(
            str(
                best.get(
                    "stage_a_label",
                    "",
                )
            )
        )

        seen_sources.add(
            str(
                best.get(
                    "source_name",
                    "",
                )
            )
        )

    return selected
