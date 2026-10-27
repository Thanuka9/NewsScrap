from __future__ import annotations

import argparse
import csv
from pathlib import Path
import shutil


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

LABEL_FIELDS = (
    "human_relevance_label",
    "human_topic_labels",
    "review_status",
    "human_notes",
    "label_source",
    "label_confidence",
)


def read_csv(path: Path) -> tuple[list[dict], list[str]]:
    with path.open(
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    return rows, fieldnames


def write_csv(
    path: Path,
    rows: list[dict],
    fieldnames: list[str],
) -> None:
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


def nonblank(value) -> bool:
    return bool(
        str(value or "").strip()
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Carry previously reviewed labels into a "
            "new active-learning review batch by source_url."
        )
    )
    parser.add_argument(
        "--source",
        action="append",
        required=True,
        help=(
            "Previously labelled review CSV. "
            "May be supplied more than once."
        ),
    )
    parser.add_argument(
        "--target",
        required=True,
        help=(
            "Current active-learning review CSV to update."
        ),
    )
    args = parser.parse_args()

    source_paths = []
    for value in args.source:
        path = Path(value)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        source_paths.append(path)

    target_path = Path(args.target)
    if not target_path.is_absolute():
        target_path = PROJECT_ROOT / target_path

    for path in source_paths:
        if not path.exists():
            raise FileNotFoundError(
                f"Source review CSV not found: {path}"
            )

    if not target_path.exists():
        raise FileNotFoundError(
            f"Target review CSV not found: {target_path}"
        )

    annotation_index = {}

    for source_path in source_paths:
        source_rows, _ = read_csv(source_path)

        for row in source_rows:
            source_url = str(
                row.get("source_url", "")
                or ""
            ).strip()

            if not source_url:
                continue

            relevance = str(
                row.get(
                    "human_relevance_label",
                    "",
                )
                or ""
            ).strip()

            if not relevance:
                continue

            annotation_index[source_url] = {
                field: (
                    row.get(field, "")
                    or ""
                )
                for field in LABEL_FIELDS
            }

    target_rows, fieldnames = read_csv(
        target_path
    )

    for field in LABEL_FIELDS:
        if field not in fieldnames:
            fieldnames.append(field)

    preserved = 0
    already_labelled = 0

    for row in target_rows:
        source_url = str(
            row.get("source_url", "")
            or ""
        ).strip()

        if not source_url:
            continue

        if nonblank(
            row.get(
                "human_relevance_label"
            )
        ):
            already_labelled += 1
            continue

        previous = annotation_index.get(
            source_url
        )

        if not previous:
            continue

        for field in LABEL_FIELDS:
            row[field] = (
                previous.get(field, "")
            )

        preserved += 1

    backup_path = (
        target_path.with_name(
            target_path.stem
            + "_before_label_carryforward"
            + target_path.suffix
        )
    )

    if not backup_path.exists():
        shutil.copy2(
            target_path,
            backup_path,
        )

    write_csv(
        target_path,
        target_rows,
        fieldnames,
    )

    remaining_unlabelled = sum(
        1
        for row in target_rows
        if not nonblank(
            row.get(
                "human_relevance_label"
            )
        )
    )

    print()
    print("=" * 80)
    print(
        "REVIEW LABEL CARRY-FORWARD COMPLETE"
    )
    print("=" * 80)
    print(
        f"Prior labelled URLs : "
        f"{len(annotation_index)}"
    )
    print(
        f"Target rows         : "
        f"{len(target_rows)}"
    )
    print(
        f"Already labelled    : "
        f"{already_labelled}"
    )
    print(
        f"Labels preserved    : "
        f"{preserved}"
    )
    print(
        f"Still unlabelled    : "
        f"{remaining_unlabelled}"
    )
    print(
        f"Updated target      : "
        f"{target_path}"
    )
    print(
        f"Backup              : "
        f"{backup_path}"
    )


if __name__ == "__main__":
    main()
