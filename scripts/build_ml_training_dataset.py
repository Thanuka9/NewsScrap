from __future__ import annotations

# HUMAN_GOLD_DATASET_BUILDER_V1

import argparse
import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import random
from zoneinfo import ZoneInfo


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

ACTIVE_REVIEW_ROOT = (
    PROJECT_ROOT
    / "data"
    / "review"
    / "active_learning"
)

STAGED_ROOT = (
    PROJECT_ROOT
    / "data"
    / "staged"
)

CORPUS_ROOT = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "ml_corpus"
)

TRAINING_ROOT = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "datasets"
)

HUMAN_GOLD_ROOT = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "human_gold"
)

CATEGORY_GOLD_PATH = (
    HUMAN_GOLD_ROOT
    / "stakeholder_category_gold.csv"
)

SRI_LANKA_TZ = ZoneInfo(
    "Asia/Colombo"
)

VALID_RELEVANCE = {
    "RELEVANT",
    "WATCH",
    "NOISE",
}

PAIR_FILES = (
    "near_duplicate_candidates.csv",
    "same_event_candidates.csv",
    "story_continuations.csv",
    "shared_event_context.csv",
)


# ============================================================
# HELPERS
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


def write_csv(
    path: Path,
    rows: list[dict],
    fieldnames: list[str],
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
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(
            rows
        )


def load_json(
    path: Path,
) -> dict:

    return json.loads(
        path.read_text(
            encoding="utf-8-sig"
        )
    )


def stable_hash(
    value: str,
) -> str:

    return hashlib.sha256(
        value.encode(
            "utf-8"
        )
    ).hexdigest()


def clean_label(
    value,
) -> str:

    return str(
        value
        or ""
    ).strip().upper()


def parse_topics(
    value,
) -> list[str]:

    topics = []

    for item in str(
        value
        or ""
    ).split(";"):

        item = (
            item.strip()
            .upper()
        )

        if (
            item
            and item
            not in topics
        ):

            topics.append(
                item
            )

    return topics


def normalize_url(
    value,
) -> str:

    return str(
        value
        or ""
    ).strip()


# ============================================================
# STAGED ARTICLE INDEX
# ============================================================


def build_article_index() -> dict[
    str,
    dict,
]:
    """
    Index the newest known staged version by both source_url
    and canonical_url.

    We prefer the largest article_version when present.
    If article_version is absent, later path order wins only
    as a deterministic fallback.
    """

    index: dict[
        str,
        dict,
    ] = {}

    if not STAGED_ROOT.exists():
        return index

    for path in sorted(
        STAGED_ROOT.rglob(
            "*.json"
        )
    ):

        try:
            payload = (
                load_json(
                    path
                )
            )
        except Exception:
            continue

        source_url = normalize_url(
            payload.get(
                "source_url"
            )
        )

        canonical_url = normalize_url(
            payload.get(
                "canonical_url"
            )
        )

        try:
            version = int(
                payload.get(
                    "article_version",
                    0,
                )
                or 0
            )
        except (
            TypeError,
            ValueError,
        ):
            version = 0

        record = {
            "payload": payload,
            "path": path,
            "version": version,
        }

        for key in {
            source_url,
            canonical_url,
        }:

            if not key:
                continue

            previous = (
                index.get(
                    key
                )
            )

            if (
                previous is None
                or version
                >= previous[
                    "version"
                ]
            ):

                index[
                    key
                ] = record

    return index


# ============================================================
# STORY GROUPING
# ============================================================


class UnionFind:

    def __init__(
        self,
    ) -> None:

        self.parent = {}

    def find(
        self,
        value: str,
    ) -> str:

        if value not in self.parent:
            self.parent[
                value
            ] = value

        if (
            self.parent[
                value
            ]
            != value
        ):

            self.parent[
                value
            ] = self.find(
                self.parent[
                    value
                ]
            )

        return self.parent[
            value
        ]

    def union(
        self,
        left: str,
        right: str,
    ) -> None:

        left_root = self.find(
            left
        )

        right_root = self.find(
            right
        )

        if (
            left_root
            != right_root
        ):

            # Stable union direction.
            low = min(
                left_root,
                right_root,
            )

            high = max(
                left_root,
                right_root,
            )

            self.parent[
                high
            ] = low


def corpus_window_from_review_name(
    review_path: Path,
) -> tuple[
    str | None,
    int | None,
]:

    # review_batch_2026-09-16_33d.csv
    stem = (
        review_path.stem
    )

    prefix = (
        "review_batch_"
    )

    if not stem.startswith(
        prefix
    ):
        return (
            None,
            None,
        )

    remainder = (
        stem[
            len(
                prefix
            ):
        ]
    )

    parts = (
        remainder.split(
            "_"
        )
    )

    if len(
        parts
    ) < 2:
        return (
            None,
            None,
        )

    end_date = (
        parts[
            0
        ]
    )

    day_token = (
        parts[
            1
        ]
    )

    if not day_token.endswith(
        "d"
    ):
        return (
            None,
            None,
        )

    try:
        days = int(
            day_token[
                :-1
            ]
        )
    except ValueError:
        return (
            None,
            None,
        )

    return (
        end_date,
        days,
    )


def build_story_groups(
    review_path: Path,
    source_urls: list[str],
) -> dict[
    str,
    str,
]:

    uf = UnionFind()

    known_urls = set(
        source_urls
    )

    for url in known_urls:
        uf.find(
            url
        )

    # HUMAN_GOLD_DATASET_BUILDER_V1
    # Use reviewed event links so duplicate news reports remain in one split.
    if CATEGORY_GOLD_PATH.is_file():
        category_rows = read_csv(
            CATEGORY_GOLD_PATH
        )
        event_anchor = {}

        for row in category_rows:
            url = normalize_url(
                row.get(
                    "source_url"
                )
            )

            if url not in known_urls:
                continue

            duplicate_of = normalize_url(
                row.get(
                    "human_duplicate_of_url"
                )
            )

            if (
                duplicate_of
                and duplicate_of in known_urls
            ):
                uf.union(
                    url,
                    duplicate_of,
                )

            event_key = str(
                row.get(
                    "human_event_key"
                )
                or ""
            ).strip().upper()

            if event_key:
                previous = event_anchor.get(
                    event_key
                )

                if previous:
                    uf.union(
                        url,
                        previous,
                    )
                else:
                    event_anchor[
                        event_key
                    ] = url

    (
        end_date,
        days,
    ) = (
        corpus_window_from_review_name(
            review_path
        )
    )

    if (
        end_date
        and days
    ):

        corpus_dir = (
            CORPUS_ROOT
            / end_date
            / f"window_{days}d"
        )

        for file_name in (
            PAIR_FILES
        ):

            path = (
                corpus_dir
                / file_name
            )

            if not path.exists():
                continue

            for row in (
                read_csv(
                    path
                )
            ):

                left = normalize_url(
                    row.get(
                        "left_url"
                    )
                )

                right = normalize_url(
                    row.get(
                        "right_url"
                    )
                )

                if (
                    left in known_urls
                    and right in known_urls
                ):

                    uf.union(
                        left,
                        right,
                    )

    groups = defaultdict(
        list
    )

    for url in (
        sorted(
            known_urls
        )
    ):

        groups[
            uf.find(
                url
            )
        ].append(
            url
        )

    url_to_group = {}

    for members in (
        groups.values()
    ):

        signature = (
            "\n".join(
                sorted(
                    members
                )
            )
        )

        group_id = (
            "story_"
            + stable_hash(
                signature
            )[
                :12
            ]
        )

        for url in members:

            url_to_group[
                url
            ] = (
                group_id
            )

    return (
        url_to_group
    )


# ============================================================
# SPLITTING
# ============================================================


@dataclass
class StoryGroup:

    group_id: str
    rows: list[dict]

    @property
    def size(
        self,
    ) -> int:

        return len(
            self.rows
        )

    @property
    def label_counts(
        self,
    ) -> Counter:

        return Counter(
            row[
                "target_relevance_label"
            ]
            for row in self.rows
        )


def _largest_remainder_targets(
    total: int,
    ratios: dict[str, float],
) -> dict[str, int]:
    """
    Convert fractional split targets into integer row capacities
    while preserving the exact total.

    Example for 36 rows at 70/15/15:
        train=25, validation=6, test=5
    """

    raw = {
        name: (
            total
            * ratio
        )
        for (
            name,
            ratio,
        ) in ratios.items()
    }

    targets = {
        name: int(
            value
        )
        for (
            name,
            value,
        ) in raw.items()
    }

    remainder = (
        total
        - sum(
            targets.values()
        )
    )

    order = sorted(
        ratios,
        key=lambda name: (
            -(
                raw[
                    name
                ]
                - targets[
                    name
                ]
            ),
            (
                0
                if name
                == "validation"
                else (
                    1
                    if name
                    == "test"
                    else 2
                )
            ),
        ),
    )

    for name in (
        order[
            :remainder
        ]
    ):

        targets[
            name
        ] += 1

    return targets


def split_story_groups(
    rows: list[dict],
    *,
    train_ratio: float,
    validation_ratio: float,
    test_ratio: float,
    seed: int,
) -> dict[
    str,
    str,
]:
    """
    Deterministic group-aware, approximately stratified split.

    Guarantees:
      * every story_group_id is assigned to exactly one split;
      * linked stories never leak across train/validation/test;
      * split sizes stay as close as group structure permits to
        the requested ratios;
      * when feasible, every supervised class appears in every
        split (especially rare classes such as RELEVANT);
      * class balance is considered during assignment.
    """

    ratios = {
        "train": float(
            train_ratio
        ),
        "validation": float(
            validation_ratio
        ),
        "test": float(
            test_ratio
        ),
    }

    ratio_total = sum(
        ratios.values()
    )

    if abs(
        ratio_total
        - 1.0
    ) > 1e-9:

        raise ValueError(
            "Train/validation/test ratios "
            "must sum to 1.0"
        )

    grouped = defaultdict(
        list
    )

    for row in rows:

        grouped[
            row[
                "story_group_id"
            ]
        ].append(
            row
        )

    groups = [
        StoryGroup(
            group_id=group_id,
            rows=group_rows,
        )
        for (
            group_id,
            group_rows,
        ) in grouped.items()
    ]

    split_names = (
        "train",
        "validation",
        "test",
    )

    total_rows = len(
        rows
    )

    row_targets = (
        _largest_remainder_targets(
            total_rows,
            ratios,
        )
    )

    total_labels = Counter(
        row[
            "target_relevance_label"
        ]
        for row in rows
    )

    expected_label_counts = {
        split: {
            label: (
                count
                * ratios[
                    split
                ]
            )
            for (
                label,
                count,
            ) in total_labels.items()
        }
        for split in split_names
    }

    label_weights = {
        label: (
            1.0
            / max(
                count,
                1,
            )
        )
        for (
            label,
            count,
        ) in total_labels.items()
    }

    rng = random.Random(
        seed
    )

    decorated = [
        (
            -group.size,
            rng.random(),
            group,
        )
        for group in groups
    ]

    decorated.sort(
        key=lambda item: (
            item[
                0
            ],
            item[
                1
            ],
        )
    )

    groups = [
        item[
            2
        ]
        for item in decorated
    ]

    current_rows = Counter()

    current_labels = {
        split: Counter()
        for split in split_names
    }

    assignment: dict[
        str,
        str,
    ] = {}

    unassigned = {
        group.group_id: group
        for group in groups
    }

    # ========================================================
    # RARE-CLASS COVERAGE SEEDING
    # ========================================================
    #
    # If a class exists in at least 3 independent story groups,
    # place one such group into each split before normal greedy
    # allocation. This prevents cases like RELEVANT disappearing
    # entirely from the test set.
    # ========================================================

    label_to_groups: dict[
        str,
        list[StoryGroup],
    ] = defaultdict(
        list
    )

    for group in groups:

        for label in (
            group.label_counts
        ):

            label_to_groups[
                label
            ].append(
                group
            )

    labels_by_rarity = sorted(
        total_labels,
        key=lambda label: (
            total_labels[
                label
            ],
            label,
        ),
    )

    for label in (
        labels_by_rarity
    ):

        independent_groups = {
            group.group_id
            for group in (
                label_to_groups[
                    label
                ]
            )
        }

        if len(
            independent_groups
        ) < len(
            split_names
        ):

            continue

        for split in (
            "validation",
            "test",
            "train",
        ):

            if (
                current_labels[
                    split
                ][
                    label
                ]
                > 0
            ):

                continue

            candidates = [
                group
                for group in (
                    label_to_groups[
                        label
                    ]
                )
                if (
                    group.group_id
                    in unassigned
                )
                and (
                    current_rows[
                        split
                    ]
                    + group.size
                    <= row_targets[
                        split
                    ]
                )
            ]

            if not candidates:
                continue

            candidates.sort(
                key=lambda group: (
                    group.size,
                    -group.label_counts.get(
                        label,
                        0,
                    ),
                    len(
                        group.label_counts
                    ),
                    group.group_id,
                )
            )

            chosen = (
                candidates[
                    0
                ]
            )

            assignment[
                chosen.group_id
            ] = split

            current_rows[
                split
            ] += (
                chosen.size
            )

            current_labels[
                split
            ].update(
                chosen.label_counts
            )

            del unassigned[
                chosen.group_id
            ]

    # ========================================================
    # GENERAL GROUP-AWARE ALLOCATION
    # ========================================================

    remaining_groups = [
        group
        for group in groups
        if group.group_id
        in unassigned
    ]

    for group in (
        remaining_groups
    ):

        feasible = [
            split
            for split in split_names
            if (
                current_rows[
                    split
                ]
                + group.size
                <= row_targets[
                    split
                ]
            )
        ]

        candidate_splits = (
            feasible
            if feasible
            else list(
                split_names
            )
        )

        best_split = None
        best_key = None

        for split in (
            candidate_splits
        ):

            projected_rows = (
                current_rows[
                    split
                ]
                + group.size
            )

            overflow = max(
                0,
                projected_rows
                - row_targets[
                    split
                ],
            )

            remaining_after = (
                row_targets[
                    split
                ]
                - projected_rows
            )

            class_cost = 0.0

            for label in (
                total_labels
            ):

                projected_label = (
                    current_labels[
                        split
                    ][
                        label
                    ]
                    + group.label_counts.get(
                        label,
                        0,
                    )
                )

                expected = (
                    expected_label_counts[
                        split
                    ][
                        label
                    ]
                )

                diff = (
                    projected_label
                    - expected
                )

                class_cost += (
                    label_weights[
                        label
                    ]
                    * (
                        diff
                        ** 2
                    )
                )

            key = (
                overflow
                * 10000.0,
                class_cost,
                abs(
                    remaining_after
                ),
                (
                    0
                    if split
                    == "train"
                    else (
                        1
                        if split
                        == "validation"
                        else 2
                    )
                ),
            )

            if (
                best_key is None
                or key
                < best_key
            ):

                best_key = key
                best_split = split

        assignment[
            group.group_id
        ] = best_split

        current_rows[
            best_split
        ] += group.size

        current_labels[
            best_split
        ].update(
            group.label_counts
        )

    return assignment


# ============================================================
# MAIN
# ============================================================


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Build a versioned supervisory-news "
            "training dataset from the active-learning "
            "review CSV."
        )
    )

    parser.add_argument(
        "--review-file",
        default=(
            "data/training/human_gold/"
            "supervisory_review_gold.csv"
        ),
        help=(
            "Path to the labelled active-learning "
            "review CSV."
        ),
    )

    parser.add_argument(
        "--dataset-version",
        default="v6_bank_supervision_candidate",
        help=(
            "Dataset version. Default is the human-gold candidate dataset."
        ),
    )

    parser.add_argument(
        "--include-unlabeled",
        action="store_true",
        help=(
            "Include rows without a relevance label. "
            "Unlabelled rows are not assigned to supervised "
            "train/validation/test files."
        ),
    )

    parser.add_argument(
        "--train-ratio",
        type=float,
        default=0.70,
    )

    parser.add_argument(
        "--validation-ratio",
        type=float,
        default=0.15,
    )

    parser.add_argument(
        "--test-ratio",
        type=float,
        default=0.15,
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
    )

    args = (
        parser.parse_args()
    )

    review_path = Path(
        args.review_file
    )

    if not review_path.is_absolute():
        review_path = (
            PROJECT_ROOT
            / review_path
        )

    if not review_path.exists():

        raise FileNotFoundError(
            f"Review file not found: "
            f"{review_path}"
        )

    review_rows = (
        read_csv(
            review_path
        )
    )

    if not review_rows:

        raise RuntimeError(
            "Review CSV is empty."
        )

    # HUMAN_GOLD_DATASET_BUILDER_V1
    # If approval metadata exists, fail closed to approved training rows only.
    if (
        "approved_for_training"
        in review_rows[0]
    ):
        review_rows = [
            row
            for row in review_rows
            if str(
                row.get(
                    "approved_for_training",
                    ""
                )
            ).strip().lower()
            in {
                "true",
                "1",
                "yes",
                "y",
            }
        ]

        if not review_rows:
            raise RuntimeError(
                "No approved_for_training rows were found."
            )

    required_columns = {
        "source_url",
        "human_relevance_label",
        "human_topic_labels",
        "review_status",
    }

    missing_columns = (
        required_columns
        - set(
            review_rows[
                0
            ].keys()
        )
    )

    if missing_columns:

        raise RuntimeError(
            "Review CSV is missing required columns: "
            + ", ".join(
                sorted(
                    missing_columns
                )
            )
        )

    article_index = (
        build_article_index()
    )

    source_urls = [
        normalize_url(
            row.get(
                "source_url"
            )
        )
        for row in review_rows
        if normalize_url(
            row.get(
                "source_url"
            )
        )
    ]

    story_groups = (
        build_story_groups(
            review_path,
            source_urls,
        )
    )

    dataset_rows = []

    skipped_unlabeled = 0
    missing_article_count = 0

    for review_row in (
        review_rows
    ):

        source_url = normalize_url(
            review_row.get(
                "source_url"
            )
        )

        relevance = clean_label(
            review_row.get(
                "human_relevance_label"
            )
        )

        if not relevance:

            if not args.include_unlabeled:

                skipped_unlabeled += 1
                continue

        elif relevance not in VALID_RELEVANCE:

            raise RuntimeError(
                "Invalid relevance label "
                f"{relevance!r} for URL "
                f"{source_url}"
            )

        article_record = (
            article_index.get(
                source_url
            )
        )

        if not article_record:

            missing_article_count += 1
            continue

        article = (
            article_record[
                "payload"
            ]
        )

        article_text = str(
            article.get(
                "article_text",
                ""
            )
            or ""
        ).strip()

        if (
            relevance
            and not article_text
        ):

            raise RuntimeError(
                "Labelled row has no staged article text: "
                f"{source_url}"
            )

        topic_labels = (
            parse_topics(
                review_row.get(
                    "human_topic_labels"
                )
            )
        )

        dataset_rows.append(
            {
                "dataset_row_id": (
                    "row_"
                    + stable_hash(
                        source_url
                    )[
                        :12
                    ]
                ),

                "story_group_id": (
                    story_groups.get(
                        source_url
                    )
                    or (
                        "story_"
                        + stable_hash(
                            source_url
                        )[
                            :12
                        ]
                    )
                ),

                "source_name": (
                    article.get(
                        "source_name"
                    )
                    or review_row.get(
                        "source_name"
                    )
                ),

                "headline": (
                    article.get(
                        "headline"
                    )
                    or review_row.get(
                        "headline"
                    )
                ),

                "article_text": (
                    article_text
                ),

                "published_at": (
                    article.get(
                        "published_at"
                    )
                ),

                "source_url": (
                    source_url
                ),

                "target_relevance_label": (
                    relevance
                ),

                "target_topic_labels": (
                    ";".join(
                        topic_labels
                    )
                ),

                "label_source": (
                    review_row.get(
                        "label_source",
                        ""
                    )
                ),

                "label_confidence": (
                    clean_label(
                        review_row.get(
                            "label_confidence"
                        )
                    )
                ),

                "review_status": (
                    clean_label(
                        review_row.get(
                            "review_status"
                        )
                    )
                ),

                "review_notes": (
                    review_row.get(
                        "human_notes",
                        ""
                    )
                ),

                "stage1_gate_decision": (
                    review_row.get(
                        "stage1_gate_decision",
                        ""
                    )
                ),

                "stage1_financial_score": (
                    review_row.get(
                        "stage1_financial_score",
                        ""
                    )
                ),

                "stage1_pr_score": (
                    review_row.get(
                        "stage1_pr_score",
                        ""
                    )
                ),

                "finbert_sentiment": (
                    review_row.get(
                        "finbert_sentiment",
                        ""
                    )
                ),

                "finbert_sentiment_confidence": (
                    review_row.get(
                        "finbert_sentiment_confidence",
                        ""
                    )
                ),

                "hdbscan_cluster": (
                    review_row.get(
                        "hdbscan_cluster",
                        ""
                    )
                ),

                "is_hdbscan_noise": (
                    review_row.get(
                        "is_hdbscan_noise",
                        ""
                    )
                ),

                "selection_reasons": (
                    review_row.get(
                        "selection_reasons",
                        ""
                    )
                ),

                "primary_entities": (
                    review_row.get(
                        "primary_entities",
                        ""
                    )
                ),

                "all_entities": (
                    review_row.get(
                        "all_entities",
                        ""
                    )
                ),
            }
        )

    labelled_rows = [
        row
        for row in dataset_rows
        if row[
            "target_relevance_label"
        ]
    ]

    if not labelled_rows:

        raise RuntimeError(
            "No labelled rows were found."
        )

    split_assignment = (
        split_story_groups(
            labelled_rows,
            train_ratio=(
                args.train_ratio
            ),
            validation_ratio=(
                args.validation_ratio
            ),
            test_ratio=(
                args.test_ratio
            ),
            seed=(
                args.seed
            ),
        )
    )

    for row in (
        dataset_rows
    ):

        if row[
            "target_relevance_label"
        ]:

            row[
                "split"
            ] = (
                split_assignment[
                    row[
                        "story_group_id"
                    ]
                ]
            )

        else:

            row[
                "split"
            ] = (
                "unlabeled"
            )

    # Leakage assertion.
    group_splits = defaultdict(
        set
    )

    for row in (
        labelled_rows
    ):

        split = (
            split_assignment[
                row[
                    "story_group_id"
                ]
            ]
        )

        group_splits[
            row[
                "story_group_id"
            ]
        ].add(
            split
        )

    leakage_groups = {
        group_id: splits
        for (
            group_id,
            splits,
        ) in group_splits.items()
        if len(
            splits
        ) > 1
    }

    if leakage_groups:

        raise RuntimeError(
            "Story-group leakage detected: "
            f"{leakage_groups}"
        )

    dataset_name = (
        "supervisory_relevance_"
        + args.dataset_version
    )

    output_root = (
        TRAINING_ROOT
        / dataset_name
    )

    output_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    fieldnames = [
        "dataset_row_id",
        "story_group_id",
        "split",
        "source_name",
        "headline",
        "article_text",
        "published_at",
        "source_url",
        "target_relevance_label",
        "target_topic_labels",
        "label_source",
        "label_confidence",
        "review_status",
        "review_notes",
        "stage1_gate_decision",
        "stage1_financial_score",
        "stage1_pr_score",
        "finbert_sentiment",
        "finbert_sentiment_confidence",
        "hdbscan_cluster",
        "is_hdbscan_noise",
        "selection_reasons",
        "primary_entities",
        "all_entities",
    ]

    dataset_path = (
        output_root
        / "dataset.csv"
    )

    write_csv(
        dataset_path,
        dataset_rows,
        fieldnames,
    )

    split_counts = {}

    for split in (
        "train",
        "validation",
        "test",
    ):

        split_rows = [
            row
            for row in dataset_rows
            if row[
                "split"
            ]
            == split
        ]

        write_csv(
            output_root
            / f"{split}.csv",
            split_rows,
            fieldnames,
        )

        split_counts[
            split
        ] = (
            len(
                split_rows
            )
        )

    label_counts = Counter(
        row[
            "target_relevance_label"
        ]
        for row in labelled_rows
    )

    split_label_counts = {}

    for split in (
        "train",
        "validation",
        "test",
    ):

        split_label_counts[
            split
        ] = dict(
            Counter(
                row[
                    "target_relevance_label"
                ]
                for row in dataset_rows
                if row[
                    "split"
                ]
                == split
            )
        )

    story_group_count = len(
        {
            row[
                "story_group_id"
            ]
            for row in labelled_rows
        }
    )

    provenance_counts = dict(
        Counter(
            row[
                "label_source"
            ]
            or "UNSPECIFIED"
            for row in labelled_rows
        )
    )

    confidence_counts = dict(
        Counter(
            row[
                "label_confidence"
            ]
            or "UNSPECIFIED"
            for row in labelled_rows
        )
    )

    review_status_counts = dict(
        Counter(
            row[
                "review_status"
            ]
            or "UNSPECIFIED"
            for row in labelled_rows
        )
    )

    manifest = {
        "status": "SUCCESS",

        "created_at": (
            datetime.now(
                SRI_LANKA_TZ
            )
            .isoformat()
        ),

        "dataset_name": (
            dataset_name
        ),

        "dataset_version": (
            args.dataset_version
        ),

        "source_review_file": str(
            review_path.relative_to(
                PROJECT_ROOT
            )
        ),

        "dataset_file": str(
            dataset_path.relative_to(
                PROJECT_ROOT
            )
        ),

        "row_count": (
            len(
                dataset_rows
            )
        ),

        "labelled_row_count": (
            len(
                labelled_rows
            )
        ),

        "skipped_unlabeled_count": (
            skipped_unlabeled
        ),

        "missing_staged_article_count": (
            missing_article_count
        ),

        "story_group_count": (
            story_group_count
        ),

        "story_group_leakage_count": (
            len(
                leakage_groups
            )
        ),

        "label_counts": (
            dict(
                label_counts
            )
        ),

        "label_source_counts": (
            provenance_counts
        ),

        "label_confidence_counts": (
            confidence_counts
        ),

        "review_status_counts": (
            review_status_counts
        ),

        "split_seed": (
            args.seed
        ),

        "split_ratios": {
            "train": (
                args.train_ratio
            ),
            "validation": (
                args.validation_ratio
            ),
            "test": (
                args.test_ratio
            ),
        },

        "split_counts": (
            split_counts
        ),

        "split_label_counts": (
            split_label_counts
        ),

        "files": {
            "train": str(
                (
                    output_root
                    / "train.csv"
                )
                .relative_to(
                    PROJECT_ROOT
                )
            ),

            "validation": str(
                (
                    output_root
                    / "validation.csv"
                )
                .relative_to(
                    PROJECT_ROOT
                )
            ),

            "test": str(
                (
                    output_root
                    / "test.csv"
                )
                .relative_to(
                    PROJECT_ROOT
                )
            ),
        },

        "training_readiness": {
            "provisional_only": any(
                (
                    row[
                        "label_source"
                    ]
                    or ""
                )
                .upper()
                .find(
                    "PROVISIONAL"
                )
                >= 0
                for row in labelled_rows
            ),

            "note": (
                "This dataset may be used for pipeline validation "
                "and weak-label/bootstrap experiments. "
                "Do not represent AI-assisted provisional labels "
                "as independently human-verified gold labels."
            ),
        },
    }

    manifest_path = (
        output_root
        / "manifest.json"
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

    print("=" * 80)

    print(
        "SUPERVISORY ML DATASET "
        "BUILD COMPLETE"
    )

    print("=" * 80)

    print(
        f"Review source        : "
        f"{review_path}"
    )

    print(
        f"Review rows          : "
        f"{len(review_rows)}"
    )

    print(
        f"Dataset rows         : "
        f"{len(dataset_rows)}"
    )

    print(
        f"Labelled rows        : "
        f"{len(labelled_rows)}"
    )

    print(
        f"Story groups         : "
        f"{story_group_count}"
    )

    print(
        f"Leakage groups       : "
        f"{len(leakage_groups)}"
    )

    print(
        f"Label counts         : "
        f"{dict(label_counts)}"
    )

    print(
        f"Train rows           : "
        f"{split_counts['train']}"
    )

    print(
        f"Validation rows      : "
        f"{split_counts['validation']}"
    )

    print(
        f"Test rows            : "
        f"{split_counts['test']}"
    )

    print(
        f"Output root          : "
        f"{output_root}"
    )

    print(
        f"Manifest             : "
        f"{manifest_path}"
    )

    if manifest[
        "training_readiness"
    ][
        "provisional_only"
    ]:

        print()

        print(
            "NOTE: AI-assisted provisional labels "
            "are present. Treat this as a bootstrap "
            "dataset, not final human-gold training data."
        )


if __name__ == "__main__":

    main()
