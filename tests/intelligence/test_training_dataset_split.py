from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path


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


from build_ml_training_dataset import (  # noqa: E402
    split_story_groups,
)


def _make_rows():

    rows = []

    labels = (
        ["NOISE"] * 20
        + ["WATCH"] * 11
        + ["RELEVANT"] * 5
    )

    group_ids = []

    for i in range(
        30
    ):
        group_ids.append(
            f"single_{i:02d}"
        )

    group_ids.extend(
        [
            "pair_a",
            "pair_a",
            "pair_b",
            "pair_b",
            "pair_c",
            "pair_c",
        ]
    )

    assert len(
        group_ids
    ) == 36

    for (
        group_id,
        label,
    ) in zip(
        group_ids,
        labels,
    ):

        rows.append(
            {
                "story_group_id": (
                    group_id
                ),
                "target_relevance_label": (
                    label
                ),
            }
        )

    return rows


def _counts_by_split(
    rows,
    assignment,
):

    result = {
        split: Counter()
        for split in (
            "train",
            "validation",
            "test",
        )
    }

    for row in rows:

        split = (
            assignment[
                row[
                    "story_group_id"
                ]
            ]
        )

        result[
            split
        ][
            row[
                "target_relevance_label"
            ]
        ] += 1

    return result


def test_group_split_prevents_story_leakage():

    rows = (
        _make_rows()
    )

    assignment = (
        split_story_groups(
            rows,
            train_ratio=0.70,
            validation_ratio=0.15,
            test_ratio=0.15,
            seed=42,
        )
    )

    group_splits = defaultdict(
        set
    )

    for row in rows:

        group_splits[
            row[
                "story_group_id"
            ]
        ].add(
            assignment[
                row[
                    "story_group_id"
                ]
            ]
        )

    assert all(
        len(
            splits
        )
        == 1
        for splits in (
            group_splits.values()
        )
    )


def test_split_sizes_respect_requested_ratios():

    rows = (
        _make_rows()
    )

    assignment = (
        split_story_groups(
            rows,
            train_ratio=0.70,
            validation_ratio=0.15,
            test_ratio=0.15,
            seed=42,
        )
    )

    counts = Counter(
        assignment[
            row[
                "story_group_id"
            ]
        ]
        for row in rows
    )

    assert (
        counts[
            "train"
        ]
        == 25
    )

    assert {
        counts[
            "validation"
        ],
        counts[
            "test"
        ],
    } == {
        5,
        6,
    }


def test_relevant_class_present_in_every_split():

    rows = (
        _make_rows()
    )

    assignment = (
        split_story_groups(
            rows,
            train_ratio=0.70,
            validation_ratio=0.15,
            test_ratio=0.15,
            seed=42,
        )
    )

    counts = (
        _counts_by_split(
            rows,
            assignment,
        )
    )

    for split in (
        "train",
        "validation",
        "test",
    ):

        assert (
            counts[
                split
            ][
                "RELEVANT"
            ]
            >= 1
        )


def test_all_three_classes_present_when_feasible():

    rows = (
        _make_rows()
    )

    assignment = (
        split_story_groups(
            rows,
            train_ratio=0.70,
            validation_ratio=0.15,
            test_ratio=0.15,
            seed=42,
        )
    )

    counts = (
        _counts_by_split(
            rows,
            assignment,
        )
    )

    for split in (
        "train",
        "validation",
        "test",
    ):

        assert (
            counts[
                split
            ][
                "RELEVANT"
            ]
            >= 1
        )

        assert (
            counts[
                split
            ][
                "WATCH"
            ]
            >= 1
        )

        assert (
            counts[
                split
            ][
                "NOISE"
            ]
            >= 1
        )


def test_split_is_deterministic():

    rows = (
        _make_rows()
    )

    first = (
        split_story_groups(
            rows,
            train_ratio=0.70,
            validation_ratio=0.15,
            test_ratio=0.15,
            seed=42,
        )
    )

    second = (
        split_story_groups(
            rows,
            train_ratio=0.70,
            validation_ratio=0.15,
            test_ratio=0.15,
            seed=42,
        )
    )

    assert (
        first
        == second
    )
