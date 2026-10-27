from bank_intel.intelligence.review_selection import (
    score_review_candidate,
    select_diverse_batch,
)


TEST_CONFIG = {
    "selection": {
        "weights": {
            "hdbscan_noise": 25,
            "cluster_representative": 35,
            "stage1_watch": 25,
            "stage1_include": 10,
            "pr_relevance_conflict": 30,
            "structural_topic_signal": 10,
            "structural_stage1_exclude": 45,
            "strong_financial_low_pr_signal": 15,
            "story_continuation": 15,
            "shared_event_context": 10,
            "low_sentiment_confidence": 5,
            "primary_regulated_entity": 10,
        },
        "thresholds": {
            "low_sentiment_confidence": 0.60,
            "pr_score_conflict": 40,
            "financial_score_conflict": 35,
            "strong_financial_score": 80,
            "strong_financial_max_pr_score": 39,
        },
        "structural_review_terms": [
            "stake",
            "capital",
            "liquidity",
        ],
    }
}


def test_structural_excluded_outlier_is_high_priority():

    analysis = {
        "headline": (
            "Investor stake in finance company "
            "crosses 10%"
        ),
        "stage1": {
            "gate_decision": (
                "EXCLUDE_NOISE"
            ),
            "financial_relevance_score": 22,
            "pr_noise_score": 0,
        },
        "ml": {
            "finbert": {
                "sentiment_confidence": 0.8,
            },
            "entity_roles": {
                "primary_entities": [],
                "entities": [],
            },
        },
    }

    result = score_review_candidate(
        analysis=analysis,
        source_url="x",
        hdbscan_cluster=-1,
        representative_rank=None,
        config=TEST_CONFIG,
    )

    assert (
        "HDBSCAN_OUTLIER"
        in result.selection_reasons
    )

    assert (
        "STRUCTURAL_TOPIC_EXCLUDED_BY_STAGE1"
        in result.selection_reasons
    )

    assert (
        "STRUCTURAL_TOPIC_SIGNAL"
        in result.selection_reasons
    )

    assert (
        result.priority_score
        >= 80
    )


def test_watch_representative_is_prioritised():

    analysis = {
        "headline": (
            "Bond market activity subdued"
        ),
        "stage1": {
            "gate_decision": "WATCH",
            "financial_relevance_score": 56,
            "pr_noise_score": 0,
        },
        "ml": {
            "finbert": {
                "sentiment_confidence": 0.62,
            },
            "entity_roles": {
                "primary_entities": [],
                "entities": [],
            },
        },
    }

    result = score_review_candidate(
        analysis=analysis,
        source_url="x",
        hdbscan_cluster=1,
        representative_rank=1,
        config=TEST_CONFIG,
    )

    assert (
        "STAGE1_WATCH"
        in result.selection_reasons
    )

    assert (
        "CLUSTER_REPRESENTATIVE_RANK_1"
        in result.selection_reasons
    )

    assert (
        result.priority_score
        >= 60
    )


def test_selector_keeps_diversity():

    candidates = [
        {
            "headline": "A",
            "priority_score": 100,
            "hdbscan_cluster": 0,
            "stage1_gate_decision": "WATCH",
            "source_name": "Daily FT",
            "publication_date": "2026-09-01",
        },
        {
            "headline": "B",
            "priority_score": 99,
            "hdbscan_cluster": 0,
            "stage1_gate_decision": "WATCH",
            "source_name": "Daily FT",
            "publication_date": "2026-09-02",
        },
        {
            "headline": "C",
            "priority_score": 95,
            "hdbscan_cluster": 1,
            "stage1_gate_decision": "EXCLUDE_NOISE",
            "source_name": "Ceylon Today",
            "publication_date": "2026-09-03",
        },
    ]

    selected = select_diverse_batch(
        candidates,
        target=2,
    )

    assert (
        selected[0]["headline"]
        == "A"
    )

    assert (
        selected[1]["headline"]
        == "C"
    )
