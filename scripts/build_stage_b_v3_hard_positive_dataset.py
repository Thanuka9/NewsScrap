from __future__ import annotations

"""
Build Stage-B v3 hard-positive development dataset.

Base:
  supervisory_relevance_v5_stage_b_targeted/train.csv (116 rows)
  original validation.csv remains unchanged (13 rows)

New blind-adjudicated pool:
  stage_b_hard_positive_CLEAN_59.csv

Before retraining, reserve a NEW fixed 20-row holdout:
  10 WATCH + 10 RELEVANT

Remaining new augmentation:
  39 rows = 9 WATCH + 30 RELEVANT

Expected combined train:
  155 rows = 77 WATCH + 78 RELEVANT

Safety:
- inherited test.csv copied byte-for-byte; labels never read
- previous 16-row inspected diagnostic holdout is copied only, never trained on
- zero URL/group/row-id/headline overlap across train/validation/new holdout
"""

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_BASE = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "datasets"
    / "supervisory_relevance_v5_stage_b_targeted"
)

DEFAULT_AUG = (
    PROJECT_ROOT
    / "data"
    / "review"
    / "stage_b_hard_positive_pool"
    / "20261012_092929"
    / "adjudication_results"
    / "stage_b_hard_positive_CLEAN_59.csv"
)

DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "datasets"
    / "supervisory_relevance_v6_stage_b_hard_positive"
)

LABELS = ("WATCH", "RELEVANT")

# Fixed BEFORE retraining.
FRESH_HOLDOUT_IDS = {
    # WATCH — high-confidence adjacent/hard-negative concepts.
    "SB_HP_005",  # insurance financial results
    "SB_HP_006",  # bond market
    "SB_HP_013",  # tourism macro
    "SB_HP_017",  # FIU/insurance awareness
    "SB_HP_020",  # monetary policy
    "SB_HP_024",  # IMF/governance context
    "SB_HP_030",  # finance-company ranking
    "SB_HP_057",  # government-securities market
    "SB_HP_059",  # reserves / FX intervention
    "SB_HP_065",  # policy-rate forecast

    # RELEVANT — hard-positive supervisory concepts.
    "SB_HP_003",  # beneficial ownership / AML framework
    "SB_HP_008",  # direct bank financial condition
    "SB_HP_037",  # bank Tier II capital
    "SB_HP_042",  # NBFI senior governance
    "SB_HP_050",  # bank fraud / forensic review
    "SB_HP_053",  # banking-sector systemic risk
    "SB_HP_056",  # direct Central Bank investigation
    "SB_HP_063",  # sector capital / asset quality
    "SB_HP_068",  # CBSL bank-merger framework
    "SB_HP_072",  # finance-company funding / prudential risk
}


def norm(value: Any) -> str:
    return str(value or "").strip()


def normalize_headline(value: str) -> str:
    text = norm(value).lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", "", text)
    return text.strip()


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader), list(reader.fieldnames or [])


def write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    fields: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=fields,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def values(rows, field):
    return {norm(r.get(field)) for r in rows if norm(r.get(field))}


def headlines(rows):
    return {
        normalize_headline(r.get("headline", ""))
        for r in rows
        if normalize_headline(r.get("headline", ""))
    }


def validate_rows(
    split_name: str,
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        raise RuntimeError(f"{split_name} is empty")

    required = {
        "dataset_row_id",
        "source_url",
        "story_group_id",
        "headline",
        "article_text",
        "target_relevance_label",
    }

    for index, row in enumerate(rows, start=1):
        missing = required - set(row)
        if missing:
            raise RuntimeError(
                f"{split_name} row {index}: missing {sorted(missing)}"
            )

        label = norm(row.get("target_relevance_label")).upper()
        if label not in LABELS:
            raise RuntimeError(
                f"{split_name} row {index}: invalid label {label!r}"
            )

        for field in (
            "dataset_row_id",
            "source_url",
            "story_group_id",
            "headline",
            "article_text",
        ):
            if not norm(row.get(field)):
                raise RuntimeError(
                    f"{split_name} row {index}: blank {field}"
                )


def audit_pair(
    left_name: str,
    left: list[dict[str, Any]],
    right_name: str,
    right: list[dict[str, Any]],
) -> dict[str, int]:
    left_sets = {
        "url": values(left, "source_url"),
        "group": values(left, "story_group_id"),
        "row_id": values(left, "dataset_row_id"),
        "headline": headlines(left),
    }
    right_sets = {
        "url": values(right, "source_url"),
        "group": values(right, "story_group_id"),
        "row_id": values(right, "dataset_row_id"),
        "headline": headlines(right),
    }

    result = {}
    for field in ("url", "group", "row_id", "headline"):
        count = len(left_sets[field] & right_sets[field])
        key = f"{left_name}_{right_name}_{field}_overlap"
        result[key] = count
        if count:
            raise RuntimeError(
                f"Leakage detected: {key}={count}"
            )

    return result


def convert_aug_row(row: dict[str, str]) -> dict[str, str]:
    review_id = norm(row.get("review_id"))
    if not review_id:
        raise RuntimeError("Augmentation row missing review_id")

    label = norm(row.get("target_relevance_label")).upper()
    if label not in LABELS:
        raise RuntimeError(
            f"{review_id}: invalid label {label!r}"
        )

    story_group = norm(row.get("story_group_id"))
    if not story_group:
        story_group = f"HP_EVENT_{review_id}"

    return {
        **row,
        "dataset_row_id": f"hp_{review_id.lower()}",
        "story_group_id": story_group,
        "target_relevance_label": label,
        "original_target_relevance_label": label,
        "two_stage_task": "STAGE_B_WATCH_VS_RELEVANT",
        "label_mapping_version": "stage_b_hard_positive_v1",
        "dataset_origin": "HARD_POSITIVE_AUGMENTATION",
        "augmentation_review_id": review_id,
        "label_source": norm(row.get("label_source"))
        or "GPT-5.6_SOL_AI_ASSISTED_BLIND_PROVISIONAL",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build Stage-B v3 hard-positive dataset with a new locked "
            "20-row holdout. Inherited test.csv is never read."
        )
    )
    parser.add_argument("--base-dataset", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--augmentation-csv", type=Path, default=DEFAULT_AUG)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    base = (
        args.base_dataset
        if args.base_dataset.is_absolute()
        else PROJECT_ROOT / args.base_dataset
    )
    aug_path = (
        args.augmentation_csv
        if args.augmentation_csv.is_absolute()
        else PROJECT_ROOT / args.augmentation_csv
    )
    output = (
        args.output_root
        if args.output_root.is_absolute()
        else PROJECT_ROOT / args.output_root
    )

    train_path = base / "train.csv"
    val_path = base / "validation.csv"
    test_path = base / "test.csv"
    diagnostic_path = base / "boundary_holdout.csv"

    for path in (train_path, val_path, test_path, diagnostic_path, aug_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    if output.exists() and any(output.iterdir()):
        raise RuntimeError(
            f"Refusing to overwrite non-empty output directory: {output}"
        )

    base_train, train_fields = read_csv(train_path)
    validation, val_fields = read_csv(val_path)
    aug_rows, aug_fields = read_csv(aug_path)

    # Do not parse inherited test.csv.
    test_sha_before = sha256_file(test_path)

    validate_rows("base_train", base_train)
    validate_rows("validation", validation)

    if len(base_train) != 116:
        raise RuntimeError(
            f"Expected v5 base train=116; found {len(base_train)}"
        )
    if len(validation) != 13:
        raise RuntimeError(
            f"Expected validation=13; found {len(validation)}"
        )
    if len(aug_rows) != 59:
        raise RuntimeError(
            f"Expected CLEAN_59 augmentation; found {len(aug_rows)}"
        )

    converted = [convert_aug_row(row) for row in aug_rows]

    ids = {
        row["augmentation_review_id"]
        for row in converted
    }
    missing = FRESH_HOLDOUT_IDS - ids
    if missing:
        raise RuntimeError(
            f"Fresh holdout IDs missing from CLEAN_59: {sorted(missing)}"
        )

    fresh_holdout = [
        row
        for row in converted
        if row["augmentation_review_id"] in FRESH_HOLDOUT_IDS
    ]

    new_train = [
        row
        for row in converted
        if row["augmentation_review_id"] not in FRESH_HOLDOUT_IDS
    ]

    holdout_counts = Counter(
        row["target_relevance_label"]
        for row in fresh_holdout
    )
    new_train_counts = Counter(
        row["target_relevance_label"]
        for row in new_train
    )

    if holdout_counts != Counter({"WATCH": 10, "RELEVANT": 10}):
        raise RuntimeError(
            f"Fresh holdout must be 10/10; found {dict(holdout_counts)}"
        )

    if len(new_train) != 39:
        raise RuntimeError(
            f"Expected 39 new training rows; found {len(new_train)}"
        )

    expected_new_train = Counter({"WATCH": 9, "RELEVANT": 30})
    if new_train_counts != expected_new_train:
        raise RuntimeError(
            "Unexpected augmentation-train label counts: "
            f"{dict(new_train_counts)}"
        )

    combined_train = list(base_train) + new_train

    validate_rows("combined_train", combined_train)
    validate_rows("validation", validation)
    validate_rows("fresh_holdout", fresh_holdout)

    combined_counts = Counter(
        row["target_relevance_label"]
        for row in combined_train
    )

    if combined_counts != Counter({"WATCH": 77, "RELEVANT": 78}):
        raise RuntimeError(
            f"Expected combined train 77 WATCH / 78 RELEVANT; "
            f"found {dict(combined_counts)}"
        )

    leakage = {}
    leakage.update(
        audit_pair(
            "train",
            combined_train,
            "validation",
            validation,
        )
    )
    leakage.update(
        audit_pair(
            "train",
            combined_train,
            "fresh_holdout",
            fresh_holdout,
        )
    )
    leakage.update(
        audit_pair(
            "validation",
            validation,
            "fresh_holdout",
            fresh_holdout,
        )
    )

    # Also ensure no duplicate source URLs inside each active split.
    for split_name, rows in (
        ("train", combined_train),
        ("validation", validation),
        ("fresh_holdout", fresh_holdout),
    ):
        urls = [norm(row["source_url"]) for row in rows]
        if len(urls) != len(set(urls)):
            raise RuntimeError(
                f"Duplicate source_url inside {split_name}"
            )

    output.mkdir(parents=True, exist_ok=True)

    fields = list(
        dict.fromkeys(
            train_fields
            + val_fields
            + aug_fields
            + [
                "dataset_row_id",
                "story_group_id",
                "target_relevance_label",
                "original_target_relevance_label",
                "two_stage_task",
                "label_mapping_version",
                "dataset_origin",
                "augmentation_review_id",
                "label_source",
            ]
        )
    )

    write_csv(output / "train.csv", combined_train, fields)
    write_csv(output / "validation.csv", validation, fields)
    write_csv(output / "hard_positive_train.csv", new_train, fields)
    write_csv(output / "fresh_boundary_holdout.csv", fresh_holdout, fields)

    # Preserve the already-inspected first holdout for historical diagnostics only.
    shutil.copyfile(
        diagnostic_path,
        output / "diagnostic_holdout_v1_INSPECTED.csv",
    )

    # Preserve inherited test byte-for-byte, without reading labels.
    shutil.copyfile(test_path, output / "test.csv")
    test_sha_after = sha256_file(output / "test.csv")

    if test_sha_before != test_sha_after:
        raise RuntimeError("Inherited test.csv hash changed")

    manifest = {
        "status": "PASS",
        "version": "supervisory_relevance_v6_stage_b_hard_positive",
        "task": "STAGE_B_WATCH_VS_RELEVANT",
        "base_dataset": str(base),
        "augmentation_source": str(aug_path),
        "base_train_rows": len(base_train),
        "new_clean_pool_rows": len(converted),
        "new_training_rows": len(new_train),
        "new_training_counts": dict(new_train_counts),
        "combined_train_rows": len(combined_train),
        "combined_train_counts": dict(combined_counts),
        "validation_rows": len(validation),
        "fresh_holdout_rows": len(fresh_holdout),
        "fresh_holdout_counts": dict(holdout_counts),
        "fresh_holdout_ids": sorted(FRESH_HOLDOUT_IDS),
        "previous_diagnostic_holdout": (
            "Copied as diagnostic_holdout_v1_INSPECTED.csv and never added "
            "to training. It is no longer fresh promotion evidence."
        ),
        "test_policy": (
            "Inherited test.csv copied byte-for-byte. Test labels were not "
            "read by this builder and must not be used during training/tuning."
        ),
        "test_sha256_before": test_sha_before,
        "test_sha256_after": test_sha_after,
        "leakage_audit": leakage,
        "provenance": (
            "New hard-positive labels are GPT-5.6 Sol AI-assisted blind "
            "provisional labels; not human gold."
        ),
    }

    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )

    print("=" * 96)
    print("STAGE-B v3 HARD-POSITIVE DATASET COMPLETE")
    print("=" * 96)
    print(f"Base train rows            : {len(base_train)}")
    print(f"Clean hard-positive pool   : {len(converted)}")
    print(f"New training rows          : {len(new_train)}")
    print(f"Combined train rows        : {len(combined_train)}")
    print(f"Original validation rows   : {len(validation)}")
    print(f"Fresh boundary holdout     : {len(fresh_holdout)}")
    print("Inherited test labels read : 0")
    print(f"Inherited test hash kept   : {test_sha_before == test_sha_after}")
    print()
    print("COMBINED TRAIN COUNTS")
    for label in LABELS:
        print(f"  {label:<10} {combined_counts[label]}")
    print()
    print("NEW HOLDOUT COUNTS")
    for label in LABELS:
        print(f"  {label:<10} {holdout_counts[label]}")
    print()
    print("LEAKAGE AUDIT")
    for key, value in leakage.items():
        print(f"  {key:<56} {value}")
    print()
    print(
        "Previous 16-row diagnostic holdout copied only as "
        "diagnostic_holdout_v1_INSPECTED.csv"
    )
    print(f"Output: {output}")


if __name__ == "__main__":
    main()
