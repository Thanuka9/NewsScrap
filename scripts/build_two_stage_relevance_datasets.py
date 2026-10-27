from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_SOURCE = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "datasets"
    / "supervisory_relevance_v6_bank_supervision_candidate"
)

DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "datasets"
    / "supervisory_relevance_v6_bank_supervision_candidate_two_stage"
)

TZ = ZoneInfo("Asia/Colombo")

VALID_SOURCE_LABELS = {
    "NOISE",
    "WATCH",
    "RELEVANT",
}

VALID_SPLITS = {
    "train",
    "validation",
    "test",
}

STAGE_A_MAP = {
    "NOISE": "NOISE",
    "WATCH": "KEEP",
    "RELEVANT": "KEEP",
}

STAGE_B_ALLOWED = {
    "WATCH",
    "RELEVANT",
}


def text(value) -> str:
    return str(value or "").strip()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv(path: Path) -> tuple[list[dict], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        return rows, list(reader.fieldnames or [])


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def label_counts(rows: list[dict]) -> dict[str, int]:
    return dict(
        sorted(
            Counter(
                text(row.get("target_relevance_label")).upper()
                for row in rows
            ).items()
        )
    )


def split_counts(rows: list[dict]) -> dict[str, int]:
    return dict(
        sorted(
            Counter(
                text(row.get("split")).lower()
                for row in rows
            ).items()
        )
    )


def split_label_counts(rows: list[dict]) -> dict[str, dict[str, int]]:
    result = {}
    for split in ("train", "validation", "test"):
        subset = [
            row
            for row in rows
            if text(row.get("split")).lower() == split
        ]
        result[split] = label_counts(subset)
    return result


def group_leakage(rows: list[dict]) -> list[dict]:
    group_splits = defaultdict(set)

    for row in rows:
        group_id = text(row.get("story_group_id"))
        split = text(row.get("split")).lower()

        if not group_id:
            continue

        group_splits[group_id].add(split)

    leakage = []

    for group_id, splits in sorted(group_splits.items()):
        if len(splits) > 1:
            leakage.append(
                {
                    "story_group_id": group_id,
                    "splits": sorted(splits),
                }
            )

    return leakage


def duplicate_values(rows: list[dict], field: str) -> list[str]:
    counts = Counter(
        text(row.get(field))
        for row in rows
        if text(row.get(field))
    )

    return sorted(
        value
        for value, count in counts.items()
        if count > 1
    )


def source_split_parity(
    source_root: Path,
    dataset_rows: list[dict],
) -> dict:
    """
    Check that source train/validation/test CSVs match dataset.csv
    by dataset_row_id. This is read-only validation.
    """

    result = {}

    dataset_ids_by_split = defaultdict(set)

    for row in dataset_rows:
        split = text(row.get("split")).lower()
        row_id = text(row.get("dataset_row_id"))

        if split in VALID_SPLITS and row_id:
            dataset_ids_by_split[split].add(row_id)

    for split in ("train", "validation", "test"):
        source_path = source_root / f"{split}.csv"

        if not source_path.exists():
            result[split] = {
                "status": "MISSING_SOURCE_SPLIT_FILE",
                "dataset_count": len(dataset_ids_by_split[split]),
                "file_count": None,
                "missing_from_file": None,
                "extra_in_file": None,
            }
            continue

        split_rows, _ = read_csv(source_path)

        file_ids = {
            text(row.get("dataset_row_id"))
            for row in split_rows
            if text(row.get("dataset_row_id"))
        }

        expected = dataset_ids_by_split[split]

        result[split] = {
            "status": (
                "PASS"
                if file_ids == expected
                else "FAIL"
            ),
            "dataset_count": len(expected),
            "file_count": len(file_ids),
            "missing_from_file": sorted(expected - file_ids),
            "extra_in_file": sorted(file_ids - expected),
        }

    return result


def validate_source(rows: list[dict], fieldnames: list[str]) -> list[str]:
    errors = []

    required = {
        "dataset_row_id",
        "story_group_id",
        "split",
        "headline",
        "article_text",
        "published_at",
        "source_url",
        "target_relevance_label",
    }

    missing_columns = sorted(required - set(fieldnames))

    if missing_columns:
        errors.append(
            "Missing required columns: "
            + ", ".join(missing_columns)
        )

    if len(rows) != 219:
        errors.append(
            f"Expected 219 source rows, found {len(rows)}."
        )

    for index, row in enumerate(rows, start=1):
        label = text(
            row.get("target_relevance_label")
        ).upper()

        split = text(
            row.get("split")
        ).lower()

        if label not in VALID_SOURCE_LABELS:
            errors.append(
                f"Row {index}: invalid source label {label!r}."
            )

        if split not in VALID_SPLITS:
            errors.append(
                f"Row {index}: invalid split {split!r}."
            )

        if not text(row.get("dataset_row_id")):
            errors.append(
                f"Row {index}: dataset_row_id missing."
            )

        if not text(row.get("headline")):
            errors.append(
                f"Row {index}: headline missing."
            )

        if not text(row.get("article_text")):
            errors.append(
                f"Row {index}: article_text missing."
            )

    duplicate_row_ids = duplicate_values(
        rows,
        "dataset_row_id",
    )

    if duplicate_row_ids:
        errors.append(
            "Duplicate dataset_row_id values: "
            + ", ".join(duplicate_row_ids[:10])
        )

    duplicate_urls = duplicate_values(
        rows,
        "source_url",
    )

    if duplicate_urls:
        errors.append(
            f"Duplicate source_url values: {len(duplicate_urls)}"
        )

    leakage = group_leakage(rows)

    if leakage:
        errors.append(
            f"Story-group split leakage detected: {len(leakage)} groups."
        )

    return errors


def add_common_columns(
    row: dict,
    *,
    original_label: str,
    task_name: str,
    mapping_version: str,
) -> dict:
    result = dict(row)

    result["original_target_relevance_label"] = original_label
    result["two_stage_task"] = task_name
    result["label_mapping_version"] = mapping_version

    return result


def build_stage_a(rows: list[dict]) -> list[dict]:
    output = []

    for row in rows:
        original = text(
            row.get("target_relevance_label")
        ).upper()

        result = add_common_columns(
            row,
            original_label=original,
            task_name="STAGE_A_NOISE_VS_KEEP",
            mapping_version="two-stage-v1",
        )

        result["target_relevance_label"] = STAGE_A_MAP[original]

        output.append(result)

    return output


def build_stage_b(rows: list[dict]) -> list[dict]:
    output = []

    for row in rows:
        original = text(
            row.get("target_relevance_label")
        ).upper()

        if original not in STAGE_B_ALLOWED:
            continue

        result = add_common_columns(
            row,
            original_label=original,
            task_name="STAGE_B_WATCH_VS_RELEVANT",
            mapping_version="two-stage-v1",
        )

        # Stage B keeps the original WATCH / RELEVANT labels.
        result["target_relevance_label"] = original

        output.append(result)

    return output


def output_fields(source_fields: list[str]) -> list[str]:
    fields = list(source_fields)

    additions = [
        "original_target_relevance_label",
        "two_stage_task",
        "label_mapping_version",
    ]

    for value in additions:
        if value not in fields:
            fields.append(value)

    return fields


def write_dataset_bundle(
    root: Path,
    rows: list[dict],
    fields: list[str],
    *,
    dataset_version: str,
    task_name: str,
    label_mapping: dict,
    source_dataset: Path,
    source_dataset_sha256: str,
    source_manifest: dict,
) -> dict:
    root.mkdir(
        parents=True,
        exist_ok=True,
    )

    dataset_path = root / "dataset.csv"

    write_csv(
        dataset_path,
        rows,
        fields,
    )

    output_files = {
        "dataset.csv": sha256_file(dataset_path)
    }

    for split in (
        "train",
        "validation",
        "test",
    ):
        split_rows = [
            row
            for row in rows
            if text(row.get("split")).lower() == split
        ]

        split_path = root / f"{split}.csv"

        write_csv(
            split_path,
            split_rows,
            fields,
        )

        output_files[
            f"{split}.csv"
        ] = sha256_file(
            split_path
        )

    leakage = group_leakage(rows)

    row_ids = [
        text(row.get("dataset_row_id"))
        for row in rows
    ]

    unique_row_ids = set(row_ids)

    manifest = {
        "status": (
            "PASS"
            if (
                not leakage
                and len(row_ids) == len(unique_row_ids)
            )
            else "FAIL"
        ),
        "created_at": datetime.now(TZ).isoformat(),
        "dataset_version": dataset_version,
        "task_name": task_name,
        "source_dataset": str(
            source_dataset.relative_to(PROJECT_ROOT)
        ),
        "source_dataset_sha256": source_dataset_sha256,
        "source_dataset_version": source_manifest.get(
            "dataset_version"
        ),
        "source_human_gold": source_manifest.get(
            "human_gold"
        ),
        "source_review_provenance": source_manifest.get(
            "review_provenance"
        ),
        "training_data_policy": (
            "Derived only from supervisory_relevance_v6_bank_supervision_candidate. "
            "Historical 200-row AI-assisted disagreement adjudication "
            "is NOT included in training, validation, or test."
        ),
        "label_mapping": label_mapping,
        "row_count": len(rows),
        "story_group_count": len(
            {
                text(row.get("story_group_id"))
                for row in rows
                if text(row.get("story_group_id"))
            }
        ),
        "story_group_leakage_count": len(leakage),
        "story_group_leakage": leakage,
        "duplicate_dataset_row_id_count": (
            len(row_ids)
            - len(unique_row_ids)
        ),
        "label_counts": label_counts(rows),
        "split_counts": split_counts(rows),
        "split_label_counts": split_label_counts(rows),
        "split_strategy": source_manifest.get(
            "split_strategy"
        ),
        "validation_start": source_manifest.get(
            "validation_start"
        ),
        "test_start": source_manifest.get(
            "test_start"
        ),
        "output_sha256": output_files,
        "notes": [
            "Original dataset_row_id values are preserved.",
            "Original story_group_id values are preserved.",
            "Original train/validation/test split assignments are preserved.",
            "Original label is retained in original_target_relevance_label.",
            "target_relevance_label is the task-specific training target.",
            "No source rows are relabelled using the historical AI-assisted audit.",
        ],
    }

    manifest_path = root / "manifest.json"

    manifest_path.write_text(
        json.dumps(
            manifest,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build two-stage supervisory relevance datasets "
            "from the frozen v3 reviewed dataset."
        )
    )

    parser.add_argument(
        "--source",
        default=str(DEFAULT_SOURCE),
        help=(
            "Source supervisory_relevance_v6_bank_supervision_candidate directory."
        ),
    )

    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=(
            "Output root for two-stage datasets."
        ),
    )

    args = parser.parse_args()

    source_root = Path(args.source)

    if not source_root.is_absolute():
        source_root = PROJECT_ROOT / source_root

    output_root = Path(args.output)

    if not output_root.is_absolute():
        output_root = PROJECT_ROOT / output_root

    source_dataset = source_root / "dataset.csv"
    source_manifest_path = source_root / "manifest.json"

    if not source_dataset.exists():
        raise FileNotFoundError(
            source_dataset
        )

    if not source_manifest_path.exists():
        raise FileNotFoundError(
            source_manifest_path
        )

    rows, source_fields = read_csv(
        source_dataset
    )

    source_manifest = json.loads(
        source_manifest_path.read_text(
            encoding="utf-8-sig"
        )
    )

    print("=" * 96)
    print("BUILD TWO-STAGE SUPERVISORY RELEVANCE DATASETS")
    print("=" * 96)
    print(f"Source : {source_dataset}")
    print(f"Rows   : {len(rows)}")
    print()

    errors = validate_source(
        rows,
        source_fields,
    )

    parity = source_split_parity(
        source_root,
        rows,
    )

    for split, result in parity.items():
        if result["status"] != "PASS":
            errors.append(
                f"Source split parity failed for {split}: {result}"
            )

    if errors:
        print("SOURCE VALIDATION: FAIL")
        for error in errors:
            print(f"  - {error}")
        raise SystemExit(1)

    print("SOURCE VALIDATION: PASS")
    print(f"Source labels : {label_counts(rows)}")
    print(f"Source splits : {split_counts(rows)}")
    print(
        "Group leakage : "
        f"{len(group_leakage(rows))}"
    )
    print()

    fields = output_fields(
        source_fields
    )

    stage_a = build_stage_a(
        rows
    )

    stage_b = build_stage_b(
        rows
    )

    source_hash = sha256_file(
        source_dataset
    )

    stage_a_root = (
        output_root
        / "stage_a_noise_vs_keep"
    )

    stage_b_root = (
        output_root
        / "stage_b_watch_vs_relevant"
    )

    stage_a_manifest = write_dataset_bundle(
        stage_a_root,
        stage_a,
        fields,
        dataset_version=(
            "supervisory_relevance_v6_bank_supervision_candidate_stage_a_noise_vs_keep"
        ),
        task_name=(
            "STAGE_A_NOISE_VS_KEEP"
        ),
        label_mapping={
            "NOISE": "NOISE",
            "WATCH": "KEEP",
            "RELEVANT": "KEEP",
        },
        source_dataset=source_dataset,
        source_dataset_sha256=source_hash,
        source_manifest=source_manifest,
    )

    stage_b_manifest = write_dataset_bundle(
        stage_b_root,
        stage_b,
        fields,
        dataset_version=(
            "supervisory_relevance_v6_bank_supervision_candidate_stage_b_watch_vs_relevant"
        ),
        task_name=(
            "STAGE_B_WATCH_VS_RELEVANT"
        ),
        label_mapping={
            "WATCH": "WATCH",
            "RELEVANT": "RELEVANT",
            "NOISE": "EXCLUDED_FROM_STAGE_B",
        },
        source_dataset=source_dataset,
        source_dataset_sha256=source_hash,
        source_manifest=source_manifest,
    )

    parent_manifest = {
        "status": (
            "PASS"
            if (
                stage_a_manifest["status"] == "PASS"
                and stage_b_manifest["status"] == "PASS"
            )
            else "FAIL"
        ),
        "created_at": datetime.now(TZ).isoformat(),
        "dataset_version": (
            "supervisory_relevance_v6_bank_supervision_candidate_two_stage"
        ),
        "source_dataset": str(
            source_dataset.relative_to(PROJECT_ROOT)
        ),
        "source_dataset_sha256": source_hash,
        "source_manifest_sha256": sha256_file(
            source_manifest_path
        ),
        "source_split_parity": parity,
        "stage_a": {
            "directory": str(
                stage_a_root.relative_to(PROJECT_ROOT)
            ),
            "row_count": stage_a_manifest["row_count"],
            "label_counts": stage_a_manifest["label_counts"],
            "split_counts": stage_a_manifest["split_counts"],
            "split_label_counts": stage_a_manifest[
                "split_label_counts"
            ],
            "story_group_leakage_count": stage_a_manifest[
                "story_group_leakage_count"
            ],
        },
        "stage_b": {
            "directory": str(
                stage_b_root.relative_to(PROJECT_ROOT)
            ),
            "row_count": stage_b_manifest["row_count"],
            "label_counts": stage_b_manifest["label_counts"],
            "split_counts": stage_b_manifest["split_counts"],
            "split_label_counts": stage_b_manifest[
                "split_label_counts"
            ],
            "story_group_leakage_count": stage_b_manifest[
                "story_group_leakage_count"
            ],
        },
        "frozen_baseline_policy": (
            "The existing three-class v3 model remains frozen and "
            "is not overwritten by this dataset build."
        ),
        "ai_assisted_evaluation_policy": (
            "The 200-row historical AI-assisted blind adjudication "
            "remains evaluation evidence only and is excluded here."
        ),
    }

    output_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    parent_manifest_path = (
        output_root
        / "manifest.json"
    )

    parent_manifest_path.write_text(
        json.dumps(
            parent_manifest,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print("=" * 96)
    print("TWO-STAGE DATASET BUILD COMPLETE")
    print("=" * 96)

    print("STAGE A — NOISE vs KEEP")
    print(
        f"  Rows         : "
        f"{stage_a_manifest['row_count']}"
    )
    print(
        f"  Labels       : "
        f"{stage_a_manifest['label_counts']}"
    )
    print(
        f"  Splits       : "
        f"{stage_a_manifest['split_counts']}"
    )
    print(
        f"  Split labels : "
        f"{stage_a_manifest['split_label_counts']}"
    )
    print(
        f"  Leakage      : "
        f"{stage_a_manifest['story_group_leakage_count']}"
    )
    print()

    print("STAGE B — WATCH vs RELEVANT")
    print(
        f"  Rows         : "
        f"{stage_b_manifest['row_count']}"
    )
    print(
        f"  Labels       : "
        f"{stage_b_manifest['label_counts']}"
    )
    print(
        f"  Splits       : "
        f"{stage_b_manifest['split_counts']}"
    )
    print(
        f"  Split labels : "
        f"{stage_b_manifest['split_label_counts']}"
    )
    print(
        f"  Leakage      : "
        f"{stage_b_manifest['story_group_leakage_count']}"
    )
    print()

    print(
        f"Output root    : "
        f"{output_root}"
    )
    print(
        f"Parent manifest: "
        f"{parent_manifest_path}"
    )


if __name__ == "__main__":
    main()
