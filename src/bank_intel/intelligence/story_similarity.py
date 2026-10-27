from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import re


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "by",
    "for", "from", "has", "have", "in", "into", "is", "it",
    "its", "of", "on", "or", "that", "the", "their", "this",
    "to", "was", "were", "with",
}


@dataclass(frozen=True)
class StoryPairAssessment:
    pair_type: str
    cosine_similarity: float
    date_gap_days: int | None
    headline_jaccard: float
    shared_primary_entities: tuple[str, ...]
    shared_entities: tuple[str, ...]
    evidence: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def headline_tokens(
    headline: str,
) -> set[str]:

    tokens = re.findall(
        r"[a-z0-9]+",
        str(
            headline
            or ""
        ).lower(),
    )

    return {
        token
        for token in tokens
        if (
            len(token) >= 3
            and token not in STOPWORDS
        )
    }


def headline_jaccard(
    left: str,
    right: str,
) -> float:

    left_tokens = headline_tokens(
        left
    )

    right_tokens = headline_tokens(
        right
    )

    union = (
        left_tokens
        | right_tokens
    )

    if not union:
        return 0.0

    return (
        len(
            left_tokens
            & right_tokens
        )
        / len(
            union
        )
    )


def _safe_iso_date(
    value: str | None,
) -> date | None:

    if not value:
        return None

    try:

        return date.fromisoformat(
            str(
                value
            )[:10]
        )

    except ValueError:

        return None


def date_gap_days(
    left: str | None,
    right: str | None,
) -> int | None:

    left_date = (
        _safe_iso_date(
            left
        )
    )

    right_date = (
        _safe_iso_date(
            right
        )
    )

    if (
        left_date is None
        or right_date is None
    ):

        return None

    return abs(
        (
            left_date
            - right_date
        ).days
    )


def entity_sets(
    analysis: dict,
) -> tuple[
    set[str],
    set[str],
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

    all_entities = {
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
    }

    if not all_entities:

        all_entities = {
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
        }

    return (
        primary,
        all_entities,
    )


def assess_story_pair(
    *,
    left_analysis: dict,
    right_analysis: dict,
    cosine_similarity: float,
    related_min_cosine: float = 0.88,
    same_event_min_cosine: float = 0.94,
    near_duplicate_min_cosine: float = 0.96,
    max_event_date_gap_days: int = 3,
    same_event_min_headline_jaccard: float = 0.30,
    near_duplicate_min_headline_jaccard: float = 0.55,
) -> StoryPairAssessment:
    """
    Convert raw FinBERT semantic similarity into a review-oriented
    relationship candidate.

    The pair types are deliberately descriptive:

    NEAR_DUPLICATE_CANDIDATE
        Very high semantic similarity plus strong headline overlap.
        This is the closest candidate to duplicate coverage.

    SAME_EVENT_CANDIDATE
        Cross-source or same-day coverage with strong evidence that
        both articles describe the same underlying event.

    STORY_CONTINUATION
        Same primary entity / institution or highly related market
        subject on different publication dates. This preserves evolving
        market stories without falsely deduplicating them.

    SHARED_EVENT_CONTEXT
        Strong headline/event wording overlap, close dates, but different
        primary subjects. Example: two institutions receiving recognition
        from the same ranking or awards programme.

    RELATED_TOPIC
        Broad finance-domain relationship only.

    BELOW_THRESHOLD
        Not retained.
    """

    cosine = float(
        cosine_similarity
    )

    left_date = (
        left_analysis.get(
            "publication_date"
        )
        or left_analysis.get(
            "published_at"
        )
    )

    right_date = (
        right_analysis.get(
            "publication_date"
        )
        or right_analysis.get(
            "published_at"
        )
    )

    gap = date_gap_days(
        left_date,
        right_date,
    )

    left_source = str(
        left_analysis.get(
            "source_name",
            "",
        )
        or ""
    ).strip()

    right_source = str(
        right_analysis.get(
            "source_name",
            "",
        )
        or ""
    ).strip()

    same_source = (
        bool(
            left_source
        )
        and left_source
        == right_source
    )

    same_day = (
        gap == 0
    )

    jaccard = (
        headline_jaccard(
            str(
                left_analysis.get(
                    "headline",
                    "",
                )
            ),
            str(
                right_analysis.get(
                    "headline",
                    "",
                )
            ),
        )
    )

    (
        left_primary,
        left_all,
    ) = entity_sets(
        left_analysis
    )

    (
        right_primary,
        right_all,
    ) = entity_sets(
        right_analysis
    )

    shared_primary = tuple(
        sorted(
            left_primary
            & right_primary
        )
    )

    shared_entities = tuple(
        sorted(
            left_all
            & right_all
        )
    )

    evidence: list[
        str
    ] = []

    if shared_primary:

        evidence.append(
            "SHARED_PRIMARY_ENTITY"
        )

    elif shared_entities:

        evidence.append(
            "SHARED_ENTITY"
        )

    if (
        jaccard
        >= near_duplicate_min_headline_jaccard
    ):

        evidence.append(
            "STRONG_HEADLINE_OVERLAP"
        )

    elif (
        jaccard
        >= same_event_min_headline_jaccard
    ):

        evidence.append(
            "HEADLINE_OVERLAP"
        )

    close_in_time = (
        gap is not None
        and gap
        <= max_event_date_gap_days
    )

    if close_in_time:

        evidence.append(
            "CLOSE_PUBLICATION_DATES"
        )

    if same_source:

        evidence.append(
            "SAME_SOURCE"
        )

    else:

        evidence.append(
            "CROSS_SOURCE"
        )

    if same_day:

        evidence.append(
            "SAME_PUBLICATION_DATE"
        )

    pair_type = (
        "BELOW_THRESHOLD"
    )

    # ========================================================
    # NEAR DUPLICATE
    # ========================================================

    if (
        cosine
        >= near_duplicate_min_cosine
        and close_in_time
        and jaccard
        >= near_duplicate_min_headline_jaccard
        and (
            bool(
                shared_primary
            )
            or bool(
                shared_entities
            )
            or not same_source
        )
    ):

        pair_type = (
            "NEAR_DUPLICATE_CANDIDATE"
        )


    # ========================================================
    # SAME EVENT
    #
    # We avoid calling different-day same-source market recaps
    # the same event. Those become STORY_CONTINUATION instead.
    # ========================================================

    elif (
        cosine
        >= same_event_min_cosine
        and close_in_time
        and (
            not same_source
            or same_day
        )
        and (
            bool(
                shared_primary
            )
            or (
                bool(
                    shared_entities
                )
                and jaccard
                >= same_event_min_headline_jaccard
            )
            or jaccard
            >= near_duplicate_min_headline_jaccard
        )
    ):

        pair_type = (
            "SAME_EVENT_CANDIDATE"
        )


    # ========================================================
    # STORY CONTINUATION
    # ========================================================

    elif (
        cosine
        >= same_event_min_cosine
        and close_in_time
        and same_source
        and not same_day
        and (
            bool(
                shared_primary
            )
            or bool(
                shared_entities
            )
        )
    ):

        pair_type = (
            "STORY_CONTINUATION"
        )


    # ========================================================
    # SHARED EVENT CONTEXT
    #
    # Strong event/ranking wording but different institutions.
    # ========================================================

    elif (
        cosine
        >= same_event_min_cosine
        and close_in_time
        and jaccard
        >= same_event_min_headline_jaccard
        and not shared_primary
        and not shared_entities
    ):

        pair_type = (
            "SHARED_EVENT_CONTEXT"
        )


    # ========================================================
    # BROAD RELATED TOPIC
    # ========================================================

    elif (
        cosine
        >= related_min_cosine
    ):

        pair_type = (
            "RELATED_TOPIC"
        )

    return (
        StoryPairAssessment(

            pair_type=(
                pair_type
            ),

            cosine_similarity=(
                cosine
            ),

            date_gap_days=(
                gap
            ),

            headline_jaccard=float(
                jaccard
            ),

            shared_primary_entities=(
                shared_primary
            ),

            shared_entities=(
                shared_entities
            ),

            evidence=tuple(
                evidence
            ),
        )
    )
