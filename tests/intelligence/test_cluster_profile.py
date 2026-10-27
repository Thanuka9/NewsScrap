import numpy as np

from bank_intel.intelligence.cluster_profile import build_cluster_profiles


def test_cluster_profile_keeps_noise_and_representatives():
    articles = [
        {
            "publication_date": "2026-09-01",
            "source_name": "A",
            "headline": "Bank wins major award",
            "source_url": "a",
            "analysis": {
                "stage1": {"gate_decision": "EXCLUDE_NOISE"},
                "ml": {
                    "finbert": {"sentiment_label": "positive"},
                    "entity_roles": {
                        "primary_entities": ["Bank A"],
                        "entities": [{"canonical_name": "Bank A"}],
                    },
                },
            },
        },
        {
            "publication_date": "2026-09-02",
            "source_name": "A",
            "headline": "Bank receives another award",
            "source_url": "b",
            "analysis": {
                "stage1": {"gate_decision": "EXCLUDE_NOISE"},
                "ml": {
                    "finbert": {"sentiment_label": "positive"},
                    "entity_roles": {
                        "primary_entities": ["Bank B"],
                        "entities": [{"canonical_name": "Bank B"}],
                    },
                },
            },
        },
        {
            "publication_date": "2026-09-03",
            "source_name": "B",
            "headline": "Weather warning",
            "source_url": "c",
            "analysis": {
                "stage1": {"gate_decision": "EXCLUDE_NOISE"},
                "ml": {
                    "finbert": {"sentiment_label": "negative"},
                    "entity_roles": {
                        "primary_entities": [],
                        "entities": [],
                    },
                },
            },
        },
    ]

    embeddings = np.array(
        [[1.0, 0.0], [0.99, 0.01], [0.0, 1.0]],
        dtype=np.float32,
    )

    result = build_cluster_profiles(
        articles=articles,
        embeddings=embeddings,
        hdbscan_labels=[0, 0, -1],
    )

    assert result["cluster_count"] == 1
    assert result["noise_count"] == 1
    assert result["clusters"][0]["article_count"] == 2
    assert len(result["clusters"][0]["representative_articles"]) == 2
    assert result["noise_articles"][0]["headline"] == "Weather warning"
