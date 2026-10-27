from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
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

VALID_LABELS = {
    "NOISE",
    "WATCH",
    "RELEVANT",
}

DEFAULT_BASE_DATASET = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "datasets"
    / "supervisory_relevance_v2"
    / "dataset.csv"
)

DEFAULT_AUDIT_FILE = (
    PROJECT_ROOT
    / "data"
    / "review"
    / "active_learning"
    / "priority_label_audit_analyst_reviewed.csv"
)

DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "datasets"
    / "supervisory_relevance_v3_reviewed"
)

REVIEWED_LABEL_SOURCE = (
    "GPT-5.6_SOL_AI_ASSISTED_SCOPE_REVIEW_PROVISIONAL"
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


def normalize(value) -> str:
    return str(value or "").strip()


def upper(value) -> str:
    return normalize(value).upper()




def topic_set(value) -> set[str]:
    return {
        item.strip().upper()
        for item in normalize(value).split(";")
        if item.strip()
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(
            lambda: file.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)
    return digest.hexdigest()


def parse_date(value: str):
    text = normalize(value)

    if not text:
        raise ValueError(
            "Missing published_at."
        )

    normalized = text.replace(
        "Z",
        "+00:00",
    )

    try:
        return datetime.fromisoformat(
            normalized
        ).date()
    except ValueError:
        return datetime.strptime(
            text[:10],
            "%Y-%m-%d",
        ).date()


def append_note(
    existing,
    new_note,
) -> str:
    old = normalize(existing)
    new = normalize(new_note)

    if not new:
        return old

    if not old:
        return new

    return (
        old
        + " | SCOPE_REVIEW: "
        + new
    )


def ensure_unique_urls(
    rows: list[dict],
    *,
    label: str,
) -> None:
    seen = set()
    duplicates = []

    for row in rows:
        url = normalize(
            row.get("source_url")
        )

        if not url:
            raise RuntimeError(
                f"{label} contains a row without source_url."
            )

        if url in seen:
            duplicates.append(url)

        seen.add(url)

    if duplicates:
        raise RuntimeError(
            f"{label} contains duplicate source URLs: "
            f"{sorted(set(duplicates))}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Apply the banking-supervision scope review "
            "to v2 and build a new leakage-safe temporal "
            "v3 dataset without modifying v2."
        )
    )

    parser.add_argument(
        "--base-dataset",
        default=str(
            DEFAULT_BASE_DATASET
        ),
    )

    parser.add_argument(
        "--audit-file",
        default=str(
            DEFAULT_AUDIT_FILE
        ),
    )

    parser.add_argument(
        "--output-root",
        default=str(
            DEFAULT_OUTPUT_ROOT
        ),
    )

    parser.add_argument(
        "--validation-start",
        default="2026-07-01",
    )

    parser.add_argument(
        "--test-start",
        default="2026-09-01",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Allow replacing an existing v3 output folder."
        ),
    )

    args = parser.parse_args()

    base_path = Path(
        args.base_dataset
    )
    audit_path = Path(
        args.audit_file
    )
    output_root = Path(
        args.output_root
    )

    if not base_path.is_absolute():
        base_path = (
            PROJECT_ROOT
            / base_path
        )

    if not audit_path.is_absolute():
        audit_path = (
            PROJECT_ROOT
            / audit_path
        )

    if not output_root.is_absolute():
        output_root = (
            PROJECT_ROOT
            / output_root
        )

    if not base_path.exists():
        raise FileNotFoundError(
            f"Base dataset not found: {base_path}"
        )

    if not audit_path.exists():
        raise FileNotFoundError(
            f"Audit file not found: {audit_path}"
        )

    if output_root.exists():
        existing_files = list(
            output_root.iterdir()
        )

        if (
            existing_files
            and not args.overwrite
        ):
            raise RuntimeError(
                "Output root already contains files. "
                "Refusing to overwrite v3. "
                "Use --overwrite only if you intentionally "
                "want to rebuild it: "
                f"{output_root}"
            )

    validation_start = (
        datetime.strptime(
            args.validation_start,
            "%Y-%m-%d",
        ).date()
    )

    test_start = (
        datetime.strptime(
            args.test_start,
            "%Y-%m-%d",
        ).date()
    )

    if validation_start >= test_start:
        raise ValueError(
            "validation-start must be earlier than test-start."
        )

    base_rows, base_fields = read_csv(
        base_path
    )

    audit_rows, audit_fields = read_csv(
        audit_path
    )

    if not base_rows:
        raise RuntimeError(
            "Base dataset is empty."
        )

    if not audit_rows:
        raise RuntimeError(
            "Audit file is empty."
        )

    required_base = {
        "source_url",
        "story_group_id",
        "published_at",
        "target_relevance_label",
        "target_topic_labels",
        "label_source",
        "label_confidence",
        "review_status",
        "review_notes",
    }

    missing_base = (
        required_base
        - set(base_fields)
    )

    if missing_base:
        raise RuntimeError(
            "Base dataset missing required columns: "
            + ", ".join(
                sorted(missing_base)
            )
        )

    required_audit = {
        "source_url",
        "reference_label_provisional",
        "reviewer_relevance_label",
        "reviewer_topic_labels",
        "reviewer_scope_rationale",
        "reviewer_name_or_id",
        "reviewer_reviewed_at",
        "reviewer_status",
        "review_scope",
        "review_evidence_status",
        "review_confidence",
        "review_date_flag",
        "suggested_publication_date",
        "suggested_story_group_id",
        "review_provenance",
    }

    missing_audit = (
        required_audit
        - set(audit_fields)
    )

    if missing_audit:
        raise RuntimeError(
            "Audit file missing required columns: "
            + ", ".join(
                sorted(missing_audit)
            )
        )

    ensure_unique_urls(
        base_rows,
        label="Base dataset",
    )

    ensure_unique_urls(
        audit_rows,
        label="Audit file",
    )

    base_by_url = {
        normalize(
            row["source_url"]
        ): row
        for row in base_rows
    }

    audit_by_url = {
        normalize(
            row["source_url"]
        ): row
        for row in audit_rows
    }

    missing_from_base = sorted(
        set(audit_by_url)
        - set(base_by_url)
    )

    if missing_from_base:
        raise RuntimeError(
            "Audit rows are missing from the base dataset: "
            f"{missing_from_base}"
        )

    # Guardrail: this reviewed audit was explicitly created
    # for the banking-supervision-focused scope.
    bad_scope = sorted(
        {
            normalize(
                row.get("review_scope")
            )
            for row in audit_rows
            if normalize(
                row.get("review_scope")
            )
            != "SRI_LANKA_BANKING_SUPERVISION"
        }
    )

    if bad_scope:
        raise RuntimeError(
            "Audit contains unexpected review_scope values: "
            f"{bad_scope}"
        )

    extra_audit_fields = [
        "audit_review_applied",
        "pre_review_target_relevance_label",
        "pre_review_target_topic_labels",
        "pre_review_published_at",
        "pre_review_story_group_id",
        "pre_review_label_source",
        "scope_review_reviewer",
        "scope_review_reviewed_at",
        "scope_review_status",
        "scope_review_scope",
        "scope_review_evidence_status",
        "scope_review_confidence",
        "scope_review_rationale",
        "scope_review_provenance",
    ]

    output_fields = list(
        base_fields
    )

    for field in extra_audit_fields:
        if field not in output_fields:
            output_fields.append(field)

    updated_rows = []
    application_report = []

    label_change_count = 0
    topic_change_count = 0
    date_change_count = 0
    story_group_change_count = 0

    for base_row in base_rows:
        row = dict(base_row)

        for field in extra_audit_fields:
            row.setdefault(
                field,
                "",
            )

        url = normalize(
            row["source_url"]
        )

        audit = audit_by_url.get(
            url
        )

        if audit is None:
            updated_rows.append(row)
            continue

        reviewed_label = upper(
            audit.get(
                "reviewer_relevance_label"
            )
        )

        if reviewed_label not in VALID_LABELS:
            raise RuntimeError(
                f"Invalid reviewed label {reviewed_label!r} "
                f"for {url}"
            )

        reviewed_topics = normalize(
            audit.get(
                "reviewer_topic_labels"
            )
        )

        if not reviewed_topics:
            raise RuntimeError(
                f"Reviewed topics are blank for {url}"
            )

        old_label = upper(
            row.get(
                "target_relevance_label"
            )
        )

        old_topics = normalize(
            row.get(
                "target_topic_labels"
            )
        )

        old_date = normalize(
            row.get(
                "published_at"
            )
        )

        old_group = normalize(
            row.get(
                "story_group_id"
            )
        )

        old_source = normalize(
            row.get(
                "label_source"
            )
        )

        row[
            "audit_review_applied"
        ] = "TRUE"

        row[
            "pre_review_target_relevance_label"
        ] = old_label

        row[
            "pre_review_target_topic_labels"
        ] = old_topics

        row[
            "pre_review_published_at"
        ] = old_date

        row[
            "pre_review_story_group_id"
        ] = old_group

        row[
            "pre_review_label_source"
        ] = old_source

        row[
            "target_relevance_label"
        ] = reviewed_label

        row[
            "target_topic_labels"
        ] = reviewed_topics

        row[
            "label_source"
        ] = REVIEWED_LABEL_SOURCE

        row[
            "label_confidence"
        ] = upper(
            audit.get(
                "review_confidence"
            )
        )

        row[
            "review_status"
        ] = upper(
            audit.get(
                "reviewer_status"
            )
        )

        row[
            "review_notes"
        ] = append_note(
            row.get(
                "review_notes"
            ),
            audit.get(
                "reviewer_scope_rationale"
            ),
        )

        row[
            "scope_review_reviewer"
        ] = normalize(
            audit.get(
                "reviewer_name_or_id"
            )
        )

        row[
            "scope_review_reviewed_at"
        ] = normalize(
            audit.get(
                "reviewer_reviewed_at"
            )
        )

        row[
            "scope_review_status"
        ] = upper(
            audit.get(
                "reviewer_status"
            )
        )

        row[
            "scope_review_scope"
        ] = normalize(
            audit.get(
                "review_scope"
            )
        )

        row[
            "scope_review_evidence_status"
        ] = upper(
            audit.get(
                "review_evidence_status"
            )
        )

        row[
            "scope_review_confidence"
        ] = upper(
            audit.get(
                "review_confidence"
            )
        )

        row[
            "scope_review_rationale"
        ] = normalize(
            audit.get(
                "reviewer_scope_rationale"
            )
        )

        row[
            "scope_review_provenance"
        ] = normalize(
            audit.get(
                "review_provenance"
            )
        )

        suggested_date = normalize(
            audit.get(
                "suggested_publication_date"
            )
        )

        date_flag = normalize(
            audit.get(
                "review_date_flag"
            )
        )

        if (
            suggested_date
            and date_flag.startswith(
                "DATE_MISMATCH:"
            )
        ):
            # Preserve only the verified calendar date.
            # Do not invent a clock time.
            row[
                "published_at"
            ] = suggested_date

        suggested_group = normalize(
            audit.get(
                "suggested_story_group_id"
            )
        )

        if suggested_group:
            row[
                "story_group_id"
            ] = suggested_group

        new_label = upper(
            row[
                "target_relevance_label"
            ]
        )

        new_topics = normalize(
            row[
                "target_topic_labels"
            ]
        )

        new_date = normalize(
            row[
                "published_at"
            ]
        )

        new_group = normalize(
            row[
                "story_group_id"
            ]
        )

        label_changed = (
            old_label
            != new_label
        )

        topic_changed = (
            topic_set(old_topics)
            != topic_set(new_topics)
        )

        date_changed = (
            old_date
            != new_date
        )

        group_changed = (
            old_group
            != new_group
        )

        label_change_count += int(
            label_changed
        )
        topic_change_count += int(
            topic_changed
        )
        date_change_count += int(
            date_changed
        )
        story_group_change_count += int(
            group_changed
        )

        application_report.append(
            {
                "source_url": url,
                "headline": normalize(
                    row.get(
                        "headline"
                    )
                ),
                "old_label": old_label,
                "new_label": new_label,
                "label_changed": str(
                    label_changed
                ).upper(),
                "old_topics": old_topics,
                "new_topics": new_topics,
                "topic_changed": str(
                    topic_changed
                ).upper(),
                "old_published_at": old_date,
                "new_published_at": new_date,
                "date_changed": str(
                    date_changed
                ).upper(),
                "old_story_group_id": old_group,
                "new_story_group_id": new_group,
                "story_group_changed": str(
                    group_changed
                ).upper(),
                "review_status": upper(
                    audit.get(
                        "reviewer_status"
                    )
                ),
                "review_confidence": upper(
                    audit.get(
                        "review_confidence"
                    )
                ),
                "review_scope": normalize(
                    audit.get(
                        "review_scope"
                    )
                ),
                "review_rationale": normalize(
                    audit.get(
                        "reviewer_scope_rationale"
                    )
                ),
            }
        )

        updated_rows.append(row)

    if len(updated_rows) != len(base_rows):
        raise RuntimeError(
            "Row-count changed unexpectedly."
        )

    if len(application_report) != len(audit_rows):
        raise RuntimeError(
            "Not every audit row was applied."
        )

    # Validate final labels.
    invalid_labels = sorted(
        {
            upper(
                row.get(
                    "target_relevance_label"
                )
            )
            for row in updated_rows
        }
        - VALID_LABELS
    )

    if invalid_labels:
        raise RuntimeError(
            f"Invalid labels after review: {invalid_labels}"
        )

    # Rebuild temporal split after the date/group corrections.
    groups = defaultdict(
        list
    )

    for row in updated_rows:
        groups[
            normalize(
                row[
                    "story_group_id"
                ]
            )
        ].append(row)

    group_assignment = {}

    for group_id, group_rows in groups.items():
        latest_date = max(
            parse_date(
                row[
                    "published_at"
                ]
            )
            for row in group_rows
        )

        if latest_date >= test_start:
            split = "test"
        elif latest_date >= validation_start:
            split = "validation"
        else:
            split = "train"

        group_assignment[
            group_id
        ] = split

    for row in updated_rows:
        row[
            "split"
        ] = group_assignment[
            normalize(
                row[
                    "story_group_id"
                ]
            )
        ]

    # Leakage assertion.
    group_splits = defaultdict(
        set
    )

    for row in updated_rows:
        group_splits[
            normalize(
                row[
                    "story_group_id"
                ]
            )
        ].add(
            row[
                "split"
            ]
        )

    leakage_groups = {
        group_id: sorted(splits)
        for group_id, splits in group_splits.items()
        if len(splits) > 1
    }

    if leakage_groups:
        raise RuntimeError(
            "Story-group leakage detected after review: "
            f"{leakage_groups}"
        )

    output_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    dataset_path = (
        output_root
        / "dataset.csv"
    )

    write_csv(
        dataset_path,
        updated_rows,
        output_fields,
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
            for row in updated_rows
            if row[
                "split"
            ]
            == split
        ]

        write_csv(
            output_root
            / f"{split}.csv",
            split_rows,
            output_fields,
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
                upper(
                    row[
                        "target_relevance_label"
                    ]
                )
                for row in split_rows
            )
        )

    all_labels = set(
        VALID_LABELS
    )

    for split, counts in split_label_counts.items():
        missing = (
            all_labels
            - set(counts)
        )
        if missing:
            raise RuntimeError(
                f"{split} split is missing classes: "
                f"{sorted(missing)}"
            )

    report_fields = [
        "source_url",
        "headline",
        "old_label",
        "new_label",
        "label_changed",
        "old_topics",
        "new_topics",
        "topic_changed",
        "old_published_at",
        "new_published_at",
        "date_changed",
        "old_story_group_id",
        "new_story_group_id",
        "story_group_changed",
        "review_status",
        "review_confidence",
        "review_scope",
        "review_rationale",
    ]

    application_report_path = (
        output_root
        / "audit_application_report.csv"
    )

    write_csv(
        application_report_path,
        application_report,
        report_fields,
    )

    final_label_counts = dict(
        Counter(
            upper(
                row[
                    "target_relevance_label"
                ]
            )
            for row in updated_rows
        )
    )

    manifest = {
        "status": "SUCCESS",
        "created_at": datetime.now(
            SRI_LANKA_TZ
        ).isoformat(),
        "dataset_version": (
            "supervisory_relevance_v3_reviewed"
        ),
        "scope": (
            "SRI_LANKA_BANKING_SUPERVISION"
        ),
        "human_gold": False,
        "review_provenance": (
            "AI-assisted scope adjudication; "
            "not independent human verification."
        ),
        "base_dataset": str(
            base_path
        ),
        "audit_file": str(
            audit_path
        ),
        "base_dataset_sha256": sha256_file(
            base_path
        ),
        "audit_file_sha256": sha256_file(
            audit_path
        ),
        "row_count": len(
            updated_rows
        ),
        "audit_rows_applied": len(
            application_report
        ),
        "label_changes": (
            label_change_count
        ),
        "topic_changes": (
            topic_change_count
        ),
        "publication_date_changes": (
            date_change_count
        ),
        "story_group_changes": (
            story_group_change_count
        ),
        "story_group_count": len(
            groups
        ),
        "story_group_leakage_count": len(
            leakage_groups
        ),
        "validation_start": (
            validation_start.isoformat()
        ),
        "test_start": (
            test_start.isoformat()
        ),
        "split_strategy": (
            "Temporal. Entire story group assigned "
            "using its latest publication date."
        ),
        "final_label_counts": (
            final_label_counts
        ),
        "split_counts": (
            split_counts
        ),
        "split_label_counts": (
            split_label_counts
        ),
        "reviewed_label_source": (
            REVIEWED_LABEL_SOURCE
        ),
        "notes": [
            (
                "v2 files are read-only inputs and are "
                "not modified."
            ),
            (
                "A reviewed publication-date override "
                "uses the verified calendar date only; "
                "no artificial clock time is created."
            ),
            (
                "Manual story-group overrides are applied "
                "before temporal splitting."
            ),
            (
                "This dataset remains provisional because "
                "the review is AI-assisted rather than "
                "independent human-gold adjudication."
            ),
        ],
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
        "SUPERVISORY RELEVANCE V3 REVIEWED BUILD COMPLETE"
    )
    print("=" * 80)
    print(
        f"Base rows                 : "
        f"{len(base_rows)}"
    )
    print(
        f"Audit rows applied        : "
        f"{len(application_report)}"
    )
    print(
        f"Label changes             : "
        f"{label_change_count}"
    )
    print(
        f"Topic changes             : "
        f"{topic_change_count}"
    )
    print(
        f"Publication-date changes  : "
        f"{date_change_count}"
    )
    print(
        f"Story-group changes       : "
        f"{story_group_change_count}"
    )
    print(
        f"Final story groups        : "
        f"{len(groups)}"
    )
    print(
        f"Leakage groups            : "
        f"{len(leakage_groups)}"
    )
    print(
        f"Final label counts        : "
        f"{final_label_counts}"
    )
    print(
        f"Train rows                : "
        f"{split_counts['train']}"
    )
    print(
        f"Validation rows           : "
        f"{split_counts['validation']}"
    )
    print(
        f"Test rows                 : "
        f"{split_counts['test']}"
    )
    print(
        f"Train labels              : "
        f"{split_label_counts['train']}"
    )
    print(
        f"Validation labels         : "
        f"{split_label_counts['validation']}"
    )
    print(
        f"Test labels               : "
        f"{split_label_counts['test']}"
    )
    print(
        f"Output root               : "
        f"{output_root}"
    )
    print(
        f"Audit report              : "
        f"{application_report_path}"
    )
    print(
        f"Manifest                  : "
        f"{manifest_path}"
    )


if __name__ == "__main__":
    main()
