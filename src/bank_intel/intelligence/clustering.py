from __future__ import annotations

from dataclasses import (
    asdict,
    dataclass,
)

import numpy as np


# ============================================================
# SIMILARITY RESULT
# ============================================================


@dataclass(
    frozen=True
)
class SimilarityPair:

    left_index: int

    right_index: int

    cosine_similarity: float

    def to_dict(
        self,
    ) -> dict:

        return asdict(
            self
        )


# ============================================================
# CLUSTER RESULT
# ============================================================


@dataclass(
    frozen=True
)
class ClusteringResult:

    status: str

    hdbscan_labels: list[
        int
    ]

    hdbscan_cluster_count: int

    hdbscan_noise_count: int

    kmeans_labels: list[
        int
    ]

    kmeans_k: int | None

    kmeans_silhouette: float | None

    pca_components: int | None

    def to_dict(
        self,
    ) -> dict:

        return asdict(
            self
        )


# ============================================================
# NORMALISATION
# ============================================================


def _normalise_rows(
    matrix: np.ndarray,
) -> np.ndarray:

    matrix = np.asarray(
        matrix,
        dtype=np.float32,
    )

    norms = np.linalg.norm(
        matrix,
        axis=1,
        keepdims=True,
    )

    norms[
        norms == 0
    ] = 1.0

    return (
        matrix
        / norms
    ).astype(
        np.float32,
        copy=False,
    )


# ============================================================
# COSINE SIMILARITY MATRIX
# ============================================================


def cosine_similarity_matrix(
    embeddings: np.ndarray,
) -> np.ndarray:

    normalised = (
        _normalise_rows(
            embeddings
        )
    )

    return (
        normalised
        @ normalised.T
    )


# ============================================================
# TOP STORY SIMILARITIES
# ============================================================


def top_similarity_pairs(
    embeddings: np.ndarray,
    *,
    minimum_similarity: float = 0.82,
    top_n: int = 100,
) -> list[
    SimilarityPair
]:

    if len(
        embeddings
    ) < 2:

        return []

    similarities = (
        cosine_similarity_matrix(
            embeddings
        )
    )

    pairs = []

    for left in range(
        similarities.shape[0]
    ):

        for right in range(
            left + 1,
            similarities.shape[1],
        ):

            score = float(
                similarities[
                    left,
                    right,
                ]
            )

            if (
                score
                < minimum_similarity
            ):

                continue

            pairs.append(
                SimilarityPair(

                    left_index=(
                        left
                    ),

                    right_index=(
                        right
                    ),

                    cosine_similarity=(
                        score
                    ),
                )
            )

    pairs.sort(
        key=lambda item: (
            item.cosine_similarity
        ),
        reverse=True,
    )

    return (
        pairs[
            :top_n
        ]
    )


# ============================================================
# CLUSTERING
# ============================================================


def cluster_embeddings(
    embeddings: np.ndarray,
    *,
    minimum_articles: int = 12,
    pca_max_components: int = 50,
    hdbscan_min_cluster_size: int = 3,
    hdbscan_min_samples: int | None = None,
    hdbscan_cluster_selection_method: str = "eom",
    kmeans_enabled: bool = True,
    kmeans_min_k: int = 2,
    kmeans_max_k: int = 12,
    random_state: int = 42,
) -> ClusteringResult:
    """
    Discover natural semantic groups.

    Main clustering:
        HDBSCAN

    Diagnostic comparison:
        K-Means

    We deliberately do not force clustering when the
    sample is too small.
    """

    embeddings = (
        _normalise_rows(
            embeddings
        )
    )

    n_samples = int(
        embeddings
        .shape[0]
    )


    # ========================================================
    # SAMPLE TOO SMALL
    # ========================================================

    if (
        n_samples
        < minimum_articles
    ):

        return (
            ClusteringResult(

                status=(
                    "INSUFFICIENT_SAMPLE"
                ),

                hdbscan_labels=[
                    -1
                ] * n_samples,

                hdbscan_cluster_count=0,

                hdbscan_noise_count=(
                    n_samples
                ),

                kmeans_labels=[],

                kmeans_k=None,

                kmeans_silhouette=None,

                pca_components=None,
            )
        )


    # Delay expensive ML imports until clustering is actually
    # required.

    from sklearn.cluster import (
        HDBSCAN,
        KMeans,
    )

    from sklearn.decomposition import (
        PCA,
    )

    from sklearn.metrics import (
        silhouette_score,
    )


    # ========================================================
    # PCA
    # ========================================================

    n_features = int(
        embeddings
        .shape[1]
    )

    pca_components = min(
        int(
            pca_max_components
        ),
        n_features,
        n_samples - 1,
    )

    if (
        pca_components >= 2
        and pca_components
        < n_features
    ):

        reducer = PCA(
            n_components=(
                pca_components
            ),

            random_state=(
                random_state
            ),
        )

        clustering_matrix = (
            reducer.fit_transform(
                embeddings
            )
        )

    else:

        clustering_matrix = (
            embeddings
        )

        pca_components = None


    # ========================================================
    # HDBSCAN
    # ========================================================

    hdbscan = HDBSCAN(
        min_cluster_size=(
            hdbscan_min_cluster_size
        ),
        min_samples=(
            hdbscan_min_samples
        ),
        cluster_selection_method=(
            hdbscan_cluster_selection_method
        ),
        copy=False,
    )

    hdbscan_labels = (
        hdbscan
        .fit_predict(
            clustering_matrix
        )
        .astype(
            int
        )
    )


    unique_hdbscan = {
        int(
            value
        )

        for value in (
            hdbscan_labels
        )

        if (
            int(
                value
            )
            >= 0
        )
    }


    noise_count = int(
        np.sum(
            hdbscan_labels
            == -1
        )
    )


    # ========================================================
    # K-MEANS DIAGNOSTIC
    # ========================================================

    best_k = None

    best_score = None

    best_labels = []

    if (
        kmeans_enabled
        and n_samples >= 4
    ):

        upper = min(
            int(
                kmeans_max_k
            ),
            n_samples - 1,
        )

        lower = max(
            2,
            int(
                kmeans_min_k
            ),
        )


        for k in range(
            lower,
            upper + 1,
        ):

            candidate = KMeans(

                n_clusters=k,

                random_state=(
                    random_state
                ),

                n_init="auto",
            )

            labels = (
                candidate
                .fit_predict(
                    clustering_matrix
                )
            )


            if (
                len(
                    set(
                        int(
                            value
                        )
                        for value
                        in labels
                    )
                )
                < 2
            ):

                continue


            score = float(
                silhouette_score(
                    clustering_matrix,
                    labels,
                )
            )


            if (
                best_score
                is None
                or score
                > best_score
            ):

                best_score = (
                    score
                )

                best_k = (
                    k
                )

                best_labels = [
                    int(
                        value
                    )
                    for value
                    in labels
                ]


    # ========================================================
    # RESULT
    # ========================================================

    return (
        ClusteringResult(

            status="SUCCESS",

            hdbscan_labels=[
                int(
                    value
                )
                for value
                in hdbscan_labels
            ],

            hdbscan_cluster_count=(
                len(
                    unique_hdbscan
                )
            ),

            hdbscan_noise_count=(
                noise_count
            ),

            kmeans_labels=(
                best_labels
            ),

            kmeans_k=(
                best_k
            ),

            kmeans_silhouette=(
                best_score
            ),

            pca_components=(
                pca_components
            ),
        )
    )