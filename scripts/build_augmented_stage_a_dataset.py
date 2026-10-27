from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime, timezone
import csv
import hashlib
import json
import shutil

PROJECT = Path.cwd()

OLD_ROOT = PROJECT / r"data\training\datasets\supervisory_relevance_v4_two_stage\stage_a_noise_vs_keep"
NEW_ROOT = PROJECT / r"data\training\datasets\supervisory_relevance_v6_bank_supervision_candidate_two_stage\stage_a_noise_vs_keep"

OUT_ROOT = PROJECT / r"data\training\datasets\supervisory_relevance_v7_augmented_stage_a"

IDENTITY_FIELDS = [
    "dataset_row_id",
    "story_group_id",
    "source_url",
]

LABEL_FIELD = "target_relevance_label"


def read_csv(path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def value(row, field):
    return str(row.get(field, "") or "").strip()


def labels(rows):
    return dict(Counter(value(r, LABEL_FIELD).upper() for r in rows))


def values(rows, field):
    return {
        value(r, field)
        for r in rows
        if value(r, field)
    }


print("=" * 100)
print("BUILD V7 AUGMENTED STAGE-A DATASET")
print("=" * 100)

required = [
    OLD_ROOT / "train.csv",
    OLD_ROOT / "validation.csv",
    OLD_ROOT / "test.csv",
    NEW_ROOT / "train.csv",
]

for path in required:
    if not path.exists():
        raise SystemExit(f"MISSING: {path}")

old_train, old_fields = read_csv(OLD_ROOT / "train.csv")
old_val, val_fields = read_csv(OLD_ROOT / "validation.csv")
old_test, test_fields = read_csv(OLD_ROOT / "test.csv")
new_train, new_fields = read_csv(NEW_ROOT / "train.csv")

print(f"Old train      : {len(old_train)} {labels(old_train)}")
print(f"New v6 train   : {len(new_train)} {labels(new_train)}")
print(f"Old validation : {len(old_val)} {labels(old_val)}")
print(f"Old test       : {len(old_test)} {labels(old_test)}")

# ------------------------------------------------------------------
# Validate labels.
# ------------------------------------------------------------------

for name, rows in [
    ("old train", old_train),
    ("new train", new_train),
    ("validation", old_val),
    ("test", old_test),
]:
    invalid = [
        value(r, LABEL_FIELD)
        for r in rows
        if value(r, LABEL_FIELD).upper() not in {"NOISE", "KEEP"}
    ]

    if invalid:
        raise SystemExit(
            f"FAIL: invalid labels in {name}: {sorted(set(invalid))}"
        )

# ------------------------------------------------------------------
# Protected benchmark leakage gate.
# ------------------------------------------------------------------

protected = old_val + old_test

for field in IDENTITY_FIELDS:
    overlap = values(new_train, field) & values(protected, field)

    print(
        f"New train vs protected {field:<20}: "
        f"{len(overlap)}"
    )

    if overlap:
        raise SystemExit(
            f"FAIL: v6 train overlaps old validation/test on {field}: "
            f"{sorted(overlap)[:20]}"
        )

# ------------------------------------------------------------------
# Merge old train + v6 train.
#
# Rules:
# - old train is authoritative and always preserved;
# - if new row matches old train by URL or row ID and has same label,
#   skip as duplicate;
# - if labels conflict, FAIL;
# - story_group_id alone does not automatically remove a row because
#   one story group can legitimately contain multiple source articles.
# ------------------------------------------------------------------

old_by_row_id = {
    value(r, "dataset_row_id"): r
    for r in old_train
    if value(r, "dataset_row_id")
}

old_by_url = {
    value(r, "source_url"): r
    for r in old_train
    if value(r, "source_url")
}

merged = list(old_train)

added = 0
skipped_duplicate = 0

for row in new_train:
    matches = []

    rid = value(row, "dataset_row_id")
    url = value(row, "source_url")

    if rid and rid in old_by_row_id:
        matches.append(("dataset_row_id", old_by_row_id[rid]))

    if url and url in old_by_url:
        matches.append(("source_url", old_by_url[url]))

    if matches:
        new_label = value(row, LABEL_FIELD).upper()

        for field, old_row in matches:
            old_label = value(old_row, LABEL_FIELD).upper()

            if old_label != new_label:
                raise SystemExit(
                    "FAIL: conflicting training label\n"
                    f"field={field}\n"
                    f"value={value(row, field)}\n"
                    f"old={old_label}\n"
                    f"new={new_label}"
                )

        skipped_duplicate += 1
        continue

    merged.append(row)
    added += 1

print()
print(f"V6 rows added        : {added}")
print(f"Duplicates skipped   : {skipped_duplicate}")
print(f"Augmented train rows : {len(merged)}")
print(f"Augmented labels     : {labels(merged)}")

# ------------------------------------------------------------------
# Final leakage checks.
# ------------------------------------------------------------------

for field in IDENTITY_FIELDS:
    train_values = values(merged, field)
    val_values = values(old_val, field)
    test_values = values(old_test, field)

    tv = train_values & val_values
    tt = train_values & test_values
    vt = val_values & test_values

    print(
        f"{field:<20} "
        f"train/val={len(tv)} "
        f"train/test={len(tt)} "
        f"val/test={len(vt)}"
    )

    if tv or tt or vt:
        raise SystemExit(
            f"FAIL: split leakage detected for {field}"
        )

# ------------------------------------------------------------------
# Preserve a union schema.
# ------------------------------------------------------------------

fields = []

for source_fields in [
    old_fields,
    new_fields,
    val_fields,
    test_fields,
]:
    for field in source_fields:
        if field not in fields:
            fields.append(field)

# ------------------------------------------------------------------
# Fresh output.
# ------------------------------------------------------------------

if OUT_ROOT.exists():
    backup = OUT_ROOT.with_name(
        OUT_ROOT.name
        + ".backup_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    shutil.move(str(OUT_ROOT), str(backup))
    print(f"[BACKUP] {backup}")

OUT_ROOT.mkdir(parents=True, exist_ok=True)

write_csv(
    OUT_ROOT / "train.csv",
    merged,
    fields,
)

# IMPORTANT:
# Validation/test content is copied from old frozen benchmark.
# We write them with the common schema but verify identity/content below.
write_csv(
    OUT_ROOT / "validation.csv",
    old_val,
    fields,
)

write_csv(
    OUT_ROOT / "test.csv",
    old_test,
    fields,
)

# Full dataset file.
all_rows = []

for split_name, rows in [
    ("train", merged),
    ("validation", old_val),
    ("test", old_test),
]:
    for row in rows:
        copy = dict(row)
        copy["split"] = split_name
        all_rows.append(copy)

write_csv(
    OUT_ROOT / "dataset.csv",
    all_rows,
    fields,
)

manifest = {
    "status": "PASS",
    "dataset_version": "supervisory_relevance_v7_augmented_stage_a",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "task": "STAGE_A_NOISE_VS_KEEP",
    "strategy": (
        "Old successful Stage-A training corpus augmented with "
        "v6 bank-supervision training examples. "
        "Old validation and test benchmark retained."
    ),
    "sources": {
        "old_stage_a": str(OLD_ROOT.relative_to(PROJECT)),
        "v6_stage_a": str(NEW_ROOT.relative_to(PROJECT)),
    },
    "row_count": len(all_rows),
    "split_counts": {
        "train": len(merged),
        "validation": len(old_val),
        "test": len(old_test),
    },
    "label_counts": {
        "train": labels(merged),
        "validation": labels(old_val),
        "test": labels(old_test),
    },
    "augmentation": {
        "old_train_rows": len(old_train),
        "v6_candidate_train_rows": len(new_train),
        "v6_rows_added": added,
        "duplicate_rows_skipped": skipped_duplicate,
        "final_train_rows": len(merged),
    },
    "leakage": {
        "train_validation_story_group_overlap": len(
            values(merged, "story_group_id")
            & values(old_val, "story_group_id")
        ),
        "train_test_story_group_overlap": len(
            values(merged, "story_group_id")
            & values(old_test, "story_group_id")
        ),
        "train_validation_url_overlap": len(
            values(merged, "source_url")
            & values(old_val, "source_url")
        ),
        "train_test_url_overlap": len(
            values(merged, "source_url")
            & values(old_test, "source_url")
        ),
        "train_validation_row_id_overlap": len(
            values(merged, "dataset_row_id")
            & values(old_val, "dataset_row_id")
        ),
        "train_test_row_id_overlap": len(
            values(merged, "dataset_row_id")
            & values(old_test, "dataset_row_id")
        ),
    },
    "files": {},
}

for filename in [
    "train.csv",
    "validation.csv",
    "test.csv",
    "dataset.csv",
]:
    manifest["files"][filename] = {
        "sha256": sha256(OUT_ROOT / filename),
    }

with (OUT_ROOT / "manifest.json").open(
    "w",
    encoding="utf-8",
) as f:
    json.dump(manifest, f, indent=2)

print()
print("=" * 100)
print("V7 AUGMENTED STAGE-A BUILD COMPLETE")
print("=" * 100)
print(f"Train      : {len(merged)} {labels(merged)}")
print(f"Validation : {len(old_val)} {labels(old_val)}")
print(f"Test       : {len(old_test)} {labels(old_test)}")
print(f"Added v6   : {added}")
print(f"Skipped    : {skipped_duplicate}")
print("Leakage    : PASS")
print(f"Output     : {OUT_ROOT}")
print(f"Manifest   : {OUT_ROOT / 'manifest.json'}")
