from __future__ import annotations

import argparse
import csv
from datetime import (
    date,
    datetime,
    timedelta,
)
import hashlib
import json
from pathlib import Path
from statistics import fmean
from zoneinfo import ZoneInfo

import numpy as np

from bank_intel.intelligence.cluster_profile import (
    build_cluster_profiles,
)
from bank_intel.intelligence.clustering import (
    cluster_embeddings,
    top_similarity_pairs,
)
from bank_intel.intelligence.finbert_engine import (
    load_ml_config,
)
from bank_intel.intelligence.story_similarity import (
    assess_story_pair,
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

CORPUS_ROOT = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "ml_corpus"
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


def _relevance(
    analysis: dict,
) -> dict:

    return (
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


def _news_raw_validate_two_stage_analysis(
    analysis: dict,
    *,
    analysis_path: Path,
) -> bool:
    """
    Validate the frozen two-stage production contract.

    Returns operational_keep.
    """

    relevance = (
        _relevance(
            analysis
        )
    )

    architecture = str(
        relevance.get(
            "architecture",
            "",
        )
        or ""
    ).upper()

    if architecture != "TWO_STAGE":

        raise RuntimeError(
            "Corpus analysis found a non-two-stage ML article: "
            f"{analysis_path}"
        )

    stage_a = (
        _stage_a(
            analysis
        )
    )

    stage_a_status = str(
        stage_a.get(
            "status",
            "",
        )
        or ""
    ).upper()

    stage_a_label = str(
        stage_a.get(
            "predicted_label",
            "",
        )
        or ""
    ).upper()

    if (
        stage_a_status
        != "SUCCESS"
        or stage_a_label
        not in {
            "NOISE",
            "KEEP",
        }
    ):

        raise RuntimeError(
            "Invalid Stage-A output in "
            f"{analysis_path}: "
            f"status={stage_a_status!r}, "
            f"label={stage_a_label!r}"
        )

    operational_keep = bool(
        relevance.get(
            "operational_keep",
            False,
        )
    )

    if (
        operational_keep
        != (
            stage_a_label
            == "KEEP"
        )
    ):

        raise RuntimeError(
            "Stage-A label / operational_keep mismatch: "
            f"{analysis_path}"
        )

    stage_b_executed = bool(
        relevance.get(
            "stage_b_executed",
            False,
        )
    )

    if operational_keep:

        if not stage_b_executed:

            raise RuntimeError(
                "Stage B was not executed for a Stage-A KEEP article: "
                f"{analysis_path}"
            )

        stage_b = (
            _stage_b(
                analysis
            )
        )

        stage_b_status = str(
            stage_b.get(
                "status",
                "",
            )
            or ""
        ).upper()

        stage_b_label = str(
            stage_b.get(
                "predicted_label",
                "",
            )
            or ""
        ).upper()

        if (
            stage_b_status
            != "SUCCESS"
            or stage_b_label
            not in {
                "WATCH",
                "RELEVANT",
            }
        ):

            raise RuntimeError(
                "Invalid Stage-B output in "
                f"{analysis_path}: "
                f"status={stage_b_status!r}, "
                f"label={stage_b_label!r}"
            )

    else:

        if stage_b_executed:

            raise RuntimeError(
                "Stage B executed for a Stage-A NOISE article: "
                f"{analysis_path}"
            )

    return operational_keep


# NEWS_RAW_OPERATIONAL_PHASE3_BRIDGE_V1
# ============================================================================
# The transformer two-stage path and the operational production path are
# deliberately different concepts.
#
# RAW path:
#   Stage A raw prediction controls whether Stage B was executed.
#
# OPERATIONAL path:
#   safety/human policy controls whether the article enters Phase 3.
#
# Phase-3 validation therefore validates a temporary raw-model view, while
# corpus selection continues using the actual operational payload.
# ============================================================================


def _news_raw_validation_view(
    value,
):
    import copy

    cloned = copy.deepcopy(
        value
    )

    def normalise(
        node,
    ):

        if isinstance(
            node,
            dict,
        ):

            relevance = (
                node.get(
                    "supervisory_relevance"
                )
            )

            if isinstance(
                relevance,
                dict,
            ):

                stage_a = (
                    relevance.get(
                        "stage_a",
                        {},
                    )
                    or {}
                )

                if isinstance(
                    stage_a,
                    dict,
                ):

                    raw_label = str(
                        stage_a.get(
                            "raw_predicted_label"
                        )
                        or
                        stage_a.get(
                            "model_predicted_label"
                        )
                        or
                        stage_a.get(
                            "predicted_label"
                        )
                        or
                        ""
                    ).strip().upper()

                    if raw_label in {
                        "KEEP",
                        "NOISE",
                    }:

                        raw_keep = (
                            raw_label
                            == "KEEP"
                        )

                        # ---------------------------------------------
                        # Restore RAW Stage-A view ONLY in this cloned
                        # validation object.
                        # ---------------------------------------------

                        stage_a[
                            "predicted_label"
                        ] = raw_label

                        stage_a[
                            "operational_label"
                        ] = raw_label

                        stage_a[
                            "operational_keep"
                        ] = raw_keep

                        stage_a[
                            "operational_decision"
                        ] = (
                            "KEEP"
                            if raw_keep
                            else "FILTER_NOISE"
                        )

                        relevance[
                            "stage_a"
                        ] = stage_a

                        relevance[
                            "operational_label"
                        ] = raw_label

                        relevance[
                            "operational_keep"
                        ] = raw_keep

                        relevance[
                            "operational_decision"
                        ] = (
                            "KEEP"
                            if raw_keep
                            else "FILTER_NOISE"
                        )

                        # Some two-stage builds place operational
                        # compatibility fields at slightly different
                        # nested levels. Normalise only known contract
                        # names inside supervisory_relevance.
                        def sync_contract_fields(
                            obj,
                        ):

                            if isinstance(
                                obj,
                                dict,
                            ):

                                for key in list(
                                    obj.keys()
                                ):

                                    if (
                                        key
                                        == "operational_keep"
                                    ):
                                        obj[
                                            key
                                        ] = raw_keep

                                    elif (
                                        key
                                        == "operational_label"
                                    ):
                                        obj[
                                            key
                                        ] = raw_label

                                    elif (
                                        key
                                        == "operational_decision"
                                    ):
                                        obj[
                                            key
                                        ] = (
                                            "KEEP"
                                            if raw_keep
                                            else
                                            "FILTER_NOISE"
                                        )

                                    else:
                                        sync_contract_fields(
                                            obj[
                                                key
                                            ]
                                        )

                            elif isinstance(
                                obj,
                                list,
                            ):

                                for item in obj:
                                    sync_contract_fields(
                                        item
                                    )

                        sync_contract_fields(
                            relevance
                        )

                        node[
                            "supervisory_relevance"
                        ] = relevance

            for child in list(
                node.values()
            ):
                normalise(
                    child
                )

        elif isinstance(
            node,
            list,
        ):

            for child in node:
                normalise(
                    child
                )

    normalise(
        cloned
    )

    return cloned


def validate_two_stage_analysis(
    *args,
    **kwargs,
):
    """
    Validate the original two-stage ML contract without allowing later
    production overrides to masquerade as raw model predictions.

    The actual analysis object is never changed here.
    """

    safe_args = tuple(
        _news_raw_validation_view(
            value
        )
        if isinstance(
            value,
            (
                dict,
                list,
            ),
        )
        else value

        for value
        in args
    )

    safe_kwargs = {
        key: (
            _news_raw_validation_view(
                value
            )
            if isinstance(
                value,
                (
                    dict,
                    list,
                ),
            )
            else value
        )

        for (
            key,
            value,
        )
        in kwargs.items()
    }

    return (
        _news_raw_validate_two_stage_analysis(
            *safe_args,
            **safe_kwargs,
        )
    )






# NEWS_CLEAN_OPERATIONAL_CORPUS_V1
# ============================================================================
# RAW Stage A:
#     controls historical Stage-B execution validation.
#
# OPERATIONAL Stage A:
#     controls Phase-3 corpus membership and every downstream stage.
# ============================================================================


def _news_clean_raw_label(analysis):
    if not isinstance(analysis, dict):
        return ""

    relevance = (
        (analysis.get("ml", {}) or {})
        .get("supervisory_relevance", {})
        or {}
    )

    stage_a = (
        relevance.get("stage_a", {})
        or {}
    )

    policy = (
        analysis.get("production_policy", {})
        or {}
    )

    for value in (
        stage_a.get("raw_predicted_label"),
        relevance.get("raw_predicted_label"),
        stage_a.get("model_predicted_label"),
        policy.get("raw_stage_a_label"),
    ):
        label = str(value or "").strip().upper()

        if label in {"KEEP", "NOISE"}:
            return label

    # Legacy fallback only.
    return str(
        stage_a.get("predicted_label")
        or relevance.get("predicted_label")
        or ""
    ).strip().upper()


def _news_clean_operational_label(analysis):
    if not isinstance(analysis, dict):
        return ""

    relevance = (
        (analysis.get("ml", {}) or {})
        .get("supervisory_relevance", {})
        or {}
    )

    stage_a = (
        relevance.get("stage_a", {})
        or {}
    )

    # ----------------------------------------------------------------
    # Explicit operational label.
    # ----------------------------------------------------------------

    for value in (
        stage_a.get("operational_label"),
        relevance.get("operational_label"),
    ):
        label = str(value or "").strip().upper()

        if label in {"KEEP", "NOISE"}:
            return label

    # ----------------------------------------------------------------
    # Explicit operational Boolean.
    # ----------------------------------------------------------------

    for container in (
        stage_a,
        relevance,
    ):
        value = container.get(
            "operational_keep"
        )

        if isinstance(value, bool):
            return (
                "KEEP"
                if value
                else "NOISE"
            )

    # ----------------------------------------------------------------
    # Production-policy audit.
    # ----------------------------------------------------------------

    policies = [
        analysis.get(
            "production_policy"
        ),
        (
            analysis.get(
                "supervisory_comparison",
                {},
            )
            or {}
        ).get(
            "production_policy"
        ),
    ]

    for policy in policies:

        if not isinstance(policy, dict):
            continue

        label = str(
            policy.get("operational_label")
            or ""
        ).strip().upper()

        if label in {"KEEP", "NOISE"}:
            return label

        value = policy.get(
            "operational_keep"
        )

        if isinstance(value, bool):
            return (
                "KEEP"
                if value
                else "NOISE"
            )

    # ----------------------------------------------------------------
    # Backward compatibility.
    # ----------------------------------------------------------------

    for value in (
        stage_a.get("predicted_label"),
        relevance.get("predicted_label"),
    ):
        label = str(value or "").strip().upper()

        if label in {"KEEP", "NOISE"}:
            return label

    return ""


def _news_clean_raw_validation_copy(value):
    """
    Deep-copy an analysis payload and expose RAW Stage A to the strict
    two-stage validator. Original payload is never changed.
    """

    import copy

    cloned = copy.deepcopy(value)

    if not isinstance(cloned, dict):
        return cloned

    relevance = (
        (cloned.get("ml", {}) or {})
        .get("supervisory_relevance", {})
        or {}
    )

    stage_a = (
        relevance.get("stage_a", {})
        or {}
    )

    if not isinstance(stage_a, dict):
        return cloned

    raw = _news_clean_raw_label(
        cloned
    )

    if raw not in {
        "KEEP",
        "NOISE",
    }:
        return cloned

    raw_keep = raw == "KEEP"

    stage_a[
        "raw_predicted_label"
    ] = raw

    stage_a[
        "predicted_label"
    ] = raw

    stage_a[
        "operational_label"
    ] = raw

    stage_a[
        "operational_keep"
    ] = raw_keep

    relevance[
        "raw_predicted_label"
    ] = raw

    relevance[
        "predicted_label"
    ] = raw

    relevance[
        "operational_label"
    ] = raw

    relevance[
        "operational_keep"
    ] = raw_keep

    relevance[
        "operational_decision"
    ] = (
        "KEEP"
        if raw_keep
        else "FILTER_NOISE"
    )

    relevance[
        "stage_a"
    ] = stage_a

    cloned.setdefault(
        "ml",
        {},
    )[
        "supervisory_relevance"
    ] = relevance

    return cloned


def _news_clean_validate_raw(
    *args,
    **kwargs,
):
    """
    Strict validation runs against RAW Stage A.
    """

    new_args = tuple(
        _news_clean_raw_validation_copy(value)
        if isinstance(value, dict)
        else value
        for value in args
    )

    new_kwargs = {
        key: (
            _news_clean_raw_validation_copy(value)
            if isinstance(value, dict)
            else value
        )
        for key, value
        in kwargs.items()
    }

    return validate_two_stage_analysis(
        *new_args,
        **new_kwargs,
    )


def _news_clean_operationalize_analysis(
    analysis,
):
    """
    The normal Phase-3 working copy must expose the FINAL production decision.
    """

    if not isinstance(analysis, dict):
        return analysis

    relevance = (
        (analysis.get("ml", {}) or {})
        .get("supervisory_relevance", {})
        or {}
    )

    stage_a = (
        relevance.get("stage_a", {})
        or {}
    )

    if not isinstance(stage_a, dict):
        return analysis

    raw = _news_clean_raw_label(
        analysis
    )

    operational = (
        _news_clean_operational_label(
            analysis
        )
    )

    if operational not in {
        "KEEP",
        "NOISE",
    }:
        raise RuntimeError(
            "Phase-3 article has no valid "
            "operational Stage-A label: "
            f"{analysis.get('headline')}"
        )

    keep = (
        operational
        == "KEEP"
    )

    if raw in {
        "KEEP",
        "NOISE",
    }:
        stage_a[
            "raw_predicted_label"
        ] = raw

        relevance[
            "raw_predicted_label"
        ] = raw

    # Every legacy/current Phase-3 access now sees OPERATIONAL.
    stage_a[
        "predicted_label"
    ] = operational

    stage_a[
        "operational_label"
    ] = operational

    stage_a[
        "operational_keep"
    ] = keep

    relevance[
        "predicted_label"
    ] = operational

    relevance[
        "stage_a_label"
    ] = operational

    relevance[
        "operational_label"
    ] = operational

    relevance[
        "operational_keep"
    ] = keep

    relevance[
        "operational_decision"
    ] = (
        "KEEP"
        if keep
        else "FILTER_NOISE"
    )

    relevance[
        "stage_a"
    ] = stage_a

    analysis.setdefault(
        "ml",
        {},
    )[
        "supervisory_relevance"
    ] = relevance

    # Compatibility fields occasionally used by older Phase-3 code.
    analysis[
        "stage_a_label"
    ] = operational

    analysis[
        "operational_stage_a_label"
    ] = operational

    analysis[
        "operational_keep"
    ] = keep

    return analysis


def _news_clean_operationalize_manifest_item(
    item,
    analysis,
):
    """
    Keep any manifest summary fields aligned with the operational analysis.
    """

    if not isinstance(item, dict):
        return

    operational = (
        _news_clean_operational_label(
            analysis
        )
    )

    if operational not in {
        "KEEP",
        "NOISE",
    }:
        return

    keep = (
        operational
        == "KEEP"
    )

    # Common schema variants.
    item[
        "predicted_label"
    ] = operational

    item[
        "stage_a_label"
    ] = operational

    item[
        "supervisory_prediction"
    ] = operational

    item[
        "operational_label"
    ] = operational

    item[
        "operational_keep"
    ] = keep


def corpus_articles(
    *,
    end_date: date,
    days: int,
    expected_ml_version: str,
) -> tuple[
    list[
        dict
    ],
    list[
        str
    ],
]:

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

    articles = []
    dates_with_manifests = []
    incompatible_dates = []

    current = (
        start_date
    )

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

        dates_with_manifests.append(
            publication_date
        )

        manifest_ml_version = str(
            manifest.get(
                "ml_version",
                "",
            )
            or ""
        )

        architecture = str(
            manifest.get(
                "supervisory_relevance_architecture",
                "",
            )
            or ""
        ).upper()

        if (
            manifest_ml_version
            != expected_ml_version
            or architecture
            != "TWO_STAGE"
        ):

            incompatible_dates.append(
                (
                    publication_date,
                    manifest_ml_version,
                    architecture,
                )
            )

            current += timedelta(
                days=1
            )
            continue

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

            embedding_path = (
                resolve_project_path(
                    str(
                        item[
                            "embedding_file"
                        ]
                    )
                )
            )

            if (
                not analysis_path.exists()
                or not embedding_path.exists()
            ):

                raise FileNotFoundError(
                    "ML manifest references a missing "
                    "analysis/embedding file."
                )

            analysis = (
                load_json(
                    analysis_path
                )
            )
            analysis = _news_clean_operationalize_analysis(analysis)
            _news_clean_operationalize_manifest_item(item, analysis)

            operational_keep = (
                _news_clean_validate_raw(
                    analysis,
                    analysis_path=(
                        analysis_path
                    ),
                )
            )

            embedding = (
                np.load(
                    embedding_path
                )
                .astype(
                    np.float32
                )
                .reshape(
                    -1
                )
            )

            if (
                embedding.size
                == 0
                or not np.all(
                    np.isfinite(
                        embedding
                    )
                )
            ):

                raise RuntimeError(
                    "Invalid embedding: "
                    f"{embedding_path}"
                )

            source_url = str(
                analysis.get(
                    "source_url",
                    "",
                )
                or ""
            ).strip()

            if not source_url:

                raise RuntimeError(
                    "ML analysis has blank source_url: "
                    f"{analysis_path}"
                )

            articles.append(
                {
                    "publication_date": (
                        publication_date
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
                    "published_at": (
                        analysis.get(
                            "published_at"
                        )
                    ),
                    "source_url": (
                        source_url
                    ),
                    "analysis": (
                        analysis
                    ),
                    "analysis_file": str(
                        analysis_path
                    ),
                    "embedding_file": str(
                        embedding_path
                    ),
                    "embedding": (
                        embedding
                    ),
                    "operational_keep": (
                        operational_keep
                    ),
                }
            )

        current += timedelta(
            days=1
        )

    if incompatible_dates:

        formatted = "; ".join(
            (
                f"{day}: version={version!r}, "
                f"architecture={architecture!r}"
            )
            for (
                day,
                version,
                architecture,
            ) in incompatible_dates[
                :20
            ]
        )

        raise RuntimeError(
            "The requested corpus window contains ML outputs "
            "from a different production architecture/version. "
            "Do not mix old 3-class outputs with frozen two-stage "
            f"{expected_ml_version}. Incompatible dates: "
            f"{formatted}. "
            "For immediate QA use --days 1 on 2026-10-07; "
            "for a larger historical window rerun Phase-2 ML only "
            "for the dates you intentionally want in that window."
        )

    seen_urls = set()

    for article in (
        articles
    ):

        url = (
            article[
                "source_url"
            ]
        )

        if url in seen_urls:

            raise RuntimeError(
                "Duplicate source_url detected in ML corpus: "
                f"{url}"
            )

        seen_urls.add(
            url
        )

    return (
        articles,
        dates_with_manifests,
    )


PAIR_FIELDS = [
    "pair_type",
    "cosine_similarity",
    "date_gap_days",
    "headline_jaccard",
    "shared_primary_entities",
    "shared_entities",
    "evidence",
    "left_publication_date",
    "left_source",
    "left_headline",
    "left_url",
    "right_publication_date",
    "right_source",
    "right_headline",
    "right_url",
]


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


def write_pair_csv(
    path: Path,
    rows: list[
        dict
    ],
) -> None:

    write_csv(
        path,
        rows,
        PAIR_FIELDS,
    )


def normalize_matrix(
    articles: list[
        dict
    ],
) -> tuple[
    np.ndarray,
    dict,
]:

    if not articles:

        return (
            np.empty(
                (
                    0,
                    0,
                ),
                dtype=(
                    np.float32
                ),
            ),
            {
                "dimension": 0,
                "min_input_norm": None,
                "max_input_norm": None,
            },
        )

    dimensions = {
        int(
            article[
                "embedding"
            ].shape[
                0
            ]
        )
        for article in (
            articles
        )
    }

    if len(
        dimensions
    ) != 1:

        raise RuntimeError(
            "Embedding dimensions differ across corpus: "
            f"{sorted(dimensions)}"
        )

    matrix = np.vstack(
        [
            article[
                "embedding"
            ]
            for article in (
                articles
            )
        ]
    ).astype(
        np.float32
    )

    norms = np.linalg.norm(
        matrix,
        axis=1,
    )

    if np.any(
        norms <= 0
    ):

        raise RuntimeError(
            "Corpus contains zero-norm embeddings."
        )

    input_min = float(
        norms.min()
    )
    input_max = float(
        norms.max()
    )

    # Defensively normalize. This preserves cosine direction and
    # makes clustering/similarity semantics reproducible even if
    # an older saved vector differs by tiny floating-point scale.
    matrix = (
        matrix
        / norms[
            :,
            None,
        ]
    ).astype(
        np.float32
    )

    return (
        matrix,
        {
            "dimension": int(
                matrix.shape[
                    1
                ]
            ),
            "min_input_norm": (
                input_min
            ),
            "max_input_norm": (
                input_max
            ),
        },
    )


def filtered_noise_rows(
    articles: list[
        dict
    ],
) -> list[
    dict
]:

    rows = []

    for article in (
        articles
    ):

        if article[
            "operational_keep"
        ]:

            continue

        analysis = (
            article[
                "analysis"
            ]
        )

        stage1 = (
            analysis.get(
                "stage1",
                {},
            )
            or {}
        )

        stage_a = (
            _stage_a(
                analysis
            )
        )

        comparison = (
            analysis.get(
                "supervisory_comparison",
                {},
            )
            or {}
        )

        rows.append(
            {
                "publication_date": (
                    article[
                        "publication_date"
                    ]
                ),
                "source_name": (
                    article[
                        "source_name"
                    ]
                ),
                "headline": (
                    article[
                        "headline"
                    ]
                ),
                "source_url": (
                    article[
                        "source_url"
                    ]
                ),
                "stage1_gate_decision": (
                    stage1.get(
                        "gate_decision"
                    )
                ),
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
                "stage1_stage_a_status": (
                    (
                        comparison.get(
                            "stage_a",
                            {},
                        )
                        or {}
                    )
                    .get(
                        "comparison_status"
                    )
                ),
                "review_priority": (
                    comparison.get(
                        "overall_review_priority"
                    )
                ),
            }
        )

    return rows


def build_event_groups(
    rows: list[
        dict
    ],
    article_by_url: dict[
        str,
        dict
    ],
) -> list[
    dict
]:
    """
    Connected components over SAME_EVENT + NEAR_DUPLICATE edges.

    These are candidate event groups, not automatically deduplicated truth.
    """

    if not rows:
        return []

    parent: dict[
        str,
        str
    ] = {}

    def find(
        value: str,
    ) -> str:

        parent.setdefault(
            value,
            value,
        )

        while (
            parent[
                value
            ]
            != value
        ):

            parent[
                value
            ] = parent[
                parent[
                    value
                ]
            ]

            value = parent[
                value
            ]

        return value

    def union(
        left: str,
        right: str,
    ) -> None:

        left_root = find(
            left
        )
        right_root = find(
            right
        )

        if (
            left_root
            != right_root
        ):

            if (
                left_root
                < right_root
            ):

                parent[
                    right_root
                ] = left_root

            else:

                parent[
                    left_root
                ] = right_root

    for row in (
        rows
    ):

        left = str(
            row.get(
                "left_url",
                "",
            )
            or ""
        ).strip()

        right = str(
            row.get(
                "right_url",
                "",
            )
            or ""
        ).strip()

        if (
            left
            and right
        ):

            union(
                left,
                right,
            )

    component_urls: dict[
        str,
        set[
            str
        ],
    ] = {}

    for url in (
        list(
            parent
        )
    ):

        component_urls.setdefault(
            find(
                url
            ),
            set(),
        ).add(
            url
        )

    groups = []

    for urls in (
        component_urls.values()
    ):

        if len(
            urls
        ) < 2:

            continue

        sorted_urls = sorted(
            urls
        )

        digest = hashlib.sha256(
            "\n".join(
                sorted_urls
            ).encode(
                "utf-8"
            )
        ).hexdigest()[
            :12
        ]

        component_edges = [
            row
            for row in (
                rows
            )
            if (
                row.get(
                    "left_url"
                )
                in urls
                and row.get(
                    "right_url"
                )
                in urls
            )
        ]

        similarities = [
            float(
                row[
                    "cosine_similarity"
                ]
            )
            for row in (
                component_edges
            )
        ]

        members = []

        for url in (
            sorted_urls
        ):

            article = (
                article_by_url.get(
                    url,
                    {},
                )
            )

            members.append(
                {
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
                        url
                    ),
                    "stage_b_label": (
                        _stage_b(
                            article.get(
                                "analysis",
                                {},
                            )
                        )
                        .get(
                            "predicted_label"
                        )
                    ),
                }
            )

        members.sort(
            key=lambda item: (
                str(
                    item.get(
                        "publication_date",
                        "",
                    )
                ),
                str(
                    item.get(
                        "source_name",
                        "",
                    )
                ),
                str(
                    item.get(
                        "headline",
                        "",
                    )
                ),
            )
        )

        groups.append(
            {
                "event_group_id": (
                    f"evt_{digest}"
                ),
                "status": (
                    "CANDIDATE_EVENT_GROUP"
                ),
                "article_count": len(
                    members
                ),
                "edge_count": len(
                    component_edges
                ),
                "min_cosine_similarity": (
                    min(
                        similarities
                    )
                    if similarities
                    else None
                ),
                "max_cosine_similarity": (
                    max(
                        similarities
                    )
                    if similarities
                    else None
                ),
                "mean_cosine_similarity": (
                    fmean(
                        similarities
                    )
                    if similarities
                    else None
                ),
                "members": (
                    members
                ),
                "edges": (
                    component_edges
                ),
            }
        )

    groups.sort(
        key=lambda item: (
            -int(
                item[
                    "article_count"
                ]
            ),
            str(
                item[
                    "event_group_id"
                ]
            ),
        )
    )

    return groups


def empty_profile_payload() -> dict:

    return {
        "cluster_count": 0,
        "hdbscan_outlier_count": 0,
        "hdbscan_outlier_articles": [],
        "noise_count": 0,
        "noise_articles": [],
        "hdbscan_label_minus_one_semantics": (
            "SEMANTIC_OUTLIER_NOT_RELEVANCE_NOISE"
        ),
        "clusters": [],
    }


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Rolling Stage-A-KEEP FinBERT similarity, candidate "
            "event linking, HDBSCAN profiling, and diagnostic K-Means."
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
        load_ml_config()
    )

    expected_ml_version = str(
        config.get(
            "version",
            "",
        )
        or ""
    )

    (
        all_articles,
        dates_with_manifests,
    ) = (
        corpus_articles(
            end_date=(
                end_date
            ),
            days=(
                days
            ),
            expected_ml_version=(
                expected_ml_version
            ),
        )
    )

    intelligence_articles = [
        article
        for article in (
            all_articles
        )
        if article[
            "operational_keep"
        ]
    ]

    filtered_articles = [
        article
        for article in (
            all_articles
        )
        if not article[
            "operational_keep"
        ]
    ]

    print()
    print(
        "=" * 88
    )
    print(
        "BANK SUPERVISION NEWS — PHASE 3 CORPUS INTELLIGENCE"
    )
    print(
        "=" * 88
    )
    print(
        f"Window start             : {start_date.isoformat()}"
    )
    print(
        f"Window end               : {end_date.isoformat()}"
    )
    print(
        f"ML version               : {expected_ml_version}"
    )
    print(
        f"Compatible ML dates      : {len(dates_with_manifests)}"
    )
    print(
        f"All Phase-2 articles     : {len(all_articles)}"
    )
    print(
        f"Stage-A KEEP corpus      : {len(intelligence_articles)}"
    )
    print(
        f"Stage-A NOISE audit only : {len(filtered_articles)}"
    )

    output_root = (
        CORPUS_ROOT
        / end_date.isoformat()
        / f"window_{days}d"
    )

    output_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    noise_audit_path = (
        output_root
        / "stage_a_filtered_noise_audit.csv"
    )

    write_csv(
        noise_audit_path,
        filtered_noise_rows(
            all_articles
        ),
        [
            "publication_date",
            "source_name",
            "headline",
            "source_url",
            "stage1_gate_decision",
            "stage_a_label",
            "stage_a_confidence",
            "stage1_stage_a_status",
            "review_priority",
        ],
    )

    if not intelligence_articles:

        manifest = {
            "status": "NO_STAGE_A_KEEP_ARTICLES",
            "created_at": (
                datetime.now(
                    SRI_LANKA_TZ
                ).isoformat()
            ),
            "ml_version": (
                expected_ml_version
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
            "all_article_count": len(
                all_articles
            ),
            "stage_a_keep_count": 0,
            "stage_a_noise_count": len(
                filtered_articles
            ),
            "stage_a_noise_audit_file": str(
                noise_audit_path.relative_to(
                    PROJECT_ROOT
                )
            ),
        }

        (
            output_root
            / "corpus_manifest.json"
        ).write_text(
            json.dumps(
                manifest,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        return

    (
        matrix,
        embedding_qa,
    ) = (
        normalize_matrix(
            intelligence_articles
        )
    )

    similarity = (
        config.get(
            "similarity",
            {},
        )
        or {}
    )

    related_min = float(
        similarity.get(
            "related_min_cosine_similarity",
            0.88,
        )
    )

    raw_pairs = (
        top_similarity_pairs(
            matrix,
            minimum_similarity=(
                related_min
            ),
            top_n=int(
                similarity.get(
                    "top_related_pairs",
                    250,
                )
            ),
        )
    )

    all_pair_rows = []
    same_event_rows = []
    near_duplicate_rows = []
    story_continuation_rows = []
    shared_event_context_rows = []
    related_topic_rows = []

    for pair in (
        raw_pairs
    ):

        left = (
            intelligence_articles[
                pair.left_index
            ]
        )

        right = (
            intelligence_articles[
                pair.right_index
            ]
        )

        assessment = (
            assess_story_pair(
                left_analysis=(
                    left[
                        "analysis"
                    ]
                ),
                right_analysis=(
                    right[
                        "analysis"
                    ]
                ),
                cosine_similarity=(
                    pair.cosine_similarity
                ),
                related_min_cosine=(
                    related_min
                ),
                same_event_min_cosine=float(
                    similarity.get(
                        "same_event_min_cosine_similarity",
                        0.94,
                    )
                ),
                near_duplicate_min_cosine=float(
                    similarity.get(
                        "near_duplicate_min_cosine_similarity",
                        0.96,
                    )
                ),
                max_event_date_gap_days=int(
                    similarity.get(
                        "same_event_max_date_gap_days",
                        3,
                    )
                ),
                same_event_min_headline_jaccard=float(
                    similarity.get(
                        "same_event_min_headline_jaccard",
                        0.30,
                    )
                ),
                near_duplicate_min_headline_jaccard=float(
                    similarity.get(
                        "near_duplicate_min_headline_jaccard",
                        0.55,
                    )
                ),
            )
        )

        row = {
            "pair_type": (
                assessment.pair_type
            ),
            "cosine_similarity": round(
                assessment.cosine_similarity,
                6,
            ),
            "date_gap_days": (
                assessment.date_gap_days
            ),
            "headline_jaccard": round(
                assessment.headline_jaccard,
                6,
            ),
            "shared_primary_entities": (
                "; ".join(
                    assessment.shared_primary_entities
                )
            ),
            "shared_entities": (
                "; ".join(
                    assessment.shared_entities
                )
            ),
            "evidence": (
                "; ".join(
                    assessment.evidence
                )
            ),
            "left_publication_date": (
                left[
                    "publication_date"
                ]
            ),
            "left_source": (
                left[
                    "source_name"
                ]
            ),
            "left_headline": (
                left[
                    "headline"
                ]
            ),
            "left_url": (
                left[
                    "source_url"
                ]
            ),
            "right_publication_date": (
                right[
                    "publication_date"
                ]
            ),
            "right_source": (
                right[
                    "source_name"
                ]
            ),
            "right_headline": (
                right[
                    "headline"
                ]
            ),
            "right_url": (
                right[
                    "source_url"
                ]
            ),
        }

        all_pair_rows.append(
            row
        )

        if (
            assessment.pair_type
            == "NEAR_DUPLICATE_CANDIDATE"
        ):

            near_duplicate_rows.append(
                row
            )

        elif (
            assessment.pair_type
            == "SAME_EVENT_CANDIDATE"
        ):

            same_event_rows.append(
                row
            )

        elif (
            assessment.pair_type
            == "STORY_CONTINUATION"
        ):

            story_continuation_rows.append(
                row
            )

        elif (
            assessment.pair_type
            == "SHARED_EVENT_CONTEXT"
        ):

            shared_event_context_rows.append(
                row
            )

        elif (
            assessment.pair_type
            == "RELATED_TOPIC"
        ):

            related_topic_rows.append(
                row
            )

    related_path = (
        output_root
        / "related_pairs.csv"
    )

    related_topic_path = (
        output_root
        / "related_topic_pairs.csv"
    )

    same_event_path = (
        output_root
        / "same_event_candidates.csv"
    )

    near_duplicate_path = (
        output_root
        / "near_duplicate_candidates.csv"
    )

    story_continuation_path = (
        output_root
        / "story_continuations.csv"
    )

    shared_event_context_path = (
        output_root
        / "shared_event_context.csv"
    )

    write_pair_csv(
        related_path,
        all_pair_rows,
    )

    write_pair_csv(
        related_topic_path,
        related_topic_rows,
    )

    write_pair_csv(
        same_event_path,
        same_event_rows,
    )

    write_pair_csv(
        near_duplicate_path,
        near_duplicate_rows,
    )

    write_pair_csv(
        story_continuation_path,
        story_continuation_rows,
    )

    write_pair_csv(
        shared_event_context_path,
        shared_event_context_rows,
    )

    article_by_url = {
        article[
            "source_url"
        ]: article
        for article in (
            intelligence_articles
        )
    }

    event_groups = (
        build_event_groups(
            (
                same_event_rows
                + near_duplicate_rows
            ),
            article_by_url,
        )
    )

    event_groups_path = (
        output_root
        / "candidate_event_groups.json"
    )

    event_groups_path.write_text(
        json.dumps(
            {
                "status": "SUCCESS",
                "semantics": (
                    "Connected candidate groups from SAME_EVENT_CANDIDATE "
                    "and NEAR_DUPLICATE_CANDIDATE edges. "
                    "Not automatic deduplication truth."
                ),
                "event_group_count": (
                    len(
                        event_groups
                    )
                ),
                "groups": (
                    event_groups
                ),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    clustering = (
        config.get(
            "clustering",
            {},
        )
        or {}
    )

    reduction = (
        clustering.get(
            "dimensionality_reduction",
            {},
        )
        or {}
    )

    hdbscan = (
        clustering.get(
            "hdbscan",
            {},
        )
        or {}
    )

    kmeans = (
        clustering.get(
            "kmeans_diagnostic",
            {},
        )
        or {}
    )

    result = (
        cluster_embeddings(
            matrix,
            minimum_articles=int(
                clustering.get(
                    "minimum_articles",
                    12,
                )
            ),
            pca_max_components=int(
                reduction.get(
                    "max_components",
                    50,
                )
            ),
            hdbscan_min_cluster_size=int(
                hdbscan.get(
                    "min_cluster_size",
                    3,
                )
            ),
            hdbscan_min_samples=(
                hdbscan.get(
                    "min_samples"
                )
            ),
            hdbscan_cluster_selection_method=str(
                hdbscan.get(
                    "cluster_selection_method",
                    "eom",
                )
            ),
            kmeans_enabled=bool(
                kmeans.get(
                    "enabled",
                    True,
                )
            ),
            kmeans_min_k=int(
                kmeans.get(
                    "min_k",
                    2,
                )
            ),
            kmeans_max_k=int(
                kmeans.get(
                    "max_k",
                    12,
                )
            ),
            random_state=int(
                kmeans.get(
                    "random_state",
                    42,
                )
            ),
        )
    )

    cluster_payload = (
        result.to_dict()
    )

    cluster_payload.update(
        {
            "window_start": (
                start_date.isoformat()
            ),
            "window_end": (
                end_date.isoformat()
            ),
            "days": (
                days
            ),
            "corpus_semantics": (
                "STAGE_A_KEEP_ONLY"
            ),
            "article_count": len(
                intelligence_articles
            ),
            "hdbscan_label_minus_one_semantics": (
                "SEMANTIC_OUTLIER_NOT_RELEVANCE_NOISE"
            ),
            "articles": [],
        }
    )

    hdbscan_labels_available = (
        len(
            result.hdbscan_labels
        )
        == len(
            intelligence_articles
        )
    )

    kmeans_labels_available = (
        len(
            result.kmeans_labels
        )
        == len(
            intelligence_articles
        )
    )

    for (
        index,
        article,
    ) in enumerate(
        intelligence_articles
    ):

        analysis = (
            article[
                "analysis"
            ]
        )

        stage_b = (
            _stage_b(
                analysis
            )
        )

        cluster_payload[
            "articles"
        ].append(
            {
                "publication_date": (
                    article[
                        "publication_date"
                    ]
                ),
                "source_name": (
                    article[
                        "source_name"
                    ]
                ),
                "headline": (
                    article[
                        "headline"
                    ]
                ),
                "source_url": (
                    article[
                        "source_url"
                    ]
                ),
                "stage_a_label": "KEEP",
                "stage_b_label": (
                    stage_b.get(
                        "predicted_label"
                    )
                ),
                "hdbscan_cluster": (
                    result.hdbscan_labels[
                        index
                    ]
                    if hdbscan_labels_available
                    else None
                ),
                "kmeans_cluster": (
                    result.kmeans_labels[
                        index
                    ]
                    if kmeans_labels_available
                    else None
                ),
            }
        )

    cluster_path = (
        output_root
        / "clusters.json"
    )

    cluster_path.write_text(
        json.dumps(
            cluster_payload,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    profile_config = (
        config.get(
            "cluster_profiling",
            {},
        )
        or {}
    )

    if hdbscan_labels_available:

        profiles = (
            build_cluster_profiles(
                articles=(
                    intelligence_articles
                ),
                embeddings=(
                    matrix
                ),
                hdbscan_labels=(
                    result.hdbscan_labels
                ),
                top_terms=int(
                    profile_config.get(
                        "top_headline_terms",
                        12,
                    )
                ),
                representative_articles=int(
                    profile_config.get(
                        "representative_articles",
                        5,
                    )
                ),
            )
        )

        profile_status = (
            "SUCCESS"
        )

    else:

        profiles = (
            empty_profile_payload()
        )

        profile_status = (
            "CLUSTERING_LABELS_NOT_AVAILABLE"
        )

    profiles[
        "status"
    ] = (
        profile_status
    )

    profiles[
        "corpus_semantics"
    ] = (
        "STAGE_A_KEEP_ONLY"
    )

    profile_path = (
        output_root
        / "cluster_profiles.json"
    )

    profile_path.write_text(
        json.dumps(
            profiles,
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
        "ml_version": (
            expected_ml_version
        ),
        "supervisory_relevance_architecture": (
            "TWO_STAGE"
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
        "compatible_ml_dates": (
            dates_with_manifests
        ),
        "all_phase2_article_count": len(
            all_articles
        ),
        "stage_a_keep_article_count": len(
            intelligence_articles
        ),
        "stage_a_noise_audit_count": len(
            filtered_articles
        ),
        "semantic_corpus_policy": (
            "SIMILARITY_AND_CLUSTERING_USE_STAGE_A_KEEP_ONLY"
        ),
        "embedding_qa": (
            embedding_qa
        ),
        "related_pair_count": len(
            all_pair_rows
        ),
        "related_topic_pair_count": len(
            related_topic_rows
        ),
        "same_event_candidate_count": len(
            same_event_rows
        ),
        "near_duplicate_candidate_count": len(
            near_duplicate_rows
        ),
        "story_continuation_count": len(
            story_continuation_rows
        ),
        "shared_event_context_count": len(
            shared_event_context_rows
        ),
        "candidate_event_group_count": len(
            event_groups
        ),
        "clustering_status": (
            result.status
        ),
        "hdbscan_cluster_count": (
            result.hdbscan_cluster_count
        ),
        "hdbscan_outlier_count": (
            result.hdbscan_noise_count
        ),
        "hdbscan_label_minus_one_semantics": (
            "SEMANTIC_OUTLIER_NOT_RELEVANCE_NOISE"
        ),
        "kmeans_role": (
            "DIAGNOSTIC_ONLY"
        ),
        "kmeans_k": (
            result.kmeans_k
        ),
        "kmeans_silhouette": (
            result.kmeans_silhouette
        ),
        "stage_a_noise_audit_file": str(
            noise_audit_path.relative_to(
                PROJECT_ROOT
            )
        ),
        "related_pairs_file": str(
            related_path.relative_to(
                PROJECT_ROOT
            )
        ),
        "related_topic_file": str(
            related_topic_path.relative_to(
                PROJECT_ROOT
            )
        ),
        "same_event_file": str(
            same_event_path.relative_to(
                PROJECT_ROOT
            )
        ),
        "near_duplicate_file": str(
            near_duplicate_path.relative_to(
                PROJECT_ROOT
            )
        ),
        "story_continuation_file": str(
            story_continuation_path.relative_to(
                PROJECT_ROOT
            )
        ),
        "shared_event_context_file": str(
            shared_event_context_path.relative_to(
                PROJECT_ROOT
            )
        ),
        "candidate_event_groups_file": str(
            event_groups_path.relative_to(
                PROJECT_ROOT
            )
        ),
        "cluster_file": str(
            cluster_path.relative_to(
                PROJECT_ROOT
            )
        ),
        "cluster_profile_file": str(
            profile_path.relative_to(
                PROJECT_ROOT
            )
        ),
    }

    manifest_path = (
        output_root
        / "corpus_manifest.json"
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
        "PHASE 3 CORPUS INTELLIGENCE COMPLETE"
    )
    print(
        "=" * 88
    )
    print(
        f"Stage-A KEEP articles     : {len(intelligence_articles)}"
    )
    print(
        f"Stage-A NOISE audit rows  : {len(filtered_articles)}"
    )
    print(
        f"Related pairs             : {len(all_pair_rows)}"
    )
    print(
        f"Related-topic pairs       : {len(related_topic_rows)}"
    )
    print(
        f"Same-event candidates     : {len(same_event_rows)}"
    )
    print(
        f"Near-duplicate candidates : {len(near_duplicate_rows)}"
    )
    print(
        f"Story continuations       : {len(story_continuation_rows)}"
    )
    print(
        f"Shared event contexts     : {len(shared_event_context_rows)}"
    )
    print(
        f"Candidate event groups    : {len(event_groups)}"
    )
    print(
        f"Clustering status         : {result.status}"
    )
    print(
        f"HDBSCAN clusters          : {result.hdbscan_cluster_count}"
    )
    print(
        "HDBSCAN outliers          : "
        f"{result.hdbscan_noise_count}"
    )
    print(
        f"K-Means k (diagnostic)    : {result.kmeans_k}"
    )
    print(
        f"Silhouette                : {result.kmeans_silhouette}"
    )
    print(
        f"Cluster profiles          : {profile_path}"
    )
    print(
        f"Manifest                  : {manifest_path}"
    )
    print(
        f"Output root               : {output_root}"
    )


if __name__ == "__main__":

    main()
