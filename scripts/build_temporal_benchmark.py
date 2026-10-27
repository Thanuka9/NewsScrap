from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

SRI_LANKA_TZ = ZoneInfo(
    "Asia/Colombo"
)


def read_csv(
    path: Path,
) -> tuple[
    list[dict],
    list[str],
]:
    with path.open(
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fieldnames = list(
            reader.fieldnames
            or []
        )
    return rows, fieldnames


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
        writer.writerows(rows)


def parse_date(
    value,
):
    text = str(
        value
        or ""
    ).strip()

    if not text:
        raise ValueError(
            "Missing published_at"
        )

    # Support ISO datetimes with timezone and plain dates.
    normalized = text.replace(
        "Z",
        "+00:00",
    )

    try:
        return (
            datetime.fromisoformat(
                normalized
            )
            .date()
        )
    except ValueError:
        return (
            datetime.strptime(
                text[:10],
                "%Y-%m-%d",
            )
            .date()
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a leakage-safe temporal benchmark "
            "from an existing labelled supervisory "
            "relevance dataset."
        )
    )

    parser.add_argument(
        "--dataset-file",
        default=(
            "data/training/datasets/"
            "supervisory_relevance_v2/"
            "dataset.csv"
        ),
    )

    parser.add_argument(
        "--validation-start",
        default="2026-07-01",
        help=(
            "First validation date YYYY-MM-DD."
        ),
    )

    parser.add_argument(
        "--test-start",
        default="2026-09-01",
        help=(
            "First test date YYYY-MM-DD."
        ),
    )

    parser.add_argument(
        "--output-name",
        default=(
            "supervisory_relevance_v2_temporal"
        ),
    )

    args = parser.parse_args()

    dataset_path = Path(
        args.dataset_file
    )

    if not dataset_path.is_absolute():
        dataset_path = (
            PROJECT_ROOT
            / dataset_path
        )

    if not dataset_path.exists():
        raise FileNotFoundError(
            f"Dataset not found: "
            f"{dataset_path}"
        )

    validation_start = (
        datetime.strptime(
            args.validation_start,
            "%Y-%m-%d",
        )
        .date()
    )

    test_start = (
        datetime.strptime(
            args.test_start,
            "%Y-%m-%d",
        )
        .date()
    )

    if (
        validation_start
        >= test_start
    ):
        raise ValueError(
            "validation-start must be "
            "earlier than test-start"
        )

    rows, fieldnames = read_csv(
        dataset_path
    )

    required = {
        "story_group_id",
        "published_at",
        "target_relevance_label",
    }

    missing = (
        required
        - set(
            fieldnames
        )
    )

    if missing:
        raise RuntimeError(
            "Dataset is missing required "
            "columns: "
            + ", ".join(
                sorted(
                    missing
                )
            )
        )

    groups = defaultdict(
        list
    )

    for row in rows:
        groups[
            row[
                "story_group_id"
            ]
        ].append(
            row
        )

    group_assignment = {}

    for (
        group_id,
        group_rows,
    ) in groups.items():

        dates = [
            parse_date(
                row[
                    "published_at"
                ]
            )
            for row in group_rows
        ]

        # Assign the entire story group according to
        # its LATEST article date. This prevents an
        # earlier continuation article from leaking
        # information about a later article into train.
        latest_date = max(
            dates
        )

        if (
            latest_date
            >= test_start
        ):
            split = "test"

        elif (
            latest_date
            >= validation_start
        ):
            split = "validation"

        else:
            split = "train"

        group_assignment[
            group_id
        ] = split

    temporal_rows = []

    for row in rows:
        updated = dict(
            row
        )

        updated[
            "split"
        ] = (
            group_assignment[
                row[
                    "story_group_id"
                ]
            ]
        )

        temporal_rows.append(
            updated
        )

    # Leakage assertion.
    group_splits = defaultdict(
        set
    )

    for row in temporal_rows:
        group_splits[
            row[
                "story_group_id"
            ]
        ].add(
            row[
                "split"
            ]
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

    output_root = (
        PROJECT_ROOT
        / "data"
        / "training"
        / "datasets"
        / args.output_name
    )

    output_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    write_csv(
        output_root
        / "dataset.csv",
        temporal_rows,
        fieldnames,
    )

    split_counts = {}
    split_label_counts = {}

    for split in (
        "train",
        "validation",
        "test",
    ):
        split_rows = [
            row
            for row in temporal_rows
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
        ] = len(
            split_rows
        )

        split_label_counts[
            split
        ] = dict(
            Counter(
                row[
                    "target_relevance_label"
                ]
                for row in split_rows
            )
        )

    # Require all classes in every split.
    all_labels = {
        row[
            "target_relevance_label"
        ]
        for row in temporal_rows
    }

    missing_classes = {}

    for split in (
        "train",
        "validation",
        "test",
    ):
        present = set(
            split_label_counts[
                split
            ]
        )

        absent = (
            all_labels
            - present
        )

        if absent:
            missing_classes[
                split
            ] = sorted(
                absent
            )

    if missing_classes:
        raise RuntimeError(
            "Temporal benchmark is missing "
            "classes in one or more splits: "
            f"{missing_classes}"
        )

    manifest = {
        "status": "SUCCESS",
        "created_at": (
            datetime.now(
                SRI_LANKA_TZ
            )
            .isoformat()
        ),
        "source_dataset": str(
            dataset_path.relative_to(
                PROJECT_ROOT
            )
        ),
        "output_name": (
            args.output_name
        ),
        "validation_start": (
            validation_start.isoformat()
        ),
        "test_start": (
            test_start.isoformat()
        ),
        "row_count": len(
            temporal_rows
        ),
        "story_group_count": len(
            groups
        ),
        "story_group_leakage_count": len(
            leakage_groups
        ),
        "split_counts": (
            split_counts
        ),
        "split_label_counts": (
            split_label_counts
        ),
        "group_assignment_rule": (
            "Assign each story group by its "
            "latest publication date."
        ),
        "note": (
            "Temporal benchmark for development "
            "and model evaluation. Existing random "
            "v2 split is preserved separately."
        ),
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
        "TEMPORAL BENCHMARK BUILD COMPLETE"
    )
    print("=" * 80)
    print(
        f"Rows                 : "
        f"{len(temporal_rows)}"
    )
    print(
        f"Story groups         : "
        f"{len(groups)}"
    )
    print(
        f"Leakage groups       : "
        f"{len(leakage_groups)}"
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
        f"Train labels         : "
        f"{split_label_counts['train']}"
    )
    print(
        f"Validation labels    : "
        f"{split_label_counts['validation']}"
    )
    print(
        f"Test labels          : "
        f"{split_label_counts['test']}"
    )
    print(
        f"Output root          : "
        f"{output_root}"
    )
    print(
        f"Manifest             : "
        f"{manifest_path}"
    )


if __name__ == "__main__":
    main()
