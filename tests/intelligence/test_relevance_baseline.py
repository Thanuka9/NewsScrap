from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[2]
)

SCRIPTS_ROOT = (
    PROJECT_ROOT
    / "scripts"
)

if str(
    SCRIPTS_ROOT
) not in sys.path:
    sys.path.insert(
        0,
        str(
            SCRIPTS_ROOT
        ),
    )


from run_relevance_baseline import (  # noqa: E402
    assert_no_story_leakage,
    dataset_signature,
    evaluate_predictions,
    select_logistic_c,
)


def test_dataset_signature_is_stable_to_row_order():

    rows_a = [
        {
            "source_url": "b",
            "headline": "B",
            "article_text": "body b",
        },
        {
            "source_url": "a",
            "headline": "A",
            "article_text": "body a",
        },
    ]

    rows_b = list(
        reversed(
            rows_a
        )
    )

    assert (
        dataset_signature(
            rows_a
        )
        == dataset_signature(
            rows_b
        )
    )


def test_story_leakage_detection():

    train = [
        {
            "story_group_id": "g1"
        }
    ]

    validation = [
        {
            "story_group_id": "g2"
        }
    ]

    test = [
        {
            "story_group_id": "g3"
        }
    ]

    assert_no_story_leakage(
        train,
        validation,
        test,
    )


def test_metrics_include_relevant_class():

    y_true = np.asarray(
        [
            "NOISE",
            "WATCH",
            "RELEVANT",
            "RELEVANT",
        ],
        dtype=object,
    )

    y_pred = np.asarray(
        [
            "NOISE",
            "WATCH",
            "RELEVANT",
            "WATCH",
        ],
        dtype=object,
    )

    metrics = evaluate_predictions(
        y_true,
        y_pred,
    )

    assert (
        "RELEVANT"
        in metrics[
            "per_class"
        ]
    )

    assert (
        0.0
        <= metrics[
            "macro_f1"
        ]
        <= 1.0
    )


def test_logistic_c_selection_runs():

    train_x = np.asarray(
        [
            [1.0, 0.0],
            [0.9, 0.1],
            [0.0, 1.0],
            [0.1, 0.9],
            [-1.0, -1.0],
            [-0.9, -1.1],
        ],
        dtype=np.float32,
    )

    train_y = np.asarray(
        [
            "NOISE",
            "NOISE",
            "WATCH",
            "WATCH",
            "RELEVANT",
            "RELEVANT",
        ],
        dtype=object,
    )

    validation_x = np.asarray(
        [
            [1.1, 0.0],
            [0.0, 1.1],
            [-1.0, -0.8],
        ],
        dtype=np.float32,
    )

    validation_y = np.asarray(
        [
            "NOISE",
            "WATCH",
            "RELEVANT",
        ],
        dtype=object,
    )

    selected_c, results = (
        select_logistic_c(
            train_x,
            train_y,
            validation_x,
            validation_y,
        )
    )

    assert (
        selected_c
        in {
            result[
                "C"
            ]
            for result in results
        }
    )

    assert (
        len(
            results
        )
        > 0
    )
