from __future__ import annotations

"""
Build Stage-B v2 targeted-augmentation development dataset.

Design
------
Base:
    existing v4 Stage-B train/validation/test

Augmentation:
    blind-adjudicated CLEAN_71 pool

Fixed fresh boundary holdout:
    16 augmentation articles (8 WATCH, 8 RELEVANT)
    These are NOT added to train.csv or validation.csv.

Training:
    original Stage-B train + remaining 55 clean augmentation articles

Checkpoint selection:
    original 13-row validation split only, preserved unchanged in composition

Evaluation:
    boundary_holdout.csv is reserved for post-training comparison only
    test.csv is copied byte-for-byte and must remain untouched.

No existing base labels are changed.
"""

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_BASE = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "datasets"
    / "supervisory_relevance_v4_two_stage"
    / "stage_b_watch_vs_relevant"
)

DEFAULT_AUG = (
    PROJECT_ROOT
    / "data"
    / "review"
    / "stage_b_targeted_augmentation"
    / "20261009_133941"
    / "adjudication_results"
    / "stage_b_targeted_augmentation_CLEAN_71.csv"
)

DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "datasets"
    / "supervisory_relevance_v5_stage_b_targeted"
)

LABELS = ("WATCH", "RELEVANT")

# Intentionally fixed before retraining.
BOUNDARY_HOLDOUT_IDS = {
    # WATCH — insurance / governance / macro / market / AML-adjacent
    "SB_AUG_033",
    "SB_AUG_101",
    "SB_AUG_018",
    "SB_AUG_019",
    "SB_AUG_083",
    "SB_AUG_012",
    "SB_AUG_084",
    "SB_AUG_077",

    # RELEVANT — AML / enforcement / prudential / direct results
    "SB_AUG_013",
    "SB_AUG_085",
    "SB_AUG_062",
    "SB_AUG_070",
    "SB_AUG_047",
    "SB_AUG_063",
    "SB_AUG_008",
    "SB_AUG_040",
}


def norm(value: Any) -> str:
    return str(value or "").strip()


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


def normalize_headline(value: str) -> str:
    import re

    text = norm(value).lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", "", text)
    return text.strip()


def validate_required(
    split_name: str,
    rows: list[dict[str, Any]],
) -> None:
    required = {
        "dataset_row_id",
        "source_url",
        "story_group_id",
        "target_relevance_label",
        "headline",
        "article_text",
        "published_at",
        "label_source",
    }

    if not rows:
        raise RuntimeError(f"{split_name} is empty")

    for index, row in enumerate(rows, start=1):
        missing = required - set(row)
        if missing:
            raise RuntimeError(
                f"{split_name} row {index}: missing fields {sorted(missing)}"
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


def audit_no_overlap(
    train: list[dict[str, Any]],
    validation: list[dict[str, Any]],
    holdout: list[dict[str, Any]],
) -> dict[str, int]:
    def values(rows, field):
        return {norm(r.get(field)) for r in rows if norm(r.get(field))}

    def headlines(rows):
        return {
            normalize_headline(r.get("headline", ""))
            for r in rows
            if normalize_headline(r.get("headline", ""))
        }

    sets = {}
    for name, rows in (
        ("train", train),
        ("validation", validation),
        ("boundary_holdout", holdout),
    ):
        sets[name] = {
            "url": values(rows, "source_url"),
            "group": values(rows, "story_group_id"),
            "row_id": values(rows, "dataset_row_id"),
            "headline": headlines(rows),
        }

    result = {}

    pairs = (
        ("train", "validation"),
        ("train", "boundary_holdout"),
        ("validation", "boundary_holdout"),
    )

    for left, right in pairs:
        for field in ("url", "group", "row_id", "headline"):
            count = len(sets[left][field] & sets[right][field])
            result[f"{left}_{right}_{field}_overlap"] = count
            if count:
                raise RuntimeError(
                    f"Leakage detected: {left} vs {right} {field} overlap={count}"
                )

    return result


def convert_aug_row(row: dict[str, str]) -> dict[str, str]:
    review_id = norm(row.get("review_id"))
    if not review_id:
        raise RuntimeError("Augmentation row missing review_id")

    label = norm(row.get("target_relevance_label")).upper()
    if label not in LABELS:
        raise RuntimeError(
            f"{review_id}: invalid augmentation label {label!r}"
        )

    story_group = norm(row.get("story_group_id"))
    if not story_group:
        story_group = f"AUG_EVENT_{review_id}"

    return {
        **row,
        "dataset_row_id": f"aug_{review_id.lower()}",
        "story_group_id": story_group,
        "target_relevance_label": label,
        "original_target_relevance_label": label,
        "two_stage_task": "STAGE_B_WATCH_VS_RELEVANT",
        "label_mapping_version": "stage_b_targeted_aug_v1",
        "label_source": norm(row.get("label_source"))
        or "GPT-5.6_SOL_AI_ASSISTED_BLIND_PROVISIONAL",
        "dataset_origin": "TARGETED_BOUNDARY_AUGMENTATION",
        "augmentation_review_id": review_id,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build Stage-B v2 targeted dataset with a locked fresh "
            "boundary holdout and untouched inherited test split."
        )
    )
    parser.add_argument(
        "--base-dataset",
        type=Path,
        default=DEFAULT_BASE,
    )
    parser.add_argument(
        "--augmentation-csv",
        type=Path,
        default=DEFAULT_AUG,
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT,
    )
    args = parser.parse_args()

    base = args.base_dataset
    aug_path = args.augmentation_csv
    output = args.output_root

    if not base.is_absolute():
        base = PROJECT_ROOT / base
    if not aug_path.is_absolute():
        aug_path = PROJECT_ROOT / aug_path
    if not output.is_absolute():
        output = PROJECT_ROOT / output

    base_train_path = base / "train.csv"
    base_val_path = base / "validation.csv"
    base_test_path = base / "test.csv"

    for path in (base_train_path, base_val_path, base_test_path, aug_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    if output.exists() and any(output.iterdir()):
        raise RuntimeError(
            "Output directory already contains files; refuse to overwrite: "
            f"{output}"
        )

    base_train, base_train_fields = read_csv(base_train_path)
    base_val, base_val_fields = read_csv(base_val_path)
    aug_rows, aug_fields = read_csv(aug_path)

    # No test labels are read. Hash/copy only.
    base_test_sha256 = sha256_file(base_test_path)

    validate_required("base_train", base_train)
    validate_required("base_validation", base_val)

    if len(aug_rows) != 71:
        raise RuntimeError(
            f"Expected CLEAN_71 augmentation file; found {len(aug_rows)} rows"
        )

    converted_aug = [convert_aug_row(row) for row in aug_rows]

    aug_ids = {
        norm(row.get("augmentation_review_id"))
        for row in converted_aug
    }

    missing_holdout = BOUNDARY_HOLDOUT_IDS - aug_ids
    if missing_holdout:
        raise RuntimeError(
            f"Boundary holdout IDs missing from augmentation: "
            f"{sorted(missing_holdout)}"
        )

    boundary_holdout = [
        row
        for row in converted_aug
        if row["augmentation_review_id"] in BOUNDARY_HOLDOUT_IDS
    ]

    augmentation_train = [
        row
        for row in converted_aug
        if row["augmentation_review_id"] not in BOUNDARY_HOLDOUT_IDS
    ]

    holdout_counts = Counter(
        row["target_relevance_label"]
        for row in boundary_holdout
    )

    if holdout_counts != Counter({"WATCH": 8, "RELEVANT": 8}):
        raise RuntimeError(
            "Boundary holdout must be exactly 8 WATCH + 8 RELEVANT; "
            f"found {dict(holdout_counts)}"
        )

    if len(augmentation_train) != 55:
        raise RuntimeError(
            f"Expected 55 augmentation training rows; found {len(augmentation_train)}"
        )

    # Preserve original training examples; annotate lineage only.
    base_train_out = []
    for row in base_train:
        out = dict(row)
        out["dataset_origin"] = "V4_BASE_TRAIN"
        out["augmentation_review_id"] = ""
        base_train_out.append(out)

    base_val_out = []
    for row in base_val:
        out = dict(row)
        out["dataset_origin"] = "V4_BASE_VALIDATION"
        out["augmentation_review_id"] = ""
        base_val_out.append(out)

    train = base_train_out + augmentation_train
    validation = base_val_out

    validate_required("combined_train", train)
    validate_required("validation", validation)
    validate_required("boundary_holdout", boundary_holdout)

    leakage = audit_no_overlap(
        train,
        validation,
        boundary_holdout,
    )

    # Duplicate URL within each development split is also forbidden.
    for split_name, rows in (
        ("train", train),
        ("validation", validation),
        ("boundary_holdout", boundary_holdout),
    ):
        urls = [norm(r["source_url"]) for r in rows]
        if len(urls) != len(set(urls)):
            raise RuntimeError(
                f"Duplicate source_url inside {split_name}"
            )

    output.mkdir(parents=True, exist_ok=True)

    fields = list(
        dict.fromkeys(
            base_train_fields
            + base_val_fields
            + aug_fields
            + [
                "dataset_row_id",
                "story_group_id",
                "target_relevance_label",
                "original_target_relevance_label",
                "two_stage_task",
                "label_mapping_version",
                "label_source",
                "dataset_origin",
                "augmentation_review_id",
            ]
        )
    )

    write_csv(output / "train.csv", train, fields)
    write_csv(output / "validation.csv", validation, fields)
    write_csv(
        output / "augmentation_train.csv",
        augmentation_train,
        fields,
    )
    write_csv(
        output / "boundary_holdout.csv",
        boundary_holdout,
        fields,
    )

    # Exact byte-for-byte inherited test copy.
    shutil.copyfile(
        base_test_path,
        output / "test.csv",
    )

    copied_test_sha256 = sha256_file(output / "test.csv")
    if copied_test_sha256 != base_test_sha256:
        raise RuntimeError("Copied test.csv hash mismatch")

    train_counts = Counter(
        row["target_relevance_label"]
        for row in train
    )
    val_counts = Counter(
        row["target_relevance_label"]
        for row in validation
    )
    aug_train_counts = Counter(
        row["target_relevance_label"]
        for row in augmentation_train
    )

    manifest = {
        "status": "PASS",
        "version": "supervisory_relevance_v5_stage_b_targeted",
        "task_name": "STAGE_B_WATCH_VS_RELEVANT",
        "label_order": list(LABELS),
        "base_dataset": str(base),
        "augmentation_source": str(aug_path),
        "base_train_rows": len(base_train),
        "augmentation_total_clean_rows": len(converted_aug),
        "augmentation_train_rows": len(augmentation_train),
        "boundary_holdout_rows": len(boundary_holdout),
        "combined_train_rows": len(train),
        "validation_rows": len(validation),
        "train_counts": dict(train_counts),
        "validation_counts": dict(val_counts),
        "augmentation_train_counts": dict(aug_train_counts),
        "boundary_holdout_counts": dict(holdout_counts),
        "boundary_holdout_ids": sorted(BOUNDARY_HOLDOUT_IDS),
        "selection_policy": (
            "Fixed 16-row boundary holdout (8 WATCH/8 RELEVANT) reserved "
            "before retraining. Original 13-row validation remains the sole "
            "checkpoint-selection split."
        ),
        "test_policy": (
            "Inherited test.csv copied byte-for-byte. Test labels were not read "
            "by this builder and must not be used during training/tuning."
        ),
        "base_test_sha256": base_test_sha256,
        "copied_test_sha256": copied_test_sha256,
        "leakage_audit": leakage,
        "human_gold": False,
        "provenance": (
            "Base labels and augmentation labels are AI-assisted provisional; "
            "augmentation labels were assigned blind before hidden candidate "
            "selection strata were inspected."
        ),
    }

    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )

    print("=" * 96)
    print("STAGE-B v2 TARGETED DATASET COMPLETE")
    print("=" * 96)
    print(f"Base train rows          : {len(base_train)}")
    print(f"Augmentation clean rows  : {len(converted_aug)}")
    print(f"Augmentation train rows  : {len(augmentation_train)}")
    print(f"Combined train rows      : {len(train)}")
    print(f"Original validation rows : {len(validation)}")
    print(f"Fresh boundary holdout   : {len(boundary_holdout)}")
    print("Test labels read         : 0")
    print(f"Test hash preserved      : {base_test_sha256 == copied_test_sha256}")
    print()
    print("TRAIN COUNTS")
    for label in LABELS:
        print(f"  {label:<10} {train_counts[label]}")
    print()
    print("BOUNDARY HOLDOUT COUNTS")
    for label in LABELS:
        print(f"  {label:<10} {holdout_counts[label]}")
    print()
    print("LEAKAGE AUDIT")
    for key, value in leakage.items():
        print(f"  {key:<55} {value}")
    print()
    print(f"Output: {output}")


if __name__ == "__main__":
    main()
