from bank_intel.intelligence.story_similarity import (
    assess_story_pair,
)


def article(
    headline,
    publication_date,
    primary=None,
    entities=None,
    source="Daily FT",
):

    return {
        "headline": (
            headline
        ),

        "publication_date": (
            publication_date
        ),

        "source_name": (
            source
        ),

        "ml": {
            "entity_roles": {
                "primary_entities": (
                    primary
                    or []
                ),

                "entities": [
                    {
                        "canonical_name": (
                            name
                        )
                    }
                    for name in (
                        entities
                        or primary
                        or []
                    )
                ],
            }
        },
    }


def test_different_bank_awards_are_shared_event_context():

    left = article(
        "Vallibel Finance among Sri Lanka's Top 100 Most Valuable Brands",
        "2026-09-07",
    )

    right = article(
        "HNB once again ranked among Sri Lanka's Most Valuable Brands for 2026",
        "2026-09-08",
    )

    result = assess_story_pair(
        left_analysis=(
            left
        ),
        right_analysis=(
            right
        ),
        cosine_similarity=(
            0.963322
        ),
    )

    assert (
        result.pair_type
        == "SHARED_EVENT_CONTEXT"
    )


def test_daily_bond_articles_are_story_continuation():

    left = article(
        "Bond market yields continue uptrend",
        "2026-09-08",
        entities=[
            "Central Bank of Sri Lanka"
        ],
    )

    right = article(
        "Bond yields move up on selected durations; activity remains muted",
        "2026-09-09",
        entities=[
            "Central Bank of Sri Lanka"
        ],
    )

    result = assess_story_pair(
        left_analysis=(
            left
        ),
        right_analysis=(
            right
        ),
        cosine_similarity=(
            0.958505
        ),
    )

    assert (
        result.pair_type
        == "STORY_CONTINUATION"
    )


def test_cse_articles_are_story_continuation():

    left = article(
        "CSE ends down 0.38% on heightened selling pressure",
        "2026-09-09",
        primary=[
            "Colombo Stock Exchange"
        ],
    )

    right = article(
        "CSE extends losses over geopolitical concerns",
        "2026-09-11",
        primary=[
            "Colombo Stock Exchange"
        ],
    )

    result = assess_story_pair(
        left_analysis=(
            left
        ),
        right_analysis=(
            right
        ),
        cosine_similarity=(
            0.950714
        ),
    )

    assert (
        result.pair_type
        == "STORY_CONTINUATION"
    )


def test_cross_source_same_entity_can_be_same_event():

    left = article(
        "HNB announces capital raising programme",
        "2026-09-10",
        primary=[
            "Hatton National Bank PLC"
        ],
        source="Daily FT",
    )

    right = article(
        "HNB unveils new capital raising programme",
        "2026-09-10",
        primary=[
            "Hatton National Bank PLC"
        ],
        source="Daily Mirror",
    )

    result = assess_story_pair(
        left_analysis=(
            left
        ),
        right_analysis=(
            right
        ),
        cosine_similarity=(
            0.97
        ),
    )

    assert (
        result.pair_type
        in {
            "SAME_EVENT_CANDIDATE",
            "NEAR_DUPLICATE_CANDIDATE",
        }
    )
