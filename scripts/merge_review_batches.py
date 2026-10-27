from __future__ import annotations

import argparse
import csv
from pathlib import Path


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
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


def nonblank(
    value,
) -> bool:
    return bool(
        str(
            value
            or ""
        ).strip()
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Merge multiple labelled active-learning "
            "review CSVs into one deduplicated master "
            "review file using source_url."
        )
    )

    parser.add_argument(
        "--input",
        action="append",
        required=True,
        help=(
            "Input review CSV. May be supplied multiple "
            "times. Later inputs take precedence for "
            "non-conflicting duplicate rows."
        ),
    )

    parser.add_argument(
        "--output",
        required=True,
        help=(
            "Output merged review CSV."
        ),
    )

    args = parser.parse_args()

    input_paths = []

    for value in args.input:
        path = Path(value)

        if not path.is_absolute():
            path = (
                PROJECT_ROOT
                / path
            )

        if not path.exists():
            raise FileNotFoundError(
                f"Input review CSV not found: {path}"
            )

        input_paths.append(path)

    output_path = Path(
        args.output
    )

    if not output_path.is_absolute():
        output_path = (
            PROJECT_ROOT
            / output_path
        )

    merged_by_url: dict[
        str,
        dict,
    ] = {}

    fieldnames: list[str] = []

    duplicate_count = 0
    conflict_count = 0
    conflicts = []

    input_row_count = 0

    for input_path in input_paths:
        rows, fields = read_csv(
            input_path
        )

        input_row_count += len(
            rows
        )

        for field in fields:
            if field not in fieldnames:
                fieldnames.append(field)

        for row in rows:
            source_url = str(
                row.get(
                    "source_url",
                    ""
                )
                or ""
            ).strip()

            if not source_url:
                raise RuntimeError(
                    f"Row without source_url in {input_path}"
                )

            previous = (
                merged_by_url.get(
                    source_url
                )
            )

            if previous is None:
                merged_by_url[
                    source_url
                ] = dict(row)
                continue

            duplicate_count += 1

            old_label = str(
                previous.get(
                    "human_relevance_label",
                    ""
                )
                or ""
            ).strip().upper()

            new_label = str(
                row.get(
                    "human_relevance_label",
                    ""
                )
                or ""
            ).strip().upper()

            if (
                old_label
                and new_label
                and old_label
                != new_label
            ):
                conflict_count += 1

                conflicts.append(
                    {
                        "source_url": (
                            source_url
                        ),
                        "old_label": (
                            old_label
                        ),
                        "new_label": (
                            new_label
                        ),
                        "old_headline": (
                            previous.get(
                                "headline",
                                ""
                            )
                        ),
                        "new_headline": (
                            row.get(
                                "headline",
                                ""
                            )
                        ),
                    }
                )

                continue

            combined = dict(
                previous
            )

            for field in fieldnames:
                new_value = row.get(
                    field,
                    ""
                )

                if nonblank(
                    new_value
                ):
                    combined[
                        field
                    ] = (
                        new_value
                    )

            merged_by_url[
                source_url
            ] = (
                combined
            )

    if conflicts:
        conflict_path = (
            output_path.with_name(
                output_path.stem
                + "_conflicts.csv"
            )
        )

        conflict_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with conflict_path.open(
            "w",
            newline="",
            encoding="utf-8-sig",
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=[
                    "source_url",
                    "old_label",
                    "new_label",
                    "old_headline",
                    "new_headline",
                ],
            )
            writer.writeheader()
            writer.writerows(
                conflicts
            )

        raise RuntimeError(
            f"Found {conflict_count} conflicting "
            f"relevance labels. Review: {conflict_path}"
        )

    merged_rows = list(
        merged_by_url.values()
    )

    merged_rows.sort(
        key=lambda row: (
            str(
                row.get(
                    "publication_date",
                    ""
                )
                or ""
            ),
            str(
                row.get(
                    "source_name",
                    ""
                )
                or ""
            ),
            str(
                row.get(
                    "headline",
                    ""
                )
                or ""
            ),
        )
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_path.open(
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
            merged_rows
        )

    labelled_count = sum(
        1
        for row in merged_rows
        if nonblank(
            row.get(
                "human_relevance_label"
            )
        )
    )

    print()
    print("=" * 80)
    print(
        "REVIEW BATCH MERGE COMPLETE"
    )
    print("=" * 80)
    print(
        f"Input files          : "
        f"{len(input_paths)}"
    )
    print(
        f"Input rows           : "
        f"{input_row_count}"
    )
    print(
        f"Duplicate URLs       : "
        f"{duplicate_count}"
    )
    print(
        f"Conflicts            : "
        f"{conflict_count}"
    )
    print(
        f"Unique rows          : "
        f"{len(merged_rows)}"
    )
    print(
        f"Labelled rows        : "
        f"{labelled_count}"
    )
    print(
        f"Output               : "
        f"{output_path}"
    )


if __name__ == "__main__":
    main()
