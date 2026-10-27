import numpy as np

from bank_intel.intelligence.clustering import (
    cluster_embeddings,
    top_similarity_pairs,
)


# ============================================================
# SIMILARITY
# ============================================================


def test_similarity_pairs_find_near_duplicate():

    embeddings = np.array(
        [
            [
                1.0,
                0.0,
                0.0,
            ],

            [
                0.99,
                0.01,
                0.0,
            ],

            [
                0.0,
                1.0,
                0.0,
            ],
        ],

        dtype=np.float32,
    )


    pairs = (
        top_similarity_pairs(

            embeddings,

            minimum_similarity=0.95,

            top_n=10,
        )
    )


    assert (
        len(
            pairs
        )
        == 1
    )


    assert (
        pairs[
            0
        ].left_index
        == 0
    )


    assert (
        pairs[
            0
        ].right_index
        == 1
    )


# ============================================================
# SMALL DATASET
# ============================================================


def test_clustering_refuses_tiny_sample():

    embeddings = np.eye(
        5,
        dtype=np.float32,
    )


    result = (
        cluster_embeddings(

            embeddings,

            minimum_articles=12,
        )
    )


    assert (
        result.status
        == "INSUFFICIENT_SAMPLE"
    )


    assert (
        result.hdbscan_cluster_count
        == 0
    )