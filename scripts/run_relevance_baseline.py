from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import pickle
from typing import Iterable

import numpy as np


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

SRC_ROOT = (
    PROJECT_ROOT
    / "src"
)

DEFAULT_DATASET_ROOT = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "datasets"
    / "supervisory_relevance_v2_temporal"
)

DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "relevance_baseline"
    / "supervisory_relevance_v2_temporal"
)

LABEL_ORDER = [
    "NOISE",
    "WATCH",
    "RELEVANT",
]

C_GRID = [
    0.01,
    0.03,
    0.1,
    0.3,
    1.0,
    3.0,
    10.0,
]


# ============================================================
# DATA
# ============================================================


def read_csv(
    path: Path,
) -> list[dict]:

    with path.open(
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as file:

        return list(
            csv.DictReader(
                file
            )
        )


def dataset_signature(
    rows: Iterable[dict],
) -> str:
    """
    Stable content signature for embedding cache validation.
    """

    digest = hashlib.sha256()

    for row in sorted(
        rows,
        key=lambda value: str(
            value.get(
                "source_url",
                "",
            )
        ),
    ):

        for field in (
            "source_url",
            "headline",
            "article_text",
        ):

            digest.update(
                str(
                    row.get(
                        field,
                        "",
                    )
                    or ""
                ).encode(
                    "utf-8"
                )
            )

            digest.update(
                b"\0"
            )

    return digest.hexdigest()


def validate_rows(
    rows: list[dict],
    *,
    split_name: str,
) -> None:

    if not rows:

        raise RuntimeError(
            f"{split_name} dataset is empty."
        )

    required = {
        "source_url",
        "headline",
        "article_text",
        "target_relevance_label",
        "story_group_id",
    }

    missing = (
        required
        - set(
            rows[
                0
            ].keys()
        )
    )

    if missing:

        raise RuntimeError(
            f"{split_name} dataset is missing columns: "
            + ", ".join(
                sorted(
                    missing
                )
            )
        )

    invalid_labels = sorted(
        {
            str(
                row.get(
                    "target_relevance_label",
                    "",
                )
                or ""
            ).strip().upper()
            for row in rows
        }
        - set(
            LABEL_ORDER
        )
    )

    if invalid_labels:

        raise RuntimeError(
            f"{split_name} has invalid labels: "
            f"{invalid_labels}"
        )


def assert_no_story_leakage(
    train_rows: list[dict],
    validation_rows: list[dict],
    test_rows: list[dict],
) -> None:

    split_groups = {
        "train": {
            row[
                "story_group_id"
            ]
            for row in train_rows
        },
        "validation": {
            row[
                "story_group_id"
            ]
            for row in validation_rows
        },
        "test": {
            row[
                "story_group_id"
            ]
            for row in test_rows
        },
    }

    overlaps = {
        "train_validation": (
            split_groups[
                "train"
            ]
            & split_groups[
                "validation"
            ]
        ),
        "train_test": (
            split_groups[
                "train"
            ]
            & split_groups[
                "test"
            ]
        ),
        "validation_test": (
            split_groups[
                "validation"
            ]
            & split_groups[
                "test"
            ]
        ),
    }

    bad = {
        name: sorted(
            values
        )
        for (
            name,
            values,
        ) in overlaps.items()
        if values
    }

    if bad:

        raise RuntimeError(
            "Story-group leakage detected: "
            f"{bad}"
        )


# ============================================================
# EMBEDDINGS
# ============================================================


def load_engine():
    """
    Import lazily so --help and unit tests do not require
    torch/transformers to import the script.
    """

    import sys

    src_text = str(
        SRC_ROOT
    )

    if src_text not in sys.path:

        sys.path.insert(
            0,
            src_text,
        )

    from bank_intel.intelligence.finbert_engine import (  # type: ignore
        FinBertEngine,
    )

    return FinBertEngine()


def cache_path_for_split(
    output_root: Path,
    split_name: str,
) -> Path:

    return (
        output_root
        / "embeddings"
        / f"{split_name}.npz"
    )


def generate_embeddings(
    rows: list[dict],
    *,
    split_name: str,
    output_root: Path,
    refresh: bool,
    engine_holder: dict,
) -> tuple[
    np.ndarray,
    list[dict],
]:
    """
    Generate or load frozen FinBERT embeddings.

    Metadata saved alongside vectors:
      - source_url
      - model_name
      - dataset signature
      - article order
    """

    cache_path = (
        cache_path_for_split(
            output_root,
            split_name,
        )
    )

    signature = (
        dataset_signature(
            rows
        )
    )

    if (
        cache_path.exists()
        and not refresh
    ):

        cached = np.load(
            cache_path,
            allow_pickle=True,
        )

        cached_signature = str(
            cached[
                "dataset_signature"
            ].item()
        )

        cached_urls = [
            str(
                item
            )
            for item in (
                cached[
                    "source_urls"
                ].tolist()
            )
        ]

        expected_urls = [
            str(
                row[
                    "source_url"
                ]
            )
            for row in rows
        ]

        if (
            cached_signature
            == signature
            and cached_urls
            == expected_urls
        ):

            embeddings = (
                cached[
                    "embeddings"
                ]
                .astype(
                    np.float32,
                    copy=False,
                )
            )

            metadata = [
                {
                    "source_url": (
                        row[
                            "source_url"
                        ]
                    ),
                    "cached": True,
                }
                for row in rows
            ]

            return (
                embeddings,
                metadata,
            )

    if (
        "engine"
        not in engine_holder
    ):

        engine_holder[
            "engine"
        ] = (
            load_engine()
        )

    engine = (
        engine_holder[
            "engine"
        ]
    )

    vectors = []
    metadata = []

    total = len(
        rows
    )

    for index, row in enumerate(
        rows,
        start=1,
    ):

        headline = str(
            row.get(
                "headline",
                "",
            )
            or ""
        ).strip()

        article_text = str(
            row.get(
                "article_text",
                "",
            )
            or ""
        ).strip()

        analysis, embedding = (
            engine.analyse(
                headline=headline,
                article_text=article_text,
            )
        )

        vectors.append(
            np.asarray(
                embedding,
                dtype=np.float32,
            )
        )

        metadata.append(
            {
                "source_url": (
                    row[
                        "source_url"
                    ]
                ),
                "model_name": (
                    analysis.model_name
                ),
                "device": (
                    analysis.device
                ),
                "headline_chunks": (
                    analysis.headline_chunks
                ),
                "body_chunks": (
                    analysis.body_chunks
                ),
                "sentiment_label": (
                    analysis.sentiment_label
                ),
                "sentiment_confidence": (
                    analysis.sentiment_confidence
                ),
                "cached": False,
            }
        )

        print(
            f"[{split_name}] "
            f"{index}/{total} "
            f"{headline[:90]}"
        )

    embeddings = np.vstack(
        vectors
    ).astype(
        np.float32,
        copy=False,
    )

    cache_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    np.savez_compressed(
        cache_path,
        embeddings=embeddings,
        source_urls=np.asarray(
            [
                row[
                    "source_url"
                ]
                for row in rows
            ],
            dtype=object,
        ),
        dataset_signature=np.asarray(
            signature,
            dtype=object,
        ),
        model_name=np.asarray(
            engine.model_name,
            dtype=object,
        ),
    )

    return (
        embeddings,
        metadata,
    )


# ============================================================
# MODEL / METRICS
# ============================================================


def labels_from_rows(
    rows: list[dict],
) -> np.ndarray:

    return np.asarray(
        [
            str(
                row[
                    "target_relevance_label"
                ]
            )
            .strip()
            .upper()
            for row in rows
        ],
        dtype=object,
    )


def evaluate_predictions(
    y_true: np.ndarray,
    y_pred: np.ndarray,
) -> dict:

    from sklearn.metrics import (
        accuracy_score,
        balanced_accuracy_score,
        classification_report,
        confusion_matrix,
        f1_score,
        precision_recall_fscore_support,
    )

    report = classification_report(
        y_true,
        y_pred,
        labels=LABEL_ORDER,
        output_dict=True,
        zero_division=0,
    )

    confusion = confusion_matrix(
        y_true,
        y_pred,
        labels=LABEL_ORDER,
    )

    precision, recall, f1, support = (
        precision_recall_fscore_support(
            y_true,
            y_pred,
            labels=LABEL_ORDER,
            zero_division=0,
        )
    )

    per_class = {}

    for (
        index,
        label,
    ) in enumerate(
        LABEL_ORDER
    ):

        per_class[
            label
        ] = {
            "precision": float(
                precision[
                    index
                ]
            ),
            "recall": float(
                recall[
                    index
                ]
            ),
            "f1": float(
                f1[
                    index
                ]
            ),
            "support": int(
                support[
                    index
                ]
            ),
        }

    return {
        "accuracy": float(
            accuracy_score(
                y_true,
                y_pred,
            )
        ),
        "balanced_accuracy": float(
            balanced_accuracy_score(
                y_true,
                y_pred,
            )
        ),
        "macro_f1": float(
            f1_score(
                y_true,
                y_pred,
                labels=LABEL_ORDER,
                average="macro",
                zero_division=0,
            )
        ),
        "per_class": (
            per_class
        ),
        "classification_report": (
            report
        ),
        "confusion_matrix": (
            confusion.tolist()
        ),
        "label_order": (
            LABEL_ORDER
        ),
    }


def select_logistic_c(
    train_x: np.ndarray,
    train_y: np.ndarray,
    validation_x: np.ndarray,
    validation_y: np.ndarray,
) -> tuple[
    float,
    list[dict],
]:
    """
    Select C using validation macro-F1 only.
    Test data is never used here.
    """

    from sklearn.linear_model import (
        LogisticRegression,
    )

    results = []

    best_c = None
    best_key = None

    for c_value in (
        C_GRID
    ):

        model = LogisticRegression(
            C=c_value,
            class_weight="balanced",
            max_iter=5000,
            random_state=42,
            solver="lbfgs",
        )

        model.fit(
            train_x,
            train_y,
        )

        predictions = (
            model.predict(
                validation_x
            )
        )

        metrics = (
            evaluate_predictions(
                validation_y,
                predictions,
            )
        )

        relevant_f1 = (
            metrics[
                "per_class"
            ][
                "RELEVANT"
            ][
                "f1"
            ]
        )

        result = {
            "C": (
                c_value
            ),
            "macro_f1": (
                metrics[
                    "macro_f1"
                ]
            ),
            "balanced_accuracy": (
                metrics[
                    "balanced_accuracy"
                ]
            ),
            "relevant_f1": (
                relevant_f1
            ),
        }

        results.append(
            result
        )

        # Primary objective: macro F1.
        # Tie-breakers: RELEVANT F1, balanced accuracy,
        # then smaller C for the simpler regularized model.
        key = (
            result[
                "macro_f1"
            ],
            result[
                "relevant_f1"
            ],
            result[
                "balanced_accuracy"
            ],
            -float(
                c_value
            ),
        )

        if (
            best_key is None
            or key
            > best_key
        ):

            best_key = key
            best_c = (
                c_value
            )

    return (
        float(
            best_c
        ),
        results,
    )


def majority_baseline(
    train_y: np.ndarray,
    target_y: np.ndarray,
) -> dict:

    counts = {
        label: int(
            np.sum(
                train_y
                == label
            )
        )
        for label in LABEL_ORDER
    }

    majority_label = max(
        counts,
        key=counts.get,
    )

    predictions = np.asarray(
        [
            majority_label
        ]
        * len(
            target_y
        ),
        dtype=object,
    )

    metrics = (
        evaluate_predictions(
            target_y,
            predictions,
        )
    )

    metrics[
        "majority_label"
    ] = (
        majority_label
    )

    return (
        metrics
    )


# ============================================================
# OUTPUT
# ============================================================


def write_prediction_csv(
    path: Path,
    rows: list[dict],
    y_pred: np.ndarray,
    probabilities: np.ndarray,
    classes: list[str],
) -> None:

    probability_index = {
        label: index
        for (
            index,
            label,
        ) in enumerate(
            classes
        )
    }

    fieldnames = [
        "published_at",
        "source_name",
        "headline",
        "source_url",
        "story_group_id",
        "true_label",
        "predicted_label",
        "p_noise",
        "p_watch",
        "p_relevant",
        "correct",
    ]

    with path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )

        writer.writeheader()

        for (
            row,
            predicted,
            probability_row,
        ) in zip(
            rows,
            y_pred,
            probabilities,
        ):

            true_label = str(
                row[
                    "target_relevance_label"
                ]
            ).strip().upper()

            writer.writerow(
                {
                    "published_at": (
                        row.get(
                            "published_at",
                            "",
                        )
                    ),
                    "source_name": (
                        row.get(
                            "source_name",
                            "",
                        )
                    ),
                    "headline": (
                        row.get(
                            "headline",
                            "",
                        )
                    ),
                    "source_url": (
                        row.get(
                            "source_url",
                            "",
                        )
                    ),
                    "story_group_id": (
                        row.get(
                            "story_group_id",
                            "",
                        )
                    ),
                    "true_label": (
                        true_label
                    ),
                    "predicted_label": (
                        predicted
                    ),
                    "p_noise": float(
                        probability_row[
                            probability_index[
                                "NOISE"
                            ]
                        ]
                    ),
                    "p_watch": float(
                        probability_row[
                            probability_index[
                                "WATCH"
                            ]
                        ]
                    ),
                    "p_relevant": float(
                        probability_row[
                            probability_index[
                                "RELEVANT"
                            ]
                        ]
                    ),
                    "correct": (
                        true_label
                        == predicted
                    ),
                }
            )


# ============================================================
# MAIN
# ============================================================


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Frozen-FinBERT embedding baseline for "
            "RELEVANT/WATCH/NOISE classification."
        )
    )

    parser.add_argument(
        "--dataset-root",
        default=str(
            DEFAULT_DATASET_ROOT
        ),
        help=(
            "Temporal benchmark directory containing "
            "train.csv, validation.csv and test.csv."
        ),
    )

    parser.add_argument(
        "--output-root",
        default=str(
            DEFAULT_OUTPUT_ROOT
        ),
    )

    parser.add_argument(
        "--refresh-embeddings",
        action="store_true",
        help=(
            "Ignore cached FinBERT embeddings and "
            "recompute all splits."
        ),
    )

    args = (
        parser.parse_args()
    )

    dataset_root = Path(
        args.dataset_root
    )

    if not dataset_root.is_absolute():

        dataset_root = (
            PROJECT_ROOT
            / dataset_root
        )

    output_root = Path(
        args.output_root
    )

    if not output_root.is_absolute():

        output_root = (
            PROJECT_ROOT
            / output_root
        )

    output_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    train_rows = read_csv(
        dataset_root
        / "train.csv"
    )

    validation_rows = read_csv(
        dataset_root
        / "validation.csv"
    )

    test_rows = read_csv(
        dataset_root
        / "test.csv"
    )

    validate_rows(
        train_rows,
        split_name="train",
    )

    validate_rows(
        validation_rows,
        split_name="validation",
    )

    validate_rows(
        test_rows,
        split_name="test",
    )

    assert_no_story_leakage(
        train_rows,
        validation_rows,
        test_rows,
    )

    engine_holder = {}

    train_x, _ = generate_embeddings(
        train_rows,
        split_name="train",
        output_root=output_root,
        refresh=(
            args.refresh_embeddings
        ),
        engine_holder=engine_holder,
    )

    validation_x, _ = generate_embeddings(
        validation_rows,
        split_name="validation",
        output_root=output_root,
        refresh=(
            args.refresh_embeddings
        ),
        engine_holder=engine_holder,
    )

    test_x, _ = generate_embeddings(
        test_rows,
        split_name="test",
        output_root=output_root,
        refresh=(
            args.refresh_embeddings
        ),
        engine_holder=engine_holder,
    )

    train_y = labels_from_rows(
        train_rows
    )

    validation_y = labels_from_rows(
        validation_rows
    )

    test_y = labels_from_rows(
        test_rows
    )

    selected_c, validation_grid = (
        select_logistic_c(
            train_x,
            train_y,
            validation_x,
            validation_y,
        )
    )

    from sklearn.linear_model import (
        LogisticRegression,
    )

    # IMPORTANT:
    # Fit only on TRAIN for the first locked test evaluation.
    # Validation was used to select C and is not merged back
    # before this test result.
    model = LogisticRegression(
        C=selected_c,
        class_weight="balanced",
        max_iter=5000,
        random_state=42,
        solver="lbfgs",
    )

    model.fit(
        train_x,
        train_y,
    )

    validation_pred = (
        model.predict(
            validation_x
        )
    )

    validation_prob = (
        model.predict_proba(
            validation_x
        )
    )

    test_pred = (
        model.predict(
            test_x
        )
    )

    test_prob = (
        model.predict_proba(
            test_x
        )
    )

    validation_metrics = (
        evaluate_predictions(
            validation_y,
            validation_pred,
        )
    )

    test_metrics = (
        evaluate_predictions(
            test_y,
            test_pred,
        )
    )

    majority_validation = (
        majority_baseline(
            train_y,
            validation_y,
        )
    )

    majority_test = (
        majority_baseline(
            train_y,
            test_y,
        )
    )

    prediction_dir = (
        output_root
        / "predictions"
    )

    prediction_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    write_prediction_csv(
        prediction_dir
        / "validation_predictions.csv",
        validation_rows,
        validation_pred,
        validation_prob,
        list(
            model.classes_
        ),
    )

    write_prediction_csv(
        prediction_dir
        / "test_predictions.csv",
        test_rows,
        test_pred,
        test_prob,
        list(
            model.classes_
        ),
    )

    model_path = (
        output_root
        / "logistic_regression.pkl"
    )

    with model_path.open(
        "wb"
    ) as file:

        pickle.dump(
            model,
            file,
        )

    result = {
        "status": "SUCCESS",
        "experiment": (
            "frozen_finbert_logistic_regression"
        ),
        "dataset_root": str(
            dataset_root
        ),
        "embedding_model": (
            "ProsusAI/finbert"
        ),
        "classifier": (
            "LogisticRegression"
        ),
        "class_weight": (
            "balanced"
        ),
        "label_order": (
            LABEL_ORDER
        ),
        "train_rows": len(
            train_rows
        ),
        "validation_rows": len(
            validation_rows
        ),
        "test_rows": len(
            test_rows
        ),
        "selected_C": (
            selected_c
        ),
        "validation_grid": (
            validation_grid
        ),
        "validation_metrics": (
            validation_metrics
        ),
        "test_metrics": (
            test_metrics
        ),
        "majority_baseline_validation": (
            majority_validation
        ),
        "majority_baseline_test": (
            majority_test
        ),
        "notes": [
            (
                "FinBERT is frozen; only the "
                "logistic-regression classifier is trained."
            ),
            (
                "Hyperparameter C is selected on validation "
                "macro-F1 only."
            ),
            (
                "Test data is not used for model selection."
            ),
            (
                "Current labels are predominantly AI-assisted "
                "provisional labels, not independently "
                "human-verified gold labels."
            ),
        ],
    }

    result_path = (
        output_root
        / "metrics.json"
    )

    result_path.write_text(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print()
    print("=" * 80)
    print(
        "FROZEN FINBERT RELEVANCE BASELINE COMPLETE"
    )
    print("=" * 80)
    print(
        f"Train rows                 : "
        f"{len(train_rows)}"
    )
    print(
        f"Validation rows            : "
        f"{len(validation_rows)}"
    )
    print(
        f"Test rows                  : "
        f"{len(test_rows)}"
    )
    print(
        f"Embedding dimension        : "
        f"{train_x.shape[1]}"
    )
    print(
        f"Selected C                 : "
        f"{selected_c}"
    )
    print()
    print(
        "VALIDATION"
    )
    print(
        f"  Macro F1                 : "
        f"{validation_metrics['macro_f1']:.4f}"
    )
    print(
        f"  Balanced accuracy        : "
        f"{validation_metrics['balanced_accuracy']:.4f}"
    )
    print(
        f"  RELEVANT F1              : "
        f"{validation_metrics['per_class']['RELEVANT']['f1']:.4f}"
    )
    print()
    print(
        "TEST"
    )
    print(
        f"  Macro F1                 : "
        f"{test_metrics['macro_f1']:.4f}"
    )
    print(
        f"  Balanced accuracy        : "
        f"{test_metrics['balanced_accuracy']:.4f}"
    )
    print(
        f"  RELEVANT precision       : "
        f"{test_metrics['per_class']['RELEVANT']['precision']:.4f}"
    )
    print(
        f"  RELEVANT recall          : "
        f"{test_metrics['per_class']['RELEVANT']['recall']:.4f}"
    )
    print(
        f"  RELEVANT F1              : "
        f"{test_metrics['per_class']['RELEVANT']['f1']:.4f}"
    )
    print()
    print(
        "MAJORITY BASELINE — TEST"
    )
    print(
        f"  Macro F1                 : "
        f"{majority_test['macro_f1']:.4f}"
    )
    print(
        f"  Balanced accuracy        : "
        f"{majority_test['balanced_accuracy']:.4f}"
    )
    print()
    print(
        f"Metrics                    : "
        f"{result_path}"
    )
    print(
        f"Test predictions           : "
        f"{prediction_dir / 'test_predictions.csv'}"
    )
    print(
        f"Model                      : "
        f"{model_path}"
    )


if __name__ == "__main__":
    main()
